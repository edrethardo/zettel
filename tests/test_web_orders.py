"""Tests für Warenkorb-, Bestell- und Pick-Ansicht (WB-324).

Kein Browser, kein Netz — dieselbe Bauart wie `test_web_catalog.py`: die
Oberfläche wird über `fastapi.testclient` als HTTP-Client geprüft, die
Produkte kommen aus der aufgezeichneten Knuspr-Antwort.

Geprüft wird durchgehend am HTTP-Rand und an der Datenbank dahinter: dass eine
Seite einen Knopf zeigt, sagt nichts darüber, ob der Knopf etwas tut. Deshalb
steht hinter jedem Klick eine Abfrage, was danach in der Datenbank steht.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import db, orders
from zettel.web import app as webapp

STIL = Path(webapp.STATIC_DIR) / "stil.css"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HAFER = "Alpro Haferdrink Original VEGAN"

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


def test_der_warenkorb_nennt_die_summe_vor_dem_bestellknopf(client, con):
    pid = _pid(con, MILCH)                       # 1,19 €
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    client.post("/warenkorb/einlegen", data={"free_text": "Blumen"}, headers=HTMX)
    text = client.get("/warenkorb").text
    summe = text.split('class="summe"', 1)[1].split("</p>", 1)[0]
    assert "2,38 €" in summe
    assert "1 Posten ohne Preis" in summe
    assert text.index('class="summe"') < text.index("Bestellung abschicken")


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
# Die Zahl im Kopf (WB-372)
#
# Sie stand an jeder Korb-Aktion DIESER Seite still: „+", „−", Löschen und das
# Freitext-Feld tauschten den Korb und liessen den Kopf behaupten, es lägen
# noch drei Sachen drin. `_eingelegt.html` an der Katalog-Kachel machte es von
# Anfang an richtig — ausgerechnet der Korb selbst nicht.


def _kopfzahl(text):
    """Die Zahl aus dem out-of-band getauschten `#korb-anzahl`, oder None."""
    treffer = re.search(r'id="korb-anzahl"[^>]*>(\d+)<', text)
    return None if treffer is None else int(treffer.group(1))


def test_die_zahl_im_kopf_kommt_bei_jeder_korbaktion_mit(client, con):
    """+, −, Löschen und Freitext — jede Antwort trägt den Kopf nach.

    Gezählt werden ZEILEN und keine Stückzahlen (`orders.korb_anzahl`): „+"
    und „−" an einer Zeile ändern die Zahl also nicht, und genau deshalb steht
    sie hier trotzdem in jeder Antwort. Vorher fehlte sie in allen vier, und
    der Kopf behauptete nach einem Löschen weiter, es läge etwas im Korb.
    """
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    item = orders.inhalt(con)[0]["id"]

    mehr = client.post(f"/warenkorb/posten/{item}/menge?qty=2",
                       headers=HTMX).text
    assert _kopfzahl(mehr) == 1
    weniger = client.post(f"/warenkorb/posten/{item}/menge?qty=1",
                          headers=HTMX).text
    assert _kopfzahl(weniger) == 1

    freitext = client.post("/warenkorb/einlegen",
                           data={"free_text": "Brötchen vom Bäcker"},
                           headers=HTMX).text
    assert _kopfzahl(freitext) == 2

    # „−" bis auf null nimmt die Zeile heraus — dann MUSS der Kopf mitgehen.
    raus = client.post(f"/warenkorb/posten/{item}/menge?qty=0",
                       headers=HTMX).text
    assert _kopfzahl(raus) == 1

    brot = orders.inhalt(con)[0]["id"]
    leer = client.post(f"/warenkorb/posten/{brot}/loeschen", headers=HTMX).text
    assert _kopfzahl(leer) == 0
    assert orders.inhalt(con) == []


def test_die_zahl_im_kopf_wird_nur_out_of_band_getauscht(client, con):
    """Und steht nicht als zweite Zahl mitten im Korb.

    Sie gehört in den Kopf. Im Vollbild darf dieses Stück sie also gerade
    NICHT mitbringen — sonst stünde sie zweimal auf der Seite, einmal oben und
    einmal irgendwo zwischen den Zeilen.
    """
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    seite = client.get("/warenkorb").text
    assert seite.count('id="korb-anzahl"') == 1
    assert 'hx-swap-oob' not in seite.split('id="korb-anzahl"')[1][:60]

    stueck = client.post("/warenkorb/einlegen",
                         data={"free_text": "Salz"}, headers=HTMX).text
    assert stueck.count('id="korb-anzahl"') == 1
    assert 'hx-swap-oob="true"' in stueck


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

    r = client.post(f"/pick/{b['id']}/posten/{item}?stand=gepickt", headers=HTMX)
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
    client.post(f"/pick/{alt}/posten/{alt_item}?stand=gepickt", headers=HTMX)

    client.post("/warenkorb/einlegen", data={"free_text": "neu"}, headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    neu = orders.bestellungen(con, "offen")[0]["id"]

    text = client.get("/bestellungen").text
    assert text.index(f'id="b{neu}"') < text.index(f'id="b{alt}"')
    assert "alt" in text and "neu" in text


def test_leere_uebersicht_sagt_es(client):
    assert "Noch nichts abgeschickt." in client.get("/bestellungen").text


def test_die_bestellkarte_zeigt_die_ersten_posten_als_zeilen(client, con):
    namen = [r["name"] for r in con.execute(
        "SELECT name FROM product ORDER BY id LIMIT 6")]
    for n in namen:
        client.post(f"/katalog/einlegen?product_id={_pid(con, n)}", headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    text = client.get("/bestellungen").text
    karte = text.split('<ul class="posten"', 1)[1].split("</ul>", 1)[0]
    zeilen = re.findall(r"<li>(.*?)</li>", karte, re.S)
    assert len(zeilen) == 4
    assert "und 2 weitere" in text
    assert ", ".join(namen) not in text


def test_der_rest_auf_der_karte_wird_gebeugt_wie_er_gezaehlt_wird(client, con):
    """„und 1 weitere" ist kein Deutsch — ein Rest ist einer.

    Dieselbe Sorgfalt wie bei „liegt/liegen" in `mengen.py`: die Zahl steht
    daneben, also fällt die falsche Endung sofort auf.
    """
    namen = [r["name"] for r in con.execute(
        "SELECT name FROM product ORDER BY id LIMIT 5")]
    for n in namen:
        client.post(f"/katalog/einlegen?product_id={_pid(con, n)}", headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    text = client.get("/bestellungen").text
    assert "und 1 weiterer" in text
    assert "und 1 weitere<" not in text


def test_nach_dem_abschicken_steht_eine_quittung(client, con):
    pid = _pid(con, MILCH)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    r = client.post("/warenkorb/abschicken", follow_redirects=False)
    assert r.status_code == 303
    ziel = r.headers["location"]
    assert ziel.startswith("/bestellungen?fertig=")
    text = client.get(ziel).text
    quittung = text.split('class="fertig"', 1)[1].split("</p>", 1)[0]
    assert "Abgeschickt" in quittung
    assert "1 Posten" in quittung
    assert "2,38 €" in quittung
    assert "Pick-Liste" in quittung
    # Ohne den Parameter — etwa beim zweiten Aufruf — keine Quittung.
    assert 'class="fertig"' not in client.get("/bestellungen").text


def _quittung(client, ziel):
    """Der Quittungsabsatz unter `ziel` — oder None, wenn keiner dasteht."""
    text = client.get(ziel).text
    if 'class="fertig"' not in text:
        return None
    return text.split('class="fertig"', 1)[1].split("</p>", 1)[0]


def test_die_quittung_verschweigt_nicht_was_keinen_preis_hat(client, con):
    """Was der Korb vor dem Knopf sagt, sagt die Quittung danach auch.

    Eine Bestellung nur aus Freitext hätte sonst „zusammen etwa 0,00 €"
    gemeldet — genau die glatte Null, gegen die `euro()` seinen Strich setzt.
    """
    client.post("/warenkorb/einlegen", data={"free_text": "Blumen"}, headers=HTMX)
    r = client.post("/warenkorb/abschicken", follow_redirects=False)
    nur_freitext = _quittung(client, r.headers["location"])
    assert "0,00 €" not in nur_freitext
    assert "1 Posten ohne Preis" in nur_freitext

    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    client.post("/warenkorb/einlegen", data={"free_text": "Blumen"}, headers=HTMX)
    r = client.post("/warenkorb/abschicken", follow_redirects=False)
    gemischt = _quittung(client, r.headers["location"])
    assert "1,19 €" in gemischt
    assert "1 Posten ohne Preis" in gemischt


def test_eine_erledigte_bestellung_bekommt_keine_quittung_mehr(client, con):
    """Die URL überlebt Reload, Zurück und das Weiterreichen im Chat.

    „Steht jetzt auf der Pick-Liste" über einen gestern erledigten Einkauf
    wäre eine grüne Lüge.
    """
    client.post("/warenkorb/einlegen", data={"free_text": "Blumen"}, headers=HTMX)
    item = orders.inhalt(con)[0]["id"]
    ziel = client.post("/warenkorb/abschicken",
                       follow_redirects=False).headers["location"]
    assert _quittung(client, ziel) is not None

    b = orders.bestellungen(con, "offen")[0]["id"]
    client.post(f"/pick/{b}/posten/{item}?stand=gepickt", headers=HTMX)
    assert orders.bestellung(con, b)["state"] == "erledigt"
    assert _quittung(client, ziel) is None


def test_eine_kaputte_fertig_id_laesst_die_uebersicht_stehen(client):
    """`?fertig=abc` gab 422 — die ganze Übersicht war damit unerreichbar.

    Eine unbekannte ID gibt schon jetzt still keine Quittung; eine kaputte
    soll sich genauso verhalten und nicht die Seite mitnehmen.
    """
    r = client.get("/bestellungen?fertig=abc")
    assert r.status_code == 200
    assert 'class="fertig"' not in r.text


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
        f"/pick/{offene_bestellung}/posten/{zeilen[0]['id']}?stand=gepickt",
        headers=HTMX)
    assert r.status_code == 200
    assert "Noch 2 zu holen." in r.text
    assert orders.posten(con, offene_bestellung)[0]["picked_at"]
    assert orders.bestellung(con, offene_bestellung)["state"] == "offen"


def test_alles_abgehakt_macht_die_bestellung_erledigt(client, con,
                                                      offene_bestellung):
    for zeile in orders.posten(con, offene_bestellung):
        r = client.post(
            f"/pick/{offene_bestellung}/posten/{zeile['id']}?stand=gepickt",
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
        client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}?stand=gepickt",
                    headers=HTMX)
    r = client.post(
        f"/pick/{offene_bestellung}/posten/{zeilen[0]['id']}?stand=offen",
        headers=HTMX)
    assert "Noch 1 zu holen." in r.text
    assert orders.bestellung(con, offene_bestellung)["state"] == "offen"


def test_abhaken_ohne_htmx_fuehrt_zurueck_zur_liste(client, con,
                                                    offene_bestellung):
    zeile = orders.posten(con, offene_bestellung)[0]
    r = client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}?stand=gepickt",
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pick/{offene_bestellung}"


def test_unbekannte_bestellung_und_posten_ergeben_404(client, offene_bestellung):
    assert client.get("/pick/999999").status_code == 404
    assert client.post(f"/pick/{offene_bestellung}/posten/999999?stand=gepickt",
                       headers=HTMX).status_code == 404


# --------------------------------------------------------------------------
# „Gab's nicht" (WB-373): der Ausweg vor dem leeren Regal
#
# Der Name steht im Laden auf einem Telefon. „Ausverkauft" wäre eine
# Behauptung über den Laden, die er im Vorbeigehen nicht prüfen kann —
# „gab's nicht" ist das, was er hinterher sagt.

def test_gabs_nicht_steht_an_jeder_pickzeile(client, con, offene_bestellung):
    zeile = _zeile(client.get("/pick").text, MILCH)
    item = orders.posten(con, offene_bestellung)[0]["id"]
    assert "gab's nicht" in zeile
    assert f'/pick/{offene_bestellung}/posten/{item}?stand=fehlt' in zeile


def test_ein_nicht_bekommener_posten_schliesst_die_bestellung_ab(
        client, con, offene_bestellung):
    zeilen = orders.posten(con, offene_bestellung)
    for zeile in zeilen[:-1]:
        client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}"
                    "?stand=gepickt", headers=HTMX)
    r = client.post(f"/pick/{offene_bestellung}/posten/{zeilen[-1]['id']}"
                    "?stand=fehlt", headers=HTMX)

    assert r.status_code == 200
    assert orders.bestellung(con, offene_bestellung)["state"] == "erledigt"
    # Und die Meldung behauptet NICHT, es sei alles abgehakt gewesen.
    assert "Alles abgehakt" not in r.text
    assert "2 geholt" in r.text and "1 gab's nicht" in r.text


def test_gabs_nicht_laesst_sich_im_laden_zuruecknehmen(client, con,
                                                       offene_bestellung):
    zeilen = orders.posten(con, offene_bestellung)
    for zeile in zeilen[:-1]:
        client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}"
                    "?stand=gepickt", headers=HTMX)
    client.post(f"/pick/{offene_bestellung}/posten/{zeilen[-1]['id']}"
                "?stand=fehlt", headers=HTMX)

    r = client.post(f"/pick/{offene_bestellung}/posten/{zeilen[-1]['id']}"
                    "?stand=offen", headers=HTMX)
    assert "Noch 1 zu holen." in r.text
    assert orders.bestellung(con, offene_bestellung)["state"] == "offen"
    assert orders.posten(con, offene_bestellung)[-1]["stand"] == "offen"


def test_die_historie_zeigt_beide_zahlen(client, con, offene_bestellung):
    """„offen seit drei Tagen" ist keine ehrliche Bestellung, das hier schon."""
    zeilen = orders.posten(con, offene_bestellung)
    for zeile in zeilen[:-1]:
        client.post(f"/pick/{offene_bestellung}/posten/{zeile['id']}"
                    "?stand=gepickt", headers=HTMX)
    client.post(f"/pick/{offene_bestellung}/posten/{zeilen[-1]['id']}"
                "?stand=fehlt", headers=HTMX)

    text = client.get("/bestellungen").text
    assert "2 geholt, 1 gab's nicht" in text
    assert "3 Posten" not in text


