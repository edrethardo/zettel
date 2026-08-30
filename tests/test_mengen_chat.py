"""Der Chat-Weg trägt Mengen (WB-369).

WB-362 hat das Rechnen gebaut — skalieren, je Produkt zusammenzählen, erst
danach aufrunden — und es griff nur dort, wo jemand ein gespeichertes Rezept
von Hand mit Produkten verknüpft hatte. Der produktive Weg ist ein anderer:
**Chat -> Chefkoch -> `chat_suggestion` -> Korb**, und dort stand bis zu
diesem Ticket nur eine vom Modell GERATENE Packungszahl.

Der Kerntest steht unter „Zwei Rezepte, eine Packung" und läuft über den
CHAT, nicht über `recipes.in_den_korb`: zwei Chat-Züge zu zwei Rezepten, die
sich das Hackfleisch teilen, ergeben EINE Packung — 200 g plus 300 g sind
500 g, und die Packung hat 500 g. Vor WB-369 waren es zwei, weil zweimal „1
Packung" im Korb landete.

**Kein Test geht ins Netz und keiner weckt die Box.** Chefkoch ist ein
Doppelgänger mit zwei erfundenen Rezepten (die aufgezeichnete Fixture unter
`tests/fixtures/` bleibt für WB-338 zuständig, sie hat nur ein Rezept und
kein geteiltes Hackfleisch), das Modell ist ein `FakeLLM` mit fester Antwort.
"""
from __future__ import annotations

import json

import pytest

from zettel import db
from zettel.assistant import chat as chatmodul
from zettel.assistant import vorschlaege
from zettel.gerichte import lauf, quelle
from zettel.llm import wake
from zettel.llm.client import Antwort


# --------------------------------------------------------------------------
# Doppelgänger

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
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="fake")


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


def _rezept(rezept_id, titel, zutaten, servings=4):
    """Eine Chefkoch-Detailantwort, wie die API sie schreibt."""
    return {
        "id": rezept_id, "title": titel, "servings": servings,
        "siteUrl": f"https://www.chefkoch.de/rezepte/{rezept_id}/",
        "instructions": "Alles kochen.",
        "ingredientGroups": [
            {"header": None,
             "ingredients": [{"name": n, "amount": a, "unit": e}
                             for n, a, e in zutaten]}],
    }


#: Zwei Rezepte, die sich das Hackfleisch teilen — in Gramm, damit die
#: Rechnung beisst. (Bei „Zehe" gegen „100 g" tut sie es nicht; das ist die
#: bekannte Lücke aus WB-362 und steht weiter unten als eigener Test.)
BOLO = _rezept("111", "Bolognese",
               [("Hackfleisch, gemischtes", 200.0, "g"),
                ("Zwiebel(n)", 2.0, None),
                ("Salz", 1.0, "TL")])
CHILI = _rezept("222", "Chili con Carne",
                [("Hackfleisch, gemischtes", 300.0, "g"),
                 ("Knoblauchzehe(n)", 4.0, None)])

SUCHE = {"Bolognese": {"count": 1, "results": [
             {"recipe": {"id": "111", "title": "Bolognese",
                         "rating": {"rating": 4.5, "numVotes": 100},
                         "siteUrl": BOLO["siteUrl"]}}]},
         "Chili": {"count": 1, "results": [
             {"recipe": {"id": "222", "title": "Chili con Carne",
                         "rating": {"rating": 4.5, "numVotes": 100},
                         "siteUrl": CHILI["siteUrl"]}}]}}


class FakeChefkoch:
    """Die beiden Rezepte oben, über dieselbe URL-Form wie die echte API."""

    def get(self, url):
        if "/v2/recipes?" in url:
            for wort, treffer in SUCHE.items():
                if wort.lower() in url.lower():
                    return _Antwort(treffer)
            return _Antwort({"count": 0, "results": []})
        for rezept in (BOLO, CHILI):
            if url.endswith(f"/recipes/{rezept['id']}"):
                return _Antwort(rezept)
        raise AssertionError(f"Unerwartete URL: {url}")


