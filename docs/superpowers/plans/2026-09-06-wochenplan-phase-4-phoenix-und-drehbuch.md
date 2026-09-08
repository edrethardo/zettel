# Wochenplan, Phase 4 — Messung mit Phoenix und das Drehbuch: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Planer läuft gegen die echte Box, seine Züge stehen als Traces in einem eigenen Phoenix-Projekt, die Zahlen stehen in `EVALS.md` — und das Drehbuch hat einen Shot, in dem der Mehrwert sichtbar wird: eine Woche, ein Knopf, eine Einkaufsliste, die den Bestand abzieht, und der Trace daneben mit `zettel.plan.rejected`.

**Architecture:** `scripts/plan_probe.py` ist der Harness nach dem Muster von `breite_probe.py`: Arbeitskopie der Datenbank, je Szenario ein Plan, `Planer.planen` gegen die Box (`--trace` in `ZETTEL_PHOENIX_PROJECT`), danach „Ja auf alle Tage", `in_den_korb`, Labels. Die Probe prüft die Spans anschliessend gegen Phoenix (`plan.woche`-Chain mit den `zettel.plan.*`-Attributen, LLM-Kind darunter). Ergebnis als JSON + Provenienz in `evals/`, Abschnitt in `EVALS.md`. Das Drehbuch bekommt in `docs/contest/VIDEO.md` einen Abschnitt „v3 — der Wochenplan" mit Take-Ablauf, Untertiteln und Voice-Over; `~/picknick-video/dreh_plan.py` fährt den Take.

**Tech Stack:** wie gehabt; Phoenix auf `localhost:6006`, Box `vllm-box.local:8000`, `wake-vllm`.

---

### Task 1: `scripts/plan_probe.py`

- [x] **Step 1:** Szenarien (deterministisch, im Skript): (A) 3 Tage, 2 Personen, ≤ 40 min, Bestand „500 g Kartoffeln, 6 Eier"; (B) 5 Tage, 4 Personen, Budget 60 €; (C) 4 Tage, 2 Personen, Bestand „Nudeln, 200 g Parmesan", Tag 2 auswärts; (D) wie A, dann Tag 1 „Nein" → Neuplanung; (E) 7 Tage, 2 Personen, ≤ 30 min (die Vorlage wird knapp — misst, ob das Modell rät oder weglässt).
- [x] **Step 2:** Je Szenario: Arbeitskopie (`VACUUM INTO`), Plan anlegen, `Planer.planen` (Zeit messen), Tage lesen, „Ja" auf alle belegten, `in_den_korb`, Zahlen: presented/assigned/rejected/rest/lines/covered/price/prewarmed, Dauer, Modell.
- [x] **Step 3:** `--trace`: `obs.einrichten()`, danach `obs.flush()`; Prüfung gegen Phoenix wie `trace_probe.py`: für jeden `plan.woche`-Span `input.value`, `output.value`, `zettel.plan.presented`, `zettel.plan.rejected` nicht leer; ein LLM-Span mit `llm.model_name` darunter.
- [x] **Step 4:** JSON nach `evals/plan_probe-<datum>-<modell>.json` + `.provenienz.json`.

### Task 2: Laufen lassen und aufschreiben

- [x] **Step 1:** Box wecken (`wake-vllm`), Modell erfragen.
- [x] **Step 2:** `ZETTEL_PHOENIX_PROJECT="Zettel Eval Wochenplan" .venv/bin/python scripts/plan_probe.py --db <Kopie> --trace --json evals/…`.
- [x] **Step 3:** `EVALS.md`: Abschnitt „Der Wochenplaner (2026-09-06)" — Kommando, Tabelle je Szenario, was die Messung NICHT sagt (ein Lauf, ein Modell, 19 Gerichte in der Vorlage).

### Task 3: Drehbuch

- [x] **Step 1:** `docs/contest/VIDEO.md`: neuer Kopfabschnitt „Stand 06.09. — v3, der Wochenplan": warum der Shot den Mehrwert trägt (aus „ein Satz -> eine Liste" wird „eine Woche -> eine Liste, abzüglich dessen, was da ist"), Prep (Demo-DB mit ~10 geholten Gerichten und vorgewärmter Zuordnung), Take-Ablauf (Realzeit-Tabelle), Shots 8a–8d mit Untertitel und VO, Endcard-Zeile.
- [x] *(geschrieben, kein Trockenlauf — die Bühne stand nicht)* **Step 2:** `~/picknick-video/dreh_plan.py`: Choreografie nach dem Muster von `dreh.py` (Marken, `zustand()` aus der Datenbank, Formen statt Koordinaten): Formular ausfüllen, „Woche planen", Wartefenster mit Blick nach Phoenix, Tage lesen, ein „Nein" + „Offene Tage neu planen", Bestand „Was sagt der Bon?" bestätigen, Einkaufsliste in den Korb, Trace `plan.woche` öffnen.
- [x] *(als eigenes `schnitt_plan.py`, `schnitt.py` bleibt unberührt)* **Step 3:** `~/picknick-video/schnitt.py`: Untertitel für die Plan-Shots als eigener Block, Marken benannt.

### Task 4: Doku und Abschluss

- [x] `2026-09-06-wochenplan-design.md` Abschnitt 9: alle vier Stücke gebaut, mit Verweis auf die Pläne; Abschnitt 5: kcal/Protein bleibt draussen bis jemand die Summe je Tag baut (die Daten liegen jetzt).
- [x] README/SHOWCASE: ein Absatz zum Wochenplan.
- [x] `.venv/bin/python -m pytest -q -n 4` grün.
