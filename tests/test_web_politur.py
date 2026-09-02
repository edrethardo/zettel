"""Sechs Kleinigkeiten, die zusammen „fertig" von „funktioniert" trennen (WB-379).

Kein Browser, kein Netz, kein Telefon — dieselbe Bauart wie die übrigen
Web-Tests: die Oberfläche wird über `fastapi.testclient` als HTTP-Client
geprüft, das Stilblatt als Text.

Was diese Datei NICHT prüfen kann und wofür sie deshalb Stellvertreter
nimmt, steht bei den betroffenen Tests jeweils dabei: ob Safari wirklich
hineinzoomt, ob die feste Leiste unten jeden Reiter ganz zeigt und
ob ein Sprungziel im Bild landet, entscheidet ein Gerät und kein Test. Was
hier steht, sind die Bedingungen, ohne die es sicher schiefgeht.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import db, orders, recipes
from zettel.assistant import vorschlaege
from zettel.web import app as webapp

STIL = Path(webapp.STATIC_DIR) / "stil.css"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HTMX = {"HX-Request": "true"}

#: Alle Vollseiten des Shops. `/` ist eine Weiterleitung und steht deshalb
#: nicht dabei; `/pick` ohne Bestellung und `/bons` ohne Datei sind ihre
#: eigenen leeren Zustände und sollen trotzdem antworten.
VOLLSEITEN = ["/katalog", "/chat", "/warenkorb", "/rezepte", "/bestellungen",
              "/pick", "/bons", "/status", "/rolle", "/mehr"]

#: Welcher Reiter der Leiste auf einer Seite markiert ist. Fünf Reiter für
#: zehn Seiten: was nicht selbst einen hat, gehört unter „Mehr".
REITER = {"/rezepte": "/mehr", "/bestellungen": "/mehr", "/bons": "/mehr",
          "/status": "/mehr", "/rolle": "/mehr"}


def _block(stil: str, selektor: str) -> str:
    """Die Deklarationen GENAU EINES Blocks. Ein Selektor, der zweimal im
    Blatt steht, gewinnt die Kaskade mit dem späteren Block — und ein Test,
    der nur den ersten liest, bliebe grün (so ist es bei `.pickzeile .stellen
    .mini` am 2026-09-01 fast passiert). Der Treffer muss am Zeilenanfang
    stehen, sonst zählt `.a .b {` auch in `.x .a .b {` mit."""
    treffer = list(re.finditer("^" + re.escape(selektor) + r" \{", stil, re.M))
    assert len(treffer) == 1, (
        f"{selektor} steht {len(treffer)}× am Zeilenanfang im Blatt,"
        " erwartet genau einmal")
    rest = stil[treffer[0].end():]
    return rest[:rest.index("}")]


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
def test_jede_vollseite_markiert_ihren_reiter(client, pfad):
    text = client.get(pfad).text
    treffer = re.findall(r'<a href="([^"]+)" class="aktiv" aria-current="page"',
                         text)
    assert treffer == [REITER.get(pfad, pfad)], (
        f"{pfad} markiert {treffer} statt {REITER.get(pfad, pfad)}")


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
    """`aria-current` hört ein Vorleseprogramm, sehen kann man es nicht.
    Der Reiter bekommt Farbe UND eine Kante — ein Farbton allein ist auf
    einem Telefon in der Sonne kein Unterschied."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".leiste a.aktiv")
    assert "color" in block and "box-shadow" in block


def test_die_leiste_hat_fuenf_reiter_und_liegt_unter_dem_blatt(client):
    """Sieben Ziele passten in keine 390 px (UI-Review 2026-09-01, Fund 6).
    Fünf Reiter passen; alles Weitere sammelt „Mehr". Die Leiste steht im
    HTML NACH `<main>`: sie ist fest am unteren Rand, und ein
    Vorleseprogramm soll erst den Inhalt hören."""
    text = client.get("/katalog").text
    leiste = text.split('<nav class="leiste" id="hauptnavigation"', 1)[1]
    leiste = leiste.split("</nav>", 1)[0]
    assert re.findall(r'<a href="([^"]+)"', leiste) == [
        "/katalog", "/chat", "/warenkorb", "/pick", "/mehr"]
    assert 'id="korb-anzahl"' in leiste
    assert text.index("</main>") < text.index('<nav class="leiste"')
    assert '<footer class="fuss"' not in text
    assert "leisteRasten" not in text


