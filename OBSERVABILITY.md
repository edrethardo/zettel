# Der Span-Vertrag

Was ein Chat-Zug in Phoenix hinterlässt, welche Attribute daran hängen und was
sie bedeuten. Dieses Dokument ist der Vertrag: was hier steht, prüfen
`tests/test_obs.py` und `checks/smoke.py` gegen einen In-Memory-Exporter, und
`scripts/trace_probe.py` gegen ein laufendes Phoenix.

Projekt in Phoenix: **`Picknick Agent`**, OTLP/HTTP auf
`http://localhost:6006/v1/traces`. Abschaltbar mit `PICKNICK_TRACING=0`; alles
andere heisst „an", auch nichts.

## Der Fall, um den es geht

Beim Bauen des Agenten kam dieser Satz durch:

> „…dazu brauche ich noch Zahnpasta und Butter"

Das Modell wählte eine **ButterBoyz BIO Salzbutter für 4,69 €**. Ein
Fehlgriff — niemand will handgemachte Spezialbutter, wenn er „Butter" sagt.
Die naheliegende Erklärung ist ein schlechtes Modell. Sie ist falsch, und der
Trace sagt warum. Das hier steht in den beiden `catalog.search`-Spans dieses
Zugs (Ränge aus dem echten Katalog, 2026-08-28, 2.498 Produkte):

```
„Butter“ — 5 Kandidaten vorgelegt          picknick.rejected = 0
   4,01  #1771  ButterBoyz BIO Butter Chili & Röstzwiebel   4,79 €
   4,01  #1772  ButterBoyz BIO Butter Feige & Anis          4,69 €
   3,96  #1757  ButterBoyz BIO Kräuterbutter                4,79 €
   3,96  #1766  ButterBoyz BIO Salzbutter        ← gewählt  4,69 €
   3,96  #1768  ButterBoyz BIO Steinpilzbutter              4,99 €
„Zahnpasta“ — 0 Kandidaten vorgelegt
```

`picknick.rejected = 0` heisst: **das Modell hat nichts erfunden.** Es hat aus
der vorgelegten Liste gewählt — und in der Liste stand keine normale Butter.
Die Wahl war unter den fünf Angeboten sogar vertretbar. Der Fehlgriff gehört
dem Retrieval.

Das ist kein Gedächtnisprotokoll: der Fall ist am 2026-08-28 gegen die echte
Box und das laufende Phoenix noch einmal gefahren worden. Der Trace
`099d1fdd…`, `chat.turn`-Span `6734d91d3569c413`, trägt

```
picknick.terms = 2   picknick.products = 1   picknick.free_text = 1
picknick.rejected = 0
picknick.weakest_term = "Zahnpasta"   picknick.weakest_rank = 0.0
catalog.search „Butter“     picknick.candidates = 5, rank_top = 4,0057
catalog.search „Zahnpasta“  picknick.candidates = 0, kein rank_top
plan.extract   252 prompt / 39 completion Token
plan.choose    527 prompt / 32 completion Token
```

und `plan.choose` gab wörtlich
`{"auswahl": [{"begriff": "Butter", "produkt_id": 1766, "menge": 1}]}` zurück —
die Salzbutter, eine der fünf, die vorlagen.

Genau deshalb ist `catalog.search` ein **`RETRIEVER`-Span und kein `TOOL`.**
Als `TOOL` wäre die Suche ein JSON-Klumpen im Trace und die Frage „lag es am
Modell oder an der Suche" bliebe ein Leseauftrag. Als `RETRIEVER` rendert
Phoenix die Kandidaten als Dokumentenliste mit Score, und die Antwort steht
da, ohne dass man etwas aufklappt.

Die Regel zum Ablesen ist kurz:

| im Trace | Schuld |
|---|---|
| das Richtige stand nicht unter den Dokumenten | Retrieval |
| es stand darunter, das Modell nahm ein anderes | Modell |
| `picknick.rejected > 0` | Modell, und zwar erfindend |

## Der Baum

Ein Chat-Zug ist genau ein Trace:

```
CHAIN        chat.turn         input: der Satz der Nutzerin
 ├ LLM       plan.extract      output: [{begriff, menge}, …]
 ├ RETRIEVER catalog.search     „Butter“    -> 5 Kandidaten mit Score
 ├ RETRIEVER catalog.search     „Zahnpasta“ -> 0 Kandidaten
 ├ LLM       plan.choose       Kandidaten -> gewählte product_ids
 └ output: die Vorschlagsliste
```

Ein `catalog.search`-Span **je Begriff**, nicht einer für alle Suchen. Die
Frage lautet „hat die Suche für DIESEN Begriff etwas Brauchbares vorgelegt",
und an einem Sammel-Span ist sie nicht mehr zu stellen.

Der **Rezeptweg** erzeugt nur den `CHAIN`-Span, mit
`picknick.path = "recipe"`: keine Modellstufe, keine Suche. Das ist auch der
Weg, der noch funktioniert, wenn die vLLM-Box schläft.

### Woher die Spans kommen

Die beiden `LLM`-Spans schreibt dieses Projekt **nicht selbst** — sie kommen
vom `OpenAIInstrumentor` (die vLLM-Box ist OpenAI-kompatibel). Der nennt sie
`ChatCompletion`; `picknick.obs.otel.StufenBenenner` benennt sie beim Öffnen
in `plan.extract` und `plan.choose` um.

Der naheliegende Ausweg — einen eigenen LLM-Span um den Aufruf legen — wäre
falsch: dann lägen zwei LLM-Spans übereinander, und Phoenix rechnet Kosten je
LLM-Span aus Tokenzahl mal Modell. Ein Chat-Zug wäre doppelt so teuer wie er
ist. Aus demselben Grund setzt dieses Projekt **keine Tokenzahlen von Hand**.

## Die Attribute

### `chat.turn` — `CHAIN`

| Attribut | Typ | Bedeutung |
|---|---|---|
| `input.value` | Text | der Satz, wortwörtlich |
| `output.value` | JSON | die Vorschlagsliste: `product_id`, `name`, `menge`, `begriff`, `rang`, `freitext` |
| `session.id` | Text | `korb-<order_id>` — mehrere Sätze zu **einem** Einkauf liegen in Phoenix als eine Sitzung zusammen. Ohne das steht jeder Zug für sich und „sie hat nachgebessert" ist nicht mehr zu sehen. |
| `picknick.order_id` | int | die Bestellung, an der der Zug hängt |
| `picknick.path` | Text | `llm` oder `recipe` |
| `picknick.chat_message_id` | int | die Antwortzeile in `chat_message` |
| `picknick.terms` | int | Begriffe aus Stufe 1 |
| `picknick.products` | int | Vorschläge mit echtem Produkt |
| `picknick.free_text` | int | Vorschläge ohne Produkt (Begriff bleibt als Freitext stehen) |
| `picknick.rejected` | int | **wie oft das Modell eine ID nannte, die ihm nie vorgelegt wurde** |
| `picknick.recipes` | Text | die erkannten Rezepte, nur auf dem Rezeptweg |
| `picknick.weakest_term` | Text | der Begriff mit dem schwächsten besten Treffer |
| `picknick.weakest_rank` | float | dessen Rang; ein Begriff ganz ohne Treffer zählt als `0.0` |

`picknick.rejected` ist die härteste Zusicherung des Projekts, als Zahl. Das
Modell sieht in Stufe 1 keinen Katalog und darf in Stufe 3 nur nennen, was ihm
vorgelegt wurde; eine fremde ID wird **verworfen und nicht repariert**. Wer in
Phoenix nach `picknick.rejected > 0` filtert, bekommt genau die Züge, in denen
das Modell etwas erfunden hat. Im Butter-Fall sind es null — und deshalb ist
er ein Retrieval-Fall.

