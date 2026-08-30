"""Ein Rezeptwechsel, der nicht zu Ende kommt, darf nichts verändert haben.

**Der Fund kommt aus dem laufenden Shop, nicht aus einer Idee** (2026-08-30).
Der Nutzer hat auf dem Telefon zweimal ein anderes Rezept gewählt; im
Zugriffsprotokoll stehen beide Hälften des Wechsels mit 200:

    POST /chat/38/rezept                                       200
    POST /chat/38/rezept/vorschlaege?rezept=1112181217260303    200
    POST /chat/38/rezept                                       200
    POST /chat/38/rezept/vorschlaege?rezept=120181051167042     200

In der Datenbank steht danach:

    dish 8 „Lasagne Bolognese"  ->  recipe 13 „Lasagne alla Bolognese
                                    mit Béchamelsoße"   (12:56:37)
    chat_rezept zu Zug 38       ->  recipe 11 „Lasagne"
    chat_message                    kein einziger neuer Zug

Und im Chat stand — nachgemessen, während dieser Test entstand — die Karte
„Lasagne" mit der Marke „vorgeschlagen", und darunter „Lasagne alla Bolognese
mit Béchamelsoße" als etwas, das man noch WÄHLEN kann. Man hatte es gerade
gewählt.

Die Ursache steht in den Phoenix-Spuren: beide `chat.turn` endeten nach genau
3,04 s mit `ChatNichtVerfuegbar` — dem health-Timeout aus `zettel.llm.wake`.
Also lief die erste Hälfte (Detail holen, Gericht umhängen — kein Modell), und
die zweite (die zwei Modellstufen) lief nicht.

**Der Zeiger des Gerichts gehört dem ZUG, nicht der Vorschau.** Solange kein
Zug entstanden ist, zeigt das Gericht auf das Rezept, das der Chat zeigt. Das
ist dieselbe Zusicherung, die WB-403 für den Verlauf gibt: was im Dokument
steht und was in der Datenbank steht, soll dasselbe sein.

Geprüft werden die drei Wege, auf denen ein Wechsel steckenbleibt:

    1. die zweite Hälfte wird nie angefordert (Telefon zu, Verbindung weg)
    2. die zweite Hälfte läuft und das Modell bedient nicht
    3. die zweite Hälfte läuft und der Zug scheitert anders

**Kein Test geht ins Netz und keiner an ein Modell.**
"""
from __future__ import annotations

from zettel import db
from zettel.llm import wake

from test_alternativrezepte import (HTMX, PHO_BO, PHO_GA, _antworten, _karte,
                                    _pho_geholt, _rezept_des_gerichts, _shop,
                                    _wechsel, _zug_id)
from test_alternativrezepte import datei  # noqa: F401  (Fixture)


class Wackelbox:
    """Ein Wecker, den ein Test mitten im Ablauf abschalten kann.

    Der Grund ist der gemessene Fall: der erste Zug lief (12:28), der Wechsel
    eine halbe Stunde später fand die Box nicht mehr bedienend.
    """

    def __init__(self):
        self.bedient = True

    def zustand(self):
        if self.bedient:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(
            wake.WACHT_AUF, seit_s=2.0,
            grund="Die Box antwortet, bedient aber noch nicht "
                  "(keine brauchbare Antwort (ReadTimeout)).")


def _zuege(pfad) -> int:
    con = db.connect(pfad)
    try:
        return con.execute("SELECT count(*) AS n FROM chat_message"
                           " WHERE role = 'assistant'").fetchone()["n"]
    finally:
        con.close()


def _erster_zug(datei, tmp_path, box=None):
    """Ein gewöhnlicher Chefkoch-Zug — der Stand vor jedem Wechsel."""
    _pho_geholt(datei)
    client, http, waehler = _shop(datei, tmp_path, box=box)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    return client, http, waehler, _zug_id(datei)


# --------------------------------------------------------------------------
# 1. Die zweite Hälfte kommt nie

def test_ohne_zweite_haelfte_bleibt_das_gericht_beim_alten_rezept(datei,
                                                                  tmp_path):
    """Telefon zu, Verbindung weg — und die Wahl war trotzdem schon gebucht.

    Die erste Hälfte kostet kein Modell und kommt deshalb immer durch. Hängt
    sie das Gericht um, ist die Wahl vollzogen, ohne dass je ein Zug dazu
    entstanden wäre — und der Chat zeigt weiter das alte Rezept.
    """
    client, _, _, mid = _erster_zug(datei, tmp_path)
    assert _rezept_des_gerichts(datei) == PHO_BO

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                          headers=HTMX)
    assert antwort.status_code == 200
    # Die Vorschau zeigt das gewählte Rezept — darum geht WB-402, und das
    # bleibt so.
    assert "Pho Ga" in antwort.text

    assert _rezept_des_gerichts(datei) == PHO_BO, (
        "Die Vorschau hat das Gericht umgehängt, obwohl kein Zug entstanden "
        "ist.")


# --------------------------------------------------------------------------
# 2. Die zweite Hälfte läuft, das Modell bedient nicht

def test_gescheiterter_zug_laesst_das_gericht_stehen(datei, tmp_path):
    """Der gemessene Fall: 3,04 s health-Timeout, kein Zug, Gericht umgehängt."""
    box = Wackelbox()
    client, _, _, mid = _erster_zug(datei, tmp_path, box=box)
    vorher = _zuege(datei)

    box.bedient = False
    erste, zweite = _wechsel(client, mid, PHO_GA)
    assert erste.status_code == 200 and zweite.status_code == 200
    assert _zuege(datei) == vorher, "Es ist doch ein Zug entstanden."

    assert _rezept_des_gerichts(datei) == PHO_BO, (
        "Das Gericht zeigt auf das gewählte Rezept, obwohl der Zug dazu nie "
        "gelaufen ist.")


