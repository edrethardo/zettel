"""Elf von zwölf Rezepten werden nicht mehr weggeworfen (WB-387).

Chefkoch liefert je Suche zwölf Rezepte in EINER Antwort. Bis zu diesem
Ticket nahm `chefkoch.bestes()` genau eines, und die übrigen elf verschwanden,
bevor sie jemand sehen konnte — obwohl sie nichts kosteten. Gemessen an
„Lasagne" (2026-08-29) sind das keine Varianten desselben Gerichts: klassisch
mit Hack, vegetarisch mit Spinat, Zucchini, Filoteig mit Ziegenkäse. Welches
gemeint war, weiss nur der Mensch.

**Kein Test geht ins Netz.** Chefkoch ist die aufgezeichnete Pho-Suche aus
`tests/fixtures/` samt dem Detail des Rezepts, das dabei gewinnt; das Detail
einer ALTERNATIVE gibt es dort nicht — es wird hier aus derselben Aufzeichnung
gebaut und ist als Doppelgänger kenntlich (`_alternativ_detail`). Was daran
zählt, ist nicht sein Inhalt, sondern dass genau eine Anfrage dafür nötig ist.

Die Testfragen sind die des Tickets, in seiner Reihenfolge:

    die Alternativen stehen zur Wahl, mit Zahlen
    die Wahl führt in den normalen Ablauf
    ohne Wahl passiert, was heute passiert
    die Wahl löst KEINEN zweiten Suchabruf aus
    was im Korb liegt, bleibt liegen — und die Oberfläche sagt es
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picknick import db
from picknick.assistant import chat as chatmodul
from picknick.gerichte import chefkoch, lauf, quelle, speicher
from picknick.llm import wake
from picknick.llm.client import Antwort
from picknick.web import app as webapp

FIXTURES = Path(__file__).parent / "fixtures"
SUCHE = json.loads((FIXTURES / "chefkoch_pho_suche.json")
                   .read_text(encoding="utf-8"))
REZEPT = json.loads((FIXTURES / "chefkoch_pho_rezept.json")
                    .read_text(encoding="utf-8"))

#: Das Rezept, das die Gewichtung aus der aufgezeichneten Suche wählt.
PHO_BO = "3595991540759513"
#: Die erste Alternative darunter — „Pho Ga", 4,85 aus 33 Stimmen.
PHO_GA = "3228981480357511"
#: Ein Chefkoch-Plus-Treffer aus derselben Antwort. Er trägt `numVotes: 255`,
#: also einen Platzhalter statt eines Abstimmungsergebnisses.
PLUS = "4175071668773984"

HTMX = {"HX-Request": "true"}


# --------------------------------------------------------------------------
# Doppelgänger

class FakeHTTP:
    """Chefkoch, aufgezeichnet. Merkt sich JEDE URL — daran hängt die Zusage
    „die Wahl löst keinen zweiten Suchabruf aus"."""

    def __init__(self, seiten):
        self.seiten = dict(seiten)
        self.geholt: list[str] = []

    def get(self, url):
        self.geholt.append(url)
        for teil, payload in self.seiten.items():
            if teil in url:
                return _Antwort(payload)
        raise AssertionError(f"Unerwartete URL: {url}")

    @property
    def suchen(self) -> list[str]:
        return [u for u in self.geholt if "/v2/recipes?" in u]


class _Antwort:
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
        self.aufrufe.append(list(nachrichten))
        if not self.antworten:
            raise AssertionError("Mehr Modellaufrufe als Antworten.")
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="fake")


class Waehler:
    """Die Wahl einer Alternative, gegen den Doppelgänger statt gegen das Netz.

    Dasselbe Muster wie `FakeHoler` in `test_gerichte.py` und aus demselben
    Grund: er zählt seine Aufrufe, und daran hängt die Zusage, dass ein
    Zurückwechseln gar keine Anfrage mehr kostet.
    """

    def __init__(self, http):
        self.http = http
        self.gewaehlt: list[str] = []

    def __call__(self, con, gericht, treffer, *, frist_s=None):
        self.gewaehlt.append(treffer["rezept_id"])
        return lauf.waehle_jetzt(con, gericht, treffer, http=self.http)


