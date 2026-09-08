---
name: grocery-catalog-source
description: Use when adding a grocery catalogue source for a shop in another country — "add a scraper for my supermarket", "make this work with Tesco/Albert Heijn/Migros/Carrefour", "the catalogue is German, I want my local shop", "how do I crawl my shop's products". Also for auditing an existing source that has gone quiet or thin.
version: 1
---

# Adding a catalogue source for your country

Zettel ships with one source: `knuspr.de` (Germany). Everything above the
catalogue — search, cart, chat agent, recipes, receipts, evals — is
source-agnostic. A new country means **one crawler and one parser**, not a fork.

This skill is the method that produced the German source, written down so you
do not have to rediscover it. It is deliberately opinionated about two things:
**politeness** and **what you are allowed to guess** (nothing).

---

## 0. Before any code: is this shop allowed and reachable?

Do this in **one request per host**, with an honest User-Agent. It takes
minutes and decides whether the rest is worth doing.

```bash
UA="zettel/1.0 (private household catalogue; source evaluation; 1 req/1.5 s)"
curl -s -o /tmp/rb -w "%{http_code}\n" -A "$UA" "https://SHOP.example/robots.txt"
cat /tmp/rb
```

Read the `User-agent: *` block and decide:

| what you see | what it means |
|---|---|
| the search/API paths you wanted are `Disallow` | **stop.** That is the shop saying no. Do not route around it |
| `403` on `robots.txt` itself | a bot wall. Try a second client once — walls are often client-dependent — and if it holds, stop |
| a `Sitemap:` line | **the best thing that can happen.** Go to step 2 |
| open, with a normal shop | continue |

Then, three rules that are not negotiable in this project:

* **No browser automation, no disguised headers, no login.** If the data needs
  an account or a rebuilt app protocol, it is out of scope — that is a
  decision, not an oversight.
* **No paid scraping service** to get around a `Disallow`. The verdict does
  not change because someone else sends the request.
* **One request at a time, ≥1.5 s apart, honest User-Agent naming what you
  are.** A wider catalogue means a *longer* nightly run, never a faster one.

## 1. Find the payload the shop's own front-end uses

Do **not** parse rendered HTML. Almost every modern grocery site fetches JSON
and draws it client-side; that JSON is stabler, smaller and already typed.

1. Load one category or search page and save it.
2. Look for a hydration blob: `__NEXT_DATA__`, `__NUXT__`,
   `self.__next_f`, `window.__INITIAL_STATE__`. It often contains the
   prefetched query **and the query keys** that name the calls.
3. Grep the page and its JS chunks for endpoint fragments:

```bash
grep -oE '/[a-z0-9/_-]*(api|service|graphql)[a-z0-9/_.-]*' page.html | sort -u
grep -oE 'src="(/[^"]+\.js)"' page.html | sed 's/src="//;s/"//' > chunks.txt
# then fetch a few chunks and grep them for the same fragments,
# plus: byIds, products=, productIds, BATCH_SIZE, path:"
```

> Worked example (Germany, 2026-09-06): the react-query key
> `productCardsInfosLoading` led to a module that contained
> `path:"/api/v1/products/card"` and `PRODUCTS_BATCH_SIZE = 100`. That endpoint
> answers **100 product ids per request**. Finding it turned a 6,388-request
> job into 238 requests. **Budget an hour for this search — it is the highest
> paid hour in the whole task.**

If you cannot find a batch endpoint after a bounded search (say 15 requests),
fall back to the search endpoint — but say so, and keep looking later.

## 2. Get the list of what exists — the sitemap

A search endpoint answers "what matches this word". It can never answer
**"what is missing"**. A product sitemap can, and most shops publish one.

```bash
curl -s -A "$UA" https://SHOP.example/sitemap.xml | grep -i loc
# then the product one, often gzipped:
curl -s -A "$UA" https://SHOP.example/sitemap_products.xml.gz | gunzip | grep -c '<loc>'
```

If the product id is in the URL (`/269-weisskohl-1-stk`), you have won: one
request gives you the complete id set, and the difference against your own
`product` table is the exact gap.

> Worked example: 15,161 ids in the sitemap, 8,773 in the database — **6,388
> missing**, including the plain white cabbage the chat agent had been failing
> to find. The term list had been the scope of the catalogue without anyone
> deciding that.

## 3. Read the *whole* payload once before writing the parser

Print the field names of one product and look at every one of them.

```bash
curl -s -A "$UA" "<one search or batch call>" \
  | python3 -c "import sys,json; p=json.load(sys.stdin)[...]; print(sorted(p)); print(p)"
```

