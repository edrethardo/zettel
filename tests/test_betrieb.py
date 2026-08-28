"""Tests für Betrieb: Sicherung, Statusseite, systemd-Units (WB-331).

Ohne Netz und ohne systemd. Die Units werden als Text geprüft — das ist hier
angemessen und nicht faul: was an ihnen schiefgehen kann, ist genau eine
falsche Zeile (`python3` statt `.venv/bin/python`, ein fehlendes
`Persistent=true`), und dass sie syntaktisch gültig sind, sagt
`systemd-analyze verify` von Hand. Ein Test, der Dienste startet, würde auf
dieser Maschine echte Timer scharf machen.
"""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picknick import betrieb, db
from picknick.scrapers import begriffe as begriffsliste
from picknick.scrapers import knuspr, nachtlauf
from picknick.web import app as webapp

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"
FIXTURE = Path(__file__).parent / "fixtures" / "knuspr_milch.json"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"


class FakeHTTP:
    """Liefert die aufgezeichnete Antwort als erste Seite, danach nichts."""

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


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def db_datei(tmp_path):
    pfad = tmp_path / "picknick.db"
    con = db.connect(pfad)
    db.migrate(con)
    knuspr.crawl(con, FakeHTTP([_payload()]), ["milch"], pause_s=0)
    con.close()
    return pfad


@pytest.fixture
def client(db_datei, tmp_path):
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=bilder)) as c:
        yield c


# --------------------------------------------------------------------------
# Sicherung

def test_sicherung_ist_lesbar_und_hat_dieselben_daten(db_datei, tmp_path):
    """`VACUUM INTO` erzeugt eine eigenständige, vollständige Datei."""
    ziel, weg = betrieb.sichern(db_datei, tmp_path / "stände")
    assert ziel.is_file() and not weg

    original = db.connect(db_datei)
    kopie = sqlite3.connect(str(ziel))
    kopie.row_factory = sqlite3.Row
    try:
        erwartet = [tuple(r) for r in original.execute(
            "SELECT external_id, name, price_cents FROM product ORDER BY id")]
        tatsaechlich = [tuple(r) for r in kopie.execute(
            "SELECT external_id, name, price_cents FROM product ORDER BY id")]
    finally:
        original.close()
        kopie.close()
    assert erwartet == tatsaechlich
    assert len(erwartet) > 1
    assert MILCH in {name for _, name, _ in erwartet}


def test_sicherung_landet_ohne_angabe_neben_der_datenbank(db_datei, monkeypatch,
                                                          tmp_path):
    """Kein fester relativer Pfad: der zeigt dorthin, wo der Prozess steht.

    Beim Probestart der systemd-Unit sicherte ein `data/sicherungen` die
    Testdatenbank ins Projektverzeichnis (2026-08-28).
    """
    woanders = tmp_path / "woanders"
    woanders.mkdir()
    monkeypatch.chdir(woanders)
    ziel, _ = betrieb.sichern(db_datei)
    assert not (woanders / "sicherungen").exists()
    assert ziel.parent == db_datei.parent / "sicherungen"


def test_sicherung_ohne_datenbank_sagt_es(tmp_path):
    with pytest.raises(FileNotFoundError):
        betrieb.sichern(tmp_path / "gibtsnicht.db", tmp_path / "stände")


def test_achter_stand_raeumt_den_aeltesten_ab(db_datei, tmp_path):
    """Sieben Stände (Spec 12) — der achte verdrängt den ersten."""
    ziel_dir = tmp_path / "stände"
    angelegt = []
    for tag in range(1, 9):
        stand, weg = betrieb.sichern(
            db_datei, ziel_dir, stempel=f"2026-08-{tag:02d}T03-30-00")
        angelegt.append(stand)
        if tag <= betrieb.STAENDE:
            assert weg == [], f"nach {tag} Ständen darf nichts weg sein"

    assert weg == [angelegt[0]], "der ÄLTESTE muss weichen, nicht der neueste"
    assert not angelegt[0].exists()
    vorhanden = betrieb.staende(ziel_dir)
    assert len(vorhanden) == betrieb.STAENDE == 7
    assert vorhanden == angelegt[1:]


