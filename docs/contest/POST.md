# LinkedIn post — draft

Entwurf zum Einreichen für den NVIDIA GTC Golden Ticket Contest. Platzhalter
in spitzen Klammern selbst ersetzen; **nichts hieran ist gepostet** — das
machst du. Der Text behauptet nur, was das Repo belegt.

Checkliste vor dem Absenden:

- [ ] Judge ist **Merve Noyan** (Hugging Face) — von ihr hast du vom Contest
      erfahren, und die Regeln verlangen genau diesen Tag. Beim Posten wirklich
      taggen (LinkedIn: „@Merve Noyan“ tippen und den Vorschlag anklicken),
      nicht nur schreiben
- [ ] `<video link>` — das 60-Sekunden-Video (Drehbuch: `VIDEO.md`)
- [ ] das Repo ist öffentlich unter `https://github.com/edrethardo/zettel` (SHOWCASE.md ist die
      englische Einstiegsseite und im README verlinkt)
- [ ] Hashtag `#NVIDIAGTC` steht drin
- [ ] Video zuerst hochladen, Link ins Posting — LinkedIn rankt natives
      Video besser; dann ist `<video link>` überflüssig und kann raus
- [ ] Test- und Check-Zahl frisch messen (`pytest -q`, `checks/smoke.py`) —
      die 1,346/79 sind vom 2026-09-04 und wachsen weiter

---

NVIDIA's Nemotron 3.5 Lightning, 4-bit on one RTX 3090, now runs our
household's grocery agent: 128 dishes, 85 % of the ingredients found in the
catalog, six seconds a dish — and it may only pick from what the shop
retrieved. I built the app for us and made the LLM inside it fully
accountable; here is what you can take from it.

Type "everything for lasagna, and toilet paper" (in German — it's our
household app) and it pulls a real top-rated recipe, computes pack counts
from the ingredient quantities, and puts nothing in the cart without a
per-item Yes. What the catalog cannot find stays on the list as visible
free text — nothing is ever silently dropped.

The part I care about most: the model is only allowed to choose from
candidates the shop retrieved. Invented product IDs are rejected, counted,
and shown — in the UI and in the trace.

The stack:
- NVIDIA Nemotron 3.5 Lightning 30B-A3B (open weights, 4-bit) on vLLM —
  16.6 GiB on a single NVIDIA RTX 3090, 6 s per dish; measured against
  Qwen3.8-27B as the reference on the same 128 dishes. No cloud, no API keys.
- FastAPI + HTMX + SQLite FTS5 — one process, no build step.
- Arize Phoenix — every chat turn is one trace, and every Yes/No the user
  taps becomes an eval label.

Measured, not vibed: 128 dishes end-to-end, three open models, one RTX
3090. Nemotron 3.5 Lightning (4-bit) finds 85 % of the ingredients in the
catalog — median 89 % per dish — and runs all 128 dishes in 2.2 minutes, six
seconds a dish; the 27B reference finds 87 % and takes three times as long.
An older Nemotron-Nano-8B scored a median of 0 % on the same harness, and
that number is in the docs next to the wins. 1,364 tests, a 79-check smoke
gate that blocks the network at socket level, and the failure cases written
up — "Salat" once scored zero, and the docs explain exactly why.

What you can take from it: retrieve first, present ids, reject anything
not presented and count it, and let the user's Yes/No be the eval label.
That works for any agent that picks rows from a database — products,
tickets, documents. The pattern with its three code locations: PATTERN.md
in the repo. (Same discipline in my model-eval harness, where the LLM judge
is checked against real test runs: github.com/edrethardo/llm-eval-phoenix.)

Demo: <video link>
Repo & engineering tour: https://github.com/edrethardo/zettel

@Merve Noyan #NVIDIAGTC

---

**Kürzere Variante** (falls der Feed-Algorithmus kurze Posts bevorzugt oder
du zwei Anläufe willst):

---

An open NVIDIA Nemotron model, one RTX 3090, zero cloud — and a grocery
list our household actually shops from.

"Everything for lasagna, and toilet paper" → real recipe, computed pack
counts, per-item confirmation. The model may only pick from retrieved
candidates; invented IDs are rejected and counted. Every turn is a Phoenix
trace, every user decision an eval label. Evaluated on 128 dishes with three
open models, failure cases documented.

Nemotron 3.5 Lightning 30B-A3B (4-bit; Qwen3.8-27B as reference) · vLLM · FastAPI + HTMX + SQLite · Arize Phoenix

Demo: <video link> · Repo: https://github.com/edrethardo/zettel

@Merve Noyan #NVIDIAGTC
