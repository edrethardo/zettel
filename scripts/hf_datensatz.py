#!/usr/bin/env python3
"""Die Breitenmessung als Datensatz für den Hugging Face Hub (2026-09-05).

Aus den Roh-JSONs der Läufe (`evals/breite_probe-*.json`, je eine Liste
von Gerichten) entstehen zwei CSVs:

* `dishes.csv`  — jedes Gericht einmal: Name, Achse, gemessener Satz,
  Zusatzartikel. Das ist die Aufgabe.
* `results.csv` — eine Zeile je Lauf und Gericht: Modell, Weg, Begriffe,
  Katalogtreffer, Freitext, Quote, verworfene IDs, Dauer, Fehler, das
  Phoenix-Projekt mit den Traces. Das ist die Antwort.

Modell und Projekt kommen aus der `.provenienz.json` neben dem Roh-JSON —
nicht aus dem Dateinamen. Fehlt sie, steht der Dateiname im Feld `run` und
das Modell bleibt leer; geraten wird nichts.

    .venv/bin/python scripts/hf_datensatz.py evals/hf/zettel-dishes evals/breite_probe-2026-09-05-*.json
"""
import csv
import json
import pathlib
import sys

GERICHT_SPALTEN = ["dish", "axis", "sentence", "extra_article"]
ERGEBNIS_SPALTEN = ["run", "model", "dish", "path", "terms", "catalog_hits",
                    "free_text", "hit_rate", "rejected", "seconds", "error",
                    "source_recipe", "phoenix_project", "measured_at"]


def _provenienz(roh: pathlib.Path) -> dict:
    p = roh.with_name(roh.name.replace(".json", ".provenienz.json"))
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _lauf_name(roh: pathlib.Path) -> str:
    return roh.stem.replace("breite_probe-", "")


def _quote(z: dict) -> str:
    begriffe = int(z.get("begriffe") or 0)
    if not begriffe:
        return ""
    return f"{int(z.get('katalogtreffer') or 0) / begriffe:.3f}"


def lesen(pfade) -> tuple[list[dict], list[dict]]:
    """(Gerichte, Ergebnisse) aus den Roh-JSONs."""
    gerichte: dict[str, dict] = {}
    ergebnisse: list[dict] = []
    for pfad in pfade:
        roh = pathlib.Path(pfad)
        prov = _provenienz(roh)
        for z in json.loads(roh.read_text(encoding="utf-8")):
            name = z["gericht"]
            gerichte.setdefault(name, {
                "dish": name, "axis": z.get("achse", ""),
                "sentence": z.get("satz", ""), "extra_article": z.get("rest") or ""})
            ergebnisse.append({
                "run": _lauf_name(roh), "model": prov.get("modell", ""),
                "dish": name, "path": z.get("weg", ""),
                "terms": int(z.get("begriffe") or 0),
                "catalog_hits": int(z.get("katalogtreffer") or 0),
                "free_text": int(z.get("freitext") or 0),
                "hit_rate": _quote(z), "rejected": int(z.get("verworfen") or 0),
                "seconds": z.get("dauer_s", ""), "error": z.get("fehler") or "",
                "source_recipe": z.get("quelle_name") or "",
                "phoenix_project": prov.get("phoenix_projekt", ""),
                "measured_at": prov.get("created_lesbar", "")})
    return list(gerichte.values()), ergebnisse


def schreiben(pfade, ziel) -> tuple[int, int]:
    ziel = pathlib.Path(ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    gerichte, ergebnisse = lesen(pfade)
    for datei, spalten, zeilen in (("dishes.csv", GERICHT_SPALTEN, gerichte),
                                   ("results.csv", ERGEBNIS_SPALTEN, ergebnisse)):
        with (ziel / datei).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=spalten)
            w.writeheader()
            w.writerows(zeilen)
    return len(gerichte), len(ergebnisse)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    n_g, n_e = schreiben(sys.argv[2:], sys.argv[1])
    print(f"{n_g} Gerichte, {n_e} Ergebniszeilen -> {sys.argv[1]}")