def _holer(con, gericht, *, frist_s=None):
    return lauf.hole_jetzt(con, gericht, http=FakeChefkoch())


# --------------------------------------------------------------------------
# Katalog

ZUSATZ = [
    ("hack1", "Rinderhackfleisch", "500 g", "Fleisch", "Rind", "Hackfleisch"),
    ("zwie1", "Zwiebeln Gelb, Netz", "1 kg", "Obst & Gemüse", "Gemüse",
     "Zwiebeln"),
    ("knob1", "Knoblauch", "100 g", "Obst & Gemüse", "Gemüse", "Knoblauch"),
    ("klo1", "Toilettenpapier 10 Rollen", "10 Stk", "Haushalt", "Papier",
     "Toilettenpapier"),
]


def _zusatz(con):
    for external_id, name, gebinde, l1, l2, l3 in ZUSATZ:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 199, ?, ?, ?, ?)",
            (external_id, name, gebinde, l1, l2, l3))
    con.commit()


@pytest.fixture
def con(vorlagen, tmp_path):
    # Eine DATEI, weil der Abruf einen Pfad braucht (wie in test_gerichte.py).
    c = db.connect(vorlagen.datei(tmp_path / "zettel.db", "mengen_katalog",
                                  vorlagen.katalog, _zusatz))
    yield c
    c.close()


def _pid(con, teil):
    return con.execute("SELECT id FROM product WHERE name LIKE ?",
                       (f"%{teil}%",)).fetchone()["id"]