def test_ein_erfundener_stand_wird_abgelehnt(client, con, offene_bestellung):
    """400 und nicht 404: der Posten existiert, der Stand nicht."""
    item = orders.posten(con, offene_bestellung)[0]["id"]
    r = client.post(f"/pick/{offene_bestellung}/posten/{item}"
                    "?stand=ausverkauft", headers=HTMX)
    assert r.status_code == 400
    assert orders.posten(con, offene_bestellung)[0]["stand"] == "offen"


# --------------------------------------------------------------------------
# Ein Haken, der nicht ankommt, darf nicht aussehen wie einer, der ankam

def test_der_haken_gibt_rueckmeldung_und_sperrt_sich_solange(
        client, offene_bestellung):
    zeile = _zeile(client.get("/pick").text, MILCH)
    assert 'hx-indicator="#pick-laeuft"' in zeile
    assert 'hx-disabled-elt="find input"' in zeile


def test_ein_gescheiterter_haken_bleibt_nicht_stumm(client, offene_bestellung):
    """Ohne diese drei Stücke tauscht HTMX bei einem Fehler nichts, und die
    Checkbox bleibt optisch gesetzt, obwohl nichts gespeichert wurde."""
    text = client.get("/pick").text
    assert 'id="pick-fehler"' in text and "Nicht gespeichert" in text
    # Beide Fehlerwege: der Server sagt Nein, und der Server sagt gar nichts.
    assert "htmx:responseError" in text and "htmx:sendError" in text
    # Und das Kästchen springt zurück, sonst lügt es weiter.
    assert "reset" in text
    # Die Meldung steht AUSSERHALB der Liste, die bei jedem Haken getauscht
    # wird — sonst verschwände sie mit der Antwort, auf die sie wartet.
    assert text.index('id="pickliste"') < text.index('id="pick-fehler"')
    assert 'id="pick-fehler"' not in text.split('id="pickliste"', 1)[1] \
        .split("</div>", 1)[0]


