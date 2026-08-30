"""Tests für das Auslesen eines Bons am HTTP-Rand (WB-358).

Kein Netz, kein Modell, keine Box, kein Thread:

* das Modell ist ein Fake mit fester Antwort (wie in `test_web_chat.py`),
* der Weckzustand wird untergeschoben,
* und die Läufe werden mit `bons.sofort` SYNCHRON ausgeführt. Ein Test, der
  auf einen Hintergrund-Thread wartet, ist ein Test, der irgendwann flackert.
  Dass es im Betrieb wirklich ein Thread ist, prüft `test_bons_lauf.py`.

Der Bon ist der NACHGEBAUTE aus `tests/fixtures/` — die echten unter
`data/bons/` sind gitignored und gehören einer realen Person.
"""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from zettel import bons, db
from zettel.bons import lesen, zuordnung
from zettel.llm import wake
from zettel.llm.client import Antwort
from zettel.web import app as webapp

HTMX = {"HX-Request": "true"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

kein_pdftotext = pytest.mark.skipif(
    not lesen.werkzeug_da(lesen.PDFTOTEXT),
    reason="pdftotext fehlt (poppler-utils)")

PRODUKTE = [
    ("Miil Kräuterquark 40%", 129),
    ("Bergbauern Brotzeit Käse", 249),
]


class FakeLLM:
    def __init__(self, *antworten):
        self.antworten = list(antworten)

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        if not self.antworten:
            raise AssertionError("Mehr Modellaufrufe als Antworten.")
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def __init__(self, zustand=wake.BEDIENT, grund=None):
        self._zustand = zustand
        self._grund = grund

    def zustand(self):
        if self._zustand == wake.BEDIENT:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(self._zustand, seit_s=5.0, grund=self._grund)


DEUTUNG = json.dumps({"zeilen": [
    {"bon": "KRAEUTERQUARK", "artikel": True, "klartext": "Kräuterquark",
     "suchbegriffe": ["Kräuterquark", "Quark"]},
    {"bon": "BROTZEIT KAESE", "artikel": True, "klartext": "Brotzeitkäse",
     "suchbegriffe": ["Brotzeit Käse", "Käse"]},
    {"bon": "SCHOKO-KEKSE", "artikel": True, "klartext": "Schokokekse",
     "suchbegriffe": ["Schokokekse"]},
]}, ensure_ascii=False)


def _produkte(con):
    for i, (name, preis) in enumerate(PRODUKTE, start=1):
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            "                     unit_text, category_l1, category_l2,"
            "                     category_l3)"
            " VALUES ('knuspr', ?, ?, ?, '250 g', 'Molkerei', 'Käse', 'Käse')",
            (str(200 + i), name, preis))
    con.commit()


@pytest.fixture
def db_datei(vorlagen, tmp_path):
    """Die zwei Produkte von oben — einmal gebaut, hier kopiert (conftest.py).

    Der eigene Vorlagenname: `db_datei` heisst in den anderen Web-Tests der
    volle Katalog, hier sind es genau die beiden Zeilen, die der nachgebaute
    Bon nennt.
    """
    return vorlagen.datei(tmp_path / "zettel.db", "bonlesen_produkte",
                          _produkte)


@pytest.fixture
def bon_dir(tmp_path):
    return tmp_path / "bons"


@pytest.fixture
def bau(db_datei, tmp_path, bon_dir):
    """Baut einen Client — mit wählbarer Modellantwort und wählbarem Starter."""
    def _bau(*antworten, box=None, starter=bons.sofort):
        app = webapp.create_app(
            db_path=db_datei, image_dir=tmp_path / "bilder", bon_dir=bon_dir,
            zuordner=zuordnung.Zuordner(FakeLLM(*antworten),
                                        wecker=box or Box()),
            bonlaeufe=bons.Laeufe(starter=starter))
        return TestClient(app)
    return _bau


@pytest.fixture
def client(bau):
    return bau(DEUTUNG)


def _lade(client, name, inhalt):
    client.post("/bons", files={"datei": (name, inhalt)})
    con = db.connect(client.app.state.db_path)
    con.close()
    return sorted(p.name for p in client.app.state.bon_dir.iterdir())[-1]


def _con(client):
    return db.connect(client.app.state.db_path)


def _zeilen(client):
    con = _con(client)
    try:
        beleg = bons.belege(con)[0]
        return bons.posten(con, beleg["id"])
    finally:
        con.close()


def _preise(client):
    con = _con(client)
    try:
        return bons.echte_preise(con)
    finally:
        con.close()


# --------------------------------------------------------------------------
# Der PDF-Weg von Ende zu Ende

@kein_pdftotext
def test_ohne_lauf_sagt_die_liste_dass_nichts_gelesen_ist(client, bon_pdf):
    _lade(client, "ebon.pdf", bon_pdf)
    seite = client.get("/bons").text
    assert "noch nicht ausgelesen" in seite
    assert "Auslesen" in seite