def _alternativ_detail(rezept_id: str, titel: str) -> dict:
    """Das Detail einer Alternative — nachgebaut, nicht aufgezeichnet.

    Die Fixture enthält nur das Detail des Rezepts, das die Suche gewinnt;
    ein zweites aufzunehmen hiesse, chefkoch.de für einen Test anzufassen.
    Nachgebaut wird deshalb aus derselben Antwort, mit einer kurzen
    Zutatenliste und anderen Zeiten — was hier geprüft wird, ist der WEG und
    nicht der Inhalt des Rezepts.
    """
    detail = copy.deepcopy(REZEPT)
    detail["id"] = rezept_id
    detail["title"] = titel
    detail["preparationTime"] = 25
    detail["cookingTime"] = 35
    detail["restingTime"] = 0
    detail["servings"] = 4
    detail["ingredientGroups"] = [{
        "header": "",
        "ingredients": [
            {"name": "Ingwer", "amount": 1.0, "unit": "Stück",
             "usageInfo": ""},
            {"name": "Mie Nudeln", "amount": 250.0, "unit": "g",
             "usageInfo": ""},
        ],
    }]
    return detail


ZUSATZ = (
    ("rifi", "Rinderfilet 400 g", "Fleisch", "Rind", "Filet"),
    ("ingw", "Ingwer frisch", "Obst & Gemüse", "Gemüse", "Ingwer"),
    ("mien", "Mie Nudeln 250 g", "Nudeln", "Asia", "Mie"),
)


def _zusatz(con):
    for external_id, name, l1, l2, l3 in ZUSATZ:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 199, '1 Stk', ?, ?, ?)",
            (external_id, name, l1, l2, l3))
    con.commit()


@pytest.fixture
def datei(vorlagen, tmp_path):
    return vorlagen.datei(tmp_path / "picknick.db", "alternativen_katalog",
                          vorlagen.katalog, _zusatz)


def _pid(pfad, teil):
    con = db.connect(pfad)
    try:
        return con.execute("SELECT id FROM product WHERE name LIKE ?",
                           (f"%{teil}%",)).fetchone()["id"]
    finally:
        con.close()


