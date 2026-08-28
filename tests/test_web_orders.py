"""Tests für Warenkorb-, Bestell- und Pick-Ansicht (WB-324).

Kein Browser, kein Netz — dieselbe Bauart wie `test_web_catalog.py`: die
Oberfläche wird über `fastapi.testclient` als HTTP-Client geprüft, die
Produkte kommen aus der aufgezeichneten Knuspr-Antwort.

Geprüft wird durchgehend am HTTP-Rand und an der Datenbank dahinter: dass eine
Seite einen Knopf zeigt, sagt nichts darüber, ob der Knopf etwas tut. Deshalb
steht hinter jedem Klick eine Abfrage, was danach in der Datenbank steht.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picknick import db, orders
from picknick.scrapers import knuspr
from picknick.web import app as webapp

FIXTURE = Path(__file__).parent / "fixtures" / "knuspr_milch.json"
STIL = Path(webapp.STATIC_DIR) / "stil.css"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HAFER = "Alpro Haferdrink Original VEGAN"

HTMX = {"HX-Request": "true"}


class FakeHTTP:
    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, url):
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


@pytest.fixture
def db_datei(tmp_path):
    pfad = tmp_path / "picknick.db"
    con = db.connect(pfad)
    db.migrate(con)
    knuspr.crawl(con, FakeHTTP([json.loads(FIXTURE.read_text(encoding="utf-8"))]),
                 ["milch"], pause_s=0)
    con.close()
    return pfad


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


# --------------------------------------------------------------------------
# Der „+"-Knopf am Katalog (WB-323 hat ihn bewusst weggelassen)

def test_katalogkachel_hat_einen_einlegeknopf(client, con):
    text = client.get("/katalog").text
    assert f'/katalog/einlegen?product_id={_pid(con, MILCH)}' in text
    assert 'class="plus"' in text


def test_plus_knopf_legt_wirklich_in_den_warenkorb(client, con):
    pid = _pid(con, MILCH)
    r = client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    assert r.status_code == 200

    zeilen = orders.inhalt(con)
    assert len(zeilen) == 1
    assert zeilen[0]["product_id"] == pid
    assert zeilen[0]["qty"] == 1
    assert orders.bestellung(con, zeilen[0]["order_id"])["state"] == "draft"


def test_plus_knopf_meldet_zurueck_und_zaehlt_den_kopf_hoch(client, con):
    pid = _pid(con, MILCH)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    r = client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    assert "2× im Korb" in r.text
    # Die Zahl im Kopf wird per hx-swap-oob mitgeschickt.
    assert 'id="korb-anzahl"' in r.text and 'hx-swap-oob="true"' in r.text
    # Zweimal derselbe Knopf ergibt eine Zeile mit Menge 2, nicht zwei Zeilen.
    assert len(orders.inhalt(con)) == 1


def test_plus_knopf_ohne_htmx_kommt_zum_katalog_zurueck(client, con):
    pid = _pid(con, HAFER)
    r = client.post(f"/katalog/einlegen?product_id={pid}",
                    headers={"referer": "http://testserver/katalog?q=hafer"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/katalog?q=hafer"
    assert orders.inhalt(con)[0]["product_id"] == pid


def test_plus_knopf_nimmt_keine_erfundene_produkt_id(client, con):
    assert client.post("/katalog/einlegen?product_id=999999",
                       headers=HTMX).status_code == 404
    assert client.post("/katalog/einlegen?product_id=keine",
                       headers=HTMX).status_code == 404
    assert orders.inhalt(con) == []


def test_kopf_zeigt_die_zahl_im_korb(client, con):
    assert 'id="korb-anzahl"' in client.get("/katalog").text
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    kopf = client.get("/katalog").text.split('id="korb-anzahl"', 1)[1][:80]
    assert ">1<" in kopf


def test_ein_blick_in_den_katalog_legt_keinen_warenkorb_an(client, con):
    client.get("/katalog")
    client.get("/warenkorb")
    assert con.execute("SELECT count(*) n FROM orders").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Warenkorbansicht

def test_warenkorb_zeigt_die_zeilen(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    text = client.get("/warenkorb").text
    assert MILCH in text
    assert "Bestellung abschicken" in text


def test_leerer_warenkorb_sagt_es_und_bietet_kein_abschicken(client):
    text = client.get("/warenkorb").text
    assert "Der Warenkorb ist leer." in text
    assert "Bestellung abschicken" not in text


def test_menge_aendern_ueber_die_oberflaeche(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    item = orders.inhalt(con)[0]["id"]

    r = client.post(f"/warenkorb/posten/{item}/menge?qty=3", headers=HTMX)
    assert r.status_code == 200
    assert "<html" not in r.text.lower()          # nur das Bruchstück
    assert orders.inhalt(con)[0]["qty"] == 3

    client.post(f"/warenkorb/posten/{item}/menge?qty=0", headers=HTMX)
    assert orders.inhalt(con) == []


def test_laden_waehlen_ueber_die_oberflaeche(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    item = orders.inhalt(con)[0]["id"]
    r = client.post(f"/warenkorb/posten/{item}/laden", data={"store": "lidl"},
                    headers=HTMX)
    assert r.status_code == 200
    assert orders.inhalt(con)[0]["store"] == "lidl"


def test_unbekannter_laden_bleibt_ohne_wirkung(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    item = orders.inhalt(con)[0]["id"]
    r = client.post(f"/warenkorb/posten/{item}/laden", data={"store": "aldi"},
                    headers=HTMX)
    assert r.status_code == 200
    assert "ist kein Laden" in r.text
    assert orders.inhalt(con)[0]["store"] == "egal"


def test_zeile_loeschen_ueber_die_oberflaeche(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    item = orders.inhalt(con)[0]["id"]
    client.post(f"/warenkorb/posten/{item}/loeschen", headers=HTMX)
    assert orders.inhalt(con) == []


def test_die_auswahl_zeigt_die_vorbelegung_der_letzten_wahl(client, con):
    """Spec 4: der Laden wird aus der letzten Wahl für dasselbe Produkt gefüllt."""
    pid = _pid(con, MILCH)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    item = orders.inhalt(con)[0]["id"]
    client.post(f"/warenkorb/posten/{item}/laden", data={"store": "rewe"},
                headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)

    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    assert orders.inhalt(con)[0]["store"] == "rewe"
    zeile = client.get("/warenkorb").text.split('name="store"', 1)[1][:400]
    assert re.search(r'value="rewe"\s+selected', zeile)


# --------------------------------------------------------------------------
# Freitext — gleichwertig, überall (Spec 4)

def test_warenkorb_hat_ein_feld_fuer_freitext(client):
    assert 'name="free_text"' in client.get("/warenkorb").text


def test_freitext_einlegen_ueber_das_feld(client, con):
    r = client.post("/warenkorb/einlegen",
                    data={"free_text": "Brötchen vom Bäcker"}, headers=HTMX)
    assert r.status_code == 200
    assert "Brötchen vom Bäcker" in r.text
    zeile = orders.inhalt(con)[0]
    assert zeile["free_text"] == "Brötchen vom Bäcker"
    assert zeile["product_id"] is None


def test_leerer_freitext_ergibt_keine_zeile_sondern_eine_ansage(client, con):
    r = client.post("/warenkorb/einlegen", data={"free_text": "   "},
                    headers=HTMX)
    assert r.status_code == 200
    assert "leeres Feld" in r.text
    assert orders.inhalt(con) == []


def test_freitext_laeuft_ueber_die_oberflaeche_vollstaendig_durch(client, con):
    """Einlegen, abschicken, in der Pick-Ansicht abhaken — alles per HTTP."""
    client.post("/warenkorb/einlegen", data={"free_text": "Klopapier"},
                headers=HTMX)
    item = orders.inhalt(con)[0]["id"]
    client.post(f"/warenkorb/posten/{item}/laden", data={"store": "lidl"},
                headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)

    b = orders.bestellungen(con, "offen")[0]
    assert b["state"] == "offen"

    seite = client.get("/pick").text
    assert "Klopapier" in seite and "Lidl" in seite

    r = client.post(f"/pick/{b['id']}/posten/{item}?gepickt=1", headers=HTMX)
    assert r.status_code == 200
    assert "erledigt" in r.text
    assert orders.bestellung(con, b["id"])["state"] == "erledigt"


# --------------------------------------------------------------------------
# Abschicken

def test_abschicken_wechselt_den_zustand_und_leitet_weiter(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    korb = orders.inhalt(con)[0]["order_id"]

    r = client.post("/warenkorb/abschicken", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/bestellungen")

    b = orders.bestellung(con, korb)
    assert b["state"] == "offen"
    assert b["submitted_at"]
    assert orders.inhalt(con) == []          # der nächste Korb fängt leer an


def test_abschicken_mit_leerem_korb_sagt_es_statt_zu_stolpern(client, con):
    r = client.post("/warenkorb/abschicken")
    assert r.status_code == 200
    assert "leer" in r.text
    assert orders.bestellungen(con) == []


def test_abschicken_per_htmx_schickt_den_browser_weiter(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    r = client.post("/warenkorb/abschicken", headers=HTMX)
    # Ein 303 würde HTMX die ganze Seite in den Korb hineintauschen.
    assert r.status_code == 204
    assert r.headers["HX-Redirect"].startswith("/bestellungen")


# --------------------------------------------------------------------------
# Bestellübersicht: offene oben, erledigte darunter (Spec 9)

def test_uebersicht_stellt_offene_ueber_erledigte(client, con):
    client.post("/warenkorb/einlegen", data={"free_text": "alt"}, headers=HTMX)
    alt_item = orders.inhalt(con)[0]["id"]
    client.post("/warenkorb/abschicken", follow_redirects=False)
    alt = orders.bestellungen(con, "offen")[0]["id"]
    client.post(f"/pick/{alt}/posten/{alt_item}?gepickt=1", headers=HTMX)

    client.post("/warenkorb/einlegen", data={"free_text": "neu"}, headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    neu = orders.bestellungen(con, "offen")[0]["id"]

    text = client.get("/bestellungen").text
    assert text.index(f'id="b{neu}"') < text.index(f'id="b{alt}"')
    assert "alt" in text and "neu" in text


def test_leere_uebersicht_sagt_es(client):
    assert "Noch nichts abgeschickt." in client.get("/bestellungen").text


# --------------------------------------------------------------------------
# Pick-Ansicht (Spec 9)

@pytest.fixture
def offene_bestellung(client, con):
    """Milch bei Rewe, Haferdrink bei Lidl, Klopapier egal wo."""
    for name, laden in ((MILCH, "rewe"), (HAFER, "lidl")):
        client.post(f"/katalog/einlegen?product_id={_pid(con, name)}",
                    headers=HTMX)
        item = orders.inhalt(con)[-1]["id"]
        client.post(f"/warenkorb/posten/{item}/laden", data={"store": laden},
                    headers=HTMX)
    client.post("/warenkorb/einlegen", data={"free_text": "Klopapier"},
                headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    return orders.bestellungen(con, "offen")[0]["id"]


def test_pick_ansicht_gruppiert_nach_laden(client, offene_bestellung):
    text = client.get("/pick").text
    assert text.index("Rewe") < text.index("Lidl") < text.index("Egal wo")
    # Jede Sache steht unter ihrem Laden und nicht irgendwo.
    rewe = text[text.index("Rewe"):text.index("Lidl")]
    assert MILCH in rewe and HAFER not in rewe
    egal = text[text.index("Egal wo"):]
    assert "Klopapier" in egal


def test_pick_ansicht_hat_grosse_checkboxen_mit_bild(client, con,
                                                     offene_bestellung, bild_dir):
    row = con.execute("SELECT id, image_path FROM product WHERE name = ?",
                      (MILCH,)).fetchone()
    (bild_dir / Path(row["image_path"]).name).write_bytes(b"\xff\xd8\xff-kein-jpeg")

    text = client.get("/pick").text
    assert 'type="checkbox"' in text
    assert f'src="/bild/{row["id"]}"' in text

    # Tap-Ziel: das ist im Laden der Unterschied zwischen benutzbar und nicht.
    stil = STIL.read_text(encoding="utf-8")
    block = stil.split(".haken {", 1)[1].split("}", 1)[0]
    assert int(re.search(r"min-height:\s*(\d+)px", block).group(1)) >= 44
    kasten = stil.split('.haken input[type="checkbox"] {', 1)[1].split("}", 1)[0]
    assert int(re.search(r"width:\s*(\d+)px", kasten).group(1)) >= 32


def test_abhaken_setzt_den_haken_und_zeigt_den_stand(client, con,
                                                     offene_bestellung):
    zeilen = orders.posten(con, offene_bestellung)
    r = client.post(
        f"/pick/{offene_bestellung}/posten/{zeilen[0]['id']}?gepickt=1",
        headers=HTMX)
    assert r.status_code == 200
    assert "Noch 2 zu holen." in r.text
    assert orders.posten(con, offene_bestellung)[0]["picked_at"]
    assert orders.bestellung(con, offene_bestellung)["state"] == "offen"


def test_alles_abgehakt_macht_die_bestellung_erledigt(client, con,
                                                      offene_bestellung):
    for zeile in orders.posten(con, offene_bestellung):
        r = client.post(
            f"/pick/{offene_bestellung}/posten/{zeile['id']}?gepickt=1",
            headers=HTMX)
    assert "Alles abgehakt" in r.text
    b = orders.bestellung(con, offene_bestellung)
    assert b["state"] == "erledigt"
    assert b["done_at"]
    # Und die Pick-Ansicht macht danach nicht mehr mit dieser Bestellung auf.
    assert "keine Bestellung offen" in client.get("/pick").text


def test_haken_wieder_wegnehmen_oeffnet_die_bestellung(client, con,
                                                       offene_bestellung):
    zeilen = orders.posten(con, offene_bestellung)
    for zeile in zeilen:
        client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}?gepickt=1",
                    headers=HTMX)
    r = client.post(
        f"/pick/{offene_bestellung}/posten/{zeilen[0]['id']}?gepickt=0",
        headers=HTMX)
    assert "Noch 1 zu holen." in r.text
    assert orders.bestellung(con, offene_bestellung)["state"] == "offen"


def test_abhaken_ohne_htmx_fuehrt_zurueck_zur_liste(client, con,
                                                    offene_bestellung):
    zeile = orders.posten(con, offene_bestellung)[0]
    r = client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}?gepickt=1",
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pick/{offene_bestellung}"


def test_unbekannte_bestellung_und_posten_ergeben_404(client, offene_bestellung):
    assert client.get("/pick/999999").status_code == 404
    assert client.post(f"/pick/{offene_bestellung}/posten/999999?gepickt=1",
                       headers=HTMX).status_code == 404


def test_ohne_offene_bestellung_bleibt_der_weg_zum_katalog(client):
    text = client.get("/pick").text
    assert "keine Bestellung offen" in text
    assert "/katalog" in text


def test_weitere_offene_bestellungen_stehen_zur_auswahl(client, con,
                                                        offene_bestellung):
    client.post("/warenkorb/einlegen", data={"free_text": "Blumen"},
                headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    zweite = [b["id"] for b in orders.offene(con) if b["id"] != offene_bestellung][0]

    text = client.get("/pick").text
    assert "Weitere offene Bestellungen" in text
    assert f'/pick/{zweite}' in text


# --------------------------------------------------------------------------
# Nichts Fremdes im HTML (dieselbe Regel wie in WB-323)

@pytest.mark.parametrize("pfad", ["/warenkorb", "/bestellungen", "/pick"])
def test_kein_cdn_verweis_in_den_neuen_ansichten(client, offene_bestellung, pfad):
    text = client.get(pfad).text.lower()
    assert "unpkg" not in text
    assert "cdn." not in text
    assert "//" not in re.sub(r"<!--.*?-->", "", text, flags=re.S).replace(
        "<!doctype html>", "")
