# Picknick

Ein privater Bestell-Shop für zwei Personen im Tailnet. Eine Person legt Lebensmittel in einen Warenkorb und schickt die
Bestellung ab, eine zweite kauft sie physisch im Laden ein und hakt sie
dort auf dem Handy ab. **Es wird nie eine
Bestellung an einen echten Händler geschickt.** Dazu ein Chat-Feld: freier Text
(„alles für Spaghetti Bolognese, und Klopapier") wird auf echte
Katalogprodukte abgebildet und als Vorschlag vorgelegt.

Zweiter, gleichrangiger Zweck: Der Chat-Agent ist in Arize Phoenix vollständig
beobachtbar, bewertbar und reproduzierbar vergleichbar. Der Entwurf steht in
`docs/superpowers/specs/2026-08-28-picknick-design.md`.

## Wozu die Observability gut ist — in einem Fall

Auf „…dazu brauche ich noch Zahnpasta und Butter" wählte das Modell eine
**ButterBoyz-Spezialbutter für 4,69 €**. Ein Fehlgriff. Die naheliegende
Erklärung ist ein schlechtes Modell; der Trace sagt, dass sie falsch ist:

```
„Butter“ — 5 Kandidaten vorgelegt        picknick.rejected = 0
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
| [`DESIGN.md`](DESIGN.md) | Architektur und die Entscheidungen, die von aussen wie ein Versehen aussehen |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | **der Span-Vertrag**: welcher Span, welche Attribute, was sie bedeuten |
| [`EVALS.md`](EVALS.md) | Dataset, Evaluatoren, die vier Varianten und die echten Zahlen |
| [`DEMO.md`](DEMO.md) | der Klickpfad zum Vorführen, eine Prüfung pro Klick |
| [`GATES.md`](GATES.md) | was das Gate abdeckt — und was ausdrücklich nicht |

## Schnellstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m picknick.scrapers.nachtlauf --begriff milch   # etwas Katalog
.venv/bin/python -m picknick.web.app
```

Der Shop lauscht dann auf `http://100.64.0.1:8730` (Tailscale) und auf
`http://127.0.0.1:8730`. Auf `0.0.0.0` bindet er nicht — der Versuch bricht mit
einer Fehlermeldung ab, weil es kein Passwort gibt und das Tailnet der einzige
Schutz ist.

Für den Chat braucht es die lokale vLLM-Box; ohne sie läuft alles ausser dem
Chat weiter, und der Rezeptweg sogar auch. Für Traces und Evals ein Phoenix
auf `localhost:6006` — ohne läuft der Shop unverändert, siehe
[`OBSERVABILITY.md`](OBSERVABILITY.md).

## Prüfen

```bash
.venv/bin/python checks/smoke.py     # das Gate: 51 Checks, exit 0 / 1
.venv/bin/python -m pytest -q        # 709 Tests, rund 22 s
.venv/bin/python -m pytest -q -n auto   # dieselben Tests auf allen Kernen, rund 14 s
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
  `picknick/scrapers/begriffe.py` — Knuspr hat keinen Endpunkt für den ganzen
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
.venv/bin/python -m picknick.gerichte.lauf --gericht "Pho"   # ein Gericht
.venv/bin/python -m picknick.gerichte.lauf --alle            # offene Wünsche
```

Zurücknehmen lässt sich der Abruf an einer Stelle: `Chat(quelle=Quelle(
holer=gerichte.nicht_holen))` liest weiter den Speicher, holt aber nichts
mehr nach. Genau das tun die Evals, damit zwei Läufe vergleichbar bleiben.