def _extract(*paare, gericht=None):
    return json.dumps(
        {"gericht": gericht,
         "begriffe": [{"suchbegriffe": list(b) if isinstance(b, tuple) else [b],
                       "menge": m} for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _zug(con, satz, gericht, *begriffe, wahl=()):
    """Ein Chat-Zug über die Quelle: Stufe 1 nennt das Gericht, 1b, dann 3."""
    llm = FakeLLM(_extract(*[(b, 1) for b in begriffe], gericht=gericht),
                  _extract(*[(b, 2) for b in begriffe]),
                  _choose(*wahl))
    agent = chatmodul.Chat(llm, wecker=Box(), quelle=quelle.Quelle(holer=_holer))
    return agent.turn(con, satz), llm


def _alles_ja(con, ergebnis):
    vorschlaege.alle_entscheiden(con, ergebnis.chat_message_id,
                                 vorschlaege.BEHALTEN)


def _korbzeile(con, product_id):
    return con.execute(
        "SELECT qty, need_amount, need_unit, hand_qty FROM order_item"
        " WHERE product_id = ?", (product_id,)).fetchone()


# --------------------------------------------------------------------------
# Der Kerntest

def test_zwei_rezepte_ueber_den_chat_teilen_sich_eine_packung(con):
    """**Der Kerntest des Tickets.** 200 g + 300 g gegen „500 g" = 1 Packung.

    Beide Züge laufen über den Chat und über „Ja" — nicht über
    `recipes.in_den_korb`. Vor WB-369 lag hier eine 2: jeder Zug legte „eine
    Packung" ein, und aus 1 + 1 wurde 2, obwohl zusammen 500 g gebraucht
    werden.
    """
    hack = _pid(con, "Rinderhackfleisch")
    erster, _ = _zug(con, "alles für Bolognese", "Bolognese", "Hackfleisch",
                     wahl=[("Hackfleisch", hack, 1)])
    _alles_ja(con, erster)
    nach_dem_ersten = _korbzeile(con, hack)
    assert (nach_dem_ersten["need_amount"], nach_dem_ersten["qty"]) == (200.0, 1)

    zweiter, _ = _zug(con, "alles für Chili", "Chili", "Hackfleisch",
                      wahl=[("Hackfleisch", hack, 1)])
    _alles_ja(con, zweiter)

    zeile = _korbzeile(con, hack)
    assert zeile["need_amount"] == 500.0
    assert zeile["need_unit"] == "g"
    assert zeile["qty"] == 1, "500 g passen in eine Packung à 500 g"


def test_ein_chat_zug_legt_die_benoetigte_menge_in_den_korb(con):
    """Punkt 1 bis 3 des Tickets an einem einzigen Zug."""
    hack = _pid(con, "Rinderhackfleisch")
    ergebnis, _ = _zug(con, "alles für Bolognese", "Bolognese", "Hackfleisch",
                       wahl=[("Hackfleisch", hack, 1)])

    zeile = [v for v in ergebnis.vorschlaege if v["product_id"] == hack][0]
    assert (zeile["need_amount"], zeile["need_unit"]) == (200.0, "g")
    _alles_ja(con, ergebnis)
    assert _korbzeile(con, hack)["need_amount"] == 200.0


def test_die_geratene_packungszahl_weicht_der_echten_menge(con):
    """Regel 4: wo eine Menge dasteht, wird die Zahl des Modells nicht benutzt.

    Das Modell antwortet hier mit `menge: 2` — es kennt die Packungsgrösse
    nicht und rät. Im Korb liegt trotzdem EINE Packung, weil 200 g in 500 g
    passen.
    """
    hack = _pid(con, "Rinderhackfleisch")
    ergebnis, _ = _zug(con, "alles für Bolognese", "Bolognese", "Hackfleisch",
                       wahl=[("Hackfleisch", hack, 2)])
    zeile = [v for v in ergebnis.vorschlaege if v["product_id"] == hack][0]
    assert zeile["qty"] == 1
    _alles_ja(con, ergebnis)
    assert _korbzeile(con, hack)["qty"] == 1


def test_ein_vorschlag_ohne_menge_behaelt_die_geratene_zahl(con):
    """„Klopapier" hat keine Menge — dort bleibt alles wie vor WB-369."""
    klo = _pid(con, "Toilettenpapier")
    ergebnis, _ = _zug(con, "alles für Bolognese und Klopapier", "Bolognese",
                       "Hackfleisch", "Toilettenpapier",
                       wahl=[("Hackfleisch", _pid(con, "Rinderhackfleisch"), 1),
                             ("Toilettenpapier", klo, 2)])
    zeile = [v for v in ergebnis.vorschlaege if v["product_id"] == klo][0]
    assert zeile["need_amount"] is None
    assert zeile["qty"] == 2, "ohne Menge bleibt die Zahl des Modells stehen"
    _alles_ja(con, ergebnis)
    assert _korbzeile(con, klo)["qty"] == 2


def test_nicht_passende_einheit_wird_gekennzeichnet_und_nicht_geraten(con):
    """4 Zehen gegen „100 g" — die bekannte Lücke aus WB-362.

    Sie wird hier NICHT gelöst, sondern benannt: die Menge steht an der
    Zeile, die Packungszahl ist eine und der Grund ist lesbar. Geraten wird
    nichts.
    """
    knob = _pid(con, "Knoblauch")
    ergebnis, _ = _zug(con, "alles für Chili", "Chili", "Knoblauch",
                       wahl=[("Knoblauch", knob, 3)])
    zeile = [v for v in ergebnis.vorschlaege if v["product_id"] == knob][0]

    assert (zeile["need_amount"], zeile["need_unit"]) == (4.0, "Stk")
    assert zeile["rechnung"].ausrechenbar is False
    assert "lässt sich nicht gegen die Packung" in zeile["rechnung"].grund
    assert zeile["packungen"] == 1

    _alles_ja(con, ergebnis)
    korb = _korbzeile(con, knob)
    assert korb["qty"] == 1, "eine Packung, keine geratene drei"
    assert korb["need_amount"] == 4.0, "die Menge bleibt trotzdem stehen"


def test_die_menge_ueberlebt_eine_korrektur(con):
    """„Nein, lieber das andere Hackfleisch" — 200 g braucht sie weiterhin.

    Die Menge gehört der ZUTAT und nicht dem Produkt. Ohne diese Zusicherung
    verlöre ausgerechnet die Korrektur sie, und im Korb läge eine Packung
    nach Bauchgefühl.
    """
    con.execute(
        "INSERT INTO product (source, external_id, name, price_cents,"
        " unit_text, category_l1, category_l2, category_l3)"
        " VALUES ('knuspr', 'hack2', 'Bio Rinderhackfleisch', 399, '250 g',"
        "         'Fleisch', 'Rind', 'Hackfleisch')")
    con.commit()
    hack, bio = _pid(con, "Rinderhackfleisch"), _pid(con, "Bio Rinder")
    ergebnis, _ = _zug(con, "alles für Bolognese", "Bolognese", "Hackfleisch",
                       wahl=[("Hackfleisch", hack, 1)])
    zeile = [v for v in ergebnis.vorschlaege if v["product_id"] == hack][0]

    korrektur = vorschlaege.korrigieren(con, zeile["id"], bio)
    assert korrektur["need_amount"] == 200.0
    # 200 g gegen 250 g: eine Packung. Die geratene Zahl hätte hier dasselbe
    # ergeben — der Punkt ist, dass die MENGE mitgewandert ist.
    assert _korbzeile(con, bio)["need_amount"] == 200.0


def test_der_span_zeigt_den_weg_von_der_zutat_zum_posten(con):
    """Spec 7: Zutatenmenge, Suchbegriff, Produkt, Packungszahl in einem Span.

    Sonst ist später nicht zu sehen, wo eine falsche Menge entstanden ist.
    """
    from zettel import obs
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter)

    hack = _pid(con, "Rinderhackfleisch")
    ergebnis, _ = _zug(con, "alles für Bolognese", "Bolognese", "Hackfleisch",
                       wahl=[("Hackfleisch", hack, 1)])

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs.setze_provider(provider)
    try:
        _alles_ja(con, ergebnis)
    finally:
        obs.abbauen()

    spans = [s for s in exporter.get_finished_spans() if s.name == "korb.menge"]
    assert len(spans) == 1
    a = spans[0].attributes
    assert a["zettel.need_added"] == 200.0
    assert a["zettel.need_amount"] == 200.0
    assert a["zettel.search_term"] == "Hackfleisch"
    assert a["zettel.product_id"] == hack
    assert a["zettel.packages"] == 1
    assert a["zettel.computable"] is True


# --------------------------------------------------------------------------
# Die Zuordnung selbst — ohne Chat, ohne Modell

def test_zwei_begriffe_auf_dasselbe_produkt_verlieren_keine_menge(con):
    """„Hackfleisch" und „Rinderhack" -> ein Produkt, aber beide Mengen.

    Zwei gleiche Vorschlagszeilen wären zweimal dieselbe Entscheidung, die
    zweite fällt deshalb weg (seit WB-327). Ihre Menge fällt seit WB-369
    nicht mit weg.
    """
    zeilen = [{"product_id": 7, "free_text": None, "qty": 1, "bedarf": 200.0,
               "einheit": "g", "search_term": "Hackfleisch", "rang": None},
              {"product_id": 7, "free_text": None, "qty": 1, "bedarf": 300.0,
               "einheit": "g", "search_term": "Rinderhack", "rang": None}]
    zusammen = chatmodul._zusammengefasst(zeilen)
    assert len(zusammen) == 1
    assert zusammen[0]["bedarf"] == 500.0


def test_portionen_bleiben_die_des_rezepts(con):
    """Regel 5, und zwar als das, was sie ist: NICHT gebaut.

    „für 6" aus dem Satz zu lesen hiesse raten, welche Zahl im Satz die
    Portionszahl ist. Chefkochs `servings` bleibt deshalb unverändert — eine
    falsche Portionszahl wäre schlimmer als keine (siehe README und
    OBSERVABILITY.md).
    """
    hack = _pid(con, "Rinderhackfleisch")
    ergebnis, _ = _zug(con, "alles für Bolognese für 6", "Bolognese",
                       "Hackfleisch", wahl=[("Hackfleisch", hack, 1)])
    zeile = [v for v in ergebnis.vorschlaege if v["product_id"] == hack][0]
    assert zeile["need_amount"] == 200.0, "die Menge des Rezepts, ungeskaliert"
