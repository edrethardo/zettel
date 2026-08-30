"""Die Zuordnung gehört dem Rezept, nicht dem Zug (WB-408).

Der Nutzer: „Der Wechsel klappt nichteinmal. Fix das so dass es schnell ist.
Fix es vor allem im Design. Das Wechseln von einem Vorschlag zum anderen
sollte quasi sofort funktionieren."

Er dauerte 24 bis 27 Sekunden, weil ein Wechsel denselben Satz noch einmal
durch beide Modellstufen schickte — auch beim Zurückwechseln zu einem Rezept,
das eine Minute vorher schon gerechnet worden war.

**Der Entwurfsfehler ist, WEM die Modellarbeit gehört.** Auf dem Quellenweg
hängt sie an nichts, was ein Wechsel ändert:

    plan.zutatenbegriffe(zutaten, gericht, servings)   nur das REZEPT
    plan.choose("", aufgaben)                          nur BEGRIFFE + KATALOG

Der Satz geht seit WB-386 ausdrücklich nicht mehr in Stufe 3. Damit ist die
Modellarbeit eines Rezeptzugs eine reine Funktion des Rezepts und des
Katalogs — und wurde trotzdem bei jedem Zug neu bezahlt.

Diese Datei prüft die Zusicherungen, die daraus folgen. **Der Zähler ist
`client.llm.aufrufe`**: der Doppelgänger zählt jede Frage an das Modell, und
genau daran hängt „quasi sofort".

Kein Test geht ins Netz und keiner an ein echtes Modell.
"""
from __future__ import annotations

import json

from zettel import db
from zettel.assistant import zuordnung
from zettel.gerichte import speicher

from test_alternativrezepte import (HTMX, PHO_BO, PHO_GA, _antworten,
                                    _pho_geholt, _rezept_des_gerichts, _shop,
                                    _wechsel, _zug_id)
from test_alternativrezepte import datei  # noqa: F401  (Fixture)
from test_wechselabbruch import Wackelbox


def _zeilen(pfad, mid: int) -> list[tuple]:
    """Die Vorschlagszeilen eines Zugs — Produkt, Freitext, Menge."""
    con = db.connect(pfad)
    try:
        return [(r["product_id"], r["free_text"], r["qty"])
                for r in con.execute(
                    "SELECT product_id, free_text, qty FROM chat_suggestion"
                    " WHERE chat_message_id = ? ORDER BY id", (mid,))]
    finally:
        con.close()


def _gemerkt(pfad, recipe_id: int):
    con = db.connect(pfad)
    try:
        return zuordnung.lesen(con, recipe_id)
    finally:
        con.close()


def _rezept_id(pfad, source_id: str) -> int:
    con = db.connect(pfad)
    try:
        return con.execute("SELECT id FROM recipe WHERE source_id = ?",
                           (source_id,)).fetchone()["id"]
    finally:
        con.close()


# --------------------------------------------------------------------------
# 1. Dasselbe Rezept kostet das Modell nur einmal

def test_der_zweite_zug_zum_selben_rezept_fragt_kein_modell(datei, tmp_path):
    """Ein Zug, zwei Modellstufen — und beim zweiten Mal keine.

    `_antworten(datei, 1)` gibt dem Doppelgänger Antworten für GENAU EINEN
    Zug. Fragt der zweite Zug noch einmal, bricht er ab („Mehr Modellaufrufe
    als Antworten") — der Test misst also nicht eine Zahl, sondern eine
    Grenze.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 1))

    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    assert len(client.llm.aufrufe) == 2, "Der erste Zug kostet beide Stufen."
    erster = _zug_id(datei)

    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    assert len(client.llm.aufrufe) == 2, "Der zweite Zug hat gefragt."

    assert _zeilen(datei, _zug_id(datei)) == _zeilen(datei, erster), (
        "Aus dem Gedächtnis kommt eine andere Vorschlagsliste heraus.")


def test_die_zuordnung_steht_am_rezept(datei, tmp_path):
    """Was gemerkt wird, ist die Antwort des Modells — und nur sie."""
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 1))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)

    gemerkt = _gemerkt(datei, _rezept_id(datei, PHO_BO))
    assert gemerkt, "Für das Rezept wurde nichts gemerkt."
    assert [g["suchbegriffe"] for g in gemerkt] == [["Ingwer"],
                                                    ["Mie Nudeln", "Nudeln"]]
    assert all(g["gewaehlt"] for g in gemerkt)
    assert all(g["product_id"] for g in gemerkt)


# --------------------------------------------------------------------------
# 2. Der Wechsel — darum geht das Ticket

def test_der_rueckwechsel_kostet_kein_modell(datei, tmp_path):
    """Hin kostet einen Zug, zurück kostet nichts.

    Genau die Schleife, die der Nutzer gefahren ist: Lasagne -> Lasagne
    Bolognese -> Lasagne. Der dritte Schritt war ein zweiter voller
    Modelllauf für ein Ergebnis, das schon einmal dastand.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 2))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    _wechsel(client, mid, PHO_GA)
    # **Drei und nicht vier** (WB-411): der Wechsel kostet nur noch Stufe 1.
    # Stufe 3 entfällt, weil „Ingwer" und „Mie Nudeln" seit dem ersten Zug im
    # Gedächtnis stehen — dieselben Begriffe, dieselben Kandidaten.
    assert len(client.llm.aufrufe) == 3, "Der Wechsel kostet nur Stufe 1."
    zweiter = _zug_id(datei)

    _wechsel(client, zweiter, PHO_BO)
    assert len(client.llm.aufrufe) == 3, (
        "Der Rückwechsel hat das Modell gefragt, obwohl das Rezept bekannt "
        "ist.")
    assert _rezept_des_gerichts(datei) == PHO_BO


