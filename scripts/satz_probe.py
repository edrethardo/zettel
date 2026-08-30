"""Stufe 3 gegen EINGEFRORENE Kandidaten — wovon hängt die Wahl ab? (WB-386)

KEIN Test. Diese Probe geht an die echte vLLM-Box, aber **nicht** ins Netz
und **nicht** durch die Suche: sie friert erst die Vorlage ein, die Stufe 3
bekommen würde, und variiert danach nur noch den Prompt.

    # 1. einfrieren (braucht die Box für Stufe 1, den Katalog für Stufe 2)
    .venv/bin/python scripts/satz_probe.py --einfrieren --db kopie.db \\
        --nach vorlagen.json

    # 2. messen (braucht nur noch die Box, kein Katalog, kein Chefkoch)
    .venv/bin/python scripts/satz_probe.py --messen --aus vorlagen.json \\
        --wdh 3

**Warum eingefroren?** Weil die Frage des Tickets ein Vergleich ist: „Salat"
holt 0 von 9, „alles für Salat" holt 6 von 9. Fährt man beide Sätze durch den
ganzen Zug, unterscheiden sie sich auch in der Zutatenliste, in den
Suchbegriffen und in den Kandidaten — und der Vergleich misst dann alles
zusammen. Eingefroren unterscheidet sich genau ein String.

**Warum mehrfach?** Temperatur 0 macht die Box nicht deterministisch (WB-380
hat einen Ausreisser gesehen: Bibimbap stand parallel auf 0 und einzeln auf
7). Eine einzelne Zahl je Variante wäre nicht unterscheidbar von Rauschen;
`--wdh` fährt jede Variante mehrfach und die Ausgabe zeigt jeden Lauf.

## Die Varianten

Jede Variante ändert genau EINE Sache am Prompt von `plan.choose`:

    satz         wie heute: „Anfrage: Salat"
    satz_ganz    „Anfrage: alles für Salat" — die Kontrollzeile aus WB-380
    ohne_satz    gar keine Anfragezeile
    rezept       „Rezept: <Name>" STATT der Anfrage
    rezept_satz  „Rezept: <Name>" ÜBER der Anfrage — beides
    regel        wie heute, plus eine Zeile in der Anweisung

Die Zusicherung von Stufe 3 ist in keiner Variante berührt: gewählt wird
immer nur aus den vorgelegten Kandidaten, und `plan.choose` verwirft
Erfundenes wie sonst auch. Was hier variiert, ist der Kontext der Wahl.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zettel import db  # noqa: E402
from zettel.assistant import chat as chatmodul  # noqa: E402
from zettel.assistant import plan  # noqa: E402
from zettel.gerichte import quelle  # noqa: E402
from zettel.llm import wake  # noqa: E402
from zettel.llm.client import Modellzugang  # noqa: E402

#: Die Gerichte, deren Vorlage eingefroren wird — und warum jedes dasteht.
#: Die ersten beiden sind die Fälle aus WB-380 (Rezept schief, 0 Treffer),
#: „Salat als ganzer Satz" ist die Kontrolle, der Rest ist der GEGENBEWEIS:
#: eine Änderung, die dort etwas kaputtmacht, ist keine Verbesserung.
GERICHTE: list[tuple[str, str]] = [
    ("Salat", "WB-380: Rezept schief (KFC Coleslaw), 0 von 9"),
    ("Kartoffelpürree", "WB-380: Rezept schief, 0 Treffer"),
    ("Auflauf", "mehrdeutig, ein Wort"),
    ("Suppe", "mehrdeutig, ein Wort"),
    ("Eintopf", "mehrdeutig, ein Wort"),
    ("alles für Salat", "Kontrolle: dasselbe Gericht als ganzer Satz"),
    # Der Gegenfall zur Vermutung des Tickets: Satz und Rezept passen hier
    # zusammen („alles für Königsberger Klopse" -> „Königsberger Klopse"),
    # und trotzdem stand das Gericht im Lauf vom 2026-08-29 auf 0 von 16.
    ("alles für Königsberger Klopse",
     "Gegenfall: Satz und Rezept passen, trotzdem 0 von 16"),
    ("alles für Lasagne", "Gegenbeweis: Rezept passt"),
    ("alles für Chili con Carne", "Gegenbeweis: Rezept passt"),
    ("alles für Apfelkuchen", "Gegenbeweis: Backen, viele Grundzutaten"),
]

#: Die Zusatzzeile der Variante `regel`. Sie steht am Ende der Regeln, weil
#: sie eine AUSNAHME zur Regel darüber ist („passt nichts, lass ihn weg") und
#: eine Ausnahme nach der Regel gelesen werden muss, nicht davor.
REGEL_ZEILE = ("\n- Jeder Begriff wird für sich entschieden. Ob das Gericht "
               "zur Anfrage passt, ist nicht deine Frage: Milch bleibt Milch.")


def varianten(satz: str, gericht: str | None) -> dict[str, dict]:
    """Die Varianten für EINE eingefrorene Vorlage.

    Fehlt der Rezeptname (Modellweg), fallen die Rezeptvarianten weg statt
    still auf den Satz zurückzufallen — sonst stünde in der Tabelle unter
    „rezept" in Wahrheit „satz".
    """
    v = {
        "satz": {"satz": satz},
        "satz_ganz": {"satz": satz if satz.startswith("alles für")
                      else f"alles für {satz}"},
        "ohne_satz": {"satz": ""},
        "regel": {"satz": satz,
                  "system": plan.SYSTEM_CHOOSE + REGEL_ZEILE},
    }
    if gericht:
        v["rezept"] = {"satz": "", "gericht": gericht}
        v["rezept_satz"] = {"satz": satz, "gericht": gericht}
    return v


# --------------------------------------------------------------------------
# Einfrieren


class _Faenger(chatmodul.Chat):
    """Ein Chat, der vor Stufe 3 stehen bleibt.

    Der Zug läuft echt — Rezept, Stufe 1, Suche —, nur die Wahl fällt aus.
    So ist die eingefrorene Vorlage garantiert die, die der Shop vorlegt,
    und keine nachgebaute.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.gefangen: list[dict] = []

    def _waehlen(self, text, aufgaben, system=None):
        self.gefangen.append({"text": text, "aufgaben": aufgaben})
        return plan.Auswahl(), None


