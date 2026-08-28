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

> **Stand der Aufzeichnung: 2026-08-28, Katalog mit 2.498 Produkten, vor
> WB-339 und WB-340.** Wer den Fall heute nachfährt, sieht etwas anderes —
> und das ist der Punkt. Nach dem Vollcrawl (10.361 Produkte) und der
> Wortgrenzen-Sortierung aus WB-339 liegt „Weihenstephan Butter" auf Platz 3
> der Kandidaten, neben Landliebe und Lindner. Das Retrieval legt jetzt vor,
> was damals fehlte; dieselbe Frage bekäme heute vermutlich eine andere
> Antwort. Der Fall bleibt hier stehen, weil er zeigt, WOFÜR der
> RETRIEVER-Span da ist — nicht als Aussage über den heutigen Katalog. Eine
> Trace-Aufzeichnung ohne Katalog- und Codestand danebenzustellen wäre genau
> der Fehler, den dieses Dokument vermeiden soll.

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
 ├ LLM       plan.extract      output: [{suchbegriffe, menge}, …]
 ├ RETRIEVER catalog.search     [„Auberginen“, „Aubergine“] -> 6 Kandidaten
 ├ RETRIEVER catalog.search     [„Zahnpasta“]               -> 0 Kandidaten
 ├ LLM       plan.choose       Kandidaten -> gewählte product_ids
 └ output: die Vorschlagsliste
