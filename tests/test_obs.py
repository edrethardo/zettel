"""Tests für den Span-Vertrag (WB-328, Spec 7).

**Ohne Phoenix und ohne die Box.** Die Spans landen in einem
`InMemorySpanExporter`, das Modell ist ein echter `openai`-Client auf einem
`httpx.MockTransport`. Der Umweg über das echte SDK ist Absicht und nicht
Umständlichkeit: die LLM-Spans kommen laut Spec 7.2 vom `OpenAIInstrumentor`,
und der hängt an den SDK-Aufrufen. Ein Fake-LLM wie in `test_assistant.py`
erzeugte gar keinen LLM-Span — der Baum aus Spec 7.1 wäre dann nur zur Hälfte
geprüft, und zwar an der Hälfte, die Tokenzahlen und Kosten trägt.

Was hier geprüft wird, ist der Reihe nach: die Form des Baums, die
Span-Kinds, dass die Attribute NICHT LEER sind (ein leerer Span sieht in der
Oberfläche genauso gut aus und misst nichts), die Retriever-Dokumente mit ID,
Inhalt und Score, der Rezeptweg ohne LLM-Spans — und zuletzt die harte
Anforderung aus Spec 7.3: **fällt Phoenix aus, fällt der Shop nicht aus.**

Der Butter-Fall aus WB-327 steht als eigener Test da (`test_der_butterfall…`),
weil er der Grund für `RETRIEVER` statt `TOOL` ist: er zeigt, dass sich am
Trace ablesen lässt, ob ein Fehlgriff am Modell oder an der Suche lag.
"""
import json
import threading
import time

import httpx
import openai
import pytest
from openinference.instrumentation.openai import OpenAIInstrumentor
from openinference.semconv.trace import (
    DocumentAttributes,
    SpanAttributes,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter)

from picknick import db, obs, recipes
from picknick.assistant import chat as chatmodul
from picknick.llm import wake
from picknick.llm.client import Modellzugang
from picknick.obs import otel as tracermodul

KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
DOKS = SpanAttributes.RETRIEVAL_DOCUMENTS

#: Der Katalog dieses Tests ist der Butter-Fall aus WB-327 in klein: zu
#: „Butter" gibt es AUSSCHLIESSLICH Spezialbutter, zu „Zwiebeln" genau das
#: Richtige. Damit ist der Unterschied, den der Trace zeigen soll, im Katalog
#: angelegt und nicht behauptet.
KATALOG = [
    ("bb1", "ButterBoyz Handgemachte BIO-Salzbutter", 469, "250 g",
     "Milch, Molkerei & Butter", "Butter", "Markenbutter"),
    ("bb2", "ButterBoyz Kräuterbutter", 399, "150 g",
     "Milch, Molkerei & Butter", "Butter", "Markenbutter"),
    ("bb3", "ButterBoyz Trüffelbutter", 649, "100 g",
     "Milch, Molkerei & Butter", "Butter", "Markenbutter"),
    ("zw1", "Zwiebeln", 149, "1 kg", "Obst & Gemüse", "Gemüse", "Zwiebeln"),
    ("sp1", "Spaghetti No. 5", 189, "500 g", "Nudeln & Reis", "Nudeln",
     "Spaghetti"),
]


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.migrate(c)
    for external_id, name, cent, gebinde, l1, l2, l3 in KATALOG:
        c.execute(
            "INSERT INTO product (source, external_id, name, brand,"
            " price_cents, unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 'Testmarke', ?, ?, ?, ?, ?)",
            (external_id, name, cent, gebinde, l1, l2, l3))
    c.commit()
    yield c
    c.close()