# --------------------------------------------------------------------------
# Die Mengenrechnung gehört vor das Regal (WB-362 gerechnet, WB-373 gezeigt)

def _bedarf(con, order_id, **felder):
    """Setzt Bedarf und Packungszahl am ersten Posten (der Milch)."""
    item = orders.posten(con, order_id)[0]
    satz = ", ".join(f"{k} = ?" for k in felder)
    con.execute(f"UPDATE order_item SET {satz} WHERE id = ?",
                (*felder.values(), item["id"]))
    con.commit()
    return item["id"]


def test_die_gebrauchte_menge_ist_die_hauptangabe_der_pickzeile(
        client, con, offene_bestellung):
    """Vor dem Regal zählt, wie viel gebraucht wird — was auf der Packung
    steht, liest man dort ab (WB-381).

    Bis dahin besetzte die Packungsgrösse das Mengenfeld und die gebrauchte
    Menge stand klein darunter: die wichtigere der beiden Zahlen war die
    kleinere.
    """
    _bedarf(con, offene_bestellung, need_amount=1000, need_unit="ml")

    zeile = _zeile(client.get("/pick").text, MILCH)
    assert '<span class="menge">1000 ml gebraucht</span>' in zeile
    # Die Packungsgrösse steht daneben — und nicht an ihrer Stelle.
    assert '<span class="gebinde">dafür 1 × 1 l</span>' in zeile
    assert '<span class="menge">1 l' not in zeile


