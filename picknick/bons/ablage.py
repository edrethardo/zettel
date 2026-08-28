"""Kassenbons vom Handy entgegennehmen, ablegen, auflisten, löschen (WB-344).

Nur der Transport. Was auf dem Bon steht, liest `bons.lesen` und `bons.zerlegen`
aus (WB-358) — dieses Modul fasst den Inhalt nie an, es kennt nur Bytes und
Dateinamen.

**Warum nach Inhalt geprüft wird und nicht nach Endung.** Der Dateiname kommt
vom Telefon und ist damit im Prinzip von aussen bestimmt; `.png` am Ende ist
eine Behauptung des Absenders, keine Eigenschaft der Datei. Geprüft werden
deshalb die ersten Bytes. Der Gedanke stammt aus der Werkbank
(`werkbank/src/werkbank/uploads.py`), der Unterschied ist Absicht: dort werden
ausschliesslich Bilder angenommen, hier muss PDF dazu. `pdftotext` ist auf
dieser Maschine vorhanden, `tesseract` nicht — ein eBon als PDF ist der weitaus
bessere Weg als ein Foto, und genau den darf diese Seite nicht ausschliessen.

Standardbibliothek, kein Bildmodul: PNG, JPEG, HEIC und PDF lassen sich an
ihren ersten Bytes auseinanderhalten (Spec 15 — die Abhängigkeitsliste bleibt,
was sie ist).

Kein Passwort. Der Sicherheitsrahmen ist das Tailnet (Spec 10); eine Anmeldung
ausgerechnet auf dieser einen Seite wäre ein zweites, widersprüchliches Modell.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

#: Grössengrenze. Begründet, nicht geraten: ein eBon als PDF liegt bei einigen
#: hundert Kilobyte, ein HEIC-Foto vom iPhone bei 2 bis 5 MB, ein hoch
#: aufgelöstes JPEG eines langen Kassenzettels erreicht rund 10 MB. 25 MB
#: lassen dafür Luft und bleiben zugleich eine Grösse, die dieser Prozess ohne
#: Not im Speicher hält — der Rumpf wird am Stück gelesen und zerlegt, ein
#: Streaming-Weg wäre Aufwand für einen Fall, den es hier nicht gibt.
MAX_BYTES = 25 * 1024 * 1024

#: (Magic Bytes, Endung). HEIC trägt `ftyp` erst bei Offset 4, siehe
#: `erkenne_typ()`.
SIGNATUREN = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"%PDF", ".pdf"),
]

#: Was die Seite in Worten anbietet — an einer Stelle, damit Meldung und
#: Prüfung nicht auseinanderlaufen.
ERLAUBT = "PNG, JPEG, HEIC oder PDF"


class BonFehler(ValueError):
    """Ein Upload wurde abgelehnt, und der Text sagt der Nutzerin warum.

    Eigene Klasse, damit die Route den erklärbaren Fall vom echten Absturz
    unterscheiden kann: die Meldung hier ist für Menschen geschrieben und darf
    unverändert auf die Seite.
    """


def erkenne_typ(roh: bytes) -> str | None:
    """Endung nach den ersten Bytes — oder `None`, wenn nichts davon passt."""
    for magie, endung in SIGNATUREN:
        if roh.startswith(magie):
            return endung
    # HEIC/HEIF vom iPhone: die ersten vier Bytes sind die Boxlänge, erst
    # danach steht die Kennung. Deshalb Offset 4 und nicht 0.
    if len(roh) > 12 and roh[4:8] == b"ftyp":
        return ".heic"
    return None


def sicherer_name(original: str, endung: str, jetzt: datetime | None = None) -> str:
    """Ein Dateiname, der das Zielverzeichnis nicht verlassen kann.

    Drei Dinge fallen weg: der Verzeichnisanteil (`Path.name` erledigt `../`
    und einen absoluten Pfad in einem Zug), alles ausser `A-Za-z0-9._-` — damit
    auch Nullbytes und Zeichen, die eine Shell anders liest — und die
    mitgeschickte Endung. Die Endung kommt aus dem INHALT; sonst hätte der
    Absender über den Namen doch wieder bestimmt, was die Datei zu sein
    vorgibt.

    Der Zeitstempel ist kein Schmuck: zwei Fotos vom selben Bon heissen auf dem
    Telefon gerne gleich, und sie sollen sich nicht gegenseitig überschreiben.
    """
    stamm = Path(original or "bon").name          # wirft jeden Verzeichnisteil weg
    stamm = Path(stamm).stem
    stamm = re.sub(r"[^A-Za-z0-9._-]+", "-", stamm).strip("-._") or "bon"
    stempel = (jetzt or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamm[:40]}-{stempel}{endung}"


def pfad_im_verzeichnis(verzeichnis, name: str) -> Path | None:
    """Der Pfad zu `name` in `verzeichnis` — oder `None`, wenn er hinausführt.

    Zweite Verteidigungslinie, für Namen, die NICHT durch `sicherer_name()`
    gegangen sind: beim Löschen kommt der Name aus der URL. Geprüft wird nach
    `resolve()`, also gegen `..` und Symlinks in einem, und ein Name mit
    Verzeichnistrenner oder Nullbyte kommt gar nicht erst so weit.
    """
    if not name or "/" in name or "\\" in name or "\x00" in name:
        return None
    if name in (".", ".."):
        return None
    wurzel = Path(verzeichnis).resolve()
    ziel = (wurzel / name).resolve()
    if ziel.parent != wurzel:
        return None
    return ziel


def speichern(verzeichnis, original_name: str, roh: bytes,
              max_bytes: int = MAX_BYTES) -> str:
    """Legt `roh` als Bon ab und gibt den verwendeten Dateinamen zurück.

    Jede Ablehnung wirft `BonFehler` mit einem Satz, der sagt, was los ist UND
    was zu tun wäre. Das ist der Anlass des Tickets: „Load failed" ohne Grund
    ist genau der Fehler, den diese Seite besser machen soll.
    """
    if not roh:
        raise BonFehler("Die Datei ist leer. Bitte den Bon noch einmal auswählen.")
    if len(roh) > max_bytes:
        raise BonFehler(
            f"Die Datei ist {len(roh) / (1024 * 1024):.1f} MB gross, erlaubt"
            f" sind {max_bytes // (1024 * 1024)} MB. Als PDF aus der Rewe- oder"
            " Lidl-App ist ein Bon deutlich kleiner als ein Foto davon.")
    endung = erkenne_typ(roh)
    if endung is None:
        raise BonFehler(
            f"Das ist kein Bon-Format ({ERLAUBT} erwartet). Geprüft wird der"
            " Inhalt der Datei, nicht ihr Name — eine umbenannte Textdatei"
            " reicht nicht.")

    verzeichnis = Path(verzeichnis)
    verzeichnis.mkdir(parents=True, exist_ok=True)
    basis = sicherer_name(original_name, endung)
    stamm = basis[: -len(endung)]
    # O_EXCL statt „gibt es schon? dann anders nennen": zwei Uploads in
    # derselben Sekunde bekämen sonst denselben Namen, und einer der beiden
    # Bons wäre weg. Create-or-fail ist atomar, der Zähler gibt jedem Versuch
    # einen frischen Kandidaten. 0o600, weil ein Kassenbon niemanden sonst
    # etwas angeht.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    for versuch in range(1, 1001):
        name = basis if versuch == 1 else f"{stamm}-{versuch}{endung}"
        try:
            fd = os.open(verzeichnis / name, flags, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
        return name
    raise BonFehler("Kein freier Dateiname gefunden — bitte alte Bons löschen.")


def groesse_text(anzahl: int) -> str:
    """Grösse so, wie ein Mensch sie liest — KB ab einem KB, MB ab einem MB."""
    if anzahl >= 1024 * 1024:
        return f"{anzahl / (1024 * 1024):.1f} MB"
    if anzahl >= 1024:
        return f"{anzahl / 1024:.0f} KB"
    return f"{anzahl} B"


def liste(verzeichnis) -> list[dict]:
    """Was abgelegt ist: Name, Zeitpunkt, Grösse. Neueste zuerst.

    Ein fehlendes Verzeichnis ist kein Fehler, sondern der Normalfall vor dem
    ersten Upload — die Seite soll dann eine leere Liste zeigen und nicht
    abstürzen.
    """
    wurzel = Path(verzeichnis)
    if not wurzel.is_dir():
        return []
    eintraege = []
    for pfad in sorted(wurzel.iterdir()):
        if not pfad.is_file():
            continue
        st = pfad.stat()
        zeit = datetime.fromtimestamp(st.st_mtime)
        eintraege.append({
            "name": pfad.name,
            "groesse": st.st_size,
            "groesse_text": groesse_text(st.st_size),
            "datum": zeit,
            "datum_text": zeit.strftime("%d.%m.%Y %H:%M"),
        })
    # Neueste oben: wer gerade etwas hochgeladen hat, sucht es nicht unten.
    # Der Name entscheidet bei gleicher Sekunde — `sorted()` oben macht die
    # Reihenfolge damit auch dann noch vorhersagbar.
    eintraege.sort(key=lambda e: e["datum"], reverse=True)
    return eintraege


def loeschen(verzeichnis, name: str) -> bool:
    """Löscht einen Bon. `True`, wenn wirklich etwas weg ist.

    Ein Name, der aus dem Verzeichnis hinausführt, wird nicht etwa bereinigt,
    sondern abgelehnt: bereinigen hiesse raten, was gemeint war, und beim
    Löschen ist Raten die falsche Antwort.
    """
    ziel = pfad_im_verzeichnis(verzeichnis, name)
    if ziel is None or not ziel.is_file():
        return False
    ziel.unlink()
    return True