@pytest.fixture
def spans():
    """Ein Provider, der in den Speicher exportiert — wie im Betrieb, nur ohne
    Netz. Der Instrumentor hängt an genau diesem Provider (Spec 7.2)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs.setze_provider(provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    yield exporter
    OpenAIInstrumentor().uninstrument()
    obs.abbauen()


# --------------------------------------------------------------------------
# Ein echtes openai-SDK ohne Netz

#: Feste Tokenzahlen, damit die Prüfung „nicht leer" eine Zahl hat, die auch
#: dann falsch aussähe, wenn sie erfunden wäre.
TOKEN_PROMPT, TOKEN_COMPLETION = 137, 42


def _mock_zugang(*inhalte: str) -> Modellzugang:
    """Ein `Modellzugang` auf einem HTTP-Doppelgänger.

    Kein Socket, aber der ganze Weg durch das `openai`-SDK — und damit durch
    den Instrumentor.
    """
    antworten = list(inhalte)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "object": "list",
                "data": [{"id": "Qwen3.8-27B-Instruct", "object": "model",
                          "created": 0, "owned_by": "vllm"}]})
        assert antworten, "Es wurde öfter gefragt als geantwortet."
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 0,
            "model": "Qwen3.8-27B-Instruct",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": antworten.pop(0)}}],
            "usage": {"prompt_tokens": TOKEN_PROMPT,
                      "completion_tokens": TOKEN_COMPLETION,
                      "total_tokens": TOKEN_PROMPT + TOKEN_COMPLETION}})

    client = openai.OpenAI(
        base_url="http://box.test/v1", api_key="1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return Modellzugang(endpunkt="http://box.test/v1", client=client)


class Box:
    """Die Box bedient — ohne sie anzufassen."""

    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="Qwen3.8-27B-Instruct")


def _extract(*paare):
    """Stufe 1 wie seit WB-340: je Zutat eine Kette. Ein Tupel = eine Kette."""
    return json.dumps(
        {"begriffe": [{"suchbegriffe": list(b) if isinstance(b, tuple) else [b],
                       "menge": m} for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _agent(*antworten, **weitere):
    return chatmodul.Chat(_mock_zugang(*antworten), wecker=Box(), **weitere)


def _nach_namen(exporter):
    return [s.name for s in exporter.get_finished_spans()]


def _suche(exporter, begriff):
    """Der `catalog.search`-Span dieser Zutat.

    Gefunden über `picknick.term` — den genauesten Begriff der Kette.
    `input.value` ist seit WB-340 die ganze Kette als JSON und taugt nicht
    mehr als Schlüssel.
    """
    treffer = [s for s in exporter.get_finished_spans()
               if s.name == "catalog.search"
               and s.attributes.get("picknick.term") == begriff]
    assert len(treffer) == 1, f"{begriff}: {len(treffer)} Spans, erwartet 1"
    return treffer[0]


def _einer(exporter, name):
    treffer = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(treffer) == 1, f"{name}: {len(treffer)} Spans, erwartet 1"
    return treffer[0]


def _butter_und_zwiebeln(con):
    """Der Satz aus WB-327, klein: zwei Begriffe, zwei Suchen, eine Wahl."""
    return _agent(_extract(("Butter", 1), ("Zwiebeln", 1)),
                  _choose(("Butter",
                           _pid(con, "ButterBoyz Handgemachte BIO-Salzbutter"),
                           1),
                          ("Zwiebeln", _pid(con, "Zwiebeln"), 1)))


# --------------------------------------------------------------------------
# Der Baum aus Spec 7.1

def test_der_baum_hat_genau_die_form_aus_der_spec(con, spans):
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    # Reihenfolge des Beendens: die Kinder vor dem Elternteil, und die Kinder
    # in der Reihenfolge, in der sie liefen.
    assert _nach_namen(spans) == [
        "plan.extract",
        "catalog.search", "catalog.search",
        "plan.choose",
        "chat.turn",
    ]


def test_alles_haengt_unter_chat_turn(con, spans):
    """Verschachtelung, nicht bloss Anwesenheit.

    Fünf Spans nebeneinander sähen in einer Namensliste genauso aus wie ein
    Baum — in Phoenix wären es aber fünf Traces statt eines Chat-Zugs, und
    keine Auswertung fände die Stufen wieder zusammen.
    """
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    wurzel = _einer(spans, "chat.turn")
    assert wurzel.parent is None
    kinder = [s for s in spans.get_finished_spans() if s.name != "chat.turn"]
    assert len(kinder) == 4
    for kind in kinder:
        assert kind.parent is not None
        assert kind.parent.span_id == wurzel.context.span_id
        assert kind.context.trace_id == wurzel.context.trace_id


def test_die_span_kinds_stimmen(con, spans):
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    kinds = [(s.name, s.attributes[KIND]) for s in spans.get_finished_spans()]
    assert kinds == [
        ("plan.extract", "LLM"),
        # RETRIEVER und nicht TOOL — der Kern des Tickets (Spec 7.1).
        ("catalog.search", "RETRIEVER"),
        ("catalog.search", "RETRIEVER"),
        ("plan.choose", "LLM"),
        ("chat.turn", "CHAIN"),
    ]


# --------------------------------------------------------------------------
# Die Attribute sind nicht leer

def test_chat_turn_traegt_satz_und_vorschlagsliste(con, spans):
    ergebnis = _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    a = _einer(spans, "chat.turn").attributes
    assert a[SpanAttributes.INPUT_VALUE] == "Butter und Zwiebeln"
    ausgabe = json.loads(a[SpanAttributes.OUTPUT_VALUE])
    assert [v["name"] for v in ausgabe] == [
        p["name"] for p in ergebnis.vorschlaege]
    assert ausgabe[0]["begriff"] == "Butter"
    assert a[obs.PFAD] == "llm"
    assert a["picknick.terms"] == 2
    assert a["picknick.products"] == 2
    assert a["picknick.free_text"] == 0
    assert a["picknick.rejected"] == 0
    assert a["picknick.order_id"] == ergebnis.order_id
    assert a["picknick.chat_message_id"] == ergebnis.chat_message_id
    # Die Sitzung ist der Warenkorb: mehrere Sätze zu einem Einkauf gehören
    # in Phoenix zusammen.
    assert a["session.id"] == f"korb-{ergebnis.order_id}"


def test_die_llm_spans_tragen_getrennte_tokenzahlen(con, spans):
    """Spec 7.3: Span-Kind LLM, und Token-Klassen getrennt.

    Nicht von Hand gesetzt — sie kommen vom Instrumentor. Genau deshalb
    dürfen hier keine zwei LLM-Spans übereinanderliegen: Phoenix rechnet die
    Kosten je LLM-Span, und doppelte Spans wären doppelte Kosten.
    """
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    for name in ("plan.extract", "plan.choose"):
        a = _einer(spans, name).attributes
        assert a[KIND] == "LLM"
        assert a[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == TOKEN_PROMPT
        assert a[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] == TOKEN_COMPLETION
        assert a[SpanAttributes.LLM_MODEL_NAME] == "Qwen3.8-27B-Instruct"
        assert a[SpanAttributes.INPUT_VALUE]
        assert a[SpanAttributes.OUTPUT_VALUE]


def test_plan_extract_zeigt_die_begriffe_und_choose_die_kandidaten(con, spans):
    """Die Ausgabe von Stufe 1 und die Eingabe von Stufe 3 stehen im Trace.

    Ohne das wäre nicht nachvollziehbar, WAS dem Modell vorlag — und damit
    wäre die Frage nach Modell- oder Retrieval-Fehler wieder offen.
    """
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    extract = _einer(spans, "plan.extract").attributes
    assert "Butter" in extract[SpanAttributes.OUTPUT_VALUE]
    choose = _einer(spans, "plan.choose").attributes
    assert "ButterBoyz Handgemachte BIO-Salzbutter" in (
        choose[SpanAttributes.INPUT_VALUE])


# --------------------------------------------------------------------------
# Die Retriever-Dokumente

def test_kandidaten_stehen_als_dokumente_mit_id_inhalt_und_score(con, spans):
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    butter = _suche(spans, "Butter")
    a = butter.attributes
    assert a["picknick.candidates"] == 3

    for i in range(3):
        p = f"{DOKS}.{i}."
        assert a[p + DocumentAttributes.DOCUMENT_ID].isdigit()
        assert "ButterBoyz" in a[p + DocumentAttributes.DOCUMENT_CONTENT]
        # Der Score ist der `rang` aus der Suche: negiertes bm25, also
        # positiv und „grösser ist besser".
        assert a[p + DocumentAttributes.DOCUMENT_SCORE] > 0
        assert json.loads(a[p + DocumentAttributes.DOCUMENT_METADATA])[
            "kategorie"].startswith("Milch")

    # Absteigend sortiert — sonst wäre „der beste Treffer" im Trace nicht der
    # erste, und jede Ablesung ginge schief.
    scores = [a[f"{DOKS}.{i}." + DocumentAttributes.DOCUMENT_SCORE]
              for i in range(3)]
    assert scores == sorted(scores, reverse=True)
    assert a["picknick.rank_top"] == scores[0]
    # Der Inhalt ist das, was das Modell sah: Name, Gebinde, Preis.
    assert a[f"{DOKS}.0." + DocumentAttributes.DOCUMENT_CONTENT].endswith("€")


def test_ein_span_je_zutat_mit_ihrer_ganzen_begriffskette(con, spans):
    """Ein Span je ZUTAT (WB-340), und er nennt die ganze Kette.

    Nicht einer je Begriff: die Kandidaten mehrerer Begriffe werden zu EINER
    Liste vereinigt, und die ist an keinem Begriffs-Span mehr zu sehen. Was
    ein Kandidat gekostet hat — über welchen Begriff er kam —, steht deshalb
    an ihm selbst.
    """
    agent = _agent(_extract((("Salzbutter", "Butter"), 1), ("Zwiebeln", 1)),
                   _choose(("Salzbutter", _pid(con, "ButterBoyz Kräuterbutter"),
                            1)))
    agent.turn(con, "Butter und Zwiebeln")

    suchen = [s for s in spans.get_finished_spans()
              if s.name == "catalog.search"]
    assert [s.attributes["picknick.term"] for s in suchen] == [
        "Salzbutter", "Zwiebeln"]
    a = suchen[0].attributes
    assert json.loads(a[SpanAttributes.INPUT_VALUE]) == ["Salzbutter", "Butter"]
    assert a["picknick.search_terms"] == "Salzbutter, Butter"


def test_der_span_nennt_je_kandidat_den_begriff_der_ihn_brachte(con, spans):
    """Ohne die Herkunft ist an einer vereinigten Liste nicht mehr zu sehen,
    WARUM ein Produkt vorlag — und genau das ist der Zweck des Span-Vertrags.
    """
    agent = _agent(_extract((("Salzbutter", "Butter"), 1)),
                   _choose(("Salzbutter", _pid(con, "ButterBoyz Kräuterbutter"),
                            1)))
    agent.turn(con, "Butter")

    a = _einer(spans, "catalog.search").attributes
    n = a["picknick.candidates"]
    assert n == 3, "Die drei Butter-Produkte, nach Produkt-ID entdoppelt."
    via = [json.loads(a[f"{DOKS}.{i}." + DocumentAttributes.DOCUMENT_METADATA]
                      )["via"] for i in range(n)]
    assert sorted(via) == ["Butter", "Butter", "Salzbutter"]
    ausgabe = json.loads(a[SpanAttributes.OUTPUT_VALUE])
    assert {v["name"]: v["via"] for v in ausgabe}[
        "ButterBoyz Handgemachte BIO-Salzbutter"] == "Salzbutter"


def test_suche_ohne_treffer_ist_ein_span_ohne_dokumente(con, spans):
    """Ein Begriff, den der Katalog nicht kennt, verschwindet nicht.

    Er wird zum Freitext-Vorschlag (WB-327) — und im Trace steht ein
    RETRIEVER-Span mit null Kandidaten. Das ist der eindeutigste
    Retrieval-Fehler, den es gibt, und er soll sichtbar sein.
    """
    agent = _agent(_extract(("Wachsmalstifte", 1)), _choose())
    ergebnis = agent.turn(con, "Wachsmalstifte")

    span = _einer(spans, "catalog.search")
    assert span.attributes["picknick.candidates"] == 0
    assert f"{DOKS}.0." + DocumentAttributes.DOCUMENT_ID not in span.attributes
    wurzel = _einer(spans, "chat.turn").attributes
    assert wurzel["picknick.free_text"] == 1
    assert wurzel["picknick.weakest_term"] == "Wachsmalstifte"
    assert wurzel["picknick.weakest_rank"] == 0.0
    assert ergebnis.n_freitext == 1


# --------------------------------------------------------------------------
# Modellfehler oder Retrieval-Fehler? Der Butter-Fall aus WB-327

def test_der_butterfall_ist_am_trace_als_retrieval_fehler_zu_erkennen(
        con, spans):
    """Der Beleg für `RETRIEVER` statt `TOOL`.

    Das Modell wählt eine handgemachte BIO-Salzbutter für 4,69 € — ein
    Fehlgriff. Am Trace ist zu sehen, dass es NICHT das Modell war: alle
    vorgelegten Kandidaten waren Spezialbutter, normale Butter stand nie zur
    Wahl. Der Rang sagt es vorher, und `picknick.weakest_term` auf der Wurzel
    sagt es, ohne dass man einen Ast aufklappt.
    """
    _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    wurzel = _einer(spans, "chat.turn").attributes
    # Das Modell hat nichts erfunden — an ihm lag es nicht.
    assert wurzel["picknick.rejected"] == 0
    # Die Suche hat bei „Butter" am schwächsten vorgelegt.
    assert wurzel["picknick.weakest_term"] == "Butter"

    raenge = {s.attributes["picknick.term"]:
              s.attributes["picknick.rank_top"]
              for s in spans.get_finished_spans()
              if s.name == "catalog.search"}
    assert raenge["Butter"] < raenge["Zwiebeln"]
    assert wurzel["picknick.weakest_rank"] == raenge["Butter"]

    # Und die Vorlage selbst: keine normale Butter darunter.
    butter = _suche(spans, "Butter")
    inhalte = [butter.attributes[f"{DOKS}.{i}."
                                 + DocumentAttributes.DOCUMENT_CONTENT]
               for i in range(butter.attributes["picknick.candidates"])]
    assert all("ButterBoyz" in i for i in inhalte)


def test_eine_erfundene_id_zeigt_der_trace_als_modellfehler(con, spans):
    """Die andere Richtung: die Suche legte vor, das Modell griff daneben."""
    agent = _agent(_extract(("Zwiebeln", 1)), _choose(("Zwiebeln", 999999, 1)))
    agent.turn(con, "Zwiebeln")

    wurzel = _einer(spans, "chat.turn").attributes
    # Kandidaten waren da …
    such = _einer(spans, "catalog.search").attributes
    assert such["picknick.candidates"] == 1
    # … und trotzdem kam nichts heraus. Das ist ein Modellfehler.
    assert wurzel["picknick.rejected"] == 1
    assert wurzel["picknick.products"] == 0


# --------------------------------------------------------------------------
# Der Rezeptweg

def test_rezepttreffer_hat_keine_llm_spans(con, spans):
    recipes.anlegen(con, "Zwiebelkuchen", zutaten=[
        {"product_id": _pid(con, "Zwiebeln"), "qty": 2}])
    agent = chatmodul.Chat(_mock_zugang(), wecker=Box())

    ergebnis = agent.turn(con, "Zwiebelkuchen bitte")

    assert ergebnis.weg == "recipe"
    assert _nach_namen(spans) == ["chat.turn"]
    a = _einer(spans, "chat.turn").attributes
    assert a[obs.PFAD] == "recipe"
    assert a["picknick.recipes"] == "Zwiebelkuchen"
    # Kein Begriff, keine Suche, keine schwächste Suche — und deshalb auch
    # kein Attribut dafür. `None` wäre ein Wert, der behauptet, es hätte eine
    # Suche gegeben.
    assert "picknick.weakest_term" not in a
    # Ohne Rest im Satz behauptet auch nichts einen (WB-370).
    assert "picknick.rest" not in a
    assert "picknick.rest_added" not in a
    assert a[SpanAttributes.OUTPUT_VALUE]


def test_was_neben_dem_gericht_stand_steht_im_span(con, spans):
    """Der Artikel neben dem Gericht ist im Trace wiederzufinden (WB-370).

    Ohne diese zwei Attribute hinterlässt ein Artikel, der aus dem Satz
    verschwindet, gar nichts — und die Messung, die zu WB-370 geführt hat,
    liesse sich später nicht wiederholen. `rest` ist die andere Hälfte des
    Satzes zu `dish`, `rest_added` sagt, ob dieser Zug eine Zeile dafür
    angelegt hat.
    """
    recipes.anlegen(con, "Zwiebelkuchen", zutaten=[
        {"product_id": _pid(con, "Zwiebeln"), "qty": 2}])
    agent = chatmodul.Chat(_mock_zugang(), wecker=Box())

    ergebnis = agent.turn(con, "Zwiebelkuchen und Klopapier")

    a = _einer(spans, "chat.turn").attributes
    assert a[obs.PFAD] == "recipe"
    assert a["picknick.rest"] == "Klopapier"
    assert a["picknick.rest_added"] is True
    assert "Klopapier" in [v["name"] for v in ergebnis.vorschlaege]


# --------------------------------------------------------------------------
# chat_message.span_id

def test_span_id_landet_an_beiden_chatzeilen(con, spans):
    """Der Haken für die Annotationen aus Spec 8.1 (WB-329)."""
    from picknick.assistant import vorschlaege

    ergebnis = _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")

    wurzel = _einer(spans, "chat.turn")
    erwartet = f"{wurzel.context.span_id:016x}"
    ids = [m["span_id"] for m in vorschlaege.verlauf(con, ergebnis.order_id)]
    assert ids == [erwartet, erwartet]


def test_ohne_tracer_bleibt_span_id_leer(con):
    """Kein Tracer, kein Span, keine ID.

    Eine Null-ID (`0000000000000000`) wäre schlimmer als `NULL`: sie sähe aus
    wie ein Verweis und zeigte ins Leere.
    """
    from picknick.assistant import vorschlaege

    ergebnis = _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")
    assert [m["span_id"]
            for m in vorschlaege.verlauf(con, ergebnis.order_id)] == [None,
                                                                      None]


# --------------------------------------------------------------------------
# Fällt Phoenix aus, fällt der Shop nicht aus (Spec 7.3)

class HaengenderExporter(SpanExporter):
    """Ein Collector, der die Verbindung annimmt und dann schweigt.

    Der Ausfall, den es zu überstehen gilt. Der echte OTLP-Exporter braucht
    dafür nicht einmal ein schwarzes Loch: gegen einen schlicht geschlossenen
    Port versucht er es mit wachsender Pause wieder und kostet gemessen
    6,44 s je Span, 34,93 s für die fünf Spans eines Chat-Zugs. Hier steht
    statt dessen ein blockierender Exporter, weil ein Test keine 35 Sekunden
    warten soll, um dasselbe zu zeigen.
    """

    def __init__(self):
        self.los = threading.Event()
        self.angefasst = threading.Event()

    def export(self, spans):
        self.angefasst.set()
        self.los.wait(30)
        return SpanExportResult.FAILURE

    def shutdown(self):
        self.los.set()


def test_ein_haengender_collector_haelt_den_chat_zug_nicht_auf(con):
    exporter = HaengenderExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        tracermodul.NichtBlockierend(SimpleSpanProcessor(exporter)))
    obs.setze_provider(provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    try:
        begonnen = time.monotonic()
        ergebnis = _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")
        gedauert = time.monotonic() - begonnen
    finally:
        OpenAIInstrumentor().uninstrument()
        exporter.los.set()

    # Der Export hängt (der Exporter wurde angefasst und wartet noch) …
    assert exporter.angefasst.wait(5)
    # … der Chat-Zug ist trotzdem fertig, vollständig und schnell.
    assert ergebnis.n_produkte == 2
    assert gedauert < 2.0, f"Der Zug brauchte {gedauert:.1f} s"


def test_ein_kaputter_exporter_laesst_den_zug_nicht_scheitern(con):
    """Nicht bloss nicht blockieren — auch nicht scheitern."""

    class Kaputt(SpanExporter):
        def export(self, spans):
            raise RuntimeError("Collector weg")

        def shutdown(self):
            pass

    provider = TracerProvider()
    provider.add_span_processor(
        tracermodul.NichtBlockierend(SimpleSpanProcessor(Kaputt())))
    obs.setze_provider(provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    try:
        ergebnis = _butter_und_zwiebeln(con).turn(con, "Butter und Zwiebeln")
    finally:
        OpenAIInstrumentor().uninstrument()

    assert ergebnis.n_produkte == 2
    assert ergebnis.chat_message_id is not None


def test_ohne_phoenix_richtet_einrichten_nichts_ein_und_wirft_nicht():
    """Ein Endpunkt, der nicht existiert, ist kein Grund zu scheitern."""
    obs.abbauen()
    provider = obs.einrichten(endpunkt="nicht mal eine url",
                              projekt="Testprojekt")
    # Entweder Phoenix schluckt den Unsinn (dann steht ein Provider da, der
    # ins Leere exportiert) oder es wirft — beides ist in Ordnung, solange
    # der Aufruf zurückkommt und `tracer()` benutzbar bleibt.
    assert obs.tracer() is not None
    if provider is not None:
        provider.shutdown()
        obs.abbauen()


def test_tracing_laesst_sich_ueber_die_umgebung_abschalten():
    assert not obs.an({"PICKNICK_TRACING": "0"})
    assert not obs.an({"PICKNICK_TRACING": "aus"})
    assert obs.an({})
    assert obs.an({"PICKNICK_TRACING": "1"})


# --------------------------------------------------------------------------
# Der Prozessor selbst

def test_nicht_blockierend_verliert_keinen_span():
    """Kein Batching heisst: nichts wird zusammengefasst, nichts fällt weg.

    Der Unterschied zum `BatchSpanProcessor`, der laut Spec 7.3 gemessen
    6.615 von 8.408 Spans durchgelassen hat.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    prozessor = tracermodul.NichtBlockierend(SimpleSpanProcessor(exporter))
    provider.add_span_processor(prozessor)
    t = provider.get_tracer("test")
    for i in range(2000):
        with t.start_as_current_span(f"s{i}"):
            pass
    assert prozessor.force_flush(10_000)
    assert len(exporter.get_finished_spans()) == 2000
    assert prozessor.verworfen == 0
    prozessor.shutdown()


