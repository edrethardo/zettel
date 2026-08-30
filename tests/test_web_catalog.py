"""Tests für Web-Grundgerüst und Katalogansicht (WB-323).

Kein Browser, kein Netz. Die Oberfläche wird über `fastapi.testclient` als
HTTP-Client geprüft; die Produkte kommen aus derselben aufgezeichneten
Knuspr-Antwort wie in `test_knuspr.py` und `test_catalog.py`, durch den echten
Crawler in eine Datei geschrieben (die Vorlage aus `conftest.py`) — die App
öffnet ihre eigene Verbindung, mit `:memory:` sähe sie eine leere Datenbank.
Der Crawl legt selbst einen `ok`-Lauf von heute an; der Katalog ist damit
frisch, solange ein Test nichts anderes einträgt.

Die Bindung wird an der Konfiguration geprüft, nicht am echten Socket: ein Test,
der wirklich auf 0.0.0.0 bindet, um zu sehen, dass es nicht geht, tut genau das,
was er verhindern soll.
"""
import ipaddress
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import db
from zettel.web import app as webapp

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HAFER = "Alpro Haferdrink Original VEGAN"


def _lauf(con, status, vor_tagen):
    """Trägt einen Crawl-Lauf mit einem Alter in Tagen ein."""
    stand = (datetime.now() - timedelta(days=vor_tagen)).strftime("%Y-%m-%dT%H:%M:%S")
    con.execute(
        "INSERT INTO scrape_run (source, started_at, finished_at, status,"
        " n_products) VALUES ('knuspr', ?, ?, ?, 1)", (stand, stand, status))
    con.commit()


@pytest.fixture
def bild_dir(tmp_path):
    d = tmp_path / "bilder"
    d.mkdir()
    return d


@pytest.fixture
def client(db_datei, bild_dir):
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=bild_dir)) as c:
        yield c


# --------------------------------------------------------------------------
# Katalogansicht

def test_startseite_zeigt_produkte(client):
    r = client.get("/")
    assert r.status_code == 200
    assert MILCH in r.text
    assert HAFER in r.text


def test_preis_wird_aus_cent_formatiert(client):
    assert "1,19 €" in client.get("/katalog").text


def test_gebindegroesse_steht_an_der_kachel(client):
    r = client.get("/katalog")
    kachel = r.text.split(MILCH, 1)[1][:400]
    assert "1 l" in kachel


def test_kategorien_stehen_in_der_seite(client):
    r = client.get("/katalog")
    assert "Milch, Molkerei &amp; Butter" in r.text
    assert "Frischmilch" in r.text or "Milch" in r.text


def test_suche_filtert_die_trefferliste(client):
    voll = client.get("/produkte").text
    assert MILCH in voll and HAFER in voll

    gefiltert = client.get("/produkte", params={"q": "landmilch"}).text
    assert MILCH in gefiltert
    assert HAFER not in gefiltert


def test_suche_ohne_treffer_sagt_es(client):
    r = client.get("/produkte", params={"q": "schraubenzieher"})
    assert r.status_code == 200
    assert "Nichts gefunden" in r.text


def test_kategoriewechsel_liefert_nur_das_teilstueck(client):
    r = client.get("/produkte", params={"l1": "Pflanzenbasiertes Kühlregal"})
    assert HAFER in r.text
    assert MILCH not in r.text
    # Ein HTMX-Austausch bringt keine zweite ganze Seite mit.
    assert "<html" not in r.text.lower()


def test_sonderzeichen_in_der_suche_werfen_keinen_fehler(client):
    for eingabe in ['"', "milch*", "'; DROP TABLE product; --", "😀"]:
        assert client.get("/produkte", params={"q": eingabe}).status_code == 200


# --------------------------------------------------------------------------
# Keine fremden Hosts im ausgelieferten HTML

@pytest.mark.parametrize("pfad", ["/katalog", "/produkte", "/rolle", "/pick"])
def test_kein_cdn_verweis_im_html(client, pfad):
    text = client.get(pfad).text.lower()
    assert "unpkg" not in text
    assert "cdn." not in text
    assert "//" not in re.sub(r"<!--.*?-->", "", text, flags=re.S).replace(
        "<!doctype html>", "")