@kein_pdftotext
def test_auslesen_legt_posten_mit_preis_und_datum_an(client, bon_pdf):
    name = _lade(client, "ebon.pdf", bon_pdf)
    r = client.post(f"/bons/{name}/auslesen")
    assert r.status_code == 200                 # nach der 303 auf /bons/<name>

    zeilen = _zeilen(client)
    assert len(zeilen) == 6
    kekse = next(z for z in zeilen if z["bon_text"] == "SCHOKO-KEKSE")
    assert (kekse["qty"], kekse["total_cents"]) == (2, 200)

    con = _con(client)
    try:
        beleg = bons.belege(con)[0]
    finally:
        con.close()
    assert (beleg["store"], beleg["bought_on"]) == ("rewe", "2026-03-04")


@kein_pdftotext
def test_zuordnung_steht_da_und_ist_nicht_bestaetigt(client, bon_pdf):
    name = _lade(client, "ebon.pdf", bon_pdf)
    seite = client.post(f"/bons/{name}/auslesen").text

    assert "KRAEUTERQUARK" in seite
    assert "Miil Kräuterquark" in seite
    assert "Ja" in seite and "Nein" in seite
    # Der Kauf zählt noch nicht — nichts wurde bestätigt.
    assert _preise(client) == []
    assert "6 offen" in seite


@kein_pdftotext
def test_erst_ja_macht_daraus_einen_echten_preis(client, bon_pdf):
    name = _lade(client, "ebon.pdf", bon_pdf)
    client.post(f"/bons/{name}/auslesen")
    zeile = next(z for z in _zeilen(client) if z["bon_text"] == "KRAEUTERQUARK")

    r = client.post(f"/bons/posten/{zeile['id']}/entscheiden?decision=kept",
                    headers=HTMX)
    assert r.status_code == 200
    assert "bestätigt" in r.text

    preise = _preise(client)
    assert len(preise) == 1
    assert (preise[0]["store"], preise[0]["bought_on"],
            preise[0]["total_cents"]) == ("rewe", "2026-03-04", 129)


@kein_pdftotext
def test_nein_verwirft_ohne_kauf(client, bon_pdf):
    name = _lade(client, "ebon.pdf", bon_pdf)
    client.post(f"/bons/{name}/auslesen")
    zeile = next(z for z in _zeilen(client) if z["bon_text"] == "KRAEUTERQUARK")

    r = client.post(f"/bons/posten/{zeile['id']}/entscheiden?decision=removed",
                    headers=HTMX)
    assert r.status_code == 200
    assert "verworfen" in r.text
    assert _preise(client) == []


@kein_pdftotext
def test_ja_zu_einer_zeile_ohne_produkt_wird_erklaert(client, bon_pdf):
    """`APFELSAFT NATUR` hat keinen Treffer — „Ja" dazu wäre „Ja" zu nichts."""
    name = _lade(client, "ebon.pdf", bon_pdf)
    client.post(f"/bons/{name}/auslesen")
    zeile = next(z for z in _zeilen(client)
                 if z["bon_text"] == "APFELSAFT NATUR")
    r = client.post(f"/bons/posten/{zeile['id']}/entscheiden?decision=kept",
                    headers=HTMX)
    assert r.status_code == 400
    assert "keinem Produkt zugeordnet" in r.text
    assert _preise(client) == []


@kein_pdftotext
def test_korrigieren_setzt_ein_anderes_produkt(client, bon_pdf):
    name = _lade(client, "ebon.pdf", bon_pdf)
    client.post(f"/bons/{name}/auslesen")
    zeile = next(z for z in _zeilen(client) if z["bon_text"] == "KRAEUTERQUARK")

    # Erst die Kandidaten holen — dieselbe Suche wie im Katalog, ohne Modell.
    r = client.get(f"/bons/posten/{zeile['id']}/suche?q=Brotzeit", headers=HTMX)
    assert r.status_code == 200
    assert "Bergbauern Brotzeit Käse" in r.text

    con = _con(client)
    try:
        pid = con.execute("SELECT id FROM product WHERE name LIKE 'Bergbauern%'"
                          ).fetchone()["id"]
    finally:
        con.close()
    r = client.post(
        f"/bons/posten/{zeile['id']}/korrigieren?produkt_id={pid}",
        headers=HTMX)
    assert r.status_code == 200

    neu = next(z for z in _zeilen(client) if z["bon_text"] == "KRAEUTERQUARK")
    assert neu["product_id"] == pid
    assert neu["bestaetigt"]