def einfrieren(basis_db: str, saetze, zugang) -> list[dict]:
    vorlagen = []
    for satz, warum in saetze:
        con = db.connect(basis_db)
        db.migrate(con)
        t0 = time.monotonic()
        try:
            agent = _Faenger(zugang, wecker=wake.Wecker(),
                             quelle=quelle.Quelle())
            ergebnis = agent.turn(con, satz)
        except Exception as e:                   # noqa: BLE001 — Probe
            print(f"  {satz:<28} FEHLER {type(e).__name__}: {e}")
            con.close()
            continue
        con.close()
        if not agent.gefangen:
            print(f"  {satz:<28} keine Stufe 3 (weg={ergebnis.weg})")
            continue
        f = agent.gefangen[0]
        aufgaben = [{"begriff": a["begriff"], "menge": a.get("menge", 1),
                     "suchbegriffe": a.get("suchbegriffe") or [],
                     "kandidaten": [plan.kandidat_kurz(p)
                                    for p in a["kandidaten"]]}
                    for a in f["aufgaben"] if a.get("kandidaten")]
        vorlagen.append({"satz": satz, "warum": warum, "weg": ergebnis.weg,
                         "gericht": ergebnis.quelle_name or ergebnis.gericht,
                         "aufgaben": aufgaben})
        print(f"  {satz:<28} {ergebnis.weg:<8} "
              f"„{(ergebnis.quelle_name or '—')[:34]}“ — "
              f"{len(aufgaben)} Begriffe mit Kandidaten, "
              f"{sum(len(a['kandidaten']) for a in aufgaben)} Kandidaten "
              f"({time.monotonic() - t0:.0f} s)")
    return vorlagen


# --------------------------------------------------------------------------
# Messen


def _lauf(zugang, vorlage: dict, einstellung: dict) -> dict:
    t0 = time.monotonic()
    try:
        auswahl = plan.choose(zugang, einstellung.get("satz", ""),
                              vorlage["aufgaben"],
                              system=einstellung.get("system",
                                                     plan.SYSTEM_CHOOSE),
                              gericht=einstellung.get("gericht"))
    except Exception as e:                       # noqa: BLE001 — Probe
        return {"fehler": f"{type(e).__name__}: {e}", "gewaehlt": 0,
                "verworfen": 0, "dauer_s": time.monotonic() - t0}
    return {"fehler": None, "gewaehlt": len(auswahl.gewaehlt),
            "verworfen": len(auswahl.verworfen),
            "dauer_s": time.monotonic() - t0}


