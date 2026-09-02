"""Tests für die Rezeptansicht (WB-325).

Kein Browser, kein Netz — dieselbe Bauart wie `test_web_orders.py`: die
Oberfläche wird über `fastapi.testclient` als HTTP-Client geprüft, und hinter
jedem Klick steht eine Abfrage, was danach in der Datenbank steht. Dass eine
Seite einen Knopf zeigt, sagt nichts darüber, ob der Knopf etwas tut.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import db, orders, recipes
from zettel.web import app as webapp

STIL = Path(webapp.STATIC_DIR) / "stil.css"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"

HTMX = {"HX-Request": "true"}


@pytest.fixture
def bild_dir(tmp_path):
    d = tmp_path / "bilder"
    d.mkdir()
    return d


@pytest.fixture
def client(db_datei, bild_dir):
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=bild_dir)) as c:
        yield c


@pytest.fixture
def con(db_datei):
    c = db.connect(db_datei)
    yield c
    c.close()


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _ausmustern(con, name):
    con.execute("UPDATE product SET active = 0 WHERE name = ?", (name,))
    con.commit()


def _rid(client, name="Milchreis"):
    """Legt ein Rezept über die Oberfläche an und gibt seine id zurück."""
    r = client.post("/rezepte", data={"name": name}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


# --------------------------------------------------------------------------
# Navigation und Liste (Spec 9)

def test_rezepte_stehen_unter_mehr(client):
    """Seit Welle 2 hat die Leiste fünf Reiter; die Rezepte stehen unter „Mehr"."""
    assert '<a href="/rezepte">Rezepte</a>' in client.get("/mehr").text


def test_leere_liste_sagt_es_und_bietet_das_anlegen_an(client):
    text = client.get("/rezepte").text
    assert "Noch kein Rezept" in text
    assert 'name="name"' in text


def test_rezept_anlegen_ueber_die_oberflaeche(client, con):
    r = client.post("/rezepte", data={"name": "Milchreis"},
                    follow_redirects=False)
    assert r.status_code == 303
    neu = int(r.headers["location"].rsplit("/", 1)[1])
    assert recipes.rezept(con, neu)["name"] == "Milchreis"
    assert "Milchreis" in client.get("/rezepte").text


def test_rezept_ohne_namen_wird_abgelehnt_mit_ansage(client, con):
    r = client.post("/rezepte", data={"name": "   "})
    assert r.status_code == 200
    assert "braucht einen Namen" in r.text
    assert recipes.rezepte(con) == []


def test_rezept_das_es_nicht_gibt_ist_eine_404(client):
    assert client.get("/rezepte/999999").status_code == 404
    assert client.post("/rezepte/999999/korb").status_code == 404


# --------------------------------------------------------------------------
# Zutaten: Katalogprodukt und Freitext gleichwertig (Spec 4)

def test_zutat_aus_dem_katalog_ueber_die_suche(client, con):
    rid = _rid(client)
    treffer = client.get(f"/rezepte/{rid}/suche?q=Landmilch", headers=HTMX)
    assert MILCH in treffer.text
    assert f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}" in treffer.text

    r = client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                    headers=HTMX)
    assert r.status_code == 200
    assert "<html" not in r.text.lower()          # nur das Bruchstück
    zutaten = recipes.zutaten(con, rid)
    assert [z["product_id"] for z in zutaten] == [_pid(con, MILCH)]


def test_gemischtes_rezept_ueber_die_oberflaeche(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                headers=HTMX)
    r = client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "Zimt"},
                    headers=HTMX)
    assert "Zimt" in r.text and MILCH in r.text

    zutaten = recipes.zutaten(con, rid)
    assert [z["name"] for z in zutaten] == [MILCH, "Zimt"]
    assert zutaten[0]["free_text"] is None
    assert zutaten[1]["product_id"] is None


def test_leerer_freitext_ergibt_keine_zutat_sondern_eine_ansage(client, con):
    rid = _rid(client)
    r = client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "  "},
                    headers=HTMX)
    assert r.status_code == 200
    assert "leeres Feld" in r.text
    assert recipes.zutaten(con, rid) == []


