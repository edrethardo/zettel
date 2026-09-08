# Wochenplan, Phase 2 — `plan.woche`, die vierte Modellstufe: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Knopf „Woche planen": das Modell ordnet den offenen Tagen Gerichte zu — **nur aus der Vorlage** (`wochenplan.vorlage.gerichte`), erfundene IDs werden verworfen und gezählt, nie repariert. Neuplanung einzelner Tage über denselben Zug. Ein `plan.woche`-Span mit den Attributen aus Abschnitt 7 des Entwurfs, Labels aus der Nutzung beim Übergeben in den Korb.

**Architecture:** Die Stufe lebt neben `extract` und `choose` in `zettel/assistant/plan.py` (`woche()`, `Wochenwahl`) und benutzt denselben `_frage`-Weg (Guided JSON, Temperatur 0, Denken aus). Der Zug (`zettel/wochenplan/zug.py`, `Planer`) öffnet `obs.chain("plan.woche")`, holt Vorlage und offene Tage, ruft die Stufe, prüft die Antwort gegen die Vorlage, schreibt die Tage und lässt für neu belegte Rezepte ohne gemerkte Zuordnung `Chat.zuordnung_vorwaermen` laufen — damit hängen die `catalog.search`-Retriever der Einkaufsliste unter dem Plan-Span, wie im Entwurf gezeichnet. Der Planer fasst den Chat-Zug nicht an: er benutzt ihn.

**Tech Stack:** wie Phase 1; Spans über `zettel.obs`, Labels über `zettel.obs.labels` (Phoenix-Client im Hintergrund-Thread, wirft nie).

---

## Entscheidungen

**Was das Modell sieht.** Den Rahmen (Personen, Minuten), den bestätigten Bestand (Namen), die Tage mit Wochentag — festgelegte Tage mit ihrem Gericht, offene Tage als Frage —, und die Gerichte der Vorlage: `id`, Name, Gesamtzeit, Portionen, Zutatennamen. **Keinen Katalog, keine Preise, keine Kalorien.** Es ordnet zu und begründet in einem Satz (`grund`, Text); jede Zahl am Plan rechnet der Code.