def test_mehr_sammelt_was_keinen_reiter_hat(client):
    """Rezepte, Bestellungen, Bons — und darunter, leiser, Rolle und Status:
    die Fusszeile aus Welle 1 geht in dieser Seite auf."""
    text = client.get("/mehr").text
    seite = text.split("<main>", 1)[1].split("</main>", 1)[0]
    assert re.findall(r'<a href="([^"]+)"', seite) == [
        "/rezepte", "/bestellungen", "/bons", "/rolle", "/status"]


def test_eine_seite_unter_mehr_markiert_den_reiter_mehr(client):
    text = client.get("/rezepte").text
    assert '<a href="/mehr" class="aktiv" aria-current="page"' in text
    assert '<a href="/rezepte" class="aktiv"' not in text


def test_das_rollenabzeichen_steht_neben_dem_pfeil_und_nicht_in_der_mitte():
    """`.mehrliste a` ist ein Flex-Container, in dem ZWEI Kinder nach rechts
    wollen: das Abzeichen mit der Rolle und der Pfeil aus `::after`. Zwei
    `margin-left: auto` teilen den freien Platz unter sich auf, statt ihn
    einem zu geben — „Wer bin ich?" trug seine Rolle damit mitten in der
    Zeile. Nur der Pfeil schiebt, das Abzeichen fährt neben ihm mit; genau
    die Antwort, die `.leiste .korbzahl` schon bekommen hat."""
    stil = STIL.read_text(encoding="utf-8")
    # Die Vorgabe von `.anzahl` bleibt, wie sie ist — in den Kategorien, wo
    # sie herkommt, soll die Zahl weiterhin an den rechten Rand.
    assert "margin-left: auto" in _block(stil, ".anzahl")
    assert "margin-left: auto" in _block(stil, ".mehrliste a::after")
    assert "margin-left: 0" in _block(stil, ".mehrliste .anzahl")


def test_die_leiste_ist_fest_und_das_blatt_macht_ihr_platz():
    """Eine feste Leiste deckt die unteren 56 px des Fensters. Was darunter
    liegt, ist unerreichbar: das Blatt bekommt unten den Abstand der Leiste,
    und die klebende Kasse setzt sich auf sie statt unter sie."""
    stil = STIL.read_text(encoding="utf-8")
    leiste = _block(stil, ".leiste")
    assert "position: fixed" in leiste and "bottom: 0" in leiste
    assert "calc(var(--leiste) + var(--v6))" in _block(stil, "main")
    assert "bottom: var(--leiste)" in _block(stil, ".kasse")
    # `.fussnote` bleibt; die Regeln `.fuss`, `.fuss a`, `.kopf nav …` gehen.
    assert not re.search(r"^\.fuss\b", stil, re.M)
    assert not re.search(r"^\.kopf nav", stil, re.M)
    # Der Zaun gegen den Rückweg: die Leiste, die sie ersetzt, scrollte quer
    # und blendete ihre Ränder aus. Kommen diese beiden Eigenschaften ins
    # Blatt zurück, ist die alte Leiste zurück — dann soll hier etwas rot
    # werden und nicht erst ein Blick auf ein Telefon.
    assert "scroll-snap" not in stil and "mask-image" not in stil