# --------------------------------------------------------------------------
# Der Shop weiss, ob seine eigene Beobachtung ankommt (WB-377)
#
# Ohne diese Buchführung kann der Shop über den Tracer nur sagen, dass er
# eingerichtet ist. Der `SimpleSpanProcessor` verwirft das Ergebnis von
# `export()`, danach sieht ein abgelehnter Span aus wie ein angekommener — und
# eine Statusseite, die daraufhin „Tracing läuft" behauptet, wäre genau die
# stille Lüge, gegen die dieses Projekt gebaut ist.

def _mit_buchfuehrung(exporter):
    """Derselbe Aufbau wie im Betrieb: Schlange, Prozessor, Buchführung."""
    prozessor = SimpleSpanProcessor(tracermodul.Buchfuehrend(exporter))
    provider = TracerProvider()
    schlange = tracermodul.NichtBlockierend(prozessor)
    provider.add_span_processor(schlange)
    return provider, schlange


def test_der_tracerstand_zaehlt_die_angekommenen_spans():
    exporter = InMemorySpanExporter()
    provider, schlange = _mit_buchfuehrung(exporter)
    t = provider.get_tracer("test")
    for i in range(3):
        with t.start_as_current_span(f"s{i}"):
            pass
    assert schlange.force_flush(10_000)

    stand = obs.tracerstand(provider)
    assert stand["gemessen"] is True
    assert stand["angekommen"] == 3
    assert stand["gescheitert"] == 0
    assert stand["zuletzt_ok"] is not None
    assert stand["zuletzt_fehler"] is None
    schlange.shutdown()


