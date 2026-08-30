"""Das vorgeschlagene Rezept steht im Chat — nicht nur ein Satz darüber
(WB-383).

**Kein Test geht ins Netz.** Chefkoch ist der aufgezeichnete Pho Bo aus
`tests/fixtures/`, das Modell ein Fake mit fester Antwort, die Box wird
untergeschoben. Dieselbe Bauart wie `test_gerichte.py`, nur eine Ebene höher:
dort wird der ZUG geprüft, hier die ANSICHT.

Der Zweck der Karte ist eine Entscheidung und kein Nachschlagen — „damit man
weiß worauf man sich einlassen würde". Deshalb prüfen die Tests hier nicht
bloss, DASS das Rezept dasteht, sondern in welcher REIHENFOLGE: Pho Bo sind
neuneinhalb Stunden und die Käse-Lauch-Suppe fünfunddreissig Minuten, und das
ist die Zahl, die entscheidet. Sie muss vor der Zutatenliste stehen und darf
nie eingeklappt sein.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import db, recipes
from zettel.assistant import chat as chatmodul
from zettel.assistant import zugrezept
from zettel.gerichte import lauf, quelle
from zettel.llm import wake
from zettel.llm.client import Antwort
from zettel.web import app as webapp

FIXTURES = Path(__file__).parent / "fixtures"
SUCHE = json.loads((FIXTURES / "chefkoch_pho_suche.json")
                   .read_text(encoding="utf-8"))
REZEPT = json.loads((FIXTURES / "chefkoch_pho_rezept.json")
                    .read_text(encoding="utf-8"))
PHO_BO = "3595991540759513"

HTMX = {"HX-Request": "true"}


# --------------------------------------------------------------------------
# Doppelgänger — dieselben wie in `test_gerichte.py`, hier nur so viel wie
# nötig.

class FakeHTTP:
    def __init__(self, seiten):
        self.seiten = dict(seiten)

    def get(self, url):
        for teil, payload in self.seiten.items():
            if teil in url:
                return _Antwort(payload)
        raise AssertionError(f"Unerwartete URL: {url}")


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
    """Katalog plus die drei Produkte, die der Pho-Zug findet."""
    return vorlagen.datei(tmp_path / "zettel.db", "zugrezept_katalog",
                          vorlagen.katalog, _zusatz)


def _leere_chatzeile(con) -> int:
    """Eine Antwortzeile ohne Vorschläge, samt der Bestellung, an der sie
    hängt. Für die Tests, die die Karte OHNE einen ganzen Chat-Zug brauchen."""
    con.execute("INSERT INTO orders (state, created_at)"
                " VALUES ('draft', '2026-01-01')")
    order_id = con.execute("SELECT id FROM orders ORDER BY id DESC"
                           " LIMIT 1").fetchone()["id"]
    con.execute("INSERT INTO chat_message (order_id, role, content,"
                " created_at) VALUES (?, 'agent', 'x', '2026-01-01')",
                (order_id,))
    return con.execute("SELECT id FROM chat_message ORDER BY id DESC"
                       " LIMIT 1").fetchone()["id"]


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


def _pho_geholt(pfad):
    """Holt Pho Bo aus der Fixture in die Datenbank. Gibt die recipe_id."""
    con = db.connect(pfad)
    try:
        lauf.hole_eines(con, FakeHTTP({"/v2/recipes?": SUCHE,
                                       f"/v2/recipes/{PHO_BO}": REZEPT}),
                        "Pho", pause_s=0, schreib=lambda _: None)
        return con.execute("SELECT id FROM recipe ORDER BY id DESC"
                           " LIMIT 1").fetchone()["id"]
    finally:
        con.close()


def _pho_zug(pfad, tmp_path):
    """Ein echter Chefkoch-Zug über die Oberfläche. Gibt (client, recipe_id)."""
    recipe_id = _pho_geholt(pfad)
    llm = FakeLLM(
        _extract((("Rinderfilet", "Rindfleisch"), 1), (("Ingwer",), 1),
                 (("Mie Nudeln", "Nudeln"), 1), (("Sternanis",), 1)),
        _choose(("Rinderfilet", _pid(pfad, "Rinderfilet"), 1),
                ("Ingwer", _pid(pfad, "Ingwer"), 1),
                ("Mie Nudeln", _pid(pfad, "Mie Nudeln"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    app = webapp.create_app(db_path=pfad, image_dir=tmp_path / "bilder",
                            chat=agent)
    client = TestClient(app)
    antwort = client.post("/chat", data={"satz": "alles für Pho"},
                          headers=HTMX)
    assert antwort.status_code == 200
    return client, recipe_id


def _karte(text: str) -> str:
    """Die Rezeptkarte aus einer Seite herausgeschnitten."""
    assert '<section class="zugrezept">' in text, "keine Rezeptkarte"
    return text.split('<section class="zugrezept">', 1)[1].split(
        "</section>", 1)[0]


# --------------------------------------------------------------------------
# Der Kern: das Rezept steht da

def test_ein_chefkoch_zug_zeigt_das_rezept(datei, tmp_path):
    """Zutaten mit Mengen, Bewertung, Portionen und die Herkunft.

    Bis WB-383 stand hier ein Satz ÜBER das Rezept — „12 Zutaten im Rezept,
    10 davon auf dem Zettel" — und das Rezept selbst nirgends.
    """
    client, recipe_id = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)

    assert "Pho Bo" in karte
    # Die Zutatenliste, wie die Quelle sie schreibt — mit Menge und Einheit.
    assert "Markknochen" in karte and "Sternanis" in karte
    mengen = [" ".join(m.split()) for m in
              re.findall(r'<span class="menge">(.*?)</span>', karte)]
    assert "3 Liter" in mengen and "1 kg" in mengen and "500 g" in mengen
    # Und die Gruppen, die die Quelle setzt („Für die Brühe").
    assert "Für die Brühe" in karte
    # Bewertung mit Stimmenzahl, Portionen, Schwierigkeit.
    assert "4.84 aus 62 Stimmen" in karte
    assert "6 Portionen" in karte
    assert "Schwierigkeit 2 von 3" in karte
    # Die Herkunft genannt UND verlinkt.
    assert "chefkoch.de/rezepte/" in karte
    assert f'href="/rezepte/{recipe_id}"' in karte


def test_die_gesamtzeit_steht_ganz_oben_und_als_eine_zahl(datei, tmp_path):
    """Die Zahl, die entscheidet — vor der Zutatenliste, nicht als drei Felder.

    Pho Bo sind 90 + 480 Minuten. „9½ Stunden" ist die Auskunft; „prep 90,
    cook 480" ist eine Datenbankzeile.
    """
    client, _ = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)

    assert "9½ Stunden" in karte
    assert "570" not in karte and "480 Min" not in karte
    # Und zwar VOR der Zutatenliste (Nachtrag zu WB-383).
    assert karte.index("9½ Stunden") < karte.index("Markknochen")


def test_die_katalogdeckung_steht_vor_der_zutatenliste(datei, tmp_path):
    """„23 Zutaten, 8 davon nicht auf dem Zettel" — vor dem „Ja", nicht danach.

    Diese Zahl sagt, wie viel Nachlaufen der Einkauf wird. Sie steht im selben
    Moment schon fest wie die Bewertung, die der Chat bisher als Einzige
    nannte.
    """
    client, _ = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)

    treffer = re.search(r'<p class="deckung">(.*?)</p>', karte, re.S)
    assert treffer, "keine Deckungszeile"
    zeile = " ".join(treffer.group(1).split())
    assert "23 Zutaten" in zeile
    # Vier Begriffe gingen auf den Zettel, 23 stehen im Rezept.
    assert "19 davon nicht auf dem Zettel" in zeile
    assert karte.index("deckung") < karte.index("Markknochen")


def test_die_zubereitung_ist_da_aber_nicht_aufgeklappt(datei, tmp_path):
    """Eingeklappt UND nicht mitgeschickt — sonst hätte das Einklappen nichts
    gebracht.

    Pho Bos Zubereitung sind 3.924 Zeichen. Ein `<details>` allein versteckt
    nur, was trotzdem übertragen wurde; hier wird sie beim Aufklappen geholt.
    """
    client, recipe_id = _pho_zug(datei, tmp_path)
    seite = client.get("/chat").text
    karte = _karte(seite)

    klapper = re.search(r"<details[^>]*>", karte)
    assert klapper, "kein Aufklapper"
    assert "open" not in klapper.group(0), "die Zubereitung steht offen da"
    assert "Zubereitung" in karte
    assert f'hx-get="/rezepte/{recipe_id}/zubereitung"' in karte
    volltext = client.get(f"/rezepte/{recipe_id}/zubereitung").text
    schritte = re.findall(r"<li>", volltext)
    assert len(schritte) >= 3
    # Was dort steht, stand vorher nicht auf der Chatseite — das ist der
    # ganze Zweck des Nachladens.
    erster = re.search(r"<li>(.{40,80})", volltext).group(1)
    assert erster not in seite


def test_die_zeit_ist_nie_eingeklappt(datei, tmp_path):
    """Die Zeit ist der Grund, warum jemand „nein" sagt — sie darf nicht in
    einem `<details>` stecken."""
    client, _ = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)

    vor_dem_klapper = karte.split("<details", 1)[0]
    assert "9½ Stunden" in vor_dem_klapper
    assert "23 Zutaten" in vor_dem_klapper


def test_der_bezug_zum_entwurf_ist_erkennbar(datei, tmp_path):
    """Nicht zwei Dinge nebeneinander, die zufällig denselben Namen tragen
    (WB-337): was unten bestätigt wird, landet in genau diesem Rezept."""
    client, _ = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)
    assert "in genau dieses Rezept" in karte


def test_der_zug_traegt_die_karte_auch_beim_teiltausch(datei, tmp_path):
    """Ein Sammelknopf tauscht den ganzen ZUG (WB-372) — die Karte muss mit.

    Sie hängt an denselben Vorschlagszeilen wie die Deckungszahl: eine
    Korrektur legt eine neue Zeile an, und eine Karte, die dabei verschwindet
    oder stehen bleibt, zeigte danach etwas anderes als der Verlauf.
    """
    client, _ = _pho_zug(datei, tmp_path)
    con = db.connect(datei)
    try:
        mid = con.execute("SELECT id FROM chat_message WHERE role = 'assistant'"
                          " ORDER BY id DESC LIMIT 1").fetchone()["id"]
    finally:
        con.close()

    antwort = client.post(f"/chat/{mid}/alle?decision=kept", headers=HTMX).text
    karte = _karte(antwort)
    assert "9½ Stunden" in karte and "Markknochen" in karte


def test_die_zubereitung_eines_unbekannten_rezepts_ist_eine_404(datei,
                                                                tmp_path):
    """Eine Adresse, die ins Leere zeigt, bekommt die ehrliche Antwort."""
    app = webapp.create_app(db_path=datei, image_dir=tmp_path / "bilder",
                            chat=chatmodul.Chat(FakeLLM(), wecker=Box()))
    assert TestClient(app).get("/rezepte/9999/zubereitung").status_code == 404


# --------------------------------------------------------------------------
# Was NICHT passieren darf

def test_ein_modellzug_zeigt_kein_rezept(datei, tmp_path):
    """Der Modellweg hat keins — dort darf auch nichts danach aussehen."""
    llm = FakeLLM(_extract((("Milch",), 1)),
                  _choose(("Milch", _pid(datei, "Milch"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    app = webapp.create_app(db_path=datei, image_dir=tmp_path / "bilder",
                            chat=agent)
    client = TestClient(app)
    client.post("/chat", data={"satz": "Milch"}, headers=HTMX)

    seite = client.get("/chat").text
    assert '<section class="zugrezept">' not in seite
    assert "Stunden" not in seite and "Schwierigkeit" not in seite


def test_ein_rezeptweg_zug_zeigt_dasselbe_wie_ein_chefkoch_zug(datei,
                                                              tmp_path):
    """Sonst sähe der schnelle Weg ärmer aus als der langsame.

    Der Rezeptweg hat keinen Rezeptentwurf — das Rezept gibt es ja schon —,
    und genau deshalb reicht `chat_entwurf.recipe_id` als Verknüpfung nicht.
    """
    recipe_id = _pho_geholt(datei)
    con = db.connect(datei)
    try:
        # Damit `rezeptweg.erkenne` greift, braucht das Rezept verknüpfte
        # PRODUKTE und einen Namen, der im Satz steht.
        recipes.aendern(con, recipe_id, name="Pho")
        recipes.zutat_hinzufuegen(con, recipe_id,
                                  product_id=_pid(datei, "Mie Nudeln"), qty=1)
    finally:
        con.close()

    agent = chatmodul.Chat(FakeLLM(), wecker=Box(),
                           quelle=quelle.Quelle(holer=quelle.nicht_holen))
    app = webapp.create_app(db_path=datei, image_dir=tmp_path / "bilder",
                            chat=agent)
    client = TestClient(app)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)

    karte = _karte(client.get("/chat").text)
    assert "9½ Stunden" in karte
    assert "4.84 aus 62 Stimmen" in karte
    assert "Markknochen" in karte
    assert "chefkoch.de/rezepte/" in karte


def test_fehlende_felder_bleiben_leer_statt_geraten(datei):
    """Ohne Zeit steht keine Zeit da — kein Platzhalter, keine Schätzung."""
    con = db.connect(datei)
    try:
        recipe_id = recipes.anlegen(con, "Nudeln mit Butter", servings=2)
        con.execute(
            "INSERT INTO recipe_ingredient (recipe_id, pos, raw_name, name,"
            " amount, unit) VALUES (?, 0, '250 g Nudeln', 'Nudeln', 250, 'g')",
            (recipe_id,))
        _leere_chatzeile(con)
        con.commit()
    finally:
        con.close()

    con = db.connect(datei)
    try:
        mid = con.execute("SELECT id FROM chat_message ORDER BY id DESC"
                          " LIMIT 1").fetchone()["id"]
        zugrezept.merken(con, mid, [recipe_id])
        karten = zugrezept.zum_zug(con, mid, [])
    finally:
        con.close()

    assert len(karten) == 1
    k = karten[0]
    assert k["gesamt_minuten"] is None and k["zeitsatz"] is None
    assert k["source_rating"] is None
    assert k["n_schritte"] == 0
    assert k["n_zutaten"] == 1


# --------------------------------------------------------------------------
# Die Zahl selbst

@pytest.mark.parametrize("minuten,satz", [
    (None, None), (0, None),
    (35, "35 Minuten"),                  # Käse-Lauch-Suppe: 15 + 20
    (59, "59 Minuten"),
    (60, "1 Stunde"),
    (65, "1 Stunde 5 Minuten"),          # Ratatouille: 30 + 35
    (90, "1½ Stunden"),
    (345, "5 Stunden 45 Minuten"),       # Amaretto-Mousse: 45 + 300
    (570, "9½ Stunden"),                 # Pho Bo: 90 + 480
])
def test_die_gesamtzeit_liest_sich_wie_eine_auskunft(minuten, satz):
    """„9½ Stunden", nicht „570 Minuten" und nicht „prep 90, cook 480"."""
    assert zugrezept.zeitsatz(minuten) == satz