def test_die_pickmeldung_liegt_ueber_der_leiste():
    """Die Meldung der Pick-Liste ist fest am unteren Rand — und die Leiste
    auch. Bei `bottom: 12px` deckte sie 48 der 56 px der Leiste, und ein
    Fehler bleibt bis zum nächsten Tausch stehen: offline war die Leiste
    damit nicht mehr zu treffen. Sie sitzt jetzt ÜBER der Leiste."""
    stil = STIL.read_text(encoding="utf-8")
    assert "bottom: calc(var(--leiste)" in _block(stil, ".pick-meldung")


def test_das_foto_faengt_auf_derselben_hoehe_an_wie_der_name():
    """Eine Zeile trägt einen Satz von einer bis sechs Zeilen; ein mittig
    ausgerichtetes Foto rutschte mit ihm nach unten und ergab neun
    verschiedene Abstände auf EINER Seite (WB-400 Runde 3). Bild und Text
    beginnen oben — ein Abstand statt neun."""
    stil = STIL.read_text(encoding="utf-8")
    bild = _block(stil, ".zeile > .bild")
    text = _block(stil, ".zeile > .text")
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


def test_der_griff_an_der_rezeptzutat_ist_kein_flex_container():
    """UI-Review 2026-09-01, Fund 4: als `display: flex` wurde der Hinweis
    („, aus der Mühle") zum Geschwister des Namens, beide teilten sich die
    Breite — „Lavend / elblüte / n". Ein Block lässt beide als EINEN Satz
    fliessen; die 44 px (WB-379) bleiben."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".rezeptzutaten a.name")
    assert "display: flex" not in block
    assert "display: block" in block
    assert "min-height: var(--tap)" in block


def test_die_mengenspalte_der_rezeptzutat_ist_weder_starr_noch_mono():
    """„1 gr. Dose/n" stand dreizeilig in 8ch Monospace. Eine Kochangabe ist
    keine Maschinenausgabe; für fluchtende Ziffern reicht `tabular-nums`. Die
    globale `.menge`-Regel setzt heute selbst keine Schrift mehr — die
    Deklaration steht trotzdem als Zaun, nicht als Kaskaden-Override, damit
    Monospace hier nie zurückkehrt."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".rezeptzutaten .menge")
    assert "8ch" not in block
    assert "var(--mono)" not in block
    assert "tabular-nums" in block
    assert "font-family: inherit" in block


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


# --------------------------------------------------------------------------
# 7. Der Bestellknopf klebt am unteren Rand

