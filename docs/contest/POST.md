# LinkedIn post — draft

Entwurf zum Einreichen für den NVIDIA GTC Golden Ticket Contest. Platzhalter
in spitzen Klammern selbst ersetzen; **nichts hieran ist gepostet** — das
machst du. Der Text behauptet nur, was das Repo belegt.

Checkliste vor dem Absenden:

- [ ] Judge ist **Chorouk Malmoum** (Founder, AgentX Academy) — von ihr hast
      du vom Contest erfahren, und die Regeln verlangen genau diesen Tag. Beim
      Posten wirklich taggen (LinkedIn: „@Chorouk Malmoum“ tippen und den
      Vorschlag anklicken), nicht nur schreiben. Ihr Feed: Agenten, die lokal
      laufen (DGX Spark), und NVIDIAs These „small models for agentic AI“ —
      darum steht der 3B-active-Satz im Modellabsatz
- [ ] `<video link>` — der Film (146 s mit Intro, Drehbuch: `VIDEO.md`, Abschnitt v6)
- [ ] das Repo ist öffentlich unter `https://github.com/edrethardo/zettel` (SHOWCASE.md ist die
      englische Einstiegsseite und im README verlinkt)
- [ ] Hashtag `#NVIDIAGTC` steht drin
- [ ] Video zuerst hochladen, Link ins Posting — LinkedIn rankt natives
      Video besser; dann ist `<video link>` überflüssig und kann raus
- [ ] **Der Film ist `zettel_demo_2026-09-10_intro_final.mp4`** (146 s: Intro, dann
      der Satz, der die Woche plant, dann der eine Einkauf, alles auf Nemotron) als nativer Upload; Vorschaubild
      `thumbnail_2026-09-10.png`. Der erste Kommentar bleibt, ein zweiter
      Film-Kommentar entfällt
- [x] Test- und Check-Zahl frisch messen (`pytest -q`, `checks/smoke.py`) —
      1.517 Tests (14 übersprungen) und 79 Checks, gemessen 2026-09-10; sie
      wachsen weiter, also vor dem Absenden noch einmal

---

An open NVIDIA Nemotron 3.5 Lightning (30B total, 3B active, 4-bit) runs our household's grocery agent on one RTX 3090 under the TV: 128 German dishes, 85 % of ingredients found, 6 s a dish — and 1 invented product ID in 1,167. Rejected, counted, on the trace.

Not a prompt wrapper, not an orchestra either: two model calls with a database search in between, and one rule that makes them checkable.

1. Retrieve first. The model turns "everything for lasagna, and toilet paper" into search terms and sees zero catalog rows. SQLite FTS5 does the search and presents at most 5 candidates per term.

2. Reject, don't repair. An ID that was never presented is thrown out — no fuzzy rescue — and the term stays visible as free text. The JSON schema enforces shape, not truth, so the check lives in code. It was silently unenforced for 4 days after a vLLM upgrade; every number stayed the same.

3. Count it, and let real decisions be the labels. zettel.rejected sits on every turn's span in Arize Phoenix. Nothing enters the cart without a per-item Yes, and every Yes/No goes back to that span as an annotation when the order is submitted. Nobody annotates.

The same three rules run one level up. A weekly plan: four numbers and one sentence about what is already in the fridge, and the model assigns dishes to days — only from the dishes this household already has. It returns one sentence per day and not a single number: servings, weekly sums, what the declared stock covers, packs, price and leftovers are computed in code. Stock is deliberately not a tracked inventory — the shop knows purchases, not consumption — so yesterday's receipt may suggest ("10 eggs — still there?") and nothing counts before a Yes. Measured against the real database, 5 scenarios, both models: Qwen3.8-27B rejected 0 in all six turns; Nemotron 3.5 rejected 0 in the four normal ones; in the two thin ones — one dish offered for seven days, a re-plan where the remaining dishes were already fixed on other days — the code rejected 6 and 3 proposals: the same dish on every day, fixed days filled again. Counted, none reached the page — and the trace showed the offer was the bug, not the model: dishes already in the plan were offered again. Since then the model is offered only what it can choose, and the schema enumerates the allowed ids and open days, so guided decoding cannot produce anything else — the same five scenarios after the fix: 0 rejected, 20 s. 3–4 s per week on Nemotron. Every Yes/No on a day goes back to the `plan.woche` span as a label.