**Was verworfen wird.** Eine `gericht_id`, die nicht vorgelegt war; ein Tag, der nicht offen war; ein Gericht, das an diesem Plan schon steht (doppelt); ein Gericht, das an genau diesem Tag verworfen wurde („Nein" heisst: nicht das). Jeder Verwurf zählt in `zettel.plan.rejected`, mit Grund.

**Neuplanung** ist derselbe Zug: offen sind die Tage ohne Rezept und die mit `removed`; `kept` und `auswaerts` sind Festlegungen und gehen als solche in den Prompt.

**Ohne Modell** bleibt alles aus Phase 1 bedienbar. Schläft die Box, sagt die Seite das (derselbe Satz wie im Chat) und weckt sie über denselben Wecker.

---

### Task 1: `plan.woche` — die Stufe

**Files:**
- Modify: `zettel/assistant/plan.py` (`SYSTEM_WOCHE`, `SCHEMA_WOCHE`, `MAX_TOKENS_WOCHE`, `Wochenwahl`, `wochenvorlage()`, `woche()`)
- Test: `tests/test_wochenplan_zug.py`

- [x] **Step 1:** `Wochenwahl` (frozen): `gewaehlt: [{tag, recipe_id, grund}]`, `verworfen: [{tag, recipe_id, grund}]`, `roh`.
- [x] **Step 2:** `wochenvorlage(tage, gerichte, *, personen, max_minuten, bestand)` — der Benutzerteil des Prompts als Text: Rahmen, Bestand, Tage (offen/festgelegt), Gerichte mit id. Deterministisch, testbar ohne Modell.
- [x] **Step 3:** `woche(zugang, tage, gerichte, …) -> Wochenwahl`: `_frage` mit `SCHEMA_WOCHE` (`{"tage": [{"tag": int, "gericht_id": int, "grund": str}]}`), Prüfung gegen Vorlage und offene Tage, Dublettenprüfung, `grund` auf 160 Zeichen gekürzt. Ohne offene Tage oder ohne Gerichte: kein Modellaufruf, leere Wahl.
- [x] **Step 4:** Tests: gültige Antwort; erfundene id → verworfen mit Grund, Tag bleibt leer; Tag nicht offen → verworfen; doppeltes Gericht → zweites verworfen; Verpackung (`{"plan": …}`, Codefence) toleriert; leere Vorlage fragt nicht.

### Task 2: `wochenplan.zug.Planer` — der Zug mit Span

**Files:**
- Create: `zettel/wochenplan/zug.py`
- Modify: `zettel/wochenplan/__init__.py`

- [x] **Step 1:** `Planer(chat)`; `planen(con, plan_id, *, vorwaermen=True) -> dict`. Ablauf: laden → `vorlage.gerichte(con, rahmen)` minus die an verworfenen Tagen abgelehnten Rezepte je Tag → offene Tage → Zustand der Box (`chat.zustand()`, wirft `ChatNichtVerfuegbar`) → `obs.chain("plan.woche", eingabe=…)` → `obs.stufe("plan.woche")` um `plan.woche()` → `speicher.tag_setzen` je gewählter Tag (mit `grund`) → für jedes neu gesetzte Rezept ohne `recipe_item` und ohne `recipe_zuordnung`: `chat.zuordnung_vorwaermen(con, rid)` (unter dem Span) → `einkaufsliste` für `rest` → Attribute → `span_id` am Plan.
- [x] **Step 2:** Attribute: `zettel.path = "plan"`, `zettel.plan_id`, `zettel.plan.days` (offene Tage), `zettel.plan.fixed`, `zettel.plan.presented`, `zettel.plan.assigned`, `zettel.plan.rejected`, `zettel.plan.rest`, `zettel.plan.lines`, `zettel.plan.prewarmed`; `output.value` = die Zuordnung Tag → Rezept.
- [x] **Step 3:** Rückgabe: `{belegt, verworfen, vorgelegt, offen, meldung}`; `PlanFehler` des Modells → Meldung, kein Absturz; Box schläft → `ChatNichtVerfuegbar` nach oben (die Route zeigt den Chat-Satz).
- [x] **Step 4:** Tests mit `FakeLLM` + `InMemorySpanExporter`: Tage werden belegt; `kept`-Tag bleibt und steht als festgelegt im Prompt; `removed`-Tag wird neu belegt, sein altes Rezept ist nicht in der Vorlage; Span heisst `plan.woche`, Kind CHAIN, `rejected` zählt die erfundene id; `span_id` steht am Plan; ohne Gerichte keine Modellfrage.

### Task 3: Labels aus der Nutzung

**Files:**
- Modify: `zettel/obs/labels.py` (`plan_annotationen`, `plan_schreiben`), `zettel/wochenplan/liste.py` (`in_den_korb` ruft `plan_schreiben`)
- Test: `tests/test_wochenplan_zug.py`

- [x] **Step 1:** Je entschiedener Tag eine Annotation `plan_day` (Label `kept`/`removed`, Erklärung „Mo: Lasagne — <grund>", identifier `zettel-plan-day-<tag_id>`); je Plan ein Score `plan_precision` = behaltene / entschiedene Tage (kein Score bei null entschiedenen), identifier `zettel-plan-<id>`. Nur wenn `plan.span_id` gesetzt ist.
- [x] **Step 2:** Geschrieben beim Übergeben in den Korb — der Moment, in dem die Entscheidungen feststehen (wie `orders.abschicken` für den Chat). Wirft nie.
- [x] **Step 3:** Tests: die Annotationen rechnen richtig; ohne Span-ID nichts; `offen` zählt nicht.

### Task 4: Oberfläche

**Files:**
- Modify: `zettel/web/app.py` (`POST /plan/{id}/planen`, `app.state.planer`, `create_app(planer=…)`), `zettel/web/templates/plan.html`, Texte
- Test: `tests/test_web_plan.py`

- [x] **Step 1:** Route: Planer laufen lassen, Meldung „n Tage belegt, m Vorschläge verworfen"; `ChatNichtVerfuegbar` → derselbe Satz wie im Chat (`_nicht_verfuegbar`); `PlanFehler` → Satz aus dem Zug.
- [x] **Step 2:** Der Knopf im Kopf: „Woche planen" / „Offene Tage neu planen"; Indikator während des Zugs.
- [x] **Step 3:** Tests: mit Fake-Chat werden die Tage belegt und die Meldung steht da; schlafende Box → Meldung, Plan unverändert.

### Task 5: Grün

- [x] `.venv/bin/python -m pytest -q -n 4` — alles grün, kein Netz.
