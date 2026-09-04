# Case Study: Observability on a Self-Hosted Grocery Agent

Zettel — German for the slip of paper you take to the shop — is a grocery
agent for a two-person household: a 27B open-weights model on a single
RTX 3090 turns "everything for lasagna, and toilet paper" into a real
shopping list, fully traced in Arize Phoenix.
[`SHOWCASE.md`](SHOWCASE.md) is the product tour. This document tells three
stories from building it: a trace that overturned the obvious explanation, a
measurement that corrected its own hypothesis three times before yielding a
one-line fix, and an eval-label design where ground truth falls out of
ordinary use. Every number below is measured, dated, and can be found with
`grep` in [`EVALS.md`](EVALS.md), [`OBSERVABILITY.md`](OBSERVABILITY.md), or
the git history.

## 1. The butter case: what a trace sees that a test never will

While building the agent, this sentence went through: *"…dazu brauche ich
noch Zahnpasta und Butter"* ("…I also need toothpaste and butter"). The model
put an artisanal **ButterBoyz BIO Salzbutter for 4.69 €** on the list. Nobody
who says "butter" wants hand-made specialty butter, so the obvious
explanation is a bad model. The trace says otherwise. The turn's two
`catalog.search` spans contain (ranks from the real catalog, 2026-08-28,
2,498 products):

```
"Butter" — 5 candidates presented          zettel.rejected = 0
   4.01  #1771  ButterBoyz BIO Butter Chili & Röstzwiebel
   4.01  #1772  ButterBoyz BIO Butter Feige & Anis
   3.96  #1757  ButterBoyz BIO Kräuterbutter
   3.96  #1766  ButterBoyz BIO Salzbutter        ← chosen
   3.96  #1768  ButterBoyz BIO Steinpilzbutter
"Zahnpasta" — 0 candidates presented
```

`zettel.rejected = 0` means the model invented nothing: it chose from the
list it was shown, and **no plain butter was in that list**. Among the five
offers the choice was even defensible. The failure belongs to retrieval, not
the model. A test on the final answer sees a wrong product; only the trace
shows the candidate list the model actually had. This is why `catalog.search`
is a `RETRIEVER` span and not a `TOOL`: Phoenix renders the candidates as
scored documents, and the question "model or search?" answers itself without
opening a JSON blob. The reading rule is three lines:

| In the trace | Blame |
|---|---|
| the right product was not among the documents | retrieval |
| it was there, the model took another | model |
| `zettel.rejected > 0` | model, inventing |

The record carries its own date on purpose. `OBSERVABILITY.md` marks it
"recorded 2026-08-28, catalog of 2,498 products, before WB-339 and WB-340" —
because whoever re-runs the case today sees something else. After the full
crawl (10,361 products) and the word-boundary sort order of WB-339,
"Weihenstephan Butter" sits at rank 3 of the candidates; the same question
would likely get a different answer now. The case stays in the docs as an
argument for the `RETRIEVER` span, not as a claim about the current catalog —
a trace record without its catalog and code state written next to it would be
exactly the mistake the span exists to prevent. And it is not a memory: the
turn was re-driven on 2026-08-28 against the live box and Phoenix (trace
`099d1fdd…`, span `6734d91d3569c413`, output verbatim
`{"auswahl": [{"begriff": "Butter", "produkt_id": 1766, "menge": 1}]}`).

## 2. The salad arc: a measurement that corrected its own hypothesis three times

**Breadth first.** WB-380 (`scripts/breite_probe.py`) drove 64 dishes across
12 axes — baking, vegan, typos, ambiguous names, fantasy dishes, exotic
international — through the whole path: sentence → ingredients → search
terms → products → cart → shopping list. Run of 2026-08-29, no run failed,
median 83 % of terms per dish found a catalog product. But **four dishes
landed at zero**, and three of them lost *everything*: Salat 0 of 9,
Kartoffelpürree 0 of 11, Königsberger Klopse 0 of 16 terms with a product.
(The fourth zero, Bibimbap, turned out to be an outlier: re-run alone it
scored 7 of 8.) The hypothesis: stage 3 — the call that picks products from
the presented candidates — checks them against the *sentence*, not the
recipe. "Kartoffelpürree" fetches a recipe titled "Schweinefilet auf
Süßkartoffelpüree mit Lebkuchenjus und Rosenkohl"; when sentence and recipe
clash, the model rejects **every** candidate, milk and butter included. A
control line seemed to show that phrasing alone was half the story:

```
"Salat"              ->  0 of 9 terms with a product
"alles für Salat"    ->  6 of 9      (same recipe, same catalog)
```

