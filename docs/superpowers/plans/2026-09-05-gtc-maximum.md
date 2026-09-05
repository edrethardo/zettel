# GTC Berlin — die Note auf das Maximum: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jede der vier Jury-Noten (Innovation · NVIDIA/Partner-Tech · Nutzen · Doku/Präsentation) bis zum Post am 08.09.2026 um mindestens einen Punkt heben, ohne eine Zahl zu behaupten, die nicht gemessen ist.

**Architecture:** Kein Umbau der App. Drei Sorten Arbeit: (1) mehr Messung (128 Gerichte, zwei Modelle), (2) Erzählung nach vorn (Muster für Entwickler, Post-Kopf, Video-Pointe), (3) Sichtbarkeit beim Judge (Hugging-Face-Datensatz). Was nur Aaron kann — Gesicht, Stimme, Push, Post — steht als eigene Aufgabe mit allem, was er dafür braucht.

**Tech Stack:** `scripts/breite_probe.py` (Harness), Phoenix (Traces je Lauf in eigenem Projekt), vLLM-Box (Qwen jetzt, Nemotron über die Box-Session), `~/picknick-video/` (Schnitt, Endcard, Untertitel), Hugging Face Hub (Datensatz), `~/code/zettel-public` (gefilterter Klon zum Pushen).

**Frist:** Post bis Montag 08.09., Contest-Ende 10.09. Heute ist Samstag 05.09.

---

## Warum diese Aufgaben — die Wertung von heute (04./05.09.)

| Kriterium | heute | Hebel in diesem Plan |
|---|---|---|
| (a) Innovation | 7 | Muster als kopierbare Seite (T2), Pointe im Video (T4) |
| (b) NVIDIA/Partner | 7–8 | Nemotron auf 128 Gerichten (T1), Datensatz auf Hugging Face (T5) |
| (c) Nutzen | 6 | Entwickler-Nutzen vorne im Post und README (T3), PATTERN.md (T2) |
| (d) Doku/Präsentation | 8 | Gesicht und Stimme (T6), Push (T7), Zahlen-Sweep (T8) |

---

### Task 1: 128 Gerichte, zwei Modelle

