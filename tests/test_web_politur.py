"""Sechs Kleinigkeiten, die zusammen „fertig" von „funktioniert" trennen (WB-379).

Kein Browser, kein Netz, kein Telefon — dieselbe Bauart wie die übrigen
Web-Tests: die Oberfläche wird über `fastapi.testclient` als HTTP-Client
geprüft, das Stilblatt als Text.

Was diese Datei NICHT prüfen kann und wofür sie deshalb Stellvertreter
nimmt, steht bei den betroffenen Tests jeweils dabei: ob Safari wirklich
hineinzoomt, ob eine quer scrollende Leiste auf 375 px abgeschnitten ist und
ob ein Sprungziel im Bild landet, entscheidet ein Gerät und kein Test. Was
hier steht, sind die Bedingungen, ohne die es sicher schiefgeht.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picknick import db
from picknick.web import app as webapp

STIL = Path(webapp.STATIC_DIR) / "stil.css"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HTMX = {"HX-Request": "true"}

#: Alle Vollseiten des Shops. `/` ist eine Weiterleitung und steht deshalb
#: nicht dabei; `/pick` ohne Bestellung und `/bons` ohne Datei sind ihre
#: eigenen leeren Zustände und sollen trotzdem antworten.
VOLLSEITEN = ["/katalog", "/chat", "/warenkorb", "/rezepte", "/bestellungen",
              "/pick", "/bons", "/status", "/rolle"]


@pytest.fixture
def bild_dir(tmp_path):
    d = tmp_path / "bilder"
    d.mkdir()
    return d


@pytest.fixture
def bon_dir(tmp_path):
    d = tmp_path / "bons"
    d.mkdir()
    return d


@pytest.fixture
def client(db_datei, bild_dir, bon_dir):
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=bild_dir,
                                      bon_dir=bon_dir)) as c:
        yield c


@pytest.fixture
def con(db_datei):
    c = db.connect(db_datei)
    yield c
    c.close()


def _pid(con, name=MILCH):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _alter_katalog(con, tage=40):
    """Macht den Katalog alt genug für das Hinweisband aus Spec 11.

    Der aufgezeichnete Crawl legt selbst einen frischen `ok`-Lauf an — ohne
    diesen zweiten, älteren Lauf schwiege die Seite zu Recht.
    """
    con.execute("DELETE FROM scrape_run")
    stand = (datetime.now() - timedelta(days=tage)).strftime("%Y-%m-%dT%H:%M:%S")
    con.execute("INSERT INTO scrape_run (source, started_at, finished_at,"
                " status, n_products) VALUES ('knuspr', ?, ?, 'ok', 1)",
                (stand, stand))
    con.commit()


# --------------------------------------------------------------------------
# 1. Ein Zeitfilter für beide Schreibweisen der Datenbank

def test_beide_schreibweisen_derselben_sekunde_werden_gleich_gelesen():
    """`orders` schreibt mit Leerzeichen, `scrape_run` mit `T`. Genau dieser
    Unterschied stand zwei Seiten weit auseinander in der Oberfläche."""
    assert webapp.zeit("2026-08-28 17:32:47") == webapp.zeit("2026-08-28T17:32:47")


@pytest.mark.parametrize("vor_tagen, erwartet", [
    (0, "heute um 17:32"),
    (1, "gestern um 17:32"),
    (2, "vorgestern um 17:32"),
])
def test_die_letzten_drei_tage_bekommen_ein_wort(vor_tagen, erwartet):
    jetzt = datetime(2026, 8, 29, 9, 0, 0)
    stand = (jetzt - timedelta(days=vor_tagen)).strftime("%Y-%m-%d 17:32:47")
    assert webapp.zeit(stand, jetzt=jetzt) == erwartet


def test_aelteres_bekommt_das_deutsche_datum():
    """Dieselbe Form, in der die Bonliste seit jeher schreibt."""
    jetzt = datetime(2026, 8, 29, 9, 0, 0)
    assert webapp.zeit("2026-08-03T07:05:00", jetzt=jetzt) == "03.08.2026 um 07:05"


def test_gestern_zaehlt_in_kalendertagen_und_nicht_in_24_stunden():
    """Um 00:30 ist 23:50 gestern, auch wenn es vierzig Minuten her ist. Mit
    einem Abstand in Stunden gerechnet stünde dort „heute"."""
    jetzt = datetime(2026, 8, 29, 0, 30, 0)
    assert webapp.zeit("2026-08-28 23:50:00", jetzt=jetzt) == "gestern um 23:50"


@pytest.mark.parametrize("wert", [None, ""])
def test_ohne_zeitpunkt_steht_nichts_da(wert):
    assert webapp.zeit(wert) == ""