def test_ein_ablehnender_collector_faellt_im_tracerstand_auf():
    """Der Fall, den es zu sehen gilt: Spans gehen raus und kommen nie an."""

    class Ablehnend(SpanExporter):
        def export(self, spans):
            return SpanExportResult.FAILURE

        def shutdown(self):
            pass

    provider, schlange = _mit_buchfuehrung(Ablehnend())
    t = provider.get_tracer("test")
    for i in range(2):
        with t.start_as_current_span(f"s{i}"):
            pass
    assert schlange.force_flush(10_000)

    stand = obs.tracerstand(provider)
    assert stand["angekommen"] == 0
    assert stand["gescheitert"] == 2
    assert stand["zuletzt_fehler"] is not None
    assert "nimmt die Spans nicht an" in stand["grund"]
    schlange.shutdown()


def test_ein_werfender_exporter_wird_gebucht_und_nicht_weitergereicht():
    """Die Buchführung darf den Zug nicht mitnehmen — nur mitschreiben."""

    class Kaputt(SpanExporter):
        def export(self, spans):
            raise RuntimeError("Collector weg")

        def shutdown(self):
            pass

    buch = tracermodul.Buchfuehrend(Kaputt())
    assert buch.export([object()]) is SpanExportResult.FAILURE
    assert buch.gescheitert == 1
    assert "RuntimeError: Collector weg" in buch.grund