def messen(zugang, vorlagen: list[dict], wdh: int) -> list[dict]:
    zeilen = []
    for vorlage in vorlagen:
        n = len(vorlage["aufgaben"])
        print(f"\n„{vorlage['satz']}“ — {n} Begriffe mit Kandidaten, "
              f"Rezept „{vorlage['gericht'] or '—'}“ ({vorlage['warum']})")
        for name, einstellung in varianten(vorlage["satz"],
                                           vorlage["gericht"]).items():
            laeufe = [_lauf(zugang, vorlage, einstellung)
                      for _ in range(wdh)]
            treffer = [l["gewaehlt"] for l in laeufe]
            print(f"    {name:<12} " + "  ".join(f"{t:>2}/{n}"
                                                 for t in treffer)
                  + f"   Median {statistics.median(treffer):>4.1f}"
                  + ("   FEHLER: " + str(laeufe[0]["fehler"])
                     if laeufe[0]["fehler"] else ""))
            zeilen.append({"satz": vorlage["satz"], "variante": name,
                           "begriffe": n, "treffer": treffer,
                           "median": statistics.median(treffer),
                           "verworfen": [l["verworfen"] for l in laeufe],
                           "dauer_s": round(
                               statistics.mean(l["dauer_s"] for l in laeufe),
                               1)})
    return zeilen


def bericht(zeilen: list[dict]) -> None:
    """Eine Spalte je Variante, eine Zeile je Satz — und die Summe unten.

    Die Summe ist die einzige Zahl, die eine Entscheidung trägt: eine
    Variante, die „Salat" rettet und „Apfelkuchen" verliert, ist keine.
    """
    namen: list[str] = []
    for z in zeilen:
        if z["variante"] not in namen:
            namen.append(z["variante"])
    saetze: list[str] = []
    for z in zeilen:
        if z["satz"] not in saetze:
            saetze.append(z["satz"])
    print()
    print("=" * 100)
    print("MEDIAN GEWÄHLTER BEGRIFFE — Zeile: Satz, Spalte: Variante")
    print("=" * 100)
    print(f"  {'Satz':<28} {'Beg':>3} " + " ".join(f"{n:>12}" for n in namen))
    print("  " + "-" * 96)
    for satz in saetze:
        teil = {z["variante"]: z for z in zeilen if z["satz"] == satz}
        n = next(iter(teil.values()))["begriffe"]
        print(f"  {satz[:28]:<28} {n:>3} "
              + " ".join(f"{teil[v]['median']:>12.1f}" if v in teil
                         else f"{'—':>12}" for v in namen))
    print("  " + "-" * 96)
    print(f"  {'SUMME der Mediane':<28} "
          f"{sum(z['begriffe'] for z in zeilen if z['variante'] == namen[0]):>3} "
          + " ".join(
              f"{sum(z['median'] for z in zeilen if z['variante'] == v):>12.1f}"
              for v in namen))
    print()
    print("  Die Spalte `satz` ist der heutige Stand. Eine Variante zählt "
          "nur,")
    print("  wenn ihre SUMME höher liegt und keine Zeile darunter "
          "abstürzt.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", help="KOPIE der Datenbank — nicht data/picknick.db")
    p.add_argument("--einfrieren", action="store_true")
    p.add_argument("--messen", action="store_true")
    p.add_argument("--nach", default=None, help="Vorlagen hierhin schreiben")
    p.add_argument("--aus", default=None, help="Vorlagen von hier lesen")
    p.add_argument("--wdh", type=int, default=3)
    p.add_argument("--json", default=None, help="Messzeilen hierhin")
    args = p.parse_args()

    zustand = wake.zustand()
    print(f"Box: {zustand.zustand} {zustand.modell or ''}")
    if not zustand.bedient:
        print("Die Box bedient nicht — erst `wake-vllm`.")
        return 1
    zugang = Modellzugang()

    if args.einfrieren:
        if not args.db or not args.nach:
            print("--einfrieren braucht --db (die KOPIE) und --nach.")
            return 2
        if Path(args.db).resolve() == Path(db.DEFAULT_DB).resolve():
            print("Nicht auf data/picknick.db proben — erst kopieren.")
            return 2
        print("\nEINFRIEREN")
        vorlagen = einfrieren(args.db, GERICHTE, zugang)
        Path(args.nach).write_text(
            json.dumps(vorlagen, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"\n  {len(vorlagen)} Vorlagen -> {args.nach}")

    if args.messen:
        if not args.aus:
            print("--messen braucht --aus (die eingefrorenen Vorlagen).")
            return 2
        vorlagen = json.loads(Path(args.aus).read_text(encoding="utf-8"))
        print(f"\nMESSEN — {len(vorlagen)} Vorlagen, {args.wdh}× je Variante")
        zeilen = messen(zugang, vorlagen, args.wdh)
        if args.json:
            Path(args.json).write_text(
                json.dumps(zeilen, ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"\n  Rohdaten: {args.json}")
        bericht(zeilen)

    if not (args.einfrieren or args.messen):
        print("Nichts zu tun — `--einfrieren` oder `--messen` angeben.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
