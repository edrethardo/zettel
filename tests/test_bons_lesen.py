"""Tests für das Auslesen eines Kassenbons (WB-358).

**Die echten Bons unter `data/bons/` kommen hier nicht vor.** Sie sind
gitignored, sie gehören einer realen Person, und eine Fixture aus ihnen wäre
genau der Weg, auf dem Zahlungsdaten und fremde Einkäufe ins Repo geraten.
Gearbeitet wird mit dem NACHGEBAUTEN Bon aus
`tests/fixtures/bon_rewe_nachgebaut.txt`: dieselbe Struktur, dieselbe
Anordnung, erfundene Artikel und erfundene Zahlungsdaten.

Kein Netz, kein Modell, keine Box. `pdftotext` ist ein lokales Programm; die
Tests, die es brauchen, überspringen sich selbst, wenn es fehlt — auf dieser
Maschine ist es da (`poppler-utils`).
"""
import shutil

import pytest

from picknick import bons
from picknick.bons import lesen, zerlegen

from conftest import ZAHLUNGSDATEN, baue_pdf  # noqa: F401  (Pfad via rootdir)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

kein_pdftotext = pytest.mark.skipif(
    not lesen.werkzeug_da(lesen.PDFTOTEXT),
    reason="pdftotext fehlt (poppler-utils) — der PDF-Weg ist nicht prüfbar")


# --------------------------------------------------------------------------
# Artikelzeilen

def test_artikelzeilen_werden_mit_name_und_preis_erkannt(bon_text):
    bon = zerlegen.zerlege(bon_text)
    gefunden = {p.text: p.gesamt_cents for p in bon.posten}
    assert gefunden == {
        "KRAEUTERQUARK": 129,
        "BROTZEIT KAESE": 249,
        "SCHOKO-KEKSE": 200,
        "ZWIEBELRINGE TK": 219,
        "GURKENSALAT MIN.": 99,
        "APFELSAFT NATUR": 149,
    }


def test_name_mit_leerzeichen_bleibt_ganz(bon_text):
    """Der Preis steht rechtsbündig — am ersten Leerzeichen zu trennen wäre
    falsch, und zwar bei jedem zweiten Produkt."""
    bon = zerlegen.zerlege(bon_text)
    assert "GURKENSALAT MIN." in [p.text for p in bon.posten]


def test_mengenzeile_gehoert_zur_zeile_darueber(bon_text):
    bon = zerlegen.zerlege(bon_text)
    kekse = next(p for p in bon.posten if p.text == "SCHOKO-KEKSE")
    assert kekse.menge == 2
    assert kekse.einzel_cents == 100
    assert kekse.gesamt_cents == 200
    assert kekse.mengentext == "2 Stk x 1,00"


def test_mengenzeile_haengt_nicht_an_einer_fremden_zeile(bon_text):
    """Nur der Posten DIREKT darüber bekommt die Menge, kein anderer."""
    bon = zerlegen.zerlege(bon_text)
    for p in bon.posten:
        if p.text != "SCHOKO-KEKSE":
            assert p.menge == 1, p


def test_mengenzeile_ohne_posten_darueber_wird_ignoriert():
    """Eine Mengenzeile ganz am Anfang gehört zu nichts — und erfindet nichts."""
    bon = zerlegen.zerlege("              2 Stk x   1,00\n"
                           "KRAEUTERQUARK                    1,29 B\n")
    assert [(p.text, p.menge) for p in bon.posten] == [("KRAEUTERQUARK", 1)]


def test_gewichtszeile_setzt_keinen_stueckpreis():
    """`0,532 kg x 9,99` ist ein KILOpreis. Als Einzelpreis wäre er gelogen."""
    bon = zerlegen.zerlege(
        "WEINTRAUBEN HELL                 5,31 B\n"
        "          0,532 kg x  9,99 EUR/kg\n")
    p = bon.posten[0]
    assert p.menge == 1
    assert p.einzel_cents is None
    assert p.gesamt_cents == 531
    assert "0,532 kg" in p.mengentext


# --------------------------------------------------------------------------
# Nicht-Artikel

@pytest.mark.parametrize("name", ["PFAND 0,25 EURO", "RABATT AKTION"])
def test_pfand_und_rabatt_sind_keine_artikel(bon_text, name):
    bon = zerlegen.zerlege(bon_text)
    assert name not in [p.text for p in bon.posten]


def test_summe_und_zahlungszeile_sind_keine_artikel(bon_text):
    bon = zerlegen.zerlege(bon_text)
    texte = " ".join(p.text for p in bon.posten)
    assert "SUMME" not in texte
    assert "Mastercard" not in texte
    # Und die Summe steht trotzdem am Bon — sie ist eine Auskunft, kein Kauf.
    assert bon.summe_cents == 1065
    assert bon.summe_posten_cents == 1045


def test_bonbons_sind_ein_artikel():
    """„BON" als Teilstring träfe „BONBONS". Verglichen wird der Wortanfang."""
    bon = zerlegen.zerlege("BONBONS FRUCHT                   1,49 B\n")
    assert [p.text for p in bon.posten] == ["BONBONS FRUCHT"]


