"""Handprobe der Katalogsuche gegen die ECHTE Datei (WB-339). KEIN Test.

    .venv/bin/python scripts/suche_probe.py
    .venv/bin/python scripts/suche_probe.py Sellerie Sahne Reis
    .venv/bin/python scripts/suche_probe.py --treffer 10 Milch

Zeigt je Begriff zwei Listen nebeneinander: **vorher** die Reihenfolge, die
allein bm25 ergibt (so sortierte die Suche bis WB-339), und **nachher** die,
die `search()` heute liefert. Beide werden aus DEMSELBEN Kandidatenpool
gebildet — der Unterschied ist also die Sortierung und nichts sonst.

Warum das kein Test ist: `data/picknick.db` ist gitignored und ändert sich mit
jedem Crawl. Ein Test, der an dieser Datei hängt, ist morgen rot, ohne dass
jemand etwas kaputt gemacht hat. Die Fälle aus diesem Skript stehen deshalb
als kleiner Testkatalog in `tests/test_catalog.py` nach; hier geht es um die
Frage, die ein Testkatalog nicht beantworten kann: hält die Sortierung auch
gegen 10.361 echte Produkte, oder war der Testkatalog nur freundlich?

Braucht nichts ausser der Datenbankdatei — kein Netz, kein Modell.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picknick import db  # noqa: E402
from picknick.catalog import search  # noqa: E402

#: Die sechs Begriffe aus WB-339, gemessen am 28.08.2026. Bei allen lagen
#: zwischen dem Richtigen und dem Kompositum 0,01 bis 0,17 Rang — für bm25
#: nicht unterscheidbar.
BEGRIFFE = ["Zwiebel", "Spaghetti", "Milch", "mehl", "Mais", "Sellerie"]


#: So gross ist der Pool, aus dem beide Spalten gebildet werden. Gross genug,
#: dass er die besten bm25-Treffer sicher enthält — sonst zeigte die linke
#: Spalte nicht den alten Stand, sondern nur die alte Reihenfolge der neuen
#: Auswahl, und der Vergleich wäre geschönt.
POOL = 1000


def vorher(treffer: list[dict]) -> list[dict]:
    """Dieselben Kandidaten, nur nach bm25 sortiert — der Stand vor WB-339."""
    return sorted(treffer, key=lambda p: (-p["rang"], p["name"]))


def zeile(p: dict, breite: int) -> str:
    name = p["name"]
    if len(name) > breite:
        name = name[:breite - 1] + "…"
    return f"{p['rang']:6.2f}  {name:<{breite}}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("begriffe", nargs="*", default=None)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--treffer", type=int, default=5)
    args = ap.parse_args()

    pfad = Path(args.db)
    if not pfad.exists():
        print(f"Keine Katalogdatei unter {pfad} — erst crawlen.")
        return 1

    con = db.connect(pfad)
    anzahl = con.execute(
        "SELECT count(*) FROM product WHERE active = 1").fetchone()[0]
    print(f"{pfad} — {anzahl} aktive Produkte\n")

    breite = 44
    for begriff in (args.begriffe or BEGRIFFE):
        pool = search.search(con, begriff, limit=POOL)
        neu = pool[:args.treffer]
        alt = vorher(pool)[:args.treffer]
        print(f"„{begriff}“")
        print(f"  {'VORHER (nur bm25)':<{breite + 8}}"
              f"  NACHHER (Wortstufe, dann bm25)")
        for a, n in zip(alt, neu):
            # Die Stufe steht nur rechts: links gab es sie noch nicht.
            print(f"  {zeile(a, breite)}  s{n['wortstufe']} {zeile(n, breite)}")
        print()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