def test_abraeumen_fasst_fremde_dateien_nicht_an(db_datei, tmp_path):
    """Im Sicherungsverzeichnis wird gelöscht — aber nur, was von uns ist."""
    ziel_dir = tmp_path / "stände"
    for tag in range(1, 9):
        betrieb.sichern(db_datei, ziel_dir, stempel=f"2026-08-{tag:02d}T03-30-00")
    fremd = ziel_dir / "vor-dem-umzug.db"
    fremd.write_bytes(b"nicht von uns")
    betrieb.abraeumen(ziel_dir, behalten=1)
    assert fremd.exists()
    assert len(betrieb.staende(ziel_dir)) == 1


def test_zwei_sicherungen_in_derselben_sekunde(db_datei, tmp_path):
    """`VACUUM INTO` überschreibt nicht — der zweite Stand bekommt einen Namen."""
    ziel_dir = tmp_path / "stände"
    a, _ = betrieb.sichern(db_datei, ziel_dir, stempel="2026-08-28T03-30-00")
    b, _ = betrieb.sichern(db_datei, ziel_dir, stempel="2026-08-28T03-30-00")
    assert a != b and a.exists() and b.exists()


# --------------------------------------------------------------------------
# Statusbericht

def _lauf(pfad, **felder):
    con = db.connect(pfad)
    spalten = ", ".join(felder)
    platz = ", ".join("?" for _ in felder)
    con.execute(f"INSERT INTO scrape_run ({spalten}) VALUES ({platz})",
                tuple(felder.values()))
    con.commit()
    con.close()


def test_verworfener_lauf_steht_mit_begruendung_auf_der_statusseite(db_datei, client):
    grund = ("nur 3 Produkte gegenüber 900 im letzten guten Lauf "
             "(Schwelle 50%) — Katalog unverändert gelassen")
    _lauf(db_datei, source="knuspr", started_at="2026-08-27T03:30:00",
          finished_at="2026-08-27T03:31:00", status="rejected", n_products=3,
          error=grund)
    r = client.get("/status")
    assert r.status_code == 200
    assert "Verworfene" in r.text
    assert "rejected" in r.text
    # Die Begründung selbst, nicht bloss die Tatsache eines Fehlschlags.
    assert "im letzten guten Lauf" in r.text
    assert "2026-08-27T03:30:00" in r.text


def test_abgebrochener_lauf_ohne_status_faellt_auf(db_datei, client):
    """Ein Lauf ohne Abschluss ist der Fall, in dem ein Crawler still scheitert."""
    _lauf(db_datei, source="knuspr", started_at="2026-08-26T03:30:00")
    text = client.get("/status").text
    assert "ohne Abschluss" in text
    assert "abgebrochen" in text


def test_statusseite_ohne_jeden_lauf_sagt_etwas_sinnvolles(tmp_path):
    """Der Zustand direkt nach der Installation — Schweigen wäre irreführend."""
    pfad = tmp_path / "leer.db"
    con = db.connect(pfad)
    db.migrate(con)
    con.close()
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    with TestClient(webapp.create_app(db_path=pfad, image_dir=bilder)) as c:
        r = c.get("/status")
    assert r.status_code == 200
    assert "noch keinen erfolgreichen Lauf" in r.text
    assert "Noch kein Lauf verzeichnet" in r.text
    assert "Kein Lauf wurde verworfen." in r.text
    assert "0" in r.text


def test_statusseite_zeigt_produktzahl_und_letzten_guten_lauf(db_datei, client):
    con = db.connect(db_datei)
    n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                    ).fetchone()["n"]
    con.close()
    text = client.get("/status").text
    assert f"<strong>{n}</strong>" in text
    assert "Letzter erfolgreicher Lauf" in text