def test_ohne_buchfuehrung_sagt_der_stand_unbekannt_statt_null():
    """Eine 0 wäre hier erfunden — der Provider misst schlicht nicht mit.

    Genau der Zustand eines von Hand gesetzten Providers (`setze_provider`).
    Die Statusseite muss „unbekannt" schreiben können und nicht „nichts
    angekommen".
    """
    provider = TracerProvider()
    schlange = tracermodul.NichtBlockierend(
        SimpleSpanProcessor(InMemorySpanExporter()))
    provider.add_span_processor(schlange)
    stand = obs.tracerstand(provider)
    assert stand["gemessen"] is False
    assert stand["angekommen"] == 0 and stand["zuletzt_ok"] is None
    schlange.shutdown()


def test_der_tracerstand_geht_nicht_ins_netz_und_haelt_auch_ohne_provider():
    """Der Aufruf hängt an einer Seite, die jemand nur aufmacht, um
    nachzusehen: er darf weder Phoenix fragen noch die Box wecken."""
    obs.abbauen()

    def _nie(*a, **k):
        raise AssertionError("tracerstand() wollte ins Netz.")

    echt = httpx.Client.request
    httpx.Client.request = _nie
    try:
        stand = obs.tracerstand()
    finally:
        httpx.Client.request = echt
    assert stand["eingerichtet"] is False
    # Wohin es GINGE, ist auch ohne Provider eine beantwortbare Frage.
    assert stand["endpunkt"] and stand["projekt"]