**Files:**
- Modify: `scripts/breite_probe.py` (GERICHTE: 64 neue Einträge, dieselben elf Achsen)
- Create: `evals/breite_probe-2026-09-05-qwen-128.json` + `.provenienz.json`, `evals/breite_probe-2026-09-0X-nemotron35-128.json` + `.provenienz.json`
- Modify: `EVALS.md` (Abschnitt „128 Gerichte"), `SHOWCASE.md` (Zahlen), `docs/contest/POST.md`, `~/picknick-video/endcard.py`

- [x] **Step 1: 64 neue Gerichte eintragen** — je Achse im selben Verhältnis wie die erste Hälfte, jedes mit einem Kommentar, wofür es dasteht; acht davon mit wechselndem Zusatzartikel (Müllbeutel, Backpapier, Küchenrolle, Spülschwamm, Duschgel, Klopapier, Batterien, Kaffeefilter). Zweite Schreibweisen-Paare (Spaghetti/Spagetti Carbonara), zweite Kontrolle bei den Mehrdeutigen („Curry als ganzer Satz").
- [x] **Step 2: Liste prüfen** — `.venv/bin/python scripts/breite_probe.py --achsen` zeigt 128 Gerichte, keine Dublette (`GERICHTE`-Namen eindeutig).
- [x] **Step 3: Qwen-Lauf (Box steht auf Qwen)** — DB-Kopie frisch aus `data/picknick.db`, dann
  `ZETTEL_PHOENIX_PROJECT="Zettel Eval Qwen 128" .venv/bin/python scripts/breite_probe.py --messen --db <Kopie> --json evals/breite_probe-2026-09-05-qwen-128.json --trace`. Erwartung: 128 gelaufen, 0 Fehler, ~5 min (Phase A holt 64 neue Rezepte bei Chefkoch mit 1,5 s Pause).
- [x] **Step 4: Nemotron-Lauf** — Fenster bei der Box-Session anfragen („Nemotron wie am 05.09., Wächter aus, ~20 min"), dann dasselbe Kommando mit `ZETTEL_PHOENIX_PROJECT="Zettel Eval Nemotron 3.5 128"`; danach „Messung abgeschlossen" melden.
- [x] **Step 5: Aus den Spans nachrechnen** (wie bei Lauf 1–3: `attributes.zettel`/`picknick`, `terms`/`products` je `chat.turn`), Tabelle 64 vs 128 je Modell — steigt oder fällt die Quote mit der Breite? Die zehn schlechtesten neu benennen.
- [x] **Step 6: Provenienz-Dateien** wie `evals/breite_probe-2026-09-05-nemotron35.provenienz.json` (Stack, Endpunkt `vllm-box.local`, Modell, Kontext, Commit, Phoenix-Projekt, guided = response_format).
- [x] **Step 7: EVALS.md** — neuer Abschnitt „128 Gerichte (05.09.)": Kommando, Tabelle beider Modelle, was sich gegenüber 64 änderte, „Was diese Messung NICHT sagt" (ein Lauf, keine Wiederholung). SHOWCASE: Leitsatz und Tabelle auf 128 („evaluated across 128 dishes"), die 64er-Läufe bleiben als Historie benannt. POST, CASE-STUDY, Endcard: Zahl nachziehen.
- [x] **Step 8: Privatscan und Commit** — `.venv/bin/python -m pytest -q tests/test_betrieb.py`, dann Commit je Lauf.

### Task 2: PATTERN.md — das Muster für Entwickler

**Files:**
- Create: `PATTERN.md` (Englisch, ~1 Seite)
- Modify: `README.md` (Dokumententabelle + englischer Kopf), `SHOWCASE.md` (Link in „The guarantees")

- [x] **Step 1: Drei Codestellen herausschneiden**, je ≤ 15 Zeilen, wörtlich aus dem Repo mit `file:line`: (1) die Kandidatenprüfung in `zettel/assistant/plan.py` (`Auswahl.verworfen`, ID nicht vorgelegt → verworfen, gezählt), (2) das `zettel.rejected`-Attribut in `zettel/assistant/chat.py` (`_span_abschluss`), (3) die Labels beim Abschicken in `zettel/obs/` (`kept`/`removed`/`correction`).
- [x] **Step 2: Seite schreiben** — Titel „Accountable choice: let the model pick only from what you retrieved". Struktur: das Problem (ein Satz), die drei Regeln, die drei Codestellen, was es messbar macht (Zahlen aus EVALS mit Link), „applies to any agent that selects from a database: products, tickets, documents". Kein Marketing, jede Zahl mit Quelle.
- [x] **Step 3: Verlinken** — README-Tabelle (eine Zeile), README-Englischkopf (Halbsatz), SHOWCASE unter „The guarantees" („the pattern, extracted: PATTERN.md").
- [x] **Step 4: Privatscan, Commit.**

### Task 3: Post und README-Kopf auf den Entwickler-Nutzen

**Files:**
- Modify: `docs/contest/POST.md`, `README.md` (erste 15 Zeilen)

- [x] **Step 1: Erste Zeile des Posts** — sie ist alles, was der Feed zeigt. Muster: „NVIDIA's Nemotron 3.5 Lightning on one RTX 3090 runs our household's grocery agent: 85 % of ingredients found across 128 dishes, 6 s per dish — and it may only pick from what the shop retrieved." (Zahlen nach Task 1 einsetzen.)
- [x] **Step 2: Absatz „what you can take from it"** — das Muster in drei Sätzen, Link auf PATTERN.md, ein Halbsatz zur Arena (`llm-eval-phoenix`: „same discipline, judge checked against real test runs").
- [x] **Step 3: README-Kopf** — der englische Absatz beginnt mit dem Muster und dem Nemotron-Satz, nicht mit „private grocery-ordering shop".
- [x] **Step 4: Kurze Fassung des Posts angleichen; Checkliste im Kopf der Datei abhaken, was erledigt ist.**

### Task 4: Die Pointe des Videos

**Files:**
- Modify: `~/picknick-video/schnitt.py` (UNTERTITEL 7), `~/picknick-video/endcard.py`
- Regenerate: `~/picknick-video/zettel_demo_nemotron_final.mp4`

- [x] **Step 1: Untertitel Shot 7** — „Every turn is one trace. The model may only pick from retrieved documents — invented IDs are rejected and counted: 2 of 581 across 64 dishes, 0 shipped." (Zahl aus Task 1 auf 128 nachziehen, sobald da.)
- [x] **Step 2: Endcard-Zahlenzeile** — „Nemotron 3.5 on one RTX 3090: 85 % of ingredients found, 128 dishes, 6 s per dish · 1,362 tests · 79 checks".
- [x] **Step 3: Neu rendern** — `python3 endcard.py github.com/edrethardo/zettel`, dann der Concat-Aufruf aus VIDEO.md „Stand 05.09."; Einzelbild bei Shot 7 und Endcard prüfen.

### Task 5: Der Datensatz auf Hugging Face

**Files:**
- Create: `evals/hf/zettel-128-dishes/README.md` (Dataset-Card), `evals/hf/zettel-128-dishes/dishes.csv`, `results.csv`
- Create: `scripts/hf_datensatz.py` (baut CSVs aus den `evals/*.json`)

- [x] **Step 1: `scripts/hf_datensatz.py`** — liest die Roh-JSONs der Läufe, schreibt `dishes.csv` (Name, Achse, Satz, Zusatzartikel) und `results.csv` (Lauf, Modell, Gericht, Begriffe, Katalogtreffer, Freitext, Dauer, rejected, Phoenix-Projekt). Test: `tests/test_hf_datensatz.py` gegen einen Mini-JSON (drei Gerichte) — Spaltennamen und Zeilenzahl.
- [x] **Step 2: Dataset-Card** — Titel, was gemessen wurde, wie (Link auf EVALS/GETTING-STARTED), Lizenz MIT, `language: de`, `task_categories: other`, Tabelle der Modelle mit Mittel/Median, „limitations" (ein Lauf, Katalog eines Händlers, deutsch).
- [ ] **Step 3: Aaron lädt hoch** — `hf upload edrethardo/zettel-128-dishes evals/hf/zettel-128-dishes --repo-type dataset`; Link in SHOWCASE und Post.

### Task 6: Gesicht und Stimme (nur Aaron)

- [ ] **Step 1: Shot 0** — Telefon auf Augenhöhe, ein Satz: „I built a grocery agent for my girlfriend and me — it runs on one RTX 3090 in my living room." Querformat, 6 s, `~/picknick-video/shot0.mp4`.
- [ ] **Step 2: Voice-over** — das Skript in `docs/contest/VIDEO.md` („VO-Skript am Stück") am Stück ablesen, ruhiges Tempo, `~/picknick-video/vo.m4a`; Zeitmarken kommen aus `schnitt.py` (Ausgabe „Zeitmarken fürs Voice-Over").
- [ ] **Step 3: Schnitt (ich)** — Shot 0 ersetzt die 5 s Schwarz, VO als Tonspur mit −16 LUFS, Untertitel bleiben; Ergebnis `zettel_demo_final.mp4`.

### Task 7: Veröffentlichen

- [x] **Step 1: Klon frisch bauen** (nach dem letzten Commit): `git clone --no-local . <tmp> && git-filter-repo --replace-text ersetzungen.txt --force`, Gegenprobe `git log -S` auf Hostname/IP/Home-Pfad = 0, Privatscan grün, nach `~/code/zettel-public`.
- [ ] **Step 2: Push (Aaron)** — `git remote add origin git@github.com:edrethardo/zettel.git && git push -u origin master`; danach die drei Links prüfen: README, SHOWCASE, Endcard-Adresse.
- [ ] **Step 3: Post (Aaron, Montag früh)** — Video nativ hochladen, Hochkant-Clip als ersten Kommentar, Merve Noyan über den Tag-Vorschlag, `#NVIDIAGTC`, erste Stunde antworten.

### Task 8: Zahlen-Sweep

- [x] **Step 1:** `grep -n "64 dishes\|64 Gerichte\|1,3[0-9][0-9] tests\|79 checks" README.md SHOWCASE.md CASE-STUDY.md EVALS.md PATTERN.md GETTING-STARTED.md docs/contest/*.md` — jede Zahl entweder als Historie datiert oder auf den neuen Stand.
- [x] **Step 2:** `pytest -q -n auto` und `checks/smoke.py` — die Zahlen in SHOWCASE/GETTING-STARTED/Endcard sind die aus diesem Lauf.
- [x] **Step 3:** Memory und `docs/contest/VIDEO.md` auf den Endstand; Commit.

## Reihenfolge und Zeit

| Wann | Was |
|---|---|
| Sa 05.09. | T1 Step 1–3 (Qwen-Lauf), T2, T4, T5 Step 1–2 |
| So 06.09. | T1 Step 4 (Nemotron-Fenster), Step 5–8; T3; T6 (Aaron) |
| Mo 07.09. | T7 Step 1, Push, Sweep T8 |
| Mo 08.09. | Post |
