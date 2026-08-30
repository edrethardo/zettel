"""Miniaturen für die Produktkacheln (WB-374).

Der Crawler legt das Originalfoto ab, so wie knuspr.de es ausliefert: bis zu
2000×2000 px, im Mittel 291 KB. Die Kachel im Katalog ist 88 px breit. Eine
Trefferliste mit 60 Kacheln schob damit **17,3 MB** über die Leitung — auf
einem Telefon, im Tailnet, teils über Mobilfunk. Gemessen am 2026-08-29 gegen
den laufenden Shop.

Dieses Modul leitet aus jedem Original **einmal** eine Miniatur ab und legt
sie unter `<bildverzeichnis>/mini/<name>.webp`. Drei Entscheidungen, die
begründet gehören:

* **Nicht im Request-Pfad.** Umrechnen kostet ~11 ms je Bild; das gehört nicht
  in den Antwortweg einer Seite, die gerade lädt (Spec 3). Auch nicht „beim
  ersten Aufruf": das verlagert die Kosten bloss auf den ersten Blick und
  macht ihn unvorhersagbar langsam. Abgeleitet wird im Nachtlauf
  (`zettel.scrapers.nachtlauf`) und von Hand über `python -m
  zettel.miniaturen`.
* **Das Original bleibt liegen.** 3,2 GB, gecrawlt und nicht
  wiederbeschaffbar, ohne knuspr.de erneut zu belasten. Hier wird nie eine
  Datei ausserhalb von `mini/` geschrieben oder gelöscht.
* **WebP.** Der Bestand ist gemischt (jpg, jpeg, png, jeweils auch in
  Grossschreibung), und ein Teil der PNGs trägt Transparenz. WebP kann beides
  und ist das einzige Format, das hier eine einzige Endung erlaubt. Gemessen
  über 300 zufällige Bilder: Ø 4,3 KB gegen Ø 293,7 KB Original, grösste
  Miniatur 10,2 KB.

Fällt die Ableitung für ein Bild aus — kaputte Datei, unbekanntes Format —,
ist das kein Fehler des Laufs. Der Bildweg im Shop liefert dann weiter das
Original aus; es sieht richtig aus und ist nur teuer.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from zettel import umgebung

#: Kantenlänge der Miniatur. Die Kachel im Katalog ist 88 px (`_produkte.html`,
#: `_rezept_treffer.html`), auf einem Telefon mit doppelter Pixeldichte also
#: 176 px. Die kleineren Bilder im Korb und in der Pick-Ansicht sind 56 px und
#: werden von derselben Miniatur mitversorgt — eine zweite Grösse zu pflegen
#: lohnt die halbe Ersparnis nicht.
KANTE = 176

#: WebP-Qualität. 80 ist der Punkt, an dem eine 176-px-Miniatur auf dem Telefon
#: nicht mehr von der grösseren zu unterscheiden ist; 90 kostete im Mittel das
#: Anderthalbfache ohne sichtbaren Gewinn.
QUALITAET = 80

#: Wo die Miniaturen liegen: ein Unterverzeichnis DES Bildverzeichnisses, damit
#: der Shop weiterhin genau einen konfigurierten Pfad kennt
#: (`ZETTEL_IMAGE_DIR`) und ein Test mit eigenem `tmp_path` nichts zusätzlich
#: umbiegen muss.
UNTERORDNER = "mini"

ENDUNG = ".webp"

#: Was überhaupt als Original in Frage kommt. Bewusst eine feste Liste und
#: nicht „alles ausser mini/": im Bildverzeichnis soll nichts landen, was hier
#: aus Versehen durch einen Decoder gereicht wird.
FORMATE = {".jpg", ".jpeg", ".png", ".webp"}


def mini_dir(image_dir: str | Path) -> Path:
    """Das Miniaturverzeichnis zu einem Bildverzeichnis."""
    return Path(image_dir) / UNTERORDNER


def mini_pfad(image_dir: str | Path, original: str | Path) -> Path:
    """Wohin die Miniatur eines Originals gehört.

    Nur der Dateiname des Originals zählt, nie sein Verzeichnis — der Aufrufer
    im Web-Prozess hat den Pfad bereits gegen `bilddatei()` geprüft, und diese
    Funktion soll ihn nicht versehentlich wieder aufweichen.
    """
    return mini_dir(image_dir) / (Path(str(original)).name + ENDUNG)


def vorhandene(image_dir: str | Path, original: str | Path) -> Path | None:
    """Die Miniatur, falls sie schon abgeleitet wurde — sonst `None`.

    Der Bildweg fällt bei `None` auf das Original zurück. Ein frisch
    gecrawltes Bild ist damit sofort sichtbar, nur noch nicht klein.
    """
    p = mini_pfad(image_dir, original)
    return p if p.is_file() else None


def ableiten(original: str | Path, ziel: str | Path,
             *, kante: int = KANTE, qualitaet: int = QUALITAET) -> bool:
    """Schreibt eine Miniatur. Gibt zurück, ob es geklappt hat.

    Wirft nicht: ein einzelnes unlesbares Bild darf einen Lauf über 10.000
    Dateien nicht abbrechen. Geschrieben wird über eine Nachbardatei und dann
    umbenannt, damit ein abgebrochener Lauf keine halbe Miniatur hinterlässt —
    die sähe für `vorhandene()` fertig aus und würde nie wieder erneuert.
    """
    from PIL import Image                       # erst hier: der Web-Prozess
                                                # importiert dieses Modul auch
                                                # dann, wenn er nie ableitet.
    original, ziel = Path(original), Path(ziel)
    temp = ziel.with_name(ziel.name + ".teil")
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(original) as bild:
            # `draft()` lässt den JPEG-Decoder gleich verkleinert dekodieren
            # statt 2000×2000 aufzubauen und danach wegzuwerfen. Kostet bei den
            # anderen Formaten nichts und ist dort wirkungslos.
            bild.draft("RGB", (kante * 2, kante * 2))
            # Ein Palettenbild kann seine Transparenz in `info` tragen statt
            # im Modus; nach `convert("RGB")` wäre sie schwarz. Pillow warnt
            # darüber, und die Warnung hat recht — 622 PNGs im Bestand.
            durchsichtig = (bild.mode in ("RGBA", "LA", "PA")
                            or "transparency" in bild.info)
            klein = bild.convert("RGBA" if durchsichtig else "RGB")
            klein.thumbnail((kante, kante), Image.LANCZOS)
            klein.save(temp, "WEBP", quality=qualitaet, method=4)
        os.replace(temp, ziel)
        return True
    except Exception:                           # noqa: BLE001 — bewusst breit
        temp.unlink(missing_ok=True)
        return False


def originale(image_dir: str | Path) -> list[Path]:
    """Alle Originale eines Bildverzeichnisses, ohne `mini/`."""
    verzeichnis = Path(image_dir)
    if not verzeichnis.is_dir():
        return []
    return sorted(p for p in verzeichnis.iterdir()
                  if p.is_file() and p.suffix.lower() in FORMATE)


def lauf(image_dir: str | Path, *, kante: int = KANTE,
         neu: bool = False, schreib=print) -> dict:
    """Leitet die fehlenden Miniaturen ab. Der Einstiegspunkt für den Nachtlauf.

    Standardmässig wird nur nachgezogen, was fehlt oder älter ist als sein
    Original — der zweite Lauf über einen unveränderten Bestand kostet damit
    nur das Auflisten des Verzeichnisses. `neu=True` erzwingt alles neu (nach
    einer Änderung an `KANTE` oder `QUALITAET`).
    """
    gemacht = uebersprungen = gescheitert = 0
    bytes_original = bytes_mini = 0
    for quelle in originale(image_dir):
        ziel = mini_pfad(image_dir, quelle)
        try:
            frisch = (not neu and ziel.is_file()
                      and ziel.stat().st_mtime >= quelle.stat().st_mtime)
        except OSError:
            frisch = False
        if frisch:
            uebersprungen += 1
        elif ableiten(quelle, ziel, kante=kante):
            gemacht += 1
        else:
            gescheitert += 1
            continue
        try:
            bytes_original += quelle.stat().st_size
            bytes_mini += ziel.stat().st_size
        except OSError:
            pass

    bericht = {"gemacht": gemacht, "uebersprungen": uebersprungen,
               "gescheitert": gescheitert,
               "bytes_original": bytes_original, "bytes_mini": bytes_mini}
    if schreib:
        n = gemacht + uebersprungen
        anteil = (f", {bytes_mini / bytes_original:.1%} der Originalgrösse"
                  if bytes_original else "")
        schreib(f"Miniaturen: {gemacht} neu, {uebersprungen} unverändert,"
                f" {gescheitert} nicht ableitbar — {n} Kacheln bedient"
                + anteil)
    return bericht


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Leitet die Miniaturen der Produktbilder ab (WB-374). "
                    "Fasst die Originale nicht an.")
    p.add_argument("--image-dir", default=None,
                   help="Bildverzeichnis (Vorgabe: $ZETTEL_IMAGE_DIR "
                        "oder data/images)")
    p.add_argument("--kante", type=int, default=KANTE,
                   help=f"längste Kante in Pixeln (Vorgabe: {KANTE})")
    p.add_argument("--neu", action="store_true",
                   help="alle neu ableiten statt nur die fehlenden")
    args = p.parse_args(argv)
    image_dir = (args.image_dir or umgebung.wert("ZETTEL_IMAGE_DIR")
                 or "data/images")
    bericht = lauf(image_dir, kante=args.kante, neu=args.neu)
    # Ungleich null nur, wenn gar nichts entstanden ist: einzelne kaputte
    # Bilder sind ein bekannter Zustand des Bestands und kein Fehlschlag.
    return 0 if (bericht["gemacht"] or bericht["uebersprungen"]) else 1


if __name__ == "__main__":
    sys.exit(main())
