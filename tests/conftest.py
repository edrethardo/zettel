"""Voreinstellungen für die ganze Testsuite.

**Kein Test schickt Spans an ein laufendes Phoenix.** Das ist nicht bloss die
Regel „kein Test geht ins Netz" (Spec 13), sondern Selbstschutz: auf diesem
Rechner läuft Phoenix auf `localhost:6006`, und `picknick.web.app` richtet den
Tracer schon beim Import ein. Ohne die Zeile unten liefe jeder Testlauf mit in
das Projekt `Picknick Agent` — und die Zahlen, mit denen `scripts/trace_probe.py`
die Verifikation aus Spec 7.4 belegt, wären ein Gemisch aus echten Chat-Zügen
und Testrauschen.

Hart gesetzt und nicht `setdefault`: eine Umgebung, in der jemand
`PICKNICK_TRACING=1` exportiert hat, soll die Suite nicht umkonfigurieren
können.

Was die Span-Tests brauchen, richten sie selbst ein — mit einem
In-Memory-Exporter (`tests/test_obs.py`).
"""
import os

os.environ["PICKNICK_TRACING"] = "0"

import pytest  # noqa: E402

from picknick import obs  # noqa: E402


@pytest.fixture(autouse=True)
def _kein_abruf_startet_einen_prozess(monkeypatch):
    """Kein Test startet den Chefkoch-Abruf (WB-338, Spec 13).

    `Chat()` baut ohne Zutun eine `Quelle`, und die startet bei einem
    unbekannten Gericht `python -m picknick.gerichte.lauf` — einen Prozess,
    der ins Netz geht. In einem Test wäre das beides: langsam und ein Gang
    ins Netz durch die Hintertür. Wer den Weg PRÜFEN will, reicht einen
    eigenen `starter` herein (siehe `tests/test_gerichte.py`); wer es nicht
    tut, bekommt hier einen Testfehler statt eines stillen Prozesses.
    """
    from picknick.gerichte import quelle

    def _nein(argv):
        raise AssertionError(
            f"Ein Test wollte einen Abruf-Prozess starten: {argv!r}. "
            "Reich einen eigenen `starter` an `Quelle` herein.")

    monkeypatch.setattr(quelle, "_als_prozess", _nein)


@pytest.fixture(autouse=True)
def _kein_tracer_uebrig():
    """Räumt einen Provider weg, den ein Test gesetzt hat.

    `obs` hält den Provider im Modul, also über den Test hinaus. Bliebe er
    stehen, schriebe der nächste Test seine Spans in den Exporter des
    vorigen — und ein Test, der zufällig als zweiter läuft, sähe Spans, die
    er nicht erzeugt hat.
    """
    yield
    obs.abbauen()


# --------------------------------------------------------------------------
# Der nachgebaute Kassenbon (WB-358)
#
# **Die beiden ECHTEN Bons unter `data/bons/` sind nicht die Fixture und
# dürfen es nie werden.** Sie sind gitignored, sie enthalten die Einkäufe
# einer realen Person und ihre Zahlungsspuren. Was hier steht, ist
# NACHGEBAUT: dieselbe Struktur, dieselbe Anordnung, erfundene Artikel und
# erfundene Zahlungsdaten. Genau deshalb darf der Datenschutz-Test die
# Kartennummer laut aussprechen — sie gehört niemandem.

from pathlib import Path  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

#: Was im nachgebauten Bon an Zahlungsdaten steht. Kein Zeichen davon darf
#: nach dem Einlesen in der Datenbank auftauchen (`tests/test_bons_kaeufe.py`).
ZAHLUNGSDATEN = {
    "kartennummer": "############4242",
    "vu_nummer": "1234509876",
    "terminal_id": "55443322",
    "trace_nummer": "998877",
    "beleg_nummer": "3877011",
}


@pytest.fixture
def bon_text():
    """Der nachgebaute Rewe-Bon als Text — so, wie `pdftotext` ihn liefert."""
    return (FIXTURES / "bon_rewe_nachgebaut.txt").read_text(encoding="utf-8")


def baue_pdf(text: str, *, groesse: int = 9) -> bytes:
    """Ein minimales PDF mit `text` in Courier, Zeile für Zeile.

    Selbstgebaut und nicht mit einer Bibliothek: das Projekt hat keine
    PDF-Abhängigkeit (Spec 15), und ein fertiges PDF als Binärdatei im Repo
    wäre eine Fixture, die niemand mehr lesen oder ändern kann. So steht der
    Bon als TEXT im Repo, und das PDF entsteht daraus im Test.

    Courier, weil `pdftotext -layout` die Spalten aus den Zeichenbreiten
    rekonstruiert — mit einer Proportionalschrift landete der Preis nicht
    mehr in derselben Spalte und der Test prüfte etwas anderes als den
    echten Bon.
    """
    zeilen = ["BT", f"/F1 {groesse} Tf", "11 TL", "1 0 0 1 30 800 Tm"]
    for z in text.split("\n"):
        # Klammern und Backslash sind in einer PDF-Zeichenkette Syntax und
        # müssen escaped werden, sonst bricht der Inhaltsstrom mitten im Bon
        # ab — und pdftotext liefert die halbe Datei ohne ein Wort dazu.
        sicher = (z.replace("\\", "\\\\").replace("(", "\\(")
                   .replace(")", "\\)"))
        zeilen.append(f"({sicher}) Tj T*")
    zeilen.append("ET")
    strom = "\n".join(zeilen).encode("latin-1", "replace")
    objekte = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
        b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        b"<< /Length " + str(len(strom)).encode() + b" >>\nstream\n" + strom
        + b"\nendstream",
    ]
    aus = bytearray(b"%PDF-1.4\n")
    stellen = []
    for i, o in enumerate(objekte, 1):
        stellen.append(len(aus))
        aus += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(aus)
    aus += f"xref\n0 {len(objekte) + 1}\n0000000000 65535 f \n".encode()
    for s in stellen:
        aus += f"{s:010d} 00000 n \n".encode()
    aus += (f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(aus)


@pytest.fixture
def bon_pdf(bon_text):
    """Derselbe Bon als PDF-Bytes."""
    return baue_pdf(bon_text)