Layer 3 is what made swapping models cost an afternoon and no production code. Same dishes, same 3090, one run each, no repetitions:
– Nemotron 3.5 Lightning, W4A16 by useful-quants, 16.6 GiB: 85 %, 6 s per dish
– Qwen3.8-27B reference, AWQ 4-bit: 87 %, 20 s
– Llama-Nemotron-Nano-8B (first 64 dishes): median 0 % per dish. That number stays in the docs next to the wins.
The 3× speed is not tok/s — those are nearly identical. The 3B-active model generates about a third of the tokens and finds two points less. Small-model-for-agents: measured, not asserted.

Local is not the test bed here, it is the deployment. The model never leaves the house; the only outbound call is a public recipe lookup. 1,517 tests and a 79-check gate that blocks the network at socket level.

Works for any agent that picks rows from a database you own — tickets, documents, accounts. Three code locations:
Pattern: https://github.com/edrethardo/zettel/blob/master/PATTERN.md
Repo: https://github.com/edrethardo/zettel
Dataset (128 dishes, 7 runs): <HF dataset link>

When a local agent is the production system — one household, one GPU — which layer would you add first before you trust it? For us it was the labels.

@Chorouk Malmoum #NVIDIAGTC

---

**Erster Kommentar, direkt nach dem Post** (nicht in den Post — er verwässert
dort), Wortlaut:

> The one trace that changed how I read failures: the model picked a 4.69 €
> artisanal salted butter for "butter". Blame the model? `zettel.rejected = 0`
> and the RETRIEVER span show five ButterBoyz variants and no plain butter —
> the fault was retrieval, and Phoenix showed it without opening a JSON blob.
> Same discipline in my eval harness, where the LLM judge is checked against
> real test runs: github.com/edrethardo/llm-eval-phoenix

Dazu der Hochkant-Trailer (44 s) und das Vergleichsbild
`docs/images/modelle-128.png`. Dann eine Stunde antworten.

**Kein zweiter Film-Kommentar mehr (10.09.).** Der native Upload ist der
kombinierte Film `zettel_demo_2026-09-10_intro_final.mp4` — 146 s, Intro, erst die Woche,
dann die Überleitung „Not the whole week? Then just tonight", dann der eine
Einkauf; alles im selben Take auf Nemotron 3.5 Lightning, mit Cache, tok/s
und Zeit bis zum ersten Token im Bild. Die früheren Einzelfilme
(`zettel_demo_nemotron_final.mp4`, `take_plan_2026-09-10a_final.mp4`) bleiben
liegen und gehen nicht mit.

**Zugeschnitten auf die Jurorin (05.09.):** ihr Vokabular (prompt wrapper,
layer, observability, production), nicht ihre Sätze; das nummerierte
Framework, weil ihre Posts so gebaut sind; die SLM-These als Messung mit dem
Verlust (zwei Punkte hinter der 27B-Referenz), nie als Beweis; kein
„Level 5", kein „nothing touches an external API" (der Rezeptabruf geht
raus). Nano-8B lief nur über die ersten 64 Gerichte — der alte Entwurf sagte
„three models, the same 128 dishes", das war falsch.

**Kürzere Variante** — nicht als zweiter LinkedIn-Post (zwei Posts teilen
die Reichweite), sondern für X oder Instagram:

---

Open NVIDIA Nemotron 3.5 Lightning (3B active, 4-bit) on one RTX 3090 runs the grocery agent our household shops from. 128 dishes: 85 % of ingredients found, 6 s a dish, 1 invented ID in 1,167 — rejected, counted, traced in Arize Phoenix. Every Yes/No is an eval label.

Demo: <video link> · Repo: https://github.com/edrethardo/zettel

@Chorouk Malmoum #NVIDIAGTC