# --------------------------------------------------------------------------
# Was gerechnet wurde, steht im Span (WB-362)
#
# Ohne diesen Span ist später nicht nachvollziehbar, warum zwei Packungen im
# Korb liegen und nicht eine — und ausgerechnet der Fall „nicht ausrechenbar"
# wäre unsichtbar, obwohl dort die Zahl aus einer Vorgabe stammt und nicht aus
# einer Rechnung.

def test_die_packungsrechnung_steht_im_span(con, spans):
    zwiebel = _pid(con, "Zwiebeln")          # unit_text: „1 kg"
    rid = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": zwiebel, "amount": 400, "unit": "g"}])
    recipes.in_den_korb(con, rid, portionen=8)

    span = _einer(spans, "korb.menge")
    a = span.attributes
    assert a["picknick.servings"] == 8
    assert a["picknick.need_added"] == 800.0      # 400 g für 4 -> 800 g für 8
    assert a["picknick.need_amount"] == 800.0     # die Summe an der Zeile
    assert a["picknick.need_unit"] == "g"
    assert a["picknick.pack_text"] == "1 kg"
    assert a["picknick.pack_amount"] == 1000.0
    assert a["picknick.computable"] is True
    assert a["picknick.packages"] == 1
    assert a["picknick.qty"] == 1
    assert "800 g gebraucht" in a[SpanAttributes.OUTPUT_VALUE]