def test_menge_und_loeschen_einer_zutat(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "Zimt"},
                headers=HTMX)
    zid = recipes.zutaten(con, rid)[0]["id"]

    client.post(f"/rezepte/{rid}/zutaten/{zid}/menge?qty=3", headers=HTMX)
    assert recipes.zutaten(con, rid)[0]["qty"] == 3

    client.post(f"/rezepte/{rid}/zutaten/{zid}/loeschen", headers=HTMX)
    assert recipes.zutaten(con, rid) == []


# --------------------------------------------------------------------------
# Bearbeiten und Löschen

def test_kopfdaten_bearbeiten(client, con):
    rid = _rid(client, "Alt")
    r = client.post(f"/rezepte/{rid}/bearbeiten",
                    data={"name": "Neu", "servings": "4", "note": "mit Zimt"},
                    follow_redirects=False)
    assert r.status_code == 303
    rezept = recipes.rezept(con, rid)
    assert (rezept["name"], rezept["servings"], rezept["note"]) == \
        ("Neu", 4, "mit Zimt")
    seite = client.get(f"/rezepte/{rid}").text
    assert 'value="Neu"' in seite and 'value="4"' in seite


def test_rezept_ohne_namen_speichern_geht_nicht(client, con):
    rid = _rid(client, "Alt")
    r = client.post(f"/rezepte/{rid}/bearbeiten", data={"name": " "})
    assert r.status_code == 200
    assert "braucht einen Namen" in r.text
    assert recipes.rezept(con, rid)["name"] == "Alt"


def test_rezept_loeschen_ueber_die_oberflaeche(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "Zimt"},
                headers=HTMX)
    r = client.post(f"/rezepte/{rid}/loeschen", follow_redirects=False)
    # Der Name reist in der Weiterleitung mit (WB-376): ohne ihn sähe die
    # Rezeptliste nach dem Löschen aus wie nach einem Abbruch.
    assert r.status_code == 303
    assert r.headers["location"].startswith("/rezepte?weg=")
    assert recipes.rezepte(con) == []
    assert "ist gelöscht" in client.get(r.headers["location"]).text
    assert client.get(f"/rezepte/{rid}").status_code == 404


# --------------------------------------------------------------------------
# Alles in den Warenkorb

def test_alles_in_den_warenkorb(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}&qty=2",
                headers=HTMX)
    client.post(f"/rezepte/{rid}/zutaten?qty=3", data={"free_text": "Zimt"},
                headers=HTMX)

    r = client.post(f"/rezepte/{rid}/korb", headers=HTMX)
    assert r.status_code == 200
    assert "im Korb" in r.text
    assert [(z["name"], z["qty"]) for z in orders.inhalt(con)] == \
        [(MILCH, 2), ("Zimt", 3)]


def test_leeres_rezept_bietet_den_korbknopf_gar_nicht_erst_an(client, con):
    rid = _rid(client)
    assert "Alles in den Warenkorb" not in client.get(f"/rezepte/{rid}").text

    # Und wer den Knopf trotzdem abschickt (altes Formular im Browser),
    # bekommt eine Ansage statt eines leeren Warenkorbs in der Datenbank.
    r = client.post(f"/rezepte/{rid}/korb", headers=HTMX)
    assert r.status_code == 200
    assert "keine Zutaten" in r.text
    assert con.execute("SELECT count(*) n FROM orders").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Ein Rezept überlebt den Katalog — der Kern dieses Tickets

def test_ausgemusterte_zutat_steht_sichtbar_im_rezept(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                headers=HTMX)
    _ausmustern(con, MILCH)

    seite = client.get(f"/rezepte/{rid}").text
    assert MILCH in seite                      # nicht stillschweigend weg
    assert "nicht mehr im Katalog" in seite
    assert "fehlt-im-katalog" in seite
    # Und schon in der Liste, bevor man das Rezept überhaupt öffnet.
    assert "1 nicht mehr im Katalog" in client.get("/rezepte").text


def test_ausgemusterte_zutat_laesst_sich_weiter_einlegen(client, con):
    rid = _rid(client)
    pid = _pid(con, MILCH)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&qty=2", headers=HTMX)
    client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "Zimt"},
                headers=HTMX)
    _ausmustern(con, MILCH)

    r = client.post(f"/rezepte/{rid}/korb", headers=HTMX)
    assert r.status_code == 200                # bricht nicht
    assert "Nicht mehr im Katalog" in r.text and MILCH in r.text

    zeilen = orders.inhalt(con)
    assert [(z["name"], z["qty"]) for z in zeilen] == [(MILCH, 2), ("Zimt", 1)]
    assert zeilen[0]["product_id"] == pid


