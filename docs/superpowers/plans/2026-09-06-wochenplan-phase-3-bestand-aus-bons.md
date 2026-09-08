# Wochenplan, Phase 3 — der Bon schlägt vor, der Mensch bestätigt: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Weg (c) aus Abschnitt 3 des Entwurfs — der Zubringer zum erklärten Bestand: „Dienstag 1 kg Kartoffeln gekauft, noch da?" Aus den bestätigten Käufen der letzten Tage (`receipt_item.decision = kept`) werden Bestandszeilen mit `herkunft = aus_bon` und `decision = offen` vorgeschlagen; sie gelten erst nach einem Ja und nur für diesen Plan.

**Architecture:** Ein Modul `zettel/wochenplan/bestand.py` ohne Modell: Käufe der letzten `TAGE_ZURUECK` Tage, deren Produkt eine Zeile der Einkaufsliste trifft (Produkt-id, sonst Name über `herkunft.punkte`), als `plan_bestand`-Zeilen mit Menge aus `qty × Packungsgrösse` (`mengen.packungsgroesse`). Aufgerufen am Ende des Zugs (`Planer.planen`, nach dem Belegen — vorher gibt es keine Liste, gegen die man prüfen könnte) und über einen Knopf. Idempotent über `receipt_item_id`.

**Tech Stack:** wie Phase 1/2. Kein Netz, kein Modell.

---

## Entscheidungen

**Nur, was der Plan braucht.** Der Bon der letzten Woche hat 30 Zeilen; wer alle vorgelegt bekommt, tippt 30-mal. Vorgeschlagen wird, was eine Zeile der Einkaufsliste trifft — das ist die Frage, die der Plan wirklich hat.

**Die Menge ist die gekaufte, nicht die übrige.** „1 kg gekauft" ist eine Tatsache vom Bon; wie viel davon noch da ist, weiss nur der Mensch. Die Zeile zeigt die gekaufte Menge und wartet auf ein Ja; wer weniger hat, sagt Nein und schreibt es als erklärten Bestand hin.

**Sieben Tage zurück.** Länger ist ein Kauf kein Hinweis mehr, sondern eine Vermutung — und Vermutungen sind das, was Abschnitt 3 ausschliesst.

---

### Task 1: `wochenplan.bestand`

**Files:**
- Create: `zettel/wochenplan/bestand.py`
- Modify: `zettel/wochenplan/speicher.py` (`bestand()` liefert `gekauft_am`), `zettel/wochenplan/__init__.py`
- Test: `tests/test_wochenplan_bestand.py`

- [x] **Step 1:** `kaeufe_der_letzten_tage(con, bis, tage=TAGE_ZURUECK)` — `kept`-Zeilen mit Produkt, Datum, Menge (`qty`), Packung.
- [x] **Step 2:** `passende(kaeufe, zeilen)` — Treffer über `product_id`, sonst `herkunft.punkte(produktname, zeile.name/zutat) >= SCHWELLE`.
- [x] **Step 3:** `vorschlagen(con, plan_id) -> int`: Einkaufsliste rechnen, passende Käufe als `plan_bestand` (`aus_bon`, `offen`, `receipt_item_id`, Menge = `qty × Packungsgrösse` in Grundeinheit, sonst `qty` Stück) anlegen; schon vorgeschlagene (`receipt_item_id` am Plan) überspringen. Gibt die Zahl der neuen Zeilen zurück.
- [x] **Step 4:** Tests: ein bestätigter Kauf, der eine Zeile trifft, wird vorgeschlagen (Menge 1000 g aus „1 kg", Datum dabei); ein Kauf ohne Bezug nicht; `removed`/`offen`-Käufe nicht; älter als 7 Tage nicht; zweimal aufrufen ergibt keine Dublette; nach dem Ja zieht die Liste ab.

### Task 2: In den Zug und auf die Seite

**Files:**
- Modify: `zettel/wochenplan/zug.py` (nach dem Belegen `bestand.vorschlagen`, Attribut `zettel.plan.stock_suggested`), `zettel/web/app.py` (`POST /plan/{id}/bestand/aus_bons`), `_plan_inhalt.html` (Knopf „Vom Bon vorschlagen", Datum an der Zeile), Texte
- Test: `tests/test_web_plan.py`, `tests/test_wochenplan_zug.py`

- [x] **Step 1:** Zug: nach `tag_setzen` und Vorwärmen `bestand.vorschlagen`; Bericht trägt `bestand_vorgeschlagen`; Meldung nennt die Zahl.
- [x] **Step 2:** Route + Knopf; die Zeile zeigt „vom Bon 02.09." und Ja/Nein.
- [x] **Step 3:** Tests: der Zug schlägt vor; der Knopf schlägt vor; Ja deckt die Zeile in der Liste.

### Task 3: Grün

- [x] `.venv/bin/python -m pytest -q -n 4`.