def test_unlesbares_kommt_unveraendert_zurueck():
    """Ein erfundener Zeitpunkt wäre die schlechtere Antwort als ein
    hässlicher echter — und ein Fehler mitten in einer Liste die schlechteste
    von allen."""
    assert webapp.zeit("irgendwann") == "irgendwann"
    assert webapp.zeit("2026-13-45") == "2026-13-45"


def test_die_bestellliste_zeigt_keinen_maschinenzeitstempel(client, con):
    """Der ganze Weg: einlegen, abschicken, ansehen."""
    client.post("/katalog/einlegen", data={"product_id": str(_pid(con)),
                                           "qty": "1"})
    client.post("/warenkorb/abschicken", follow_redirects=False)

    text = client.get("/bestellungen").text
    assert "abgeschickt heute um " in text
    # Die rohe Form, die vorher dastand: `YYYY-MM-DD HH:MM:SS`.
    assert not re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", text), (
        "auf der Bestellliste steht noch ein Maschinenzeitstempel")


def test_die_statusseite_zeigt_keinen_maschinenzeitstempel(client):
    text = client.get("/status").text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text), (
        "auf der Statusseite steht noch ein ISO-Feld mit T")


# --------------------------------------------------------------------------
# 2. Die Navigation sagt, wo man ist

@pytest.mark.parametrize("pfad", VOLLSEITEN)
def test_jede_vollseite_markiert_sich_selbst_in_der_navigation(client, pfad):
    text = client.get(pfad).text
    treffer = re.findall(r'<a href="([^"]+)" class="aktiv" aria-current="page"',
                         text)
    assert treffer == [pfad], (
        f"{pfad} markiert {treffer} statt sich selbst")


def test_eine_unterseite_markiert_ihren_bereich_mit(client, con):
    """`/pick/12` ist die Pick-Liste. Ohne Präfixvergleich wäre die Markierung
    genau auf den Unterseiten weg, auf denen man sich am ehesten verläuft."""
    client.post("/katalog/einlegen", data={"product_id": str(_pid(con)),
                                           "qty": "1"})
    client.post("/warenkorb/abschicken", follow_redirects=False)
    order_id = con.execute("SELECT max(id) AS id FROM orders"
                           " WHERE state = 'offen'").fetchone()["id"]

    text = client.get(f"/pick/{order_id}").text
    assert '<a href="/pick" class="aktiv" aria-current="page"' in text


def test_die_markierung_ist_nicht_nur_ansage_sondern_auch_sichtbar():
    """`aria-current` hört ein Vorleseprogramm, sehen kann man es nicht."""
    stil = STIL.read_text(encoding="utf-8")
    assert ".kopf nav a.aktiv" in stil
    block = stil.split(".kopf nav a.aktiv", 1)[1].split("}", 1)[0]
    assert "background" in block and "color" in block


def test_die_leiste_verschweigt_nicht_mehr_dass_sie_weitergeht():
    """Neun Ziele passen auf kein Telefon; die Bildlaufleiste war ganz
    ausgeblendet. Ob sie auf 375 px sichtbar ist, entscheidet ein Gerät —
    hier steht nur, dass sie nicht mehr weggeschaltet wird."""
    stil = STIL.read_text(encoding="utf-8")
    nav = stil.split(".kopf nav {", 1)[1].split("}", 1)[0]
    assert "scrollbar-width: none" not in nav
    assert "scrollbar-width: thin" in nav
    balken = stil.split(".kopf nav::-webkit-scrollbar {", 1)[1].split("}", 1)[0]
    assert "display: none" not in balken


def test_die_leiste_rastet_auf_den_anfang_eines_ziels():
    """Sie darf weitergehen, aber links darf kein halbes Wort stehen
    (WB-400 Runde 3).

    Gemessen wurde am Bild und im Browser: mit `scrollIntoView({inline:
    'center'})` und `scroll-snap-align: end` blieben auf acht von zwölf
    Seiten zwischen 10 und 58 px eines Ziels am linken Rand stehen — „pte"
    auf /bestellungen, „en" auf /pick. Ob ein Fetzen SICHTBAR ist,
    entscheidet ein Gerät; hier steht, dass die beiden Stellschrauben, die
    ihn erzeugt haben, in die andere Richtung stehen."""
    stil = STIL.read_text(encoding="utf-8")
    nav = stil.split(".kopf nav {", 1)[1].split("}", 1)[0]
    assert "scroll-snap-type: x mandatory" in nav
    ziel = stil.split(".kopf nav a {", 1)[1].split("}", 1)[0]
    assert "scroll-snap-align: start" in ziel
    assert "scroll-snap-align: end" not in ziel