def test_ausgemustertes_produkt_taucht_in_der_zutatensuche_nicht_mehr_auf(client, con):
    """Die Suche zeigt nur aktive Produkte — das Rezept behält seine trotzdem."""
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                headers=HTMX)
    _ausmustern(con, MILCH)
    assert MILCH not in client.get(f"/rezepte/{rid}/suche?q=Landmilch",
                                   headers=HTMX).text
    assert MILCH in client.get(f"/rezepte/{rid}").text


# --------------------------------------------------------------------------
# Daraus ein Rezept machen (Spec 6)

@pytest.fixture
def erledigte_bestellung(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    client.post("/warenkorb/einlegen", data={"free_text": "Klopapier"},
                headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    b = orders.bestellungen(con, "offen")[0]["id"]
    for p in orders.posten(con, b):
        client.post(f"/pick/{b}/posten/{p['id']}?stand=gepickt", headers=HTMX)
    assert orders.bestellung(con, b)["state"] == "erledigt"
    return b


def test_knopf_steht_an_der_erledigten_bestellung(client, erledigte_bestellung):
    text = client.get("/bestellungen").text
    assert "Daraus ein Rezept machen" in text
    assert f'action="/bestellungen/{erledigte_bestellung}/rezept"' in text


def test_offene_bestellung_hat_den_knopf_nicht(client, con):
    """Spec 6: der Moment, in dem die Zutaten beisammen sind, ist der Abschluss."""
    client.post("/warenkorb/einlegen", data={"free_text": "Klopapier"},
                headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    assert "Daraus ein Rezept machen" not in client.get("/bestellungen").text


def test_aus_der_bestellung_wird_ein_rezept_mit_deren_posten(client, con,
                                                             erledigte_bestellung):
    r = client.post(f"/bestellungen/{erledigte_bestellung}/rezept",
                    data={"name": "Sonntag"}, follow_redirects=False)
    assert r.status_code == 303
    neu = int(r.headers["location"].rsplit("/", 1)[1])

    rezept = recipes.rezept(con, neu)
    assert rezept["name"] == "Sonntag"
    assert [z["name"] for z in rezept["zutaten"]] == [MILCH, "Klopapier"]
    assert rezept["zutaten"][0]["product_id"] == _pid(con, MILCH)

    seite = client.get(f"/rezepte/{neu}").text
    assert "Sonntag" in seite and MILCH in seite and "Klopapier" in seite


def test_rezept_aus_bestellung_ohne_namen_bekommt_einen_vorschlag(
        client, con, erledigte_bestellung):
    r = client.post(f"/bestellungen/{erledigte_bestellung}/rezept",
                    data={"name": ""}, follow_redirects=False)
    neu = int(r.headers["location"].rsplit("/", 1)[1])
    assert recipes.rezept(con, neu)["name"].startswith("Einkauf vom ")


def test_rezept_aus_einer_bestellung_die_es_nicht_gibt(client, con):
    r = client.post("/bestellungen/999999/rezept", data={"name": "X"})
    assert r.status_code == 404
    assert recipes.rezepte(con) == []


# --------------------------------------------------------------------------
# Handy: eine Spalte, Tap-Ziele mindestens 44 px (Spec 9)

def test_rezeptansicht_ist_fuers_handy_gebaut(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "Zimt"},
                headers=HTMX)
    seite = client.get(f"/rezepte/{rid}").text
    # Dieselben Zeilen- und Knopfklassen wie im Warenkorb — eine Spalte.
    assert 'class="korb"' in seite and 'class="mini"' in seite
    assert 'name="viewport"' in seite

    stil = STIL.read_text(encoding="utf-8")
    tap = int(re.search(r"--tap:\s*(\d+)px", stil).group(1))
    assert tap >= 44
    for klasse in (".mini {", ".rezeptkopf input, .rezeptkopf textarea {",
                   ".rezept-daraus input {"):
        block = stil.split(klasse, 1)[1].split("}", 1)[0]
        assert "min-height: var(--tap)" in block, klasse


# --------------------------------------------------------------------------
# Portionen wählen und die Rechnung zeigen (WB-362)

def _mit_gebinde(con, name, unit_text):
    """Setzt einer Katalogzeile eine bekannte Packungsgrösse.

    Die aufgezeichnete Fixture bringt echte `unit_text` mit; für eine
    nachlesbare Rechnung muss die Zahl im Test stehen und nicht in einer
    Aufzeichnung, die sich beim nächsten Crawl ändern kann.
    """
    pid = _pid(con, name)
    con.execute("UPDATE product SET unit_text = ? WHERE id = ?",
                (unit_text, pid))
    con.commit()
    return pid


def test_die_portionszahl_steht_neben_dem_korbknopf(client, con):
    """Vorbelegt mit dem, was am Rezept steht — dort wird sie gewählt.

    Nicht in den Kopfdaten: „diesmal für acht" ist eine Aussage über diesen
    Einkauf, und wer sie dort einträgt, ändert das Rezept.
    """
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/bearbeiten",
                data={"name": "Sugo", "servings": "4"})
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                headers=HTMX)

    seite = client.get(f"/rezepte/{rid}").text
    assert 'name="portionen"' in seite
    assert "Für wie viele Portionen" in seite
    feld = seite.split('name="portionen"', 1)[1].split(">", 1)[0]
    assert 'value="4"' in feld


