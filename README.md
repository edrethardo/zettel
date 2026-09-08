# Zettel

> **English** — Zettel (German for the slip of paper you take to the shop) is
> a grocery agent for a multi-person household that is allowed to do exactly
> one thing: **choose from the products the shop retrieved** — never invent.
> An invented product id is rejected and counted; every Yes/No the household
> taps becomes an eval label; every turn is one trace in Arize Phoenix. No
> cloud, no API keys: one open model on one NVIDIA RTX 3090.

![One sentence becomes a recipe card with computed pack counts](docs/images/chat-recipe-card.gif)

| 128 German dishes, one RTX 3090 (2026-09-05) | ingredients found in the catalog | per dish | seconds per dish |
|---|---|---|---|
| **NVIDIA Nemotron 3.5 Lightning 30B-A3B** (W4A16) | 85 % | 87 % / 89 % | **6** |
| Qwen3.8-27B-Instruct (AWQ 4-bit), the reference | **87 %** | 89 % / 91 % | 20 |
| Llama-3.1-Nemotron-Nano-8B (64 dishes, 2026-08-30) | 11 % | 25 % / 0 % | 21 |

**English tour: [`SHOWCASE.md`](SHOWCASE.md) · the copyable pattern: [`PATTERN.md`](PATTERN.md) · run it: [`GETTING-STARTED.md`](GETTING-STARTED.md) · the numbers: [`EVALS.md`](EVALS.md) · two debugging stories: [`CASE-STUDY.md`](CASE-STUDY.md) · contribute: [`CONTRIBUTING.md`](CONTRIBUTING.md).** The rest of this README is in German, the household's language.

> **Using this outside Germany.** The interface speaks German and English —
> switch it under *More → Language*; screen text lives in
> `zettel/web/texte/*.json` and a partial translation is welcome, because
> anything missing falls back to German. The catalogue source is one German
> shop, and everything above it does not care where products came from:
> `product` carries a `source` column. Adding your own supermarket is one
> crawler and one parser, and the method — how to check `robots.txt` first,
> how to find the endpoint the shop's own front-end uses, and the five guards
> that keep a bad night from erasing your catalogue — is written down in
> [`.claude/skills/grocery-catalog-source/`](.claude/skills/grocery-catalog-source/SKILL.md).
> The code itself stays German; `CONTRIBUTING.md` explains why, and why that
> does not have to stop you.

