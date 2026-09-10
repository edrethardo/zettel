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
- [ ] `<video link>` — der Film (127 s mit Intro und Stimme, Drehbuch: `VIDEO.md`, Abschnitte v8 und „die Stimme liegt drunter“)
- [x] das Repo ist öffentlich unter `https://github.com/edrethardo/zettel` (seit 10.09., 20:50; Actions grün) (SHOWCASE.md ist die
      englische Einstiegsseite und im README verlinkt)
- [ ] Hashtag `#NVIDIAGTC` steht drin
- [ ] Video zuerst hochladen, Link ins Posting — LinkedIn rankt natives
      Video besser; dann ist `<video link>` überflüssig und kann raus
- [ ] **Der Film ist `zettel_demo_2026-09-10_intro_vo.mp4`** (127 s, mit Aarons Voice-Over: Intro, dann
      der Satz, der die Woche plant, dann der eine Einkauf, alles auf Nemotron) als nativer Upload; Vorschaubild
      `thumbnail_2026-09-10.png`. Der erste Kommentar bleibt, ein zweiter
      Film-Kommentar entfällt
- [x] Test- und Check-Zahl frisch messen (`pytest -q`, `checks/smoke.py`) —
      1.517 Tests (14 übersprungen) und 79 Checks, gemessen 2026-09-10; sie
      wachsen weiter, also vor dem Absenden noch einmal

---

An open NVIDIA Nemotron 3.5 Lightning (30B total, 3B active, 4-bit) runs our household's grocery agent on one RTX 3090 under the TV: 128 German dishes, 85 % of ingredients found, 6 s a dish — and 1 invented product ID in 1,167. Rejected, counted, on the trace.

Not a prompt wrapper, not an orchestra either: two model calls with a database search in between, and one rule that makes them checkable.

1. Retrieve first. The model turns "everything for lasagna, and toilet paper" into search terms and sees zero catalog rows. SQLite FTS5 searches and presents at most 5 candidates per term.

2. Reject, don't repair. An ID that was never presented is thrown out — no fuzzy rescue. The JSON schema enforces shape, not truth, so the check lives in code. It was silently unenforced for 4 days after a vLLM upgrade; every number stayed the same.

3. Count it, and let real decisions be the labels. zettel.rejected sits on every turn's span in Arize Phoenix. Nothing enters the basket without a per-item Yes, and every Yes/No goes back to that span as an annotation. Nobody annotates.

The same three rules run one level up — that is the film. One sentence: "one meal a day, 700 kcal, high protein, potatoes, eggs and pasta are in." The model reads it into the fields (every number has to be in the sentence, or it is rejected), then assigns dishes to days — only from dishes this household already has, one sentence of reasoning per day and not a single number: servings, sums, stock, packs, price, kcal are computed in code. A No on Friday re-plans Friday only, in 1.3 s; the rejected dish does not come back. Measured on the real database, 5 scenarios, both models: 0 rejected. The one bug a trace found was in the offer, not in the model — fixed, re-measured, both runs in EVALS.md.

Swapping models cost an afternoon and no production code. Same dishes, same 3090:
– Nemotron 3.5 Lightning, W4A16, 16.6 GiB: 85 %, 6 s per dish
– Qwen3.8-27B reference, AWQ 4-bit: 87 %, 20 s
– Llama-Nemotron-Nano-8B (first 64 dishes): median 0 %. That number stays in the docs next to the wins.
The 3× speed is not tok/s — the 3B-active model generates a third of the tokens and finds two points less. Small-model-for-agents: measured, not asserted.

Local is not the test bed here, it is the deployment. The model never leaves the house. 1,517 tests and a 79-check gate that blocks the network at socket level.

Works for any agent that picks rows from a database you own — tickets, documents, accounts. Pattern and repo: https://github.com/edrethardo/zettel

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

Dazu das Vergleichsbild `docs/images/modelle-128.png` (kein Trailer mehr —
der Hochkant-Schnitt ist gestrichen, 10.09.). Dann eine Stunde antworten.

**Kein zweiter Film-Kommentar mehr (10.09.).** Der native Upload ist der
kombinierte Film `zettel_demo_2026-09-10_intro_vo.mp4` — 127 s, Intro, erst die Woche,
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
die Reichweite), sondern für Instagram (Aaron hat kein X):

---

Open NVIDIA Nemotron 3.5 Lightning (3B active, 4-bit) on one RTX 3090 runs the grocery agent our household shops from. 128 dishes: 85 % of ingredients found, 6 s a dish, 1 invented ID in 1,167 — rejected, counted, traced in Arize Phoenix. Every Yes/No is an eval label.

Demo: <video link> · Repo: https://github.com/edrethardo/zettel

@Chorouk Malmoum #NVIDIAGTC