def test_die_meldung_behauptet_keinen_wechsel(datei, tmp_path):
    """Was die Meldung sagt, muss danach in der Datenbank stehen.

    Sie sagte „„Pho Ga" ist jetzt das Rezept zu „Pho"" — und genau das darf
    nach einem gescheiterten Zug nicht mehr gelten.
    """
    box = Wackelbox()
    client, _, _, mid = _erster_zug(datei, tmp_path, box=box)
    box.bedient = False
    _, zweite = _wechsel(client, mid, PHO_GA)

    assert "ist jetzt das Rezept" not in zweite.text, zweite.text[:400]


def test_der_chat_zeigt_danach_dasselbe_rezept_wie_das_gericht(datei,
                                                               tmp_path):
    """Die Karte und die Datenbank dürfen sich nicht widersprechen.

    Genau dieser Widerspruch stand im laufenden Shop: Überschrift „Lasagne",
    Marke „vorgeschlagen" an „Lasagne" — und `dish` zeigte auf ein anderes
    Rezept, das darunter noch als wählbar angeboten wurde.
    """
    box = Wackelbox()
    client, _, _, mid = _erster_zug(datei, tmp_path, box=box)
    box.bedient = False
    _wechsel(client, mid, PHO_GA)

    karte = _karte(client.get("/chat").text)
    con = db.connect(datei)
    try:
        gezeigt = con.execute(
            "SELECT r.source_id FROM chat_rezept z"
            " JOIN recipe r ON r.id = z.recipe_id"
            " WHERE z.chat_message_id = ?", (mid,)).fetchone()["source_id"]
    finally:
        con.close()
    assert gezeigt == _rezept_des_gerichts(datei), (
        "Der Chat zeigt ein anderes Rezept als das, auf das das Gericht "
        f"zeigt: Karte {gezeigt}, Gericht {_rezept_des_gerichts(datei)}\n"
        + karte[:400])


# --------------------------------------------------------------------------
# 3. Der geglückte Wechsel darf davon nichts merken

def test_der_geglueckte_wechsel_haengt_das_gericht_um(datei, tmp_path):
    """Die Gegenprobe: geht der Zug durch, gilt die Wahl."""
    client, _, _, mid = _erster_zug(datei, tmp_path)
    erste, zweite = _wechsel(client, mid, PHO_GA)

    assert erste.status_code == 200 and zweite.status_code == 200
    assert _rezept_des_gerichts(datei) == PHO_GA
    assert "Pho Ga" in zweite.text


# --------------------------------------------------------------------------
# 4. Hin und zurück, und einmal quer durch den gemessenen Ablauf

def test_hin_und_zurueck_landet_wieder_beim_ersten_rezept(datei, tmp_path):
    """Zwei geglückte Wechsel hintereinander — genau das hat der Nutzer getan.

    Der zweite geht vom NEUEN Zug aus: dessen Karte trägt das gewählte
    Rezept, und seine Liste stellt das alte wieder zur Wahl. Ein Rückwechsel
    kostet keine Anfrage mehr (WB-387), er ist eine Zeile in `dish`.
    """
    _pho_geholt(datei)
    client, http, waehler = _shop(datei, tmp_path,
                                  antworten=_antworten(datei, 3))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    _wechsel(client, mid, PHO_GA)
    assert _rezept_des_gerichts(datei) == PHO_GA
    zweiter = _zug_id(datei)
    assert zweiter != mid, "Der Wechsel hat keinen neuen Zug erzeugt."

    _wechsel(client, zweiter, PHO_BO)
    assert _rezept_des_gerichts(datei) == PHO_BO
    # Nur EIN Detail wurde je geholt: das der Alternative. Der Rückweg zum
    # ersten Rezept fasst die Quelle nicht an.
    assert waehler.gewaehlt == [PHO_GA], waehler.gewaehlt

    # Und im Verlauf steht von der Kette nur ihr letztes Glied (WB-403).
    seite = client.get("/chat").text
    assert seite.count('<section class="zugrezept">') == 1, (
        "Nach zwei Wechseln stehen mehrere Rezeptkarten im Verlauf.")


def test_nach_dem_fehlschlag_geht_der_wechsel_wieder(datei, tmp_path):
    """Der ganze gemessene Ablauf: zweimal scheitern, dann geht es.

    Der Nutzer hat am 2026-08-30 genau das getan — zwei Wechsel, beide ohne
    Zug. Danach muss der dritte Versuch ein gewöhnlicher Wechsel sein und
    nicht auf den Resten der beiden ersten aufsitzen.
    """
    box = Wackelbox()
    client, _, _, mid = _erster_zug(datei, tmp_path, box=box)

    box.bedient = False
    _wechsel(client, mid, PHO_GA)
    _wechsel(client, mid, PHO_GA)
    assert _rezept_des_gerichts(datei) == PHO_BO
    assert _zuege(datei) == 1

    box.bedient = True
    _, zweite = _wechsel(client, mid, PHO_GA)
    assert _rezept_des_gerichts(datei) == PHO_GA
    assert _zuege(datei) == 2
    assert "Pho Ga" in zweite.text
