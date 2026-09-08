# Wochenplan, Phase 1 — Daten und Ansicht, ohne Modell: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Plan über n Tage, dessen Tage von Hand mit Rezepten belegt werden, und aus dem OHNE Modell eine Einkaufsliste in den bestehenden Korb entsteht — zusammengezählt über die Tage, abzüglich des erklärten Bestands. Das ist Stück 1 aus `2026-09-06-wochenplan-design.md` (Abschnitt 9) und der Nutzen, der auch ohne LLM trägt.

**Architecture:** Drei Tabellen (`plan`, `plan_tag`, `plan_bestand`), ein Paket `zettel/wochenplan/` mit vier Modulen (`speicher`, `rahmen`, `vorlage`, `liste`), fünf Routen unter `/plan`, eine Vorlage `plan.html`. Nichts Bestehendes wird umgebaut: die Einkaufsliste benutzt `recipe_item` (vom Menschen verknüpft), sonst `recipe_zuordnung` (vom Modell gemerkt, WB-408) plus `herkunft.zuordnen` für die Mengen, und legt über `korb.einlegen(menge=…)` ein — dort wird je Produkt addiert und danach gegen die Packung gerundet (WB-362).

**Tech Stack:** SQLite (Schema in `zettel/db.py`), FastAPI + Jinja + htmx wie überall, `t()`-Texte in `zettel/web/texte/{de,en}.json`, pytest ohne Netz.

---

## Entscheidungen, die dieser Plan trifft

**Die Mengenfrage (Fund vom 06.09.).** Der Entwurf rechnet mit `recipe_item.amount`; die Tabelle ist leer (0 Zeilen), die Mengen stehen in `recipe_ingredient` (700/700) — und die wird bei jedem Quellabruf neu geschrieben. Deshalb liest die Einkaufsliste in dieser Reihenfolge, je Rezept:

1. `recipe_item` mit `amount` — die vom Menschen verknüpften Produkte (Spec 4). Wo sie stehen, gelten sie.
2. sonst `recipe_zuordnung` + `herkunft.zuordnen(recipe_ingredient, …)` — was das Modell schon einmal zu diesem Rezept ergeben hat, mit den Mengen der Quelle daneben. Nichts davon wird kopiert; die Mengen werden beim Lesen gerechnet (dieselbe Regel wie in `zuordnung.py`).
3. sonst `recipe_ingredient` als Freitext mit Menge — kein Produkt, aber die Zeile geht nicht verloren.

**Zustände.** `plan.status` ist `entwurf` oder `im_korb` — nicht `bestellt` wie im Entwurf: „bestellt" wäre erst der Korb, der abgeschickt ist, und den kennt `orders`. Der Plan sagt nur, dass seine Liste übergeben wurde; `order_id` zeigt, wohin.

**Bestand hängt am Plan** (`plan_bestand.plan_id`, ON DELETE CASCADE). Es gibt keine Tabelle, in der ein Vorrat die Woche überlebt.

**Abziehen ist Rechnen mit `mengen`.** 500 g Bestand gegen 800 g Bedarf ergibt 300 g; 6 Stück gegen 500 g ergibt **nichts** — die Zeile bleibt, wie sie ist, und sagt, warum. Kein Raten.

---

### Task 1: Schema

**Files:**
- Modify: `zettel/db.py` (SCHEMA: drei Tabellen; TABLES)
- Test: `tests/test_db.py` (Tabellen da, Cascade greift)

- [x] **Step 1:** `plan` — id, created_at, von (ISO-Datum von Tag 1), tage, personen, max_minuten, budget_cents, rahmen_text, status CHECK(`entwurf`,`im_korb`), span_id, order_id REFERENCES orders ON DELETE SET NULL.
- [x] **Step 2:** `plan_tag` — id, plan_id CASCADE, pos, datum, recipe_id REFERENCES recipe ON DELETE SET NULL, portionen, auswaerts INTEGER DEFAULT 0, grund TEXT (ein Satz, keine Zahl), decision CHECK wie überall, decided_at; UNIQUE(plan_id, pos).
- [x] **Step 3:** `plan_bestand` — id, plan_id CASCADE, product_id SET NULL, name NOT NULL, menge, einheit, herkunft CHECK(`erklaert`,`aus_bon`), receipt_item_id SET NULL, decision, decided_at.
- [x] **Step 4:** Test: Migration legt alle drei an; `DELETE FROM plan` räumt Tage und Bestand mit.

### Task 2: `wochenplan.rahmen` — Formular und Bestandstext lesen

**Files:**
- Create: `zettel/wochenplan/__init__.py`, `zettel/wochenplan/rahmen.py`
- Test: `tests/test_wochenplan.py`

