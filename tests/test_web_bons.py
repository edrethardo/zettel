"""Tests für die Bon-Upload-Seite (WB-344).

Kein Netz, kein Browser, kein echtes `data/bons` — das Zielverzeichnis liegt
unter `tmp_path` und wird über `create_app(bon_dir=…)` untergeschoben. Ein Test,
der in das echte Verzeichnis schreibt, wäre nach dem dritten Lauf ein Archiv
fremder Dateien.

Die Prüfungen greifen bewusst am HTTP-Rand an und nicht an `picknick.bons`
allein: der Weg vom Formular durch den selbstgeschriebenen Multipart-Zerleger
ist genau das Stück, das niemand sonst abdeckt (`python-multipart` ist keine
Abhängigkeit, siehe `picknick/web/multipart.py`).
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picknick import bons, db
from picknick.web import app as webapp

# Kleinste gültige Rümpfe. Es geht um die ersten Bytes, nicht um Bildinhalt —
# genau deshalb reicht ein Kopf plus Füllung, und genau deshalb ist die
# Prüfung im Server auch keine Formatvalidierung, sondern eine Kennungsprüfung.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"0" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
TEXT = b"Das hier ist eine Textdatei und kein Bon.\n" * 4


@pytest.fixture
def bon_dir(tmp_path):
    return tmp_path / "bons"


@pytest.fixture
def client(tmp_path, bon_dir):
    pfad = tmp_path / "picknick.db"
    con = db.connect(pfad)
    db.migrate(con)
    con.close()
    with TestClient(webapp.create_app(db_path=pfad,
                                      image_dir=tmp_path / "bilder",
                                      bon_dir=bon_dir)) as c:
        yield c


def _lade(client, dateiname, inhalt, **kwargs):
    return client.post("/bons", files={"datei": (dateiname, inhalt)}, **kwargs)


def _dateien(bon_dir: Path):
    return sorted(p.name for p in bon_dir.iterdir()) if bon_dir.is_dir() else []


# --------------------------------------------------------------------------
# Annahme

def test_png_wird_angenommen_und_liegt_im_verzeichnis(client, bon_dir):
    r = _lade(client, "bon.png", PNG)
    assert r.status_code == 200            # nach der 303 auf /bons
    namen = _dateien(bon_dir)
    assert len(namen) == 1
    assert namen[0].endswith(".png")
    # Und zwar Byte für Byte das, was hochgeladen wurde: der Zerleger darf am
    # Ende kein CRLF der Grenze mitnehmen.
    assert (bon_dir / namen[0]).read_bytes() == PNG


def test_pdf_wird_angenommen(client, bon_dir):
    """Der eigentliche Grund für eine eigene Seite: die Werkbank nimmt kein PDF."""
    r = _lade(client, "ebon.pdf", PDF)
    assert r.status_code == 200
    namen = _dateien(bon_dir)
    assert namen and namen[0].endswith(".pdf")
    assert (bon_dir / namen[0]).read_bytes() == PDF


def test_jpeg_und_heic_werden_angenommen(client, bon_dir):
    _lade(client, "foto.jpg", JPEG)
    _lade(client, "IMG_0042.HEIC", HEIC)
    endungen = sorted(Path(n).suffix for n in _dateien(bon_dir))
    assert endungen == [".heic", ".jpg"]


# --------------------------------------------------------------------------
# Ablehnung — jede mit einem lesbaren Grund

def test_textdatei_mit_png_endung_wird_abgelehnt(client, bon_dir):
    """Inhalt schlägt Endung. Der Name ist eine Behauptung, kein Beleg."""
    r = _lade(client, "bon.png", TEXT)
    assert r.status_code == 400
    assert _dateien(bon_dir) == []
    assert "Inhalt der Datei, nicht ihr Name" in r.text


def test_ablehnung_nennt_die_erlaubten_formate(client):
    r = _lade(client, "bon.png", TEXT)
    for wort in ("PNG", "JPEG", "HEIC", "PDF"):
        assert wort in r.text


def test_ohne_datei_sagt_die_seite_was_fehlt(client, bon_dir):
    r = client.post("/bons", files={"datei": ("", b"")})
    assert r.status_code == 400
    assert "keine Datei ausgewählt" in r.text
    assert _dateien(bon_dir) == []


def test_leere_datei_wird_abgelehnt(client, bon_dir):
    r = _lade(client, "bon.png", b"")
    assert r.status_code == 400
    assert "leer" in r.text
    assert _dateien(bon_dir) == []


def test_groessengrenze_greift_und_nennt_den_grund(client, bon_dir):
    """Knapp zu gross: erst der Zerleger merkt es, die Antwort nennt beide Zahlen."""
    zu_gross = PNG + b"\x00" * (bons.MAX_BYTES + 1024)
    r = _lade(client, "riesig.png", zu_gross)
    assert r.status_code == 400
    assert "25 MB" in r.text                 # was erlaubt ist
    assert "25.0 MB gross" in r.text         # und was ankam
    assert _dateien(bon_dir) == []


def test_offensichtlich_zu_gross_wird_am_kopf_abgewiesen(client, bon_dir):
    """Deutlich zu gross: `Content-Length` genügt, der Rumpf wird nicht gelesen.

    Ein 300-MB-Video erst in den Speicher zu holen, um dann „zu gross" zu
    sagen, wäre die teuerste Art, dasselbe zu antworten. 413 statt 400, weil
    hier wirklich die Grösse und nicht der Inhalt der Grund ist.
    """
    zu_gross = PNG + b"\x00" * (bons.MAX_BYTES * 2)
    r = _lade(client, "video.png", zu_gross)
    assert r.status_code == 413
    assert "50 MB zu gross, erlaubt sind 25 MB" in r.text
    assert _dateien(bon_dir) == []


def test_knapp_unter_der_grenze_geht_noch_durch(client, bon_dir):
    """Die Grenze soll die Grenze sein und nicht ein Stück davor.

    Der Zuschlag in `app.MULTIPART_ZUSCHLAG` existiert genau dafür: die
    Formularköpfe wiegen mit, gehören aber nicht zur Datei.
    """
    knapp = PNG + b"\x00" * (bons.MAX_BYTES - len(PNG) - 1)
    r = _lade(client, "gross.png", knapp)
    assert r.status_code == 200
    assert len(_dateien(bon_dir)) == 1


# --------------------------------------------------------------------------
# Kein Ausbrechen aus dem Verzeichnis

@pytest.mark.parametrize("boeser_name", [
    "../../entwischt.png",
    "/etc/entwischt.png",
    "..\\..\\entwischt.png",
    "bon\x00.png",
    "....//entwischt.png",
])
def test_dateiname_kann_nicht_ausbrechen(client, bon_dir, tmp_path, boeser_name):
    r = _lade(client, boeser_name, PNG)
    assert r.status_code == 200
    namen = _dateien(bon_dir)
    assert len(namen) == 1
    assert "/" not in namen[0] and "\\" not in namen[0]
    assert "\x00" not in namen[0]
    assert ".." not in namen[0]
    # Und ausserhalb des Zielverzeichnisses ist nichts entstanden.
    aussen = [p.name for p in tmp_path.iterdir() if p.is_file()]
    assert "entwischt.png" not in aussen
    bon_dir.joinpath(namen[0]).unlink()


def test_loeschen_kann_nicht_ausbrechen(client, bon_dir, tmp_path):
    """Beim Löschen kommt der Name aus der URL — hier zählt es doppelt."""
    bon_dir.mkdir(parents=True, exist_ok=True)
    opfer = tmp_path / "nicht_anfassen.txt"
    opfer.write_text("bleibt", encoding="utf-8")
    r = client.post("/bons/..%2Fnicht_anfassen.txt/loeschen",
                    follow_redirects=False)
    assert r.status_code == 404
    assert opfer.exists()

    # Auch der direkt durchgereichte Name darf nichts löschen.
    assert bons.loeschen(bon_dir, "../nicht_anfassen.txt") is False
    assert opfer.exists()


# --------------------------------------------------------------------------
# Liste und Löschen

def test_liste_zeigt_hochgeladenes_mit_datum_und_groesse(client, bon_dir):
    _lade(client, "kassenbon.png", PNG)
    name = _dateien(bon_dir)[0]
    seite = client.get("/bons").text
    assert name in seite
    assert bons.groesse_text(len(PNG)) in seite
    # Ein Datum in der Zeile, nicht bloss der Dateiname.
    from datetime import datetime
    assert datetime.now().strftime("%d.%m.%Y") in seite


def test_loeschen_entfernt_den_bon(client, bon_dir):
    _lade(client, "weg.png", PNG)
    name = _dateien(bon_dir)[0]
    r = client.post(f"/bons/{name}/loeschen")
    assert r.status_code == 200
    assert _dateien(bon_dir) == []
    assert name not in client.get("/bons").text


def test_loeschen_eines_unbekannten_bons_meldet_es(client, bon_dir):
    bon_dir.mkdir(parents=True, exist_ok=True)
    r = client.post("/bons/gibtsnicht.png/loeschen")
    assert r.status_code == 404
    assert "nicht (mehr)" in r.text


def test_zwei_uploads_ueberschreiben_sich_nicht(client, bon_dir):
    """Zwei Fotos vom selben Bon heissen auf dem Telefon gleich."""
    _lade(client, "bon.png", PNG)
    _lade(client, "bon.png", PNG + b"\x01")
    assert len(_dateien(bon_dir)) == 2


# --------------------------------------------------------------------------
# Zugang und Oberfläche

def test_seite_ist_ohne_passwort_erreichbar(client):
    """Spec 10: der Rahmen ist das Tailnet, nicht eine Anmeldung.

    Kein Cookie, kein Kopf, keine Weiterleitung auf ein Anmeldeformular — und
    kein 401, das am Telefon als „Load failed" ankommt.
    """
    r = client.get("/bons", follow_redirects=False)
    assert r.status_code == 200
    assert "Kassenbons" in r.text
    for wort in ("passwort", "anmeld", "login"):
        assert wort not in r.text.lower()


def test_upload_geht_ohne_javascript(client):
    """Das Formular ist ein echtes Formular: method, action, enctype."""
    seite = client.get("/bons").text
    assert 'method="post"' in seite
    assert 'action="/bons"' in seite
    assert 'enctype="multipart/form-data"' in seite
    assert 'type="file"' in seite


def test_bons_stehen_in_der_navigation(client):
    assert '<a href="/bons">' in client.get("/katalog").text


def test_leere_seite_sagt_dass_nichts_da_ist(client):
    assert "Noch kein Bon" in client.get("/bons").text