def _extract(*paare, gericht=None):
    return json.dumps(
        {"gericht": gericht,
         "begriffe": [{"suchbegriffe": list(b), "menge": m}
                      for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _pho_geholt(pfad) -> int:
    """Holt Pho aus der Fixture — Suche UND Detail, wie im echten Abruf."""
    con = db.connect(pfad)
    try:
        lauf.hole_eines(con, FakeHTTP({"/v2/recipes?": SUCHE,
                                       f"/v2/recipes/{PHO_BO}": REZEPT}),
                        "Pho", pause_s=0, schreib=lambda _: None)
        return con.execute("SELECT id FROM dish").fetchone()["id"]
    finally:
        con.close()


def _antworten(pfad, n: int = 2):
    """Modellantworten für `n` Chefkoch-Züge: je Stufe 1 und Stufe 3."""
    paar = [_extract((("Ingwer",), 1), (("Mie Nudeln", "Nudeln"), 1)),
            _choose(("Ingwer", _pid(pfad, "Ingwer"), 1),
                    ("Mie Nudeln", _pid(pfad, "Mie Nudeln"), 1))]
    return paar * n


def _shop(pfad, tmp_path, *, waehler=None, antworten=None):
    """Der Shop mit einem Chefkoch, der nur das Detail der Alternative kennt.

    `holer=nicht_holen`: das Gericht steht schon im Speicher, ein Zug darf es
    nicht noch einmal abrufen.
    """
    http = FakeHTTP({f"/v2/recipes/{PHO_GA}":
                     _alternativ_detail(PHO_GA, "Pho Ga")})
    waehler = waehler if waehler is not None else Waehler(http)
    llm = FakeLLM(*(antworten if antworten is not None
                    else _antworten(pfad)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen,
                                                waehler=waehler))
    app = webapp.create_app(db_path=pfad, image_dir=tmp_path / "bilder",
                            chat=agent)
    client = TestClient(app)
    # Das Fake-Modell hängt am Client, damit ein Test ZÄHLEN kann, wie oft es
    # gefragt wurde — daran hängt die Zusage aus WB-402, dass die Rezeptkarte
    # ohne Modell auskommt.
    client.llm = llm
    return client, http, waehler


def _zug_id(pfad) -> int:
    con = db.connect(pfad)
    try:
        return con.execute("SELECT id FROM chat_message WHERE role = 'assistant'"
                           " ORDER BY id DESC LIMIT 1").fetchone()["id"]
    finally:
        con.close()


def _wechsel(client, mid: int, rezept: str):
    """Beide Hälften des Wechsels (WB-402) — Karte, dann Vorschläge.

    Der Tipp beantwortet seit WB-402 nur noch die Karte; den Zug holt das
    Bruchstück per `hx-trigger="load"` nach. Wer den ganzen Wechsel meint,
    meint beide Requests — und genau die macht dieser Helfer, damit ein Test
    nicht versehentlich die halbe Strecke misst.
    """
    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": rezept},
                        headers=HTMX)
    zweite = client.post(f"/chat/{mid}/rezept/vorschlaege",
                         data={"rezept": rezept}, headers=HTMX)
    return erste, zweite


def _eine_zeile(text: str) -> str:
    """Der Text ohne Umbrüche und doppelte Leerzeichen.

    Die Vorlagen brechen Sätze um; geprüft wird der SATZ und nicht seine
    Einrückung — sonst zerbricht ein Test an einer Zeilenlänge.
    """
    return " ".join(text.split())


def _karte(text: str) -> str:
    assert '<section class="zugrezept">' in text, "keine Rezeptkarte"
    return text.split('<section class="zugrezept">', 1)[1].split(
        "</section>", 1)[0]


def _rezept_des_gerichts(pfad) -> str:
    con = db.connect(pfad)
    try:
        return con.execute(
            "SELECT r.source_id FROM dish d JOIN recipe r ON r.id = d.recipe_id"
        ).fetchone()["source_id"]
    finally:
        con.close()


# --------------------------------------------------------------------------
# Die Treffer werden abgelegt, statt weggeworfen zu werden

def test_die_suche_legt_alle_zwoelf_treffer_ab(datei):
    """Zwölf kamen, zwölf stehen da — auch die, die nie gewählt werden."""
    dish_id = _pho_geholt(datei)
    con = db.connect(datei)
    try:
        alle = con.execute("SELECT source_id, plus FROM dish_treffer"
                           " WHERE dish_id = ?", (dish_id,)).fetchall()
    finally:
        con.close()
    assert len(alle) == 12
    assert sum(1 for a in alle if a["plus"]) == 2


def test_zur_wahl_stehen_sechs_ohne_plus_und_bestgewichtet_zuerst(datei):
    """Die Rangfolge ist dieselbe wie die der Vorauswahl, nur nicht auf einen
    Eintrag zusammengestrichen — und Plus-Rezepte stehen nicht darin.

    Ihre Stimmenzahl ist keine: beide Plus-Treffer der Fixture tragen exakt
    `numVotes: 255`. Was nicht gewählt werden darf, soll auch nicht zur Wahl
    stehen.
    """
    dish_id = _pho_geholt(datei)
    con = db.connect(datei)
    try:
        wahl = speicher.treffer(con, dish_id)
    finally:
        con.close()

    assert len(wahl) == chefkoch.ANGEBOT == 6
    assert wahl[0]["rezept_id"] == PHO_BO, "die Vorauswahl steht oben"
    assert [w["gewicht"] for w in wahl] == sorted(
        (w["gewicht"] for w in wahl), reverse=True)
    assert PLUS not in {w["rezept_id"] for w in wahl}


def test_der_gespeicherte_treffer_sieht_aus_wie_ein_frischer(datei):
    """Dieselben Feldnamen wie `chefkoch.parse_treffer` — sonst gäbe es zwei
    Gestalten desselben Dings, und `hole_detail` müsste beide kennen."""
    dish_id = _pho_geholt(datei)
    con = db.connect(datei)
    try:
        gespeichert = speicher.treffer(con, dish_id)[0]
    finally:
        con.close()
    frisch = chefkoch.bestes(chefkoch.parse_treffer(SUCHE))
    for feld in ("rezept_id", "titel", "rating", "votes", "prep_minutes",
                 "difficulty", "site_url"):
        assert gespeichert[feld] == frisch[feld], feld


# --------------------------------------------------------------------------
# Die Karte bietet sie an — mit den Zahlen, die die Wahl tragen

def test_ein_chefkoch_zug_bietet_alternativen_mit_zahlen_an(datei, tmp_path):
    """Zeit, Bewertung mit Stimmenzahl, Zutatenzahl — dieselben Angaben wie in
    der Rezeptkarte aus WB-383, nur eine Ebene früher.

    Und die Zeit wird beim Namen genannt: für ein nicht geholtes Rezept
    kennt die Suchantwort nur die ARBEITSZEIT. Pho Bo steht dort mit 90
    Minuten und braucht 9½ Stunden — beides „Zeit" zu nennen wäre die
    bequemere und falsche Auskunft.
    """
    _pho_geholt(datei)
    client, http, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    karte = _karte(client.get("/chat").text)

    assert 'class="andere"' in karte, "keine Alternativen an der Karte"
    assert "5 andere Rezepte zu „Pho“" in _eine_zeile(karte)
    # Das vorgeschlagene steht MIT in der Liste — sonst wäre nicht zu sehen,
    # wogegen man wählt — und trägt seine vollen Zahlen.
    assert "vorgeschlagen" in karte
    assert "9½ Stunden" in karte and "23 Zutaten" in karte
    # Eine Alternative: Bewertung mit Stimmenzahl und die Arbeitszeit, so
    # benannt. Eine Zutatenzahl steht dort NICHT — die Suchantwort trägt
    # keine, und elf Details zu holen wären elf Anfragen.
    assert "Pho Ga" in karte
    assert "4.85 aus 33 Stimmen" in karte
    assert "45 Minuten Arbeitszeit" in karte
    # Und die Karte kostet keinen einzigen Abruf: geholt wurde beim Zug
    # nichts, die Treffer lagen schon.
    assert http.geholt == []


def test_der_rezeptweg_bietet_nichts_an(datei, tmp_path):
    """Ohne Gericht keine Trefferliste — und keine erfundene.

    Der Rezeptweg schlägt ein gespeichertes Rezept vor; dazu hat nie jemand
    gesucht, also gibt es auch keine Alternativen. Eine Liste zu zeigen
    hiesse, eine Herkunft zu behaupten.
    """
    from picknick import recipes

    con = db.connect(datei)
    try:
        rid = recipes.anlegen(con, "Nudelauflauf")
        recipes.zutat_hinzufuegen(con, rid,
                                  product_id=_pid(datei, "Mie Nudeln"))
    finally:
        con.close()
    client, _, _ = _shop(datei, tmp_path, antworten=[])
    client.post("/chat", data={"satz": "alles für Nudelauflauf"},
                headers=HTMX)
    seite = client.get("/chat").text
    assert "Nudelauflauf" in seite
    assert 'class="andere"' not in seite


# --------------------------------------------------------------------------
# Die Wahl führt in den normalen Ablauf

def test_die_wahl_fuehrt_in_den_normalen_ablauf(datei, tmp_path):
    """Ein Tipp auf eine Alternative erzeugt denselben Zug wie ein Satz:
    Zutaten, Suche, Vorschläge, Ja/Nein — kein zweiter Mechanismus daneben.
    """
    _pho_geholt(datei)
    client, _, waehler = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho, und Klopapier"},
                headers=HTMX)
    mid = _zug_id(datei)

    erste, zweite = _wechsel(client, mid, PHO_GA)
    assert erste.status_code == 200 and zweite.status_code == 200
    assert waehler.gewaehlt == [PHO_GA]

    # Das Gericht zeigt jetzt auf das gewählte Rezept.
    assert _rezept_des_gerichts(datei) == PHO_GA
    # Und der neue Zug ist ein ganz gewöhnlicher Chefkoch-Zug: eine neue
    # Antwortzeile mit Vorschlägen und einer Rezeptkarte.
    neu = _zug_id(datei)
    assert neu > mid
    con = db.connect(datei)
    try:
        n = con.execute("SELECT count(*) AS n FROM chat_suggestion"
                        " WHERE chat_message_id = ?", (neu,)).fetchone()["n"]
        weg = con.execute("SELECT content FROM chat_message WHERE id = ?",
                          (neu,)).fetchone()["content"]
    finally:
        con.close()
    assert n >= 2, "der neue Zug hat keine Vorschläge angelegt"
    assert "von Chefkoch" in weg
    # Derselbe SATZ wie beim ersten Mal — das Klopapier daneben geht wieder
    # mit, sonst wäre die Wahl eine andere Frage als die erste.
    con = db.connect(datei)
    try:
        saetze = [r["content"] for r in con.execute(
            "SELECT content FROM chat_message WHERE role = 'user'"
            " ORDER BY id")]
    finally:
        con.close()
    assert saetze == ["alles für Pho, und Klopapier"] * 2


def test_die_wahl_loest_keinen_zweiten_suchabruf_aus(datei, tmp_path):
    """Die zwölf Treffer liegen schon. Geholt wird EIN Detail, sonst nichts.

    Das ist die Nebenbedingung des Tickets: eine Liste anzubieten darf keinen
    zusätzlichen Abruf je Alternative kosten, und die Wahl selbst keine
    zweite Suche.
    """
    _pho_geholt(datei)
    client, http, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    _wechsel(client, _zug_id(datei), PHO_GA)

    assert http.suchen == [], "die Wahl hat noch einmal gesucht"
    # EIN Detail über BEIDE Hälften (WB-402): der zweite Schritt ruft
    # `waehlen` noch einmal auf, findet das Rezept aber schon in der
    # Sammlung und fasst das Netz nicht an.
    assert len(http.geholt) == 1
    assert http.geholt[0].endswith(f"/recipes/{PHO_GA}")


def test_zurueckwechseln_kostet_gar_keine_anfrage(datei, tmp_path):
    """Wer hin und her wechselt, holt das schon Geholte nicht noch einmal."""
    _pho_geholt(datei)
    client, http, waehler = _shop(datei, tmp_path,
                                  antworten=_antworten(datei, 3))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    _wechsel(client, _zug_id(datei), PHO_GA)
    _wechsel(client, _zug_id(datei), PHO_BO)

    assert _rezept_des_gerichts(datei) == PHO_BO
    # Nur die eine Anfrage für Pho Ga; der Rückweg fasst das Netz nicht an.
    assert len(http.geholt) == 1
    assert waehler.gewaehlt == [PHO_GA]


def test_eine_nicht_angebotene_id_wird_abgewiesen(datei, tmp_path):
    """Gewählt werden kann, was vorlag — dieselbe Regel wie bei den
    Produkt-IDs in `plan.choose` und den Sorten in WB-368.

    Ein Plus-Rezept steht in der Antwort der Quelle, aber nicht im Angebot:
    Es kommt hier auch dann nicht durch, wenn jemand seine ID von Hand
    einträgt.
    """
    _pho_geholt(datei)
    client, http, waehler = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": PLUS},
                          headers=HTMX)
    assert antwort.status_code == 200
    assert "nicht (mehr) zur Wahl" in antwort.text
    assert http.geholt == [] and waehler.gewaehlt == []
    assert _rezept_des_gerichts(datei) == PHO_BO
    assert _zug_id(datei) == mid, "es ist ein Zug entstanden"