`weakest_term`/`weakest_rank` sind die Abkürzung: wer eine Zugliste nach
`picknick.weakest_rank` aufsteigend sortiert, sieht die Retrieval-Probleme
zuerst, ohne einen einzigen Ast zu öffnen. Im Butter-Fall stünde dort
`Zahnpasta` mit `0.0` — der Begriff, für den die Suche gar nichts hatte.

### `catalog.search` — `RETRIEVER`

| Attribut | Bedeutung |
|---|---|
| `input.value` | der Suchbegriff |
| `output.value` | `[{id, name, rang}, …]` |
| `retrieval.documents.N.document.id` | die Produkt-ID |
| `retrieval.documents.N.document.content` | **was das Modell sah**: Name · Gebinde · Preis |
| `retrieval.documents.N.document.score` | der Rang (siehe unten) |
| `retrieval.documents.N.document.metadata` | Marke, Kategoriepfad, Preis in Cent, vorrätig — was das Modell NICHT sah, der Mensch beim Nachsehen aber braucht |
| `picknick.candidates` | Zahl der vorgelegten Kandidaten |
| `picknick.rank_top` | bester Rang dieser Suche; **fehlt**, wenn es keinen Treffer gab |
| `picknick.qty` | die Menge, die Stufe 1 zu diesem Begriff nannte |

`document.content` ist bewusst die Zeile, die dem Modell vorlag, und nicht die
ganze Produktzeile. Sonst zeigte der Trace eine Vorlage, die es nie gab, und
ein Fehlgriff sähe unerklärlicher aus, als er ist.

Bei null Treffern wird `rank_top` **nicht gesetzt**. Eine 0 dort wäre eine
Zahl, die niemand gemessen hat, und sie stünde in jeder Auswertung neben
echten Nullen.

### `plan.extract` / `plan.choose` — `LLM`

Kommen vom Instrumentor, also mit dem üblichen OpenInference-Satz:
`llm.model_name`, `llm.token_count.prompt`, `llm.token_count.completion`,
`input.value`, `output.value`, `llm.invocation_parameters`. Interessant sind
zwei davon:

* **`plan.extract` → `output.value`**: die Begriffe, die das Modell aus dem
  Satz gemacht hat. Hier sieht man, ob eine Zutat schon vor der Suche
  verlorenging — der teuerste Fehler dieses Agenten (siehe `EVALS.md`).
* **`plan.choose` → `input.value`**: die Kandidatenlisten, wie sie dem Modell
  vorlagen. Das ist die zweite Hälfte des Butter-Beweises.

## Der Rang, und was er nicht ist

Der Score an den Dokumenten ist das **negierte bm25** aus SQLite-FTS5, also
positiv und „grösser ist besser". bm25 selbst ist negativ und „kleiner ist
besser"; als Score übergeben wäre das rückwärts lesbar, und ein Score, den man
rückwärts lesen muss, wird irgendwann rückwärts gelesen.

**Er ist über Abfragen hinweg nicht geeicht.** Ein seltenes Wort bekommt
strukturell einen höheren Rang als ein häufiges, weil der Rang unter anderem
misst, wie ungewöhnlich der Begriff im Index ist. „Butter 4,01 gegen passierte
Tomaten 16,66" heisst nicht „viermal schlechter", sondern „hier stehen viele
ähnlich benannte Produkte, die Suche konnte kaum unterscheiden".

Wie stark das wirkt, misst man an `checks/smoke.py`: dessen Katalog hat zwölf
Produkte, von denen sechs zu „butter" passen — dort liegen dieselben Ränge bei
`2,06e-06`. Gleicher Code, gleiche Abfrage, sechs Grössenordnungen Unterschied,
nur weil der Index kleiner ist.

Für „wo lohnt sich das Nachsehen zuerst" reicht die Zahl. Als absolutes
Qualitätsmass taugt sie nicht, und **eine Eval, die daraus eine Schwelle
macht, misst den Katalog und nicht den Agenten.** Deshalb wird
`picknick.weakest_rank` in `EVALS.md` ausdrücklich nicht bewertet.

## Der Rückweg: Annotationen aus dem Produkt heraus