def test_htmx_wird_lokal_ausgeliefert(client):
    assert 'src="/static/htmx.min.js"' in client.get("/katalog").text
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert "htmx" in r.text[:2000].lower()


# --------------------------------------------------------------------------
# Hinweisband (Spec 11)

def test_band_bei_altem_katalog(db_datei, bild_dir):
    con = db.connect(db_datei)
    con.execute("DELETE FROM scrape_run")
    _lauf(con, "ok", vor_tagen=10)
    con.close()
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        assert "Preise sind 10 Tage alt." in c.get("/katalog").text


def test_kein_band_bei_frischem_katalog(client):
    assert "Tage alt" not in client.get("/katalog").text


def test_verworfener_lauf_zaehlt_nicht_als_aktualisierung(db_datei, bild_dir):
    con = db.connect(db_datei)
    con.execute("DELETE FROM scrape_run")
    _lauf(con, "ok", vor_tagen=9)
    _lauf(con, "rejected", vor_tagen=0)     # heute, aber verworfen (Spec 5.3)
    con.close()
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        assert "Preise sind 9 Tage alt." in c.get("/katalog").text


def test_ohne_jeden_lauf_wird_das_gesagt(tmp_path, bild_dir):
    leer = tmp_path / "leer.db"
    with TestClient(webapp.create_app(leer, bild_dir)) as c:
        assert "noch nie erfolgreich aktualisiert" in c.get("/katalog").text


def test_hinweis_grenze_liegt_bei_drei_tagen(db_datei):
    con = db.connect(db_datei)
    con.execute("DELETE FROM scrape_run")
    _lauf(con, "ok", vor_tagen=3)
    assert webapp.katalog_hinweis(con) is None
    con.execute("DELETE FROM scrape_run")
    _lauf(con, "ok", vor_tagen=4)
    assert webapp.katalog_hinweis(con) == "Preise sind 4 Tage alt."
    con.close()


# --------------------------------------------------------------------------
# Rollen-Cookie (Spec 10) — Startansicht, ausdrücklich keine Anmeldung

def test_ohne_cookie_faengt_es_beim_katalog_an(client):
    r = client.get("/")
    assert r.url.path == "/katalog"


def test_cookie_sie_fuehrt_zum_katalog(client):
    client.cookies.set(webapp.COOKIE_ROLLE, "sie")
    r = client.get("/")
    assert r.url.path == "/katalog"
    assert MILCH in r.text


def test_cookie_er_fuehrt_zur_pickliste(client):
    client.cookies.set(webapp.COOKIE_ROLLE, "er")
    r = client.get("/")
    assert r.url.path == "/pick"
    # Die Pick-Liste gibt es noch nicht — sie muss trotzdem zurückführen.
    assert "/katalog" in r.text


def test_rollenwahl_setzt_das_cookie(client):
    r = client.post("/rolle?wer=er", follow_redirects=False)
    assert r.status_code == 303
    assert client.cookies.get(webapp.COOKIE_ROLLE) == "er"
    assert client.get("/").url.path == "/pick"


def test_unbekannte_rolle_setzt_nichts(client):
    client.post("/rolle?wer=hausmeister", follow_redirects=False)
    assert client.cookies.get(webapp.COOKIE_ROLLE) is None


# --------------------------------------------------------------------------
# Bindung — die einzige Sicherheitsgrenze (Spec 10)

@pytest.mark.parametrize("host", ["0.0.0.0", "::", "0:0:0:0:0:0:0:0"])
def test_bindung_auf_alle_schnittstellen_wird_verweigert(host):
    with pytest.raises(webapp.UnsichereBindung):
        webapp.pruefe_host(host)


@pytest.mark.parametrize("host", [
    "192.168.2.163",        # LAN — im fremden WLAN erreichbar
    "10.0.0.5",
    "irgendein-laptop",     # ein Name kann sich auf alles auflösen
    "",
    "  ",
])
def test_fremde_adressen_werden_verweigert(host):
    with pytest.raises(webapp.UnsichereBindung):
        webapp.pruefe_host(host)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1",
                                  "100.101.102.103", "100.64.0.1"])