def test_zahlungsteil_wird_gar_nicht_erst_angesehen(bon_text):
    """Die strukturelle Hälfte der Datenschutzzusage.

    Was hinter der Summenzeile steht, kommt nicht einmal in die Liste der
    Zeilen, die auf Artikel geprüft werden. Eine Regel, die die Kartennummer
    erst als Nicht-Artikel aussortiert, hinge daran, dass jemand an sie
    gedacht hat.
    """
    bereich = "\n".join(zerlegen.artikelzeilen(bon_text))
    for was, wert in ZAHLUNGSDATEN.items():
        assert wert not in bereich, was
    assert "Mastercard" not in bereich
    assert "KRAEUTERQUARK" in bereich


# --------------------------------------------------------------------------
# Laden, Datum, Beträge

def test_laden_und_datum(bon_text):
    bon = zerlegen.zerlege(bon_text)
    assert bon.laden == zerlegen.REWE
    assert bon.datum == "2026-03-04"


def test_ohne_erkennbaren_laden_wird_nicht_geraten():
    bon = zerlegen.zerlege("KRAEUTERQUARK                    1,29 B\n")
    assert bon.laden == zerlegen.UNBEKANNT
    assert bon.datum is None


@pytest.mark.parametrize("text,soll", [
    ("4,38", 438), ("0,25", 25), ("-0,30", -30), ("41,23", 4123),
    ("1.99", 199), ("10,00", 1000),
])
def test_cents(text, soll):
    assert zerlegen.cents(text) == soll


# --------------------------------------------------------------------------
# Unbekanntes Format

def test_unbekanntes_format_sagt_es_verstaendlich():
    with pytest.raises(zerlegen.BonFormatFehler) as e:
        zerlegen.zerlege("Sehr geehrte Damen und Herren,\n\n"
                         "anbei die Rechnung.\n")
    text = str(e.value)
    assert "lesen" in text.lower()
    # Die Meldung sagt, WIE eine Artikelzeile aussieht — sonst weiss niemand,
    # was er stattdessen hochladen soll.
    assert "5,69" in text


def test_leerer_text_ist_kein_absturz():
    with pytest.raises(zerlegen.BonFormatFehler):
        zerlegen.zerlege("")


# --------------------------------------------------------------------------
# Der PDF-Weg

@kein_pdftotext
def test_pdf_wird_ueber_pdftotext_gelesen(tmp_path, bon_pdf):
    datei = tmp_path / "bon.pdf"
    datei.write_bytes(bon_pdf)
    bon = zerlegen.zerlege(lesen.text_aus_datei(datei))
    assert len(bon.posten) == 6
    assert bon.summe_cents == 1065


@kein_pdftotext
def test_pdf_ohne_text_sagt_dass_es_ein_bild_ist(tmp_path):
    """Ein gescanntes PDF hat keinen Textstrom. Das ist kein Absturz."""
    datei = tmp_path / "leer.pdf"
    datei.write_bytes(baue_pdf(""))
    with pytest.raises(lesen.LeseFehler) as e:
        lesen.pdf_text(datei)
    assert "OCR" in str(e.value)


def test_endung_luegt_nicht_ueber_den_leser(tmp_path, monkeypatch):
    """Gewählt wird nach dem INHALT. Ein PNG mit `.pdf` geht nicht zu poppler."""
    datei = tmp_path / "bon.pdf"
    datei.write_bytes(PNG)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(lesen.WerkzeugFehlt) as e:
        lesen.text_aus_datei(datei)
    assert "tesseract" in str(e.value)


def test_datei_ohne_bekannte_kennung(tmp_path):
    datei = tmp_path / "bon.pdf"
    datei.write_bytes(b"Das hier ist eine Textdatei.\n")
    with pytest.raises(lesen.LeseFehler) as e:
        lesen.text_aus_datei(datei)
    assert "weder PDF noch Bild" in str(e.value)


# --------------------------------------------------------------------------
# Der Bild-Weg ohne OCR

def test_ohne_ocr_sagt_der_bildweg_was_fehlt(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    datei = tmp_path / "bon.png"
    datei.write_bytes(PNG)
    with pytest.raises(lesen.WerkzeugFehlt) as e:
        lesen.text_aus_datei(datei)
    text = str(e.value)
    # Die Meldung nennt das Paket. „Nicht unterstützt" wäre eine Sackgasse.
    assert "tesseract-ocr" in text
    assert "PDF" in text


def test_ocr_da_fragt_nur_den_pfad_ab(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert lesen.ocr_da() is False
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    assert lesen.ocr_da() is True


def test_ocr_fehlt_text_ist_die_eine_meldung():
    """Seite und Ausnahme sagen denselben Satz — sonst laufen sie auseinander."""
    assert bons.OCR_FEHLT_TEXT == lesen.OCR_FEHLT_TEXT