def test_der_wechsel_zu_einem_bekannten_rezept_geht_bei_schlafender_box(
        datei, tmp_path):
    """Der Fall, an dem der Nutzer zweimal hängengeblieben ist.

    Beide Versuche endeten nach 3,04 s am health-Timeout. Zu einem Rezept,
    dessen Zuordnung dasteht, braucht der Zug überhaupt kein Modell — also
    darf er an einer schlafenden Box nicht mehr scheitern.
    """
    box = Wackelbox()
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 2),
                         box=box)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    _wechsel(client, mid, PHO_GA)
    zweiter = _zug_id(datei)

    box.bedient = False
    _, zweite = _wechsel(client, zweiter, PHO_BO)

    assert zweite.status_code == 200
    assert "liess sich nicht wählen" not in zweite.text, zweite.text[:300]
    assert _rezept_des_gerichts(datei) == PHO_BO
    assert _zug_id(datei) != zweiter, "Es ist kein neuer Zug entstanden."


# --------------------------------------------------------------------------
# 3. Was die Zuordnung ungültig macht

def test_ein_neu_geholtes_rezept_vergisst_seine_zuordnung(datei, tmp_path):
    """Andere Zutaten, andere Begriffe — die alte Zuordnung sähe gültig aus."""
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 1))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    rid = _rezept_id(datei, PHO_BO)
    assert _gemerkt(datei, rid)

    con = db.connect(datei)
    try:
        speicher.merken(con, "Pho", {
            "rezept_id": PHO_BO, "titel": "Pho Bo", "servings": 4,
            "zutaten": [{"raw_name": "Reis", "name": "Reis", "amount": 1,
                         "unit": "kg"}]})
    finally:
        con.close()

    assert _gemerkt(datei, rid) is None, (
        "Die Zuordnung hat eine neue Zutatenliste überlebt.")


def test_ein_verschwundenes_produkt_oeffnet_seinen_begriff_wieder():
    """`gewaehlt` und `product_id` beantworten zwei verschiedene Fragen.

    `NULL` bei `gewaehlt = 1` heisst „das Produkt ist aus dem Katalog
    gefallen" — eine offene Frage, die neu gestellt wird. `gewaehlt = 0`
    heisst „das Modell wollte hier nichts" und bleibt zu; sonst kostete jede
    Zutat ohne Katalogtreffer für immer einen Modellaufruf, und die Antwort
    wäre jedes Mal dieselbe.
    """
    gemerkt = [
        {"suchbegriffe": ["Ingwer"], "menge": 1, "product_id": 7,
         "wahl_menge": 1, "gewaehlt": True},
        {"suchbegriffe": ["Sternanis"], "menge": 1, "product_id": None,
         "wahl_menge": None, "gewaehlt": False},
        {"suchbegriffe": ["Reisnudeln"], "menge": 1, "product_id": None,
         "wahl_menge": None, "gewaehlt": True},
    ]
    aufgaben = [{"begriff": "Ingwer"}, {"begriff": "Sternanis"},
                {"begriff": "Reisnudeln"}, {"begriff": "Klopapier"}]

    offen = [a["begriff"] for a in zuordnung.offen(gemerkt, aufgaben)]
    assert offen == ["Reisnudeln", "Klopapier"], offen


