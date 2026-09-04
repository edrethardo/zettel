# Getting started

How to run Zettel on your own machine, what it needs, and where the first
chat turn shows up. The product tour is [`SHOWCASE.md`](SHOWCASE.md); the
household manual (German) is [`ANLEITUNG.md`](ANLEITUNG.md).

## What you need

| | Required? | Notes |
|---|---|---|
| Python 3.10+, Linux | yes | developed and gated on 3.10.12 |
| an OpenAI-compatible LLM endpoint | for the chat only | the reference is vLLM 0.27.1 serving `Qwen3.8-27B-Instruct` (AWQ 4-bit) on one RTX 3090; the shop asks `/v1/models` for the model name, nothing is hard-coded |
| Arize Phoenix on `localhost:6006` | for traces and evals only | the shop runs unchanged without it |
| Tailscale | for phones in the household | the shop binds loopback plus one tailnet address, never `0.0.0.0` |

Catalog, cart, pick list and saved recipes work with none of the optional
pieces — by design (Spec 11).

## Install and run

```bash
git clone https://github.com/edrethardo/zettel && cd zettel
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# a first slice of catalog (one search term, a few hundred products)
.venv/bin/python -m zettel.scrapers.nachtlauf --begriff milch

.venv/bin/python -m zettel.web.app
```

Open `http://127.0.0.1:8730`. If the machine is on a tailnet the shop also
listens on its `100.64.0.0/10` address and prints it at start-up.

The full catalog (~10,000 products) comes from the nightly crawl
(`deploy/zettel-crawl.timer`, 03:30) — see “Betrieb” in the README for the
systemd user units. Prices are guide values from an open catalog API, not
the store's shelf prices; the README says why.

## Configure

Everything is an environment variable; nothing is read from a config file
except the gitignored `zettel.env` in the project directory, which may hold
the two LLM values.

| Variable | Default | What it does |
|---|---|---|
| `ZETTEL_HOST` | loopback + tailnet | bind address; `0.0.0.0` is refused with an error |
| `ZETTEL_PORT` | `8730` | port |
| `ZETTEL_DB` | `data/picknick.db` | the SQLite file (WAL mode, migrations run at start) |
| `ZETTEL_IMAGE_DIR` | `data/images` | product photos from the crawl |
| `ZETTEL_BON_DIR` | `data/bons` | uploaded receipts |
| `ZETTEL_LLM_ENDPOINT` | `http://localhost:8000/v1` | OpenAI wire format; also read from `zettel.env` |
| `ZETTEL_LLM_API_KEY` | any string | vLLM ignores it; also read from `zettel.env` |
| `ZETTEL_WAKE_CMD` | `wake-vllm` | run in the background when the endpoint does not answer — the reference box suspends after ~2 h idle and wakes on LAN; set `/bin/true` if yours does not sleep |
| `ZETTEL_TRACING` | on | `0` switches the OpenTelemetry exporter off |
| `ZETTEL_PHOENIX_ENDPOINT` | `http://localhost:6006/v1/traces` | OTLP/HTTP target |
| `ZETTEL_PHOENIX_PROJECT` | `Zettel Agent` | Phoenix project; eval runs use their own |

What the chat sends to the model, and why it is safe with other servers:
stage 1 and stage 3 ask for `guided_json` and pass
`chat_template_kwargs: {"enable_thinking": false}` through `extra_body`.
A server that does not know these fields ignores them; the schema check
against the presented candidates lives in code, not in the sampler
(see “The guarantees” in `SHOWCASE.md`).

## The first turn

1. Start Phoenix if you want to watch: `phoenix serve` (or your own
   instance on port 6006).
2. Open `/chat`, type *alles für Lasagne und Klopapier*, press **Fragen**.
3. Within seconds (model warm) you get a recipe card and one suggestion
   row per ingredient with computed pack counts. Nothing is in the cart
   until you tap **Ja**.
4. In Phoenix, project `Zettel Agent`: one trace per turn —
   `chat.turn` → `plan.extract`, `catalog.search` ×N (RETRIEVER spans with
   scored documents), `plan.choose`. The turn's attributes (`zettel.terms`,
   `zettel.products`, `zettel.rejected`, …) are the contract in
   [`OBSERVABILITY.md`](OBSERVABILITY.md).
5. Submit the cart: the user's Yes/No decisions are written back to that
   span as annotations — the eval labels fall out of ordinary use.

If the chat says *„Modell wacht auf … ~90 s"*, the shop is waking the model
box and retries by itself; everything else keeps working meanwhile.

## Verify

```bash
.venv/bin/python -m pytest -q             # 1,346 tests, ~45 s, no network
.venv/bin/python -m pytest -q -n auto     # same on all cores
.venv/bin/python checks/smoke.py          # 79 checks; the network is blocked at socket level
```

Both run without model, network or Phoenix; for the smoke gate that is
enforced, not assumed (`GATES.md`).

## Evaluate a model

The 64-dish breadth run drives the whole path — sentence → recipe → search
terms → products → cart → shopping list — against a **copy** of the
database and the live model, and traces into a Phoenix project of its own:

```bash
sqlite3 data/picknick.db "VACUUM INTO 'kopie.db'"
ZETTEL_PHOENIX_PROJECT="Zettel Eval <model>" \
.venv/bin/python scripts/breite_probe.py --messen --db kopie.db --json roh.json --trace
```

Swap the model on the server, run again, compare — that is how the Qwen
vs. Nemotron table in [`EVALS.md`](EVALS.md) was made. The twelve-query
depth eval with three evaluators (Phoenix datasets and experiments) is
described there too.

## Where things live

| Path | What |
|---|---|
| `zettel/web/` | FastAPI app, Jinja templates, vendored HTMX, `stil.css` |
| `zettel/assistant/` | the three-stage agent (`plan.py`), suggestions, recipe drafts |
| `zettel/catalog/` | FTS5 search, the everyday-word table, categories |
| `zettel/obs/` | OpenInference spans, labels written back on submit |
| `zettel/llm/` | the OpenAI client, model discovery, waking the box |
| `zettel/gerichte/`, `zettel/scrapers/` | recipe fetch (Chefkoch JSON), nightly catalog crawl |
| `zettel/bons/` | receipt upload, OCR, matching |
| `checks/`, `tests/` | the smoke gate and the suite |
| `scripts/` | probes and the eval harness (`breite_probe.py`) |
| `docs/superpowers/` | design specs, plans and the adversarial UI review — the paper trail |