def test_ruhezeit_steht_sichtbar_dabei(datei):
    """Sie ist der Grund, warum aus 90 Minuten neuneinhalb Stunden werden."""
    con = db.connect(datei)
    try:
        rid = recipes.anlegen(con, "Sauerteigbrot")
        con.execute("UPDATE recipe SET prep_minutes = 30, cook_minutes = 60,"
                    " rest_minutes = 480 WHERE id = ?", (rid,))
        _leere_chatzeile(con)
        con.commit()
        mid = con.execute("SELECT id FROM chat_message ORDER BY id DESC"
                          " LIMIT 1").fetchone()["id"]
        zugrezept.merken(con, mid, [rid])
        k = zugrezept.zum_zug(con, mid, [])[0]
    finally:
        con.close()
    assert k["zeitsatz"] == "9½ Stunden"
    assert k["ruhesatz"] == "8 Stunden"


# --------------------------------------------------------------------------
# Der Altbestand

def test_alte_zuege_bekommen_ihr_rezept_zurueck(leere_db_datei):
    """Die Migration trägt nach, was `chat_entwurf` längst weiss (WB-383).

    Ohne den Nachtrag stünde die Karte an keinem einzigen der Züge, die die
    Datenbank schon trägt — und das Ticket wäre für den Bestand wirkungslos.
    """
    con = db.connect(leere_db_datei)
    try:
        rid = recipes.anlegen(con, "Bolognese")
        mid = _leere_chatzeile(con)
        con.execute(
            "INSERT INTO chat_entwurf (chat_message_id, dish, name,"
            " recipe_id, verworfen, created_at)"
            " VALUES (?, 'Bolognese', 'Bolognese', ?, 0, '2026-01-01')",
            (mid, rid))
        # So sieht die Datenbank vor WB-383 aus.
        con.execute("DROP TABLE chat_rezept")
        con.commit()
    finally:
        con.close()

    con = db.connect(leere_db_datei)
    try:
        db.migrate(con)
        zeilen = con.execute("SELECT chat_message_id, recipe_id"
                             "  FROM chat_rezept").fetchall()
        assert [tuple(z) for z in zeilen] == [(mid, rid)]
        # Und ein zweiter Lauf verdoppelt nichts.
        db.migrate(con)
        assert con.execute("SELECT count(*) AS n FROM chat_rezept"
                           ).fetchone()["n"] == 1
    finally:
        con.close()
