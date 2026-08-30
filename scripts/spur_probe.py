"""Verträgt Stufe 1 dasselbe Zerlegen wie Stufe 3? (WB-413)

KEIN Test. Diese Probe geht an die echte vLLM-Box. Sie schreibt nichts in die
Datenbank — sie liest Rezepte und fragt das Modell.

    .venv/bin/python scripts/spur_probe.py --db data/picknick.db --wdh 2

**Die Frage.** WB-412 hat Stufe 3 in vier gleichzeitige Anfragen zerlegt und
damit 16,7 s auf 9,4 s gedrückt, ohne dass sich die Wahl änderte. Für Stufe 1
(`plan.zutatenbegriffe`, Median 11,53 s) wäre dasselbe naheliegend — aber es
ist nicht dieselbe Aufgabe. Stufe 3 beantwortet je Begriff eine für sich
stehende Frage; Stufe 1 bekommt EINE Zutatenliste und soll daraus EINE Liste
von Suchbegriffen machen. Dabei sieht das Modell heute alles auf einmal, und
es nutzt das: es lässt Salz und Pfeffer weg, fasst „Zwiebel" und
„Gemüsezwiebel" nicht zusammen, und es erzeugt jeden Begriff genau einmal.

Zerlegt sieht jede Spur nur ihr Stück. Drei Dinge können deshalb kaputtgehen,
und genau die misst diese Probe:

    abdeckung   wie viele Zutaten überhaupt eine Begriffskette bekommen
    doppelt     dieselbe Kette aus zwei Spuren — keine Spur sieht die andere
    deckung     wie sehr die Ketten denen des ganzen Laufs gleichen

**Ohne Rauschband ist keine dieser Zahlen lesbar.** Die Box ist auch bei
Temperatur 0 nicht deterministisch (WB-380 hat einen Ausreisser gesehen).
Deshalb läuft `ganz` mindestens zweimal, und die Ausgabe stellt „ganz gegen
sich selbst" neben „zerlegt gegen ganz". Ist der Unterschied nicht grösser
als das Rauschen, kostet das Zerlegen nichts — und erst dann darf es in den
Shop.

Die Varianten:

    ganz       eine Anfrage, wie heute
    spuren2    die Zutatenliste reihum auf 2 gleichzeitige Anfragen
    spuren4    dieselbe auf 4

Reihum (`[i::k]`) und nicht in Blöcken, aus demselben Grund wie in
`plan._choose_gleichzeitig`: die Zutatenliste ist nach Zubereitungsschritten
geordnet („für die Sauce", „für den Teig"), und ein Block wäre ein Thema
statt eines Querschnitts.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextvars
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zettel import db, obs                                  # noqa: E402
from zettel.assistant import plan                           # noqa: E402
from zettel.gerichte import speicher                        # noqa: E402
from zettel.llm.client import Modellzugang                  # noqa: E402

VARIANTEN = (("ganz", 1), ("spuren2", 2), ("spuren4", 4))


def _kette(eintrag: dict) -> str:
    """Der erste Begriff einer Kette, normalisiert — ihr Name."""
    worte = eintrag.get("suchbegriffe") or []
    return " ".join(db.normalisiere(str(worte[0]).casefold()).split()) \
        if worte else ""


def _lauf(zugang, rezept: dict, zutaten: list[dict], spuren: int,
          variante: str = "") -> dict:
    """Ein Durchgang durch Stufe 1 — ganz oder in Spuren.

    **Der Lauf trägt einen CHAIN-Span**, wenn `--trace` gesetzt ist. Ohne ihn
    hingen die Modellaufrufe elternlos in Phoenix, und die eine Frage, um die
    es hier geht — „was hat die Spur mit den drei Zutaten geantwortet" —
    liesse sich nicht mehr stellen: die Antwort steht am LLM-Span, aber
    welcher zu welcher Variante gehört, stünde nirgends.

    `stufe(..., mehrfach=True)` aus demselben Grund wie in WB-412: bei
    mehreren Spuren sind es mehrere `plan.extract`, und ohne das Kennzeichen
    hiesse nur der schnellste so.
    """
    teile = ([zutaten] if spuren == 1
             else [t for t in (zutaten[i::spuren] for i in range(spuren)) if t])
    t0 = time.perf_counter()
    with obs.chain("stufe1.probe", eingabe=rezept["name"]) as span:
        obs.setze(span, {"zettel.variante": variante or f"spuren{spuren}",
                         "zettel.spuren": len(teile),
                         "zettel.zutaten": len(zutaten),
                         "zettel.recipe_id": int(rezept["id"])})
        with obs.stufe("plan.extract", mehrfach=True):
            if len(teile) == 1:
                begriffe = plan.zutatenbegriffe(zugang, teile[0],
                                                gericht=rezept["name"],
                                                servings=rezept["servings"])
            else:
                with cf.ThreadPoolExecutor(max_workers=len(teile)) as pool:
                    laeufe = [pool.submit(contextvars.copy_context().run,
                                          plan.zutatenbegriffe, zugang, teil,
                                          gericht=rezept["name"],
                                          servings=rezept["servings"])
                              for teil in teile]
                    begriffe = [b for f in laeufe for b in f.result()]
        dauer = time.perf_counter() - t0

        namen = [_kette(b) for b in begriffe if _kette(b)]
        herkunft = {b.get("zutat") for b in begriffe if b.get("zutat")}
        erg = {"n_ketten": len(begriffe),
               "namen": namen,
               "doppelt": len(namen) - len(set(namen)),
               "abdeckung": len(herkunft) / len(zutaten) if zutaten else 0.0,
               "sekunden": dauer}
        # Das ERGEBNIS an den Span und nicht nur die Zeit. Ein Span, der
        # nichts als eine Dauer trägt, beantwortet keine Frage, die man nicht
        # auch der Tabelle stellen könnte — die Ketten dagegen stehen sonst
        # nirgends, und sie sind der Grund für das Urteil.
        obs.setze_ausgabe(span, {"ketten": namen,
                                 "abdeckung": round(erg["abdeckung"], 3),
                                 "doppelt": erg["doppelt"]})
        return erg


def _deckung(a: list[str], b: list[str]) -> float:
    """Wie viel von `a` in `b` wiederkommt. 1,0 heisst: nichts fehlt."""
    if not a:
        return 1.0
    return sum(1 for name in set(a) if name in set(b)) / len(set(a))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/picknick.db",
                   help="wird nur GELESEN — die Probe schreibt nichts")
    p.add_argument("--mindestens", type=int, default=8,
                   help="nur Rezepte ab so vielen Zutaten")
    p.add_argument("--rezepte", type=int, default=0,
                   help="höchstens so viele Rezepte (0 = alle)")
    p.add_argument("--wdh", type=int, default=2,
                   help="Läufe je Variante — mindestens 2, sonst gibt es "
                        "kein Rauschband")
    p.add_argument("--json", default=None, help="Messzeilen hierhin")
    p.add_argument("--trace", action="store_true",
                   help="die Läufe nach Phoenix schicken")
    p.add_argument("--projekt", default="Zettel Spur-Probe",
                   help="Phoenix-Projekt (Vorgabe: %(default)s)")
    a = p.parse_args()

    if a.trace:
        # Erst ab hier und nicht per Vorgabe — dieselbe Regel wie in
        # `breite_probe.py`: ein Messlauf mit 90 Modellaufrufen soll das
        # Alltagsprojekt „Zettel Agent" nicht fluten. Und ein EIGENES
        # Projekt, damit die Läufe der Probe nicht neben den Zügen der
        # Nutzerin stehen; sie beantworten eine andere Frage.
        if obs.einrichten(projekt=a.projekt) is not None:
            stand = obs.tracerstand()
            print(f"Trace: Projekt {stand['projekt']!r} auf "
                  f"{stand['endpunkt']}")
        else:
            print("Trace: nicht eingerichtet — die Probe läuft ohne.")

    con = db.connect(a.db)
    rezepte = con.execute(
        "SELECT r.id, r.name, r.servings, count(*) AS n FROM recipe r"
        "  JOIN recipe_ingredient i ON i.recipe_id = r.id"
        " GROUP BY r.id HAVING n >= ? ORDER BY n DESC", (a.mindestens,)
    ).fetchall()
    if a.rezepte:
        rezepte = rezepte[:a.rezepte]
    if not rezepte:
        print("Keine Rezepte mit genug Zutaten.")
        return 1

    zugang = Modellzugang()
    zeilen: list[dict] = []
    print(f"{len(rezepte)} Rezepte, {a.wdh} Läufe je Variante, "
          f"Modell {zugang.modell()}\n")

    for r in rezepte:
        zutaten = speicher.zutaten(con, int(r["id"]))
        print(f"{r['name'][:52]:54} {len(zutaten):2} Zutaten",
              flush=True)
        referenz: list[str] | None = None
        for name, spuren in VARIANTEN:
            for lauf in range(a.wdh):
                try:
                    erg = _lauf(zugang, dict(r), zutaten, spuren,
                                variante=name)
                except Exception as e:                    # noqa: BLE001
                    print(f"   {name:8} Lauf {lauf + 1}: "
                          f"{type(e).__name__}: {e}", flush=True)
                    continue
                if referenz is None:
                    referenz = erg["namen"]
                    deckung = 1.0
                else:
                    deckung = _deckung(referenz, erg["namen"])
                zeilen.append({"rezept": r["name"], "zutaten": len(zutaten),
                               "variante": name, "lauf": lauf + 1,
                               "deckung": deckung,
                               **{k: v for k, v in erg.items()
                                  if k != "namen"}})
                print(f"   {name:8} Lauf {lauf + 1}: {erg['sekunden']:5.1f} s  "
                      f"{erg['n_ketten']:2} Ketten  "
                      f"Abdeckung {erg['abdeckung']:4.0%}  "
                      f"doppelt {erg['doppelt']}  "
                      f"Deckung zur Referenz {deckung:4.0%}", flush=True)

    print("\n" + "=" * 74)
    print(f"{'Variante':10} {'Sekunden':>9} {'Ketten':>7} {'Abdeckung':>10} "
          f"{'doppelt':>8} {'Deckung':>9}")
    for name, _ in VARIANTEN:
        teil = [z for z in zeilen if z["variante"] == name]
        if not teil:
            continue
        # Der erste Lauf von `ganz` IST die Referenz und hat deshalb per
        # Definition 100 % — er gehört nicht in den Vergleich.
        vergleich = [z for z in teil
                     if not (name == "ganz" and z["lauf"] == 1)]
        print(f"{name:10} "
              f"{statistics.median(z['sekunden'] for z in teil):8.1f}s "
              f"{statistics.median(z['n_ketten'] for z in teil):7.1f} "
              f"{statistics.mean(z['abdeckung'] for z in teil):9.0%} "
              f"{sum(z['doppelt'] for z in teil):8} "
              + (f"{statistics.mean(z['deckung'] for z in vergleich):8.0%}"
                 if vergleich else "        —"))
    print("\nDie Zeile `ganz` in der Spalte Deckung ist das RAUSCHBAND: so gut")
    print("stimmt der ganze Lauf mit sich selbst überein. Alles, was nicht")
    print("darunter liegt, ist kein Schaden des Zerlegens.")

    if a.json:
        Path(a.json).write_text(
            "\n".join(json.dumps(z, ensure_ascii=False) for z in zeilen),
            encoding="utf-8")
        print(f"\n{len(zeilen)} Messzeilen -> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