def test_summe_und_bestellknopf_kleben_am_unteren_rand():
    """Der Knopf stand nach 2 500 px Scrollweg (UI-Review 2026-09-01,
    Fund 8). Ob er auf einem Telefon wirklich im Bild bleibt, entscheidet
    das Gerät — hier steht, dass die Kasse überhaupt klebt und einen
    eigenen Grund hat, damit die Liste nicht durch sie hindurchscheint."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".kasse")
    assert "position: sticky" in block
    # Die Kasse klebt AUF der festen Leiste (Welle 2), nicht am Fensterrand.
    assert "bottom: var(--leiste)" in block
    assert "background: var(--grund)" in block


def test_die_kasse_umschliesst_summe_und_knopf(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con)}", headers=HTMX)
    text = client.get("/warenkorb").text
    kasse = text.split('<div class="kasse">', 1)[1].split("</div>", 1)[0]
    assert 'class="summe"' in kasse
    assert "Bestellung abschicken" in kasse


# --------------------------------------------------------------------------
# 8. Die Pick-Zeile ist eine Zeile

def test_gabs_nicht_steht_neben_dem_text_und_nicht_darunter():
    """Fünfzehn Knöpfe je in eigener Zeile mit 40 px Luft darüber (UI-Review
    2026-09-01, Fund 11). Das Formular mit dem Haken nimmt den Rest, der
    Knopf so viel, wie er braucht — beide in EINER Zeile. Ob ein langer
    Name dann umbricht, statt den Knopf zu verdrängen, entscheidet das
    Gerät; hier steht, dass der Umbruch nicht mehr erzwungen ist. Der Knopf
    darf dafür zwei Zeilen hoch sein — sonst bricht „Champignons" mitten im
    Wort, weil ihm 100 px bleiben."""
    stil = STIL.read_text(encoding="utf-8")
    form = _block(stil, ".pickzeile > form")
    assert "100%" not in form
    assert "flex: 1 1 0" in form
    stellen = _block(stil, ".pickzeile .stellen")
    assert "flex: 0 0 auto" in stellen
    knopf = _block(stil, ".pickzeile .stellen .mini")
    assert "white-space: normal" in knopf
    # Ein Selektor, ein Block — sonst gewinnt der spätere Block die Kaskade,
    # und der Test sieht nur den ersten.
    assert stil.count(".pickzeile .stellen .mini {") == 1


# --------------------------------------------------------------------------
# Welle 2: eine Schrift für das Blatt

def test_monospace_gibt_es_nur_noch_fuer_code():
    """Zwölf Regeln setzten `var(--mono)` — Preise, Packungszahlen,
    Portionsfelder, die Gesamtzeit (UI-Review 2026-09-01, Fund 13). Die
    Regel „gerechnete Zahlen wie von der Kasse" war für den Leser kein
    Unterschied, den er deuten konnte. Ziffern, die untereinander stehen,
    bekommen `tabular-nums`; Monospace bleibt, wo Code steht."""
    stil = STIL.read_text(encoding="utf-8")
    assert stil.count("var(--mono)") == 1
    assert "var(--mono)" in _block(stil, "code")


def test_die_portionsfelder_erben_die_schrift():
    """`<input>` erbt `font-family` nicht vom Body — das ist eine
    Eigenheit von Formularfeldern (UA-Stylesheet), keine Kaskade wie sonst
    im Blatt. Ohne `var(--mono)` UND ohne `font-family: inherit` fiel das
    Feld auf die Systemschrift des Formularelements zurück (meist Arial),
    nicht auf die Schrift des Blattes."""
    stil = STIL.read_text(encoding="utf-8")
    for sel in (".abschicken.portionen input", ".zugportionen input"):
        block = _block(stil, sel)
        assert "font-family: inherit" in block


def _bloecke(stil: str):
    """(Selektor, Deklarationen) für jeden Block im Blatt, ohne Kommentare."""
    ohne = re.sub(r"/\*.*?\*/", "", stil, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", ohne)


def test_der_primaere_knopf_ist_genau_eine_regel():
    """Sechs Knopfstile (UI-Review 2026-09-01, Fund 14). Die Regel: gefüllt
    ist die eine Handlung, für die die Seite da ist — Fragen, Abschicken,
    Hochladen. Drei Regeln sagten dasselbe mit 52 und 54 px; jetzt eine."""
    stil = STIL.read_text(encoding="utf-8")
    # `var(--akzent)` als Teilstring trifft auch `var(--akzent-hell)` und
    # `var(--akzent-tinte)` — beides gibt es im Blatt (die gewählte Rolle,
    # der Chip). Das Semikolon beendet die Marke, sonst meldete dieser Test
    # eines Tages einen zweiten Primärknopf, der keiner ist.
    gefuellt = sorted(sel.strip() for sel, dekl in _bloecke(stil)
                      if ("button" in sel or ".gross" in sel or ".knopf" in sel)
                      and re.search(r"background: var\(--akzent\)\s*;", dekl))
    assert gefuellt == [".abschicken .gross, .chatform button, .bonupload button"]


def test_der_sekundaere_knopf_ist_genau_eine_regel():
    """„Dazu" (`.freitext button`) war eine Kopie von „Suchen" — Zeile für
    Zeile dieselben Deklarationen unter zweitem Namen."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".suche button, .freitext button, .knopf, .rollen button")
    assert "border: 1.5px solid var(--knopflinie)" in block
    assert "background: var(--karte)" in block
    assert ".freitext button, .abschicken .gross {" not in stil


