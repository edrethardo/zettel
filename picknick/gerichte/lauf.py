"""Der Abruf als eigener Prozess — das Einzige, was Chefkoch anfasst (WB-338).

    .venv/bin/python -m picknick.gerichte.lauf --gericht "pho"
    .venv/bin/python -m picknick.gerichte.lauf --alle

Genau das startet `picknick.gerichte.quelle.Quelle.anfordern()`, und genau
das ruft man von Hand auf, wenn man wissen will, ob es noch geht. Damit gilt
Spec 3 wörtlich: **der Web-Prozess ruft nie eine fremde Seite auf** — dieser
hier tut es, wie der Katalog-Crawler, in einem eigenen Prozess mit eigenem
Ausgang.

`--alle` arbeitet die offenen Wünsche ab. Das ist der Weg für einen Timer
oder für „hol nach, was gestern liegen geblieben ist"; zwischen zwei
Gerichten wird gewartet (`chefkoch.PAUSE_S`), weil Höflichkeit gegenüber
einer fremden Seite nicht davon abhängt, ob gerade jemand zuschaut.

**Ein Fehlschlag wird VERMERKT, nicht verschwiegen.** Eine Störung landet als
`status = 'fehler'` mit ihrem Text in `dish`, ein „kennt Chefkoch nicht" als
`status = 'leer'`. Beides hält den nächsten Zug davon ab, sofort wieder
anzufragen, und beides ist im Shop ablesbar. Der Rückgabewert des Prozesses
ist ungleich null, wenn kein einziges Gericht geholt werden konnte — damit
steht der Fehlschlag auch in `systemctl status` und nicht nur in einer
Tabelle, in die niemand schaut.
"""
from __future__ import annotations

import argparse
import sys

from picknick import db
from picknick.gerichte import chefkoch, speicher


def hole_eines(con, http, gericht: str, *, pause_s: float = chefkoch.PAUSE_S,
               schlafen=None, schreib=print) -> str:
    """Holt EIN Gericht und schreibt das Ergebnis in den Speicher.

    Gibt den Zustand zurück (`ok`, `leer`, `fehler`). Wirft nicht: der Sinn
    dieser Funktion ist, dass ein Ausfall der Quelle eine Zeile in der
    Datenbank wird und kein Traceback.
    """
    zusatz = {} if schlafen is None else {"schlafen": schlafen}
    try:
        rezept = chefkoch.hole(http, gericht, pause_s=pause_s, **zusatz)
    except chefkoch.ChefkochFehler as e:
        speicher.vermerken(con, gericht, speicher.FEHLER, str(e))
        schreib(f"fehler: {gericht} — {e}")
        return speicher.FEHLER
    if rezept is None:
        speicher.vermerken(con, gericht, speicher.LEER,
                           "Kein Rezept zu diesem Gericht gefunden.")
        schreib(f"leer: {gericht} — die Quelle kennt es nicht")
        return speicher.LEER

    recipe_id = speicher.merken(con, gericht, rezept)
    note = rezept.get("rating")
    bewertung = (f"{note:.2f} aus {rezept.get('votes') or 0} Stimmen"
                 if note is not None else "ohne Bewertung")
    schreib(f"ok: {gericht} -> „{rezept['titel']}“ ({bewertung}, "
            f"{len(rezept['zutaten'])} Zutaten, "
            f"{len(rezept['schritte'])} Schritte) -> recipe {recipe_id}")
    return speicher.OK


def lauf(db_path: str, gerichte, *, http=None, pause_s: float = chefkoch.PAUSE_S,
         schreib=print) -> dict:
    """Holt eine Reihe von Gerichten. Gibt eine Zusammenfassung zurück.

    `http` ist einspritzbar, damit dieselbe Bahn ohne Netz getestet werden
    kann (Spec 13) — die Testsuite reicht einen Doppelgänger mit den
    aufgezeichneten Chefkoch-Antworten herein.
    """
    con = db.connect(db_path)
    # **`migrate()` gehört hierher und nicht nur in den Web-Prozess.** Dieser
    # Lauf ist ein EIGENER Prozess und wird auch von Hand gestartet — an einer
    # Datenbank, die noch keiner migriert hat, fiel er sonst mit „no such
    # column: source" um (gemessen beim ersten Lauf gegen die echte Datei).
    # `migrate()` ist idempotent und kostet Millisekunden.
    db.migrate(con)
    eigener_client = http is None
    if eigener_client:
        import httpx
        http = httpx.Client(timeout=chefkoch.TIMEOUT_S,
                            headers={"User-Agent": chefkoch.USER_AGENT})
    zaehler = {speicher.OK: 0, speicher.LEER: 0, speicher.FEHLER: 0}
    try:
        for i, gericht in enumerate(gerichte):
            if i and pause_s:
                import time
                time.sleep(pause_s)
            zustand = hole_eines(con, http, gericht, pause_s=pause_s,
                                 schreib=schreib)
            zaehler[zustand] = zaehler.get(zustand, 0) + 1
    finally:
        if eigener_client:
            http.close()
        con.close()
    return zaehler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--gericht", action="append", default=[],
                    help="Gerichtsname; mehrfach erlaubt")
    ap.add_argument("--alle", action="store_true",
                    help="alle offenen Wünsche aus `dish` abarbeiten")
    ap.add_argument("--pause", type=float, default=chefkoch.PAUSE_S)
    args = ap.parse_args(argv)

    gerichte = list(args.gericht)
    if args.alle:
        con = db.connect(args.db)
        try:
            gerichte += [w["query"] for w in speicher.offene(con)
                         if w["query"] not in gerichte]
        finally:
            con.close()
    if not gerichte:
        print("nichts zu holen — --gericht oder --alle angeben",
              file=sys.stderr)
        return 2

    zaehler = lauf(args.db, gerichte, pause_s=args.pause)
    print(f"{zaehler.get('ok', 0)} geholt, {zaehler.get('leer', 0)} ohne "
          f"Rezept, {zaehler.get('fehler', 0)} fehlgeschlagen")
    # Ungleich null, wenn NICHTS geholt wurde: ein Lauf, der alles verloren
    # hat, soll sich nicht wie ein guter Lauf verhalten.
    return 0 if zaehler.get("ok", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