def test_ohne_kandidaten_wird_freitext_und_nicht_gefragt(datei, tmp_path):
    """Fällt das letzte Produkt eines Begriffs weg, bleibt nichts zu wählen.

    Dann wird die Zeile Freitext — und das Modell wird trotzdem nicht
    gefragt, denn eine Wahl ohne Kandidaten gibt es nicht (`plan.choose`).
    Vor allem aber wird die Zutatenliste NICHT noch einmal zerlegt.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 1))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    rid = _rezept_id(datei, PHO_BO)

    con = db.connect(datei)
    try:
        weg = con.execute(
            "SELECT product_id FROM recipe_zuordnung"
            " WHERE recipe_id = ? ORDER BY pos LIMIT 1", (rid,)).fetchone()[0]
        # Erst die Zeilen, die auf das Produkt zeigen — dieser Test spielt
        # „aus dem Katalog gefallen", nicht „Datenbank kaputt".
        con.execute("DELETE FROM chat_kandidat WHERE product_id = ?", (weg,))
        con.execute("DELETE FROM chat_suggestion WHERE product_id = ?", (weg,))
        con.execute("DELETE FROM product WHERE id = ?", (weg,))
        con.commit()
        assert zuordnung.lesen(con, rid)[0]["gewaehlt"] is True
        assert zuordnung.lesen(con, rid)[0]["product_id"] is None
    finally:
        con.close()

    vorher = len(client.llm.aufrufe)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    assert len(client.llm.aufrufe) == vorher, (
        "Es wurde gefragt, obwohl es nichts zu wählen gibt.")
    freitexte = [ft for _, ft, _ in _zeilen(datei, _zug_id(datei)) if ft]
    assert freitexte, "Der Begriff ist ohne Produkt und ohne Freitext weg."


def test_ein_notbehelf_wird_nicht_gemerkt(datei, tmp_path):
    """Eine Zerlegung ohne Modell darf sich nicht als Modellantwort ausgeben.

    Sonst hielte ein einziger Ausfall von Stufe 1 das Rezept für immer auf
    der schlechteren Begriffskette fest — gemessen 10 von 14 statt 12 von 12.
    """
    _pho_geholt(datei)
    # Stufe 1 antwortet Unsinn, Stufe 3 ganz normal.
    client, _, _ = _shop(datei, tmp_path,
                         antworten=["kein JSON", json.dumps({"auswahl": []})])
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)

    assert _gemerkt(datei, _rezept_id(datei, PHO_BO)) is None, (
        "Der Notbehelf steht als gemerkte Zuordnung da.")


# --------------------------------------------------------------------------
# 4. Vorwärmen — damit auch der ERSTE Tipp sofort ist

def test_vorwaermen_rechnet_ohne_zug_und_ohne_umzuhaengen(datei, tmp_path):
    """Vorwärmen ist keine Wahl.

    Es rechnet die Zuordnung eines Rezepts, das noch niemand genommen hat —
    also darf danach weder ein Zug im Verlauf stehen noch das Gericht auf ein
    anderes Rezept zeigen (WB-406).
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 2))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    assert len(client.llm.aufrufe) == 2

    antwort = client.post(f"/chat/{mid}/rezept/vorwaermen?rezept={PHO_GA}",
                          headers=HTMX)
    assert antwort.status_code == 204
    # Eine Frage und nicht zwei (WB-411): Stufe 1 muss die Zutatenliste des
    # neuen Rezepts zerlegen, Stufe 3 findet beide Begriffe im Gedächtnis.
    assert len(client.llm.aufrufe) == 3, "Vorwärmen hat nicht gerechnet."
    assert _rezept_des_gerichts(datei) == PHO_BO, "Das Gericht wurde umgehängt."
    assert _zug_id(datei) == mid, "Es ist ein Zug entstanden."
    assert _gemerkt(datei, _rezept_id(datei, PHO_GA)), "Nichts gemerkt."


def test_vorgewaermt_kostet_der_wechsel_kein_modell(datei, tmp_path):
    """Das ist die Zusage des Tickets, in einer Zeile.

    Der Nutzer: „Das Wechseln von einem Vorschlag zum anderen sollte quasi
    sofort funktionieren." Ist vorgewärmt, fragt der Wechsel das Modell kein
    einziges Mal — er ist eine Suche im Katalog und ein paar Zeilen.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 2))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    client.post(f"/chat/{mid}/rezept/vorwaermen?rezept={PHO_GA}", headers=HTMX)
    vorher = len(client.llm.aufrufe)

    _wechsel(client, mid, PHO_GA)

    assert len(client.llm.aufrufe) == vorher, (
        "Der Wechsel hat gefragt, obwohl vorgewärmt war.")
    assert _rezept_des_gerichts(datei) == PHO_GA
    assert _zug_id(datei) != mid, "Es ist kein neuer Zug entstanden."


def test_vorwaermen_ist_zweimal_umsonst(datei, tmp_path):
    """Der zweite Aufruf rechnet nicht noch einmal.

    Der Trigger steht bei jedem Blick in den Chat auf der Karte. Ohne diese
    Sperre kostete das Ansehen einer Seite bei jedem Mal drei Modellläufe.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 2))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    client.post(f"/chat/{mid}/rezept/vorwaermen?rezept={PHO_GA}", headers=HTMX)
    vorher = len(client.llm.aufrufe)

    client.post(f"/chat/{mid}/rezept/vorwaermen?rezept={PHO_GA}", headers=HTMX)
    assert len(client.llm.aufrufe) == vorher


def test_die_karte_waermt_nur_am_juengsten_zug_vor(datei, tmp_path):
    """Drei Trigger, und nur an der Karte, die vor Augen steht.

    Für jede Karte im Verlauf zu rechnen hiesse, bei jedem Blick in den Chat
    die halbe Rezeptliste durch das Modell zu schicken.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 2))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    seite = client.get("/chat").text
    assert seite.count("/rezept/vorwaermen") == 3, (
        "Es sind nicht genau drei Vorwärm-Trigger auf der Seite.")
    assert f'hx-post="/chat/{mid}/rezept/vorwaermen' in seite
    # Das vorgeschlagene Rezept selbst wird nicht vorgewärmt — es ist schon da.
    assert f"rezept={PHO_BO}" not in seite.split("vorwaermen", 1)[1][:400]