def test_statusbericht_zaehlt_nur_aktive_produkte(db_datei):
    con = db.connect(db_datei)
    con.execute("UPDATE product SET active = 0 WHERE id = (SELECT min(id) FROM product)")
    con.commit()
    n_aktiv = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                          ).fetchone()["n"]
    bericht = betrieb.statusbericht(con)
    con.close()
    assert bericht["produkte"] == n_aktiv
    assert bericht["letzter_ok"]["status"] == "ok"


# --------------------------------------------------------------------------
# Nachtlauf

def test_nachtlauf_crawlt_und_sichert_ohne_netz(tmp_path):
    """Derselbe Weg wie nachts, nur mit einem Doppelgänger statt knuspr.de."""
    pfad = tmp_path / "picknick.db"
    ziel_dir = tmp_path / "stände"
    ausgabe = []
    bericht = nachtlauf.lauf(str(pfad), begriffe=["milch"], image_dir=None,
                             http=FakeHTTP([_payload()]), pause_s=0,
                             sicherung_dir=str(ziel_dir), schreib=ausgabe.append)
    assert bericht["status"] == "ok"
    assert bericht["n_products"] > 1
    assert len(betrieb.staende(ziel_dir)) == 1
    assert any("Sicherung" in z for z in ausgabe)


def test_nachtlauf_sichert_auch_wenn_der_crawl_scheitert(tmp_path):
    """Die Sicherung schützt Bestellungen und Rezepte, nicht den Katalog."""
    class KaputtesHTTP:
        def get(self, url):
            raise OSError("Name or service not known")

    pfad = tmp_path / "picknick.db"
    ziel_dir = tmp_path / "stände"
    bericht = nachtlauf.lauf(str(pfad), begriffe=["milch"], image_dir=None,
                             http=KaputtesHTTP(), pause_s=0,
                             sicherung_dir=str(ziel_dir), schreib=lambda *_: None)
    assert bericht["status"] == "error"
    assert len(betrieb.staende(ziel_dir)) == 1


def test_nachtlauf_meldet_misserfolg_als_rueckgabewert(tmp_path, monkeypatch):
    """Ein Fehlschlag muss in `systemctl status` sichtbar sein, nicht nur in der DB."""
    pfad = tmp_path / "picknick.db"
    monkeypatch.setattr(nachtlauf, "lauf",
                        lambda *a, **k: {"status": "rejected", "run_id": 1,
                                         "n_products": 3, "error": "zu wenig"})
    assert nachtlauf.main(["--db", str(pfad), "--begriff", "milch"]) == 1
    monkeypatch.setattr(nachtlauf, "lauf",
                        lambda *a, **k: {"status": "ok", "run_id": 1,
                                         "n_products": 900, "error": None})
    assert nachtlauf.main(["--db", str(pfad), "--begriff", "milch"]) == 0


def test_user_agent_ist_ascii():
    """httpx kodiert Kopfzeilen als ASCII — ein Umlaut darin tötet den Lauf.

    Der Fehler passiert beim Bauen des Clients, also vor der ersten Anfrage:
    kein `scrape_run`-Eintrag, keine Begründung, nur ein Traceback im Journal.
    Genau so ist es am 2026-08-28 beim Probestart der Unit passiert.
    """
    nachtlauf.USER_AGENT.encode("ascii")


# --------------------------------------------------------------------------
# Begriffsliste

def test_begriffsliste_ist_breit_genug_fuer_einen_haushalt():
    """Der Katalog ist genau so breit wie diese Liste — sie ist der Umfang."""
    assert len(begriffsliste.BEGRIFFE) >= 100
    assert len(set(begriffsliste.BEGRIFFE)) == len(begriffsliste.BEGRIFFE)
    # Stichproben quer durch den Haushalt: Kühlregal, Vorrat, Reinigung,
    # Hygiene. Fehlt eine dieser Ecken, findet die Suche sie nie.
    for begriff in ("milch", "hackfleisch", "nudeln", "kartoffeln",
                    "toilettenpapier", "spuelmittel", "kaffee", "aepfel"):
        assert begriff in begriffsliste.BEGRIFFE


