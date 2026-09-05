# GTC Berlin Golden Ticket — Politur und Nemotron-Messung (Design)

**Datum:** 2026-09-02 · **Frist:** 10.09.2026 (Post bis 08.09.) · **Weg B**, vom
Nutzer gewählt aus drei Vorschlägen (A Politur · B Politur + Nemotron · C dazu
Gesicht und Stimme). Baut auf `2026-08-29-gtc-auftritt-design.md` auf; dessen
Stück 4 („Nemotron messen statt tunen", damals optional) wird Kern.

## Was der Contest verlangt — aus den Originalquellen

Regeln: `developer.download.nvidia.com/licenses/gtc-berlin-golden-ticket-official-rules-for-2026.pdf`,
Seite: `developer.nvidia.com/gtc-golden-ticket-contest`.

* **Aufgabe:** eine *open-source application built with open models* auf
  LinkedIn/X/Instagram teilen, `#NVIDIAGTC`, **den Judge taggen, von dem man
  vom Contest erfahren hat** (acht Judges, u. a. Chorouk Malmoum/AgentX Academy — sie ist es —, Merve Noyan/Hugging Face,
  Johnny Nunez und Asier Arranz/NVIDIA). Kein Modell vorgeschrieben, keine
  Videolänge.
* **Frist:** 18.08.–10.09.2026, sechs Gewinner, Bekanntgabe ~14.09.
* **Kriterien, gleich gewichtet, je 1–10:** (a) Technical innovation ·
  (b) Effective use of NVIDIA and/or partner technology · (c) Potential
  impact or usefulness · (d) Quality of documentation and presentation.

## Befund am 02.09.

| Kriterium | Stand | Hebel |
|---|---|---|
| (a) | stark: Wahl nur aus Gefundenem, erfundene IDs gezählt, vier LLM-Aufgaben ohne LLM, Labels aus der Nutzung | erzählen |
| (b) | **Lücke**: RTX 3090 + vLLM + Qwen, kein NVIDIA-Modell | Nemotron 3.5 Lightning auf demselben 64-Gerichte-Harness |
| (c) | echtes Produkt, wirkt aber klein | als Muster erzählen |
| (d) | alle 10 Bilder vom 29.08. zeigen die alte UI; Zahlen veraltet (1.150/76 → 1.346/79; Kontext 106k → 65.536); ein roter Smoke-Check; Video ohne Endcard/Stimme; **Repo ohne Remote** | alles reparierbar |

Die Box (`/version`, 02.09.): **vLLM 0.27.1**, `Qwen3.8-27B-Instruct`,
`max_model_len 65536`. `useful-quants/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-W4A16`
(16,6 GiB, compressed-tensors) ist auf genau dieser Kombination validiert.

## Stücke

**1. Nemotron durch den Harness.** Box auf den W4A16-Build
(`--quantization compressed-tensors --max-num-seqs 32 --reasoning-parser nemotron_v3`),
Qwen-Konfiguration wiederherstellbar. Dann
`breite_probe.py --messen --db kopie.db --json roh_nemotron.json --trace` mit
`ZETTEL_PHOENIX_PROJECT="Zettel Eval Nemotron"`. Ergebnis als „Lauf 2" in
`EVALS.md` (WB-393) in derselben Tabelle wie Lauf 1; ausdrücklich prüfen, ob
Guided JSON trägt. **Ein schlechteres Ergebnis steht genauso drin.**

**2. Bilder mit der neuen UI.** `scripts/dreh/shot.py` neu (hell + dunkel,
390 px, 2×), Zustand über `stage.py`; die drei GIFs aus den Takes vom 02.09.
(Rezeptkarte · Portionen 3→6 · Pick-Liste), je < 5 MB. Alle zehn Dateien in
`docs/images/` ersetzen; Privatscan-Test läuft, Pixel werden angesehen.

**3. Zahlen und Texte.** SHOWCASE, CASE-STUDY, POST, Endcard-Text: Tests,
Smoke-Gate grün (der rote Check prüfte einen Satz, der seit 01.09. nicht mehr
im leeren Chat steht — der Check wird angeglichen, nicht das Blatt), Kontext
65.536, Nemotron-Zeile. POST.md lang und kurz, mit Judge-Tag.

**4. Video.** Endcard (5 s Standbild) hinter Shot 7 der langen Fassung;
Untertitel eingebrannt (LinkedIn spielt stumm). Hochkant-Clip bleibt.
Shot 0 und Voice-over bleiben offen (Weg C), Drehbuch liegt bereit.

**5. Repo veröffentlichungsfähig.** LICENSE, `.gitignore`, Privatscan,
englischer Einstieg im README mit dem Stack im ersten Absatz.
**Push und Post macht der Nutzer.**

## Reihenfolge

1 sobald die Box umgestellt ist; 2, 3, 5 parallel dazu; 4 zuletzt mit den
finalen Zahlen. Ziel: 05.09. fertig, 08.09. Post.

## Was nicht passieren darf

Keine Zahl ohne Messung. Keine Haushaltsdaten in Pixeln. Kein Passwort und
kein Hostname in einer Datei des Repos. Nichts wird gepostet oder gepusht.
Tests und Checks bleiben grün — die Smoke-Änderung ist eine Angleichung an
das Blatt, keine Lockerung.
