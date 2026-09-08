# Pitfalls — every one of these actually happened

Read this before you debug. Each entry is a real failure from the German
source, with the shape it takes so you recognise it in yours.

## The catalogue is the shape of your shopping list, not of the shop

**Symptom.** The chat agent cannot find ordinary ingredients — white cabbage,
spring onion, marjoram — and picks something absurd instead (a *white cabbage
face mask* was the real case). It looks like a bad model.

**Cause.** Search-term crawling. The catalogue only ever contains what someone
thought to type into a list of terms. Measured: 8,773 of 15,161 products.

**Fix.** Crawl by sitemap difference, not by terms. The sitemap says what
exists; your database says what you have; the difference is the job.

**How to see it before a user does.** Take the ingredients your evals could not
map, and grep them against the sitemap slugs. 44 of 106 were sitting there.

## Tonight's full crawl erases yesterday's top-up

**Symptom.** Products you added by hand or by a gap-fill run are `active = 0`
the next morning. Nobody changed anything.

**Cause.** A full crawl deactivates everything it did not see, and it never
sees the top-up's products — no search term asks for them.

**Fix.** Two of them, and you want both. A partial run must never deactivate
(`additiv=True`). A full run must only deactivate what the **sitemap** no
longer lists (`gefuehrt=...`).

## The empty sitemap that deletes everything

**Symptom.** One bad night and the whole catalogue is inactive.

**Cause.** You made "listed in the sitemap" the criterion for staying active,
the fetch returned an empty body (redirect, bot wall, a test double answering
the wrong URL), and an empty set means *nothing is listed*. One transaction,
no error, no trace.

**Fix.** `SITEMAP_MIN_ANTEIL`: refuse to believe a sitemap naming less than
half of the products you already know, and fall back to the old behaviour with
a printed reason. Write the test that hands it an empty sitemap.

## The staging table that outlives a schema change

**Symptom.** `table product_staging has no column named <new column>` on the
first run after you add a field.

**Cause.** `CREATE TABLE IF NOT EXISTS` does not look inside an existing table.

**Fix.** Drop and recreate staging on every run. Between two runs its contents
belong to nobody — which is also why it does not live in the main schema.

## The parser that reads a third of the payload

**Symptom.** You are about to add a data source for nutrition, allergens or
barcodes.

**Cause.** Nobody ever printed the full product object. In the German case,
kcal and protein had been arriving in every response, and being discarded, for
forty days — the very first recorded test fixture contains them.

**Fix.** Before adding any source: `print(sorted(product_object))` and read
every field name out loud. The cheapest new source is the one you already
download.

## The impossible number that is not a bug in your code

**Symptom.** 3,141 kcal per 100 g of goat camembert.

**Cause.** The shop's own data: kJ and kcal swapped, a misplaced decimal, or a
whole-box value labelled per 100 g.

**Fix.** A named guard that discards the impossible value, keeps the rest of
the row, and is documented as *not* repairing it. Swapping them back would be
a guess about someone else's mistake, and a meal plan computed on guessed
calories is worse than one with a gap.

## Two response shapes, one parser

**Symptom.** After you add the batch endpoint, half the catalogue has null
prices or the wrong image path.

**Cause.** The batch API and the search API return the same goods under
different names: `name` vs `productName`, `prices.salePrice` vs `price.full`,
`image.path` (absolute CDN URL) vs `imgPath` (relative), categories from
`level 0` vs `level 1`, `stock.availabilityStatus` vs `inStock`.

**Fix.** A second module with its own field map, writing into the same
columns. And strip the CDN prefix from image paths, or half your catalogue
silently hot-links to the shop's CDN — which also breaks the moment the shop's
URLs rotate.

## The field that quietly becomes NULL

**Symptom.** Barcodes disappear after a nightly run.

**Cause.** The search endpoint has no barcode, so its rows carry `NULL`, and a
plain `ON CONFLICT DO UPDATE SET ean = excluded.ean` overwrites the good value
with nothing.

**Fix.** `ean = COALESCE(excluded.ean, product.ean)`. In general: a response
that *does not know* a field must not be allowed to erase it.

## Row counts that stay the same while the data rots

**Symptom.** A run is accepted as `ok`. Prices are all NULL.

**Cause.** The shop renamed a field. Your row count did not change, so the
50 % threshold never fired.

**Fix.** Record per-run field coverage — share of rows with a price, a unit, a
category, an image — and reject a run when one of them collapses against the
last good run. This is the row threshold applied one level down.

## Politeness failures that look like technical ones

* A `403` that appears with `curl` and not with another client is a
  fingerprint check, not a ban — but if it holds across clients, it is a
  decision, and the answer is to stop.
* An honest User-Agent must be **pure ASCII**. A German umlaut in the header
  killed the nightly service instantly: httpx encodes headers as ASCII and
  raised before the first request went out.
* Rate limiting is per host, and it includes whatever else you have running.
  Do not start a second crawl while a helper is already fetching.
