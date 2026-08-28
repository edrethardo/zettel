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


# --------------------------------------------------------------------------
# Vorlage-Datenbanken (WB-366)
#
# **Eine Datenbank in einer DATEI aufzubauen kostet eine Viertelsekunde, sie
# zu kopieren kostet nichts.** Gemessen am 2026-08-28 auf dieser Maschine:
#
#     db.connect(datei) + db.migrate()                238,3 ms
#     dasselbe + knuspr.crawl() (28 Produkte)         283,4 ms
#     Vorlage kopieren und öffnen                       0,37 ms
#     Vorlage nach `:memory:` spiegeln                  0,11 ms
#
# Der Aufwand steckt nicht im Rechnen, sondern im `fsync`: SQLite sichert
# jeden Commit auf die Platte, und `migrate()` committet oft. Deshalb ist auch
# die LEERE Datei-Datenbank teuer und nicht nur die gecrawlte — wer nur den
# Crawl einspart, hat 85 % des Problems stehen lassen.
#
# Der Ausweg ist eine Vorlage je Testlauf: einmal gebaut, danach als Datei
# kopiert. Der Katalog aus `fixtures/knuspr_milch.json` ändert sich innerhalb
# eines Laufs nicht, und die Kopie ist byteweise dieselbe Datenbank — also
# auch dasselbe Schema, dieselben FTS5-Trigger und dieselben Produkt-IDs wie
# beim Bauen von Hand. Was ein Test daran ändert, ändert er an SEINER Kopie;
# die Vorlage wird nie geöffnet, ausser um sie zu lesen.
#
# Unter `pytest -n auto` hat jeder Worker seinen eigenen Prozess und damit
# seine eigene Vorlage — `tmp_path_factory` gibt jedem sein eigenes
# Basisverzeichnis, es gibt keine gemeinsame Datei und nichts zu sperren.

import json  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402

from picknick import db  # noqa: E402


class VorlagenHTTP:
    """Liefert aufgezeichnete Seiten und danach nichts mehr.

    Derselbe Doppelgänger, den die Testmodule einzeln nachbauen — hier steht
    er, weil der Vorlagenbau ihn braucht. Wer den CRAWLER prüft, baut sich
    weiter einen eigenen (siehe `tests/test_knuspr.py`): dort ist der
    Doppelgänger der Prüfgegenstand und keine Zutat.
    """

    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, url):
        return _VorlagenAntwort(self.seiten.pop(0) if self.seiten
                                else {"data": {}})


class _VorlagenAntwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


def knuspr_katalog(con):
    """Spielt die aufgezeichnete Knuspr-Antwort durch den ECHTEN Crawler ein.

    Nicht `INSERT INTO product` von Hand: so ist mitgeprüft, dass die
    FTS-Trigger auf dem Weg greifen, den der Katalog im Betrieb wirklich
    nimmt — genau wie vorher, als jede Testdatei das selbst tat.
    """
    from picknick.scrapers import knuspr

    payload = json.loads(
        (FIXTURES / "knuspr_milch.json").read_text(encoding="utf-8"))
    # totalHits der Fixture ist grösser als die eine aufgezeichnete Seite; der
    # Crawler fragt danach eine leere zweite Seite ab und bricht ab.
    knuspr.crawl(con, VorlagenHTTP([payload]), ["milch"], pause_s=0)


class Vorlagen:
    """Baut jede Vorlage-Datenbank einmal und gibt danach nur noch Kopien.

    `name` ist der Schlüssel: zwei Aufrufe mit demselben Namen bekommen
    dieselbe Vorlage, auch aus verschiedenen Testdateien. Der Name muss
    deshalb zum INHALT passen und nicht zur Testdatei — wer denselben Namen
    mit anderen Füllungen benutzt, bekäme sonst stillschweigend die zuerst
    gebaute Datenbank eines fremden Tests. Dagegen steht die Prüfung unten:
    derselbe Name mit anderen Füllungen ist ein Testfehler, keine Überraschung
    im Testergebnis.

    Die Füllungen sind Funktionen `f(con)` und werden der Reihe nach auf die
    frisch migrierte Datenbank angewandt. `vorlagen.katalog` ist die
    gebräuchlichste — ein Testmodul, das den Katalog plus eigene Produkte
    braucht, schreibt `vorlagen.con("mein_name", vorlagen.katalog, _zusatz)`
    und muss nichts aus dieser Datei importieren.
    """

    #: Der aufgezeichnete Knuspr-Katalog als Füllung.
    katalog = staticmethod(knuspr_katalog)

    def __init__(self, verzeichnis):
        self._verzeichnis = verzeichnis
        self._gebaut = {}

    def vorlage(self, name, *fuellen):
        """Pfad zur Vorlage. Baut sie beim ersten Aufruf, danach nie wieder."""
        if name not in self._gebaut:
            pfad = self._verzeichnis / f"{name}.db"
            con = db.connect(pfad)
            db.migrate(con)
            for f in fuellen:
                f(con)
            con.commit()
            # Ohne den Checkpoint stünde ein Teil der Daten noch im
            # `-wal`-Nachbarn, und die Kopie wäre halb leer. Beim sauberen
            # Schliessen räumt SQLite ihn zwar selbst weg — aber „räumt
            # selbst weg" ist eine Zusage, auf die eine Vorlage sich nicht
            # verlassen muss.
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()
            self._gebaut[name] = (pfad, fuellen)
        pfad, zuerst = self._gebaut[name]
        if zuerst != fuellen:
            raise AssertionError(
                f"Die Vorlage {name!r} wurde schon anders gefüllt "
                f"({[f.__name__ for f in zuerst]} statt "
                f"{[f.__name__ for f in fuellen]}). Gib der neuen Füllung "
                "einen eigenen Namen, sonst bekommt ein Test die Datenbank "
                "eines anderen.")
        return pfad

    def datei(self, ziel, name, *fuellen):
        """Eine frische Kopie der Vorlage unter `ziel`. Gibt `ziel` zurück."""
        shutil.copyfile(self.vorlage(name, *fuellen), ziel)
        return ziel

    def con(self, name, *fuellen):
        """Die Vorlage als offene `:memory:`-Verbindung.

        `backup()` kopiert die Seiten direkt in den Speicher — kein Umweg
        über eine zweite Datei, und die Verbindung hat dieselben Pragmas wie
        jede andere aus `db.connect()`.
        """
        con = db.connect(":memory:")
        quelle = sqlite3.connect(str(self.vorlage(name, *fuellen)))
        try:
            quelle.backup(con)
        finally:
            quelle.close()
        return con


@pytest.fixture(scope="session")
def vorlagen(tmp_path_factory):
    """Die Vorlagen dieses Testlaufs (bzw. dieses xdist-Workers)."""
    return Vorlagen(tmp_path_factory.mktemp("vorlagen"))


@pytest.fixture
def db_datei(vorlagen, tmp_path):
    """Der Katalog als eigene Datenbank-DATEI, frisch für diesen Test."""
    return vorlagen.datei(tmp_path / "picknick.db", "katalog",
                          vorlagen.katalog)


@pytest.fixture
def katalog_con(vorlagen):
    """Der Katalog als offene `:memory:`-Verbindung, frisch für diesen Test."""
    con = vorlagen.con("katalog", vorlagen.katalog)
    yield con
    con.close()


@pytest.fixture
def leere_db_datei(vorlagen, tmp_path):
    """Eine migrierte, sonst leere Datenbank-DATEI."""
    return vorlagen.datei(tmp_path / "picknick.db", "leer")