def test_ohne_gebrauchte_menge_behauptet_die_pickzeile_keine(
        client, offene_bestellung):
    """Ein von Hand eingelegter Posten HAT keine benötigte Menge — und alle
    13 Posten der echten Datenbank sind solche (WB-381).

    Dann bleibt das Feld der Hauptangabe LEER. Eine Packungsgrösse an dieser
    Stelle wäre eine erfundene Bedarfsmenge, und die ist im Laden schlimmer
    als gar keine.
    """
    zeile = _zeile(client.get("/pick").text, MILCH)
    assert "gebraucht" not in zeile
    assert '<span class="menge">' not in zeile
    # Was es gibt, steht als das da, was es ist: eine Packung zu 1 l.
    assert '<span class="gebinde">1 × 1 l</span>' in zeile


def test_die_packungszahl_steht_an_der_packung_und_nicht_am_namen(
        client, con, offene_bestellung):
    """„2× Zwiebeln Gelb, Netz / 1 kg" las sich wie zwei Kilo Zwiebeln.

    Verstecken wäre falsch — im Laden ist die Packungszahl das, was in den
    Wagen wandert. Sie steht deshalb dort, wo sie wirklich multipliziert:
    an der Packungsgrösse.
    """
    _bedarf(con, offene_bestellung, qty=2)

    zeile = _zeile(client.get("/pick").text, MILCH)
    assert f'<span class="name">{MILCH}</span>' in zeile
    assert "2×" not in zeile
    assert '<span class="gebinde">2 × 1 l</span>' in zeile


