# Contributing

Zettel is a grocery agent for one household, written in German because that is
the language the household speaks. It is public because the **pattern** is
worth copying, and it is open to contributions because a household is a small
sample and yours is different.

This file is in English. Issues and pull requests in English are welcome and
expected.

## The one thing to understand first

The agent is allowed to do exactly one thing: **choose from the products the
shop actually returned.** It never invents a product id. An invented id is
discarded and counted, never repaired. Every Yes/No a human taps becomes an
eval label; every turn is one trace.

Almost every design decision in this repo follows from that sentence. If a
change makes it easier for the model to produce something that was never
retrieved, the change is wrong even when the output looks better.
`PATTERN.md` is the two-page version, with the three places in the code where
it lives.

## The code is German. You do not have to be.

Identifiers, comments and table names are German (`warenkorb`, `verworfen`,
`uebernehmen`). This is not a stylistic flourish and it is not going to
change: the comments carry the *reasons* for decisions — often several
paragraphs of measurement and counter-argument — and translating them would
cost the thing that makes them worth reading.

What this means in practice:

* **Write your issue, PR description and commit message in English.** That is
  fine and normal here.
* **New code follows the local convention:** German identifiers, comments that
  explain *why* rather than *what*. If your German is shaky, write the comment
  in English and say so in the PR — a correct reason in the wrong language is
  worth more than a missing one, and it is easy to translate.
* **User-visible text is different** — see the next section. That is
  translated properly.
* Machine translation of an existing comment is not a welcome patch. It loses
  exactly the nuance the comment exists for.

## The interface speaks German and English

Screen text lives in `zettel/web/texte/de.json` and `en.json`, and templates
use `{{ t('key') }}`. German is the default and the fallback.

```bash
.venv/bin/python -m zettel.sprache en      # what is still untranslated
```

To add a language: copy `de.json` to `xx.json`, translate what you can, add
`"xx"` to `SPRACHEN` in `zettel/sprache.py`. **A partial translation is
welcome** — any key you leave out falls back to German, so nothing breaks and
the interface is merely incomplete. There is deliberately no test that fails
on missing translations.

Two rules for translation files:

* **No HTML in a translation unless the German original has it.** Eleven
  strings are rendered with `| safe` because they contain a `<strong>` that was
  in the markup before; every value interpolated into them is escaped
  separately. A translation that adds a tag is adding markup to a page, which
  is a code review and not a text change.
* **Do not translate text that is also input.** The three example sentences
  under the chat box are the value the button submits to the agent, and the
  agent is German: `rezeptweg.erkenne` looks for German words and the
  catalogue holds *Milch*, not *milk*. An English example would look right and
  return an empty basket. They stay German on purpose, and the line above them
  says so in your language.

What is *not* translated yet: the sentences the chat agent composes at runtime
("… 14 ingredients in the recipe, 10 of them on the list"). They live in
`zettel/assistant/` and are text with numbers in them. That is its own piece
of work, and it is a good first contribution — and it is the one that would
let the example sentences move too.

## Your shop is not our shop

The catalogue source is `knuspr.de` (Germany). Everything above it — search,
cart, chat, recipes, receipts, evals — does not care where products came from;
`product` has a `source` column and `UNIQUE (source, external_id)`.

Adding your country means one crawler and one parser. There is a skill with
the full method, the guards and the ways it goes wrong:

* `.claude/skills/grocery-catalog-source/SKILL.md`
* `.claude/skills/grocery-catalog-source/PITFALLS.md`

Four rules from it that are conditions of merging, not suggestions:

1. **`robots.txt` decides.** If the paths you need are disallowed, the answer
   is no. No browser automation around a bot wall, no rebuilt app protocol, no
   login, no paid scraping service to launder the same request.
2. **One request at a time, at least 1.5 s apart, with a User-Agent that says
   who you are.** A bigger catalogue takes longer, never faster.
3. **Never delete a product; deactivate it.** Orders and recipes point at
   products.
4. **Reject and count; never repair.** An impossible value is dropped and
   noted, not corrected by guessing what the shop meant.

## Running it

`GETTING-STARTED.md` has the five-minute path, including how to run without a
GPU. Short version:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # ~1,400 tests, ~90 s, no network
.venv/bin/python -m zettel.web         # the shop
```

## Tests

* **No test goes to the network.** Not to the shop, not to the recipe site,
  not to the model. A test that talks to a live service is red exactly when
  that service changes — the worst moment, and nothing of yours is broken.
* Real responses are recorded once into `tests/fixtures/` and everything runs
  against those files. When a shape check fails, the assertion message should
  say *"format changed? re-record the fixture."*
* Every safety rail gets a test that proves it fires. The ones that matter
  most are the ones that prevent silent, catalogue-wide damage.
* Tests are written in German, like the rest of the code, and their names are
  sentences: `test_ein_verworfener_lauf_hinterlaesst_keine_naehrwerte`.

## What a good pull request looks like

* One change, with the reason in the description.
* Comments that answer *why this and not the obvious alternative* — that is
  the house style, and it is why this repo is readable a year later.
* A number where you make a claim. "Faster" is not a claim; "20 s → 6 s per
  turn, measured over 128 dishes" is.
* Tests green, and a new test for anything a future change could quietly
  break.

## What will not be merged

* Anything that lets the model emit a product that was not retrieved.
* Anything that works around a shop's `robots.txt`, terms, or bot protection.
* Real household data — orders, receipts, photographs, names. The database is
  gitignored for a reason, and screenshots get checked before they land.
* A dependency that is not in the list this project committed to, without a
  discussion first. The point of "no cloud, no API keys, one open model on one
  GPU" is that it stays true.

## Reporting something

An issue is most useful with: what you did, what you expected, what happened,
and — if it involves the agent — the trace or the turn. If it involves a
shop's data, the raw payload for one product says more than a description of
it.