Die Nutzerin bestätigt oder verwirft jeden Vorschlag einzeln — nicht für eine
Eval, sondern weil sie es ohnehin tun muss, bevor sie bestellt. Beim
**Abschicken** (`orders.abschicken`) gehen diese Entscheidungen als
Annotationen auf den `chat.turn`-Span dieses Zugs:

| Annotation | Form |
|---|---|
| `mapping_precision` | ein Score je Zug: behaltene / entschiedene Vorschläge. Kein Label — „0,75" ist die Aussage, eine Textmarke daneben wäre eine Schwelle, die niemand festgelegt hat. |
| `suggestion` | eine je Vorschlag. Label `kept`/`removed`, Score `1.0`/`0.0`, **Erklärung = der Suchbegriff**, Metadaten mit `search_term`, `product_id`, `rank`, `free_text`. |

An jeder steht `annotator_kind = "HUMAN"`. Das ist kein Formfeld, sondern der
ganze Wert dieser Daten: wer in Phoenix danach filtert, bekommt echtes
menschliches Urteil, nicht einen LLM-Judge, der einen anderen LLM benotet. Es
ist Ground Truth ohne einen einzigen Annotationsauftrag.

So sieht das am Butter-Span von oben aus, zurückgelesen aus Phoenix, nachdem
„Zahnpasta" behalten und „Butter" verworfen und die Bestellung abgeschickt
wurde:

```
identifier              annotator_kind  label     score  explanation
picknick-suggestion-41  HUMAN           kept       1,0   Zahnpasta
picknick-suggestion-42  HUMAN           removed    0,0   Butter
picknick-turn-22        HUMAN           —          0,5   1 von 2 entschiedenen Vorschlägen behalten.
```

Die Erklärung ist der Suchbegriff. Damit steht im Trace direkt an der Zeile,
welcher Begriff schlecht gemappt hat — zusammen mit dem
`catalog.search`-Span desselben Begriffs ist „Modell oder Suche?" ohne
Zusatzarbeit beantwortet.

Der `rank` in den Metadaten schliesst den Kreis zum Butter-Fall: damit lässt
sich fragen, ob verworfene Vorschläge **systematisch** aus schwachen Suchen
kommen — dieselbe Frage wie oben, jetzt mit menschlichem Urteil daneben.

Drei Regeln halten die Zahlen ehrlich:

1. **Erst beim Abschicken**, nicht beim Tippen. Bis dahin darf sie ihre
   Meinung ändern.
2. **`offen` zählt nicht mit** — weder im Zähler noch im Nenner. Sonst zählte
   jeder Abbruch als Fehler des Modells.
3. **Ein Zug mit ausschliesslich offenen Vorschlägen bekommt gar keinen
   Score.** Eine fehlende Zahl ist ehrlicher als eine erfundene: `0.0` hiesse
   „alles falsch", wo „noch nichts gesagt" richtig ist.

`identifier` verhindert Dubletten (`picknick-turn-<id>`,
`picknick-suggestion-<id>`): zweimal abschicken ergibt denselben Bestand, nicht
den doppelten.

## Fällt Phoenix aus, fällt der Shop nicht aus

Das ist keine Absichtserklärung, es ist ein Prozessor. Gemessen am 2026-08-28
gegen einen **geschlossenen** Port:

```
ein Span  ...................................  6,44 s
fünf Spans (= ein Chat-Zug) ................. 34,93 s
fünf Spans durch `NichtBlockierend` .......... 0,15 ms
```

Die naheliegende Annahme — „ein abgelehnter Port scheitert sofort" — ist
falsch. Der OTLP-Exporter behandelt „Connection refused" als vorübergehenden
Fehler und versucht es mit wachsender Pause wieder (0,88 s, 1,83 s, 4,75 s …).
Ein Chat-Zug hätte 35 Sekunden gebraucht, nur weil Phoenix nicht läuft.