```

Ein `catalog.search`-Span **je Zutat**, nicht einer für alle Suchen. Die
Frage lautet „hat die Suche für DIESE Zutat etwas Brauchbares vorgelegt",
und an einem Sammel-Span ist sie nicht mehr zu stellen.

Seit WB-340 liefert Stufe 1 je Zutat **mehrere** Suchbegriffe, vom genauesten
zum allgemeinsten, und der Shop sucht sie alle; die Treffer werden nach
Produkt-ID entdoppelt und als EINE Kandidatenliste vorgelegt. Der Span gehört
deshalb der Zutat und nicht dem einzelnen Begriff — sonst gäbe es die Vorlage,
aus der Stufe 3 wirklich gewählt hat, an keinem Span mehr zu sehen. Über
welchen Begriff ein einzelner Kandidat kam, steht an seinem Dokument
(`document.metadata.via`).

Der **Rezeptweg** erzeugt nur den `CHAIN`-Span, mit
`picknick.path = "recipe"`: keine Modellstufe, keine Suche. Das ist auch der
Weg, der noch funktioniert, wenn die vLLM-Box schläft.

Der **Quellenweg** (`picknick.path = "chefkoch"`, WB-338) hat denselben Baum
wie der Modellweg — nur bekommt `plan.extract` dort nicht den Satz zu lesen,
sondern die Zutatenliste eines geholten Rezepts. Der Unterschied ist mit
Absicht ein eigener `path`-Wert und kein Nebensatz: **die Frage, ob die
Quelle wirklich besser ist als das Raten, wird an genau diesem Attribut
gemessen.**

Der **Auffächerungsweg** (`picknick.path = "fanout"`, WB-368) ist der
kürzeste von allen: kein `catalog.search`, kein `plan.choose`, oft nicht
einmal ein `plan.extract` — nur der `CHAIN`-Span. Er hat **null Vorschläge**,
und das ist kein Fehlgriff, sondern eine Rückfrage: „Aufschnitt" ist ein
Regal, keine Ware. Ohne den eigenen `path` sähe dieser Zug in jeder
Auswertung aus wie einer, der nichts gefunden hat.

Der Umweg lässt sich messen, weil `picknick.fanout_category` auf BEIDEN
Hälften steht: der `fanout`-Zug bietet die Sorten an, der `llm`-Zug daneben
trägt `picknick.varieties_chosen` und die Vorschläge dazu. Wer die beiden
nebeneinanderlegt, sieht, ob aus der Rückfrage ein bestätigter Posten wurde —
und `picknick.fanout_source` sagt, ob dafür überhaupt ein Modell nötig war
(`catalog` heisst: das getippte Wort war selbst eine Kategorie, der Zug lief
in 0,0 s).

**`korb.menge` steht NICHT unter `chat.turn`** (WB-362). Er entsteht, wenn
eine Menge in den Korb gerechnet wird — beim Tipp auf „Alles in den
Warenkorb" und, seit WB-369, bei jedem „Ja" auf einen Vorschlag aus einem
Rezept. Beides sind eigene Requests: der Chat-Zug ist längst beendet, wenn
jemand entscheidet, und oft steht ein „Ja" zu einem Zug von gestern an. Ihn an
den Baum oben zu hängen hiesse, eine Verwandtschaft zu behaupten, die es nicht
gibt; er ist ein eigener Trace mit einer eigenen Frage („warum liegen hier
zwei Packungen?"), und die Attribute stehen weiter unten. Verbunden sind die
beiden über `picknick.search_term` und die Produkt-ID, nicht über den Baum.

**Seit WB-367 ist ein `llm`-Zug MIT gesetztem `picknick.dish` ein Befund und
kein Normalfall.** Vorher war er die Regel: der erste Satz zu einem neuen
Gericht riet, der Abruf lief daneben, und dasselbe Gericht tauchte zweimal
auf — einmal `llm`, danach `chefkoch`. Heute wird im Zug selbst geholt, also
heisst diese Kombination: der Abruf hat nicht getragen. `picknick.dish_fetch`
sagt, warum (`leer`, `fehler`, oder leer für „gar nicht abgerufen").

Die Zeilen daneben zu legen — `picknick.products`, `picknick.free_text` und
`dish` — bleibt der ganze Vergleich. Für „alles für Pho" sah er am 2026-08-28
so aus (damals noch als die zwei Züge nacheinander, heute wäre die untere
Zeile der erste Satz):

| `path` | `dish` | `terms` | `products` | `free_text` | was dastand |
|---|---|---|---|---|---|
| `llm` | Pho | 3 | 3 | 0 | Rinderhack, Rinderknochen, Rinderbrust — das Modell drehte sich 3.358 Zeichen lang im Kreis und wiederholte dieselben drei Begriffe; entdoppelt blieben drei. Alle drei fanden ein Produkt, **keines davon gehört in eine Pho**: „KIKOK Hähnchenbrust mit Knochen", „Mark&Fein BIO Rind Gulasch". |
| `chefkoch` | Pho | 19 | 11 | 8 | Zwiebeln, Zimtstangen, Ingwer, Thai-Basilikum, Mie Nudeln, Rinderfilet, Zitronen, Chilisauce, Fleischknochen, Koriander, Kardamom — und acht ehrliche Freitexte für das, was der Katalog nicht hat (Markknochen, Nelken, Sternanis, Fischsauce, Koriandergrün, Sojasprossen, Frühlingszwiebel, Chilischote). |

**`products` allein ist die falsche Zahl**, und diese Tabelle zeigt genau
warum: der `llm`-Zug hat 3 von 3 „gefunden" und trotzdem nichts Brauchbares
geliefert. Erst `dish` daneben macht die Zeilen vergleichbar, und die
Entscheidungen der Nutzerin (Spec 8.1) machen sie beurteilbar.

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
| `picknick.path` | Text | `llm`, `recipe`, `chefkoch` oder `fanout` |
| `picknick.chat_message_id` | int | die Antwortzeile in `chat_message` |
| `picknick.terms` | int | Begriffe aus Stufe 1 |
| `picknick.products` | int | Vorschläge mit echtem Produkt |
| `picknick.free_text` | int | Vorschläge ohne Produkt (Begriff bleibt als Freitext stehen) |
| `picknick.rejected` | int | **wie oft das Modell eine ID nannte, die ihm nie vorgelegt wurde** |
| `picknick.recipes` | Text | die erkannten Rezepte, nur auf dem Rezeptweg |
| `picknick.weakest_term` | Text | der Begriff mit dem schwächsten besten Treffer |
| `picknick.weakest_rank` | float | dessen Rang; ein Begriff ganz ohne Treffer zählt als `0.0` |
| `picknick.dish` | Text | das erkannte Gericht — **auf jedem Weg**, auch wenn die Zutaten noch geraten wurden (WB-338) |
| `picknick.dish_recipe` | Text | der Rezeptname der Quelle, nur auf `chefkoch` |
| `picknick.dish_url` | Text | die `siteUrl` des Rezepts — die Herkunft, nachvollziehbar |
| `picknick.dish_requested` | bool | dieser Zug hat das Gericht selbst bei Chefkoch geholt, statt es im Speicher zu finden (WB-367) |
| `picknick.dish_fetch` | Text | was der Abruf ergab: `ok`, `leer` (Chefkoch kennt es nicht), `fehler` (Störung/Zeitüberschreitung) |
| `picknick.dish_draft` | Text | der Name des Rezeptentwurfs, den dieser Zug angeboten hat (WB-337) — gesetzt heisst „daraus KANN ein Rezept werden“, nicht „es ist eines geworden“ |
| `picknick.dish_items` | int | wie viele Vorschlagszeilen als Zutat des Gerichts erkannt wurden. Die Zeilen daneben (das Klopapier) zählen hier nicht mit |
| `picknick.rest` | Text | **was neben dem Gericht im Satz stand** (WB-370): „alles für Spaghetti Bolognese, und Klopapier“ -> `Klopapier`. Die andere Hälfte des Satzes zu `dish`; fehlt, wenn nichts danebenstand |
| `picknick.rest_added` | bool | ob dieser Zug eine eigene Zeile dafür angelegt hat. `False` heisst „stand schon auf dem Zettel“ — der Rest fiel mit einer Zutat des Rezepts zusammen |
| `picknick.fanout_category` | Text | die Katalogkategorie einer Auffächerung (WB-368) — **auf beiden Hälften des Umwegs**: auf dem `fanout`-Zug, der die Sorten angeboten hat, und auf dem `llm`-Zug, der eine davon gewählt hat |
| `picknick.fanout_varieties` | int | wie viele Sorten angeboten wurden |
| `picknick.fanout_source` | Text | `catalog` (das getippte Wort IST eine Kategorie — kein Modellaufruf) oder `model` (Stufe 1 hat zugeordnet) |
| `picknick.fanout_rejected` | Text | **die Kategorie, die das Modell nannte, obwohl es sie nicht gibt** — dieselbe Zahl wie `rejected`, eine Ebene höher |
| `picknick.varieties_chosen` | Text | welche Sorten angekreuzt wurden: `Salami, Kochschinken` |

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
| `input.value` | die ganze Begriffskette der Zutat als JSON, vom genauesten zum allgemeinsten: `["Auberginen", "Aubergine"]` |
| `picknick.term` | der genaueste Begriff — er steht für die Zutat, und `weakest_term` meint ihn |
| `picknick.search_terms` | dieselbe Kette lesbar: `Auberginen, Aubergine` |
| `output.value` | `[{id, name, rang, via}, …]` |
| `retrieval.documents.N.document.id` | die Produkt-ID |
| `retrieval.documents.N.document.content` | **was das Modell sah**: Name · Gebinde · Preis |
| `retrieval.documents.N.document.score` | der Rang (siehe unten) |
| `retrieval.documents.N.document.metadata` | `via` (der Begriff der Kette, der diesen Kandidaten brachte), Marke, Kategoriepfad, Preis in Cent, vorrätig — was das Modell NICHT sah, der Mensch beim Nachsehen aber braucht |
| `picknick.candidates` | Zahl der vorgelegten Kandidaten |
| `picknick.rank_top` | bester Rang dieser Suche; **fehlt**, wenn es keinen Treffer gab |
| `picknick.qty` | die Menge, die Stufe 1 zu dieser Zutat nannte |

Die Dokumente stehen in der Reihenfolge, in der sie dem Modell vorlagen: erst
die Treffer des genauesten Begriffs (unter sich nach Wortstufe, dann nach
Rang — WB-339), dann die des
nächsten. **Nicht global nach Score sortiert** — bm25 ist über Abfragen hinweg
nicht geeicht, und ein seltener Begriff („Körnig") schöbe seine Treffer vor die
des eigentlich gemeinten („Mais"). Der Score bleibt trotzdem an jedem Dokument:
er sagt etwas INNERHALB eines Begriffs.

`via` ist die Auskunft, ohne die eine vereinigte Kandidatenliste nicht mehr zu
lesen wäre: „Gemüse-Auberginen-Masala" kam über „Auberginen", „Aubergine,
1 Stk." über „Aubergine". Ohne das Feld stünden beide nebeneinander, und warum
das eine vorlag, wäre nicht mehr zu sagen. Dasselbe Feld steht als
`search_term` an der Vorschlagszeile — dort allerdings nur für den GEWÄHLTEN
Kandidaten (Spec 8.1, WB-329).

`document.content` ist bewusst die Zeile, die dem Modell vorlag, und nicht die
ganze Produktzeile. Sonst zeigte der Trace eine Vorlage, die es nie gab, und
ein Fehlgriff sähe unerklärlicher aus, als er ist.

Bei null Treffern wird `rank_top` **nicht gesetzt**. Eine 0 dort wäre eine
Zahl, die niemand gemessen hat, und sie stünde in jeder Auswertung neben
echten Nullen.

### `korb.menge` — `CHAIN` (WB-362)

Ein Span je Korbposten, **und nur wenn wirklich gerechnet wurde**: ein „+" an
der Kachel legt eine Packung ein und rechnet nichts aus — ein Span, der so
aussähe, als hätte er es getan, wäre eine Behauptung ohne Messung.

Er beantwortet die eine Frage, die man später an einen Warenkorb stellt:
**warum liegen hier zwei Packungen und nicht eine?**

| Attribut | Typ | Bedeutung |
|---|---|---|
| `input.value` | Text | der Produktname |
| `output.value` | Text | der Satz, den die Nutzerin an der Zeile liest: „1000 ml gebraucht — 2 × Pomito 500 g." |
| `picknick.item_id` | int | die Zeile in `order_item` |
| `picknick.product_id` | int | das Produkt — der Schlüssel, über den zusammengezählt wird |
| `picknick.search_term` | Text | der Suchbegriff, über den dieses Produkt in die Liste kam (WB-369); leer, wenn jemand am Regal auf „+" getippt hat |
| `picknick.servings` | int | für wie viele Portionen dieses Einlegen gerechnet hat |
| `picknick.need_added` | float | der Beitrag **dieses** Einlegens, schon skaliert |
| `picknick.need_added_unit` | Text | dessen Einheit, wie das Rezept sie schreibt |
| `picknick.need_amount` | float | die **Summe** an der Zeile, über alle Rezepte |
| `picknick.need_unit` | Text | deren Grundeinheit: `g`, `ml`, `Stk` — oder eine eigene wie `bund` |
| `picknick.pack_text` | Text | die Packungsgrösse, wie sie am Produkt steht: `0,75 l` |
| `picknick.pack_amount` | float | dieselbe in der Grundeinheit: `750` |
| `picknick.pack_unit` | Text | deren Einheit |
| `picknick.hand_qty` | int | wie viele Packungen ausdrücklich verlangt wurden (Griff ins Regal, von Hand gesetzte Menge) |
| `picknick.qty` | int | was am Ende im Korb liegt |
| `picknick.computable` | bool | **liess sich die Packungszahl ausrechnen?** |
| `picknick.packages` | int | die ausgerechnete Packungszahl; **fehlt**, wenn `computable` falsch ist |
| `picknick.reason` | Text | warum nicht: „2 Stk passt nicht zur Packung ‚1 kg'" |
| `picknick.assumption` | Text | die Annahme, unter der gerechnet wurde — bisher genau eine: `1 ml als 1 g gerechnet` |

**`need_added` und `need_amount` stehen beide da, und das ist der Punkt.**
Erst ihr Unterschied macht das Zusammenzählen sichtbar: zwei Züge mit je
`need_added = 40` und danach `need_amount = 80`, `packages = 1` — das ist der
Beweis, dass nach dem Zusammenzählen aufgerundet wurde und nicht davor. Stünde
nur eine der beiden Zahlen da, sähe ein einzelner Span in beiden Welten gleich
aus.

**`computable = false` ist ein eigenes Feld und nicht bloss ein fehlendes
`packages`.** Genau diese Fälle will man in Phoenix suchen: dort stammt die
Zahl im Korb aus einer Vorgabe und nicht aus einer Rechnung, und wenn sich
solche Züge häufen, fehlt dem Katalog oder dem Zerleger etwas. Ein fehlendes
Attribut lässt sich nicht filtern.

**Seit WB-369 ist dieser Span auch der Weg vom CHAT in den Korb.** Vorher
entstand er praktisch nur beim Tipp auf „Alles in den Warenkorb"; heute
entsteht er bei jedem „Ja" auf einen Vorschlag, der aus einem Rezept stammt.
Der ganze Weg steht dann in vier Attributen nebeneinander, und genau dafür
ist `search_term` dazugekommen:

    picknick.search_term  = "gemischtes Hackfleisch"   welche Zutat
    picknick.need_added   = 200                        welche Menge
    picknick.product_id   = 4711                       welches Produkt
    picknick.packages     = 1                          welche Packungszahl

Wer eine falsche Menge im Korb findet, sieht daran, in welchem Schritt sie
entstanden ist: bei einem falschen `search_term` hat Stufe 3 danebengegriffen
(das steht als Label ohnehin an der Zeile), bei einem falschen `need_added`
die Zuordnung Begriff -> Zutat (`assistant.herkunft`), bei einer falschen
`packages` die Packungsgrösse am Produkt (`pack_text` steht daneben).

**Was der Span NICHT sagt: welche Portionszahl gemeint war.** `servings` ist
auf dem Chat-Weg immer leer, weil dort nicht skaliert wird — siehe „Was hier
schwächer ist, als es aussieht".

**`assumption` ist die Ehrlichkeitsspalte.** Milliliter gegen Gramm werden 1:1
gerechnet — für Wässriges stimmt das, für Mehl (1 l wiegt rund 550 g) und Öl
(rund 910 g) nicht. Wer wissen will, wie oft der Shop auf dieser Annahme
steht, filtert danach. Sie steht aus demselben Grund auch in dem Satz, den die
Nutzerin liest: eine Annahme, die man nicht sieht, kann man nicht bestreiten.

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
| `mapping_precision` | ein Score je Zug: behaltene / entschiedene Vorschläge. Kein Label — „0,75" ist die Aussage, eine Textmarke daneben wäre eine Schwelle, die niemand festgelegt hat. Metadaten mit `suggested`, `kept`, `removed`, `open` — und seit WB-361 `withdrawn`: wie oft in diesem Zug eine Entscheidung zurückgenommen wurde. |
| `suggestion` | eine je Vorschlag. Label `kept`/`removed`, Score `1.0`/`0.0`, **Erklärung = der Suchbegriff**, Metadaten mit `search_term`, `product_id`, `rank`, `free_text` — seit WB-359 `fallback_term` sowie, wenn korrigiert wurde, `corrected_to`, und seit WB-361 `withdrawn`. |
| `recipe` | eine je Zug, aus dem ein Rezept werden konnte (WB-337). Label `saved`, `dropped` (sie wollte keines) oder `empty` (es blieb keine bestätigte Zutat übrig), Metadaten mit `dish`, `recipe_name`, `recipe_id` und `items`. Kein Score: „ein Rezept ist entstanden“ ist keine Bewertung eines Vorschlags und hat in keinem Mittelwert etwas zu suchen. |
| `correction` | eine je Korrektur (WB-359). Label `corrected` (aus der Vorlage gewählt) oder `free_text` (nichts passte, Katalog-Lücke), **Erklärung = „X statt Y“**, Metadaten mit beiden Produkten. Kein Score, und ein eigener Name: unter `suggestion` hübe die Korrektur den Mittelwert, den sie erklären soll. |

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

### Warum ein Gericht plötzlich den schnellen Weg nimmt (WB-337)

Ein Zug kann seit WB-337 ein Rezept hinterlassen: aus „alles für Spaghetti
Bolognese, und Klopapier“ wird ein Entwurf mit den GERICHTSZUTATEN — das
Klopapier gehört nicht dazu und steht nie darin. Beim Abschicken wird daraus
ein Rezept, und ab dem nächsten passenden Satz nimmt das Gericht den
Rezeptweg: `picknick.path = "recipe"`, kein `plan.extract`, kein
`plan.choose`, keine Suche, null Token.

Das ist im Trace der auffälligste Sprung, den dieses Projekt kennt — und
ohne zwei Zeilen wäre er unerklärlich. Deshalb steht am Zug, der ihn
angerichtet hat:

```
picknick.path        chefkoch
picknick.dish        Spaghetti Bolognese
picknick.dish_draft  Spaghetti Bolognese      <- daraus KANN ein Rezept werden
picknick.dish_items  3                        <- so viele Zeilen gehören dazu
```

und beim Abschicken, auf demselben Span, die Annotation:

```
name    label   explanation
recipe  saved   aus diesem Zug wurde das Rezept „Bolo“ mit 2 Zutaten —
                ab jetzt nimmt „Spaghetti Bolognese“ den Rezeptweg