def test_eine_nicht_ausrechenbare_einheit_liest_sich_nicht_wie_ein_fehler(
        client, con, offene_bestellung):
    """„6 Stange gebraucht" gegen „1 l" ist die bekannte Lücke aus WB-362:
    die Menge steht da, die Packungszahl ist 1 — und ohne ein Wort dazu
    sähe die Zeile aus, als hätte sich jemand verrechnet."""
    _bedarf(con, offene_bestellung, need_amount=6, need_unit="stange")

    zeile = _zeile(client.get("/pick").text, MILCH)
    assert '<span class="menge">6 Stange gebraucht</span>' in zeile
    assert "1 × 1 l — nicht ausrechenbar" in zeile
    # „dafür" wäre die Behauptung, die 1 stamme aus den 6 Stangen.
    assert "dafür" not in zeile


def test_eine_von_hand_gesetzte_packungszahl_sagt_sich_weiter_an(
        client, con, offene_bestellung):
    """Im Korb liegt etwas anderes, als die Rechnung verlangt (WB-362) — im
    Laden stünde sonst jemand vor der falschen Zahl."""
    _bedarf(con, offene_bestellung, need_amount=1000, need_unit="ml", qty=3)

    zeile = _zeile(client.get("/pick").text, MILCH)
    assert '<span class="gebinde">3 × 1 l</span>' in zeile
    assert "Im Korb liegen 3 — von Hand dazugelegt." in zeile


