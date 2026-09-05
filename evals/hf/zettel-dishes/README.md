---
license: mit
language:
- de
pretty_name: "Zettel — 128 German dishes through a grocery agent, three open models, one RTX 3090"
task_categories:
- other
tags:
- evaluation
- retrieval
- agents
- open-models
- nemotron
- qwen
- german
size_categories:
- n<1K
---

# Zettel — 128 German dishes, three open models, one RTX 3090

The breadth evaluation of [Zettel](https://github.com/edrethardo/zettel), a
grocery agent for a household: a sentence like *"alles für Lasagne und
Klopapier"* goes through recipe lookup → ingredient search terms → catalog
retrieval (SQLite FTS5, 10,361 products) → a model that may **only choose
from the retrieved candidates** → cart → shopping list. Every run is traced
in Arize Phoenix; the numbers here are recomputed from those traces and
from the harness output (`scripts/breite_probe.py` in the repo).

## Files

* `dishes.csv` — the task: 128 dishes, each with its axis (everyday German
  cooking, international, exotic, vegetarian, baking, countable units,
  one-word names, ambiguous words, misspellings, invented names, already
  saved), the exact sentence sent, and the extra non-food article appended
  to 16 of them ("… und Klopapier").
* `results.csv` — one row per run and dish: model, path taken (`chefkoch`
  recipe / `recipe` saved / `llm` guessed), number of search terms, terms
  that found a catalog product, free-text leftovers, hit rate, invented
  product ids rejected, seconds, source recipe, Phoenix project.

## Runs

| run | model | dishes | terms found | per-dish mean / median | median s |
|---|---|---|---|---|---|
| 2026-08-30-qwen-64 | Qwen3.8-27B-Instruct (AWQ 4-bit, vLLM 0.24) | 64 | 82 % | 84 % / 88 % | 37 |
| 2026-08-30-nemotron-nano-8b | Llama-3.1-Nemotron-Nano-8B (BF16) | 64 | 11 % | 25 % / 0 % | 21 |
| 2026-08-31-mtp | Qwen3.8-27B-Instruct | 64 | 83 % | 85 % / 89 % | 12 |
| 2026-09-01-syv | Qwen3.8-27B (vLLM 0.27.1, DFlash2) | 64 | 83 % | 84 % / 88 % | 7 |
| 2026-09-05-nemotron35 | NVIDIA Nemotron 3.5 Lightning 30B-A3B (W4A16) | 64 | 82 % | 85 % / 88 % | 6 |
| 2026-09-05-qwen-128 | Qwen3.8-27B-Instruct (vLLM 0.27.1, DFlash2) | 128 | 87 % | 89 % / 91 % | 20 |
| 2026-09-05-nemotron35-128 | NVIDIA Nemotron 3.5 Lightning 30B-A3B (W4A16, no speculation) | 128 | 85 % | 87 % / 89 % | 6 |

All on one NVIDIA GeForce RTX 3090 (24 GB), self-hosted vLLM, thinking
disabled per request, structured output via `response_format: json_schema`
(the runs up to 2026-09-05 08:00 still sent vLLM's older `guided_json`,
which 0.27.1 ignores — the numbers did not move, see `EVALS.md` in the repo).

## What it measures, and what not

* "Terms found" is retrieval against **one** German retailer's catalog
  (Knuspr's open JSON API). A miss means the catalog does not carry the
  ingredient under that name — not that the model was wrong.
* One run per model and list, no repetitions; the two 128-dish runs differ
  by two points, which is the order of magnitude one invented dish shifts.
* Rejected product ids (`rejected`) stay near zero for every model: with the
  candidates in front of them, the models rarely invent. The losses are in
  what retrieval did not find.
* Latency compares speculative (Qwen with DFlash2) against non-speculative
  (Nemotron) serving; the plain generation rate of both is ≈220 tok/s on this
  GPU.

## How to reproduce

```bash
git clone https://github.com/edrethardo/zettel && cd zettel
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sqlite3 data/picknick.db "VACUUM INTO 'kopie.db'"          # your own catalog copy
ZETTEL_PHOENIX_PROJECT="Zettel Eval <model>" \
.venv/bin/python scripts/breite_probe.py --messen --db kopie.db --json roh.json --trace
.venv/bin/python scripts/hf_datensatz.py out/ roh.json     # -> dishes.csv, results.csv
```

Full write-up with the per-axis tables and the failure cases:
`EVALS.md` and `PATTERN.md` in the repository.