def test_das_letzte_ziel_der_leiste_ist_erreichbar():
    """Firefox rechnet den Überhang des letzten Flex-Kindes nicht in
    `scrollWidth`: „wer bin ich?" blieb auch am Anschlag angeschnitten. Der
    Abstandhalter am Ende der Leiste IST ein Flex-Kind und zählt mit."""
    stil = STIL.read_text(encoding="utf-8")
    assert ".kopf nav::after" in stil
    block = stil.split(".kopf nav::after", 1)[1].split("}", 1)[0]
    assert "flex:" in block


def test_das_foto_faengt_auf_derselben_hoehe_an_wie_der_name():
    """Eine Zeile trägt einen Satz von einer bis sechs Zeilen; ein mittig
    ausgerichtetes Foto rutschte mit ihm nach unten und ergab neun
    verschiedene Abstände auf EINER Seite (WB-400 Runde 3). Bild und Text
    beginnen oben — ein Abstand statt neun."""
    stil = STIL.read_text(encoding="utf-8")
    bild = stil.split(".zeile > .bild {", 1)[1].split("}", 1)[0]
    text = stil.split(".zeile > .text {", 1)[1].split("}", 1)[0]
    assert "align-self: start" in bild
    assert "align-self: start" in text


def test_der_sammelknopf_der_wegwirft_traegt_den_rotstift(client, con):
    """„Alles übernehmen" und „Alles verwerfen" sahen gleich aus — derselbe
    Kasten, dieselbe Schrift —, obwohl der eine eine ganze Liste in den Korb
    legt und der andere sie wegwirft. Überall sonst im Blatt trägt das
    Wegnehmen den Rotstift (`.mini.loeschen`, `.knopf.loeschen`)."""
    con.execute("INSERT INTO orders (state, created_at)"
                " VALUES ('draft', '2026-08-30 00:00:00')")
    oid = con.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    con.execute("INSERT INTO chat_message (order_id, role, content,"
                " created_at) VALUES (?, 'agent', 'da', '2026-08-30 00:00:00')",
                (oid,))
    mid = con.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    con.execute("INSERT INTO chat_suggestion (chat_message_id, product_id,"
                " free_text, qty, search_term) VALUES (?, NULL, 'Salz', 1, 'Salz')",
                (mid,))
    con.commit()
    text = client.get("/chat").text
    assert 'class="mini loeschen" type="submit">Alles verwerfen' in text
    assert 'class="mini" type="submit">Alles übernehmen' in text


# --------------------------------------------------------------------------
# 3. Kein Eingabefeld unter 16 px

def _regeln(css: str):
    """(Selektor, Rumpf) je Regel. Kommentare fallen vorher weg — in ihnen
    stehen Zahlen, die keine Regel sind."""
    ohne = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for treffer in re.finditer(r"([^{}]+)\{([^{}]*)\}", ohne):
        yield treffer.group(1).strip(), treffer.group(2)


def test_kein_eingabefeld_faellt_unter_die_16_px_von_ios():
    """Safari zoomt bei einem kleineren Feld beim Hineintippen in die Seite —
    und zoomt NICHT zurück. Der Test kann kein iPhone sein; er kann die eine
    Bedingung festhalten, unter der es sicher passiert."""
    zu_klein = []
    for selektor, rumpf in _regeln(STIL.read_text(encoding="utf-8")):
        if not re.search(r"\b(input|textarea|select)\b", selektor):
            continue
        groesse = re.search(r"font-size:\s*(\d+(?:\.\d+)?)px", rumpf)
        if groesse and float(groesse.group(1)) < 16:
            zu_klein.append((selektor, groesse.group(1)))
    assert not zu_klein, f"Eingabefelder unter 16 px: {zu_klein}"


# --------------------------------------------------------------------------
# 4. Das Hinweisband erscheint auf jeder Vollseite

@pytest.mark.parametrize("pfad", VOLLSEITEN)
def test_keine_vollseite_verschweigt_einen_veralteten_katalog(client, con, pfad):
    """Ausgerechnet `/pick` las im Laden Namen, Gebinde und Bilder aus dem
    Katalog und schrieb „nicht mehr im Katalog" — auf Daten, deren Alter die
    Seite verschwieg."""
    _alter_katalog(con, tage=40)
    text = client.get(pfad).text
    assert 'class="band"' in text, f"{pfad} schweigt über den alten Katalog"
    assert "Preise sind 40 Tage alt." in text


@pytest.mark.parametrize("pfad", VOLLSEITEN)
def test_ein_frischer_katalog_bleibt_still(client, pfad):
    """Ein Band, das immer steht, liest bald niemand mehr."""
    assert "Preise sind" not in client.get(pfad).text


# --------------------------------------------------------------------------
# 5. Nach einer Suche stehen die Treffer im Blick

