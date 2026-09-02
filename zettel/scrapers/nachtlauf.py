"""Der nächtliche Katalog-Lauf (Spec 12) — Einstiegspunkt der Crawl-Unit.

    .venv/bin/python -m zettel.scrapers.nachtlauf

Genau das, was `zettel-crawl.service` startet, und genau das, was man von
Hand aufruft, wenn man wissen will, ob es noch geht. Drei Schritte in fester
Reihenfolge:

1. **Crawlen** — `knuspr.crawl()` über die Begriffsliste aus
   `zettel.scrapers.begriffe`. Der Lauf ist in `scrape_run` protokolliert,
   samt Begründung, falls er verworfen wird; die Statusseite des Shops liest
   genau diese Tabelle (Spec 11).
2. **Miniaturen ableiten** — `miniaturen.lauf()` zieht die Kachelbilder zu den
   frisch geholten Fotos nach (WB-374). Das gehört hierher und nicht in den
   Web-Prozess: umrechnen kostet ~11 ms je Bild, und der Antwortweg einer
   Seite, die gerade auf dem Telefon lädt, ist der falsche Ort dafür (Spec 3).
   Läuft auch dann, wenn der Crawl gescheitert ist — heruntergeladen sind die
   Bilder trotzdem.
3. **Sichern** — `betrieb.sichern()`, sieben Stände per `VACUUM INTO`.

Die Sicherung läuft **auch dann, wenn der Crawl scheitert**. Sie sichert nicht
den Crawl, sondern die Datenbank: Bestellungen, Rezepte und Chatverläufe sind
das, was sich nicht wiederbeschaffen lässt — der Katalog liesse sich morgen
neu holen.

Der Rückgabewert des Prozesses ist ungleich null, wenn der Katalog NICHT
aktualisiert wurde. Damit steht der Fehlschlag in `systemctl --user status
zettel-crawl` und nicht nur in einer Tabelle, in die niemand schaut.
"""
from __future__ import annotations

import argparse
import sys

from zettel import betrieb, db, miniaturen, umgebung
from zettel.scrapers import begriffe as begriffsliste
from zettel.scrapers import knuspr

#: Kennung gegenüber knuspr.de. Ein ehrlicher User-Agent statt eines
#: getarnten: wir holen einen öffentlichen Katalog in einem privaten Tempo
#: (`knuspr.PAUSE_S`), und wenn das jemandem nicht passt, soll er es sehen und
#: nicht raten müssen.
#:
#: REIN ASCII, und das ist keine Stilfrage: httpx kodiert Kopfzeilen als ASCII
#: und wirft bei einem Umlaut einen UnicodeEncodeError, noch bevor die erste
#: Anfrage rausgeht. Hier stand einmal „nächtlich" — der Dienst starb sofort
#: (gemessen 2026-08-28 an der echten Unit).
USER_AGENT = "zettel/1.0 (private household catalogue; nightly, 1 req/1.5 s)"

TIMEOUT_S = 30.0


def lauf(db_path: str, *, begriffe, image_dir: str | None,
         http=None, sichern: bool = True,
         sicherung_dir: str | None = None,
         staende: int = betrieb.STAENDE,
         pause_s: float = knuspr.PAUSE_S,
         schreib=print) -> dict:
    """Ein vollständiger Nachtlauf. Gibt den Bericht des Crawls zurück.

    `http` ist einspritzbar, damit die Testsuite denselben Weg ohne Netz gehen
    kann (Spec 13) — sie reicht einen Doppelgänger herein, der die
    aufgezeichnete Knuspr-Antwort liefert.
    """
    con = db.connect(db_path)
    eigener_client = http is None
    try:
        db.migrate(con)
        if eigener_client:
            import httpx
            http = httpx.Client(timeout=TIMEOUT_S,
                                headers={"User-Agent": USER_AGENT})
        schreib(f"Crawl: {len(begriffe)} Begriffe, Datenbank {db_path}")
        try:
            bericht = knuspr.crawl(con, http, begriffe, image_dir=image_dir,
                                   pause_s=pause_s)
        finally:
            if eigener_client:
                http.close()
        schreib(f"Lauf {bericht['run_id']}: {bericht['status']}"
                f", {bericht['n_products']} Produkte")
        if bericht["error"]:
            schreib(f"  Begründung: {bericht['error']}")
        # Was der Crawl nicht angefasst hat, steht noch mit „0,25 g" da
        # (UI-Review 2026-09-01, Fund 5). Idempotent, deshalb bei jedem Lauf.
        schreib(f"Einheiten nachgezogen: {knuspr.repariere_einheiten(con)}")
    finally:
        con.close()

    if image_dir:
        # Nach dem Crawl und ausserhalb des `try`: ein Fehlschlag des Crawls
        # lässt die schon geholten Bilder liegen, und die sollen trotzdem eine
        # Miniatur bekommen.
        miniaturen.lauf(image_dir, schreib=schreib)

    if sichern:
        # Erst nach dem Schliessen der Verbindung: `VACUUM INTO` verträgt keine
        # offene Transaktion, und eine eigene Verbindung ist der einfachste
        # Weg, sicher keine zu haben.
        ziel, weg = betrieb.sichern(db_path, sicherung_dir, behalten=staende)
        schreib(f"Sicherung: {ziel}"
                + (f" (abgeräumt: {', '.join(p.name for p in weg)})" if weg else ""))
    return bericht


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Nächtlicher Katalog-Lauf.")
    p.add_argument("--db", default=None,
                   help=f"Datenbankdatei (Vorgabe: $ZETTEL_DB oder {db.DEFAULT_DB})")
    p.add_argument("--image-dir", default=None,
                   help="Bildverzeichnis (Vorgabe: $ZETTEL_IMAGE_DIR oder data/images)")
    p.add_argument("--begriff", action="append", default=None,
                   help="einzelner Suchbegriff, mehrfach angebbar — für Handproben")
    p.add_argument("--begriffe-datei", default=None,
                   help="Datei mit einem Begriff je Zeile")
    p.add_argument("--sicherung-dir", default=None,
                   help="Vorgabe: ein Verzeichnis `sicherungen` neben der Datenbank")
    p.add_argument("--staende", type=int, default=betrieb.STAENDE)
    p.add_argument("--keine-sicherung", dest="sichern", action="store_false",
                   help="nur crawlen")
    p.add_argument("--pause", type=float, default=knuspr.PAUSE_S,
                   help="Sekunden zwischen zwei Anfragen")
    args = p.parse_args(argv)

    db_path = args.db or umgebung.wert("ZETTEL_DB") or db.DEFAULT_DB
    image_dir = (args.image_dir or umgebung.wert("ZETTEL_IMAGE_DIR")
                 or "data/images")
    if args.begriff:
        begriffe = args.begriff
    elif args.begriffe_datei:
        begriffe = begriffsliste.aus_datei(args.begriffe_datei)
    else:
        begriffe = begriffsliste.begriffe_aus_umgebung()

    bericht = lauf(db_path, begriffe=begriffe, image_dir=image_dir,
                   sichern=args.sichern, sicherung_dir=args.sicherung_dir,
                   staende=args.staende, pause_s=args.pause)
    # Nur ein „ok" heisst, dass der Katalog neu ist. `rejected` ist ein
    # gewolltes Verhalten und trotzdem ein Fehlschlag: der Katalog ist danach
    # so alt wie vorher, und das soll sichtbar sein.
    return 0 if bericht["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
