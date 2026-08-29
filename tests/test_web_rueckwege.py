"""Rückfragen, Rückwege und die eine 404-Seite (WB-376).

Vier Knöpfe zerstörten etwas endgültig, ohne zu fragen und ohne Rückweg — Bon
löschen, Bon neu lesen, Rezept löschen und „−" bei Menge 1 im Korb. Alle vier
sitzen auf einem Telefon unter dem Daumen, und auf `/bons` wanderte die
Knopfposition zwischen den Zeilen, sodass der Daumen das Falsche traf.

Was diese Datei prüft, ist deshalb nicht „gibt es einen Dialog", sondern:

* dass der ERSTE Tipp nichts kaputt macht (die Datei liegt noch da, das
  Rezept auch),
* dass die Rückfrage die ZAHL nennt, um die es geht — 14 verworfene
  Entscheidungen sind der Unterschied zwischen einer Auskunft und einem
  Türsteher,
* dass die Löschstelle in jeder Bon-Zeile dieselbe ist,
* und dass die drei Adressen ins Leere DIESELBE Seite bekommen, mit Kopf und
  Rückweg statt null Bytes.

Kein Netz, kein Modell, kein Browser: die Belege werden hier direkt gebaut
(`bons.anlegen`), weil die Rückfrage nur die Zahlen aus der Datenbank liest
und mit dem Auslesen selbst nichts zu tun hat.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from picknick import bons, db, orders, recipes
from picknick.bons import zerlegen
from picknick.web import app as webapp

HTMX = {"HX-Request": "true"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"0" * 64


@pytest.fixture
def bon_dir(tmp_path):
    return tmp_path / "bons"


@pytest.fixture
def client(db_datei, tmp_path, bon_dir):
    """Der volle Katalog, damit auch der Korb echte Produkte hat."""
    with TestClient(webapp.create_app(db_path=db_datei,
                                      image_dir=tmp_path / "bilder",
                                      bon_dir=bon_dir)) as c:
        yield c


@pytest.fixture
def con(client):
    c = db.connect(client.app.state.db_path)
    yield c
    c.close()


def _lade(client, name, inhalt) -> str:
    """Lädt eine Datei hoch und gibt ihren abgelegten Namen zurück."""
    client.post("/bons", files={"datei": (name, inhalt)})
    return sorted(p.name for p in client.app.state.bon_dir.iterdir())[-1]


def _beleg_bauen(con, datei: str, posten: int = 3) -> int:
    """Ein eingelesener Bon, ohne Modell und ohne Datei zu lesen."""
    bon = zerlegen.Bon(
        laden="rewe", datum="2026-08-01", summe_cents=100 * posten,
        posten=[zerlegen.Posten(text=f"ARTIKEL {i}", gesamt_cents=100)
                for i in range(1, posten + 1)])
    return bons.anlegen(con, bon, datei=datei)


def _stellen(text: str) -> list[str]:
    """Die Knopfleisten der Bon-Liste, in der Reihenfolge der Zeilen."""
    return re.findall(r'<span class="stellen">(.*?)</span>\s*</li>', text,
                      re.S)


# --------------------------------------------------------------------------
# Eine 404-Seite für alle Wege
#
# Vorher waren es drei Antworten auf dieselbe Frage: `/pick/99` und
# `/rezepte/99` lieferten 0 Bytes — ein weisses Blatt ohne Kopf und ohne
# Rückweg —, `/bons/gibtesnicht.pdf` dagegen eine 200 mit einer Geisterseite
# samt Auslesen-Knopf.

@pytest.mark.parametrize("weg", ["/pick/99", "/rezepte/99",
                                 "/bons/gibtsnicht.pdf"])
def test_die_drei_wege_ins_leere_liefern_dieselbe_ordentliche_seite(client, weg):
    r = client.get(weg)
    assert r.status_code == 404
    # Der Befund des Tickets in einer Zeile: 0 Bytes waren es.
    assert len(r.content) > 500, "wieder ein weisses Blatt"
    assert "Das gibt es nicht" in r.text
    # Kopf und Navigation aus `basis.html` — sonst ist die Seite eine
    # Sackgasse, aus der nur der Zurück-Knopf des Browsers führt.
    assert 'href="/katalog"' in r.text
    assert 'href="/warenkorb"' in r.text


def test_auch_eine_voellig_unbekannte_adresse_bekommt_die_seite(client):
    """Nicht nur die drei aus dem Ticket: ein vertippter Link ebenso."""
    r = client.get("/gibtesnichtundgabesnie")
    assert r.status_code == 404
    assert "Das gibt es nicht" in r.text


def test_der_rueckweg_zeigt_dorthin_wo_man_war(client):
    """Ein Link auf die Startseite wäre „fang von vorn an"."""
    assert "Zur Pick-Liste" in client.get("/pick/99").text
    assert "Alle Rezepte" in client.get("/rezepte/99").text
    assert "Alle Bons" in client.get("/bons/gibtsnicht.pdf").text


