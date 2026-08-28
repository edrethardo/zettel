"""Aus einer abgelegten Bon-Datei wird Text (WB-358).

Ein Leser je Format, und beide rufen ein LOKALES Programm auf — `pdftotext`
für den eBon, `tesseract` für den Screenshot. Das ist kein Verstoss gegen
Spec 3: der Shop ruft keine fremde Seite auf, er startet einen Prozess auf
derselben Maschine.

**Was hier gemessen ist und nicht vermutet** (2026-08-28, echter Rewe-eBon,
25 KB): `pdftotext -layout` liest ihn vollständig, 85 Zeilen, in wenigen
Millisekunden. `-layout` ist dabei nicht Geschmack, sondern Bedingung: der Bon
ist eine ZWEISPALTIGE Anordnung (Name links, Preis rechtsbündig), und ohne
`-layout` wirft poppler die Spalten durcheinander. Der Preis stünde dann
irgendwo, und die Zerlegung in `bons.zerlegen` hätte nichts mehr, woran sie
sich halten kann.

**`tesseract` ist auf dieser Maschine NICHT installiert.** Der Bild-Weg sagt
das in einem Satz, der die Lösung enthält, statt mit `FileNotFoundError`
abzubrechen — und er sagt es, ohne dass der Upload oder die Bon-Seite davon
etwas merken. Ein Bild bleibt liegen, bis jemand `tesseract-ocr` installiert;
das ist eine Systemänderung und gehört dem Nutzer, nicht diesem Prozess.

**Warum ein Zeitlimit auf beiden Wegen.** `pdftotext` ist schnell, aber ein
bösartiges oder kaputtes PDF muss das nicht sein, und OCR auf einem
1284 × 10131 grossen Screenshot dauert im zweistelligen Sekundenbereich. Beides
läuft deshalb ausserhalb des Request-Pfads (`bons.lauf`) UND unter einer Frist:
ein Lauf, der hängt, soll enden und nicht einen Thread belegen, bis der Prozess
neu startet.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from picknick.bons.ablage import erkenne_typ

#: Die Programme, so wie sie im PATH heissen. Als Name und nicht als Pfad,
#: damit eine andere Maschine sie woanders haben darf — gefunden wird über
#: `shutil.which`, genauso wie `wake-vllm` in `picknick.llm.wake`.
PDFTOTEXT = "pdftotext"
TESSERACT = "tesseract"

#: Sprachpaket für die OCR. Deutsch, weil auf einem deutschen Kassenbon
#: deutsche Wörter stehen; ohne das Paket liest tesseract englisch und macht
#: aus „Hähnchenbrustroulade" verlässlich etwas anderes.
OCR_SPRACHE = "deu"

#: Frist je Aufruf. Grosszügig für OCR (gemessen wurde sie hier nie — es gibt
#: kein tesseract), knapp genug, dass ein hängender Prozess auffällt.
FRIST_PDF_S = 30.0
FRIST_OCR_S = 300.0

#: Was `erkenne_typ()` liefert und wie es hier gelesen wird.
PDF_ENDUNGEN = (".pdf",)
BILD_ENDUNGEN = (".png", ".jpg", ".heic")


class LeseFehler(RuntimeError):
    """Aus dieser Datei liess sich kein Text gewinnen — mit Begründung.

    Eigene Klasse aus demselben Grund wie `ablage.BonFehler`: die Meldung ist
    für Menschen geschrieben und darf unverändert auf die Seite. Ein
    `CalledProcessError` darf das nicht.
    """


class WerkzeugFehlt(LeseFehler):
    """Das Programm für dieses Format ist auf dieser Maschine nicht da.

    Ausdrücklich KEIN Fehler der Datei und kein Fehler des Shops. Der
    Unterschied hat eine eigene Klasse, weil die Oberfläche ihn anders zeigen
    soll: ein Bon, der wegen fehlender OCR liegen bleibt, ist nicht kaputt —
    er wartet auf eine Installation.
    """


def werkzeug_da(name: str) -> bool:
    """Liegt `name` im PATH? Ein Blick, kein Aufruf."""
    return shutil.which(name) is not None


def ocr_da() -> bool:
    """Kann diese Maschine Bilder lesen?

    Steht als eigene Funktion hier, damit die Oberfläche die Frage stellen
    kann, OHNE einen Lauf zu starten: der Knopf „Auslesen" soll bei einem Bild
    von vornherein sagen, dass es nicht geht, statt einen Lauf zu starten, der
    sofort scheitert.
    """
    return werkzeug_da(TESSERACT)


OCR_FEHLT_TEXT = (
    "Dafür fehlt OCR auf dieser Maschine. Ein Bild-Bon lässt sich erst lesen, "
    "wenn `tesseract-ocr` und `tesseract-ocr-deu` installiert sind — das ist "
    "eine Systemänderung und gehört nicht in diesen Prozess. Ein eBon als PDF "
    "aus der Rewe- oder Lidl-App braucht sie nicht.")


def text_aus_datei(pfad) -> str:
    """Bon-Datei -> Text. Wählt den Leser nach dem INHALT, nicht der Endung.

    Derselbe Grundsatz wie beim Upload (`ablage.erkenne_typ`): der Dateiname
    ist eine Behauptung, die ersten Bytes sind eine Eigenschaft. Eine `.pdf`,
    die in Wahrheit ein PNG ist, geht hier durch die OCR und nicht durch
    poppler — und niemand rätselt über eine leere Ausgabe.
    """
    pfad = Path(pfad)
    try:
        kopf = pfad.open("rb").read(16)
    except OSError as e:
        raise LeseFehler(f"{pfad.name} lässt sich nicht öffnen: {e}") from e
    endung = erkenne_typ(kopf)
    if endung in PDF_ENDUNGEN:
        return pdf_text(pfad)
    if endung in BILD_ENDUNGEN:
        return bild_text(pfad)
    raise LeseFehler(
        f"{pfad.name} ist weder PDF noch Bild — geprüft wurde der Inhalt der "
        "Datei, nicht ihr Name.")


def pdf_text(pfad, *, frist_s: float = FRIST_PDF_S) -> str:
    """Der eBon-Weg: `pdftotext -layout`, Ausgabe nach stdout.

    `-layout` erhält die Spalten (siehe Modul-Docstring), `-enc UTF-8` ist
    nötig, weil poppler sonst je nach Locale Latin-1 ausgibt und aus „Münster"
    Bytes macht, die niemand mehr auseinanderhält. `-` als Ziel schreibt nach
    stdout, damit keine temporäre Datei entsteht, die jemand aufräumen müsste.
    """
    return _laufen_lassen(
        [PDFTOTEXT, "-layout", "-enc", "UTF-8", str(pfad), "-"],
        PDFTOTEXT, frist_s,
        fehlt=("`pdftotext` ist im PATH nicht zu finden. Es steckt im Paket "
               "`poppler-utils`. Ohne das Programm lässt sich ein eBon nicht "
               "lesen — über den Bon selbst sagt das nichts."))


def bild_text(pfad, *, frist_s: float = FRIST_OCR_S,
              sprache: str = OCR_SPRACHE) -> str:
    """Der Screenshot-Weg: `tesseract <datei> - -l deu`.

    Auf dieser Maschine gibt es kein tesseract, also endet dieser Weg
    heute IMMER in `WerkzeugFehlt` — und zwar sofort und mit einem Satz, der
    sagt, was fehlt. Gebaut ist er trotzdem vollständig: der Lidl-Bon ist ein
    GERENDERTER Screenshot (scharfe Monospace auf Weiss, Umlaute erhalten),
    kein Foto von Papier. Sobald das Paket da ist, ist die Erkennung
    erwartbar gut und der Rest der Kette (`zerlegen`) ist derselbe — die
    Struktur der beiden Bons unterscheidet sich fast nicht.
    """
    return _laufen_lassen(
        [TESSERACT, str(pfad), "-", "-l", sprache], TESSERACT, frist_s,
        fehlt=OCR_FEHLT_TEXT)


def _laufen_lassen(befehl: list[str], programm: str, frist_s: float,
                   fehlt: str) -> str:
    """Ein lokaler Prozess, dessen stdout der Text ist.

    Jeder Ausgang, der nicht „Text" ist, wird zu `LeseFehler` mit Grund —
    fehlendes Programm, Zeitüberschreitung, Endcode ungleich null, leere
    Ausgabe. Der Aufrufer muss dadurch nichts über `subprocess` wissen, und
    die Bon-Seite bekommt in jedem Fall einen Satz statt eines Stacktrace.

    Die Prüfung auf das Programm steht VOR dem Aufruf und fängt nicht bloss
    `FileNotFoundError`: so trägt die Meldung den Namen des Pakets und nicht
    „errno 2".
    """
    if not werkzeug_da(programm):
        raise WerkzeugFehlt(fehlt)
    try:
        lauf = subprocess.run(befehl, capture_output=True, timeout=frist_s,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise LeseFehler(
            f"{programm} hat nach {frist_s:.0f} s noch nicht geantwortet und "
            "wurde abgebrochen.") from e
    except OSError as e:
        raise LeseFehler(f"{programm} liess sich nicht starten: {e}") from e
    if lauf.returncode != 0:
        grund = (lauf.stderr or b"").decode("utf-8", "replace").strip()
        raise LeseFehler(
            f"{programm} endete mit Code {lauf.returncode}"
            + (f": {grund.splitlines()[-1]}" if grund else "."))
    text = (lauf.stdout or b"").decode("utf-8", "replace")
    if not text.strip():
        raise LeseFehler(
            f"{programm} hat nichts gefunden — die Datei enthält keinen Text. "
            "Bei einem PDF heisst das meist, dass es ein eingescanntes Bild "
            "ist; dafür bräuchte es OCR.")
    return text