`picknick.obs.otel.NichtBlockierend` legt eine Schlange (10.000 Spans) und
einen Hintergrund-Thread zwischen Span und Exporter. **Das ist kein
Batching**: jeder Span geht einzeln und in Reihenfolge an denselben
`SimpleSpanProcessor` — der `BatchSpanProcessor` verschluckte bei höherer
Rate Spans (gemessen: 6.615 von 8.408 angekommen), und genau deshalb steht in
der Einrichtung `batch=False`.

Die Annotationen gehen einen **anderen** Weg (HTTP-Client statt Tracer), also
greift dieser Prozessor dort nicht. Gemessen gegen `arize-phoenix-client`
3.3.0:

```
Port zu (Connection refused) ..............   0,001 s
Host antwortet nicht (Paket verschwindet) .  10,0   s  (connect-Timeout)
Phoenix nimmt an und antwortet nie ........  30,0   s  (read-Timeout)
```

Der erste Fall ist harmlos, die beiden anderen sind es nicht — und genau sie
treten auf, wenn Phoenix auf einer anderen Maschine läuft oder hängt statt zu
sterben. `obs.labels.schreiben()` sammelt deshalb im Thread des Aufrufers aus
der Datenbank (Mikrosekunden) und gibt das Senden einem Hintergrund-Thread.
**Die Bestellung geht durch, egal was Phoenix macht.**

Ohne eingerichteten Tracer ist `obs.tracer()` ein No-Op-Tracer, nie `None`.
Der Aufrufer braucht keine Fallunterscheidung, und `obs.span_id()` gibt dann
`None` statt einer Null-ID — `"0000000000000000"` sähe aus wie ein Verweis und
zeigte ins Leere.

## Was hier schwächer ist, als es aussieht

* **Die Kosten in Phoenix sind unverifiziert.** Die Tokenzahlen stimmen — sie
  kommen vom Instrumentor und sind nach Prompt/Completion getrennt. Die
  Kostenzahl daneben rechnet Phoenix aus einer Preistabelle, und
  `Qwen3.8-27B-Instruct` dürfte darin fehlen. Es ist nie nachgesehen worden.
  Wer eine Zahl in Euro braucht, prüft zuerst, ob Phoenix das Modell überhaupt
  kennt.
* **`weakest_rank` ist ein Hinweis, kein Urteil** — siehe oben. Er sortiert
  eine Arbeitsliste, er begründet keine Note.
* **Die Suche kennt nur Präfixe.** „milch" findet „Landmilch" nicht über den
  Namen (nur über die Kategorie), „Klopapier" findet nie „Toilettenpapier".
  Ein Teil dessen, was im Trace nach Retrieval-Schwäche aussieht, ist diese
  eine Eigenschaft.
* **„0 Kandidaten" hat zwei Ursachen, und der Trace unterscheidet sie nicht.**
  Entweder fehlt das Produkt im Katalog, oder die Suche findet es nicht. Beides
  sieht im RETRIEVER-Span gleich aus. Der Butter-Fall enthält ein Beispiel für
  jede Sorte:

  - `zahnpasta` war eine **Crawl-Lücke**. Zum Zeitpunkt der Aufzeichnung
    stammte der Katalog aus einem 12-Begriffe-Lauf; nach dem Vollcrawl
    (2026-08-28, 36 min 57 s, 10.361 Produkte) findet dieselbe Suche
    `meridol ZAHNPASTA`. Von 59 leeren Begriffen blieben 4.
  - `klopapier` ist eine **echte Suchschwäche** und bleibt auch nach dem
    Vollcrawl bei 0 Treffern: der Katalog führt „Toilettenpapier", und die
    Präfixsuche kennt kein Synonym.

  Ein dritter Fall ist noch unangenehmer, weil er nicht leer aussieht:
  **`mehl` liefert „Kartoffeln mehligkochend"** — `mehl*` greift auf das
  falsche Wort. Im Trace steht ein Kandidat mit Score, und nur wer ihn liest,
  merkt es.
* **`document.score` ist über Traces hinweg nicht vergleichbar**, weil er vom
  Katalogumfang abhängt. Zwei Züge vor und nach einem Crawl haben andere
  Zahlen bei gleichem Verhalten.