def test_ein_bon_ohne_datei_baut_keine_geisterseite_mehr(client, bon_dir):
    """`/bons/{name}` prüfte nur den NAMEN, nie die Datei.

    Der Name war gültig, also antwortete die Seite mit 200 und „noch nicht
    ausgelesen" — samt Auslesen-Knopf, der an derselben fehlenden Datei
    scheiterte. Das trifft sie im Alltag: die Bon-Ansicht auf dem zweiten
    Telefon, während der Bon auf dem ersten gelöscht wird.
    """
    bon_dir.mkdir(parents=True, exist_ok=True)
    r = client.get("/bons/REWE-ebon-20260101-120000.pdf")
    assert r.status_code == 404
    assert "noch nicht ausgelesen" not in r.text
    assert "Auslesen" not in r.text


def test_ein_geloeschter_bon_ist_ab_dem_naechsten_blick_weg(client):
    """Der Fall mit den zwei Telefonen, ganz durchgespielt."""
    name = _lade(client, "bon.pdf", PDF)
    assert client.get(f"/bons/{name}").status_code == 200
    client.post(f"/bons/{name}/loeschen")
    r = client.get(f"/bons/{name}")
    assert r.status_code == 404
    assert "nicht (mehr)" in r.text


# --------------------------------------------------------------------------
# Bon löschen: Rückfrage, dann Meldung

def test_der_loeschknopf_loescht_nicht_sondern_fragt(client, bon_dir):
    name = _lade(client, "bon.pdf", PDF)
    # Die Liste tippt nicht mehr direkt in den POST.
    liste = client.get("/bons").text
    assert f'href="/bons/{name}/loeschen"' in liste

    r = client.get(f"/bons/{name}/loeschen")
    assert r.status_code == 200
    assert "Abbrechen" in r.text
    assert 'action="/bons/' in r.text and "/loeschen" in r.text
    # Und vor allem: die Datei liegt noch da.
    assert (bon_dir / name).is_file()


def test_die_rueckfrage_sagt_auch_was_bleibt(client, con):
    """Sonst rechnet sie mit dem grösseren Verlust und bricht ab."""
    name = _lade(client, "bon.pdf", PDF)
    _beleg_bauen(con, name, posten=3)
    r = client.get(f"/bons/{name}/loeschen")
    assert "3 Posten" in r.text
    assert "Einkaufshistorie" in r.text


