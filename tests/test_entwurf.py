"""Tests für den Rezeptentwurf aus einem Chat-Zug (WB-337).

**Der Kerntest steht unter „Klopapier landet im Korb und nie im Rezept".**
Alles andere in dieser Datei hängt daran: der Entwurf entsteht nur, wo eine
echte Zutatenliste dahintersteht (der Chefkoch-Weg), die Zugehörigkeit hängt
an der Vorschlagszeile und überlebt Ja/Nein, und gespeichert wird erst beim
Abschicken.

Kein Test geht ins Netz: Chefkoch ist ein Doppelgänger mit einem erfundenen
Rezept, das Modell ein `FakeLLM` mit fester Antwort, die Box wird
untergeschoben. Das erfundene Rezept ist Absicht — die aufgezeichnete
Pho-Fixture hat 23 Zutaten, und ein Test, der 23 Zeilen sortiert, prüft nicht
mehr die Trennung, sondern die Fixture.
"""
import json

import pytest

from fastapi.testclient import TestClient

from picknick import db, orders, recipes
from picknick.assistant import chat as chatmodul
from picknick.assistant import entwurf as entwuerfe
from picknick.assistant import vorschlaege
from picknick.gerichte import lauf, quelle, speicher
from picknick.llm import wake
from picknick.llm.client import Antwort
from picknick.web import app as webapp

# --------------------------------------------------------------------------
# Doppelgänger

#: Ein erfundenes Chefkoch-Rezept mit drei Zutaten und einer Portionszahl.
BOLO = {
    "id": "42", "title": "Spaghetti Bolognese al Forno", "servings": 4,
    "siteUrl": "https://www.chefkoch.de/rezepte/42/",
    "instructions": "Alles kochen.",
    "ingredientGroups": [{"header": None, "ingredients": [
        {"name": "Hackfleisch, gemischtes", "amount": 500.0, "unit": "g"},
        {"name": "Tomaten, passierte", "amount": 500.0, "unit": "ml"},
        {"name": "Spaghetti", "amount": 400.0, "unit": "g"},
    ]}]}


class FakeChefkoch:
    """Chefkoch, aufgezeichnet — über dieselben URLs wie die echte API."""

    def get(self, url):
        if "?query=" in url:
            return _JSON({"count": 1, "results": [
                {"recipe": {"id": BOLO["id"], "title": BOLO["title"],
                            "rating": {"rating": 4.7, "numVotes": 900},
                            "siteUrl": BOLO["siteUrl"]}}]})
        return _JSON(BOLO)


class _JSON:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


class FakeLLM:
    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        self.aufrufe.append({"nachrichten": list(nachrichten), **weitere})
        if not self.antworten:
            raise AssertionError(
                f"Das Modell wurde {len(self.aufrufe)}-mal gefragt, es liegen "
                "aber nicht so viele Antworten bereit.")
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class NieGefragt:
    """Ein Modell, das jeden Aufruf als Testfehler meldet."""

    def modell(self, **_):
        raise AssertionError("Das Modell wurde nach dem Kürzel gefragt.")

    def chat(self, *_, **__):
        raise AssertionError(
            "Das Modell wurde gefragt, obwohl kein Modell nötig war.")


class Box:
    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="fake")


# --------------------------------------------------------------------------
# Katalog und Aufbau

ZUSATZ = [
    ("hack1", "Rinderhackfleisch 500 g", "Fleisch", "Rind", "Hackfleisch"),
    # Ein zweites Hackfleisch, damit es zu „Hackfleisch" überhaupt eine
    # ALTERNATIVE gibt (WB-359). Ohne sie liesse sich die Korrektur nicht
    # prüfen: aufgehoben wird, was die Suche vorgelegt hat, und angeboten
    # wird davon alles ausser dem Vorschlag selbst.
    ("hack2", "Hackfleisch vom Rind Bio 400 g", "Fleisch", "Rind",
     "Hackfleisch"),
    ("toma1", "Passierte Tomaten 500 g", "Konserven", "Tomaten", "Passata"),
    ("nude1", "Spaghetti No. 5 500 g", "Nudeln", "Pasta", "Spaghetti"),
    ("klo1", "Toilettenpapier 10 Rollen", "Haushalt", "Papier",
     "Toilettenpapier"),
]


def _zusatz(con):
    for external_id, name, l1, l2, l3 in ZUSATZ:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 199, '500 g', ?, ?, ?)",
            (external_id, name, l1, l2, l3))
    con.commit()


@pytest.fixture
def db_pfad(vorlagen, tmp_path):
    """Der Katalog als DATEI — die Oberflächentests unten öffnen sie selbst."""
    return vorlagen.datei(tmp_path / "picknick.db", "entwurf_katalog",
                          vorlagen.katalog, _zusatz)


@pytest.fixture
def con(db_pfad):
    c = db.connect(db_pfad)
    yield c
    c.close()


def _pid(con, teil):
    return con.execute("SELECT id FROM product WHERE name LIKE ?",
                       (f"%{teil}%",)).fetchone()["id"]