def test_loopback_und_tailnet_sind_erlaubt(host):
    assert webapp.pruefe_host(host) == host


def test_vorgabe_bindet_nicht_auf_alle_schnittstellen():
    """Die Vorgabe wird seit WB-388 erfragt statt hinterlegt — was auch
    immer die Erfragung liefert: loopback ist dabei, 0.0.0.0 nie, und alles
    andere liegt im Tailnet."""
    hosts = webapp.hosts_aus_umgebung({})
    assert "0.0.0.0" not in hosts
    assert hosts[-1] == "127.0.0.1"
    for host in hosts[:-1]:
        assert ipaddress.ip_address(host) in webapp.TAILNET


def test_vorgabe_ist_tailnet_plus_loopback(monkeypatch):
    """Mit Tailnet-Adresse dieselbe Zusammensetzung und Reihenfolge wie die
    frühere feste Vorgabe — ein Neustart ändert die Adressen nicht."""
    monkeypatch.setattr(webapp, "eigene_tailnet_adresse", lambda: "100.64.0.7")
    assert webapp.hosts_aus_umgebung({}) == ["100.64.0.7", "127.0.0.1"]


def test_vorgabe_ohne_tailnet_ist_nur_loopback(monkeypatch):
    monkeypatch.setattr(webapp, "eigene_tailnet_adresse", lambda: None)
    assert webapp.hosts_aus_umgebung({}) == ["127.0.0.1"]


def test_tailnet_erkennung_liefert_tailnet_oder_nichts():
    """Die echte Erfragung, gegen die echte Maschine: entweder eine Adresse
    aus 100.64.0.0/10 oder None — nie etwas Drittes, das dann gebunden
    würde."""
    adresse = webapp.eigene_tailnet_adresse()
    assert adresse is None or ipaddress.ip_address(adresse) in webapp.TAILNET


def test_umgebung_kann_die_adresse_setzen():
    assert webapp.hosts_aus_umgebung(
        {"ZETTEL_HOST": "100.64.9.9"}) == ["100.64.9.9"]


@pytest.mark.parametrize("wert", ["0.0.0.0", "127.0.0.1,0.0.0.0", ""])
def test_umgebung_kann_die_grenze_nicht_aushebeln(wert):
    with pytest.raises(webapp.UnsichereBindung):
        webapp.hosts_aus_umgebung({"ZETTEL_HOST": wert})


def test_sockets_bauen_lehnt_ab_bevor_etwas_lauscht(monkeypatch):
    """Geprüft wird, dass gar kein Socket entsteht — nicht der echte Port."""
    gebaut = []
    monkeypatch.setattr(webapp.socket, "socket",
                        lambda *a, **k: gebaut.append(a) or (_ for _ in ()).throw(
                            AssertionError("es wurde ein Socket geöffnet")))
    with pytest.raises(webapp.UnsichereBindung):
        webapp.sockets_bauen(["127.0.0.1", "0.0.0.0"], 8730)
    assert gebaut == []


def test_serve_startet_nicht_auf_alle_schnittstellen():
    with pytest.raises(webapp.UnsichereBindung):
        webapp.serve(hosts=["0.0.0.0"], port=8730)


# --------------------------------------------------------------------------
# Produktbilder

def test_kachel_zeigt_das_bild_wenn_die_datei_da_ist(db_datei, bild_dir):
    con = db.connect(db_datei)
    row = con.execute("SELECT id, image_path FROM product WHERE name = ?",
                      (MILCH,)).fetchone()
    (bild_dir / Path(row["image_path"]).name).write_bytes(b"\xff\xd8\xff-kein-echtes-jpeg")
    con.close()
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        assert f'src="/bild/{row["id"]}"' in c.get("/katalog").text
        antwort = c.get(f"/bild/{row['id']}")
        assert antwort.status_code == 200
        assert antwort.content.startswith(b"\xff\xd8\xff")