def test_begriffe_aus_umgebung_nimmt_datei_und_aufzaehlung(tmp_path):
    datei = tmp_path / "begriffe.txt"
    datei.write_text("milch\n# ein Kommentar\n\nbutter  # mit Rest\n",
                     encoding="utf-8")
    assert begriffsliste.begriffe_aus_umgebung(
        {"PICKNICK_BEGRIFFE": str(datei)}) == ["milch", "butter"]
    assert begriffsliste.begriffe_aus_umgebung(
        {"PICKNICK_BEGRIFFE": "milch, butter"}) == ["milch", "butter"]
    assert begriffsliste.begriffe_aus_umgebung({}) == list(begriffsliste.BEGRIFFE)
    with pytest.raises(ValueError):
        begriffsliste.begriffe_aus_umgebung({"PICKNICK_BEGRIFFE": " , "})


def test_begriffsliste_liegt_im_paket_und_nicht_im_skript():
    """WB-321 hatte den Ort offen gelassen: das Probeskript war der falsche."""
    assert (WURZEL / "picknick" / "scrapers" / "begriffe.py").is_file()


# --------------------------------------------------------------------------
# systemd-Units

@pytest.mark.parametrize("name", ["picknick.service", "picknick-crawl.service",
                                  "picknick-crawl.timer"])
def test_unit_existiert(name):
    assert (DEPLOY / name).is_file()


@pytest.mark.parametrize("name", ["picknick.service", "picknick-crawl.service"])
def test_unit_startet_das_venv_und_nicht_system_python(name):
    """System-Python hat ein zu altes `websockets`, der Shop startet dort nicht."""
    text = (DEPLOY / name).read_text(encoding="utf-8")
    zeilen = [z for z in text.splitlines() if z.startswith("ExecStart=")]
    assert len(zeilen) == 1
    (exec_start,) = zeilen
    assert exec_start.endswith(
        ("-m picknick.web.app", "-m picknick.scrapers.nachtlauf"))
    assert "/.venv/bin/python" in exec_start
    # Nicht bloss „enthält venv": ein zusätzliches nacktes python3 irgendwo im
    # Kommando wäre genau der Fehler, den dieser Test verhindern soll.
    assert " python3" not in exec_start and "=python3" not in exec_start
    assert "/usr/bin/python" not in exec_start


def test_timer_ist_persistent():
    """Ohne das fiele jeder Lauf aus, der in eine zugeklappte Nacht fällt."""
    text = (DEPLOY / "picknick-crawl.timer").read_text(encoding="utf-8")
    zeilen = [z.strip() for z in text.splitlines()]
    assert "Persistent=true" in zeilen
    assert any(z.startswith("OnCalendar=") for z in zeilen)
    assert "WantedBy=timers.target" in zeilen


def test_units_tragen_installationsabschnitte():
    """Ohne [Install] kann `systemctl --user enable` sie nicht einhängen."""
    web = (DEPLOY / "picknick.service").read_text(encoding="utf-8")
    assert "WantedBy=default.target" in web
    # Die Crawl-Unit ausdrücklich NICHT: sie wird vom Timer gestartet, ein
    # eigenes enable würde sie bei jedem Anmelden einmal loslaufen lassen.
    crawl = (DEPLOY / "picknick-crawl.service").read_text(encoding="utf-8")
    assert "[Install]" not in crawl


def test_readme_nennt_linger_und_die_installationsbefehle():
    """`Linger=no` heisst: der Dienst endet beim Abmelden (gemessen)."""
    readme = (WURZEL / "README.md").read_text(encoding="utf-8")
    assert "loginctl enable-linger user" in readme
    assert "systemctl --user enable --now picknick-crawl.timer" in readme
    assert "schläft" in readme
