# The pattern: let the model choose only from what you retrieved

Zettel is a grocery app, but the thing inside it that is worth copying is
not about groceries. It is a small discipline for any agent that has to
**select rows from a database** — products, tickets, documents, contacts —
and that you want to be able to trust and to measure. Three rules, three
code locations, and the numbers they produced.

## The problem in one sentence

A language model asked to "pick the product for *passierte Tomaten*" will
happily answer with a product id — whether or not that id exists. A schema
can force the answer to be an integer; it cannot force it to be *true*.

## Rule 1 — the model never sees the whole catalog, only candidates

Stage 1 turns the sentence into search terms and sees zero catalog rows.
Stage 2 is **the shop's own search** (SQLite FTS5), not a model call: it
runs each term and presents at most five candidates per term. Stage 3 asks
the model to choose *from those candidates* — and the prompt carries their
ids. In Phoenix, stage 2 is a `RETRIEVER` span with the candidates as scored
documents, so a bad pick can be read as "the right one was not retrieved"
versus "it was there and the model took another" without opening JSON.

## Rule 2 — an id that was not presented is rejected, not repaired

The single most important lines in the repo, `zettel/assistant/plan.py`
(stage 3, after the model answered; the code speaks German — `erlaubt` =
allowed, `verworfen` = rejected, `begriff` = search term, `mit_kandidaten` =
terms with their candidates):

```python
erlaubt: dict[int, dict] = {}
for aufgabe in mit_kandidaten:
    for p in aufgabe["kandidaten"]:
        erlaubt.setdefault(int(p["id"]), {"begriff": aufgabe["begriff"], "produkt": p, ...})

for eintrag in roh:                                   # what the model returned
    pid = _id(eintrag, ("produkt_id", "product_id", "id"))
    if pid is None or pid not in erlaubt:
        # HERE the hallucination path ends. Do not repair, do not take the
        # nearest id — reject and name it. The term is not lost: the caller
        # turns it into a free-text line the user still sees.
        verworfen.append({"produkt_id": pid, "begriff": ..., "grund": "nicht vorgelegt"})
        continue
```

No fuzzy rescue, no "closest match". A rejected pick becomes a visible
free-text row ("*süße Sahne — im Katalog nicht gefunden*"), so the user
discovers the gap now and not in the store. The check lives in code on
purpose: structured output (`response_format: json_schema`) is on, but it
enforces shape, not truth — and it turned out to be silently ignored for
four days after a vLLM upgrade (`EVALS.md`, 2026-09-05). Nothing changed,
because the guarantee was never in the sampler.

## Rule 3 — count it on the trace, and let real decisions become labels

Every rejection is a number on the turn's span, `zettel/assistant/chat.py`:

```python
obs.setze(span, {
    "zettel.terms":    len(ergebnis.begriffe),   # search terms produced
    "zettel.products": ergebnis.n_produkte,      # terms that found a catalog product
    "zettel.free_text": ergebnis.n_freitext,     # terms that stayed visible as free text
    "zettel.rejected": len(ergebnis.verworfen),  # ids the model invented — rejected, counted
    "zettel.weakest_term": schwach, "zettel.weakest_rank": rang,   # whose search was worst
})
```

And the user's Yes/No on every suggested row is the ground truth nobody had
to annotate. It is written to Phoenix **when the order is submitted** — not
on tap, because a tap on a phone is withdrawn two minutes later and a label
must not be — `zettel/obs/labels.py`, called from `orders/korb.py`:

```python
for zug in zuege:                                  # every chat turn in this order
    q = vorschlaege.quote(con, msg_id)             # kept / removed / open per turn
    raus.append(_anno(span_id, "mapping_precision", score=float(q["quote"]),
                      metadata={"suggested": q["vorgeschlagen"], "kept": q["behalten"],
                                "removed": q["verworfen"], "withdrawn": zurueck}))
    # ... plus one `suggestion` annotation per row (kept / removed) and a
    # `correction` annotation naming which candidate would have been right.
```

The eval labels fall out of ordinary use, because the household has to go
through the list anyway.

## What it makes measurable

Because rules 1–3 fix *what* is presented and *what* is counted, comparing
two models is a re-run of the same dishes and no production code — see
[`EVALS.md`](EVALS.md); 64 dishes first, 128 the same day:

| per dish, 64 dishes, one RTX 3090 | catalog hits mean / median | dishes at 0 % | `rejected` total |
|---|---|---|---|
| Qwen3.8-27B (AWQ 4-bit) | 84 % / 88 % | 1 | 6 |
| Llama-3.1-Nemotron-Nano-8B | 25 % / 0 % | 41 | 2 |
| NVIDIA Nemotron 3.5 Lightning 30B-A3B (W4A16) | 85 % / 88 % | 0 | 2 |

| per dish, 128 dishes | catalog hits mean / median | dishes below 40 % | `rejected` total |
|---|---|---|---|
| Qwen3.8-27B (AWQ 4-bit) | 89 % / 91 % | 0 | 1 |
| NVIDIA Nemotron 3.5 Lightning 30B-A3B (W4A16) | 87 % / 89 % | 1 | 1 |

`rejected` stayed tiny for all three: the models rarely invent ids when the
candidates are right in front of them. The losses are elsewhere — in what
retrieval did not find — and the trace says so per term.

## Where it applies

Anywhere an agent picks from rows you own: support tickets ("assign to
the right team"), document QA ("cite from these passages"), CRM ("which
account is this email about"). Retrieve first, present ids, reject anything
not presented, count the rejections, and let the human's accept/reject be
the label. The rest of this repo is one worked example with the numbers
attached.