def _extract(*paare):
    return json.dumps(
        {"begriffe": [{"suchbegriffe": list(b) if isinstance(b, tuple) else [b],
                       "menge": m} for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _bolo_geholt(con):
    lauf.hole_eines(con, FakeChefkoch(), "Spaghetti Bolognese", pause_s=0,
                    schreib=lambda _: None)


def _zug(con, satz="alles für Spaghetti Bolognese, und Klopapier",
         mit_klopapier=True, erfunden=None):
    """Ein Chefkoch-Zug: drei Zutaten aus dem Rezept, dazu der Rest im Satz.

    `mit_klopapier` lässt das Modell selbst einen Begriff für den Rest
    nennen. **Seit WB-370 ist das der ZUFALL und nicht mehr der Normalfall:**
    der Rest steht nicht mehr im Prompt von Stufe 1, das Modell sieht ihn
    also gar nicht. Der Fall bleibt trotzdem geprüft — nennt irgendetwas den
    Rest schon, darf er nicht ein zweites Mal danebengelegt werden.

    `erfunden` hängt einen Begriff an, der zu KEINER Zutat des Rezepts
    gehört. Das ist der blinde Fleck, an dem WB-337 scheiterte: das
    Sicherheitsnetz hielt den Rest dann für aufgegriffen.
    """
    begriffe = [(("gemischtes Hackfleisch", "Hackfleisch"), 1),
                (("passierte Tomaten", "Tomaten"), 1),
                (("Spaghetti",), 1)]
    wahl = [("gemischtes Hackfleisch", _pid(con, "Rinderhackfleisch"), 1),
            ("passierte Tomaten", _pid(con, "Passierte Tomaten"), 1),
            ("Spaghetti", _pid(con, "Spaghetti No. 5"), 1)]
    if erfunden:
        begriffe.append(((erfunden,), 1))
    if mit_klopapier:
        begriffe.append((("Klopapier", "Toilettenpapier"), 1))
        wahl.append(("Klopapier", _pid(con, "Toilettenpapier"), 1))
    llm = FakeLLM(_extract(*begriffe), _choose(*wahl))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    return agent.turn(con, satz)


def _alles_ja(con, ergebnis):
    return vorschlaege.alle_entscheiden(con, ergebnis.chat_message_id,
                                        vorschlaege.BEHALTEN)


# --------------------------------------------------------------------------
# Der Kerntest: Klopapier in den Korb, nie ins Rezept

def test_klopapier_liegt_im_korb_und_steht_nie_im_rezept(con):
    """Der Satz aus dem Ticket, von Anfang bis Ende.

    Er prüft beide Hälften, und nur zusammen sind sie die Zusicherung: das
    Klopapier ist da, wo es hingehört (im Korb), und es ist NICHT da, wo es
    nicht hingehört (im Rezept). Ein Test, der nur die zweite Hälfte prüft,
    bliebe auch dann grün, wenn der Zug das Klopapier ganz verschluckt.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    assert ergebnis.weg == chatmodul.WEG_QUELLE
    _alles_ja(con, ergebnis)

    korb = [z["name"] for z in orders.inhalt(con)]
    assert any("Toilettenpapier" in n for n in korb)

    orders.abschicken(con)
    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    namen = [z["name"] for z in rezept["zutaten"]]
    assert namen == ["Rinderhackfleisch 500 g", "Passierte Tomaten 500 g",
                     "Spaghetti No. 5 500 g"]
    assert not any("Toilettenpapier" in n for n in namen)


def _rezept_id(con, name):
    row = con.execute("SELECT id FROM recipe WHERE name = ?",
                      (name,)).fetchone()
    assert row is not None, f"Kein Rezept namens {name!r}."
    return int(row["id"])


def test_klopapier_bekommt_gar_keine_zugehoerigkeit(con):
    """Die Trennung steht schon an der Vorschlagszeile — vor jeder Entscheidung.

    `dish_item IS NULL` heisst „gehört zu keinem Gericht". Genau das macht
    das Modellfeld aus dem Ticket überflüssig: die Auskunft kommt aus
    `herkunft.zuordnen` und kostet kein Token.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    nach_name = {v["name"]: v for v in ergebnis.vorschlaege}
    assert nach_name["Toilettenpapier 10 Rollen"]["dish_item"] is None
    assert nach_name["Rinderhackfleisch 500 g"]["dish_item"] == 1
    assert ergebnis.entwurf == "Spaghetti Bolognese"
    assert ergebnis.entwurf_zutaten == 3


def test_ein_vom_modell_uebergangener_rest_geht_nicht_verloren(con):
    """Gemessen an der echten Box: das Modell lässt den Rest einfach weg.

    Am 2026-08-28 kamen zu „alles für Spaghetti Bolognese, und Klopapier"
    elf Begriffe zurück — alle elf aus Chefkochs Zutatenliste, keiner für
    das Klopapier. Es verschwand still, und damit fiel genau der Fall des
    Tickets aus: es soll im KORB liegen und nie im Rezept.

    Das Sicherheitsnetz hängt den Rest als eigene Zeile an. Er bekommt keine
    Herkunftszutat und damit auch keinen Platz im Entwurf.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con, mit_klopapier=False)

    freitexte = [v["name"] for v in ergebnis.vorschlaege if v["ist_freitext"]]
    assert "Klopapier" in freitexte
    klo = next(v for v in ergebnis.vorschlaege if v["name"] == "Klopapier")
    assert klo["dish_item"] is None
    assert ergebnis.entwurf_zutaten == 3

    _alles_ja(con, ergebnis)
    assert any(z["free_text"] == "Klopapier" for z in orders.inhalt(con))
    orders.abschicken(con)
    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert all("Klopapier" != z["name"] for z in rezept["zutaten"])


def test_ein_erfundener_begriff_verschluckt_den_rest_nicht(con):
    """Der blinde Fleck von WB-337, als Test (WB-370).

    Das Sicherheitsnetz von WB-337 hängte den Rest nur an, wenn KEIN
    einziger Begriff ohne Herkunftszutat zurückkam. Nennt das Modell etwas,
    das in keiner Zutat des Rezepts steht — „Eier" gegen Chefkochs „Ei(er)",
    „Dose Tomaten" gegen „Tomaten, geschälte" —, hielt es den Rest für
    aufgegriffen und liess ihn liegen.

    **Gemessen am 2026-08-28** gegen die echte Box, 35 Chefkoch-Gerichte:
    in 11 von 35 Zügen (31 %) kam mindestens ein Begriff ohne
    Herkunftszutat zurück, und in 10 von 35 Zügen (29 %) verschwand der
    Rest dadurch still. Das ist kein Randfall, sondern jeder dritte Zug.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con, mit_klopapier=False, erfunden="Eier")

    freitexte = [v["name"] for v in ergebnis.vorschlaege if v["ist_freitext"]]
    assert "Klopapier" in freitexte
    _alles_ja(con, ergebnis)
    assert any(z["free_text"] == "Klopapier" for z in orders.inhalt(con))


def test_der_rest_steht_nicht_mehr_im_prompt_von_stufe_1(con):
    """Was das Modell nicht sieht, kann es nicht übergehen (WB-370).

    **Gemessen am 2026-08-28** gegen die echte Box: von 35 Chefkoch-Zügen
    mit einem Rest im Satz griff das Modell ihn in 3 auf, und übersetzt hat
    es ihn in keinem einzigen. Die Prompt-Zeile kostete also den stillen
    Verlust und brachte die Übersetzung nicht, für die sie dastand.
    """
    _bolo_geholt(con)
    begriffe = [(("gemischtes Hackfleisch", "Hackfleisch"), 1)]
    llm = FakeLLM(_extract(*begriffe),
                  _choose(("gemischtes Hackfleisch",
                           _pid(con, "Rinderhackfleisch"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    ergebnis = agent.turn(con, "alles für Spaghetti Bolognese, und Klopapier")

    prompt = llm.aufrufe[0]["nachrichten"][-1]["content"]
    assert "Klopapier" not in prompt
    assert "Klopapier" in [v["name"] for v in ergebnis.vorschlaege]


def test_der_verlorene_rest_steht_in_der_meldung(con):
    """Der Verlust muss sichtbar sein, nicht nur behoben (WB-370).

    Ohne Prompt-Zeile gibt es keine Übersetzung mehr: „Klopapier" findet im
    Katalog nichts, „Toilettenpapier" schon. Die Zeile liegt trotzdem im
    Korb — und die Antwort sagt beides, statt es zu verschweigen.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con, mit_klopapier=False)
    assert "Klopapier" in ergebnis.meldung
    assert "stand daneben im Satz" in ergebnis.meldung
    assert "Ohne Katalogtreffer" in ergebnis.meldung


def test_ein_uebersetzter_rest_wird_nicht_verdoppelt(con):
    """Steht der Rest schon auf dem Zettel, kommt er nicht zweimal.

    Bis WB-370 war das der Normalfall, für den die Prompt-Zeile dastand
    („Klopapier" -> „Toilettenpapier"); gemessen liefert die echte Box ihn
    nie. Geprüft bleibt er trotzdem: der Rest kann mit einer Zutat des
    Rezepts zusammenfallen („alles für Lasagne und Tomaten"), und dann darf
    er nicht ein zweites Mal danebenstehen.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    namen = [v["name"] for v in ergebnis.vorschlaege]
    assert "Toilettenpapier 10 Rollen" in namen
    assert "Klopapier" not in namen


# --------------------------------------------------------------------------
# Erst beim Abschicken

def test_vor_dem_abschicken_gibt_es_das_rezept_noch_nicht(con):
    """Bis zum Abschicken darf sie ihre Meinung ändern (wie WB-329).

    Geprüft wird an `recipe_item`: die `recipe`-Zeile gibt es schon — sie kam
    mit dem Chefkoch-Abruf und trägt die Zubereitung. Was es NICHT geben
    darf, sind verknüpfte Produkte; erst sie machen aus dem geholten Rezept
    einen Einkaufszettel, und erst sie lassen `rezeptweg.erkenne` greifen.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)

    assert con.execute("SELECT count(*) n FROM recipe_item").fetchone()["n"] == 0
    assert [r["n_zutaten"] for r in recipes.rezepte(con)] == [0]


def test_wer_den_chat_wegklickt_hinterlaesst_kein_halbes_rezept(con):
    """Kein Abschicken, kein Rezept — auch nicht die Hälfte davon."""
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    # Die Verbindung schliessen und neu öffnen ist das Nächste an
    # „Browser zu": was jetzt nicht in der Datenbank steht, entsteht nie.
    assert con.execute("SELECT count(*) n FROM recipe_item").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Der Entwurf ist editierbar

def test_der_ueberschriebene_name_landet_im_rezept(con):
    """Nicht der vorgeschlagene, und nicht der Titel der Rezeptseite.

    Chefkoch nennt das Rezept „Spaghetti Bolognese al Forno"; danach fragt
    niemand. Was zählt, ist der Name, unter dem sie es das nächste Mal
    tippt.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    entwuerfe.benennen(con, ergebnis.chat_message_id, "Bolo wie immer")
    orders.abschicken(con)

    namen = [r["name"] for r in recipes.rezepte(con)]
    assert namen == ["Bolo wie immer"]
    assert "al Forno" not in namen[0]


def test_ein_leerer_name_faellt_auf_das_gericht_zurueck(con):
    _bolo_geholt(con)
    ergebnis = _zug(con)
    e = entwuerfe.benennen(con, ergebnis.chat_message_id, "   ")
    assert e["name"] == "Spaghetti Bolognese"


def test_eine_zeile_laesst_sich_aus_dem_entwurf_nehmen_und_zurueckholen(con):
    """„Raus" ist kein „Nein": die Zeile bleibt im Korb.

    Das ist der Unterschied, an dem das Eval-Label hängt (Spec 8.1). Wer den
    Parmesan kauft, ihn aber nicht im Rezept haben will, hat dem Modell
    nichts vorzuwerfen.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    spaghetti = next(v for v in ergebnis.vorschlaege
                     if "Spaghetti" in v["name"])

    entwuerfe.zeile_setzen(con, spaghetti["id"], drin=False)
    e = entwuerfe.zu_nachricht(con, ergebnis.chat_message_id)
    assert e["n_drin"] == 2
    # Die Entscheidung bleibt „behalten" — im Korb liegt sie weiter.
    assert vorschlaege.eine(con, spaghetti["id"])["behalten"]

    bestellung = orders.abschicken(con)
    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert not any("Spaghetti" in z["name"] for z in rezept["zutaten"])
    # Im Einkauf steht sie trotzdem — „raus" gilt dem Rezept, nicht dem Korb.
    assert any("Spaghetti" in z["name"]
               for z in orders.posten(con, bestellung["id"]))


def test_zurueckgeholt_steht_die_zeile_wieder_im_rezept(con):
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    spaghetti = next(v for v in ergebnis.vorschlaege
                     if "Spaghetti" in v["name"])
    entwuerfe.zeile_setzen(con, spaghetti["id"], drin=False)
    entwuerfe.zeile_setzen(con, spaghetti["id"], drin=True)

    orders.abschicken(con)
    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert len(rezept["zutaten"]) == 3


def test_klopapier_laesst_sich_nicht_in_den_entwurf_holen(con):
    """Der Fehler, gegen den dieses Ticket geschrieben wurde, von Hand."""
    _bolo_geholt(con)
    ergebnis = _zug(con)
    klo = next(v for v in ergebnis.vorschlaege
               if "Toilettenpapier" in v["name"])
    with pytest.raises(entwuerfe.EntwurfFehler):
        entwuerfe.zeile_setzen(con, klo["id"], drin=True)


def test_die_menge_im_entwurf_laesst_sich_aendern(con):
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    hack = next(v for v in ergebnis.vorschlaege if "Rinderhack" in v["name"])
    assert (hack["need_amount"], hack["need_unit"]) == (500.0, "g")

    entwuerfe.bedarf_setzen(con, hack["id"], "750")
    orders.abschicken(con)
    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    zutat = next(z for z in rezept["zutaten"] if "Rinderhack" in z["name"])
    assert (zutat["amount"], zutat["unit"]) == (750.0, "g")


def test_ein_verworfener_entwurf_wird_kein_rezept(con):
    """Was von selbst entsteht, braucht einen Weg, es zu lassen."""
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    entwuerfe.verwerfen(con, ergebnis.chat_message_id)
    orders.abschicken(con)

    assert [r["n_zutaten"] for r in recipes.rezepte(con)] == [0]


def test_ein_zurueckgenommenes_verwerfen_ergibt_doch_ein_rezept(con):
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    entwuerfe.verwerfen(con, ergebnis.chat_message_id)
    entwuerfe.verwerfen(con, ergebnis.chat_message_id, ja=False)
    orders.abschicken(con)

    assert recipes.rezept(con, _rezept_id(con,
                                          "Spaghetti Bolognese"))["n_zutaten"] == 3


# --------------------------------------------------------------------------
# Nur die behaltenen Gerichtszutaten

def test_eine_verworfene_gerichtszutat_steht_nicht_im_rezept(con):
    _bolo_geholt(con)
    ergebnis = _zug(con)
    for v in ergebnis.vorschlaege:
        entscheidung = (vorschlaege.VERWORFEN if "Spaghetti" in v["name"]
                        else vorschlaege.BEHALTEN)
        vorschlaege.entscheiden(con, v["id"], entscheidung)
    orders.abschicken(con)

    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert [z["name"] for z in rezept["zutaten"]] == [
        "Rinderhackfleisch 500 g", "Passierte Tomaten 500 g"]


def test_eine_offene_zeile_steht_nicht_im_rezept(con):
    """Nie entschieden heisst nie bestätigt — und nicht „stillschweigend ja".

    Dieselbe Regel wie beim Eval-Label (`offen` zählt dort nicht mit) und
    dieselbe wie beim Korb: ohne „Ja" liegt nichts drin.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    hack = next(v for v in ergebnis.vorschlaege if "Rinderhack" in v["name"])
    vorschlaege.entscheiden(con, hack["id"], vorschlaege.BEHALTEN)
    orders.abschicken(con)

    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert [z["name"] for z in rezept["zutaten"]] == ["Rinderhackfleisch 500 g"]


def test_alle_gerichtszutaten_verworfen_ergibt_kein_rezept(con):
    _bolo_geholt(con)
    ergebnis = _zug(con)
    for v in ergebnis.vorschlaege:
        entscheidung = (vorschlaege.BEHALTEN if "Toilettenpapier" in v["name"]
                        else vorschlaege.VERWORFEN)
        vorschlaege.entscheiden(con, v["id"], entscheidung)
    orders.abschicken(con)

    assert [r["n_zutaten"] for r in recipes.rezepte(con)] == [0]
    # Der Korb ist trotzdem eine Bestellung geworden.
    assert orders.bestellungen(con, "offen")


def test_ein_zurueckgenommenes_nein_wirkt_sich_auf_das_rezept_aus(con):
    """Der Rückweg aus WB-361 reicht bis ins Rezept.

    Ein zurückgenommenes „Nein" steht auf `offen` — und offen heisst nicht
    bestätigt. Die Zutat kommt also NICHT ins Rezept, solange sie dort steht;
    ein zweites „Ja" holt sie zurück.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    hack = next(v for v in ergebnis.vorschlaege if "Rinderhack" in v["name"])
    vorschlaege.entscheiden(con, hack["id"], vorschlaege.VERWORFEN)
    vorschlaege.entscheiden(con, hack["id"], vorschlaege.OFFEN)

    e = entwuerfe.zu_nachricht(con, ergebnis.chat_message_id)
    assert e["n_drin"] == 2

    vorschlaege.entscheiden(con, hack["id"], vorschlaege.BEHALTEN)
    assert entwuerfe.zu_nachricht(con,
                                 ergebnis.chat_message_id)["n_drin"] == 3


def test_eine_korrektur_ersetzt_die_zutat_auch_im_rezept(con):
    """„Nein, sondern das da" (WB-359) — im Korb UND im Rezept.

    Die Korrekturzeile erbt die Zugehörigkeit zum Gericht. Ohne diese
    Vererbung fiele ausgerechnet die Zutat aus dem Rezept, bei der die
    Nutzerin am genauesten hingesehen hat.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    hack = next(v for v in ergebnis.vorschlaege if "Rinderhack" in v["name"])
    andere = [a for a in vorschlaege.alternativen(con, hack["id"])]
    assert andere, "Die Suche hat keine Alternative aufgehoben."

    vorschlaege.entscheiden(con, hack["id"], vorschlaege.OFFEN)
    korrektur = vorschlaege.korrigieren(con, hack["id"], andere[0]["id"])
    assert korrektur["dish_item"] == 1

    orders.abschicken(con)
    rezept = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    namen = [z["name"] for z in rezept["zutaten"]]
    assert andere[0]["name"] in namen
    assert "Rinderhackfleisch 500 g" not in namen


# --------------------------------------------------------------------------
# Kein Gericht -> kein Entwurf

def test_ohne_gericht_im_satz_entsteht_kein_entwurf(con):
    """„Milch und Klopapier" läuft wie vor diesem Ticket."""
    llm = FakeLLM(_extract((("Milch",), 1), (("Klopapier",), 1)),
                  _choose(("Klopapier", _pid(con, "Toilettenpapier"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    ergebnis = agent.turn(con, "Milch und Klopapier")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.entwurf is None
    assert entwuerfe.zu_nachricht(con, ergebnis.chat_message_id) is None
    assert all(v["dish_item"] is None for v in ergebnis.vorschlaege)

    _alles_ja(con, ergebnis)
    orders.abschicken(con)
    assert recipes.rezepte(con) == []


def test_der_reine_modellweg_bekommt_keinen_entwurf(con):
    """Auch mit erkanntem Gericht — solange die Zutaten geraten sind.

    Das ist die Entscheidung aus dem Modul-Docstring von `assistant.entwurf`:
    ein Rezept aus geratenen Zutaten wäre nicht bloss falsch, sondern
    DAUERHAFT falsch — es nähme ab dem nächsten Satz den Rezeptweg und
    wiederholte den Fehler ohne ein Modell dazwischen (WB-336).
    """
    erst = json.dumps({"gericht": "Lasagne", "begriffe": [
        {"suchbegriffe": ["Blätterteig"], "menge": 1}]}, ensure_ascii=False)
    llm = FakeLLM(erst, _choose())
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    ergebnis = agent.turn(con, "alles für Lasagne")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.gericht == "Lasagne"
    assert ergebnis.entwurf is None
    assert entwuerfe.zu_nachricht(con, ergebnis.chat_message_id) is None


def test_der_rezeptweg_legt_kein_zweites_rezept_an(con):
    """Das Rezept gibt es schon — ein Entwurf daneben wäre eine Dublette."""
    recipes.anlegen(con, "Bolognese", zutaten=[
        {"product_id": _pid(con, "Rinderhackfleisch")}])
    agent = chatmodul.Chat(NieGefragt(), wecker=Box())
    ergebnis = agent.turn(con, "alles für Bolognese, und Klopapier")

    assert ergebnis.weg == chatmodul.WEG_REZEPT
    assert ergebnis.entwurf is None
    _alles_ja(con, ergebnis)
    orders.abschicken(con)
    assert [r["name"] for r in recipes.rezepte(con)] == ["Bolognese"]


# --------------------------------------------------------------------------
# Und danach nimmt derselbe Satz den Rezeptweg

def test_danach_nimmt_derselbe_satz_den_rezeptweg_ohne_modell(con):
    """**Der Ertrag des ganzen Tickets**, gegen ein Modell, das wirft.

    Ein gespeichertes Rezept überspringt Stufe 1 bis 3 vollständig
    (`picknick.path = "recipe"`). Geprüft wird das mit `NieGefragt`: wird das
    Modell doch gefragt, ist der Test rot statt langsam.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    orders.abschicken(con)

    agent = chatmodul.Chat(NieGefragt(), wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    zweiter = agent.turn(con, "mach mal Spaghetti Bolognese")
    assert zweiter.weg == chatmodul.WEG_REZEPT
    assert zweiter.rezepte == ["Spaghetti Bolognese"]
    assert len(zweiter.vorschlaege) == 3


def test_ein_erneuter_abruf_setzt_den_namen_nicht_zurueck(con):
    """Sonst wäre der Umbenennung eine Frist von 90 Tagen gesetzt.

    `speicher.merken` überschreibt beim Erneuern alle Kopfdaten — bis WB-337
    auch den Namen. Danach hiesse das Rezept wieder wie die Rezeptseite, und
    der Rezeptweg fände es im Satz nicht mehr. Der Titel der Quelle geht
    dabei nicht verloren; er steht in `source_title`.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    orders.abschicken(con)

    # Der Speicher gilt 90 Tage — für den erneuten Abruf wird er hier
    # umgangen, wie es der Lauf von Hand auch tut.
    lauf.hole_eines(con, FakeChefkoch(), "Spaghetti Bolognese", pause_s=0,
                    schreib=lambda _: None)
    r = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert r["source_title"] == "Spaghetti Bolognese al Forno"
    assert r["n_zutaten"] == 3


# --------------------------------------------------------------------------
# Wohin gespeichert wird

def test_die_zutaten_gehen_in_das_geholte_rezept_mit_der_zubereitung(con):
    """Ein zweites Rezept daneben teilte Kochen und Einkaufen auf zwei Zeilen."""
    _bolo_geholt(con)
    geholt = speicher.zeile(con, "Spaghetti Bolognese")["recipe_id"]
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    orders.abschicken(con)

    assert len(recipes.rezepte(con)) == 1
    rezept = recipes.rezept(con, geholt)
    assert rezept["name"] == "Spaghetti Bolognese"
    assert rezept["instructions"] == "Alles kochen."
    assert rezept["servings"] == 4
    assert rezept["n_zutaten"] == 3


def test_ein_vorhandener_name_wird_nicht_stillschweigend_ueberschrieben(con):
    """Statt zu überschreiben entsteht eine zweite Fassung.

    Fragen kann hier niemand: gespeichert wird beim Abschicken, und da
    schaut die Nutzerin auf die Bestellliste.
    """
    # Ein LEERES Rezept desselben Namens. Hätte es Zutaten, führe der Satz
    # gar nicht über die Quelle, sondern über den Rezeptweg — dann gäbe es
    # keinen Entwurf und nichts zu überschreiben.
    recipes.anlegen(con, "Spaghetti Bolognese")
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    orders.abschicken(con)

    namen = sorted(r["name"] for r in recipes.rezepte(con))
    assert namen == ["Spaghetti Bolognese", "Spaghetti Bolognese (2)"]
    alt = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese"))
    assert alt["zutaten"] == []
    neu = recipes.rezept(con, _rezept_id(con, "Spaghetti Bolognese (2)"))
    assert neu["n_zutaten"] == 3


def test_zweimal_speichern_legt_nicht_zweimal_an(con):
    """`saved_at` ist die Sperre — dieselbe Rolle wie `eingelegt_at`."""
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)
    korb = ergebnis.order_id
    orders.abschicken(con)

    berichte = entwuerfe.speichern(con, korb)
    assert [b["grund"] for b in berichte] == ["schon gespeichert"]
    assert recipes.rezept(con,
                          _rezept_id(con, "Spaghetti Bolognese"))["n_zutaten"] == 3


def test_ein_kaputtes_rezept_kostet_nicht_die_bestellung(con, monkeypatch):
    """Die Bestellung ist der Nutzweg, das Rezept der Ertrag daneben."""
    _bolo_geholt(con)
    ergebnis = _zug(con)
    _alles_ja(con, ergebnis)

    def kaputt(*_, **__):
        raise RuntimeError("Die Sammlung ist gerade nicht ansprechbar.")

    monkeypatch.setattr(entwuerfe.recipes, "zutat_hinzufuegen", kaputt)
    bestellung = orders.abschicken(con)
    assert bestellung["state"] == "offen"


# --------------------------------------------------------------------------
# Mehrere Gerichte in einem Satz

def test_bei_zwei_gerichten_entsteht_der_entwurf_zum_ersten_und_sagt_es(con):
    """Mehrere Rezepte aus einem Satz sind nicht Teil des Tickets.

    Genommen wird das erste, und der Zug sagt es — genauso wichtig ist, dass
    die Zutaten des ZWEITEN nicht in das Rezept des ersten wandern.
    """
    _bolo_geholt(con)
    lauf.hole_eines(con, _NudelnPur(), "Spaghetti aglio e olio", pause_s=0,
                    schreib=lambda _: None)

    llm = FakeLLM(
        _extract((("gemischtes Hackfleisch", "Hackfleisch"), 1),
                 (("Knoblauch",), 1)),
        _choose(("gemischtes Hackfleisch", _pid(con, "Rinderhackfleisch"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    ergebnis = agent.turn(
        con, "alles für Spaghetti Bolognese und Spaghetti aglio e olio")

    assert ergebnis.entwurf == "Spaghetti Bolognese"
    assert "mehrere Gerichte" in ergebnis.meldung
    e = entwuerfe.zu_nachricht(con, ergebnis.chat_message_id)
    # Der Knoblauch gehört zum zweiten Gericht und steht deshalb nicht im
    # Entwurf des ersten.
    assert [z["name"] for z in e["zeilen"]] == ["Rinderhackfleisch 500 g"]


class _NudelnPur:
    """Ein zweites Chefkoch-Rezept, mit einer Zutat, die dem ersten fehlt."""

    REZEPT = {
        "id": "43", "title": "Spaghetti aglio e olio", "servings": 2,
        "siteUrl": "https://www.chefkoch.de/rezepte/43/",
        "instructions": "Auch kochen.",
        "ingredientGroups": [{"header": None, "ingredients": [
            {"name": "Knoblauchzehe(n)", "amount": 4.0, "unit": None}]}]}

    def get(self, url):
        if "?query=" in url:
            return _JSON({"count": 1, "results": [
                {"recipe": {"id": "43", "title": self.REZEPT["title"],
                            "rating": {"rating": 4.1, "numVotes": 10},
                            "siteUrl": self.REZEPT["siteUrl"]}}]})
        return _JSON(self.REZEPT)


# --------------------------------------------------------------------------
# Der Trace (Spec 7.1)

def test_der_zug_traegt_den_entwurf_im_span_vokabular(con):
    """`dish_draft` und `dish_items` — dieselbe Familie wie `dish` (WB-338).

    Ohne die beiden wäre später nicht zu sehen, warum dasselbe Gericht ab dem
    nächsten Satz `path = recipe` nimmt und gar kein Modell mehr kostet.
    """
    _bolo_geholt(con)
    ergebnis = _zug(con)
    assert (ergebnis.entwurf, ergebnis.entwurf_zutaten) == \
        ("Spaghetti Bolognese", 3)


def test_beim_abschicken_faellt_eine_annotation_zum_rezept_an(con):
    """Was am Span nur „kann" heisst, entscheidet sich beim Abschicken.

    Die Annotation steht auf demselben `chat.turn`-Span wie die Labels und
    entsteht im selben Moment — deshalb steht sie in `obs.labels` und nicht
    daneben.
    """
    from picknick.obs import labels

    _bolo_geholt(con)
    ergebnis = _zug(con)
    vorschlaege.span_setzen(con, ergebnis.chat_message_id, "abc123")
    _alles_ja(con, ergebnis)
    korb = ergebnis.order_id
    orders.abschicken(con)

    annos = [a for a in labels.annotationen(con, korb)
             if a["name"] == labels.NAME_REZEPT]
    assert len(annos) == 1
    assert annos[0]["result"]["label"] == labels.LABEL_GESPEICHERT
    assert "Spaghetti Bolognese" in annos[0]["result"]["explanation"]
    assert annos[0]["metadata"]["items"] == 3
    assert annos[0]["metadata"]["recipe_id"]


def test_ein_verworfener_entwurf_steht_auch_im_trace(con):
    """Ein Rezept, das NICHT entstand, ist die interessantere Zeile."""
    from picknick.obs import labels

    _bolo_geholt(con)
    ergebnis = _zug(con)
    vorschlaege.span_setzen(con, ergebnis.chat_message_id, "abc123")
    _alles_ja(con, ergebnis)
    entwuerfe.verwerfen(con, ergebnis.chat_message_id)
    korb = ergebnis.order_id
    orders.abschicken(con)

    anno = next(a for a in labels.annotationen(con, korb)
                if a["name"] == labels.NAME_REZEPT)
    assert anno["result"]["label"] == labels.LABEL_VERWORFEN


# --------------------------------------------------------------------------
# Die Oberfläche (Spec 9)
#
# Kein Browser: `fastapi.testclient` gegen dieselben Adressen, die die
# Vorlage in ihre Formulare schreibt. Was hier geprüft wird, ist der Weg, den
# eine Nutzerin mit dem Daumen nimmt — und der ist der einzige, der zählt.

def _web(db_pfad, tmp_path, agent):
    app = webapp.create_app(db_path=db_pfad, image_dir=tmp_path / "bilder",
                            chat=agent)
    return TestClient(app)


def _web_agent(con, *antworten):
    return chatmodul.Chat(FakeLLM(*antworten), wecker=Box(),
                          quelle=quelle.Quelle(holer=quelle.nicht_holen))


def _web_zug(con):
    """Dieselben zwei Modellantworten wie `_zug`, aber für die Oberfläche."""
    return _web_agent(
        con,
        _extract((("gemischtes Hackfleisch", "Hackfleisch"), 1),
                 (("passierte Tomaten", "Tomaten"), 1), (("Spaghetti",), 1),
                 (("Klopapier", "Toilettenpapier"), 1)),
        _choose(("gemischtes Hackfleisch", _pid(con, "Rinderhackfleisch"), 1),
                ("passierte Tomaten", _pid(con, "Passierte Tomaten"), 1),
                ("Spaghetti", _pid(con, "Spaghetti No. 5"), 1),
                ("Klopapier", _pid(con, "Toilettenpapier"), 1)))


def test_der_entwurf_steht_im_warenkorb_und_nennt_das_klopapier_nicht(
        con, db_pfad, tmp_path):
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    stueck = client.post(
        "/chat",
        data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
        headers={"HX-Request": "true"}).text

    assert "Rezeptentwurf" in stueck
    assert 'value="Spaghetti Bolognese"' in stueck
    # Der Vorschlag steht in der Liste darüber — im Entwurf nicht.
    entwurfsteil = stueck.split('class="entwurf"', 1)[1]
    assert "Toilettenpapier" not in entwurfsteil
    assert "Rinderhackfleisch" in entwurfsteil


def test_der_weg_mit_dem_daumen_von_der_frage_bis_zum_rezept(
        con, db_pfad, tmp_path):
    """Der ganze Ablauf des Tickets über HTTP, ohne einen einzigen Modulaufruf.

    Fragen, alles übernehmen, umbenennen, eine Zeile aus dem Entwurf nehmen,
    abschicken — und danach steht das Rezept mit den richtigen Zutaten da.
    """
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    client.post("/chat",
                data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
                headers={"HX-Request": "true"})
    mid = con.execute("SELECT max(id) AS id FROM chat_message"
                      " WHERE role = 'assistant'").fetchone()["id"]
    client.post(f"/chat/{mid}/alle?decision=kept",
                headers={"HX-Request": "true"})
    client.post(f"/chat/{mid}/entwurf/name",
                data={"name": "Bolo"}, headers={"HX-Request": "true"})
    spaghetti = con.execute(
        "SELECT s.id FROM chat_suggestion s JOIN product p ON p.id = s.product_id"
        " WHERE p.name LIKE 'Spaghetti No%'").fetchone()["id"]
    client.post(f"/chat/vorschlag/{spaghetti}/rezeptzeile?drin=0",
                headers={"HX-Request": "true"})

    antwort = client.post("/warenkorb/abschicken",
                          headers={"HX-Request": "true"})
    assert antwort.status_code == 204

    rezept = recipes.rezept(con, _rezept_id(con, "Bolo"))
    assert [z["name"] for z in rezept["zutaten"]] == [
        "Rinderhackfleisch 500 g", "Passierte Tomaten 500 g"]
    # Und das Klopapier liegt in der Bestellung.
    bestellung = orders.bestellungen(con, "offen")[0]
    assert any("Toilettenpapier" in z["name"]
               for z in orders.posten(con, bestellung["id"]))


def test_die_menge_laesst_sich_ueber_die_oberflaeche_aendern(
        con, db_pfad, tmp_path):
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    client.post("/chat",
                data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
                headers={"HX-Request": "true"})
    hack = con.execute(
        "SELECT s.id FROM chat_suggestion s JOIN product p ON p.id = s.product_id"
        " WHERE p.name LIKE 'Rinderhack%'").fetchone()["id"]
    stueck = client.post(f"/chat/vorschlag/{hack}/bedarf",
                         data={"menge": "0,25", "einheit": "kg"},
                         headers={"HX-Request": "true"}).text

    assert "Rezeptentwurf" in stueck
    v = vorschlaege.eine(con, hack)
    # In der Grundeinheit, wie überall (WB-362): 0,25 kg sind 250 g.
    assert (v["need_amount"], v["need_unit"]) == (250.0, "g")


def test_kein_rezept_daraus_und_wieder_zurueck(con, db_pfad, tmp_path):
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    client.post("/chat",
                data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
                headers={"HX-Request": "true"})
    mid = con.execute("SELECT max(id) AS id FROM chat_message"
                      " WHERE role = 'assistant'").fetchone()["id"]

    stueck = client.post(f"/chat/{mid}/entwurf/verwerfen?ja=1",
                         headers={"HX-Request": "true"}).text
    assert "Daraus wird kein Rezept." in stueck
    stueck = client.post(f"/chat/{mid}/entwurf/verwerfen?ja=0",
                         headers={"HX-Request": "true"}).text
    # Der Entwurf steht wieder da, mit Namen und Zeilen — er wurde nicht neu
    # zusammengesucht, sondern nie weggeworfen.
    assert 'value="Spaghetti Bolognese"' in stueck
    assert "Rinderhackfleisch" in stueck.split('class="entwurf"', 1)[1]
    # Bestätigt ist noch nichts, und die Vorlage verspricht auch kein Rezept,
    # das so nicht entstünde. Sie sagt es aber als Anfang und nicht als
    # Fehlschlag (WB-378): entschieden wurde hier noch gar nichts.
    assert "Beim Abschicken wird daraus das Rezept" not in stueck
    assert "So fängt das Rezept" in stueck
    assert "so entsteht kein Rezept" not in stueck