**Then freeze everything but one string.** WB-386
(`scripts/satz_probe.py`) captures exactly what stage 3 is shown — terms,
quantities, candidates — from one real run, then varies *only the sentence
string in the prompt*. Without the freeze, comparing two sentences also
compares two ingredient lists, two term lists, and two candidate sets. Ten
frozen templates, six prompt variants, three repetitions each (median of
chosen terms, 2026-08-29, `Qwen3.8-27B-Instruct`; `satz` is the state before
the fix — the sentence as a bare line; `satz_ganz` the same as a full
sentence; `ohne_satz` no sentence line at all; `regel` sentence plus a rule
line; `rezept` the recipe name *instead*; `rezept_satz` both):

| Template | Terms | satz | satz_ganz | ohne_satz | regel | rezept | rezept_satz |
|---|--:|--:|--:|--:|--:|--:|--:|
| Salat | 9 | 6 | 6 | **8** | 8 | 7 | 7 |
| Kartoffelpürree | 10 | **0** | **0** | **10** | 10 | 10 | **0** |
| Auflauf | 8 | 8 | 8 | 8 | 8 | 8 | 8 |
| Suppe | 11 | 10 | 10 | 10 | 10 | 10 | 10 |
| Eintopf | 11 | 10 | 10 | 10 | 10 | 10 | 10 |
| alles für Salat | 9 | 6 | 6 | **8** | 6 | 7 | 7 |
| alles für Königsberger Klopse | 15 | 14 | 14 | 13 | 14 | 11 | 14 |
| alles für Lasagne | 13 | 13 | 13 | 13 | 13 | 13 | 13 |
| alles für Chili con Carne | 10 | 8 | 8 | 8 | 8 | 8 | 8 |
| alles für Apfelkuchen | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| **Sum** | **102** | **81** | **81** | **94** | 93 | 90 | 83 |

Three runs per cell, three times the same number — in every cell but one. On
frozen input at temperature 0 the box is reproducible, so the noise of the
breadth run (Bibimbap 0 vs. 7) was not sitting in stage 3.

**The table corrected the hypothesis three times:**

1. **Phrasing was not it.** `satz` and `satz_ganz` are identical in all ten
   rows. The control line above ("Salat" 0 vs. "alles für Salat" 6) measured
   a difference *before* stage 3 — the two sentences had produced different
   terms and therefore different candidates.
2. **More context does not drown the sentence out.** `rezept_satz` puts the
   recipe name right next to it, and Kartoffelpürree is back at 0. As long
   as the sentence is present, the model checks against it.
3. **Nor was it the clash with a skewed recipe.** Königsberger Klopse is the
   counter-case the hypothesis cannot explain: sentence and recipe match
   perfectly, yet the live run had it at 0 of 16 — and on the frozen
   template it stands at 14 of 15. The all-or-nothing failure hangs on the
   sentence being in the prompt at all, not on whether it fits the recipe.

The headline cell: **Kartoffelpürree 0 of 10 WITH the sentence, 10 of 10
WITHOUT — same candidates, same terms, one string of difference, reproduced
three times.**

**The fix is one line.** On the recipe path, stage 3 no longer receives the
sentence (`chat.Chat._aus_quelle`). The model path keeps it: there the terms
come *from* the sentence, it is the only context the stage has ("Milch" is
undecidable, "Milch für den Kaffee" is not) — and no measurement exists for
that path, so it was not touched. The stage's guarantee is untouched too:
only presented candidates can be chosen, a foreign ID is rejected, not
repaired (`tests/test_gerichte.py`).

**Before and after, over all 64 dishes** — two `breite_probe.py --messen`
runs on the same pre-warmed copy, 2026-08-29, nothing between them but this
change:

```
                                  before      after
Terms with catalog product        412/541     446/540      76 % -> 83 %
Free-text lines                   129         94           24 % -> 17 %
Per-dish quota, median            83 %        89 %
Dishes below 50 %                 5           1
Dishes at 0 %                     4           1
Dishes with errors                0           0
```

Per dish: 13 better, 10 worse, 38 unchanged. The improvements are large
(Königsberger Klopse 0/16 → 12/16, Kartoffelpürree 0/11 → 10/11, Salat
0/9 → 8/9); each regression is a single term, and the ten regressed dishes
were not individually inspected — the sum carries the decision, the single
line does not. The acceptance criterion, verbatim: "Salat" 0 of 9 → 8 of 9,
"alles für Salat" 6 of 9 → 8 of 9. Both phrasings now yield the same number,
which was the point: a skewed recipe may be a skewed recipe, but it must not
drag the ingredient list down with it.