def test_fuer_acht_statt_vier_werden_zwei_packungen_daraus(client, con):
    """Der ganze Weg über die Oberfläche: Menge verknüpfen, Portionen wählen,
    Korb füllen — und die Meldung sagt, was gerechnet wurde."""
    pid = _mit_gebinde(con, MILCH, "500 g")
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/bearbeiten",
                data={"name": "Sugo", "servings": "4"})
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=500&unit=ml",
                headers=HTMX)

    r = client.post(f"/rezepte/{rid}/korb", data={"portionen": "8"},
                    headers=HTMX)
    assert r.status_code == 200
    assert "für 8 statt 4 Portionen" in r.text
    assert "1000 ml" in r.text and "2 × 500 g" in r.text
    assert [(z["name"], z["qty"]) for z in orders.inhalt(con)] == [(MILCH, 2)]

    # Das Rezept selbst bleibt bei vier.
    assert recipes.rezept(con, rid)["servings"] == 4


def test_der_warenkorb_zeigt_was_gerechnet_wurde(client, con):
    """Regel 6: eine stumme 2 im Mengenfeld erklärt nichts."""
    pid = _mit_gebinde(con, MILCH, "500 g")
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/bearbeiten",
                data={"name": "Sugo", "servings": "4"})
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=1000&unit=ml",
                headers=HTMX)
    client.post(f"/rezepte/{rid}/korb", headers=HTMX)

    korb = client.get("/warenkorb").text
    assert "1000 ml gebraucht" in korb
    assert "2 ×" in korb


def test_die_zutat_des_rezepts_fuehrt_mit_ihrer_menge_in_die_suche(client, con):
    """Der Weg, auf dem eine Menge überhaupt an eine Zutat kommt.

    Ohne ihn müsste jemand „500 ml" von Hand in ein Mengenfeld tippen — und
    in der Praxis bliebe es leer. Dann skalierte nichts, und das Ticket wäre
    eine Zusage ohne Deckung.
    """
    rid = _rid(client)
    con.execute("INSERT INTO recipe_ingredient (recipe_id, pos, raw_name,"
                " name, amount, unit) VALUES (?, 0, ?, ?, ?, ?)",
                (rid, "Tomaten, passierte", "passierte Tomaten", 500.0, "ml"))
    con.commit()

    seite = client.get(f"/rezepte/{rid}").text
    assert "amount=500" in seite and "unit=ml" in seite

    # Und der Treffer legt sie mit ins Rezept.
    treffer = client.get(f"/rezepte/{rid}/suche?q=Milch&amount=500&unit=ml").text
    assert "amount=500" in treffer
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}"
                "&amount=500&unit=ml", headers=HTMX)
    assert recipes.zutaten(con, rid)[0]["amount"] == 500.0