> Worked example, and the cautionary tale: the German payload carries 41
> fields; the parser read 13. Among the discarded ones was
> `composition.nutritionalValues` — kcal, protein, fat, salt per 100 g, on
> every product, **from the very first recorded test fixture**. It was thrown
> away for forty days because nobody printed the payload.

Ask specifically for: **barcode/EAN/GTIN** (the only key that links to
anything outside this shop), **nutrition**, **ingredients/allergens**,
**category ids**, **unit and pack size**, **base price**, **stock**.

## 4. Write the parser as a pure function

Mirror `zettel/scrapers/knuspr.py`:

* `parse_products(payload) -> list[dict]` with exactly the keys in
  `knuspr.FELDER`. Pure — no network, no database. That is what makes it
  testable without going online.
* `parse_naehrwerte(payload) -> list[dict]` with `knuspr.NAEHRWERT_FELDER`,
  and **no row at all** when there is no nutrition. A row of NULLs claims an
  answer that does not exist.
* Money in **cents**, integers. Never floats in the database.
* A second response shape gets a **second module** (`knuspr_api.py` next to
  `knuspr.py`), not a chain of `or`s. After the next format change you want to
  see which half broke.

## 5. The five guards that keep a bad night from erasing the catalogue

Copy these; every one of them exists because something went wrong.

1. **Staging plus one transaction.** Write into `product_staging`, then move
   everything into `product` in a single transaction. A crawl that dies
   halfway leaves the catalogue untouched.
2. **Never delete, deactivate.** `active = 0`. Old orders and recipes point at
   products; a `DELETE` tears those references apart.
3. **A plausibility threshold on the run** (`MIN_ANTEIL = 0.5`). A run that
   returns less than half of the last good one is rejected with a reason. A
   captcha wall otherwise turns your catalogue into twelve products, silently.
4. **A partial run must not deactivate anything** (`uebernehmen(additiv=True)`).
   A top-up knows nothing about the products it did not ask for. Without this,
   tonight's full crawl quietly erases every product yesterday's top-up added.
5. **A credibility threshold on the sitemap** (`SITEMAP_MIN_ANTEIL`). If you
   use "still listed in the sitemap" to decide what stays active, then an
   empty read means an empty set — and an empty set deactivates **the entire
   catalogue** in one transaction. Refuse to believe a sitemap that names less
   than half of what you already know.

## 6. Reject and count; never repair

When a value is impossible, drop **that value**, keep the row, and say so in a
named function. Do not "fix" it.

> Worked example: 13 of 9,414 nutrition rows read over 900 kcal per 100 g,
> which no food does. In most of them kJ and kcal are simply swapped
> (1,935 kJ = 462 kcal). Swapping them back would look right and would be a
> **guess about someone else's mistake**. `verwirf_unmoegliche_kcal()` sets
> kcal to `NULL` and leaves kJ alone.

This is the same rule the chat agent lives by: a product id the model invented
is discarded and counted, never repaired.

## 7. Tests: record one real response, then never go online again

* Save one genuine response to `tests/fixtures/<shop>_<term>.json` (see
  `scripts/record_fixture.py`). Every test runs against that file.
* A test that talks to the live shop is red exactly when the shop changes —
  the worst possible moment, and nothing of yours is broken.
* Assert the shape, not the content: "more than 10 rows parsed" with the
  message *"format changed? re-record the fixture."*
* Write a test for each guard in section 5. They are the ones you will be
  glad about at 3 a.m.

## 8. Wiring it in

| file | what to add |
|---|---|
| `zettel/scrapers/<shop>.py` | crawl, parsers, `uebernehmen`, the guards |
| `zettel/scrapers/begriffe.py` | your shopping-list terms, in your language |
| `zettel/scrapers/nachtlauf.py` | pick the source; keep backup and thumbnails |
| `zettel/db.py` | nothing, if you reuse `product` — `source` and `UNIQUE (source, external_id)` are already there |
| `tests/test_<shop>.py` | fixture-driven tests |
| `docs/` | one page: which endpoints, measured when, with what numbers |

**Before you add a *second* shop to the same database**, decide what happens
to duplicates. The schema takes two sources happily; search, cart, recipe and
receipt matching do not. The same butter twice in the candidate list is a
worse prompt, not a bigger catalogue.

## 9. Report in numbers, not adjectives

Finish with a table someone can re-run:

```
products in sitemap        15,161
in our catalogue            8,773
fetched this run            6,388   in 238 requests
with nutrition              9,575
with barcode               12,274
requests total, all runs      573   serial, ≥1.5 s, honest UA
```

Then re-run the eval (`scripts/breite_probe.py`) over the same dishes as
before. The change in "ingredients found in the catalogue" is the only proof
that the work was worth doing.

See `PITFALLS.md` for the specific ways this goes wrong.
