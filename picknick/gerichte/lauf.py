"""Der Abruf bei Chefkoch — im Request (WB-367) und von Hand (WB-338).

    .venv/bin/python -m picknick.gerichte.lauf --gericht "pho"
    .venv/bin/python -m picknick.gerichte.lauf --alle
    .venv/bin/python -m picknick.gerichte.lauf --ohne-treffer

Zwei Eingänge in dieselbe Bahn, und der Unterschied ist nur die Frist:

* `hole_jetzt()` — **der Normalfall seit WB-367.** Der Chat ruft ihn im
  Request auf, wenn der Zwischenspeicher das Gericht nicht kennt. Kurze
  Frist (`chefkoch.TIMEOUT_SYNC_S`), keine Pause zwischen den zwei Anfragen:
  hier wartet jemand zu.
* `main()`/`lauf()` — **das Kommando von Hand.** Zum Vorwärmen („hol mir die
  zehn Gerichte, die wir dauernd kochen"), zum Nachholen dessen, was eine
  Störung liegen gelassen hat (`--alle`), und um zu sehen, ob die Quelle
  überhaupt noch antwortet. Lange Frist, `chefkoch.PAUSE_S` zwischen zwei
  Gerichten — dort schaut niemand zu, und Höflichkeit gegenüber einer fremden
  Seite hängt nicht davon ab, ob es gerade auffällt.

**Was WB-338 hier begründete, gilt nicht mehr wörtlich.** Damals startete der
Shop diesen Lauf als eigenen Prozess, weil Spec 3 sagte, der Web-Prozess rufe
nie eine fremde Seite auf. Der Preis war, dass der erste Satz zu einem neuen
Gericht noch geraten wurde. Gemessen kostet der Abruf 90 bis 147 ms und der
Modellweg daneben 35.600 ms; die Regel bleibt für den KATALOG (er wird nie
live abgefragt) und ist für den Chat zurückgenommen. Der Prozessstart aus dem
Shop heraus ist damit weg — dieses Kommando nicht, es trägt die beiden Fälle
oben.

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


def hole_jetzt(con, gericht: str, *,
               frist_s: float = chefkoch.TIMEOUT_SYNC_S, http=None,
               schreib=lambda _: None) -> str:
    """Holt EIN Gericht SOFORT, in dem Prozess, der gerade fragt (WB-367).

    Gibt denselben Zustand zurück wie `hole_eines` (`ok`, `leer`, `fehler`)
    und wirft aus demselben Grund nicht: ein Ausfall der Quelle wird eine
    Zeile in der Datenbank und danach der Modellweg, kein Traceback in einem
    Request.

    **Keine Pause zwischen Suche und Detail** (`pause_s=0`). Die 1,5 s aus
    `chefkoch.PAUSE_S` sind Höflichkeit für einen LAUF über viele Gerichte;
    hier fallen genau zwei Anfragen an, einmal im Leben dieses Gerichts, und
    ein Mensch wartet darauf. Vier Sekunden Frist gegen 100 ms Messung sind
    reichlich, anderthalb Sekunden Schlafen daneben wären es nicht.

    `schreib` ist hier still: der Request hat kein Terminal, und der Zustand
    steht in `dish`.
    """
    eigener_client = http is None
    if eigener_client:
        import httpx
        http = httpx.Client(timeout=frist_s,
                            headers={"User-Agent": chefkoch.USER_AGENT})
    try:
        return hole_eines(con, http, gericht, pause_s=0.0, schreib=schreib)
    finally:
        if eigener_client:
            http.close()


def waehle_jetzt(con, gericht: str, treffer: dict, *,
                 frist_s: float = chefkoch.TIMEOUT_SYNC_S, http=None,
                 schreib=lambda _: None) -> str:
    """Ein Mensch hat ein ANDERES Rezept gewählt (WB-387). Eine Anfrage.

    `treffer` ist die Zeile aus `dish_treffer`, also ein Rezept aus genau der
    Suchantwort, die dieses Gericht ohnehin schon gekostet hat. **Es wird
    deshalb nicht noch einmal gesucht** — nur das Detail des gewählten
    Rezepts fehlt (Zutaten, Zubereitung, Koch- und Ruhezeit), und das ist
    eine Anfrage statt zweier.

    Danach zeigt `dish` auf das gewählte Rezept, und der nächste Zug zu
    diesem Gericht nimmt es wie jedes andere — kein zweiter Mechanismus
    daneben.

    Gibt denselben Zustand zurück wie `hole_eines` (`ok`, `fehler`) und wirft
    aus demselben Grund nicht. `leer` gibt es hier nicht: das Rezept liegt
    vor, gefragt wird nur nach seinem Detail.
    """
    eigener_client = http is None
    if eigener_client:
        import httpx
        http = httpx.Client(timeout=frist_s,
                            headers={"User-Agent": chefkoch.USER_AGENT})
    try:
        rezept = chefkoch.hole_detail(http, treffer)
    except chefkoch.ChefkochFehler as e:
        # **Der Zwischenspeicher wird NICHT auf `fehler` gesetzt.** Anders als
        # beim ersten Abruf steht hier ein gültiges Rezept in `dish`; es
        # wegen einer misslungenen Wahl zu entwerten hiesse, dem Nutzer für
        # einen Fehlgriff der Quelle auch noch das zu nehmen, was er schon
        # hatte.
        schreib(f"fehler: {gericht} — {e}")
        return speicher.FEHLER
    finally:
        if eigener_client:
            http.close()
    recipe_id = speicher.merken(con, gericht, rezept)
    schreib(f"ok: {gericht} -> „{rezept['titel']}“ "
            f"({len(rezept['zutaten'])} Zutaten) -> recipe {recipe_id}")
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
    ap.add_argument("--ohne-treffer", action="store_true",
                    dest="ohne_treffer",
                    help="Gerichte neu holen, zu denen keine Trefferliste "
                         "gespeichert ist (Altbestand vor WB-387)")
    ap.add_argument("--pause", type=float, default=chefkoch.PAUSE_S)
    args = ap.parse_args(argv)

    gerichte = list(args.gericht)
    if args.alle or args.ohne_treffer:
        con = db.connect(args.db)
        try:
            wuensche = []
            if args.alle:
                wuensche += speicher.offene(con)
            if args.ohne_treffer:
                # Der Altbestand von WB-387 (siehe `speicher.ohne_treffer`).
                # **Das holt bestehende Rezepte neu**, und dabei kann ein
                # besser bewertetes gewinnen — genau das, was nach 90 Tagen
                # ohnehin geschieht. Deshalb steht es hinter einem eigenen
                # Schalter und nicht in `--alle`.
                wuensche += speicher.ohne_treffer(con)
            gerichte += [w["query"] for w in wuensche
                         if w["query"] not in gerichte]
        finally:
            con.close()
    if not gerichte:
        print("nichts zu holen — --gericht, --alle oder --ohne-treffer "
              "angeben", file=sys.stderr)
        return 2

    zaehler = lauf(args.db, gerichte, pause_s=args.pause)
    print(f"{zaehler.get('ok', 0)} geholt, {zaehler.get('leer', 0)} ohne "
          f"Rezept, {zaehler.get('fehler', 0)} fehlgeschlagen")
    # Ungleich null, wenn NICHTS geholt wurde: ein Lauf, der alles verloren
    # hat, soll sich nicht wie ein guter Lauf verhalten.
    return 0 if zaehler.get("ok", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
