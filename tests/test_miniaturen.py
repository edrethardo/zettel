"""Tests für die Miniaturen und den Bildweg (WB-374).

Kein Browser und kein Telefon — geprüft wird, was am HTTP-Rand messbar ist:
welche Datei ausgeliefert wird, wieviele Bytes das sind und ob eine bedingte
Anfrage `304` bekommt. Die Originale des echten Bestands werden hier nie
angefasst; jeder Test arbeitet in seinem eigenen `tmp_path`.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from zettel import db, miniaturen
from zettel.web import app as webapp

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"


@pytest.fixture
def bild_dir(tmp_path):
    d = tmp_path / "bilder"
    d.mkdir()
    return d


def _foto(ziel: Path, groesse=(2000, 2000), modus="RGB", format="JPEG") -> Path:
    """Ein Original in der Grössenordnung des echten Bestands.

    Ein hochskaliertes Rauschfeld statt einer Fläche: eine einfarbige
    2000×2000-Datei komprimiert auf wenige Kilobyte, und jede Aussage über die
    Ersparnis wäre dann Selbsttäuschung. Skaliert und nicht rein zufällig, weil
    reines Pixelrauschen das andere Extrem ist und sich weder als JPEG noch als
    Miniatur so verhält wie ein Produktfoto. Fester Startwert — die Tests
    vergleichen Grössen.
    """
    import random
    zufall = random.Random(4)
    kanaele = len(Image.new(modus, (1, 1)).getbands())
    roh = Image.frombytes(
        modus, (64, 64),
        bytes(zufall.randrange(256) for _ in range(64 * 64 * kanaele)))
    roh.resize(groesse, Image.BICUBIC).save(ziel, format)
    return ziel


def _produkt_mit_bild(db_datei, bild_dir, *, name=MILCH, format="JPEG"):
    """Legt die Bilddatei zu einem Produkt an und gibt dessen id zurück."""
    con = db.connect(db_datei)
    row = con.execute("SELECT id, image_path FROM product WHERE name = ?",
                      (name,)).fetchone()
    con.close()
    ziel = bild_dir / Path(row["image_path"]).name
    _foto(ziel, format=format)
    return row["id"], ziel


# --------------------------------------------------------------------------
# Das Ableiten

def test_miniatur_ist_klein_und_passt_in_die_kachel(tmp_path):
    quelle = _foto(tmp_path / "gross.jpg")
    ziel = tmp_path / "mini" / "gross.jpg.webp"
    assert miniaturen.ableiten(quelle, ziel) is True
    with Image.open(ziel) as bild:
        assert bild.format == "WEBP"
        assert max(bild.size) == miniaturen.KANTE
    assert ziel.stat().st_size < quelle.stat().st_size / 10


def test_seitenverhaeltnis_bleibt_erhalten(tmp_path):
    quelle = _foto(tmp_path / "hoch.jpg", groesse=(700, 1400))
    ziel = tmp_path / "hoch.webp"
    assert miniaturen.ableiten(quelle, ziel) is True
    with Image.open(ziel) as bild:
        assert bild.size == (miniaturen.KANTE // 2, miniaturen.KANTE)


def test_transparenz_ueberlebt(tmp_path):
    """Ein Teil des Bestands sind PNGs mit Alphakanal — die dürfen nicht
    schwarz hinterlegt ankommen."""
    quelle = _foto(tmp_path / "durchsichtig.png", groesse=(400, 400),
                   modus="RGBA", format="PNG")
    ziel = tmp_path / "durchsichtig.webp"
    assert miniaturen.ableiten(quelle, ziel) is True
    with Image.open(ziel) as bild:
        assert bild.mode in ("RGBA", "LA", "P")


def test_kaputte_datei_scheitert_ohne_zu_werfen(tmp_path):
    quelle = tmp_path / "kaputt.jpg"
    quelle.write_bytes(b"\xff\xd8\xff-kein-echtes-jpeg")
    ziel = tmp_path / "mini" / "kaputt.jpg.webp"
    assert miniaturen.ableiten(quelle, ziel) is False
    assert not ziel.exists()
    # Auch keine halbe Datei, die beim nächsten Lauf als fertig gilt.
    assert list(ziel.parent.glob("*.teil")) == []


def test_lauf_zieht_nur_das_fehlende_nach(tmp_path):
    for i in range(3):
        _foto(tmp_path / f"b{i}.jpg")
    erster = miniaturen.lauf(tmp_path, schreib=None)
    assert erster["gemacht"] == 3
    assert erster["gescheitert"] == 0
    zweiter = miniaturen.lauf(tmp_path, schreib=None)
    assert zweiter["gemacht"] == 0
    assert zweiter["uebersprungen"] == 3
    assert zweiter["bytes_mini"] < zweiter["bytes_original"] / 10


def test_lauf_laesst_die_originale_unangetastet(tmp_path):
    quelle = _foto(tmp_path / "b.jpg", groesse=(400, 400))
    vorher = quelle.read_bytes()
    miniaturen.lauf(tmp_path, schreib=None)
    assert quelle.read_bytes() == vorher
    assert miniaturen.mini_dir(tmp_path).is_dir()


def test_lauf_zaehlt_kaputte_dateien_und_bricht_nicht_ab(tmp_path):
    (tmp_path / "a-kaputt.jpg").write_bytes(b"nichts davon ist ein bild")
    _foto(tmp_path / "z-heil.jpg", groesse=(400, 400))
    bericht = miniaturen.lauf(tmp_path, schreib=None)
    assert bericht == {**bericht, "gemacht": 1, "gescheitert": 1}


def test_lauf_ueber_ein_leeres_verzeichnis(tmp_path):
    assert miniaturen.lauf(tmp_path / "gibtsnicht", schreib=None)["gemacht"] == 0


def test_der_nachtlauf_zieht_die_miniaturen_nach(tmp_path, monkeypatch):
    """Abgeleitet wird im Crawl-Lauf, nicht im Web-Prozess."""
    from zettel.scrapers import nachtlauf
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    _foto(bilder / "neu.jpg", groesse=(400, 400))
    monkeypatch.setattr(nachtlauf.knuspr, "crawl",
                        lambda *a, **k: {"run_id": 1, "status": "ok",
                                         "n_products": 1, "error": None})
    db_datei = tmp_path / "p.db"
    con = db.connect(db_datei)
    db.migrate(con)
    con.close()
    nachtlauf.lauf(str(db_datei), begriffe=["milch"], image_dir=str(bilder),
                   http=object(), sichern=False, schreib=lambda *_: None)
    assert (bilder / "mini" / "neu.jpg.webp").is_file()


def test_der_nachtlauf_zieht_die_einheiten_nach(tmp_path, monkeypatch):
    """`repariere_einheiten` hatte keinen Aufrufer ausser den Tests (UI-Review
    2026-09-01, Fund 5, Rest). Der Crawl schreibt nur die Zeilen richtig, die
    er anfasst; der Nachtlauf zieht die anderen nach."""
    from zettel.scrapers import nachtlauf
    monkeypatch.setattr(nachtlauf.knuspr, "crawl",
                        lambda *a, **k: {"run_id": 1, "status": "ok",
                                         "n_products": 0, "error": None})
    db_datei = tmp_path / "p.db"
    con = db.connect(db_datei)
    db.migrate(con)
    con.execute("INSERT INTO product (source, external_id, name, unit_text,"
                " unit, price_cents)"
                " VALUES ('knuspr', 'x1', 'Nudeln', '0,25 g', 'g', 239)")
    con.commit()
    con.close()
    meldungen = []
    nachtlauf.lauf(str(db_datei), begriffe=["nudeln"], image_dir=None,
                   http=object(), sichern=False, schreib=meldungen.append)
    con = db.connect(db_datei)
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Nudeln'"
                       ).fetchone()["unit_text"] == "250 g"
    con.close()
    assert "Einheiten nachgezogen: 1" in meldungen


# --------------------------------------------------------------------------
# Der Bildweg

def test_kachel_bekommt_die_miniatur_und_nicht_das_original(db_datei, bild_dir):
    pid, original = _produkt_mit_bild(db_datei, bild_dir)
    miniaturen.lauf(bild_dir, schreib=None)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        antwort = c.get(f"/bild/{pid}")
        assert antwort.status_code == 200
        assert antwort.headers["content-type"] == "image/webp"
        # Der eigentliche Zweck des Tickets: die Kachel wiegt einen Bruchteil.
        assert len(antwort.content) < original.stat().st_size / 10


def test_original_bleibt_ueber_einen_eigenen_weg_erreichbar(db_datei, bild_dir):
    pid, original = _produkt_mit_bild(db_datei, bild_dir)
    miniaturen.lauf(bild_dir, schreib=None)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        antwort = c.get(f"/bild/{pid}/original")
        assert antwort.status_code == 200
        assert len(antwort.content) == original.stat().st_size


def test_ohne_miniatur_faellt_der_bildweg_auf_das_original_zurueck(db_datei,
                                                                  bild_dir):
    """Der Zustand direkt nach einem Crawl: das Bild ist da, die Miniatur noch
    nicht. Die Kachel muss richtig aussehen und darf nur teuer sein."""
    pid, original = _produkt_mit_bild(db_datei, bild_dir)
    assert not miniaturen.mini_dir(bild_dir).exists()
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        antwort = c.get(f"/bild/{pid}")
        assert antwort.status_code == 200
        assert antwort.content == original.read_bytes()


def test_bedingte_anfrage_liefert_304_ohne_rumpf(db_datei, bild_dir):
    pid, _ = _produkt_mit_bild(db_datei, bild_dir)
    miniaturen.lauf(bild_dir, schreib=None)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        erst = c.get(f"/bild/{pid}")
        etag = erst.headers["etag"]
        assert etag
        wieder = c.get(f"/bild/{pid}", headers={"If-None-Match": etag})
        assert wieder.status_code == 304
        assert wieder.content == b""
        assert wieder.headers["etag"] == etag


def test_304_auch_bei_schwachem_etag_und_liste(db_datei, bild_dir):
    pid, _ = _produkt_mit_bild(db_datei, bild_dir)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        etag = c.get(f"/bild/{pid}").headers["etag"]
        for kopf in (f'W/{etag}', f'"fremd", {etag}', "*"):
            assert c.get(f"/bild/{pid}",
                         headers={"If-None-Match": kopf}).status_code == 304, kopf


def test_fremder_etag_liefert_wieder_den_rumpf(db_datei, bild_dir):
    pid, _ = _produkt_mit_bild(db_datei, bild_dir)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        antwort = c.get(f"/bild/{pid}", headers={"If-None-Match": '"veraltet"'})
        assert antwort.status_code == 200
        assert antwort.content


def test_bild_traegt_cache_control(db_datei, bild_dir):
    pid, _ = _produkt_mit_bild(db_datei, bild_dir)
    miniaturen.lauf(bild_dir, schreib=None)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        for weg in (f"/bild/{pid}", f"/bild/{pid}/original"):
            kopf = c.get(weg).headers["cache-control"]
            assert kopf == webapp.BILD_CACHE, weg
            assert "max-age" in kopf


def test_geaenderte_datei_bekommt_einen_neuen_etag(db_datei, bild_dir):
    """`last_seen_at` allein reicht nicht — die Version steckt in der Datei."""
    pid, original = _produkt_mit_bild(db_datei, bild_dir)
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        alt = c.get(f"/bild/{pid}").headers["etag"]
        _foto(original, groesse=(1000, 1000))
        import os
        os.utime(original, (0, 0))
        neu = c.get(f"/bild/{pid}").headers["etag"]
        assert neu != alt
        assert c.get(f"/bild/{pid}",
                     headers={"If-None-Match": alt}).status_code == 200


def test_fehlendes_bild_bleibt_ein_404_und_wird_nicht_gemerkt(db_datei,
                                                             bild_dir):
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        for weg in ("/bild/999999", "/bild/999999/original"):
            antwort = c.get(weg)
            assert antwort.status_code == 404, weg
            # Kein Zwischenspeichern: ein Bild, das der nächste Crawl
            # nachliefert, soll nicht eine Woche als fehlend gelten.
            assert antwort.headers["cache-control"] == "no-store", weg


def test_bildweg_kommt_auch_ueber_die_miniatur_nicht_aus_dem_verzeichnis(
        db_datei, bild_dir):
    """Die Pfadprüfung aus `bilddatei()` gilt weiterhin — auch der neue Weg
    zum Original darf keine fremde Datei ausliefern."""
    (bild_dir.parent / "geheim.txt").write_text("nicht ausliefern")
    con = db.connect(db_datei)
    con.execute("UPDATE product SET image_path = '../geheim.txt' WHERE name = ?",
                (MILCH,))
    con.commit()
    pid = con.execute("SELECT id FROM product WHERE name = ?",
                      (MILCH,)).fetchone()["id"]
    con.close()
    with TestClient(webapp.create_app(db_datei, bild_dir)) as c:
        assert c.get(f"/bild/{pid}").status_code == 404
        assert c.get(f"/bild/{pid}/original").status_code == 404


def test_miniatur_liegt_unter_dem_bildverzeichnis(tmp_path):
    """Kein Wert aus der Datenbank darf über den Miniaturpfad hinausführen."""
    p = miniaturen.mini_pfad(tmp_path, "../../etc/passwd")
    assert p.parent == miniaturen.mini_dir(tmp_path)
    assert p.name == "passwd.webp"