def test_das_schon_gewaehlte_loest_keinen_zug_aus(datei, tmp_path):
    """Ein Tipp auf das vorgeschlagene Rezept kostet keine 20 Sekunden Modell
    für ein Ergebnis, das schon dasteht."""
    _pho_geholt(datei)
    client, http, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_BO},
                          headers=HTMX)
    assert "bereits das vorgeschlagene Rezept" in antwort.text
    assert _zug_id(datei) == mid
    assert http.geholt == []


# --------------------------------------------------------------------------
# Ohne Wahl passiert, was heute passiert

def test_ohne_wahl_bleibt_alles_wie_es_war(datei, tmp_path):
    """Wählen ist ein Angebot, keine Pflicht: der Zug wartet auf nichts.

    Er nimmt das bestgewichtete Rezept und legt seine Vorschläge an — genau
    wie vor diesem Ticket.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)

    assert _rezept_des_gerichts(datei) == PHO_BO
    seite = client.get("/chat").text
    assert "Pho Bo" in seite
    con = db.connect(datei)
    try:
        n = con.execute("SELECT count(*) AS n FROM chat_suggestion"
                        ).fetchone()["n"]
    finally:
        con.close()
    assert n >= 2


# --------------------------------------------------------------------------
# Was im Korb liegt, bleibt liegen

def test_was_im_korb_liegt_bleibt_beim_wechsel_liegen(datei, tmp_path):
    """Der Korb gehört dem Korb — dieselbe Haltung wie in WB-361 und WB-384.

    Und die Oberfläche sagt es zweimal: vor dem Tipp an der Liste, danach am
    Band. Wer nachrechnen müsste, ob ihm eben etwas herausgeflogen ist,
    wechselt beim nächsten Mal nicht mehr.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    con = db.connect(datei)
    try:
        sid = con.execute("SELECT id FROM chat_suggestion"
                          " WHERE chat_message_id = ? ORDER BY id",
                          (mid,)).fetchone()["id"]
    finally:
        con.close()
    client.post(f"/chat/vorschlag/{sid}/entscheiden?decision=kept",
                headers=HTMX)

    def posten():
        c = db.connect(datei)
        try:
            return [tuple(r) for r in c.execute(
                "SELECT product_id, qty FROM order_item ORDER BY id")]
        finally:
            c.close()

    vorher = posten()
    assert vorher, "nichts im Korb — der Test prüft dann nichts"

    # Der Hinweis steht VOR dem Tipp an der Liste.
    assert ("was schon im Korb liegt, bleibt liegen"
            in _eine_zeile(client.get("/chat").text))

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                          headers=HTMX)
    assert posten() == vorher, "der Wechsel hat den Korb angefasst"
    # Und danach steht es noch einmal da, an dem Tipp, der es ausgelöst hat.
    assert "Das Rezept ist jetzt „Pho Ga“" in antwort.text
    assert "liegt weiter im" in antwort.text