```

Die Trennung der beiden ist der Punkt: **`dish_draft` ist eine Möglichkeit,
die Annotation ist die Tatsache.** Zwischen ihnen liegt alles, was die
Nutzerin noch tun darf — Zeilen verwerfen, sie aus dem Entwurf nehmen, das
Rezept umbenennen oder es ganz sein lassen. Ein Attribut, das schon am Zug
„gespeichert“ behauptete, wäre in jedem dritten Fall gelogen.

**Was diese Zahlen nicht sagen:** `dish_items` zählt, was `herkunft.zuordnen`
einer Zutat des Rezepts zuordnen konnte. Ein Begriff, der seiner Zutat nicht
sicher zuzuordnen ist, fehlt hier — gemessen in WB-369 eine von 38
Zuordnungen, in der breiteren Messung zu WB-370 zwölf von 333, nach WB-371
acht von 335 (siehe unten). Die Zahl ist also eine Untergrenze und keine
Zutatenzahl des Gerichts.

### Der Artikel neben dem Gericht (WB-370)

Der teuerste Fehler, den dieser Shop kennt, ist nicht ein falscher Vorschlag,
sondern ein Artikel, der **gar nicht mehr vorkommt**. „alles für Spaghetti
Bolognese, und Klopapier“ nimmt seit WB-367 den Chefkoch-Weg; das Klopapier
ist dort keine Zutat des Rezepts, und bis WB-370 hing es allein daran, ob das
Modell die Prompt-Zeile „Ausserdem gewünscht, nicht aus dem Rezept“ aufgriff.

**Gemessen am 2026-08-28** gegen die echte Box (`Qwen3.8-27B-Instruct`) und
35 Chefkoch-Gerichte, je einmal ohne und einmal mit einem Rest im Satz
(`scripts/rest_probe.py --messen`):

| | |
|---|---|
| Läufe mit mindestens einem Begriff **ohne** Herkunftszutat | 11 von 35 (31 %) |
| Begriffe ohne Herkunftszutat | 12 von 333 (3,6 %) |
| der Rest kam als Begriff zurück | 3 von 35 |
| der Rest kam **übersetzt** zurück | 0 von 35 |
| der Rest ging durch den blinden Fleck still verloren | 10 von 35 (29 %) |

Ein zweiter Lauf am selben Tag ergab dieselben Zahlen bei 331 statt 333
Begriffen — die Box ist bei Temperatur 0 nicht bitgenau deterministisch
(siehe `plan.MAX_TOKENS`). Die Quoten sind also auf ein Prozent genau und
nicht auf eine Nachkommastelle.

Damit fielen beide Hälften der Begründung aus WB-337 auf einmal. Das
Sicherheitsnetz („kein Begriff ohne Herkunftszutat -> der Rest wurde
übergangen -> anhängen“) greift in jedem dritten Zug daneben, weil das Modell
sehr wohl Begriffe liefert, die keiner Zutat zuzuordnen sind — sieben der
zwölf sind „Eier“ gegen Chefkochs Schreibweise „Ei(er)“. Und die Übersetzung
„Klopapier“ -> „Toilettenpapier“, für die die Prompt-Zeile dastand, lieferte
die Box **kein einziges Mal**.

Seit WB-370 steht der Rest nicht mehr im Prompt und wird im Code angehängt.
Was im Trace zu sehen ist:

```
picknick.path        chefkoch
picknick.dish        Quiche Lorraine
picknick.rest        Klopapier          <- was daneben im Satz stand
picknick.rest_added  true               <- dieser Zug hat die Zeile angelegt
picknick.free_text   1                  <- und sie fand kein Katalogprodukt
```

**Der Preis steht in derselben Zeile.** Ohne Prompt-Zeile gibt es keine
Übersetzung mehr; „Klopapier“ findet die Präfixsuche nicht (siehe unten), also
bleibt eine Freitext-Zeile. Das ist der bewusste Handel: eine sichtbare
Freitext-Zeile gegen einen Artikel, der still verschwindet. Die Antwort sagt
es auch — „„Klopapier“ stand daneben im Satz und liegt als eigene Zeile dabei“
—, weil ein Trace-Attribut niemandem im Laden hilft.

`rest_added = false` heisst nicht „übergangen“, sondern „stand schon da“: der
Rest fiel mit einer Zutat des Rezepts zusammen („alles für Lasagne und
Tomaten“). Verglichen wird dafür mit demselben Wortvergleich wie bei der
Herkunft (`herkunft.punkte`), nicht mit einer Vermutung über das Modell.

### „Eier“ findet „Ei(er)“ (WB-371)

Sieben der zwölf Begriffe ohne Herkunftszutat oben waren derselbe Fehler, und
er saß in einer Zeile: `herkunft._woerter` faltete Umlaute und trennte am
Bindestrich, ließ aber **Klammern, Kommas und Akzente stehen**. Chefkoch
schreibt die Mehrzahl aber genau so — „Ei(er)“, „Zwiebel(n)“, „Limette(n)“ —,
und damit stand dort ein einziges Wort „ei(er)“, das den Begriff „Eier“ des
Modells nicht traf:

    punkte('Eier',     'Ei(er)')     = 0.0     _woerter('Ei(er)') = ['ei(er)']
    punkte('Porree',   'Porrée')     = 0.0
    punkte('Limetten', 'Limette(n)') = 0.0

Seit WB-371 fallen die Klammerzeichen weg (statt zu trennen: „ei“ und „er“
träfe „Eier“ ebenso wenig, „ei“ ist kürzer als `herkunft.MIN_WORT`), Komma
und Semikolon trennen wie der Bindestrich, und die Akzente fallen wie die
Umlaute. Alle drei Zeilen stehen jetzt auf `1.0`.

**Gemessen am 2026-08-29**, wieder gegen die echte Box
(`Qwen3.8-27B-Instruct`) und dieselben 35 Gerichte, `rest_probe.py --messen`
vorher und nachher:

| | vorher | nachher |
|---|---|---|
| Läufe mit mindestens einem Begriff ohne Herkunftszutat | 10 von 35 (29 %) | **6 von 35 (17 %)** |
| Begriffe ohne Herkunftszutat | 11 von 331 (3 %) | **8 von 335 (2 %)** |

Die Begriffszahl schwankt zwischen zwei Läufen (331 gegen 335), weil die Box
bei Temperatur 0 nicht bitgenau ist — dieselbe Einschränkung wie oben. Damit
sind zwei Läufe kein Beleg dafür, **was genau** sich geändert hat. Deshalb
wurde Stufe 1 einmal eingefangen und die Zuordnung auf **derselben**
Modellausgabe zweimal gerechnet, mit und ohne den Fix:

    Begriffe 328   ohne Herkunftszutat: 13  ->  8

    + Apfelkuchen       Eier -> Ei(er)   4 Stk
    + Bibimbap          Eier -> Ei(er)   2 Stk
    + Griesbrei         Eier -> Ei(er)   1 Stk
    + Moussaka          Eier -> Ei(er)   4 Stk
    + Quiche Lorraine   Eier -> Ei(er)   4 Stk   (1 + 3, zusammengezählt)
    + Zwiebelkuchen     Eier -> Ei(er)   2 Stk
    - Chili con Carne   „Tomaten > Tomate“ -> jetzt Gleichstand, keine Menge

Sechs neue Zuordnungen, jede von Hand gegen das Rezept geprüft, **keine
falsche**. Die eine verlorene ist kein Rückschritt, sondern die Zusicherung
aus WB-369 bei der Arbeit: das Rezept nennt „Tomate(n)“ zweimal (2 Dosen und
100 ml), das Modell macht daraus zwei Ketten („Dose Tomaten“ und „Tomaten“),
und beide passen nun gleich gut. Vorher gewann eine davon nur deshalb, weil
die Klammer die andere aussperrte — eine Zuordnung, die aus einem Fehler kam.
Eine Menge trug die Zeile in beiden Fällen nicht: Dosen und Milliliter lassen
sich nicht zusammenzählen.

**Was übrig bleibt, ist nicht mehr diese Ursache.** Die verbleibenden acht
sind Gleichstände und Synonyme: „Eier“ gegen ein Rezept, das nur Eigelb und
Eiweiß kennt (Kaiserschmarrn); „Zwiebel“ neben der Kette
„Frühlingszwiebel > Zwiebel“, deren allgemeines Glied dieselbe Zutat trifft;
„rote Paprika“ und „gelbe Paprika“, die beide über „Paprikaschote“ auf beide
Schoten zeigen. Alle drei sind der Preis der Regel „bei Gleichstand keine
Menge“ und keine Schreibweise.

**Fehlzuordnungen wurden getrennt gezählt**, ohne Modell und damit
wiederholbar: jede Zutat der 35 Rezepte liefert über `chefkoch.zutat_kette`
ihre eigene Begriffskette, und jede Kette muss wieder auf genau die Zutat
zeigen, aus der sie stammt.

    441 Ketten   richtig 434   FALSCH 0   ohne Zuordnung 7

Vor und nach dem Fix dieselbe Zahl. Der Fix macht diese Messung nicht besser
— die Ketten kommen aus `zutat_kette`, das die Klammer ohnehin schon abwirft
— aber er macht sie auch nicht schlechter, und das war die Frage.

**`db.UMLAUTE` wurde bewusst nicht angefasst.** Dort zu falten wäre die
naheliegende Stelle, aber die Ersetzungen stehen als geschachteltes SQL in
`norm_name`/`norm_cat` und damit in einem **materialisierten** FTS-Index
(`product_fts`, `content='product'`). Ein Zeichen mehr, und alte Zeilen sind
anders gefaltet als neue — die Suche fände stillschweigend weniger, bis der
Index neu gebaut ist. Die Herkunftszuordnung sucht gar nicht im Katalog, sie
vergleicht zwei Namen desselben Rezepts; deshalb faltet sie selbst
(`herkunft._falte`).

### Aus einem negativen Label wird ein positives (WB-359)

`removed` sagt „das war falsch“. Das ist die halbe Auskunft. Seit „Nein“ die
aufgehobenen Kandidaten aufklappt und ein Tipp einen davon statt des
Vorschlags in den Korb legt, steht die andere Hälfte daneben — **welcher
Kandidat statt welchem**. Handprobe vom 2026-08-28 gegen die echte Box
(`Qwen3.8-27B-Instruct`) und den echten Katalog (10.361 Produkte), Satz „ich
brauche Butter, Schmand und Sellerie“, zurückgelesen aus Phoenix:

```
name               identifier                label      explanation
suggestion         picknick-suggestion-98    removed    Butter — stattdessen: „Kerrygold irische Butter gesalzen“
suggestion         picknick-suggestion-99    kept       Schmand
suggestion         picknick-suggestion-100   removed    Sellerie — stattdessen: „Sellerie“
correction         picknick-correction-101   corrected  „Butter“: statt „Weihenstephan Butter“ -> „Kerrygold irische Butter gesalzen“
correction         picknick-correction-102   free_text  „Sellerie“ war falsch; nichts aus der Vorlage passte — von Hand: „Sellerie“
mapping_precision  picknick-turn-34          —          1 von 3 entschiedenen Vorschlägen behalten.
```

Zwei Dinge daran sind Absicht und keine Kosmetik:

* **Die Korrektur zählt nicht in `mapping_precision`.** Die Quote steht auf
  0,33 und nicht auf 0,5 — die Korrekturzeile ist kein Vorschlag des Modells,
  sondern die Handbewegung danach. Mitgezählt hübe ausgerechnet ein Fehlgriff
  die Zahl, sobald ihn jemand geradezieht.
* **`corrected` und `free_text` sind zwei verschiedene Befunde.** Das erste
  heisst „aus der Vorlage hätte das Modell das Richtige nehmen können“ — ein
  Modellfehler. Das zweite heisst „in der Vorlage stand es gar nicht“ — eine
  Katalog-Lücke, und die ist keinem Modell anzulasten. Unter einem Label wären
  die beiden nicht mehr zu trennen.

### Der Fehltipp, den es nie gegeben hat (WB-361)

Auf dem Telefon sitzen „Ja" und „Nein" nebeneinander, und danebentippen
passiert. Seit WB-361 lässt sich **jede** Entscheidung zurücknehmen — „Ja",
„Nein" und auch eine Korrektur. Das ist Datenqualität und keine Bequemlichkeit:
ein Fehltipp verfälscht sonst genau die Zahlen, die dieses Projekt interessant
machen.

Für Phoenix folgen daraus zwei Dinge, und sie ziehen in verschiedene
Richtungen:

* **Ein zurückgenommener Tipp hinterlässt KEIN Label.** Er kann keines
  hinterlassen: geschrieben wird erst beim Abschicken (Regel 1 unten), und wer
  zurückgenommen hat, steht dann auf `offen` — und `offen` bekommt nichts
  (Regel 2). Genau deshalb kostet ein Rückweg vorher nichts. Ein Test hält es
  fest (`tests/test_labels.py`), denn es ist eine Eigenschaft der Reihenfolge
  und keine Zeile Code, die man beim Umbau stehen sieht.
* **Sichtbar sein muss er trotzdem** — sonst sähe später niemand, wie oft
  danebengetippt wird. Dafür steht `withdrawn` in den Metadaten: an der
  einzelnen `suggestion` die Rücknahmen an DIESER Zeile, an
  `mapping_precision` die Summe für den Zug, und dort zusätzlich in der
  Erklärung:

```
identifier              label     score  explanation
picknick-suggestion-77  kept       1,0   Butter                       withdrawn = 1
picknick-turn-31        —          0,5   1 von 2 entschiedenen Vorschlägen behalten; 1 Entscheidung zurückgenommen.
```

Ein `kept` mit `withdrawn = 1` ist ein anderer Datenpunkt als ein `kept` beim
ersten Hinsehen: dort hat jemand gezögert oder danebengetippt. Ein LABEL ist
die Rücknahme trotzdem nicht — „zurückgenommen" ist kein Urteil über den
Vorschlag, und als drittes Label neben `kept`/`removed` verdürbe es die
Mapping-Präzision, die genau zwei Ausgänge kennt.

Die Zahl steht in der Datenbank (`chat_suggestion.zurueckgenommen`) und nicht
am Span: die Rücknahme passiert Minuten nach dem Zug, und der `chat.turn`-Span
ist da längst geschlossen. Ein Span-Attribut liesse sich nachträglich nicht
mehr setzen — genau dafür sind Annotationen da.

### Ein Treffer, der nur über den allgemeinsten Begriff kam

Der Befund stammt aus WB-358 (Kassenbons) und gilt im Chat genauso: findet
kein genauer Begriff etwas, greift der allgemeinste — und der findet **immer
irgendetwas**. Nachgemessen am echten Katalog:

```
Kette                                     Kandidaten          erster Treffer
[Old Amsterdam, Amsterdamer Käse, Bier]   15 × via „Bier“     Singha Bier (EINWEG)
[Geflügelrolle, Rolle]                     8 × via „Rolle“    Prinzen Rolle Choco Duo
```

Kommen ALLE Kandidaten einer Zutat vom letzten Kettenglied, steht das als
`chat_suggestion.fallback_term` an der Zeile: in der Oberfläche als „nur über
den allgemeinen Begriff ‚Bier‘ gefunden“, in der Annotation als Metadatum und
in der Erklärung. Das ist bewusst die enge Regel. „Der GEWÄHLTE Kandidat kam
über das letzte Glied“ wäre zu laut, weil das letzte Glied oft ein legitimes
Synonym ist („Möhren“, „Karotten“) — die Kette [Geflügelrolle, Geflügel,
Rolle] wird deshalb NICHT markiert, denn „Geflügel“ hat selbst Treffer
geliefert. Die enge Regel meldet weniger, aber was sie meldet, stimmt.

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
* **Ein Zug, in dem am Ende ALLES offen steht, verschwindet ganz** — auch
  seine Rücknahmen. `withdrawn` hängt an der `mapping_precision`-Annotation,
  und die entsteht nur, wenn wenigstens eine Entscheidung stehen blieb (Regel
  3). Wer „Ja" tippt, zurücknimmt und dann die Seite verlässt, hinterlässt
  nichts. Das ist die Kehrseite davon, dass eine fehlende Zahl ehrlicher ist
  als eine erfundene; wer die Fehltipp-Häufigkeit wirklich messen will, muss
  sie in `chat_suggestion.zurueckgenommen` zählen und nicht in Phoenix.
* **Die Portionszahl aus dem Chat gibt es nicht** (WB-369, Regel 5). „alles
  für Lasagne für 6" nimmt Chefkochs eigene `servings` und skaliert nichts;
  `picknick.servings` bleibt auf dem Chat-Weg leer. Das ist eine Entscheidung
  und kein Versehen: welche Zahl in einem Satz die Portionszahl ist, liesse
  sich nur raten, und eine falsche Portionszahl multipliziert jede Menge im
  Korb. Die Zahlen dafür fehlen ausserdem — in den 35 echten Nutzersätzen der
  Datenbank (2026-08-28) kommt **keine einzige Ziffer** vor. Wer skalieren
  will, tippt am Rezept auf „Alles in den Warenkorb", dort steht das Feld
  (WB-362).
* **Die Zuordnung Begriff -> Zutat kann eine Menge verlieren** (WB-369). Sie
  vergleicht Wörter (`assistant.herkunft`) und erkennt ein Synonym des
  Modells nicht: nennt es „Möhren" als „Karotten", findet die Zeile ihre
  Zutat nicht und trägt keine Menge — sie verhält sich dann wie vor WB-369.
  Gemessen an den drei echten Chefkoch-Zügen der Datenbank fanden 37 von 38
  Begriffen ihre Zutat, **keiner die falsche**. Die Richtung ist Absicht: ein
  verlorener Bedarf kostet eine Packung zu viel, ein falsch zugeordneter
  Bedarf eine falsche Menge, die aussieht wie eine gerechnete.

  **Breiter gemessen ist die Lücke grösser als diese eine Zuordnung** (WB-370,
  2026-08-28, 35 Chefkoch-Gerichte, 333 Begriffe): 12 Begriffe fanden ihre
  Zutat nicht, verteilt auf 11 der 35 Läufe. Sieben davon sind derselbe Fall
  — **„Eier“ gegen Chefkochs „Ei(er)“**. `herkunft._woerter` faltet Umlaute
  und trennt am Bindestrich, lässt Klammern und Kommas aber stehen; „ei(er)“
  ist deshalb ein anderes Wort als „eier“, und dieselbe Klammer trifft
  „Limette(n)“. Zwei weitere sind „rote/gelbe Paprika“ gegen
  „Paprikaschote(n), rote“, einer ist „Porrée“ gegen „Porree“ (der Akzent
  steht nicht in `db.UMLAUTE`), einer ist ein echter Zusatz des Modells
  („Dose Tomaten“). Die Folge ist keine falsche Menge, sondern eine fehlende
  — und eine Zeile, die im Rezeptentwurf fehlt, obwohl sie eine Zutat ist.
  Das ist ein eigener Befund und kein Teil von WB-370; dort machte er nur
  sichtbar, dass sich auf „kein Begriff ohne Herkunftszutat“ nichts bauen
  lässt.
* **Ein Rezept entsteht nur aus dem Chefkoch-Weg** (WB-337). Ein `llm`-Zug
  bekommt keinen Entwurf, auch wenn er ein Gericht erkannt hat: dort rät das
  Modell die Zutaten, und es gibt keine Liste, gegen die sich prüfen liesse.
  Wer in Phoenix zählt, wie oft ein Zug ein Rezept hinterlässt, misst also
  auch, wie oft Chefkoch das Gericht kennt — und nicht nur, wie brauchbar der
  Zug war. Die beiden Fragen trennt `picknick.path`.
* **`dish_items` ist eine Untergrenze.** Die Zahl kommt aus derselben
  Wortzuordnung wie die Mengen (`assistant.herkunft`) und hat dieselbe Lücke:
  ein Begriff, der seiner Zutat nicht sicher zuzuordnen ist, fehlt im Entwurf
  und damit im Rezept. Die Richtung ist Absicht — eine fehlende Zutat lässt
  sich am Rezept nachtragen, eine falsche fällt niemandem mehr auf —, aber
  „3 Zutaten“ heisst nicht „das Gericht hat 3 Zutaten“.
* **`document.score` ist über Traces hinweg nicht vergleichbar**, weil er vom
  Katalogumfang abhängt. Zwei Züge vor und nach einem Crawl haben andere
  Zahlen bei gleichem Verhalten.
