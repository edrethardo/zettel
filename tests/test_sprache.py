"""Tests für die Sprachwahl der Oberfläche (2026-09-06).

Die Zusage, die hier abgesichert wird: **Deutsch bleibt die Vorgabe und
funktioniert vollständig**, Englisch kommt dazu und darf unvollständig sein,
ohne dass eine Seite kaputtgeht.
"""
import json
from pathlib import Path

import pytest

from zettel import sprache

TEXT_DIR = Path(sprache.TEXT_DIR)


@pytest.fixture(autouse=True)
def frisch():
    sprache.vergiss()
    yield
    sprache.vergiss()


class _Request:
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}


# --------------------------------------------------------------------------
# Der Rückfall — die wichtigste Eigenschaft

def test_fehlende_uebersetzung_faellt_auf_deutsch_zurueck(tmp_path, monkeypatch):
    monkeypatch.setattr(sprache, "TEXT_DIR", tmp_path)
    (tmp_path / "de.json").write_text(json.dumps({"a": "Korb", "b": "Chat"}),
                                      encoding="utf-8")
    (tmp_path / "en.json").write_text(json.dumps({"a": "Basket"}), encoding="utf-8")
    sprache.vergiss()
    t = sprache.uebersetzer("en")
    assert t("a") == "Basket"
    # Nicht „b" auf dem Bildschirm: ein deutscher Satz zwischen englischen ist
    # unvollständig, ein Schlüssel ist kaputt.
    assert t("b") == "Chat"


def test_unbekannter_schluessel_gibt_den_schluessel_und_wirft_nicht():
    t = sprache.uebersetzer("de")
    assert t("gibt.es.nicht") == "gibt.es.nicht"


def test_kaputte_datei_haelt_den_shop_nicht_an(tmp_path, monkeypatch):
    monkeypatch.setattr(sprache, "TEXT_DIR", tmp_path)
    (tmp_path / "de.json").write_text('{"a": "Korb"}', encoding="utf-8")
    (tmp_path / "en.json").write_text("{kaputt", encoding="utf-8")
    sprache.vergiss()
    assert sprache.uebersetzer("en")("a") == "Korb"


def test_platzhalter_werden_gefuellt(tmp_path, monkeypatch):
    monkeypatch.setattr(sprache, "TEXT_DIR", tmp_path)
    (tmp_path / "de.json").write_text(json.dumps({"n": "{n} Artikel"}),
                                      encoding="utf-8")
    sprache.vergiss()
    assert sprache.uebersetzer("de")("n", n=3) == "3 Artikel"


def test_fehlender_platzhalter_kostet_keine_seite(tmp_path, monkeypatch):
    monkeypatch.setattr(sprache, "TEXT_DIR", tmp_path)
    (tmp_path / "de.json").write_text(json.dumps({"n": "{n} Artikel"}),
                                      encoding="utf-8")
    sprache.vergiss()
    assert sprache.uebersetzer("de")("n") == "{n} Artikel"


# --------------------------------------------------------------------------
# Welche Sprache dieser Besuch will

def test_cookie_schlaegt_browser():
    r = _Request(cookies={sprache.COOKIE: "en"},
                 headers={"accept-language": "de-DE,de;q=0.9"})
    assert sprache.aus_request(r) == "en"


def test_ohne_cookie_entscheidet_der_browser():
    r = _Request(headers={"accept-language": "en-GB,en;q=0.9"})
    assert sprache.aus_request(r) == "en"


def test_unbekannte_browsersprache_wird_deutsch():
    assert sprache.aus_request(_Request(headers={"accept-language": "fr-FR"})) == "de"


def test_unsinniges_cookie_wird_deutsch():
    assert sprache.aus_request(_Request(cookies={sprache.COOKIE: "xx"})) == "de"


# --------------------------------------------------------------------------
# Die ausgelieferten Dateien

def test_deutsch_ist_vollstaendig_und_leer_ist_kein_text():
    de = sprache.lade("de")
    assert de, "de.json ist leer"
    leer = [s for s, wert in de.items() if not str(wert).strip()]
    assert leer == [], f"leere Texte in de.json: {leer}"


def test_englisch_hat_keine_ueberzaehligen_schluessel():
    # Ein Schlüssel, den es nur auf Englisch gibt, ist ein Tippfehler: er
    # wird nie gefunden, weil die Vorlage den anderen Namen benutzt.
    assert sprache.ueberzaehlige("en") == []


def test_alle_sprachen_haben_eine_datei():
    for code in sprache.SPRACHEN:
        assert (TEXT_DIR / f"{code}.json").is_file(), code


# --------------------------------------------------------------------------
# Der Weg durch die Oberfläche

@pytest.fixture
def client(leere_db_datei, tmp_path):
    from fastapi.testclient import TestClient

    from zettel.web import app as webapp
    with TestClient(webapp.create_app(db_path=leere_db_datei,
                                      image_dir=tmp_path / "bilder")) as c:
        yield c


def test_die_oberflaeche_ist_voreingestellt_deutsch(client):
    blatt = client.get("/mehr").text
    assert "Pick-Liste" in blatt and 'lang="de"' in blatt


def test_sprachwahl_setzt_das_cookie_und_kommt_zurueck(client):
    antwort = client.post("/sprache", params={"code": "en"},
                          headers={"referer": "/mehr"},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/mehr"
    assert client.cookies.get(sprache.COOKIE) == "en"


def test_nach_der_wahl_steht_die_leiste_englisch(client):
    client.post("/sprache", params={"code": "en"}, headers={"referer": "/mehr"})
    blatt = client.get("/mehr").text
    assert "Pick list" in blatt and "Pick-Liste" not in blatt
    assert 'lang="en"' in blatt


def test_ein_fremder_referer_fuehrt_nicht_aus_dem_tailnet_hinaus(client):
    antwort = client.post("/sprache", params={"code": "en"},
                          headers={"referer": "https://example.com/wohin"},
                          follow_redirects=False)
    assert antwort.headers["location"] == "/wohin"


def test_eine_unbekannte_sprache_aendert_nichts(client):
    client.post("/sprache", params={"code": "kl"}, headers={"referer": "/mehr"})
    assert client.cookies.get(sprache.COOKIE) is None
    assert "Pick-Liste" in client.get("/mehr").text
