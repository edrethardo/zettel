"""Der Zug `plan.woche` (Phase 2): die vierte Modellstufe, ihr Span, die Labels.

Kein Netz, keine Box. Das Modell ist `FakeLLM` (feste Antworten); die Spans
landen in einem `InMemorySpanExporter`. Geprüft wird die eine Zusicherung,
die den Planer trägt: **das Modell wählt nur aus der Vorlage** — eine
erfundene Gericht-id wird verworfen und gezählt, der Tag bleibt leer.
"""
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter)

from zettel import obs, recipes, wochenplan
from zettel.assistant import chat as chatmodul
from zettel.assistant import plan as stufen
from zettel.catalog import search
from zettel.llm import wake
from zettel.llm.client import Antwort
from zettel.obs import labels
from zettel.wochenplan import zug

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
BUTTER = "MIIL Deutsche Markenbutter"


class FakeLLM:
    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        self.aufrufe.append({"nachrichten": list(nachrichten), **weitere})
        if not self.antworten:
            raise AssertionError("Mehr Modellaufrufe als Antworten.")
        naechste = self.antworten.pop(0)
        if isinstance(naechste, Exception):
            raise naechste
        return Antwort(content=naechste, reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def __init__(self, zustand=wake.BEDIENT):
        self._z = zustand

    def zustand(self):
        if self._z == wake.BEDIENT:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(self._z, seit_s=3.0, grund="schläft")


@pytest.fixture
def con(katalog_con):
    return katalog_con


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs.setze_provider(provider)
    yield exporter
    obs.abbauen()


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _rezept(con, name, zutaten, minuten=None):
    rid = recipes.anlegen(con, name, servings=4, zutaten=zutaten)
    if minuten:
        con.execute("UPDATE recipe SET prep_minutes = ? WHERE id = ?",
                    (minuten, rid))
        con.commit()
    return rid


def _drei(con):
    a = _rezept(con, "Milchreis", [{"product_id": _pid(con, MILCH), "amount": 500, "unit": "ml"}], 25)
    b = _rezept(con, "Butterbrot", [{"product_id": _pid(con, BUTTER), "amount": 20, "unit": "g"}], 5)
    c = _rezept(con, "Pfannkuchen", [{"product_id": _pid(con, MILCH), "amount": 250, "unit": "ml"},
                                     {"free_text": "Mehl", "amount": 200, "unit": "g"}], 30)
    return a, b, c


def _antwort(*paare):
    return json.dumps({"tage": [{"tag": t, "gericht_id": g, "grund": f"passt zu Tag {t}"}
                                for t, g in paare]}, ensure_ascii=False)


def _planer(*antworten, box=None):
    return zug.Planer(chatmodul.Chat(FakeLLM(*antworten), wecker=box or Box()))


def _plan(con, tage=3, **werte):
    return wochenplan.anlegen(con, wochenplan.aus_formular(
        {"tage": str(tage), "personen": "2", **werte}), von="2026-09-07")


# --------------------------------------------------------------------------
# Die Stufe allein

def _tage(*offen):
    return [{"tag": i + 1, "name": f"Tag {i + 1}", "offen": o} for i, o in enumerate(offen)]


def _gerichte(*ids):
    return [{"id": i, "name": f"Gericht {i}", "minuten": 20, "servings": 4,
             "zutaten": ["Milch"]} for i in ids]


def test_stufe_waehlt_nur_vorgelegte_ids():
    llm = FakeLLM(_antwort((1, 11), (2, 99), (3, 12)))
    wahl = stufen.woche(llm, _tage(True, True, True), _gerichte(11, 12, 13))
    assert [(w["tag"], w["recipe_id"]) for w in wahl.gewaehlt] == [(1, 11), (3, 12)]
    assert [(v["tag"], v["recipe_id"], v["grund"]) for v in wahl.verworfen] == [
        (2, 99, "nicht vorgelegt")]
    assert wahl.gewaehlt[0]["grund"] == "passt zu Tag 1"
    # Guided JSON ging mit, das Schema heisst `wochenplan`.
    assert llm.aufrufe[0]["response_format"]["json_schema"]["name"] == "wochenplan"


def test_stufe_verwirft_nicht_offene_tage_und_dubletten():
    llm = FakeLLM(_antwort((1, 11), (2, 11), (3, 12), (3, 13)))
    tage = _tage(True, False, True)
    tage[1]["festgelegt"] = "Lasagne"
    wahl = stufen.woche(llm, tage, _gerichte(11, 12, 13))
    assert [(w["tag"], w["recipe_id"]) for w in wahl.gewaehlt] == [(1, 11), (3, 12)]
    assert [v["grund"] for v in wahl.verworfen] == [
        "Tag nicht offen", "zweites Gericht für denselben Tag"]


def test_stufe_legt_festgelegte_gerichte_nicht_vor():
    # Take vom 10.09. (Nemotron): das Modell wählte für den offenen Tag das
    # Gratin, das schon an Tag 1 stand — es STAND in der Liste. Seither ist
    # ein festgelegtes Gericht weder im Prompt noch im Schema; nennt das
    # Modell es trotzdem, sagt der Grund, warum es nicht ging.
    llm = FakeLLM(_antwort((1, 11)))
    tage = _tage(True, False)
    tage[1].update({"festgelegt": "Gericht 11", "festgelegt_id": 11})
    wahl = stufen.woche(llm, tage, _gerichte(11, 12))
    assert wahl.gewaehlt == []
    assert wahl.verworfen[0]["grund"] == "Gericht steht schon im Plan"
    prompt = llm.aufrufe[0]["nachrichten"][1]["content"]
    liste = prompt.split("Gerichte zur Wahl")[1]
    assert "ID 12:" in liste and "ID 11:" not in liste
    assert "Tag 2 (Tag 2): festgelegt — Gericht 11" in prompt


def test_stufe_fragt_nicht_wenn_alles_wahlbare_schon_im_plan_steht():
    # Szenario D' vom 10.09.: zwei Gerichte übrig, beide an festen Tagen.
    # Nemotron nannte damals genau die — verworfen. Jetzt gibt es nichts
    # vorzulegen, also keine Frage.
    llm = FakeLLM()
    tage = _tage(True, False, False)
    tage[1].update({"festgelegt": "Gericht 11", "festgelegt_id": 11})
    tage[2].update({"festgelegt": "Gericht 12", "festgelegt_id": 12})
    assert stufen.woche(llm, tage, _gerichte(11, 12)).gewaehlt == []
    assert llm.aufrufe == []


def test_schema_nennt_nur_offene_tage_und_vorgelegte_ids():
    # Was der Code verwerfen würde, kann der Server mit Guided Decoding gar
    # nicht erst erzeugen: `tag` und `gericht_id` sind Aufzählungen.
    llm = FakeLLM(_antwort((1, 12), (3, 13)))
    tage = _tage(True, False, True)
    tage[1].update({"festgelegt": "Gericht 11", "festgelegt_id": 11})
    stufen.woche(llm, tage, _gerichte(11, 12, 13))
    schema = llm.aufrufe[0]["response_format"]["json_schema"]["schema"]
    felder = schema["properties"]["tage"]["items"]["properties"]
    assert felder["tag"]["enum"] == [1, 3]
    assert felder["gericht_id"]["enum"] == [12, 13]
    # Das Grundschema bleibt, wie es ist — verengt wird eine Kopie.
    assert "enum" not in stufen.SCHEMA_WOCHE["properties"]["tage"]["items"]["properties"]["tag"]


def test_schema_bleibt_ein_serialisierbares_json_schema():
    # Das Schema geht als `response_format` über den Draht: es muss JSON
    # sein, die Aufzählungen ganze Zahlen, das Grundschema unverändert.
    schema = stufen.schema_woche({2}, {58, 7})
    assert json.loads(json.dumps(schema)) == schema
    felder = schema["properties"]["tage"]["items"]["properties"]
    assert felder == {"tag": {"type": "integer", "enum": [2]},
                      "gericht_id": {"type": "integer", "enum": [7, 58]},
                      "grund": {"type": "string", "maxLength": stufen.MAX_GRUND}}
    assert schema["properties"]["tage"]["items"]["required"] == ["tag", "gericht_id"]


def test_stufe_fragt_nicht_ohne_offenen_tag_oder_gericht():
    llm = FakeLLM()
    assert stufen.woche(llm, _tage(False, False), _gerichte(1)).gewaehlt == []
    assert stufen.woche(llm, _tage(True), []).gewaehlt == []
    assert llm.aufrufe == []


def test_stufe_toleriert_verpackung():
    llm = FakeLLM('```json\n{"plan": [{"day": 1, "recipe_id": 11}]}\n```')
    wahl = stufen.woche(llm, _tage(True), _gerichte(11))
    assert [(w["tag"], w["recipe_id"]) for w in wahl.gewaehlt] == [(1, 11)]


def test_vorlage_nennt_rahmen_bestand_und_festlegung():
    tage = _tage(True, False)
    tage[1]["festgelegt"] = "Lasagne"
    text = stufen.wochenvorlage(tage, _gerichte(11), personen=2, max_minuten=30,
                                bestand=["Kartoffeln"])
    assert "2 Personen, höchstens 30 Minuten" in text
    assert "Noch da (soll aufgebraucht werden): Kartoffeln" in text
    assert "Tag 2 (Tag 2): festgelegt — Lasagne" in text
    assert "ID 11: Gericht 11 — 20 Min., für 4 Portionen; Zutaten: Milch" in text


# --------------------------------------------------------------------------
# Der Zug

def test_zug_belegt_die_offenen_tage_und_traegt_den_span(con, spans):
    a, b, c = _drei(con)
    pid = _plan(con, bestand="6 Eier")
    planer = _planer(_antwort((1, a), (2, 999), (3, c)))
    bericht = planer.planen(con, pid)
    assert (bericht["belegt"], bericht["verworfen"], bericht["offen"],
            bericht["vorgelegt"]) == (2, 1, 3, 3)
    p = wochenplan.laden(con, pid)
    assert [t["recipe_id"] for t in p["tage_liste"]] == [a, None, c]
    assert p["tage_liste"][0]["grund"] == "passt zu Tag 1"
    assert all(t["decision"] == "offen" for t in p["tage_liste"])

    span = next(s for s in spans.get_finished_spans() if s.name == "plan.woche")
    a_ = span.attributes
    assert a_["openinference.span.kind"] == "CHAIN"
    assert a_["zettel.path"] == "plan"
    assert a_["zettel.plan.presented"] == 3
    assert a_["zettel.plan.days"] == 3
    assert a_["zettel.plan.assigned"] == 2
    assert a_["zettel.plan.rejected"] == 1
    assert a_["zettel.plan.rejected_reasons"] == "nicht vorgelegt"
    # Milch kommt an zwei Tagen vor, Mehl nur an einem: Rest = 1.
    assert a_["zettel.plan.rest"] == 1
    assert a_["zettel.plan.lines"] == 2
    eingabe = json.loads(a_["input.value"])
    assert eingabe["bestand"] == ["Eier"]
    ausgabe = json.loads(a_["output.value"])
    assert [(z["tag"], z["recipe_id"]) for z in ausgabe] == [(1, a), (3, c)]
    assert p["span_id"] == obs.span_id(span)
    # Die Vorlage im Prompt trägt den Bestand und die drei Gerichte.
    prompt = planer.chat.zugang.aufrufe[0]["nachrichten"][1]["content"]
    assert "Eier" in prompt and "Milchreis" in prompt and "Pfannkuchen" in prompt


def test_neuplanung_haelt_ja_fest_und_ersetzt_nein(con, spans):
    a, b, c = _drei(con)
    pid = _plan(con)
    tage = wochenplan.laden(con, pid)["tage_liste"]
    wochenplan.tag_setzen(con, tage[0]["id"], a)
    wochenplan.tag_entscheiden(con, tage[0]["id"], "kept")
    wochenplan.tag_setzen(con, tage[1]["id"], b)
    wochenplan.tag_entscheiden(con, tage[1]["id"], "removed")
    wochenplan.auswaerts_setzen(con, tage[2]["id"])

    planer = _planer(_antwort((2, c)))
    bericht = planer.planen(con, pid)
    assert bericht["offen"] == 1 and bericht["belegt"] == 1
    # Das abgelehnte Butterbrot stand nicht mehr zur Wahl, das festgelegte
    # Milchreis auch nicht mehr (steht schon im Plan): vorgelegt ist genau
    # das eine Gericht, das gewählt werden kann.
    assert bericht["vorgelegt"] == 1
    prompt = planer.chat.zugang.aufrufe[0]["nachrichten"][1]["content"]
    liste = prompt.split("Gerichte zur Wahl")[1]
    assert "Butterbrot" not in liste and "Milchreis" not in liste
    assert "Pfannkuchen" in liste
    assert "Tag 1 (Mo 07.09.): festgelegt — Milchreis" in prompt
    assert "Tag 3 (Mi 09.09.): festgelegt — auswärts" in prompt
    assert "Tag 2 (Di 08.09.): offen" in prompt
    p = wochenplan.laden(con, pid)
    assert [(t["recipe_id"], t["decision"]) for t in p["tage_liste"]] == [
        (a, "kept"), (c, "offen"), (None, "kept")]
    span = next(s for s in spans.get_finished_spans() if s.name == "plan.woche")
    assert span.attributes["zettel.plan.fixed"] == 2


def test_neuplanung_ohne_freies_gericht_fragt_nicht(con, spans):
    # D' auf Nemotron am 10.09.: Tag 1 „Nein", die zwei anderen Gerichte
    # stehen an Tag 2 und 3. Damals wurden beide vorgelegt und beide
    # genannt (rejected 3). Jetzt: nichts zur Wahl, kein Modellaufruf.
    a, b, c = _drei(con)
    pid = _plan(con)
    tage = wochenplan.laden(con, pid)["tage_liste"]
    for tag, rid in zip(tage, (a, b, c)):
        wochenplan.tag_setzen(con, tag["id"], rid)
        wochenplan.tag_entscheiden(con, tag["id"], "kept")
    wochenplan.tag_entscheiden(con, tage[0]["id"], "removed")
    planer = _planer()
    bericht = planer.planen(con, pid)
    assert bericht["meldung"] == "nichts_zur_wahl"
    assert (bericht["offen"], bericht["vorgelegt"]) == (1, 0)
    assert planer.chat.zugang.aufrufe == []
    assert spans.get_finished_spans() == ()


def test_ohne_offenen_tag_wird_nicht_gefragt(con, spans):
    a, b, c = _drei(con)
    pid = _plan(con, tage=1)
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    wochenplan.tag_setzen(con, tag["id"], a)
    wochenplan.tag_entscheiden(con, tag["id"], "kept")
    planer = _planer()
    bericht = planer.planen(con, pid)
    assert bericht["meldung"] == "kein_offener_tag"
    assert planer.chat.zugang.aufrufe == []
    assert spans.get_finished_spans() == ()


def test_ohne_gericht_wird_nicht_gefragt(con):
    pid = _plan(con)
    planer = _planer()
    assert planer.planen(con, pid)["meldung"] == "nichts_zur_wahl"
    assert planer.chat.zugang.aufrufe == []


def test_schlafende_box_wirft_und_laesst_den_plan(con):
    _drei(con)
    pid = _plan(con)
    planer = _planer(box=Box(wake.WACHT_AUF))
    with pytest.raises(chatmodul.ChatNichtVerfuegbar):
        planer.planen(con, pid)
    assert all(t["recipe_id"] is None
               for t in wochenplan.laden(con, pid)["tage_liste"])


def test_kaputte_antwort_ist_eine_meldung(con, spans):
    _drei(con)
    pid = _plan(con)
    planer = _planer("das ist kein JSON")
    bericht = planer.planen(con, pid)
    assert bericht["meldung"] == "modell_kaputt" and bericht["fehler"]
    assert all(t["recipe_id"] is None
               for t in wochenplan.laden(con, pid)["tage_liste"])
    span = next(s for s in spans.get_finished_spans() if s.name == "plan.woche")
    assert span.attributes["zettel.plan.error"]


def test_vorwaermen_laeuft_nur_fuer_quellrezepte_ohne_zuordnung(con, spans):
    """Ein Chefkoch-Rezept ohne gemerkte Zuordnung bekommt sie im Zug — unter
    dem Plan-Span, mit den Retriever-Spans der Suche."""
    cur = con.execute(
        "INSERT INTO recipe (name, servings, source, prep_minutes)"
        " VALUES ('Béchamel', 4, 'chefkoch', 15)")
    rid = int(cur.lastrowid)
    con.execute("INSERT INTO recipe_ingredient (recipe_id, pos, raw_name, name,"
                " amount, unit) VALUES (?, 0, 'Milch', 'Milch', 500, 'ml')", (rid,))
    con.execute("INSERT INTO dish (name, query, status, recipe_id, requested_at,"
                " fetched_at) VALUES ('bechamel', 'Béchamel', 'ok', ?, 'x', 'x')",
                (rid,))
    con.commit()
    pid = _plan(con, tage=1)
    extract = json.dumps({"begriffe": [{"suchbegriffe": ["Milch"], "menge": 1}]})
    # Gewählt wird, was die Suche VORLEGT — wie im Betrieb. Eine id, die nicht
    # unter den Kandidaten steht, würde Stufe 3 verwerfen.
    kandidat = search.suche_kette(con, ["Milch"], limit=5)[0]["id"]
    choose = json.dumps({"auswahl": [{"begriff": "Milch",
                                      "produkt_id": kandidat, "menge": 1}]})
    planer = _planer(_antwort((1, rid)), extract, choose)
    bericht = planer.planen(con, pid)
    assert bericht["belegt"] == 1 and bericht["vorgewaermt"] == 1
    namen = [s.name for s in spans.get_finished_spans()]
    assert "plan.woche" in namen and "recipe.zuordnung" in namen
    assert "catalog.search" in namen
    # Die Einkaufsliste trägt jetzt das Produkt, nicht den Zutatennamen.
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["product_id"] == kandidat
    assert e["zeilen"][0]["bedarf"] == 250.0


# --------------------------------------------------------------------------
# Labels

def test_labels_aus_den_tagesentscheidungen(con, spans):
    a, b, c = _drei(con)
    pid = _plan(con)
    planer = _planer(_antwort((1, a), (2, b), (3, c)))
    planer.planen(con, pid)
    tage = wochenplan.laden(con, pid)["tage_liste"]
    wochenplan.tag_entscheiden(con, tage[0]["id"], "kept")
    wochenplan.tag_entscheiden(con, tage[1]["id"], "removed")
    # Tag 3 bleibt offen — zählt nicht.
    annos = labels.plan_annotationen(con, pid)
    assert [a_["name"] for a_ in annos] == ["plan_precision", "plan_day", "plan_day"]
    quote = annos[0]
    assert quote["result"]["score"] == 0.5
    assert quote["identifier"] == f"zettel-plan-{pid}"
    assert [a_["result"]["label"] for a_ in annos[1:]] == ["kept", "removed"]
    assert "Milchreis — passt zu Tag 1" in annos[1]["result"]["explanation"]
    assert all(a_["annotator_kind"] == "HUMAN" for a_ in annos)
    assert all(a_["span_id"] == wochenplan.laden(con, pid)["span_id"] for a_ in annos)


def test_ohne_span_keine_labels(con):
    a, b, c = _drei(con)
    pid = _plan(con)
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    wochenplan.tag_setzen(con, tag["id"], a)
    wochenplan.tag_entscheiden(con, tag["id"], "kept")
    assert labels.plan_annotationen(con, pid) == []


def test_in_den_korb_schreibt_die_labels(con, spans):
    a, b, c = _drei(con)
    pid = _plan(con)
    planer = _planer(_antwort((1, a)))
    planer.planen(con, pid)
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    wochenplan.tag_entscheiden(con, tag["id"], "kept")

    gesendet = []

    class Klient:
        class spans:  # noqa: N801 — die Form des Phoenix-Clients
            @staticmethod
            def log_span_annotations(*, span_annotations, sync=False):
                gesendet.extend(span_annotations)

    geschrieben = labels.plan_schreiben(con, pid, client=Klient())
    assert labels.abwarten()
    assert [g["name"] for g in geschrieben] == ["plan_precision", "plan_day"]
    assert len(gesendet) == 2


def test_zug_laesst_den_bon_vorschlagen(con, spans):
    """Phase 3 im Zug: nach dem Belegen schlägt der Bon vor — offen, mit
    Datum, und im Span als `stock_suggested`."""
    from datetime import date

    from zettel.bons import kaeufe, zerlegen
    a, b, c = _drei(con)
    bon = zerlegen.Bon(laden="rewe", datum=date.today().isoformat(),
                       posten=[zerlegen.Posten(text="BUTTER", gesamt_cents=105, zeile=1)])
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    item = kaeufe.posten(con, rid)[0]
    kaeufe.zuordnung_setzen(con, item["id"], product_id=_pid(con, BUTTER))
    kaeufe.entscheiden(con, item["id"], "kept")
    pid = _plan(con, tage=1)
    planer = _planer(_antwort((1, b)))
    bericht = planer.planen(con, pid)
    assert bericht["bestand_vorgeschlagen"] == 1
    bestand = wochenplan.laden(con, pid)["bestand"]
    assert [(x["herkunft"], x["decision"], x["receipt_item_id"]) for x in bestand] == [
        ("aus_bon", "offen", item["id"])]
    span = next(s for s in spans.get_finished_spans() if s.name == "plan.woche")
    assert span.attributes["zettel.plan.stock_suggested"] == 1