def test_die_annahme_bleibt_an_der_pickzeile_stehen(client, con,
                                                    offene_bestellung):
    """1 ml als 1 g zu rechnen stimmt für Wässriges und nicht für Öl. Die
    Annahme darf gesehen und bestritten werden (WB-362)."""
    _bedarf(con, offene_bestellung, need_amount=1000, need_unit="g")

    zeile = _zeile(client.get("/pick").text, MILCH)
    assert '<span class="menge">1000 g gebraucht</span>' in zeile
    assert "1 ml als 1 g gerechnet" in zeile


def test_ein_freitextposten_bleibt_wie_er_war(client, offene_bestellung):
    """Er hat kein Produkt und damit keine Packungsgrösse — nur seine Zahl,
    und die darf nicht verschwinden, bloss weil es nichts zu multiplizieren
    gibt.

    Dieser hier wurde von Hand eingetippt und hat nie eine Menge gehabt.
    Dann steht auch keine da: „gebraucht" ohne Zahl wäre eine Behauptung
    über ein Rezept, das es nicht gibt. Der Fall MIT Menge steht darunter.
    """
    zeile = _zeile(client.get("/pick").text, "Klopapier")
    assert "1× Klopapier" in zeile
    assert "Freitext" in zeile
    assert "gebraucht" not in zeile


def test_ein_freitextposten_mit_menge_zeigt_sie_im_laden(client, con,
                                                         offene_bestellung):
    """„Sternanis, 3 Stk gebraucht" steht auf der Einkaufsliste (WB-385).

    Die Zeile dafür steht seit WB-381 (`bedarf_text` als Hauptangabe,
    `gebinde_text` als Nebenangabe) — es kam nur nie eine Menge an, weil
    `korb.einlegen()` sie beim Freitext verwarf. Erfunden wird dabei nichts:
    an der Stelle der Packungsangabe steht weiterhin „Freitext" und keine
    Packungszahl, die es nicht gibt.
    """
    freitext = [p for p in orders.posten(con, offene_bestellung)
                if p["ist_freitext"]][0]
    con.execute("UPDATE order_item SET need_amount = 3, need_unit = 'Stk'"
                " WHERE id = ?", (freitext["id"],))
    con.commit()

    zeile = _zeile(client.get("/pick").text, "Klopapier")
    assert '<span class="menge">3 Stk gebraucht</span>' in zeile
    assert '<span class="gebinde">Freitext</span>' in zeile
    # Keine erfundene Packungsgrösse und kein Mangel, den es nicht gibt.
    assert "1 ×" not in zeile
    assert "Packungsgrösse" not in zeile


def test_die_hauptangabe_ist_im_stil_auch_die_hauptangabe(client):
    """Eine Rangfolge, die nur in der Vorlage steht, ist keine.

    Ohne diese Regeln stünden beide Angaben im selben gedämpften Ton, und
    „welche der beiden Zahlen gilt" wäre wieder offen.
    """
    stil = STIL.read_text(encoding="utf-8")
    assert ".pickzeile .menge {" in stil
    assert ".pickzeile .gebinde {" in stil
    # Und die Packungsangabe rückt zurück, sobald eine gebrauchte Menge
    # über ihr steht — steht sie allein, trägt sie das Feld.
    assert ".pickzeile .menge + .gebinde" in stil


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


# --------------------------------------------------------------------------
# Ausgemusterte Produkte sind auch im Korb und im Laden zu sehen (WB-335)
#
# Die Rezeptansicht sagt es an vier Stellen; ab dem Warenkorb war die
# Information bisher weg. Der Schaden fällt nicht am Bildschirm an, sondern
# vor dem Regal: jemand sucht ein Produkt, das seit dem letzten Crawl
# `active = 0` ist (Spec 5.3), und findet es nicht.

def _ausmustern(con, name):
    con.execute("UPDATE product SET active = 0 WHERE name = ?", (name,))
    con.commit()


def _zeile(text: str, name: str) -> str:
    """Das `<li>`-Stück, in dem dieser Name steht.

    Damit ein Treffer wirklich AN DIESER Zeile hängt und nicht irgendwo sonst
    auf der Seite — ein Band am Seitenkopf würde die Suche nach dem blossen
    Wort ebenso bestehen, hülfe im Laden beim Scrollen aber nicht.
    """
    stuecke = [st for st in text.split("<li") if name in st]
    assert len(stuecke) == 1, f"{name} kommt {len(stuecke)}× als Zeile vor"
    return stuecke[0]