def test_die_gekappte_trefferzahl_ist_keine_warnung():
    """„60 von 10066" in Honig las sich als Warnung (Fund 15); es ist eine
    Zählung. Honig heisst in diesem Blatt „es fehlt etwas" — hier fehlt
    nichts, die Liste ist nur geschnitten."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".gekappt")
    assert "honig" not in block
    assert "var(--gedaempft)" in block


def test_die_wartezeile_im_chat_ist_lesbar():
    """„Das Modell überlegt …" in 13 px grau (Fund 16): die einzige Zeile,
    die während der Wartezeit etwas sagt, war die kleinste der Seite."""
    stil = STIL.read_text(encoding="utf-8")
    assert "font-size: var(--t-klein)" in _block(stil, ".chatkopf #chat-laeuft")


def test_ein_einzelner_zug_ohne_luecke_bekommt_einen_ganzen_satz(client, con):
    """„Alle 1 Züge tragen eine Span-ID." ist kein Deutsch (Fund 16)."""
    # Der Zug wird hier geschrieben statt über `POST /chat` erzeugt: gezählt
    # werden Züge MIT Span-ID, und ohne laufendes Tracing setzt der Weg über
    # die Oberfläche keine — dann stünde auf der Seite der Fehlerzweig und
    # der geprüfte Satz käme gar nicht vor.
    vorschlaege.nachricht(con, orders.warenkorb(con),
                          vorschlaege.ROLLE_NUTZERIN, "Milch",
                          span_id="span-1")
    # Geglättet, weil der Satz in der Vorlage umbricht — geprüft wird er
    # ganz, sonst bliebe offen, ob dahinter noch „Züge" steht.
    flach = " ".join(client.get("/status").text.split())
    assert "Der eine Zug trägt eine Span-ID." in flach
    assert "Alle 1 Züge" not in flach


def test_die_seiten_erklaeren_sich_nicht_mehr_selbst(client, con):
    """Anleitungsprosa in 13 px (UI-Review 2026-09-01, Fund 9), gestrichen
    nach eigenem Urteil; die Sätze stehen in der Commit-Nachricht."""
    # Ein geholtes Rezept: Zutatenliste, aber noch kein verknüpftes Produkt
    # (Bauart wie `test_portionen.py`). Nur so steht der leere Zustand aus
    # `_rezept.html` überhaupt auf der Seite — sonst prüfte der Test nichts.
    rid = recipes.anlegen(con, "Pho Bo", servings=4)
    for pos, name in enumerate(["Rinderbrühe", "Reisnudeln", "Ingwer"]):
        con.execute("INSERT INTO recipe_ingredient (recipe_id, pos, raw_name,"
                    " name) VALUES (?, ?, ?, ?)", (rid, pos, name, name))
    con.commit()
    # Mit `amount` in der Adresse, weil die Fussnote „Menge aus dem Rezept"
    # sonst gar nicht gerendert wird und der Satz darunter unprüfbar bliebe.
    rezept = client.get(f"/rezepte/{rid}?amount=500&unit=g").text
    assert "Noch kein Produkt verknüpft" in rezept
    assert "Menge aus dem Rezept" in rezept
    assert "Vorrang vor der Suche" not in rezept
    assert "wächst mit den Portionen" not in rezept
    assert "in der Zutatenliste" not in rezept
    # Der Hinweis am Portionsfeld hat zwei Fassungen, und die kurze darf die
    # lange nicht ersetzen: ohne `servings` rechnet nichts mit, und dann wäre
    # „alle Mengen rechnen mit" eine Zusage, die der Shop nicht hält.
    ohne = recipes.anlegen(con, "Handrezept")
    seite = client.get(f"/rezepte/{ohne}").text
    assert "ohne Zahl im Rezept gibt es keinen Faktor" in seite
    assert "alle Mengen rechnen mit" not in seite
    bons = client.get("/bons").text
    assert "Kartennummer" not in bons and "besser lesbar" not in bons
    status = client.get("/status").text
    assert "Magic Packet" not in status and "Warum eine Zeile leer blieb" not in status
    assert "Spec 5.1" not in client.get("/katalog").text