- [x] **Step 1:** `Rahmen` (frozen dataclass): tage, personen, max_minuten, budget_cents, text, bestand (list). `aus_formular(werte: dict) -> Rahmen`: Zahlen nachsichtig (`"30"`, `"30 min"`, `"40,50"` → 4050 Cent), Unsinn → None, tage 1..14 (Vorgabe 5), personen 1..12 (Vorgabe 2).
- [x] **Step 2:** `bestand_aus_text("500 g Kartoffeln, 6 Eier, Nudeln")` → `[{"menge": 500.0, "einheit": "g", "name": "Kartoffeln"}, {"menge": 6.0, "einheit": None, "name": "Eier"}, {"menge": None, "einheit": None, "name": "Nudeln"}]`. Trennung an Komma/Semikolon/Zeilenumbruch; Einheit über `mengen.falte`; Komma-Dezimalen.
- [x] **Step 3:** Tests: die drei Beispiele oben, Leerzeilen, „1,5 kg", Unsinn.

### Task 3: `wochenplan.speicher` — anlegen, Tage setzen, entscheiden, laden

**Files:**
- Create: `zettel/wochenplan/speicher.py`
- Test: `tests/test_wochenplan.py`

- [x] **Step 1:** `anlegen(con, rahmen, *, von=None, jetzt=None) -> int` legt Plan und `tage` leere Tage (datum = von + pos) und die erklärten Bestandszeilen (`herkunft = erklaert`, `decision = kept` — was jemand hingeschrieben hat, ist bestätigt).
- [x] **Step 2:** `tag_setzen(con, tag_id, recipe_id | None, portionen=None)` — Rezept an einen Tag, Portionen vorbelegt mit `rahmen.personen`; setzt decision zurück auf `offen`, `auswaerts = 0`. `auswaerts_setzen(con, tag_id)` — recipe NULL, auswaerts 1, decision `kept`.
- [x] **Step 3:** `tag_entscheiden(con, tag_id, decision)` wie `vorschlaege.entscheiden`: nur `kept`/`removed`/`offen`.
- [x] **Step 4:** `bestand_hinzufuegen(con, plan_id, name, menge, einheit, product_id=None, herkunft="erklaert")`, `bestand_entscheiden(con, bestand_id, decision)`, `bestand_entfernen`.
- [x] **Step 5:** `laden(con, plan_id) -> dict`: Plan + `tage` (je mit `recipe`-Kopf: name, servings, zeitsatz via `zugrezept.gesamtzeit/zeitsatz`, n_zutaten) + `bestand` + `zusammenfassung` (Tage belegt/gesamt, Kochzeit summiert über belegte Tage, Tage über `max_minuten`). `aktuell(con)` = jüngster Plan; `alle(con)`.
- [x] **Step 6:** Tests: anlegen ergibt n Tage mit Datum; setzen/entscheiden/auswärts; laden liefert zeitsatz; Cascade beim Löschen.

### Task 4: `wochenplan.vorlage` — was zur Wahl steht

**Files:**
- Create: `zettel/wochenplan/vorlage.py`
- Test: `tests/test_wochenplan.py`

- [x] **Step 1:** `gerichte(con, rahmen=None) -> list[dict]`: je Rezept id, name, servings, minuten (gesamtzeit oder None), zeitsatz, zutaten (Namen aus `recipe_ingredient`, sonst aus `recipe_item`), n_zutaten, `passt_zeit` (True / False / None bei unbekannt), `quelle`. **Nur Rezepte mit mindestens einer Zutat.** Rezepte, auf die ein `dish` zeigt, und eigene Rezepte (ohne Quelle); die übrigen Quellrezepte eines Gerichts (die Alternativen aus `dish_treffer`) bleiben draussen — sonst stehen sieben Lasagnen zur Wahl.
- [x] **Step 2:** Mit `rahmen.max_minuten`: Rezepte mit bekannter Zeit über der Grenze fallen weg; unbekannte bleiben mit `passt_zeit = None` — „Zeit unbekannt" heisst nicht „passt".
- [x] **Step 3:** Tests: Dublette über dish; Zeitfilter; ohne Zutaten raus.

### Task 5: `wochenplan.liste` — die Einkaufsliste

**Files:**
- Create: `zettel/wochenplan/liste.py`
- Test: `tests/test_wochenplan_liste.py`

