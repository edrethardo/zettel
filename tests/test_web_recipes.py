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

from picknick import db, orders, recipes
from picknick.web import app as webapp

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

def test_rezepte_haengen_in_der_navigation(client):
    assert '<a href="/rezepte">Rezepte</a>' in client.get("/katalog").text


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
    assert r.status_code == 303 and r.headers["location"] == "/rezepte"
    assert recipes.rezepte(con) == []
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
        client.post(f"/pick/{b}/posten/{p['id']}?gepickt=1", headers=HTMX)
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