def test_der_span_zeigt_das_zusammenzaehlen_ueber_zwei_rezepte(con, spans):
    zwiebel = _pid(con, "Zwiebeln")
    for name in ("Sugo", "Suppe"):
        rid = recipes.anlegen(con, name, servings=4, zutaten=[
            {"product_id": zwiebel, "amount": 600, "unit": "g"}])
        recipes.in_den_korb(con, rid)

    spans_ = [s for s in spans.get_finished_spans() if s.name == "korb.menge"]
    assert len(spans_) == 2
    # Der EINZELNE Beitrag bleibt gleich, die SUMME wächst — erst der
    # Unterschied zwischen beiden macht das Zusammenzählen im Trace sichtbar.
    assert [s.attributes["picknick.need_added"] for s in spans_] == [600.0, 600.0]
    assert [s.attributes["picknick.need_amount"] for s in spans_] == [600.0, 1200.0]
    assert [s.attributes["picknick.packages"] for s in spans_] == [1, 2]


def test_nicht_ausrechenbar_steht_ausdruecklich_im_span(con, spans):
    """Ein fehlendes `packages` allein wäre nicht filterbar — und genau diese
    Fälle sind die, die man in Phoenix suchen will."""
    zwiebel = _pid(con, "Zwiebeln")
    rid = recipes.anlegen(con, "Suppe", servings=4, zutaten=[
        {"product_id": zwiebel, "amount": 2, "unit": "Stange/n"}])
    recipes.in_den_korb(con, rid)

    a = _einer(spans, "korb.menge").attributes
    assert a["picknick.computable"] is False
    assert "picknick.packages" not in a
    assert "1 kg" in a["picknick.reason"]


def test_ein_griff_ins_regal_erzeugt_keinen_rechenspan(con, spans):
    """Ein „+" an der Kachel rechnet nichts aus. Ein Span, der so aussähe, als
    hätte er es getan, wäre ein leerer Span mit einer Behauptung."""
    from picknick import orders

    orders.einlegen(con, product_id=_pid(con, "Zwiebeln"), qty=2)
    assert "korb.menge" not in _nach_namen(spans)