def test_was_nicht_ausrechenbar_ist_steht_als_solches_da(client, con):
    """Regel 4 in der Oberfläche: nicht raten, aber auch nicht schweigen."""
    pid = _mit_gebinde(con, MILCH, "1 kg")
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/bearbeiten",
                data={"name": "Suppe", "servings": "4"})
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=1&unit=Stk",
                headers=HTMX)

    r = client.post(f"/rezepte/{rid}/korb", data={"portionen": "8"},
                    headers=HTMX)
    assert "Nicht ausrechenbar" in r.text
    assert "1 kg" in r.text
    # Und im Korb liegt trotzdem genau eine Packung.
    assert [z["qty"] for z in orders.inhalt(con)] == [1]


def test_die_menge_einer_zutat_laesst_sich_korrigieren(client, con):
    """Wer sich vertippt hat, soll die Zutat nicht löschen und neu suchen
    müssen — und ein leeres Feld nimmt die Menge ganz wieder weg.

    Eine eigene Route und nicht `…/menge`: die benötigte Menge wächst mit den
    Portionen, die Packungszahl nicht. Ein gemeinsames „Menge setzen" baute
    genau die Verwechslung ein, um die es in WB-362 geht.
    """
    pid = _pid(con, MILCH)
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=500&unit=ml",
                headers=HTMX)
    item = recipes.zutaten(con, rid)[0]["id"]

    r = client.post(f"/rezepte/{rid}/zutaten/{item}/bedarf",
                    data={"amount": "0,25", "unit": "l"}, headers=HTMX)
    assert r.status_code == 200
    assert recipes.zutaten(con, rid)[0]["amount"] == 250.0

    client.post(f"/rezepte/{rid}/zutaten/{item}/bedarf",
                data={"amount": "", "unit": "ml"}, headers=HTMX)
    assert recipes.zutaten(con, rid)[0]["amount"] is None
    # Und die Packungszahl bleibt davon unberührt — es sind zwei Grössen.
    assert recipes.zutaten(con, rid)[0]["qty"] == 1


# --------------------------------------------------------------------------
# Die Einheit ist ein Feld und keine versteckte Fracht (WB-375)
#
# Vorher reiste sie als `<input type="hidden">` mit. Ein geleertes Mengenfeld
# löschte sie mit, danach war das Hidden-Feld leer, und beim Neutippen wurde
# aus „500 g" ein „500 Stk" — die Zutat rechnete ab da falsch, still, und auf
# der ganzen Seite gab es kein Feld, mit dem sich das hätte richten lassen.

def _einheitenfeld(text: str) -> str:
    """Das `unit`-Feld AUS DEM BEDARF-FORMULAR.

    Nicht das erste `name="unit"` der Seite: das Suchformular trägt die Menge
    der Rezeptzutat versteckt mit ins Verknüpfen, und das ist ein anderer
    Vorgang — dort ist versteckt richtig, weil nichts daran zu ändern ist.
    """
    stueck = text.split('class="bedarf"', 1)[1].split("</form>", 1)[0]
    return re.search(r'<input[^>]*name="unit"[^>]*>', stueck).group(0)


def test_die_einheit_steht_als_eigenes_feld_auf_der_seite(client, con):
    pid = _pid(con, MILCH)
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=500&unit=ml",
                headers=HTMX)

    feld = _einheitenfeld(client.get(f"/rezepte/{rid}").text)
    assert 'type="hidden"' not in feld, "Einheit wieder versteckt"
    assert 'type="text"' in feld
    assert 'value="ml"' in feld, "das Feld zeigt die Einheit nicht"


def test_menge_leeren_und_neu_eintragen_behaelt_die_einheit(client, con):
    """Der Griff, den WB-362 verspricht — und der vorher eine Einbahnstrasse
    war: „500 g" -> Feld leeren -> „500" -> stand auf „500 Stk"."""
    pid = _pid(con, MILCH)
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=500&unit=ml",
                headers=HTMX)
    item = recipes.zutaten(con, rid)[0]["id"]

    # Feld leeren — die Einheit steht dabei weiter im (jetzt sichtbaren) Feld.
    client.post(f"/rezepte/{rid}/zutaten/{item}/bedarf",
                data={"amount": "", "unit": "ml"}, headers=HTMX)
    z = recipes.zutaten(con, rid)[0]
    assert z["amount"] is None
    assert z["unit"] == "ml", "die Einheit ging mit der Menge verloren"

    # Und neu eintippen ergibt wieder Milliliter, nicht Stück.
    client.post(f"/rezepte/{rid}/zutaten/{item}/bedarf",
                data={"amount": "500", "unit": "ml"}, headers=HTMX)
    z = recipes.zutaten(con, rid)[0]
    assert (z["amount"], z["unit"]) == (500.0, "ml")


