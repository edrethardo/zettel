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

NVIDIA's Nemotron 3.5 Lightning, 4-bit on one RTX 3090, went through 128
German dishes in our grocery agent: 85 % of ingredients found, 6 seconds a
dish — and it may only pick from what the shop retrieved. Invented product
IDs: 1 in 1,167. Rejected, counted, shown.

Our household's shopping list: she types "everything for lasagna, and
toilet paper" (in German), the app pulls a real top-rated recipe, computes
pack counts, and nothing enters the cart without a per-item Yes. What the
catalog cannot find stays visible as free text — never silently dropped.

What you can copy:
– Retrieve first (SQLite FTS5); show the model only candidate IDs.
– An ID that was not presented is rejected, not repaired — and counted on
  the trace.
– The user's Yes/No on every row is the eval label. Nobody annotates.
Works for any agent that picks rows from a database — tickets, documents,
accounts. Three code locations: PATTERN.md in the repo.

Three open models, the same 128 dishes, one RTX 3090, every turn traced in
Arize Phoenix. Nemotron 3.5 Lightning (W4A16, quantized by useful-quants):
85 %, 6 s per dish. The Qwen3.8-27B reference: 87 %, 20 s. An older
Llama-Nemotron-Nano-8B: median 0 % — that number is in the docs next to the
wins. One run each, no repetitions; the failure cases are written up. Which
model runs the household is now a measured choice, not a brand preference.

vLLM · FastAPI + HTMX · SQLite FTS5 · OpenTelemetry → Arize Phoenix ·
1,364 tests and a 79-check gate that blocks the network at socket level ·
no cloud, no API keys.

Repo: https://github.com/edrethardo/zettel
Dataset (128 dishes, 7 runs): <HF dataset link>
The pattern: https://github.com/edrethardo/zettel/blob/master/PATTERN.md

@Merve Noyan #NVIDIAGTC

---

**Erster Kommentar, direkt nach dem Post** (nicht in den Post — er verwässert
dort): der Hochkant-Trailer (41 s), das Vergleichsbild
`docs/images/modelle-128.png`, und der Satz „Same discipline in my model-eval
harness, where the LLM judge is checked against real test runs:
github.com/edrethardo/llm-eval-phoenix". Dann eine Stunde antworten.

**Kürzere Variante** — nicht als zweiter LinkedIn-Post (zwei Posts teilen
die Reichweite), sondern für X oder Instagram:

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