def test_die_kategorien_liegen_eingeklappt_ueber_der_trefferliste(client):
    """138 `<details>` à 44 px sind ~6.100 px zwischen Suchfeld und Treffern.
    Wie weit die Seite wirklich scrollt, misst nur ein Browser — hier steht,
    dass die Karte zu ist."""
    text = client.get("/katalog").text
    karte = re.search(r'<details class="kategorienkarte"([^>]*)>', text)
    assert karte, "die Kategorienkarte fehlt"
    assert "open" not in karte.group(1)


def test_wer_nach_kategorie_blaettert_bekommt_sie_offen(client):
    """Dann ist die Liste das Werkzeug, das man in der Hand hat — und der
    gewählte Ast steht offen statt zugeklappt wie die anderen 137."""
    zweig = "Milch, Molkerei &amp; Butter"
    text = client.get("/katalog", params={"l1": "Milch, Molkerei & Butter"}).text
    assert re.search(r'<details class="kategorienkarte" open>', text)
    ast = text.split(zweig, 1)[0]
    assert ast.rstrip().endswith("<summary>"), "der Ast steht nicht als Summary"
    assert "<details open>" in text, "der gewählte Ast ist zugeklappt"


def test_jeder_tausch_der_trefferliste_holt_sie_ins_bild(client):
    """Ohne `show:` ändert sich nach dem Tippen im sichtbaren Bereich nichts:
    getauscht wird eine Liste, die weiter unten steht."""
    text = client.get("/katalog").text
    tausche = re.findall(r'hx-target="#produkte" hx-swap="([^"]+)"', text)
    assert tausche, "kein Tausch auf die Trefferliste gefunden"
    assert all("show:#treffer:top" in t for t in tausche), tausche
    assert 'id="treffer"' in text, "das Sprungziel gibt es gar nicht"


# --------------------------------------------------------------------------
# 6. Der Weg von der Rezeptzutat zum Katalogtreffer — an beiden Enden

def _rezept_mit_zutat(client, con, name="Rinderbrühe", amount=500.0, unit="ml"):
    r = client.post("/rezepte", data={"name": "Pho Bo"}, follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    con.execute("INSERT INTO recipe_ingredient (recipe_id, pos, raw_name, name,"
                " amount, unit) VALUES (?, 0, ?, ?, ?, ?)",
                (rid, name, name, amount, unit))
    con.commit()
    return rid


def test_der_griff_an_der_rezeptzutat_springt_auf_die_trefferliste(client, con):
    rid = _rezept_mit_zutat(client, con)
    text = client.get(f"/rezepte/{rid}").text
    links = re.findall(r'<a class="name" href="([^"]+)"', text)
    assert links, "der Weg von der Zutat zum Katalog fehlt"
    assert all(l.endswith("#rezept-treffer") for l in links), links


def test_das_tap_ziel_der_rezeptzutat_haelt_die_44_px_des_blattes():
    """~24 px war die Höhe der Textzeile. In diesem Blatt sind Tap-Ziele 44."""
    stil = STIL.read_text(encoding="utf-8")
    assert ".rezeptzutaten a.name" in stil
    block = stil.split(".rezeptzutaten a.name", 1)[1].split("}", 1)[0]
    assert "min-height: var(--tap)" in block


def test_die_rezept_trefferliste_hat_einen_platz_fuer_die_rueckmeldung(client, con):
    rid = _rezept_mit_zutat(client, con)
    text = client.get(f"/rezepte/{rid}", params={"q": "milch"}).text
    pid = _pid(con)
    assert f'class="rueckmeldung" id="rz-{pid}"' in text


def test_das_plus_der_rezept_trefferliste_meldet_sich_am_knopf(client, con):
    """`hx-target="#rezept"` zeigt an den Seitenanfang, gedrückt wird unten.
    Die Meldung kommt deshalb per `hx-swap-oob` dorthin zurück, wo der Daumen
    war — sonst drückt man ein zweites Mal."""
    rid = _rezept_mit_zutat(client, con)
    pid = _pid(con)
    r = client.post(f"/rezepte/{rid}/zutaten?product_id={pid}", headers=HTMX)
    assert r.status_code == 200
    assert f'id="rz-{pid}" hx-swap-oob="innerHTML"' in r.text
    assert "im Rezept" in r.text
    # Der Haupttausch bleibt die Zutatenliste — die Meldung tritt nicht an
    # ihre Stelle.
    assert MILCH in r.text


def test_ohne_knopf_gibt_es_auch_keine_rueckmeldung(client, con):
    """„Nichts davon, ich schreibe es selbst" hat keinen Treffer, an dem eine
    Meldung stehen könnte. Ein `hx-swap-oob` ins Leere wäre eine Warnung in
    der Konsole und sonst nichts."""
    rid = _rezept_mit_zutat(client, con)
    r = client.post(f"/rezepte/{rid}/zutaten", data={"free_text": "Sternanis"},
                    headers=HTMX)
    assert r.status_code == 200
    assert "hx-swap-oob" not in r.text