- [x] **Step 1:** `zeilen_je_rezept(con, recipe_id, portionen) -> list[dict]` nach der Rangfolge oben; je Zeile product_id | free_text, name, bedarf, einheit (skaliert mit `mengen.skaliere(amount, servings, portionen)`), qty_geraten (aus `wahl_menge`/`menge` der Zuordnung, sonst 1), `zutat` (Herkunftszutat), `recipe_id`.
- [x] **Step 2:** `zusammenlegen(zeilen) -> list[dict]`: Schlüssel product_id, sonst gefalteter Name; Bedarf mit `mengen.summiere`; scheitert das Summieren, bleibt `bedarf = None` und `grund` sagt es; `tage` = Liste der Tages-Positionen, `rezepte` = Namen.
- [x] **Step 3:** `bestand_abziehen(zeilen, bestand) -> list[dict]`: nur `decision = kept`; Treffer über product_id oder `herkunft.punkte(name, zeile.name) >= SCHWELLE`; Abzug mit `mengen.in_grundeinheit` + `vergleichbar`; Ergebnis je Zeile `gedeckt` (True → nicht in den Korb), `bedarf_nach_bestand`, `bestand_hinweis`.
- [x] **Step 4:** `einkaufsliste(con, plan) -> dict` = Zeilen (nur `kept`-Tage — ein Tag, der `offen` ist, wird mitgezählt, denn im Plan steht er; `removed` und `auswaerts` nicht), `rest` (Zutaten, die nur an EINEM Tag vorkommen — zählbar), `preis_cents` (Summe `packungen × price_cents` über `mengen.rechne`, `echte_preise` wo vorhanden; `ohne_preis` zählt), `budget_ueber` (Cent, oder None).
- [x] **Step 5:** `in_den_korb(con, plan_id) -> dict`: je nicht gedeckte Zeile `korb.einlegen(product_id=…, free_text=…, qty=…, menge=…, einheit=…, begriff=name)`; Plan auf `im_korb` mit `order_id = orders.warenkorb(con)`; Bericht `{eingelegt, gedeckt, ohne_produkt}`. Leerer Plan → `WochenplanFehler` (nicht stumm nichts tun — dieselbe Begründung wie `LeeresRezept`).
- [x] **Step 6:** Tests mit `vorlagen.katalog`: zwei Rezepte mit je 40 g Knoblauch (recipe_item) → eine Korbzeile 80 g; Bestand 500 g gegen 800 g → 300 g; Stück gegen Gramm → nicht abgezogen mit Hinweis; Freitextzeile trägt die Menge; Rest zählt richtig; leerer Plan wirft.

### Task 6: Oberfläche

**Files:**
- Modify: `zettel/web/app.py` (Routen), `zettel/web/templates/mehr.html` (Link), `zettel/web/static/stil.css`
- Create: `zettel/web/templates/plan.html`, `_plan_tage.html`, `_plan_liste.html`
- Modify: `zettel/web/texte/de.json`, `en.json`
- Test: `tests/test_web_plan.py`

- [x] **Step 1:** `GET /plan` — jüngster Plan, sonst nur das Formular. `POST /plan` — anlegen aus dem Formular (Felder über `eingaben()`; Zahlen nachsichtig), Weiterleitung auf `/plan/{id}`.
- [x] **Step 2:** `GET /plan/{id}`: Kopf (Zeitraum, Rahmen), Tage als `.zeile`n mit Wochentag, Rezept (Link), zeitsatz, Portionen, Ja/Nein/Auswärts und ein `<select>` aus `vorlage.gerichte` mit „Setzen"; darunter Bestand (Liste + Zeile hinzufügen); darunter die Einkaufsliste mit Summe, Budgetvergleich, Rest, „In den Korb".
- [x] **Step 3:** `POST /plan/{id}/tag/{tag_id}` (`recipe_id`, `portionen`, oder `auswaerts=1`), `POST /plan/{id}/tag/{tag_id}/entscheiden?decision=`, `POST /plan/{id}/bestand` (`text` → `bestand_aus_text`), `POST /plan/{id}/bestand/{bid}/entscheiden?decision=`, `POST /plan/{id}/korb` → Bericht als `.fertig`-Meldung, Korbzahl im Kopf springt.
- [x] **Step 4:** Werte in der Query wie bei `/sprache` und `/rolle` (kein `python-multipart`); htmx-Teiltausch für Tag-Zeilen (`hx-target` auf die Zeile) und Liste, ohne JavaScript ganze Seite.
- [x] **Step 5:** Texte: alle Sätze über `t('plan.*')`, deutsch und englisch; Wochentage über `t('plan.wochentag.0..6')`.
- [x] **Step 6:** Link auf `/mehr` („Wochenplan"), `wo('/plan')` markiert „Mehr".
- [x] **Step 7:** Tests: Formular legt an; Tag setzen ändert `plan_tag`; Ja/Nein; Bestand hinzufügen; „In den Korb" legt `order_item` mit `need_amount` an und die Seite zeigt die Meldung; 404 für einen fremden Plan; englische Seite sagt „Weekly plan".

### Task 7: Grün und dokumentiert

- [x] **Step 1:** `.venv/bin/python -m pytest -q` — alles grün, kein Test geht ins Netz.
- [x] **Step 2:** `2026-09-06-wochenplan-design.md` Abschnitt 9: Stück 1 als gebaut markieren, mit der Mengenentscheidung oben.
