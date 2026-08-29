# The 60-second video — shot list and prep

Ein Drehbuch zum Abarbeiten: erst die Vorbereitung als Kommandos, dann zehn
Shots mit Sekundenangaben und fertigen englischen Untertiteln. Gefilmt wird
die **Demo-Instanz** (eigener Port, Kopie der Datenbank, keine
Haushaltsdaten) — nie der echte Shop auf 8730.

## Prep (do this once, ~10 minutes)

**1. Wake the box and check it serves:**

```bash
wake-vllm
# then confirm it serves (endpoint = your PICKNICK_LLM_ENDPOINT, see picknick.env):
curl -s "$PICKNICK_LLM_ENDPOINT/models" | head -c 200   # model id must appear
```

**2. Build the demo database** — a copy of the real one with the household
data removed (catalog and crawl history stay):

```bash
mkdir -p ~/picknick-demo/bons
cd ~/code/picknick_klon
.venv/bin/python - <<'EOF'
import sqlite3, os
ziel = os.path.expanduser("~/picknick-demo/demo.db")
if os.path.exists(ziel): os.remove(ziel)
sqlite3.connect("data/picknick.db").execute("VACUUM INTO ?", (ziel,))
d = sqlite3.connect(ziel)
for t in ["chat_kandidat","chat_sorte","chat_entwurf","chat_rezept",
          "chat_suggestion","chat_message","order_item","orders",
          "recipe_item","recipe_ingredient","recipe","receipt_item",
          "receipt","dish_treffer","dish"]:
    d.execute(f"DELETE FROM {t}")
d.commit(); d.execute("VACUUM")
print("demo.db ready,", d.execute("select count(*) from product").fetchone()[0], "products")
EOF
```

**3. Start the demo instance** on its own port, loopback only. Tracing stays
ON so the turn you film appears in Phoenix — note: that writes one demo trace
into the `Picknick Agent` project; if you don't want that, film the trace of
an older real turn instead and add `PICKNICK_TRACING=0` here.

```bash
cd ~/code/picknick_klon
PICKNICK_HOST=127.0.0.1 PICKNICK_PORT=8747 \
PICKNICK_DB=~/picknick-demo/demo.db PICKNICK_BON_DIR=~/picknick-demo/bons \
.venv/bin/python -m picknick.web.app
# leave this terminal running; note the PID line from `ss -tlnp | grep 8747`
```

**4. Warm the dish cache** so the on-camera turn skips the Chefkoch fetch and
runs in ~25 s instead of ~40–55 s (the recipe is then served from the copy's
cache — exactly what happens on every second sentence in real use; both times
measured while writing this script):

```bash
curl -s -m 300 -X POST http://127.0.0.1:8747/chat \
  --data-urlencode "satz=alles für Lasagne, und Klopapier" -o /dev/null
# then reset ONLY the chat and orders — the recipe/dish tables MUST stay,
# the dish cache points into them:
.venv/bin/python - <<'EOF'
import sqlite3, os
d = sqlite3.connect(os.path.expanduser("~/picknick-demo/demo.db"))
for t in ["chat_kandidat","chat_sorte","chat_entwurf","chat_rezept",
          "chat_suggestion","chat_message","order_item","orders"]:
    d.execute(f"DELETE FROM {t}")
d.commit(); print("chat reset, dish cache kept")
EOF
```

**Do not add the `recipe*` or `dish*` tables to that reset.** The dish cache
(`dish.recipe_id`) points at the stored recipe; deleting the recipe while
keeping the dish makes the next turn silently fall back to the model path —
no recipe card, no quantities. (Measured while writing this script.) The
warm-up turn is never *submitted*, so `recipe_item` stays empty and the next
turn still takes the Chefkoch path, not the saved-recipe path.

**5. Windows.** Two side by side:

* **Left — the shop, phone-shaped.** Firefox → `http://127.0.0.1:8747/chat` →
  `Ctrl+Shift+M` (responsive mode) → size **390 × 844**. Hide the devtools
  panel edges from the recording crop.
