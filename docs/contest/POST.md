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

I built a grocery app for our multi-person household — and made the LLM inside
it fully accountable.

Type "everything for lasagna, and toilet paper" (in German — it's our
household app) and it pulls a real top-rated recipe, computes pack counts
from the ingredient quantities, and puts nothing in the cart without a
per-item Yes. What the catalog cannot find stays on the list as visible
free text — nothing is ever silently dropped.

The part I care about most: the model is only allowed to choose from
candidates the shop retrieved. Invented product IDs are rejected, counted,
and shown — in the UI and in the trace.

The stack:
- Qwen3.8-27B-Instruct (open weights, AWQ 4-bit) on vLLM — ~17.4 GiB VRAM
  on a single NVIDIA RTX 3090. No cloud, no API keys.
- FastAPI + HTMX + SQLite FTS5 — one process, no build step.
- Arize Phoenix — every chat turn is one trace, and every Yes/No the user
  taps becomes an eval label.

Measured, not vibed: 64 dishes end-to-end (83 % of search terms found a
catalog product, median 89 % per dish), 1,346 tests, a 79-check smoke gate
that blocks the network at socket level. The same harness judged a second
open model in an afternoon — NVIDIA's Nemotron-Nano-8B scored a median of
0 %, and that number is in the docs next to the wins. The write-up includes
the failure cases — "Salat" scores zero, and the docs explain exactly why.

60-second demo: <video link>
Repo & engineering tour: https://github.com/edrethardo/zettel

@Merve Noyan #NVIDIAGTC

---

**Kürzere Variante** (falls der Feed-Algorithmus kurze Posts bevorzugt oder
du zwei Anläufe willst):

---

An open 27B model, one RTX 3090, zero cloud — and a grocery list our
household actually shops from.

"Everything for lasagna, and toilet paper" → real recipe, computed pack
counts, per-item confirmation. The model may only pick from retrieved
candidates; invented IDs are rejected and counted. Every turn is a Phoenix
trace, every user decision an eval label. Evaluated on 64 dishes, failure
cases documented.

Qwen3.8-27B (AWQ 4-bit) · vLLM · FastAPI + HTMX + SQLite · Arize Phoenix

Demo: <video link> · Repo: https://github.com/edrethardo/zettel

@Merve Noyan #NVIDIAGTC
