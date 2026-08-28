"""Handprobe: Zutaten aus der Quelle gegen Zutaten aus dem Gedächtnis (WB-338).

KEIN Test. Diese Probe geht ins Netz und an die echte vLLM-Box — sie
beantwortet die eine Frage, die ein Fake-LLM nicht beantworten kann: **ist die
Quelle wirklich besser als das Raten?**

    .venv/bin/python scripts/gericht_probe.py --gericht "Pho"
    .venv/bin/python scripts/gericht_probe.py --gericht "Spaghetti Bolognese"
    .venv/bin/python scripts/gericht_probe.py --gericht Pho --nur-vorher

Drei Teile, in dieser Reihenfolge:

1. **VORHER** — `plan.extract` allein, wie vor diesem Ticket: das Modell nennt
   die Zutaten des Gerichts aus dem Gedächtnis. Jeder Begriff wird gegen den
   echten Katalog gesucht.
2. **DIE QUELLE** — der Abruf bei Chefkoch (zwei Anfragen), das gewählte
   Rezept mit Bewertung und Herkunft.
3. **NACHHER** — derselbe Satz durch `Chat.turn()`, jetzt mit gefülltem
   Speicher. `weg` steht dann auf `chefkoch`.

Dazu die Handprobe zu WB-367:

    .venv/bin/python scripts/gericht_probe.py --gericht Ratatouille --erster-zug

**Leert den Speichereintrag und lässt genau EINEN Zug laufen.** Er muss
`weg = chefkoch` liefern, ohne dass vorher jemand etwas geholt hat — das ist
die Frage des Tickets, und sie lässt sich nur an einem Gericht stellen, das
noch nicht dasteht.

Gezählt wird ungeschönt: „hat ein Katalogprodukt gefunden" ist NICHT „hat das
richtige gefunden" — genau die Zahl, vor der EVALS.md warnt. Deshalb steht
unter jeder Liste, was gefunden wurde, und nicht nur wie viel.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picknick import db  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.assistant import plan  # noqa: E402
from picknick.catalog import search  # noqa: E402
from picknick.gerichte import chefkoch, quelle, speicher  # noqa: E402
from picknick.gerichte import lauf as gerichtelauf  # noqa: E402
from picknick.llm import wake  # noqa: E402
from picknick.llm.client import Modellzugang  # noqa: E402


def euro(cents) -> str:
    return "—" if cents is None else f"{cents // 100},{cents % 100:02d} €"


def vorher(zugang, con, satz: str) -> None:
    print("=" * 74)
    print(f"VORHER — das Modell nennt die Zutaten aus dem Gedächtnis")
    print("=" * 74)
    t0 = time.monotonic()
    try:
        ergebnis = plan.extract_plan(zugang, satz)
    except plan.PlanFehler as e:
        print(f"  Stufe 1 gescheitert: {e}")
        return
    dauer = time.monotonic() - t0
    print(f"  {len(ergebnis.zutaten)} Begriffe in {dauer:.1f} s, "
          f"erkanntes Gericht: {ergebnis.gericht!r}\n")
    getroffen = 0
    for b in ergebnis.zutaten:
        kette = b["suchbegriffe"]
        treffer = search.suche_kette(con, kette, limit=3, obergrenze=3)
        if treffer:
            getroffen += 1
            print(f"    {' -> '.join(kette):<44} {treffer[0]['name'][:40]}")
        else:
            print(f"    {' -> '.join(kette):<44} KEIN TREFFER")
    n = len(ergebnis.zutaten)
    print(f"\n  {getroffen} von {n} Begriffen finden überhaupt ein "
          f"Katalogprodukt.")


def hole(con, gericht: str) -> None:
    print()
    print("=" * 74)
    print("DIE QUELLE — zwei Anfragen an Chefkoch, dann nie wieder")
    print("=" * 74)
    import httpx

    http = httpx.Client(timeout=chefkoch.TIMEOUT_S,
                        headers={"User-Agent": chefkoch.USER_AGENT})
    try:
        t0 = time.monotonic()
        zustand = gerichtelauf.hole_eines(con, http, gericht,
                                          schreib=lambda z: print("  " + z))
        print(f"  ({time.monotonic() - t0:.1f} s, davon "
              f"{chefkoch.PAUSE_S} s Pause aus Höflichkeit)")
    finally:
        http.close()
    if zustand != "ok":
        return
    gefunden = quelle.Quelle().gericht(con, gericht)
    rezept = gefunden["rezept"]
    print(f"\n  „{rezept['name']}“")
    print(f"  {rezept['source_url']}")
    print(f"  {rezept['servings']} Portionen · "
          f"{rezept['prep_minutes']} Min. Zubereitung · "
          f"{rezept['cook_minutes']} Min. Kochzeit · "
          f"Schwierigkeit {rezept['difficulty']} · "
          f"{rezept['source_rating']} aus {rezept['source_votes']} Stimmen")
    print(f"  {len(chefkoch.schritte(rezept['instructions']))} Schritte "
          f"Zubereitung, {len(gefunden['zutaten'])} Zutaten:")
    for z in gefunden["zutaten"]:
        menge = "" if z["amount"] is None else f"{z['amount']:g}"
        print(f"    {menge:>6} {(z['unit'] or ''):<8} {z['raw_name']}")


def nachher(zugang, con, satz: str) -> None:
    print()
    print("=" * 74)
    print("NACHHER — derselbe Satz, jetzt mit dem Rezept im Speicher")
    print("=" * 74)
    agent = chatmodul.Chat(zugang, wecker=wake.Wecker(),
                           quelle=quelle.Quelle())
    t0 = time.monotonic()
    ergebnis = agent.turn(con, satz)
    print(f"  Weg: {ergebnis.weg}   ({time.monotonic() - t0:.1f} s)")
    print(f"  Herkunft: {ergebnis.quelle_name} — {ergebnis.quelle_url}")
    print(f"  Meldung: {ergebnis.meldung}\n")
    for v in ergebnis.vorschlaege:
        art = "FREITEXT" if v["ist_freitext"] else f"#{v['product_id']}"
        print(f"    {v['qty']} × {v['name'][:44]:<44} {art:>9}  "
              f"(gesucht: {v['search_term']!r})")
    print(f"\n  {ergebnis.n_produkte} Produkte, {ergebnis.n_freitext} "
          f"Freitext.")


def erster_zug(zugang, con, satz: str, gericht: str) -> None:
    """Ein einziger Zug zu einem Gericht, das noch niemand geholt hat (WB-367).

    Der Speichereintrag wird vorher gelöscht, sonst misst die Probe den
    Zwischenspeicher und nicht den Abruf. Das gelöschte Rezept bleibt in der
    Sammlung stehen — es ist fremde Arbeit und gehört dort hin; nur die
    `dish`-Zeile, also die Frage „wurde das schon geholt", fällt weg.
    """
    print()
    print("=" * 74)
    print("ERSTER ZUG — nichts im Speicher, ein Satz, und er nimmt das Rezept")
    print("=" * 74)
    con.execute("DELETE FROM dish WHERE name = ?",
                (speicher.schluessel(gericht),))
    con.commit()
    print(f"  dish-Eintrag zu {gericht!r} gelöscht: "
          f"{speicher.zeile(con, gericht) is None}")

    agent = chatmodul.Chat(zugang, wecker=wake.Wecker(), quelle=quelle.Quelle())
    t0 = time.monotonic()
    ergebnis = agent.turn(con, satz)
    dauer = time.monotonic() - t0
    print(f"  Weg: {ergebnis.weg}   Abruf: {ergebnis.abruf}   "
          f"({dauer:.1f} s für den ganzen Zug)")
    print(f"  Herkunft: {ergebnis.quelle_name} — {ergebnis.quelle_url}")
    print(f"  Meldung: {ergebnis.meldung}\n")
    for v in ergebnis.vorschlaege:
        art = "FREITEXT" if v["ist_freitext"] else f"#{v['product_id']}"
        print(f"    {v['qty']} × {v['name'][:44]:<44} {art:>9}  "
              f"(gesucht: {v['search_term']!r})")
    print(f"\n  {ergebnis.n_produkte} Produkte, {ergebnis.n_freitext} "
          f"Freitext.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=db.DEFAULT_DB)
    p.add_argument("--gericht", default="Pho")
    p.add_argument("--satz", default=None)
    p.add_argument("--nur-vorher", action="store_true")
    p.add_argument("--nur-nachher", action="store_true")
    p.add_argument("--erster-zug", action="store_true",
                   help="Speichereintrag löschen und EINEN Zug messen "
                        "(die Handprobe zu WB-367)")
    args = p.parse_args()
    satz = args.satz or f"alles für {args.gericht}"

    con = db.connect(args.db)
    db.migrate(con)
    n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                    ).fetchone()["n"]
    zustand = wake.zustand()
    print(f"Katalog: {n} aktive Produkte in {args.db}")
    print(f"Box: {zustand.zustand} {zustand.modell or ''}\n")
    if not zustand.bedient:
        print("Die Box bedient nicht — erst `wake-vllm`.")
        return 1
    zugang = Modellzugang()

    try:
        if args.erster_zug:
            erster_zug(zugang, con, satz, args.gericht)
            return 0
        if not args.nur_nachher:
            vorher(zugang, con, satz)
        if not args.nur_vorher:
            hole(con, args.gericht)
            nachher(zugang, con, satz)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