* **Right — Phoenix.** `http://localhost:6006`, project **Picknick Agent**,
  traces list sorted newest first.

Record at 1080p or better; the phone frame should fill ~40 % of the width.
Subtitles below are written to be pasted into the editor as-is.

**6. Cleanup afterwards:** `Ctrl+C` the demo instance in its terminal (or
`kill <PID>` — never `pkill -f`), then `rm -r ~/picknick-demo`.

## The 60 seconds

Total 10 shots. Times are targets — cut tighter rather than looser. Speak
nothing; the subtitles carry the story, the UI is German on purpose (one
subtitle explains that).

| # | Time | On screen | You do | Subtitle (English) |
|---|---|---|---|---|
| 1 | 0–5 s | Chat page, empty field | Type the sentence, hit **Fragen** | `One sentence, like at home (the app speaks German): "everything for lasagna — and toilet paper."` |
| 2 | 5–9 s | Spinner on the chat page | Nothing — **hard cut after ~2 s of waiting** | `A 27B open model answers — self-hosted on a single RTX 3090, no cloud, no API key. (~25 s, cut)` |
| 3 | 9–17 s | The answer + recipe card | Scroll slowly to the card; pause on time/rating; flick past the 5 alternative recipes | `It recognized the dish and pulled a real top-rated recipe in ~100 ms — cooking time, rating, five alternatives.` |
| 4 | 17–22 s | Servings field on the card | Change servings 3 → 6, tap **Mengen neu rechnen** — the spinach row visibly jumps from "600 g … 2 ×" to "1200 g … 3 ×" (verified) | `Guests tonight? Set servings to 6 — every quantity and pack count rescales.` |
| 5 | 22–30 s | Suggestion rows | Tap **Ja** on the spinach row (pause on "1200 g gebraucht — 3 × 0,54 kg"), **Ja** on one more row | `Nothing enters the cart without a Yes. "1200 g needed → 3 packs" — the pack count is computed, not guessed.` |
| 6 | 30–35 s | Scroll to the toilet-paper row | Point the cursor at "Freitext", tap **Ja** | `No catalog hit for toilet paper — the term stays on the list as free text instead of silently vanishing.` |
| 7 | 35–42 s | Cart (`Korb` in the top bar) | Open cart, scroll once, tap **Bestellung abschicken** | `The cart is the order. Submitting also saves the confirmed rows as a recipe — next time, zero model calls.` |
| 8 | 42–49 s | Pick list (`Pick-Liste`) | Check two items off, tap **gab's nicht** on one | `In the store: check items off. "Wasn't there" is an honest third state, not a missing checkmark.` |
| 9 | 49–58 s | Phoenix, right window | Open the newest `chat.turn` trace; unfold a `catalog.search` RETRIEVER span so the scored documents show; hover `picknick.rejected = 0` | `Every turn is one trace. The model may only pick from these documents — invented IDs are rejected and counted. rejected = 0.` |
| 10 | 58–60 s | End card (still image) | — | `Open weights · one RTX 3090 · evaluated on 64 dishes · 1,146 tests. Repo: <link>` |

## Why this path (if you want to reorder, don't)

Shots 1–6 are the product promise, 7–8 prove it lands in the real world, 9
is the differentiator for this jury: the same turn, as a scored-document
trace. Shot 9 only works if shot 1 ran with tracing on — that is the reason
prep step 3 leaves it on.

**What can go wrong while filming:**

* The turn errors with "Chat nicht verfügbar" → the box fell asleep again.
  `wake-vllm`, wait for it to serve, redo prep step 4's reset, film again.
* The recipe card is missing → the dish cache or its recipe got wiped (the
  step 4 reset must include neither `dish`/`dish_treffer` nor the `recipe*`
  tables). Rebuild via step 2 + 4.
* The turn takes ~40–55 s instead of ~25 s → the dish cache was cold; that is
  the first-sentence-of-a-new-dish path. Fine for real life, too long on
  camera — reset per step 4 and film the second sentence.
* Suggestion rows show no quantities → the turn fell back to the model path
  (`weg = llm`); the answer will say so. Same fix: redo step 4.