def test_ohne_bilddatei_bleibt_die_kachel_heil(client):
    text = client.get("/katalog").text
    assert MILCH in text
    assert "bild-fehlt" in text
    assert "<img" not in text


def test_bild_route_liefert_404_statt_einer_fremden_datei(client, bild_dir):
    (bild_dir.parent / "geheim.txt").write_text("nicht ausliefern")
    con = db.connect(client.app.state.db_path)
    con.execute("UPDATE product SET image_path = '../geheim.txt' WHERE name = ?",
                (MILCH,))
    con.commit()
    pid = con.execute("SELECT id FROM product WHERE name = ?",
                      (MILCH,)).fetchone()["id"]
    con.close()
    assert client.get(f"/bild/{pid}").status_code == 404


def test_bild_route_bei_unbekanntem_produkt(client):
    assert client.get("/bild/999999").status_code == 404


# --------------------------------------------------------------------------
# Die Liste sagt, dass sie geschnitten wurde (WB-375)
#
# Der Schnitt bei `SEITE` ist Absicht — auf dem Telefon scrollt niemand durch
# 657 Kacheln. Ihn zu verschweigen ist es nicht: „Milch" hatte am echten
# Katalog 657 Treffer und zeigte 60, und 44 der 138 Kategorien trugen ein
# Abzeichen, hinter dem weniger stand, als es versprach.

@pytest.fixture
def enge_seite(monkeypatch):
    """`SEITE` klein genug, dass die Fixture-Produkte sie überschreiten.

    `_liste` liest die Zahl beim Aufruf aus dem Modul, nicht beim Bauen der
    App — ein Ersetzen hier wirkt also auch für einen längst gebauten Client.
    """
    monkeypatch.setattr(webapp, "SEITE", 3)
    return 3


def test_gekappte_suche_nennt_die_wahre_zahl(client, enge_seite):
    r = client.get("/produkte", params={"q": "milch"})
    assert "3 von 26" in r.text
    assert "milch" in r.text


def test_gekappte_kategorie_nennt_die_zahl_die_am_zweig_steht(client,
                                                              enge_seite):
    """Die schlimmere Hälfte: das Abzeichen am Zweig versprach 8, der Zweig
    lieferte 3. Die Zahl im Satz muss dieselbe sein wie die am Abzeichen."""
    zweig = "Milch, Molkerei & Butter"
    liste = client.get("/produkte", params={"l1": zweig}).text
    treffer = re.search(r"(\d+) von (\d+)", liste)
    assert treffer, "die gekappte Kategorie schweigt"
    assert int(treffer.group(1)) == 3

    # Dieselbe Zahl, die im Kategoriebaum als Abzeichen hängt.
    baum = client.get("/katalog").text
    abzeichen = baum.split(zweig.replace("&", "&amp;"), 1)[1][:200]
    assert f'<span class="anzahl">{treffer.group(2)}</span>' in abzeichen


def test_eine_vollstaendige_liste_schweigt(client):
    """Kein Satz, wo nichts fehlt — sonst liest ihn irgendwann niemand mehr."""
    assert " von 26" not in client.get("/produkte", params={"q": "milch"}).text
    assert " von " not in client.get("/produkte").text.split("<ul", 1)[0]


def test_die_gekappte_suche_zaehlt_nicht_die_ausgeschlossenen_kategorien(
        db_datei, bild_dir, enge_seite):
    """Tierfutter ist aus dem Katalog verbannt (WB-343). Es in der Gesamtzahl
    mitzuzählen wäre eine zweite Falschaussage an derselben Stelle."""
    con = db.connect(db_datei)
    con.execute("UPDATE product SET category_l1 = ? WHERE id IN"
                " (SELECT id FROM product LIMIT 5)",
                (next(iter(webapp.search.AUSGESCHLOSSENE_KATEGORIEN)),))
    con.commit()
    vorher = webapp.search.count(con, "milch")
    con.close()
    with TestClient(webapp.create_app(db_path=db_datei,
                                      image_dir=bild_dir)) as c:
        text = c.get("/produkte", params={"q": "milch"}).text
    assert f"3 von {vorher}" in text
    assert vorher < 26