def test_ohne_mitgeschickte_einheit_bleibt_die_gespeicherte_stehen(client, con):
    """Ein Aufrufer, der `unit` gar nicht schickt, meint „lass sie stehen" —
    nicht „lösch sie". Das ist der Unterschied zwischen fehlend und leer."""
    pid = _pid(con, MILCH)
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=500&unit=ml",
                headers=HTMX)
    item = recipes.zutaten(con, rid)[0]["id"]

    client.post(f"/rezepte/{rid}/zutaten/{item}/bedarf",
                data={"amount": "250"}, headers=HTMX)
    z = recipes.zutaten(con, rid)[0]
    assert (z["amount"], z["unit"]) == (250.0, "ml")


def test_die_einheit_laesst_sich_ueber_das_feld_aendern(client, con):
    pid = _pid(con, MILCH)
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}&amount=2&unit=Stk",
                headers=HTMX)
    item = recipes.zutaten(con, rid)[0]["id"]

    client.post(f"/rezepte/{rid}/zutaten/{item}/bedarf",
                data={"amount": "1", "unit": "l"}, headers=HTMX)
    z = recipes.zutaten(con, rid)[0]
    assert (z["amount"], z["unit"]) == (1000.0, "ml"), "Liter wurden nicht umgerechnet"


def test_ohne_gespeicherte_einheit_behauptet_das_feld_keine(client, con):
    """Ein leeres Feld ist ehrlich: in der Spalte steht NULL. Ein vorbelegtes
    „Stk" wäre eine Behauptung, die beim nächsten Abschicken wahr würde."""
    pid = _pid(con, MILCH)
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={pid}", headers=HTMX)

    feld = _einheitenfeld(client.get(f"/rezepte/{rid}").text)
    assert 'value=""' in feld


# --------------------------------------------------------------------------
# Die Rezeptliste nennt beide Zahlen (WB-375)

def _rezeptzutaten(con, rid, namen):
    """Trägt eine Zutatenliste ein, wie ein geholtes Rezept sie mitbringt."""
    for pos, name in enumerate(namen):
        con.execute("INSERT INTO recipe_ingredient (recipe_id, pos, raw_name,"
                    " name) VALUES (?, ?, ?, ?)", (rid, pos, name, name))
    con.commit()


def test_ein_geholtes_rezept_meldet_nicht_null_zutaten(client, con):
    """Vorher zählte die Liste nur die verknüpften PRODUKTE — ein Rezept mit
    23 Zutaten stand als „0 Zutaten" da."""
    rid = _rid(client, "Pho Bo")
    _rezeptzutaten(con, rid, ["Rinderbrühe", "Reisnudeln", "Ingwer"])

    text = client.get("/rezepte").text
    assert "0 Zutaten" not in text
    assert "3 Zutaten" in text
    assert "noch nichts verknüpft" in text


def test_die_liste_nennt_die_verknuepften_neben_den_zutaten(client, con):
    rid = _rid(client, "Pho Bo")
    _rezeptzutaten(con, rid, ["Rinderbrühe", "Reisnudeln", "Ingwer"])
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                headers=HTMX)

    text = client.get("/rezepte").text
    assert "3 Zutaten" in text
    assert "1 verknüpft" in text


def test_ohne_zutatenliste_bleibt_die_zahl_der_verknuepften_stehen(client, con):
    """Ein von Hand gebautes Rezept hat keine `recipe_ingredient`-Zeilen. Dann
    sind die verknüpften Produkte die einzigen Zutaten, die es gibt."""
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/zutaten?product_id={_pid(con, MILCH)}",
                headers=HTMX)
    assert "1 Zutat" in client.get("/rezepte").text


def test_die_portionszahl_hat_ein_bezugswort(client, con):
    rid = _rid(client)
    client.post(f"/rezepte/{rid}/bearbeiten",
                data={"name": "Milchreis", "servings": "4"})
    assert "für 4 Portionen" in client.get("/rezepte").text
