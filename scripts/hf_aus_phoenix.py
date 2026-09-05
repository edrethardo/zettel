#!/usr/bin/env python3
"""Ergebniszeilen für `results.csv` aus einem Phoenix-Projekt (2026-09-05).

Für Läufe, deren Roh-JSON nicht mehr da ist (die beiden vom 30.08.: Qwen-
Referenz und Nemotron-Nano-8B, WB-393) sind die Traces die einzige Quelle.
Je `chat.turn`-Span eine Zeile, dieselben Spalten wie `hf_datensatz.py` —
nur `seconds` kommt aus dem Span selbst, und `path`/`source_recipe` fehlen,
wo das Attribut nicht gesetzt war. Braucht ein laufendes Phoenix; kein Test,
keine Zusicherung — ein Werkzeug für genau diesen einen Export.

    .venv/bin/python scripts/hf_aus_phoenix.py evals/hf/zettel-dishes/results.csv \\
        "Zettel Eval Qwen=2026-08-30-qwen-64=Qwen3.8-27B-Instruct" \\
        "Zettel Eval Nemotron=2026-08-30-nemotron-nano-8b=Llama-3.1-Nemotron-Nano-8B"
"""
import ast
import csv
import sys

from phoenix.client import Client

SPALTEN = ["run", "model", "dish", "path", "terms", "catalog_hits", "free_text",
           "hit_rate", "rejected", "seconds", "error", "source_recipe",
           "phoenix_project", "measured_at"]


def zeilen(projekt: str, lauf: str, modell: str) -> list[dict]:
    df = Client(base_url="http://localhost:6006").spans.get_spans_dataframe(
        project_name=projekt, limit=30000)
    raus = []
    for _, r in df[df["name"] == "chat.turn"].iterrows():
        a = r.get("attributes.zettel") if "attributes.zettel" in df.columns else None
        if a is None or isinstance(a, float):
            a = r.get("attributes.picknick")
        if isinstance(a, str):
            try:
                a = ast.literal_eval(a)
            except (ValueError, SyntaxError):
                a = None
        if not isinstance(a, dict):
            continue
        t, p = int(a.get("terms") or 0), int(a.get("products") or 0)
        raus.append({
            "run": lauf, "model": modell,
            "dish": a.get("dish") or str(r.get("attributes.input.value", "")).replace("alles für ", ""),
            "path": a.get("path", ""), "terms": t, "catalog_hits": p,
            "free_text": int(a.get("free_text") or 0),
            "hit_rate": f"{p / t:.3f}" if t else "", "rejected": int(a.get("rejected") or 0),
            "seconds": f"{(r['end_time'] - r['start_time']).total_seconds():.1f}",
            "error": "", "source_recipe": a.get("dish_recipe") or "",
            "phoenix_project": projekt, "measured_at": str(r["start_time"])[:19]})
    return raus


if __name__ == "__main__":
    ziel, auftraege = sys.argv[1], sys.argv[2:]
    neu = []
    for auftrag in auftraege:
        projekt, lauf, modell = auftrag.split("=")
        neu += zeilen(projekt, lauf, modell)
    with open(ziel, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SPALTEN)
        w.writerows(neu)
    print(f"{len(neu)} Zeilen angehängt an {ziel}")
