# Picknick — Design

**Datum:** 2026-08-28
**Status:** abgenommen, noch nicht implementiert
**Projekt:** `/home/user/code/picknick_klon`, Werkbank-Board als „Picknick"

---

## 1 Zweck

Ein privater Bestell-Shop für zwei Personen im Tailnet. Eine Person legt Lebensmittel in einen Warenkorb und schickt die
Bestellung ab, eine zweite kauft sie physisch im Laden ein und hakt sie
dort auf dem Handy ab. Es wird nie eine Bestellung an
einen echten Händler geschickt.

Dazu ein Chat-Feld: freier Text („alles für Spaghetti Bolognese, und Klopapier")
wird auf echte Katalogprodukte abgebildet und als Vorschlag vorgelegt.

**Zweiter, gleichrangiger Zweck:** Das Projekt ist ein Vorführstück für eine
Bewerbung bei Arize. Der Chat-Agent muss in Arize Phoenix vollständig
beobachtbar, bewertbar und reproduzierbar vergleichbar sein. Wo Produktnutzen und
Observability in Konflikt geraten, gewinnt Observability — aber der Shop muss ein
echtes, benutztes Produkt bleiben, denn genau daraus entstehen die Labels
(Abschnitt 8).

## 2 Umfang

**Enthalten:** Katalog mit Suche, Warenkorb, Bestellung, Pick-Ansicht,
Chat-gestützte Bestellung, eigene Rezeptsammlung, Phoenix-Tracing, Evals,
Experiments, Dokumentation.

**Bewusst nicht enthalten:** Bezahlung, Nutzerverwaltung, Zeitfenster/Slots,
Routenplanung, Fremdkunden, Mehrmandantenfähigkeit, öffentliche Erreichbarkeit,
mobile Apps, Substitutionsvorschläge, Preis-Nachtragen, Verrechnung zwischen den
beiden Personen.

## 3 Architektur

Ein FastAPI-Prozess, eine SQLite-Datei, server-gerendertes Jinja2 mit HTMX für
Interaktionen. Kein Build-Schritt, kein npm zur Laufzeit, ein systemd-User-Service.

```
picknick/
  web/          FastAPI-Routen, Jinja-Templates, HTMX
  catalog/      Produktsuche (SQLite FTS5), Kategorien
  orders/       Warenkorb, Bestellung, Pick-Liste
  recipes/      Rezepte und deren Produktverknüpfungen
  assistant/    Chat: Text -> Suchbegriffe -> Kandidaten -> Vorschlag
  llm/          vLLM-Client, Wake-Handling, Modell-Discovery
  obs/          Phoenix-Tracer, Span-Helfer, Annotationen
  scrapers/
    knuspr.py   Katalog-Crawler, reines HTTP
  db.py         Schema und Migrationen
checks/
  smoke.py      Gate: grün ohne Netz und ohne Modell
evals/
  dataset.py    Dataset picknick-anfragen anlegen/aktualisieren
  experiment.py Experiments über das Dataset fahren
```

Zwei Regeln tragen den Entwurf:

1. **Der Web-Prozess ruft nie eine fremde Seite auf.** Der Shop liest
   ausschließlich aus der DB. Der Crawler ist ein eigener Prozess mit eigenem
   Timer. Fällt er aus, wird der Katalog alt — der Shop funktioniert weiter.
2. **Ohne Modell bleibt alles außer dem Chat benutzbar.** Suchen, Einlegen,
   Bestellen, Abhaken laufen ohne LLM.

## 4 Datenmodell

```
product      (id, source, external_id, name, brand, price_cents, price_per_unit_cents,
              unit_text, unit, image_path, category_l1, category_l2, category_l3,
              in_stock, last_seen_at, active)
orders       (id, state, note, created_at, submitted_at, done_at)
order_item   (id, order_id, product_id NULL, free_text NULL, qty, store,
              picked_at NULL)
recipe       (id, name, servings, note)
recipe_item  (id, recipe_id, product_id NULL, free_text NULL, qty)
chat_message (id, order_id, role, content, span_id, created_at)
chat_suggestion (id, chat_message_id, product_id NULL, free_text NULL, qty,
              search_term, rank, decision, decided_at)
scrape_run   (id, source, started_at, finished_at, status, n_products, error)
```

**Abweichung, nachträglich:** Die Bestelltabelle heisst `orders`, nicht
`order`. `order` ist ein SQL-Schlüsselwort und müsste in jeder Abfrage gequotet
werden — eine Falle, die jedes Folgeticket einmal gestellt hätte. Alle Spalten
sind unverändert.

Entwurfsentscheidungen mit Begründung:

**Der Warenkorb ist eine Bestellung im Zustand `draft`.** Zustände:
`draft -> offen -> erledigt`. Kein zweites Modell, kein Kopieren beim Abschicken,
nur ein Zustandswechsel. „Unterwegs" gibt es nicht — er sieht selbst, dass er im
Laden steht.

**Es existiert genau ein `draft`, gemeinsam für beide.** Bei zwei Personen in
einem Haushalt sind getrennte Warenkörbe eine Fehlerquelle, keine Funktion; sonst
wird Milch doppelt gekauft. Wer eingelegt hat, wird nicht erfasst.

**`product_id` ist überall optional, daneben steht `free_text`.** Der Katalog wird
Lücken haben. Ohne Freitext-Ventil wird der Shop genau an der Stelle unbenutzbar,
wo er es nicht sein darf.

**`store` hängt an `order_item`, nicht an `product`.** Der Katalog ist
ladenneutral (Abschnitt 5). „Rewe oder Lidl" ist eine Entscheidung beim Einlegen.
Vorbelegt wird sie aus der letzten Wahl für dasselbe Produkt; nach zwei Wochen
stimmt die Vorauswahl meistens, ohne gepflegte Regeln.

**`chat_suggestion.decision`** (`kept` | `removed` | `offen`) ist der Kern von
Abschnitt 8: es ist die Bestätigung der Nutzerin und damit das Eval-Label.

## 5 Katalog und Crawler

### 5.1 Quelle: knuspr.de

Gemessen am 2026-08-28 (Belege in Anhang A). Rewe ist hinter Bot-Schutz
(HTTP 403 mit Captcha-Seite), Lidls offene API führt nur das Online-Sortiment
(Wein, Non-Food) und nicht das Filialsortiment. Kaufland, Flink, Edeka und
Bringmeister antworten mit 403.

`knuspr.de` (Rohlik-Gruppe) liefert dagegen einen vollständigen deutschen
Lebensmittelkatalog über eine offene JSON-Schnittstelle, ohne Schlüssel, ohne
Bot-Schutz, mit Preis, Bild, Marke, Gebindegröße und dreistufigem Kategoriebaum.
`robots.txt` sperrt nur `/regal/*`.

Weil er die Ware selbst im Laden greift, muss der Katalog nur die richtigen
Produkte zeigen, nicht die richtige Kasse ansprechen. Damit ist ein
ladenneutraler Katalog aus einer sauberen Quelle einem brüchigen
Rewe-Playwright-Crawler vorzuziehen. **Playwright entfällt vollständig.**

Ehrliche Einschränkung, die dokumentiert gehört: Die Preise sind Knuspr-Preise,
nicht Rewe- oder Lidl-Preise, und Handelsmarken wie `ja!` oder `Milbona` fehlen.
Sie sind Richtwerte. Die Oberfläche behauptet nichts anderes.

### 5.2 Ablauf

Ein Prozess, reines HTTP, ein systemd-Timer, nachts:

1. Suchbegriffe aus den Kategorienamen, die Knuspr in jeder Antwort mitliefert.
2. Je Begriff `search-metadata?search=<begriff>&companyId=6&limit=200&offset=N`,
   Schleife bis `totalHits` erreicht ist.
3. Bilder werden heruntergeladen und lokal abgelegt, **nicht** per Hotlink
   eingebunden — sonst hängt der Shop im Tailnet an `cdn.knuspr.de` und sieht
   kaputt aus, sobald die URLs rotieren.
4. 1–2 s Pause zwischen Anfragen. Bei einem nächtlichen Lauf ist Tempo egal,
   Höflichkeit nicht.

### 5.3 Schutz gegen kaputte Läufe

- **Staging und Swap.** Ein Lauf schreibt in eine Nebentabelle und wird erst am
  Ende übernommen. Ein Abbruch hinterlässt keinen halben Katalog.
- **Plausibilitätsschwelle.** Liefert ein Lauf weniger als 50 % der Produkte des
  letzten erfolgreichen, wird er verworfen und in `scrape_run` als `rejected`
  mit Begründung protokolliert.
- **Kein Löschen.** Verschwundene Produkte werden `active = 0`, nie entfernt —
  sonst zerreißen Verweise aus alten Bestellungen und Rezepten.

## 6 Der Agent

Die tragende Regel: **das Modell erfindet niemals Produkte.** Ein LLM, das
Produkt-IDs frei ausgeben darf, halluziniert Produkt-IDs. Deshalb zwei Stufen:

1. **`plan.extract`** — das Modell bekommt den Satz und gibt ausschließlich
   Suchbegriffe mit Mengen zurück:
   `[{"begriff": "Hackfleisch", "menge": 1}, {"begriff": "passierte Tomaten", "menge": 2}]`
2. **`catalog.search`** — der Shop sucht jeden Begriff selbst per SQLite-FTS5 und
   legt die Kandidaten vor.
3. **`plan.choose`** — erst jetzt wählt das Modell, und zwar nur aus der
   vorgelegten Liste. Es kann nichts wählen, was nicht existiert.

**Rezepte kürzen Stufe 1 ab.** Trifft die Anfrage ein gespeichertes Rezept, werden
dessen bereits verknüpfte Produkte direkt vorgeschlagen — ohne Modell und ohne
Suche. Das Modell wird nur für alles gebraucht, was kein Rezept ist. Rezepte legen
die beiden selbst an; zusätzlich gibt es an jeder erledigten Bestellung den Knopf
„daraus ein Rezept machen", weil das der einzige Moment ist, in dem die Zutaten
ohnehin beisammen sind.

**Nichts landet ungefragt im Warenkorb.** Der Chat legt eine Vorschlagsliste vor
(Bild, Name, Menge, Preis); sie bestätigt oder verwirft jede Zeile einzeln.

**Modellzugang.** Endpunkt aus der Konfiguration, Vorgabe
`http://vllm-box.local:8000/v1`. Das Modellkürzel wird beim Start über
`/v1/models` erfragt und nie im Code festgeschrieben — es hat auf dieser Box schon
gewechselt. Vor jedem Chat-Request wird `/v1/models` geprüft: antwortet die Box,
läuft alles normal; antwortet sie nicht, wird `wake-vllm` im Hintergrund gestartet
und die Oberfläche zeigt „Modell wacht auf … ~90 s" mit laufendem Zähler, statt zu
hängen oder abzubrechen.

## 7 Observability

Eigenes Phoenix-Projekt **`Picknick Agent`**, getrennt vom Board-Projekt
`Picknick` (dort landen über `werkbank_phoenix/ingest.py` die Ticket-Läufe, also
wie das Projekt gebaut wurde — nicht wie es sich im Betrieb verhält).

### 7.1 Span-Vertrag

Ein Chat-Zug erzeugt genau diesen Baum:

```
CHAIN      chat.turn          input: der Satz der Nutzerin
 ├ LLM     plan.extract       output: [{begriff, menge}, …]
 ├ RETRIEVER catalog.search   input: begriff   output: n Kandidaten mit Score
 ├ RETRIEVER catalog.search   (ein Span je Suchbegriff)
 ├ LLM     plan.choose        input: Kandidatenliste  output: gewählte product_ids
 └ output: die Vorschlagsliste
```

`RETRIEVER` statt `TOOL` für `catalog.search` ist bewusst gewählt: die Suche *ist*
Retrieval, und Phoenix rendert die Kandidaten dann als Dokumente mit Score statt
als JSON-Klumpen. Damit ist im Trace unmittelbar sichtbar, ob ein Fehlgriff am
Modell lag oder daran, dass FTS5 das richtige Produkt nie vorgelegt hat. Das ist
die Frage, die sich ohne Tracing nicht beantworten lässt.

Bei einem Rezepttreffer entfallen `plan.extract` und `plan.choose`; der
`chat.turn`-Span trägt dann `picknick.path = "recipe"` statt `"llm"`, damit
Auswertungen beide Wege unterscheiden können.

`chat_message.span_id` speichert die Span-ID, damit spätere Annotationen
(Abschnitt 8) den richtigen Span treffen.

### 7.2 Einrichtung

```python
from phoenix.otel import register
tracer_provider = register(
    project_name="Picknick Agent",
    endpoint="http://localhost:6006/v1/traces",
    auto_instrument=False, batch=False,
    set_global_tracer_provider=False)
from openinference.instrumentation.openai import OpenAIInstrumentor
OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
```

Die vLLM-Box ist OpenAI-kompatibel, der Instrumentor erfasst sie damit. Die
`CHAIN`- und `RETRIEVER`-Spans werden von Hand gesetzt.

### 7.3 Bereits bezahlte Fallstricke

Diese drei sind aus vorherigen Projekten belegt und werden nicht erneut geprüft:

- **`batch=False`.** Der Batch-Prozessor verschluckt bei höherer Emissionsrate
  Spans (gemessen: 6.615 von 8.408 angekommen).
- **`set_global_tracer_provider=False`.** Sonst kollidiert es mit anderen Tracern
  im selben Prozess.
- **Token-Klassen getrennt halten.** Phoenix rechnet Kosten aus Tokenzahl mal
  Modellname; werden Cache- und Eingabe-Token zusammengeworfen, ist die Kostenzahl
  in beide Richtungen falsch. Span-Kind muss `LLM` sein, damit überhaupt
  gerechnet wird.

**Fällt Phoenix aus, darf der Shop nicht ausfallen.** Der Tracer wird so
konfiguriert, dass ein nicht erreichbarer Collector den Request nicht blockiert
und nicht scheitern lässt.

### 7.4 Verifikation

Nicht über die Oberfläche. Ein leerer Span sieht dort genauso gut aus und misst
nichts. Geprüft wird programmatisch:

```python
from phoenix.client import Client
df = Client(base_url="http://localhost:6006").spans.get_spans_dataframe(
    project_identifier="Picknick Agent")
```

Zugesichert wird: Anzahl Spans > 0, **und** die interessierenden Attribute sind
nicht null (`attributes.llm.token_count.*`, Ein- und Ausgabewerte, die
Retriever-Dokumente).

## 8 Evals

### 8.1 Das Label fällt aus dem Produkt

Sie bestätigt oder verwirft jeden Vorschlag einzeln — das ist Ground Truth, ohne
dass jemand Daten annotiert. Jede Entscheidung wird in `chat_suggestion.decision`
festgehalten und als Annotation auf den `chat.turn`-Span zurückgeschrieben
(`spans.log_span_annotations`):

- `mapping_precision` — Score: behaltene / vorgeschlagene Artikel.
- je Vorschlag `kept` oder `removed`, mit dem Suchbegriff als Erklärung.

Annotationen werden erst geschrieben, wenn die Bestellung abgeschickt ist; bis
dahin kann sie ihre Meinung noch ändern. Vorschläge, über die nie entschieden
wurde, bleiben `offen` und gehen nicht in die Quote ein — sonst zählt Abbruch als
Fehler.

### 8.2 Dataset `picknick-anfragen`

Feste Eingaben mit erwartetem Ergebnis, angelegt über
`datasets.create_dataset` / `add_examples_to_dataset`. Vier Sorten:

| Sorte | Beispiel | Erwartung |
|---|---|---|
| gewöhnlich | „alles für Spaghetti Bolognese" | Nudeln, Hackfleisch, passierte Tomaten, Zwiebel, Knoblauch |
| mehrdeutig | „Milch" | irgendein Produkt aus Kategorie `Milch` |
| gemischt | „Klopapier und Spülmittel" | kein Rezeptpfad, zwei Non-Food-Treffer |
| Falle | „etwas Süßes" | ein Treffer aus einer Süßwaren-Kategorie, kein leeres Ergebnis |

Erwartet wird auf **Kategorieebene**, nicht auf Produkt-ID. Ein Dataset, das eine
bestimmte Produkt-ID verlangt, wird beim nächsten Katalog-Crawl ungültig.

### 8.3 Experiments

Dieselbe Pipeline über dasselbe Dataset, je eine Stellschraube verändert
(`experiments.run_experiment`, `evaluate_experiment`):

- Prompt-Variante A gegen B für `plan.extract`
- 5 gegen 20 vorgelegte Kandidaten für `plan.choose`
- lokales Qwen gegen ein größeres Modell

Zwei Evaluatoren: ein deterministischer, der prüft, ob das gewählte Produkt in der
erwarteten Kategorie liegt, und ein LLM-Judge für die Fälle, in denen „richtig"
Ermessenssache ist. Experiments werden von Hand gestartet und brauchen die
vLLM-Box; das Gate (Abschnitt 12) braucht sie nicht.

## 9 Oberfläche

Sechs Ansichten, durchgehend fürs Handy gebaut — beide benutzen Telefone, er im
Laden.

- **Katalog** — Suchfeld oben, darunter Kategorien aus dem Knuspr-Baum, Produkte
  als Kacheln mit Bild, Name, Gebindegröße und Preis. „+" legt ein, ohne
  Seitenwechsel.
- **Warenkorb** — der `draft`. Je Zeile Menge, Laden (Rewe / Lidl / egal,
  vorbelegt), Löschen. Ein Feld für Freitext-Artikel. Unten „Bestellung
  abschicken".
- **Chat** — gehört zum Warenkorb, kein eigener Ort. Vorschläge erscheinen als
  vorgemerkte Zeilen zum einzelnen Bestätigen oder Verwerfen.
- **Rezepte** — Liste, Anlegen und Bearbeiten, „alles in den Warenkorb".
- **Bestellungen** — offene oben, erledigte darunter.
- **Pick-Ansicht** — eine Bestellung, nach Laden getrennt, große Checkboxen mit
  Produktbild daneben, damit im Regal das Richtige gegriffen wird.

## 10 Zugang

**Kein Passwort.** Der Sicherheitsrahmen ist das Tailnet. Der Prozess bindet
ausschließlich auf die Tailscale-Adresse und `localhost`, **nie auf `0.0.0.0`** —
sonst hängt der Shop im nächsten fremden WLAN offen.

Wer die beiden sind, entscheidet ein einmalig gesetztes Cookie; es steuert nur die
Startansicht (sie → Katalog, er → Pick-Liste) und ist ausdrücklich keine
Sicherheitsgrenze. `tailscale whois` wäre eleganter, taugt hier aber nicht: ihr
Gerät läuft unter seinem Tailscale-Konto, die Abfrage würde beide gleich benennen.

**Voraussetzung, die noch offen ist:** Ihr Handy ist noch nicht im Tailnet und
muss aufgenommen werden.

## 11 Fehlerverhalten

Die Oberfläche schweigt nicht, wenn etwas fehlt:

- Katalog veraltet → Hinweisband „Preise sind N Tage alt".
- vLLM nicht erreichbar → Chat ausgegraut mit Grund und Aufwach-Hinweis, alles
  andere unverändert benutzbar.
- Crawl-Lauf verworfen → in `scrape_run` mit Begründung, sichtbar auf einer
  schlichten Statusseite.
- Phoenix nicht erreichbar → keine sichtbare Auswirkung, der Shop läuft weiter.

## 12 Betrieb

Zwei systemd-User-Units: `picknick.service` (Web) und `picknick-crawl.timer`
(nachts). Der Timer bekommt `Persistent=true` — der Laptop schläft nachts
zugeklappt, ohne das fiele der Crawl aus und der Katalog würde nie aktualisiert.

Nächtliche Sicherung der SQLite-Datei per `VACUUM INTO`, sieben Stände.

Läuft auf `user-Laptop`. Dass der Shop mit dem Laptop schläft, ist bekannt und
akzeptiert; ein Umzug auf einen Dauerläufer ist später ein reiner Ortswechsel von
Prozess und Datei.

## 13 Tests

`pytest`, dazu `checks/smoke.py` als Gate.

- **Crawler** gegen eine echte, im Repo gespeicherte Knuspr-Antwort. Nie gegen das
  Netz — sonst ist die Testsuite genauso kaputt wie die Seite, wenn sich das
  Format ändert. Ein separates, von Hand gestartetes Skript prüft gegen die echte
  Seite und meldet, ob die Felder noch stimmen.
- **Agent** gegen einen Fake-LLM mit fester Antwort, damit die Zweistufigkeit
  prüfbar ist, ohne die Box zu wecken. Ausdrücklich geprüft wird, dass eine
  erfundene Produkt-ID des Modells verworfen wird.
- **Bestell- und Warenkorblogik** als gewöhnliche Unit-Tests.
- **Observability** gegen einen In-Memory-Span-Exporter: der Baum aus 7.1
  entsteht in der richtigen Form, mit den richtigen Span-Kinds und nicht-leeren
  Attributen. Das braucht kein laufendes Phoenix.
- **Rauchtest** über den ganzen Weg: Katalog → einlegen → abschicken → abhaken.

`checks/smoke.py` wird **ohne Netz, ohne Modell und ohne Phoenix grün.** Es ist
zugleich das Gate, das in der Werkbank für dieses Projekt noch fehlt.

## 14 Dokumentation

Im Hausstil von `phoenix_mobile`, weil das Projekt vorgezeigt wird:

| Datei | Inhalt |
|---|---|
| `README.md` | was, warum, wie starten |
| `DESIGN.md` | Architektur und Entwurfsentscheidungen |
| `OBSERVABILITY.md` | der Span-Vertrag: welcher Span, welche Attribute, was sie bedeuten |
| `EVALS.md` | Dataset, Evaluatoren, wie die Experiments nachzufahren sind |
| `DEMO.md` | Klickpfad zum Vorführen, eine Prüfung pro Klick |
| `GATES.md` | was das Gate abdeckt und was ausdrücklich nicht |

## 15 Abhängigkeiten

`fastapi`, `uvicorn`, `jinja2`, `httpx`, `pytest`. SQLite und FTS5 kommen aus der
Standardbibliothek. HTMX wird als Datei mitgeliefert, nicht per CDN geladen — das
Tailnet ist nicht zwingend online.

Für Observability zusätzlich: `arize-phoenix-otel`, `arize-phoenix-client`,
`openinference-instrumentation-openai`. Die ersten beiden sind auf der Maschine
bereits im Einsatz (Version 0.17.1 und 3.3.0), der Instrumentor ist neu.

Kein Playwright, kein Chromium, kein npm zur Laufzeit.

## 16 Bewusst weggelassen

Zeitfenster und Bestellschluss; getrennte Warenkörbe; Kategorien- oder
Gangsortierung in der Pick-Ansicht; „gab's nicht"-Markierung; Nachtragen echter
Preise und Summenbildung; Rezeptimport von fremden Seiten; MCP-Server;
Messenger-Bot; Claude-API als LLM-Fallback; ein zweiter Crawler für Rewe oder
Lidl. Jedes davon ist nachrüstbar, keines wird für den ersten Nutzen gebraucht.

---

## Anhang A — gemessene Befunde, 2026-08-28

Alle Zahlen stammen aus tatsächlich ausgeführten Anfragen, nicht aus Annahmen.

| Quelle | Ergebnis |
|---|---|
| `shop.rewe.de` | HTTP 403, 251 KB Captcha-Seite. Reines HTTP scheidet aus. |
| `lidl.de` `/q/api/search` | HTTP 200, offenes JSON — aber Online-Sortiment, nicht Filialsortiment |
| `kaufland.de`, `goflink.com`, `edeka.de`, `bringmeister.de` | HTTP 403 / blockiert |
| `api.marktguru.de` | HTTP 401, Schlüssel nicht auffindbar |
| `kaufda.de`, `meinprospekt.de`, `discounto.de` | HTTP 200 HTML, nur Prospekte, müssten geparst werden |
| `openfoodfacts.org` | HTTP 200, 1787 deutsche Milchprodukte, Marke und Bild, **keine Preise** |
| `mytime.de` | HTTP 200, nicht weiter untersucht |
| **`knuspr.de`** | **HTTP 200, offen, kein Schlüssel, Vollkatalog** |

Knuspr im Detail:

```
GET /services/frontend-service/search-metadata?search=milch&companyId=6&limit=200
-> 200, 450 KB, 202 Produkte, totalHits=948
```

```json
{ "productId": 95793,
  "productName": "Miil Frische Landmilch 3,8% Vollmilch",
  "brand": "Miil",
  "price":        {"full": 1.19, "currency": "€"},
  "pricePerUnit": {"full": 1.19, "currency": "€"},
  "textualAmount": "1 l", "unit": "l", "inStock": true,
  "imgPath": "/images/grocery/products/95793/95793-1738160256165.jpg",
  "categories": [{"id": 547, "name": "Frischmilch", "level": 3},
                 {"id": 546, "name": "Milch", "level": 2}] }
```

- `offset` paginiert, `limit=200` wird angenommen, `totalHits` beendet die Schleife.
- Bilder lösen auf `cdn.knuspr.de` auf (HTTP 200, 173 KB JPEG).
- `robots.txt` sperrt ausschließlich `/regal/*`.

SQLite auf dieser Maschine ist 3.37.2, **FTS5 ist einkompiliert** — geprüft mit
`CREATE VIRTUAL TABLE … USING fts5` samt Testtreffer. Die Volltextsuche aus
Abschnitt 6 braucht damit keine Zusatzabhängigkeit.

Phoenix auf `localhost:6006`, Projekte tragen die Werkbank-Namen. In
`arize-phoenix-client` 3.3.0 verifiziert vorhanden: `datasets.create_dataset`,
`datasets.add_examples_to_dataset`, `experiments.run_experiment`,
`experiments.evaluate_experiment`, `experiments.log_evaluation`,
`spans.log_span_annotations`, `spans.get_spans_dataframe`, `projects.create`.

`werkbank_phoenix/ingest.py --dry-run` meldet
`308 tickets on the board, 169 finished runs, 0 not yet ingested` — der Ingester
kennt „Picknick", es gibt nur noch kein abgeschlossenes Ticket.
