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
- [ ] `<video link>` — das 60-Sekunden-Video (Drehbuch: `VIDEO.md`)
- [ ] das Repo ist öffentlich unter `https://github.com/edrethardo/zettel` (SHOWCASE.md ist die
      englische Einstiegsseite und im README verlinkt)
- [ ] Hashtag `#NVIDIAGTC` steht drin
- [ ] Video zuerst hochladen, Link ins Posting — LinkedIn rankt natives
      Video besser; dann ist `<video link>` überflüssig und kann raus
- [ ] Wochenplan-Film als **zweiter** Kommentar (Wortlaut unten) — der native
      Upload gehört dem Chat-Zug, LinkedIn zeigt nur einen nativen Film je Post
- [x] Test- und Check-Zahl frisch messen (`pytest -q`, `checks/smoke.py`) —
      1.499 Tests (14 übersprungen) und 79 Checks, gemessen 2026-09-09; sie
      wachsen weiter, also vor dem Absenden noch einmal

---

An open NVIDIA Nemotron 3.5 Lightning (30B total, 3B active, 4-bit) runs our household's grocery agent on one RTX 3090 under the TV: 128 German dishes, 85 % of ingredients found, 6 s a dish — and 1 invented product ID in 1,167. Rejected, counted, on the trace.

Not a prompt wrapper, not an orchestra either: two model calls with a database search in between, and one rule that makes them checkable.

1. Retrieve first. The model turns "everything for lasagna, and toilet paper" into search terms and sees zero catalog rows. SQLite FTS5 does the search and presents at most 5 candidates per term.

2. Reject, don't repair. An ID that was never presented is thrown out — no fuzzy rescue — and the term stays visible as free text. The JSON schema enforces shape, not truth, so the check lives in code. It was silently unenforced for 4 days after a vLLM upgrade; every number stayed the same.

3. Count it, and let real decisions be the labels. zettel.rejected sits on every turn's span in Arize Phoenix. Nothing enters the cart without a per-item Yes, and every Yes/No goes back to that span as an annotation when the order is submitted. Nobody annotates.

The same three rules run one level up. A weekly plan: four numbers and one sentence about what is already in the fridge, and the model assigns dishes to days — only from the dishes this household already has. It returns one sentence per day and not a single number: servings, weekly sums, what the declared stock covers, packs, price and leftovers are computed in code. Stock is deliberately not a tracked inventory — the shop knows purchases, not consumption — so yesterday's receipt may suggest ("10 eggs — still there?") and nothing counts before a Yes. Measured 2026-09-06 against the real database, 5 scenarios — on the Qwen3.8-27B reference, not yet on Nemotron: rejected 0 in all six turns, 3–14 s per week. Every Yes/No on a day goes back to the `plan.woche` span as a label.

Layer 3 is what made swapping models cost an afternoon and no production code. Same dishes, same 3090, one run each, no repetitions:
– Nemotron 3.5 Lightning, W4A16 by useful-quants, 16.6 GiB: 85 %, 6 s per dish
– Qwen3.8-27B reference, AWQ 4-bit: 87 %, 20 s
– Llama-Nemotron-Nano-8B (first 64 dishes): median 0 % per dish. That number stays in the docs next to the wins.
The 3× speed is not tok/s — those are nearly identical. The 3B-active model generates about a third of the tokens and finds two points less. Small-model-for-agents: measured, not asserted.

Local is not the test bed here, it is the deployment. The model never leaves the house; the only outbound call is a public recipe lookup. 1,499 tests and a 79-check gate that blocks the network at socket level.

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

**Zweiter Kommentar — der Wochenplan-Film.** Der Absatz über die Woche steht
im Post; der Film dazu gehört in einen eigenen Kommentar, damit der native
Upload dem Chat-Zug gehört (LinkedIn zeigt nur einen nativen Film je Post).

**`take_plan_2026-09-10a_final.mp4` — 86,9 s, quer**, Titelkarte, P1–P7,
Endcard. Das ist der Take vom 10.09.: erster Versuch ohne Warnung, 5 Tage in
5,7 s belegt aus 9 vorgelegten Gerichten, verworfen 0. Im Bild laufen die
Zahlen des Modells mit (144,8 t/s, erster Token nach 0,88 s, keine
Verdrängung), und der Untertitel nennt die Werte aus dem Phoenix-Span dieses
Zuges, nicht aus einem Messprotokoll.

Der ältere Schnitt `take_plan_2026-09-06d_final.mp4` (87,2 s) bleibt liegen,
geht aber nicht mit — er zeigt einen anderen Durchlauf und den alten Streifen.
**Ein Hochkant-Clip des Wochenplans wird nicht eingereicht** (Entscheidung
10.09.): die vorhandene Fassung stammt aus dem Take vom 06.09. und passt
weder zu den Gerichten noch zum Streifen des neuen Films; ein Nachdreh hätte
die GPU gekostet, die ein Nachbarprojekt für zwölf Stunden braucht. Ein Satz
zum Film, Vorschlag:

> One level up, same three rules: a week of dinners in, one shopping list out,
> minus what the receipt says is still there. The model assigns dishes to
> days and writes one sentence per day; every number on the plan is computed.

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