# --------------------------------------------------------------------------
# Der Trace

def test_der_wechsel_steht_am_zug(datei, tmp_path):
    """`picknick.dish_switch` — die Zahl, um die es im Ticket geht.

    Ein Zug mit gesetztem Feld ist einer, in dem „am besten bewertet" nicht
    „was ich gemeint habe" war. Ohne ihn wäre die Schlagseite der Gewichtung
    später nicht mehr messbar.
    """
    _pho_geholt(datei)
    con = db.connect(datei)
    try:
        llm = FakeLLM(*_antworten(datei))
        agent = chatmodul.Chat(llm, wecker=Box(),
                               quelle=quelle.Quelle(holer=quelle.nicht_holen))
        ergebnis = agent.turn(con, "alles für Pho", gewechselt="Pho Ga")
    finally:
        con.close()
    assert ergebnis.gewechselt == "Pho Ga"
    assert ergebnis.weg == "chefkoch"


# --------------------------------------------------------------------------
# Der Altbestand

def test_eine_alte_datenbank_bekommt_tabelle_und_spalte(leere_db_datei):
    """`dish_treffer` und `chat_rezept.dish_id` kommen per Migration nach.

    Für die Züge, die schon dastehen, gibt es nichts nachzutragen: zu ihnen
    wurde keine Trefferliste mitgeschrieben, und eine zu erfinden hiesse, eine
    Herkunft zu behaupten. Sie bleiben ohne Auswahl — bis ihr Gericht das
    nächste Mal geholt wird.
    """
    con = db.connect(leere_db_datei)
    try:
        con.execute("DROP TABLE dish_treffer")
        con.execute("ALTER TABLE chat_rezept DROP COLUMN dish_id")
        con.commit()
    finally:
        con.close()

    con = db.connect(leere_db_datei)
    try:
        db.migrate(con)
        db.migrate(con)          # idempotent
        spalten = [r[1] for r in con.execute("PRAGMA table_info(chat_rezept)")]
        assert "dish_id" in spalten
        assert con.execute("SELECT count(*) AS n FROM dish_treffer"
                           ).fetchone()["n"] == 0
    finally:
        con.close()


def test_der_altbestand_laesst_sich_von_hand_nachholen(datei):
    """`ohne_treffer()` findet die Gerichte, die vor WB-387 geholt wurden.

    Nachtragen kann sie nur ein neuer Abruf — aus einem gespeicherten Rezept
    lassen sich die elf anderen nicht zurückgewinnen. Deshalb steht dieser
    Weg als Kommando da (`lauf --ohne-treffer`) und nicht in der Migration:
    eine Migration geht nicht ins Netz.
    """
    dish_id = _pho_geholt(datei)
    con = db.connect(datei)
    try:
        assert speicher.ohne_treffer(con) == []
        # So sieht ein Gericht aus, das vor diesem Ticket geholt wurde.
        con.execute("DELETE FROM dish_treffer WHERE dish_id = ?", (dish_id,))
        con.commit()
        offen = speicher.ohne_treffer(con)
    finally:
        con.close()
    assert [o["query"] for o in offen] == ["Pho"]