def test_warenkorb_kennzeichnet_einen_ausgemusterten_posten(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    _ausmustern(con, MILCH)

    zeile = _zeile(client.get("/warenkorb").text, MILCH)
    assert "fehlt-im-katalog" in zeile
    assert "Nicht mehr im Katalog" in zeile
    # Der Satz sagt, was zu tun ist — nicht bloss, dass etwas ist.
    assert "im Laden musst du selbst schauen" in zeile


def test_pickzeile_kennzeichnet_einen_ausgemusterten_posten(
        client, con, offene_bestellung):
    _ausmustern(con, MILCH)
    text = client.get("/pick").text

    zeile = _zeile(text, MILCH)
    assert "fehlt-im-katalog" in zeile
    assert "Nicht mehr im Katalog" in zeile
    assert "nimm etwas Ähnliches" in zeile
    # Und der Hinweis steht an der Zeile, nicht in einem Band am Seitenkopf,
    # das beim Scrollen aus dem Bild ist.
    assert "Nicht mehr im Katalog" not in text.split("<li", 1)[0]


def test_der_hinweis_ueberlebt_den_htmx_austausch(client, con,
                                                  offene_bestellung):
    """Nach jedem Haken wird die Liste getauscht — der Hinweis muss bleiben."""
    _ausmustern(con, MILCH)
    zeilen = orders.posten(con, offene_bestellung)
    frisch = client.post(
        f"/pick/{offene_bestellung}/posten/{zeilen[-1]['id']}?stand=gepickt",
        headers=HTMX)
    assert "Nicht mehr im Katalog" in _zeile(frisch.text, MILCH)


def test_freitext_bekommt_keine_katalogwarnung(client, con, offene_bestellung):
    """„Klopapier" hat kein Produkt und kann deshalb nicht ausgelistet sein."""
    _ausmustern(con, MILCH)
    for pfad in ("/pick", "/warenkorb"):
        if pfad == "/warenkorb":
            client.post("/warenkorb/einlegen", data={"free_text": "Klopapier"},
                        headers=HTMX)
        zeile = _zeile(client.get(pfad).text, "Klopapier")
        assert "fehlt-im-katalog" not in zeile
        assert "Nicht mehr im Katalog" not in zeile


def test_ein_aktiver_posten_bleibt_unmarkiert(client, con, offene_bestellung):
    """Der Haferdrink ist noch im Katalog und bekommt nichts ab.

    In beiden Ansichten: eine Warnung, die auch an den Zeilen steht, mit denen
    alles in Ordnung ist, ist keine Warnung mehr.
    """
    _ausmustern(con, MILCH)
    client.post(f"/katalog/einlegen?product_id={_pid(con, HAFER)}", headers=HTMX)
    for pfad in ("/pick", "/warenkorb"):
        zeile = _zeile(client.get(pfad).text, HAFER)
        assert "fehlt-im-katalog" not in zeile
        assert "Nicht mehr im Katalog" not in zeile


def test_die_kennzeichnung_ist_im_stil_wirklich_sichtbar(client, con):
    """Eine Klasse ohne Regel im Stylesheet ist keine Kennzeichnung.

    Dieselben zwei Klassen wie in der Rezeptansicht (WB-325) — es ist
    dieselbe Sache und soll nicht wie zwei aussehen.
    """
    stil = STIL.read_text(encoding="utf-8")
    assert ".fehlt-im-katalog {" in stil
    assert ".ausgemustert {" in stil
    assert ".pickzeile .ausgemustert" in stil


def test_die_neuen_zustaende_der_pickzeile_sind_im_stil_sichtbar():
    """Eine Klasse ohne Regel im Stylesheet ist keine Kennzeichnung (WB-373).

    Und die Rückmeldung muss dort stehen, wo im Laden hingesehen wird: unten
    am Bildrand, nicht am Seitenkopf, an dem längst vorbeigescrollt ist.
    """
    stil = STIL.read_text(encoding="utf-8")
    assert ".pickzeile.gabs-nicht {" in stil
    assert ".vermisst {" in stil
    assert ".pickzeile .hinweis" in stil
    block = stil.split(".pick-meldung {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in block and "bottom:" in block