## 3. Eval labels the product produces anyway

Before an order goes out, every suggestion row gets a per-row **Yes/No** from
the person filling the cart — not for an eval, but because she has to decide
anyway before ordering. On submit, those decisions are written to the turn's
`chat.turn` span as Phoenix annotations with `annotator_kind = "HUMAN"`:
`kept`/`removed` per suggestion (score 1.0/0.0, explanation = the search
term, so the badly mapped term is readable right at the line), a
`mapping_precision` score per turn, and `correction` annotations that keep
two findings apart — `corrected` ("the right product *was* in the candidate
list": a model error) versus `free_text` ("it was not": a catalog gap, which
no model can be blamed for). Ground truth without a single annotation job.

Three rules keep the numbers honest, and they are ordering, not code:

1. **Written only on submit.** Until then she may change her mind.
2. **`open` counts in neither numerator nor denominator** — otherwise every
   abandoned session would count as a model failure.
3. **A turn with only open suggestions gets no score at all.** A missing
   number is more honest than an invented one: `0.0` would say "all wrong"
   where "nothing said yet" is the truth.

Withdrawal follows from rule 1 without producing label garbage. On a phone,
"Yes" and "No" sit next to each other, and mis-taps happen; since WB-361
every decision can be withdrawn — back to `open`, not flipped. A withdrawn
tap therefore leaves **no label** (a property of the ordering, pinned by
`tests/test_labels.py`), but it stays visible: `withdrawn` is recorded as
metadata on the annotation. A `kept` with `withdrawn = 1` is a different data
point from a first-glance `kept` — someone hesitated — yet it is deliberately
not a third label, because a third label would corrupt a precision that has
exactly two outcomes. Stable identifiers (`zettel-suggestion-<id>`) make
submitting twice yield the same set, not a double one.

Why not LLM-as-judge? The only judge available on this hardware is the same
Qwen that runs the agent — same knowledge, same blind spots; a product it
finds fitting it will also find fitting on second look. The experiment
harness in `EVALS.md` uses that judge only where deterministic category
checks cannot decide, and its column sits beside them, never above. For the
live product the answer is simpler: **the person who knows the ground truth
is already pressing the buttons.**

## What is weaker than it looks

Quoting the numbers above without this list would misrepresent them.

* **One run per state, no repetitions** — for the breadth measurement and
  the before/after comparison alike. Bibimbap scored 0, then 7 of 8 on a
  re-run; 64 dishes make such an outlier visible, a small sample would not.
  There are no confidence intervals because there is nothing to compute them
  from.
* **The breadth run measures a warm dish store.** Recipes were pre-fetched
  serially before measuring; the numbers describe the second and every later
  sentence about a dish, not the very first.
* **"Yes to every row" is the most favorable assumption.** The pipeline
  numbers assume every suggestion is accepted; anyone deleting rows gets
  less. The quantity carry-through is an upper bound.
* **The model path is unmeasured.** All ten frozen templates are recipe
  paths. Whether the sentence earns its keep on the model path ("Milch für
  den Kaffee") is an untested claim — the fix deliberately left that path
  alone rather than touch what has no measurement.
* **Ten templates decided the prompt variant, not 64.** Running `regel` and
  `rezept` over all 64 dishes would have cost two more full runs and was not
  done.
* **Catalog state gates comparability.** The butter case is from a
  2,498-product catalog, the breadth runs from a 10,361-product one; scores
  measured on different catalog states must not be compared with each other.
  Retrieval ranks (bm25) are not calibrated across queries at all, which is
  why no eval thresholds on them exist.

## Sources

The butter case, the span contract, and the label design:
[`OBSERVABILITY.md`](OBSERVABILITY.md). The breadth measurement, the frozen
probe, and the before/after tables: [`EVALS.md`](EVALS.md). The one-line fix:
commit `6e5e144` ("WB-386: Stufe 3 sieht auf dem Rezeptweg den Satz nicht
mehr — 76 % -> 83 %"). All measurements dated 2026-08-28/29, model
`Qwen3.8-27B-Instruct`, single RTX 3090. The same harness has already
judged a second open model in an afternoon (`Llama-3.1-Nemotron-Nano-8B`:
median 0 % catalog hits against Qwen's 89 % — recorded in `EVALS.md` as
plainly as the wins). The repo's gates — 1,346 tests and
a 79-check smoke gate at the time of writing — run without network, model,
or Phoenix.
