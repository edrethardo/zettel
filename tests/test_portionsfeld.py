"""Die Portionsgrösse ist einstellbar — und das Rezeptfeld hält, was es sagt
(WB-384).

Zwei Hälften eines Tickets, und beide sind Zusicherungen über ZAHLEN und nicht
über Knöpfe:

* **Im Chat gab es das Feld gar nicht.** Wer „alles für Lasagne" schrieb,
  bekam die Portionszahl der Quelle und konnte sie nicht ändern; die Mengen
  aus WB-369 wurden für eine Personenzahl gerechnet, die niemand gewählt
  hatte.
* **Auf der Rezeptseite log das obere von zwei Feldern namens „Portionen".**
  Gemessen vor dem Ticket: `servings` von 4 auf 8 gesetzt, die Zutaten
  unverändert bei 500,0 g — und darüber las die Seite „Zutaten laut Rezept
  (für 8 Portionen)".

Deshalb prüft hier kein Test bloss, DASS ein Feld dasteht. Nach jedem Tipp
wird nachgesehen, was in `recipe_ingredient`, `recipe_item`, `chat_suggestion`
und `order_item` steht.

Der Chatteil borgt sich den aufgezeichneten Pho-Zug aus
`test_web_zugrezept.py` — dieselbe Fixture, dasselbe Fake-Modell, kein Netz.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import db, orders, recipes
from zettel.web import app as webapp

from test_web_zugrezept import HTMX, _karte, _pho_zug  # noqa: F401
from test_web_zugrezept import datei  # noqa: F401  (Fixture)


# --------------------------------------------------------------------------
# Teil 1: die Rezeptseite
#
# Ein selbst gebautes Rezept statt des Katalogs: es geht um Mengen und
# Portionen, und die müssen im Test dastehen, damit die Rechnung nachlesbar
# ist — dieselbe Bauart wie in `test_portionen.py` (WB-362).

@pytest.fixture
def bild_dir(tmp_path):
    d = tmp_path / "bilder"
    d.mkdir()
    return d


@pytest.fixture
def client(db_datei, bild_dir):
    with TestClient(webapp.create_app(db_path=db_datei,
                                      image_dir=bild_dir)) as c:
        yield c


@pytest.fixture
def con(db_datei):
    c = db.connect(db_datei)
    yield c
    c.close()


def _hack(con):
    """Ein Produkt mit einer lesbaren Packungsgrösse."""
    cur = con.execute(
        "INSERT INTO product (source, external_id, name, unit_text,"
        " price_cents) VALUES ('test', 'hack', 'Hackfleisch gemischt',"
        " '500 g', 499)")
    con.commit()
    return int(cur.lastrowid)


def _lasagne(con, servings=4):
    """Das Rezept aus der Messung: 4 Portionen, 500 g Hackfleisch.

    Mit BEIDEN Mengenspalten, weil es genau um ihren Zusammenhang geht:
    `recipe_ingredient` ist die Liste, wie die Quelle sie schreibt, und
    `recipe_item.amount` die Menge am verknüpften Produkt.
    """
    pid = _hack(con)
    recipe_id = recipes.anlegen(con, "Lasagne", servings=servings,
                                zutaten=[{"product_id": pid, "qty": 1,
                                          "amount": 500, "unit": "g"}])
    con.execute(
        "INSERT INTO recipe_ingredient (recipe_id, pos, raw_name, name,"
        " amount, unit) VALUES (?, 0, 'Hackfleisch', 'Hackfleisch', 500, 'g')",
        (recipe_id,))
    con.commit()
    return recipe_id, pid


def _mengen(con, recipe_id):
    zutat = con.execute("SELECT amount FROM recipe_ingredient"
                        " WHERE recipe_id = ?", (recipe_id,)).fetchone()
    produkt = con.execute("SELECT amount FROM recipe_item"
                          " WHERE recipe_id = ?", (recipe_id,)).fetchone()
    return zutat["amount"], produkt["amount"]


def test_die_zwei_portionsfelder_sind_ohne_quelltext_unterscheidbar(client,
                                                                    con):
    """Bis WB-384 hiessen beide „Portionen" und meinten Verschiedenes.

    Der Unterschied stand in einem Quelltext-Kommentar — also nirgends, wo
    ihn jemand liest, der den Shop bedient.
    """
    recipe_id, _ = _lasagne(con)
    text = client.get(f"/rezepte/{recipe_id}").text

    # Das Feld am Korb-Knopf: dieser Einkauf, und das Rezept bleibt.
    assert "Diesmal für wie viele Portionen?" in text
    assert "Gilt nur für diesen Einkauf" in text
    assert "4 Portionen" in text
    # Das Feld in den Kopfdaten: das Rezept selbst, dauerhaft.
    assert "Für wie viele Portionen das Rezept gilt" in text
    assert "Dauerhaft, und alle Mengen" in text
    # Und keines der beiden heisst mehr bloss „Portionen".
    assert ">Portionen<" not in text


def test_servings_zu_aendern_rechnet_die_mengen_mit(client, con):
    """Der gemessene Fall des Tickets — jetzt mit stimmenden Zahlen.

    Vorher: `servings` 4 -> 8, Zutaten unverändert 500,0 g, Überschrift
    „Zutaten laut Rezept (für 8 Portionen)". Das ist eine Falschaussage.
    """
    recipe_id, _ = _lasagne(con)
    antwort = client.post(f"/rezepte/{recipe_id}/bearbeiten",
                          data={"name": "Lasagne", "servings": "8",
                                "note": ""}, follow_redirects=False)
    assert antwort.status_code == 200

    assert _mengen(con, recipe_id) == (1000.0, 1000.0)
    seite = client.get(f"/rezepte/{recipe_id}").text
    assert "Zutaten laut Rezept (für 8 Portionen)" in seite
    # Die Zeile unter der Überschrift trägt sie jetzt.
    assert '<span class="menge">1000 g</span>' in seite
    assert '<span class="menge">500 g</span>' not in seite
    # Und es wird gesagt, statt geräuschlos zu geschehen.
    assert "gilt jetzt für 8 statt 4 Portionen" in antwort.text


def test_die_umrechnung_wirkt_auf_den_naechsten_einkauf(client, con):
    """Nicht nur die Anzeige: der Einkauf rechnet danach von der neuen Zahl.

    Das ist der Grund, warum der andere Weg ausscheidet — `servings` ist die
    Grösse, auf die `recipes.in_den_korb` jede Menge bezieht. Eine geänderte
    Zahl ohne mitgerechnete Mengen verstellte jeden künftigen Einkauf, und
    zwar unsichtbar.
    """
    recipe_id, _ = _lasagne(con)
    client.post(f"/rezepte/{recipe_id}/bearbeiten",
                data={"name": "Lasagne", "servings": "8", "note": ""})
    bericht = recipes.in_den_korb(con, recipe_id)
    assert bericht["portionen"] == 8
    posten = orders.inhalt(con)[0]
    # 1000 g gebraucht, 500-g-Packung -> zwei Packungen.
    assert posten["need_amount"] == 1000.0
    assert posten["qty"] == 2


def test_ohne_portionszahl_am_rezept_wird_nichts_geraten(client, con):
    """Regel 4: kein Faktor, keine Änderung — und die Seite sagt es."""
    recipe_id, _ = _lasagne(con, servings=None)
    seite = client.get(f"/rezepte/{recipe_id}").text
    assert "Am Rezept steht keine Portionszahl" in seite

    antwort = client.post(f"/rezepte/{recipe_id}/bearbeiten",
                          data={"name": "Lasagne", "servings": "8",
                                "note": ""}, follow_redirects=False)
    # Gespeichert wird die Zahl, gerechnet wird nichts: es gibt nichts,
    # wovon aus. Also auch keine Behauptung, es sei gerechnet worden.
    assert antwort.status_code == 303
    assert _mengen(con, recipe_id) == (500.0, 500.0)
    assert con.execute("SELECT servings FROM recipe WHERE id = ?",
                       (recipe_id,)).fetchone()["servings"] == 8


def test_ein_eingelegter_korbposten_wird_nicht_rueckwirkend_umgerechnet(
        client, con):
    """Was im Korb liegt, gehört dem Korb (WB-361).

    Die Korbzeile ist beim Einlegen entstanden und kann längst eine sein, die
    die Nutzerin selbst aufgestockt hat.
    """
    recipe_id, _ = _lasagne(con)
    recipes.in_den_korb(con, recipe_id)
    vorher = orders.inhalt(con)[0]
    assert vorher["need_amount"] == 500.0

    client.post(f"/rezepte/{recipe_id}/bearbeiten",
                data={"name": "Lasagne", "servings": "8", "note": ""})
    nachher = orders.inhalt(con)[0]
    assert nachher["need_amount"] == 500.0
    assert nachher["qty"] == vorher["qty"]


# --------------------------------------------------------------------------
# Teil 2: der Chat

def _mid(karte: str) -> int:
    """Die Zug-id aus der Adresse des Portionsformulars."""
    marke = 'action="/chat/'
    assert marke in karte, "kein Portionsfeld an der Karte"
    return int(karte.split(marke)[-1].split("/portionen")[0])


def _bedarf(pfad: Path) -> dict:
    """Name -> benötigte Menge, wie sie an den Vorschlagszeilen steht."""
    con = db.connect(pfad)
    try:
        return {r["name"]: r["need_amount"] for r in con.execute(
            "SELECT coalesce(p.name, s.free_text) AS name, s.need_amount"
            "  FROM chat_suggestion s"
            "  LEFT JOIN product p ON p.id = s.product_id"
            " WHERE s.need_amount IS NOT NULL")}
    finally:
        con.close()


def test_der_chat_hat_ein_portionsfeld_vorbelegt_aus_der_quelle(datei,
                                                                tmp_path):
    """Das Feld, das WB-369 offengelassen hat.

    Nicht aus dem Satz geraten (dort steht keine Ziffer), sondern getippt —
    und vorbelegt mit Chefkochs sechs Portionen.
    """
    client, _ = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)

    assert 'class="zugportionen"' in karte
    assert "Für wie viele Portionen?" in karte
    assert 'name="portionen"' in karte and 'value="6"' in karte
    assert "Das Rezept rechnet mit 6 Portionen." in karte
    # Und die Zusage über den Korb steht vor dem Tipp da, nicht erst danach.
    assert "was schon im\n      Korb liegt, bleibt liegen" in karte


def test_eine_andere_portionszahl_rechnet_die_mengen_neu(datei, tmp_path):
    """Von 6 auf 12: jede benötigte Menge verdoppelt sich — über `mengen`."""
    client, _ = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)
    mid = _mid(karte)
    vorher = _bedarf(datei)
    assert vorher, "der Zug trägt gar keine Mengen"

    antwort = client.post(f"/chat/{mid}/portionen",
                          data={"rezept": "1", "portionen": "12"},
                          headers=HTMX)
    assert antwort.status_code == 200

    nachher = _bedarf(datei)
    assert nachher == {name: menge * 2 for name, menge in vorher.items()}
    # Die Karte sagt, wovon hochgerechnet wurde — sonst wäre Chefkochs Zahl
    # nach dem ersten Tippen verschwunden.
    karte = _karte(client.get("/chat").text)
    assert 'value="12"' in karte
    assert "Hochgerechnet von 6 Portionen laut Rezept." in karte


def test_die_zutatenliste_der_karte_folgt_der_gewaehlten_zahl(datei,
                                                              tmp_path):
    """Sonst stünde über „500 g" die Zahl 12 — dieselbe Lüge eine Ebene
    tiefer.

    Gespeichert wird dabei nichts: `recipe_ingredient` ist die Liste der
    Quelle, und dieser Zug darf sie nicht umschreiben.
    """
    client, recipe_id = _pho_zug(datei, tmp_path)
    karte = _karte(client.get("/chat").text)
    mid = _mid(karte)
    assert "500 g" in karte

    client.post(f"/chat/{mid}/portionen",
                data={"rezept": str(recipe_id), "portionen": "12"},
                headers=HTMX)
    karte = _karte(client.get("/chat").text)
    assert "1000 g" in karte

    con = db.connect(datei)
    try:
        roh = con.execute(
            "SELECT amount FROM recipe_ingredient"
            " WHERE recipe_id = ? AND unit = 'g' ORDER BY pos",
            (recipe_id,)).fetchall()
        assert 500.0 in [r["amount"] for r in roh]
    finally:
        con.close()


def test_was_schon_im_korb_liegt_wird_nicht_nachgerechnet(datei, tmp_path):
    """Ein bestätigter Vorschlag bleibt, wie er ist — und der Korb auch.

    Dieselbe Haltung wie beim Rezeptwechsel (WB-387) und bei der Rücknahme
    (WB-361): die neue Zahl wirkt auf das Nächste.
    """
    client, recipe_id = _pho_zug(datei, tmp_path)
    seite = client.get("/chat").text
    mid = _mid(_karte(seite))

    con = db.connect(datei)
    try:
        zeile = con.execute(
            "SELECT id FROM chat_suggestion WHERE need_amount IS NOT NULL"
            " ORDER BY id LIMIT 1").fetchone()
        sid = zeile["id"]
    finally:
        con.close()
    client.post(f"/chat/vorschlag/{sid}/entscheiden",
                data={"decision": "kept"}, headers=HTMX)

    vorher = _bedarf(datei)
    korb_con = db.connect(datei)
    try:
        korb_vorher = orders.inhalt(korb_con)
    finally:
        korb_con.close()
    client.post(f"/chat/{mid}/portionen",
                data={"rezept": str(recipe_id), "portionen": "12"},
                headers=HTMX)

    con = db.connect(datei)
    try:
        eingelegt = con.execute(
            "SELECT coalesce(p.name, s.free_text) AS name, s.need_amount"
            "  FROM chat_suggestion s LEFT JOIN product p"
            "    ON p.id = s.product_id WHERE s.id = ?", (sid,)).fetchone()
        korb_nachher = orders.inhalt(con)
    finally:
        con.close()

    assert eingelegt["need_amount"] == vorher[eingelegt["name"]]
    assert ([p["need_amount"] for p in korb_nachher]
            == [p["need_amount"] for p in korb_vorher])
    # Die übrigen Zeilen sind trotzdem umgerechnet.
    nachher = _bedarf(datei)
    andere = [n for n in nachher if n != eingelegt["name"]]
    assert andere and all(nachher[n] == vorher[n] * 2 for n in andere)


def test_ohne_portionszahl_am_rezept_bietet_die_karte_kein_feld(datei,
                                                                tmp_path):
    """Regel 4 im Chat: ohne Faktor kein Feld, aber ein Satz.

    Ein Feld, das nichts bewirkt, verspricht eine Rechnung, die es nicht
    gibt.
    """
    client, recipe_id = _pho_zug(datei, tmp_path)
    con = db.connect(datei)
    try:
        con.execute("UPDATE recipe SET servings = NULL WHERE id = ?",
                    (recipe_id,))
        con.commit()
    finally:
        con.close()

    karte = _karte(client.get("/chat").text)
    assert 'class="zugportionen"' not in karte
    assert "Am Rezept steht keine Portionszahl" in karte


def test_eine_zahl_die_keine_ist_verstellt_keine_menge(datei, tmp_path):
    """Getippt wird auf einem Telefon. Ein Vertipper kostet keinen Einkauf."""
    client, recipe_id = _pho_zug(datei, tmp_path)
    mid = _mid(_karte(client.get("/chat").text))
    vorher = _bedarf(datei)

    antwort = client.post(f"/chat/{mid}/portionen",
                          data={"rezept": str(recipe_id), "portionen": "acht"},
                          headers=HTMX)
    assert antwort.status_code == 200
    assert _bedarf(datei) == vorher
    assert "keine Portionszahl" in antwort.text