@kein_pdftotext
def test_neu_lesen_verdoppelt_die_kaeufe_nicht(client, bau, bon_pdf):
    client = bau(DEUTUNG, DEUTUNG)
    name = _lade(client, "ebon.pdf", bon_pdf)
    client.post(f"/bons/{name}/auslesen")
    client.post(f"/bons/{name}/auslesen")
    con = _con(client)
    try:
        assert len(bons.belege(con)) == 1
        assert con.execute("SELECT count(*) FROM receipt_item").fetchone()[0] == 6
    finally:
        con.close()


# --------------------------------------------------------------------------
# Wenn etwas nicht geht

def test_bild_ohne_ocr_bricht_die_seite_nicht(client, monkeypatch):
    """Ohne tesseract bekommt ein Bild gar keinen Auslesen-Knopf.

    Und die Seite sagt in einem Satz, was fehlt und was es braucht — statt
    einen Knopf anzubieten, der verlässlich scheitert.
    """
    monkeypatch.setattr(shutil, "which",
                        lambda n: None if n == lesen.TESSERACT else f"/usr/bin/{n}")
    name = _lade(client, "foto.png", PNG)
    seite = client.get("/bons").text
    assert seite.count("Auslesen") == 0
    assert "tesseract-ocr" in seite
    assert name in seite                       # der Upload bleibt heil


def test_bild_ausgelesen_meldet_die_fehlende_ocr(client, monkeypatch):
    """Wer den Knopf trotzdem trifft (alte Seite, zweiter Tab), bekommt Text."""
    monkeypatch.setattr(shutil, "which",
                        lambda n: None if n == lesen.TESSERACT else f"/usr/bin/{n}")
    name = _lade(client, "foto.png", PNG)
    r = client.post(f"/bons/{name}/auslesen")
    assert r.status_code == 200
    assert "tesseract-ocr" in r.text
    # Kein Beleg, keine halbe Kaufhistorie.
    con = _con(client)
    try:
        assert bons.belege(con) == []
    finally:
        con.close()
    assert client.get("/bons").status_code == 200


@kein_pdftotext
def test_unbekanntes_format_wird_verstaendlich_gemeldet(client):
    from conftest import baue_pdf
    name = _lade(client, "rechnung.pdf",
                 baue_pdf("Sehr geehrte Damen und Herren,\n\n"
                          "anbei unsere Rechnung.\n"))
    r = client.post(f"/bons/{name}/auslesen")
    assert r.status_code == 200
    assert "kein Kassenbon" in r.text
    con = _con(client)
    try:
        assert bons.belege(con) == []
    finally:
        con.close()


@kein_pdftotext
def test_schlafende_box_kostet_die_kaeufe_nicht(bau, bon_pdf):
    """Ohne Modell steht der Beleg trotzdem da — mit Preis und Datum."""
    client = bau(box=Box(wake.WACHT_AUF, grund="vLLM lädt die Gewichte."))
    name = _lade(client, "ebon.pdf", bon_pdf)
    seite = client.post(f"/bons/{name}/auslesen").text
    assert "Zuordnung zum Katalog fehlt noch" in seite
    zeilen = _zeilen(client)
    assert len(zeilen) == 6
    assert all(z["product_id"] is None for z in zeilen)
    assert all(z["offen"] for z in zeilen)


def test_auslesen_eines_unbekannten_bons_meldet_es(client, bon_dir):
    bon_dir.mkdir(parents=True, exist_ok=True)
    r = client.post("/bons/gibtsnicht.pdf/auslesen")
    assert r.status_code == 404
    assert "nicht (mehr)" in r.text


# --------------------------------------------------------------------------
# Der Lauf blockiert den Request nicht

@kein_pdftotext
def test_laufender_lauf_antwortet_sofort_und_fragt_nach(bau, bon_pdf):
    """Der Request startet und wartet nicht.

    `starter` tut hier NICHTS — der Lauf bleibt also für immer im Zustand
    „läuft". Genau das ist der Punkt: die Route antwortet trotzdem, und die
    Antwort trägt den Auslöser für die nächste Nachfrage.
    """
    client = bau(DEUTUNG, starter=lambda fn: None)
    name = _lade(client, "ebon.pdf", bon_pdf)
    r = client.post(f"/bons/{name}/auslesen")
    assert r.status_code == 200
    assert "Wird ausgelesen" in r.text
    assert f"/bons/{name}/stand" in r.text
    assert "hx-trigger" in r.text
    # Und die Liste bietet keinen zweiten Knopf an, solange etwas läuft.
    assert "wird ausgelesen" in client.get("/bons").text


@kein_pdftotext
def test_fertiger_lauf_fragt_nicht_weiter_nach(client, bon_pdf):
    name = _lade(client, "ebon.pdf", bon_pdf)
    client.post(f"/bons/{name}/auslesen")
    r = client.get(f"/bons/{name}/stand", headers=HTMX)
    assert r.status_code == 200
    assert "hx-trigger" not in r.text