Ein privater Bestell-Shop für einen Mehrpersonenhaushalt im Tailnet. Eine Person legt Lebensmittel in einen Warenkorb und schickt die
Bestellung ab, eine zweite kauft sie physisch im Laden ein und hakt sie
dort auf dem Handy ab. **Es wird nie eine
Bestellung an einen echten Händler geschickt.** Dazu ein Chat-Feld: freier Text
(„alles für Spaghetti Bolognese, und Klopapier") wird auf echte
Katalogprodukte abgebildet und als Vorschlag vorgelegt.

Zweiter, gleichrangiger Zweck: Der Chat-Agent ist in Arize Phoenix vollständig
beobachtbar, bewertbar und reproduzierbar vergleichbar.

Seit dem 06.09. eine Ebene darüber: der **Wochenplan** (unter „Mehr"). Vier
Zahlen und ein Satz („500 g Kartoffeln, 6 Eier, Nudeln"), und das Modell
belegt die Tage — nur mit Gerichten, die der Haushalt schon hat; erfundene
werden verworfen und gezählt. Jede Zahl am Plan rechnet der Code, der
Bestand ist ein erklärter Rahmen für diesen Plan und kein Lagerstand, und
der Bon von gestern darf vorschlagen, aber nicht entscheiden. Entwurf:
`docs/superpowers/specs/2026-09-06-wochenplan-design.md`, Messung in
[`EVALS.md`](EVALS.md).

## Wozu die Observability gut ist — in einem Fall

Auf „…dazu brauche ich noch Zahnpasta und Butter" wählte das Modell eine
**ButterBoyz-Spezialbutter für 4,69 €**. Ein Fehlgriff. Die naheliegende
Erklärung ist ein schlechtes Modell; der Trace sagt, dass sie falsch ist:

```
„Butter“ — 5 Kandidaten vorgelegt        zettel.rejected = 0
   4,01  #1771  ButterBoyz BIO Butter Chili & Röstzwiebel
   4,01  #1772  ButterBoyz BIO Butter Feige & Anis
   3,96  #1757  ButterBoyz BIO Kräuterbutter
   3,96  #1766  ButterBoyz BIO Salzbutter      ← gewählt
   3,96  #1768  ButterBoyz BIO Steinpilzbutter
„Zahnpasta“ — 0 Kandidaten vorgelegt
```

`rejected = 0` heisst: das Modell hat nichts erfunden, es hat aus der Liste
gewählt — und in der Liste stand keine normale Butter. **Der Fehlgriff gehört
dem Retrieval, nicht dem Modell.** Genau deshalb ist `catalog.search` ein
`RETRIEVER`-Span und kein `TOOL`. Der ganze Vertrag steht in
[`OBSERVABILITY.md`](OBSERVABILITY.md).

## Die Dokumente

| Datei | worum es geht |
|---|---|
| [`PATTERN.md`](PATTERN.md) | Englisch: das übertragbare Muster — nur aus Gefundenem wählen, Erfundenes verwerfen und zählen, Entscheidungen als Labels — mit den drei Codestellen |
| [`ANLEITUNG.md`](ANLEITUNG.md) | **die Bedienungsanleitung** für alle im Haushalt, die damit einkaufen: Reiter, Chat, Korb, Pick-Liste, Rezepte, Bons |
| [`GETTING-STARTED.md`](GETTING-STARTED.md) | Englisch: installieren, konfigurieren, der erste Zug, prüfen, ein Modell messen |
| [`DESIGN.md`](DESIGN.md) | Architektur und die Entscheidungen, die von aussen wie ein Versehen aussehen |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | **der Span-Vertrag**: welcher Span, welche Attribute, was sie bedeuten |
| [`EVALS.md`](EVALS.md) | Dataset, Evaluatoren, die vier Varianten und die echten Zahlen |
| [`DEMO.md`](DEMO.md) | der Klickpfad zum Vorführen, eine Prüfung pro Klick |
| [`GATES.md`](GATES.md) | was das Gate abdeckt — und was ausdrücklich nicht |
| [`LEHREN.md`](LEHREN.md) | was das Projekt gekostet hat und was davon woanders gilt |

## Schnellstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m zettel.scrapers.nachtlauf --begriff milch   # etwas Katalog
.venv/bin/python -m zettel.web.app
```

Der Shop lauscht dann auf `http://127.0.0.1:8730` — und, wenn die Maschine
im Tailnet ist, zusätzlich auf `http://<deine Tailnet-Adresse>:8730`. Die
Adresse steht nicht im Code: sie wird beim Start von der Maschine erfragt
(eine Adresse aus `100.64.0.0/10`, siehe `eigene_tailnet_adresse()`), und
`ZETTEL_HOST` überschreibt die Wahl. Auf `0.0.0.0` bindet er nie — der
Versuch bricht mit einer Fehlermeldung ab, weil es kein Passwort gibt und das
Tailnet der einzige Schutz ist.

Für den Chat braucht es ein lokales vLLM (Vorgabe `http://localhost:8000/v1`;
eine Box woanders im Netz trägt man in `ZETTEL_LLM_ENDPOINT` ein — in der
Umgebung oder in einer gitignorten `zettel.env` im Projektverzeichnis).
Ohne Modell läuft alles ausser dem Chat weiter, und der Rezeptweg sogar
auch. Für Traces und Evals ein Phoenix auf `localhost:6006` — ohne läuft der Shop unverändert, siehe
[`OBSERVABILITY.md`](OBSERVABILITY.md).

## Prüfen

```bash
.venv/bin/python checks/smoke.py     # das Gate: 79 Checks, exit 0 / 1
.venv/bin/python -m pytest -q        # 1.364 Tests, rund 45 s
.venv/bin/python -m pytest -q -n auto   # dieselben Tests auf allen Kernen, rund 20 s
```

Beides ohne Netz, ohne Modell, ohne Phoenix — und im Fall des Gates ist das
nicht zugesichert, sondern **erzwungen**: `checks/smoke.py` sperrt vor dem
ersten Projektimport `connect`, `bind` und `getaddrinfo` im eigenen Prozess
und prüft als Erstes, dass eine Verbindung nach `localhost:6006` scheitert.
Was das abdeckt und was ausdrücklich nicht, steht in
[`GATES.md`](GATES.md).

## Der Katalog: was er ist und was er nicht ist

**Die Preise sind Knuspr-Preise, nicht Rewe- oder Lidl-Preise.** Eingekauft
wird bei Rewe und Lidl; der Katalog stammt von knuspr.de, weil es die einzige
Quelle mit einer benutzbaren Suche war. Die Preise sind damit Richtwerte für
die Planung, nicht die Summe an der Kasse. Und **Handelsmarken wie `ja!` oder
`Milbona` fehlen** — genau die also, nach denen im Laden am ehesten gegriffen
wird. Der Satz steht auch in der Oberfläche unter jeder Kachelliste, nicht nur
hier.

Zwei weitere Dinge, die man kennen muss, bevor man dem Katalog etwas anlastet:

* **Die Suche kennt nur Wortanfänge.** „milch" findet „Landmilch" nicht über
  den Namen (nur über die Kategorie), „Klopapier" findet nie
  „Toilettenpapier" — dort ist das Umformulieren ausdrücklich die Aufgabe des
  Modells.
* **Der Katalog ist so breit wie die Begriffsliste** in
  `zettel/scrapers/begriffe.py` — Knuspr hat keinen Endpunkt für den ganzen
  Katalog, nur die Suche. Der Vollcrawl über alle 170 Begriffe ist am
  2026-08-28 gelaufen: **36 min 57 s, 10.361 Produkte**, danach liefern noch
  **4 von 170 Begriffen** keinen Treffer (`sojasosse`, `paprikapulver`,
  `tiefkuehlpizza`, `tiefkuehlgemuese` — zusammengeschriebene Wörter, die
  Knuspr getrennt führt).

  Davor stammte der Katalog aus einem 12-Begriffe-Lauf mit 2.498 Produkten,
  und 59 Begriffe waren leer. Wer ältere Beispiele in diesem Projekt liest,
  sollte das wissen: „Zahnpasta liefert nichts" war eine **Crawl-Lücke, kein
  fehlendes Sortiment** — heute findet die Suche `meridol ZAHNPASTA`.

## Rezepte von Chefkoch — was erlaubt ist und was ich nicht gelesen habe

Nennt ein Chat-Satz ein **Gericht**, kommen die Zutaten aus einem
echten Rezept statt aus dem Gedächtnis des Modells. Der Anlass ist gemessen:
bei „alles für Pho" zählte das Modell zwanzig Rindfleischteile auf, von denen
**keiner** im Katalog stand — es weiss nicht, was Pho ist, und merkt es nicht.
Chefkoch kennt 85 Pho-Rezepte; das bestbewertete liefert 23 Zutaten.

Benutzt werden genau zwei Endpunkte einer offenen JSON-API, ohne Schlüssel:

```
GET https://api.chefkoch.de/v2/recipes?query=<gericht>&limit=12   # Metadaten
GET https://api.chefkoch.de/v2/recipes/<id>                       # Zutaten
```

**`robots.txt` erlaubt beide.** `api.chefkoch.de/robots.txt` sperrt
ausschliesslich `/v2/search/suggestions/` und die Kommentare zweier einzelner
Rezepte.

**`robots.txt` ist nicht dasselbe wie eine Erlaubnis.** Wer dieses Projekt
weitergibt oder öffentlich betreibt, muss die Nutzungsbedingungen selbst
prüfen und die Abwägung neu treffen. Entsprechend höflich fragt der Abruf:

* **zwei Anfragen je Gericht, dann nie wieder** — das Ergebnis wird in `dish`
  zwischengespeichert (90 Tage; „kennt Chefkoch nicht" sieben Tage, eine
  Störung eine Stunde). Ein Gericht wird nicht bei jedem Chat-Zug neu geholt.
* **eine dritte, wenn ein Mensch ein anderes Rezept wählt**. Die
  Suche liefert zwölf Rezepte in einer Antwort; sie werden seither
  mitgespeichert (`dish_treffer`) und zur Wahl gestellt. **Gesucht wird
  dafür nicht noch einmal** — geholt wird allein das Detail des gewählten
  Rezepts, und ein schon geholtes kostet gar keine Anfrage.
* **1,5 s Pause** zwischen zwei Anfragen im Lauf von Hand (`chefkoch.PAUSE_S`).
  Im Chat-Request entfällt sie: dort fallen genau zwei Anfragen an, einmal im
  Leben dieses Gerichts, und ein Mensch wartet darauf.
* **ein ehrlicher User-Agent**, der das Projekt benennt.
* **die Herkunft bleibt am Rezept**: Rezeptname und `siteUrl` stehen in der
  Rezeptansicht, mit Link auf die Originalseite. Das ist fremde Arbeit.

**Der erste Satz zu einem neuen Gericht nimmt schon das Rezept** —. Liegt nichts im Zwischenspeicher, holt der Web-Prozess selbst, mit 2 s
Frist je Anfrage; bei Zeitüberschreitung oder Ausfall bleibt es beim
Modellweg, und die Meldung sagt warum. Der Chat bricht nicht.

Davor stand hier das Gegenteil, und zwar mit Absicht: Spec 3 sagte pauschal
*„der Web-Prozess ruft nie eine fremde Seite auf"*, also trug er nur einen
Wunsch ein und startete einen eigenen Prozess — der erste Satz bekam die
geratene Liste, erst der zweite das Rezept. Gemessen am 2026-08-28 kostet der
Abruf 90 bis 147 ms und der Modellweg daneben 35.600 ms; die Regel schützte
einen Request, der ohnehin eine halbe Minute auf die vLLM-Box wartet, vor
einem Zehntel Sekunde. **Für den Katalog gilt sie unverändert weiter:** der
wird nie live abgefragt, und ein Ausfall von knuspr.de verhindert kein
Einkaufen.

Das Kommando von Hand bleibt — zum Vorwärmen und zum Nachholen:

```bash
.venv/bin/python -m zettel.gerichte.lauf --gericht "Pho"   # ein Gericht
.venv/bin/python -m zettel.gerichte.lauf --alle            # offene Wünsche
.venv/bin/python -m zettel.gerichte.lauf --ohne-treffer    # Altbestand
```

`--ohne-treffer` holt die Gerichte neu, ohne Trefferliste: zu
ihnen wurde keine Trefferliste mitgeschrieben, und aus einem gespeicherten
Rezept lassen sich die elf anderen nicht zurückgewinnen. **Das holt
bestehende Rezepte neu**, und dabei kann ein inzwischen besser bewertetes
gewinnen — genau das, was nach 90 Tagen ohnehin geschieht.

Zurücknehmen lässt sich der Abruf an einer Stelle: `Chat(quelle=Quelle(
holer=gerichte.nicht_holen))` liest weiter den Speicher, holt aber nichts
mehr nach. Genau das tun die Evals, damit zwei Läufe vergleichbar bleiben.

