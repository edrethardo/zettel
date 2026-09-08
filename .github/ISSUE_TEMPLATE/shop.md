---
name: A shop for my country
about: You want to add (or have added) a catalogue source for another supermarket
labels: source
---

**Shop and country**

**What `robots.txt` says** — paste the `User-agent: *` block. If the paths you
need are disallowed, the answer is no and this issue can be closed right away;
we do not route around that.

**How the front-end gets its products** — the endpoint, and how you found it.
A batch endpoint that takes many ids at once is worth an hour of searching:
it is the difference between a few hundred requests and tens of thousands.

**Is there a product sitemap?** — the URL, and how many `<loc>` entries.
This is what tells you what is *missing*; a search endpoint never can.

**One raw product object** — all fields, unedited. Especially: barcode/EAN,
nutrition, unit and pack size, base price, category, stock.

**How many products** the shop lists in total, if you know.

The method, the guards and the known traps are in
`.claude/skills/grocery-catalog-source/`. Read `PITFALLS.md` before you start;
every entry in it is a failure that already happened here.