def test_der_zweite_schritt_loescht_und_sagt_es(client, bon_dir):
    name = _lade(client, "bon.pdf", PDF)
    r = client.post(f"/bons/{name}/loeschen", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/bons?weg=")
    assert not (bon_dir / name).exists()
    # Eine Liste, die bloss kürzer geworden ist, sagt nicht, ob der richtige
    # Bon getroffen wurde.
    assert "ist gelöscht" in client.get(r.headers["location"]).text


def test_die_rueckfrage_zu_einem_bon_der_weg_ist(client, bon_dir):
    bon_dir.mkdir(parents=True, exist_ok=True)
    r = client.get("/bons/gibtsnicht.pdf/loeschen")
    assert r.status_code == 404
    assert "Das gibt es nicht" in r.text


# --------------------------------------------------------------------------
# „Neu lesen" nennt die Zahl der Entscheidungen, die es verwirft

def test_neu_lesen_nennt_die_zahl_der_entscheidungen(client, con):
    name = _lade(client, "bon.pdf", PDF)
    receipt = _beleg_bauen(con, name, posten=4)
    pid = _erstes_produkt(con)
    zeilen = bons.posten(con, receipt)
    for z in zeilen[:2]:
        bons.zuordnung_setzen(con, z["id"], product_id=pid)
        bons.entscheiden(con, z["id"], "kept")
    bons.entscheiden(con, zeilen[2]["id"], "removed")

    r = client.get(f"/bons/{name}/auslesen")
    assert r.status_code == 200
    assert "3 Entscheidungen" in r.text
    assert "2 bestätigt" in r.text and "1 verworfen" in r.text
    # Und der Lauf hat NICHT begonnen: der Beleg steht unverändert da.
    assert bons.beleg_zu_datei(con, name)["id"] == receipt
    assert bons.bilanz(con, receipt)["bestaetigt"] == 2


def test_beim_ersten_auslesen_gibt_es_nichts_zu_verlieren(client):
    """Kein Verlust, keine Verlustliste — und der Knopf heisst anders."""
    name = _lade(client, "bon.pdf", PDF)
    r = client.get(f"/bons/{name}/auslesen")
    assert r.status_code == 200
    assert "Entscheidungen" not in r.text
    assert "Auslesen" in r.text


def test_die_liste_fuehrt_bei_einem_gelesenen_bon_ueber_die_rueckfrage(
        client, con):
    name = _lade(client, "bon.pdf", PDF)
    _beleg_bauen(con, name)
    stellen = _stellen(client.get("/bons").text)
    assert len(stellen) == 1
    # Ein Link (GET) und kein Formular (POST): der erste Tipp liest nur.
    assert f'href="/bons/{name}/auslesen"' in stellen[0]
    assert f'action="/bons/{name}/auslesen"' not in stellen[0]


# --------------------------------------------------------------------------
# Die Knopfposition wandert nicht
#
# Vorher hatte die PDF-Zeile `[Auslesen][×]` und die Bild-Zeile ohne OCR nur
# `[×]` — das Löschen sass dort, wo eine Zeile weiter oben der harmlose Knopf
# sass.

def test_ohne_ocr_haelt_ein_platzhalter_die_stelle_frei(client, monkeypatch):
    monkeypatch.setattr(bons, "ocr_da", lambda: False)
    _lade(client, "foto.png", PNG)
    _lade(client, "ebon.pdf", PDF)

    stellen = _stellen(client.get("/bons").text)
    assert len(stellen) == 2
    for zeile in stellen:
        # Zwei Stellen je Zeile, und die zweite ist immer das Löschen — auch
        # in der Zeile, die gar nichts auszulesen hat.
        knoepfe = re.findall(r'class="mini([^"]*)"', zeile)
        assert len(knoepfe) == 2, zeile
        assert "loeschen" in knoepfe[-1], zeile
    # Genau eine der beiden Zeilen kann gelesen werden, die andere hält den
    # Platz frei.
    assert sum("platzhalter" in z for z in stellen) == 1


def test_das_loeschen_ist_in_jeder_zeile_das_letzte(client, monkeypatch):
    monkeypatch.setattr(bons, "ocr_da", lambda: True)
    _lade(client, "foto.png", PNG)
    _lade(client, "ebon.pdf", PDF)
    for zeile in _stellen(client.get("/bons").text):
        knoepfe = re.findall(r'class="mini([^"]*)"', zeile)
        assert len(knoepfe) == 2, zeile
        assert "loeschen" in knoepfe[-1]


# --------------------------------------------------------------------------
# Rezept löschen: die Rückfrage zählt auf, was am CASCADE hängt

def _rezept_mit_inhalt(con) -> int:
    rid = recipes.anlegen(con, "Pho Bo")
    con.execute(
        "UPDATE recipe SET instructions = ?, source_url = ?, cook_minutes = ?"
        " WHERE id = ?",
        ("Erst dies.\n\nDann das.\n\nZum Schluss jenes.",
         "https://www.chefkoch.de/rezepte/1/Pho.html", 480, rid))
    for i, name in enumerate(["Rinderbrust", "Reisbandnudeln", "Sternanis"]):
        con.execute(
            "INSERT INTO recipe_ingredient (recipe_id, pos, raw_name,"
            "                               name, amount, unit)"
            " VALUES (?, ?, ?, ?, ?, ?)", (rid, i, name, name, 500.0, "g"))
    con.commit()
    return rid


def test_die_rueckfrage_zaehlt_auf_was_der_cascade_mitnimmt(client, con):
    rid = _rezept_mit_inhalt(con)
    r = client.get(f"/rezepte/{rid}/loeschen")
    assert r.status_code == 200
    assert "3 Zutaten laut Rezept" in r.text
    assert "3 Schritten" in r.text
    assert "chefkoch.de" in r.text
    assert "Zeiten" in r.text
    # Der erste Tipp macht nichts kaputt.
    assert recipes.rezept(con, rid)["name"] == "Pho Bo"


def test_der_loeschknopf_am_rezept_fuehrt_auf_die_rueckfrage(client, con):
    rid = _rezept_mit_inhalt(con)
    seite = client.get(f"/rezepte/{rid}").text
    assert f'href="/rezepte/{rid}/loeschen"' in seite
    # Kein Formular mehr, das mit einem Tipp löscht.
    assert f'action="/rezepte/{rid}/loeschen"' not in seite


def test_ein_leeres_rezept_sagt_dass_nichts_dranhaengt(client, con):
    rid = recipes.anlegen(con, "Leer")
    con.commit()
    r = client.get(f"/rezepte/{rid}/loeschen")
    assert "es hängt nichts daran" in r.text


def test_die_rueckfrage_zu_einem_rezept_das_es_nicht_gibt(client):
    r = client.get("/rezepte/999999/loeschen")
    assert r.status_code == 404
    assert "Das gibt es nicht" in r.text


# --------------------------------------------------------------------------
# Der Korb: Rückweg statt Rückfrage
#
# WB-361 hat für jeden Chat-Tipp einen Rückweg gebaut und verweist für die
# Rücknahme ausdrücklich in den Korb — „dort steht ein Löschknopf". Genau der
# war die einzige Stelle ohne Rückweg.

def _erstes_produkt(con) -> int:
    return con.execute("SELECT id FROM product WHERE active = 1"
                       " ORDER BY id LIMIT 1").fetchone()["id"]


def test_minus_bei_menge_eins_bietet_den_rueckweg_an(client, con):
    pid = _erstes_produkt(con)
    item = orders.einlegen(con, product_id=pid, qty=1, store="rewe")

    r = client.post(f"/warenkorb/posten/{item}/menge?qty=0", headers=HTMX)
    assert r.status_code == 200
    assert "ist aus dem Korb" in r.text
    assert "rückgängig" in r.text
    assert orders.inhalt(con) == []


def test_der_rueckweg_legt_die_zeile_wieder_hin(client, con):
    pid = _erstes_produkt(con)
    item = orders.einlegen(con, product_id=pid, qty=3, store="lidl")
    vorher = orders.inhalt(con)[0]

    r = client.post(f"/warenkorb/posten/{item}/loeschen", headers=HTMX)
    felder = dict(re.findall(r'name="(\w+)" value="([^"]*)"', r.text))
    assert felder["product_id"] == str(pid)

    zurueck = client.post("/warenkorb/wiederherstellen", data=felder,
                          headers=HTMX)
    assert zurueck.status_code == 200
    assert "liegt wieder im Korb" in zurueck.text
    nachher = orders.inhalt(con)
    assert len(nachher) == 1
    # Dieselbe Zeile, nicht bloss irgendeine: Menge und Laden gehen mit.
    assert nachher[0]["product_id"] == vorher["product_id"]
    assert nachher[0]["qty"] == vorher["qty"] == 3
    assert nachher[0]["store"] == "lidl"


def test_der_rueckweg_haelt_auch_den_bedarf_fest(client, con):
    """Die gerechnete Menge aus WB-362 ist der teuerste Teil einer Zeile."""
    pid = con.execute("SELECT id FROM product WHERE unit_text IS NOT NULL"
                      " AND active = 1 ORDER BY id LIMIT 1").fetchone()["id"]
    item = orders.einlegen(con, product_id=pid, qty=1, menge=500.0,
                           einheit="g")
    vorher = orders.inhalt(con)[0]
    assert vorher["need_amount"] == 500.0

    r = client.post(f"/warenkorb/posten/{item}/loeschen", headers=HTMX)
    felder = dict(re.findall(r'name="(\w+)" value="([^"]*)"', r.text))
    client.post("/warenkorb/wiederherstellen", data=felder, headers=HTMX)

    nachher = orders.inhalt(con)[0]
    assert nachher["need_amount"] == 500.0
    assert nachher["need_unit"] == "g"
    assert nachher["qty"] == vorher["qty"]


def test_ein_freitextposten_kommt_ebenso_zurueck(client, con):
    orders.einlegen(con, free_text="Klopapier", qty=2)
    item = orders.inhalt(con)[0]["id"]
    r = client.post(f"/warenkorb/posten/{item}/loeschen", headers=HTMX)
    felder = dict(re.findall(r'name="(\w+)" value="([^"]*)"', r.text))
    client.post("/warenkorb/wiederherstellen", data=felder, headers=HTMX)
    zeilen = orders.inhalt(con)
    assert [z["name"] for z in zeilen] == ["Klopapier"]
    assert zeilen[0]["qty"] == 2


def test_die_ruecknahme_legt_nichts_doppelt_hin(client, con):
    """Zwischen Löschen und Rücknahme kann dieselbe Sache neu im Korb liegen.

    Dann ist die Rücknahme erfüllt; eine zweite Zeile wäre eine, die im Laden
    zweimal gegriffen wird.
    """
    pid = _erstes_produkt(con)
    item = orders.einlegen(con, product_id=pid, qty=1)
    r = client.post(f"/warenkorb/posten/{item}/loeschen", headers=HTMX)
    felder = dict(re.findall(r'name="(\w+)" value="([^"]*)"', r.text))

    orders.einlegen(con, product_id=pid, qty=5)
    client.post("/warenkorb/wiederherstellen", data=felder, headers=HTMX)

    zeilen = orders.inhalt(con)
    assert len(zeilen) == 1
    assert zeilen[0]["qty"] == 5, "die jüngere Aussage gilt"


def test_ohne_javascript_bleibt_der_rueckweg_stehen(client, con):
    """Eine Weiterleitung würde ihn verschlucken — dann wäre er nur Zierde."""
    pid = _erstes_produkt(con)
    item = orders.einlegen(con, product_id=pid, qty=1)
    r = client.post(f"/warenkorb/posten/{item}/menge?qty=0",
                    follow_redirects=False)
    assert r.status_code == 200, "eine 303 verliert den Rückweg"
    assert "rückgängig" in r.text
    assert 'action="/warenkorb/wiederherstellen"' in r.text


def test_ein_posten_den_es_nicht_gibt_ergibt_keinen_rueckweg(client, con):
    r = client.post("/warenkorb/posten/999999/loeschen", headers=HTMX)
    assert r.status_code == 200
    assert "rückgängig" not in r.text
    assert "gibt es nicht" in r.text
