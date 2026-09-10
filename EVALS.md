# Dataset, Evaluatoren, Experiments

Zwölf feste Anfragen, drei Evaluatoren, vier Varianten desselben Agenten. Alle
Zahlen unten sind gemessen — am 2026-08-28, gegen `Qwen3.8-27B-Instruct` auf
der lokalen vLLM-Box und den echten Katalog (2.498 Produkte). Was daran
schwach ist, steht am Ende und ist nicht kurz.

Dazu kommt seit dem 2026-08-29 die **Breitenmessung des ganzen Rezeptwegs**
über 64 Gerichte (WB-380, `scripts/breite_probe.py`) — sie steht weiter unten
und misst etwas anderes: nicht vier Varianten an zwölf Anfragen, sondern
einen Zug an vielen Gerichten, bis auf die Einkaufsliste.

## Nachfahren

```bash
.venv/bin/python evals/dataset.py --pruefen    # ohne Phoenix, ohne Box
.venv/bin/python evals/dataset.py              # legt/aktualisiert das Dataset
.venv/bin/python evals/experiment.py --liste
.venv/bin/python evals/experiment.py basis prompt-b kandidaten-20 ohne-guided
```

Die Läufe brauchen ein laufendes Phoenix **und** die wache Box.
`checks/smoke.py` und `pytest` brauchen beides nicht: alles, was hier rein
rechnet, steht in Funktionen ohne Netz und wird in `tests/test_evals.py` genau
so geprüft.

Jeder Lauf arbeitet auf einer **Kopie** von `data/picknick.db` (`VACUUM INTO`).
Ein Chat-Zug schreibt Nachrichten und Vorschläge in den Warenkorb; vierzig
Züge über vier Varianten hätten den echten Warenkorb der beiden mit
Testrauschen gefüllt — und die Annotationen aus `OBSERVABILITY.md` gleich mit.

`--dry-run` fährt ein einzelnes Beispiel und speichert nichts in Phoenix.
`--ohne-judge` lässt den LLM-Judge weg; die deterministischen Scores stehen
dann trotzdem vollständig da.

## Das Dataset `zettel-anfragen`

> Hiess bis zum 30.08.2026 `picknick-anfragen` (WB-401, siehe README). In
> Phoenix ist der Name der Schlüssel: `python -m evals.dataset` legt unter dem
> neuen Namen ein **neues** Dataset an, das alte bleibt daneben stehen — samt
> aller Experimente, die darauf gelaufen sind. Die Zahlen weiter unten in
> diesem Dokument sind auf `picknick-anfragen` gemessen worden; dort sind sie
> nachzuschlagen. Die zwölf Beispiele sind dieselben, das Dataset wird aus
> `evals/dataset.py` erzeugt und nicht von Hand gepflegt.

Zwölf Beispiele, vier Sorten zu je drei:

| Sorte | Beispiel | Ermessen? |
|---|---|---|
| gewöhnlich | „alles für Spaghetti Bolognese" | nein |
| gewöhnlich | „Butter, Frischkäse und Naturjoghurt fürs Frühstück" | nein |
| gewöhnlich | „ich koche eine Tomatensauce selbst — was muss ich kaufen?" | nein |
| mehrdeutig | „Milch" | ja |
| mehrdeutig | „Joghurt" | ja |
| mehrdeutig | „Käse für die Nudeln" | ja |
| gemischt | „Klopapier und Spülmittel" | nein |
| gemischt | „Toilettenpapier, Spülmittel und Butter" | nein |
| gemischt | „wir haben kein Klopapier mehr und die Milch ist alle" | nein |
| Falle | „etwas Süßes" | ja |
| Falle | „irgendwas zum Frühstück" | ja |
| Falle | „was zum Knabbern für den Filmabend" | ja |

**Erwartet wird auf Kategorieebene, nie auf Produkt-ID.** Ein Dataset, das für
„passierte Tomaten" die ID 64 verlangt, ist beim nächsten Crawl ungültig —
Knuspr vergibt die IDs, ein ausgelistetes Produkt nimmt die Erwartung mit ins
Grab. Erwartet wird `Konserven & Eingelegtes > Tomaten`; das überlebt jeden
Crawl, in dem es diese Warengruppe noch gibt. Und wenn nicht, sagt
`dataset.py --pruefen` es laut, statt dass die Eval still auf 0 fällt.

Was **nicht** drinsteht: „Toastbrot" wäre ein hübsches drittes
Frühstücksbeispiel und findet im echten Katalog null Produkte — der Toast
heisst „Harry Butter Toast", und die Suche kennt nur Wortanfänge. Ein Beispiel,
das daran scheitert, misst die Suche und nicht den Agenten. „Klopapier" bleibt
drin, obwohl es genauso null Treffer hat: dort ist das Umformulieren in
„Toilettenpapier" ausdrücklich die Aufgabe des Modells, weil Stufe 1 den
Katalog nie sieht. Der Unterschied ist die Absicht.

## Die drei Evaluatoren

### `zutaten_vollstaendigkeit` — Recall

Für jede erwartete Pflichtzutat: liegt **irgendein** vorgeschlagenes Produkt in
einer ihrer Kategorien? Ohne Rücksicht darauf, unter welchem Begriff es
gefunden wurde — gesucht wird die Vollständigkeit des Einkaufs, nicht die
Sauberkeit der Zuordnung.

Dieser Score existiert wegen eines konkreten Befunds. Die Handprobe gegen die
echte Box lieferte für „alles für Spaghetti Bolognese" in Stufe 1:

```
Spaghetti, Hackfleisch, passierte Tomaten, Klopapier
```

**Zwiebeln und Knoblauch fehlten.** Jeder gelieferte Begriff fand ein Produkt,
die Präzision war makellos — und die Sauce wird trotzdem nichts. Gemerkt hätte
man es im Laden. Keine Präzisionsmetrik zeigt das.

Jedes Produkt wird **höchstens einer** Zutat zugeordnet (maximale Paarung im
zweiseitigen Graphen, Kuhns Erweiterungspfad). Ohne diese Bedingung misst der
Recall genau dort zu gut, wo es weh tut: `Zwiebeln` und `Knoblauch` liegen im
echten Katalog beide in `Gemüse > Zwiebeln & Knoblauch`, und ein einziges Netz
Zwiebeln zählte sonst für beide.

Freitext-Vorschläge zählen nicht: „Zwiebeln" als Zeile, die die Nutzerin selbst
suchen muss, ist kein gelieferter Einkauf.

### `kategorie_praezision` — Präzision

Jeder Produktvorschlag wird über seinen **Suchbegriff** einer erwarteten Zutat
zugeordnet und muss dann in deren Kategorien liegen. Der Spaghettilöffel aus
`Haushaltsartikel > Küchenhelfer` — im echten Katalog der bestplatzierte
Treffer für „Spaghetti" — ist damit ein Fehlgriff und keine Auslegungssache.

Was sich keiner Zutat zuordnen lässt, ist bei einem Gericht erlaubter Beifang
(Parmesan zur Bolognese) und bleibt ungezählt; bei einer abgezählten
Einkaufsliste wird es gegen alle erwarteten Kategorien geprüft. Ein falsch
genommener Rezeptweg ist ein hartes `0.0` — „Klopapier und Spülmittel" aus der
Rezeptsammlung zu beantworten wäre auch dann falsch, wenn zufällig passende
Produkte herauskämen.

### `llm_urteil` — der Judge, und nur wo nötig

Nur für Beispiele mit `metadata.ermessen` (die sechs oben mit „ja"). „Käse für
die Nudeln" ist mit einem Blauschimmelkäse kategorial richtig und praktisch
falsch; das kann keine Kategorieprüfung entscheiden. Bei den anderen sechs
gibt der Judge **ausdrücklich keine Zahl** ab, statt eine zu erfinden.

Drei Urteile, nicht zwei: `passt` = 1,0, `teilweise` = 0,5, `passt_nicht` =
0,0. Er sieht die erwarteten Kategorien nicht, nur eine Beschreibung in
Prosa — bekäme er die Kategorien, prüfte er dasselbe wie
`kategorie_praezision`, nur teurer und unzuverlässiger.

Eine unlesbare Antwort ergibt **keinen** Score, sondern das Label `unlesbar`.
Eine 0,0 stünde in Phoenix neben den echten Nullen und sähe aus wie ein Agent,
der versagt hat, obwohl der Richter gestottert hat.

### Was ausdrücklich NICHT gemessen wird

**`zettel.weakest_rank`.** Der bm25-Rang steht am Span und ist verlockend,
aber über Abfragen hinweg nicht geeicht (siehe `OBSERVABILITY.md`). Eine
Schwelle darauf misst den Katalog und nicht den Agenten — und sie kippte jedes
Mal, wenn der Crawler etwas Neues einsammelt.

## Die vier Varianten

Je **eine** Stellschraube gegen die Basis verschoben. Mehr als eine wäre kein
Experiment, sondern ein neuer Agent.

| Variante | Änderung |
|---|---|
| `basis` | Prompt A, 5 Kandidaten, `guided_json` an — der Betriebsstand |
| `prompt-b` | Prompt B für `plan.extract`: Vollständigkeit vor Kürze |
| `kandidaten-20` | 20 statt 5 vorgelegte Kandidaten je Begriff |
| `ohne-guided` | ohne `guided_json` — trägt der Prompt allein? |

Seit WB-338 gibt es eine fünfte Stellschraube, die **noch nicht gefahren
ist**: `quelle` gegen `gedaechtnis` — dieselbe Anfrage einmal mit gefülltem
Gerichtespeicher (`zettel.path = chefkoch`) und einmal ohne
(`zettel.path = llm`). Der Trace trägt dafür alles Nötige (`zettel.dish`
steht auf beiden Wegen, siehe `OBSERVABILITY.md`), und die Handprobe
(`scripts/gericht_probe.py`) zeigt den Unterschied schon von Hand: bei „alles
für Pho" nannte das Modell drei Rindfleischteile, von denen keiner in eine
Pho gehört, die Quelle 19 Begriffe mit 11 Katalogtreffern. **Eine Handprobe
ist keine Evaluation** — was fehlt, ist das Dataset dazu und die Frage, ob
`zutaten_vollstaendigkeit` an einem geholten Rezept überhaupt dasselbe misst
(die erwarteten Zutaten stünden dann aus derselben Quelle wie die
gemessenen).

Spec 8.3 nennt als dritte Stellschraube „lokales Qwen gegen ein grösseres
Modell". **Diese Variante ist nicht fahrbar**: auf der Box liegt genau ein
Modell, und ein Zukauf über eine fremde API ist ausdrücklich abgelehnt. Ein
Vergleich, für den es keinen zweiten Lauf gibt, wird hier nicht behauptet und
schon gar nicht mit Zahlen ausgestattet. An seine Stelle tritt `ohne-guided`.

## Die Zahlen

Lauf vom 2026-08-28, 07:47–07:50, Dataset-Version 4, je 12 von 12 Beispielen
erfolgreich, **eine Wiederholung**:

| Score | basis | prompt-b | kandidaten-20 | ohne-guided |
|---|---|---|---|---|
| `zutaten_vollstaendigkeit` (n=12) | 0,611 | **0,750** | 0,611 | 0,611 |
| `kategorie_praezision` (n=10) | 0,850 | **0,960** | 0,850 | 0,850 |
| `llm_urteil` (n=6) | 0,417 | **0,500** | 0,417 | 0,417 |

`n` ist die Zahl der Beispiele, die überhaupt einen Score bekamen. Bei der
Präzision sind es zehn: zwei Fallen liefern gar kein Produkt, das sich
bewerten liesse, und bekommen deshalb keine Zahl statt einer Null.

### Was diese Tabelle sagt

**Drei von vier Varianten sind Zeile für Zeile identisch.** `kandidaten-20`
und `ohne-guided` haben gegenüber der Basis in keinem der drei Scores etwas
bewegt. Das ist ein Ergebnis, kein kaputter Lauf:

* **`ohne-guided`:** alle zwölf Ausgaben waren wortgleich, mit und ohne Schema,
  bis auf die Produkt-ID. Bei `temperature = 0` und einem Modell, das sich
  ohnehin an das Format hält, ist die Einschränkung nie bindend. `guided_json`
  ist hier eine Versicherung gegen ein *anderes* Modell und keine Verbesserung
  dieses einen.
* **`kandidaten-20`:** vier statt fünf Kandidaten mehr vorzulegen ändert die
  Wahl nicht. Das Modell greift ohnehin nach dem, was oben steht — und was
  oben steht, entscheidet die Suche. Genau die Aussage des Butter-Falls, noch
  einmal von der anderen Seite.

**Prompt B bewegt etwas, und der Grund ist nicht nur der erwartete.** Der
Unterschied bei der Präzision (0,850 → 0,960) kommt aus genau zwei Beispielen:

```
mehrdeutig-kaese-nudeln   „Käse für die Nudeln“
  basis     Begriffe: Käse, Nudeln    -> Allgäuer Käse + Kritharaki-Nudeln   0,50
  prompt-b  Begriffe: Käse            -> Allgäuer Käse                       1,00

falle-knabbern            „was zum Knabbern für den Filmabend“
  basis     Begriffe: Snacks          -> Alnatura BIO Dinkel Butterkekse
                                         [Babynahrung > Baby & Kleinkind]    0,00
  prompt-b  Popcorn, Nüsse, Chips, Käse, Wurst -> 3 von 5 passend            0,60
```

Der zweite Fall ist ein echter Gewinn: aus einem Begriff werden fünf, und die
Falle „was zum Knabbern" wird beantwortet statt in die Babynahrung zu greifen.

Der erste ist **keiner.** Prompt B gewinnt dort, indem er die Nudeln
**weglässt** — die Anfrage war „Käse für die Nudeln", und wer nur den Käse
liefert, hat eine perfekte Präzision und ein unvollständiges Ergebnis. Die
Präzision belohnt hier das Schweigen. Dass die Vollständigkeit derselben Zeile
1,0 bleibt, liegt daran, dass das Dataset für dieses Beispiel nur den Käse als
Pflichtzutat führt.

Der Gewinn bei der Vollständigkeit (0,611 → 0,750) ist dagegen sauber: er
kommt aus „ich koche eine Tomatensauce selbst" — Basis lieferte nur `Tomaten`
(1 von 3), Prompt B `Tomaten, Zwiebeln, Knoblauch, Basilikum` (3 von 3). Das
ist genau der Befund, gegen den Prompt B geschrieben wurde.

## Die Breitenmessung des Rezeptwegs (WB-380)

Das oben ist der MODELLWEG an zwölf Anfragen. Das hier ist der **ganze
Rezeptweg an 64 Gerichten** — Satz → Zutat → Suchbegriff → Produkt →
Korbposten → Einkaufsliste, zum ersten Mal in der Breite:

```bash
sqlite3 data/picknick.db "VACUUM INTO 'kopie.db'"
.venv/bin/python scripts/breite_probe.py --messen --db kopie.db --json roh.json
.venv/bin/python scripts/breite_probe.py --achsen     # nur die Gerichtsliste
```

Lauf vom **2026-08-29**, `Qwen3.8-27B-Instruct`, 10.361 aktive Produkte,
sechs Züge parallel, 2,9 min Chefkoch (seriell, mit Pause) und 6,6 min
Messung. **Kein Lauf ist gescheitert.**

### Die Gerichte sind gestreut, nicht gesammelt

64 Gerichte über zwölf Achsen. Nicht „viele Gerichte", sondern Gerichte, die
verschiedene Dinge kaputtmachen können — eine lange Liste ähnlicher Gerichte
wäre die schlechtere Messung gewesen.

| Achse | n | Beg | Kat | Quote | Zeilen mit Menge |
|---|--:|--:|--:|--:|--:|
| Backen | 6 | 44 | 40 | **91 %** | 84 % |
| vegetarisch/vegan | 6 | 62 | 55 | 89 % | 84 % |
| zählbare Einheiten | 6 | 45 | 40 | 89 % | 84 % |
| Alltagsküche | 8 | 76 | 62 | 82 % | 75 % |
| international, im Katalog | 7 | 56 | 46 | 82 % | 71 % |
| Einwort-Gericht | 6 | 56 | 44 | 79 % | 73 % |
| Schreibweise/Tippfehler | 6 | 46 | 34 | 74 % | 65 % |
| Fantasiename | 3 | 7 | 5 | 71 % | **0 %** |
| mehrdeutig | 6 | 57 | 37 | 65 % | 65 % |
| international, exotisch | 7 | 84 | 48 | **57 %** | **51 %** |
| bereits gespeichert | 3 | — | 19 | — | 76 % |
| *mit Zusatzartikel (quer)* | 8 | 67 | 59 | 88 % | 58 % |

Der Rezeptweg hat keine Begriffe (seine Produkte stehen schon im Rezept) und
deshalb keine Katalogquote.

Der Zusatzartikel liegt quer über die Achsen — „alles für X, und Klopapier"
ist kein eigenes Gericht, sondern ein Zusatz auf acht von ihnen. Er kam
achtmal von acht im Korb an. Die Probe zählt nur sieben, und das ist ein
Fehler IHRER Zählung und nicht des Shops: bei „Glibberschmarrn" lief der
Modellweg, und der übersetzte „Klopapier" zu „Toilettenpapier" — im Korb
liegt „Moddia Toilettenpapier 3-lagig", der Wortvergleich der Probe erkennt
es nicht. Die Ausgabe nennt den Fall deshalb namentlich, statt eine zu
niedrige Zahl unwidersprochen stehen zu lassen.

### Die Zahlen über alle 64

```
Wege                    58× chefkoch, 3× llm, 3× recipe
Chefkoch (Phase A)      54× ok, 7× aus dem Speicher, 3× „kennt es nicht“
Katalogtreffer          411 von 533 Begriffen (77 %)
Freitext                122 von 533 (23 %)
Rezeptentwurf           58 von 64 Zügen (91 %), zusammen 512 Zutaten
Zusatzartikel im Korb   8 von 8 (7 wörtlich, 1 übersetzt — siehe unten)
Dauer je Gericht        Median 34 s, 2 s bis 71 s
Auffächerung            0 von 64 — sie springt bei keinem Einwort-Gericht an
```

**Die Verteilung, nicht der Mittelwert.** 79 % im Schnitt heisst hier wenig:

```
Katalogtreffer je Gericht        Zeilen mit Menge je Gericht
  0– 20 %  ████ 4                  0– 20 %  ██████ 6
 20– 40 %  █ 1                    20– 40 %  ██ 2
 40– 60 %  █████ 5                40– 60 %  ███████████ 11
 60– 80 %  ██████████████████ 18  60– 80 %  █████████████████ 17
 80–100 %  █████████████ 33       80–100 %  ████████████ 28
 Median 83 %, Spanne 0–100 %      Median 79 %, Spanne 0–100 %
```

Zwei Drittel der Gerichte liegen über 80 %, und vier liegen bei null. Der
Mittelwert liegt dazwischen und beschreibt keines von beiden.

### Die Mengen bis auf die Einkaufsliste

Die Frage des Nutzers war: „Schau vor allem, ob die Mengenangaben beim
Einkauf drin sind."

```
558 Vorschlagszeilen  ->  558 Korbposten  ->  558 Einkaufslistenzeilen
                                               davon 394 mit Menge (71 %)
```

**Keine einzige Zeile geht verloren.** 164 Mengen schon, und sie verteilen
sich sauber auf zwei Ursachen und keine dritte:

| Glied | Zahl | Was da passiert |
|---|--:|---|
| 1 · Die Quelle nennt keine Menge | 119 von 732 Zutaten (16 %) | „Salz und Pfeffer n. B." — Chefkochs `0.0` ist keine Menge, und sie durchzureichen ergäbe eine gekaufte Packung für nichts |
| 2 · Die Zuordnung findet die Zutat nicht | 136 von 613 (22 %) | WB-369/WB-371. Nach oben verzerrt: fallen zwei Zutaten auf einen Begriff, zählt eine hier als verloren, obwohl ihre Menge in der Summe steckt |
| 3 · Die Einheit ist nicht rechenbar | 328 von 485 Zeilen (68 %) | WB-362. **Die Menge steht trotzdem auf der Liste** — nur die Packungszahl ist eine 1 statt einer Rechnung |
| 4 · Etwas dazwischen verliert sie | **115 Zeilen** | `orders.korb.einlegen`: ein Posten ohne Produkt bekommt keine Menge. Der Vorschlag zeigt „500 g gebraucht", die Einkaufsliste zeigt nichts |
| Korbposten → Einkaufsliste | **375 → 375** | kein Verlust. Die Anzeige aus WB-381 gibt weiter, was dasteht |

Glied 4 ist der einzige echte Bruch, und er ist ausgerechnet dort, wo die
Menge am meisten wert wäre: eine Zutat, die der Katalog nicht führt, muss
irgendwo anders besorgt werden — und genau dann steht im Laden nicht mehr,
wie viel. 115 Zeilen in 37 der 64 Gerichte. Die Rechnung geht exakt auf:
485 Vorschläge mit Menge − 375 Korbposten − 110 Freitext = 0 auf dem
Chefkoch-Weg. Es gibt keinen unerklärten Verlust.

> **Nachtrag, 2026-08-29 (WB-385): Glied 4 ist behoben.** `korb.einlegen()`
> behält die Menge eines Freitextpostens; eine Packungszahl wird daraus
> weiterhin nicht gerechnet. Die Zahlen in dieser Tabelle sind die des Laufs
> vom 2026-08-29 und bleiben stehen, wie sie gemessen wurden — **neu gemessen
> wurde nicht**, das kostet einen vollen Lauf gegen Netz und Modell. Was
> geprüft ist: der Weg vom Rezept bis auf die Einkaufsliste trägt die Menge
> jetzt (`checks/smoke.py`, Abschnitt Portionen; `tests/test_portionen.py`).
>
> **Zweiter Nachtrag, 2026-08-30 (WB-393): jetzt ist neu gemessen.** Der
> Referenzlauf weiter unten ist der volle Lauf, den der erste Nachtrag noch
> schuldig blieb. Glied 4 steht dort auf **null**: 481 Vorschlagszeilen mit
> Menge → 481 Korbposten → 481 Listenzeilen, darunter 85 Freitextzeilen, die
> ihre Menge vorher genau hier verloren hätten. Der Anteil der
> Einkaufslistenzeilen mit Menge steigt damit von 71 % auf **92 %**. Die
> 115 Zeilen in der Tabelle oben sind kein offener Befund mehr, sondern die
> Messung des Zustands vor WB-385 (Lauf 08:36, Reparatur 09:00).

**Glied 3 ist grösser als gedacht und trifft etwas anderes als vermutet.**
Von den Mengen, die es auf die Liste schaffen, sind nur 157 von 485 gegen die
Packung rechenbar. Die Einheiten dahinter:

```
rechenbar        g 110, ml 39, Stk 13
nicht rechenbar  Stk 95, EL 40, TL 16, Pck 11, Scheibe 8, Zehe 8,
                 Stange 7, Bund 6, Becher 5, Dose 5, …
```

WB-362 nannte „4 Zehen gegen 100 g" als den Fall. Zehe kommt achtmal vor.
Der grosse Fall ist **„2 Zwiebeln" gegen „1 kg Netz"** — Stück gegen Gramm,
95-mal — und danach der Esslöffel.

### Die zehn schlechtesten Gerichte, namentlich

| Gericht | Note | Grund |
|---|--:|---|
| Salat | 0,00 | 9 von 9 ohne Katalogprodukt, keine Menge auf der Liste |
| Kartoffelpürree | 0,00 | 11 von 11 ohne Katalogprodukt |
| Bibimbap | 0,00 | 8 von 8 ohne Katalogprodukt — **Ausreisser**, einzeln nachgefahren 7 von 8 |
| Schrumpelfrikandel | 0,00 | Fantasiename: Chefkoch kennt nichts, das Modell nennt „Frikadellen", der Katalog findet nichts |
| Kartoffelsalat | 0,50 | Rezeptweg; 5 von 10 Zeilen sind Freitext und verlieren die Menge (Glied 4) |
| Okonomiyaki | 0,62 | 5 von 8 ohne Katalogprodukt: Bonitoflocken, Nori, Okonomiyaki-Sauce |
| Zwuckelpfanne mit Gnubbeln | 0,80 | Fantasiename — das Modell **erfindet** ein Gericht und legt vier Produkte in den Korb |
| Gnocchi mit Salbeibutter | 0,83 | Eigelb, Grieß und Salbei fehlen im Katalog |
| Ratatouille | 0,91 | Rezeptweg, eine Freitextzeile ohne Menge |
| Glibberschmarrn | 1,00 | Fantasiename; im Korb liegt nur das Klopapier daneben |

Die Note ist Katalogquote plus Mengenquote, höchstens 2,0. Ein Fehler wäre
−1, es gab keinen.

**Salat und Kartoffelpürree haben denselben, reproduzierbaren Grund, und er
ist der interessanteste Befund dieser Messung: Stufe 3 wählt gegen den SATZ,
nicht gegen das Rezept.** „Salat" holt bei Chefkoch „KFC Coleslaw",
„Kartoffelpürree" holt „Schweinefilet auf Süßkartoffelpüree mit Lebkuchenjus
und Rosenkohl". Passen Satz und Rezept nicht zusammen, lehnt das Modell
**jeden einzelnen** Kandidaten ab — auch Milch, Butter und Zwiebeln, die
vorgelegt dastanden. Es ist kein Ausrutschen, sondern ein Alles-oder-nichts.

Die Kontrollzeile im Dataset schien zu zeigen, dass schon der Satzbau die
Hälfte davon ausmacht:

```
„Salat"                   -> 0 von 9 Begriffen mit Produkt
„alles für Salat"         -> 6 von 9      (dasselbe Rezept, derselbe Katalog)
```

> **Nachtrag, 2026-08-29 (WB-386): der Satzbau war es nicht.** An
> eingefrorenen Kandidaten gemessen liefern „Salat" und „alles für Salat" im
> Prompt von Stufe 3 **dieselbe** Zahl (6 von 9, dreimal gleich). Der
> Unterschied dieser Kontrollzeile lag also nicht an Stufe 3, sondern davor:
> die beiden Sätze holten verschiedene Begriffe und damit verschiedene
> Kandidaten. Die Diagnose „Stufe 3 wählt gegen den Satz" bleibt richtig, die
> Erklärung über den Satzbau war falsch. Der nächste Abschnitt misst es.

### Was diese Messung NICHT sagt

* **Sie misst den warmen Speicher.** Phase A holt jedes Gericht vorher bei
  Chefkoch, seriell und mit Pause. Gemessen ist damit der zweite und jeder
  weitere Satz zu einem Gericht, nicht der allererste (der holt im Request,
  WB-367, ~100 ms).
* **Sie sagt „hat ein Produkt gefunden", nicht „hat das richtige gefunden".**
  Dieselbe Warnung wie oben. Der Fantasiename „Zwuckelpfanne mit Gnubbeln"
  hat 4 von 5 Begriffen im Katalog — und das Gericht gibt es nicht.
* **„Ja" auf jede Zeile.** Das ist die günstigste Annahme für die Mengen: wer
  Zeilen wegtippt, bekommt weniger. Die 71 % sind eine Obergrenze.
* **Ein Lauf, keine Wiederholung.** Bibimbap zeigt, was das wert ist: dasselbe
  Gericht stand einmal auf 0 und einmal auf 7 von 8. Bei 64 Gerichten fällt
  ein Ausreisser auf; bei drei fiele er nicht auf.
* **Der Rezeptweg musste erst hergestellt werden.** Keines der fünf Rezepte
  der echten Datenbank hat verknüpfte Produkte (`recipe_item` ist überall
  leer), weil alle aus Chefkoch stammen — `rezeptweg.erkenne` übergeht sie,
  der Weg `recipe` ist auf dieser Datenbank gar nicht erreichbar. Die Probe
  legt ihn deshalb vorher an, auf dem Weg, auf dem er im Betrieb entsteht:
  ein Chefkoch-Zug, „Ja", abschicken (WB-337). **Das ist selbst ein Befund**:
  der schnellste und verlässlichste Weg des Shops wird heute von niemandem
  benutzt.
* **Keine Zielquote.** Diese Messung war die erste; eine Schwelle vorher
  gesetzt wäre geraten gewesen.

## Der Satz im Prompt von Stufe 3 (WB-386)

WB-380 fand drei Gerichte, die **alles** verloren: Salat 0 von 9,
Kartoffelpürree 0 von 11, Königsberger Klopse 0 von 16 Begriffen mit einem
Katalogprodukt. Nicht wenig — nichts. Die Vermutung dort: Stufe 3 wählt gegen
den Satz statt gegen das Rezept, und passen die beiden nicht zusammen, lehnt
das Modell auch Milch und Butter ab.

**Die Vermutung stimmte im Ergebnis und nicht in der Begründung.** Gemessen
wurde sie mit einer zweiten Probe, die den Rest des Zuges festhält:

```bash
.venv/bin/python scripts/satz_probe.py --einfrieren --db kopie.db \
    --nach vorlagen.json          # Rezept, Stufe 1, Suche — einmal, echt
.venv/bin/python scripts/satz_probe.py --messen --aus vorlagen.json --wdh 3
```

Eingefroren wird genau das, was Stufe 3 vorgelegt bekommt: Begriffe, Mengen
und Kandidaten. Danach ändert sich **nur noch ein String im Prompt**. Ohne
das Einfrieren misst ein Vergleich zweier Sätze auch zwei Zutatenlisten, zwei
Begriffslisten und zwei Kandidatenmengen mit.

### Zehn Vorlagen, sechs Prompt-Varianten, je dreimal

Median gewählter Begriffe, 2026-08-29, `Qwen3.8-27B-Instruct`. Jede Zeile ist
eine eingefrorene Vorlage, jede Spalte eine Fassung desselben Prompts.
`satz` ist der Stand vor diesem Ticket.

| Vorlage | Beg | satz | satz_ganz | ohne_satz | regel | rezept | rezept_satz |
|---|--:|--:|--:|--:|--:|--:|--:|
| Salat | 9 | 6 | 6 | **8** | 8 | 7 | 7 |
| Kartoffelpürree | 10 | **0** | **0** | **10** | 10 | 10 | **0** |
| Auflauf | 8 | 8 | 8 | 8 | 8 | 8 | 8 |
| Suppe | 11 | 10 | 10 | 10 | 10 | 10 | 10 |
| Eintopf | 11 | 10 | 10 | 10 | 10 | 10 | 10 |
| alles für Salat | 9 | 6 | 6 | **8** | 6 | 7 | 7 |
| alles für Königsberger Klopse | 15 | 14 | 14 | 13 | 14 | 11 | 14 |
| alles für Lasagne | 13 | 13 | 13 | 13 | 13 | 13 | 13 |
| alles für Chili con Carne | 10 | 8 | 8 | 8 | 8 | 8 | 8 |
| alles für Apfelkuchen | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| **Summe** | **102** | **81** | **81** | **94** | 93 | 90 | 83 |

Die Varianten: `satz` wie bisher („Anfrage: Salat"), `satz_ganz` derselbe
Satz als ganzer Satz, `ohne_satz` gar keine Anfragezeile, `regel` der Satz
plus eine Zeile in der Anweisung („Milch bleibt Milch"), `rezept` der
Rezeptname **statt** der Anfrage, `rezept_satz` beides.

**Drei Läufe je Feld, dreimal dieselbe Zahl — in jedem einzelnen Feld ausser
einem.** Auf eingefrorener Vorlage ist die Box bei Temperatur 0
reproduzierbar; das Rauschen aus WB-380 (Bibimbap 0 gegen 7) sass also nicht
in Stufe 3.

### Was daraus folgt

* **Der Satz ist die Ursache, nicht der Widerspruch zum Rezept.**
  Kartoffelpürree: 0 von 10 mit Satz, 10 von 10 ohne — dieselben Kandidaten,
  dieselben Begriffe, ein String Unterschied.
* **Den Rezeptnamen danebenzustellen hilft nicht.** `rezept_satz` steht bei
  Kartoffelpürree wieder auf 0. Solange der Satz dasteht, prüft das Modell
  gegen ihn; mehr Kontext übertönt ihn nicht.
* **Der Satzbau ist es nicht.** `satz` und `satz_ganz` sind in allen zehn
  Zeilen gleich. Die Kontrollzeile aus WB-380 („Salat" 0, „alles für Salat" 6)
  misst also einen Unterschied **vor** Stufe 3 — verschiedene Begriffe,
  verschiedene Kandidaten.
* **Eine Regelzeile reicht fast, aber nicht ganz.** `regel` rettet
  Kartoffelpürree und lässt „alles für Salat" auf 6 stehen. Eine Zeile mehr
  Prompt für ein schlechteres Ergebnis ist kein Handel.
* **Königsberger Klopse ist der Gegenfall, den die Vermutung nicht erklärt.**
  Satz und Rezept passen dort perfekt zusammen — und im Lauf stand das
  Gericht trotzdem auf 0 von 16. Auf eingefrorener Vorlage steht es auf 14
  von 15. Der Alles-oder-nichts-Ausfall hängt also am Satz im Prompt, nicht
  daran, ob er zum Rezept passt.

### Geändert wurde eine Zeile

Auf dem **Rezeptweg** bekommt Stufe 3 keinen Satz mehr
(`chat.Chat._aus_quelle`). Der Modellweg behält ihn: dort kommen die Begriffe
aus dem Satz, er ist der einzige Kontext, den die Stufe hat („Milch" ist nicht
entscheidbar, „Milch für den Kaffee" schon) — und für diesen Weg liegt keine
Messung vor, also wurde er nicht angefasst. Die gewählte Sorte (WB-368)
behält ihren eigenen Prompt.

**Die Zusicherung der Stufe ist unberührt:** gewählt wird nur aus den
vorgelegten Kandidaten, eine nicht vorgelegte ID wird verworfen und nicht
repariert (`tests/test_gerichte.py::test_auch_ohne_satz_wird_nur_vorgelegtes_gewaehlt`).

### Vorher und nachher, über alle 64 Gerichte

Zweimal `scripts/breite_probe.py --messen` auf derselben vorgewärmten Kopie,
2026-08-29, dazwischen nur diese Änderung:

```
                                  vorher      nachher
Begriffe mit Katalogprodukt       412/541     446/540      76 % -> 83 %
Freitext                          129         94           24 % -> 17 %
Quote je Gericht, Median          83 %        89 %
Quote je Gericht, Mittel          79 %        84 %
Gerichte unter 50 %               5           1
Gerichte bei 0 %                  4           1
Gerichte mit Fehler               0           0
```

Je Gericht: **13 besser, 10 schlechter, 38 gleich.** Die Verbesserungen sind
gross, die Verschlechterungen sind je ein Begriff:

```
+12  Königsberger Klopse       0/16 -> 12/16
+10  Kartoffelpürree           0/11 -> 10/11
 +8  Salat                      0/9 ->   8/9
 +3  Okonomiyaki                3/8 ->   6/8
 +2  Gnocchi mit Salbeibutter   3/6 ->   5/6
 +2  Massaman Curry            7/11 ->  9/11
 +2  Salat als ganzer Satz       6/9 ->  8/9
 -1  Käse-Lauch-Suppe            6/6 ->  5/6
 -1  Petersilienpesto            6/6 ->  5/6
 -1  Knoblauchsuppe              6/8 ->  5/8
 …   sechs weitere mit -1
```

Und die Abnahme des Tickets, wörtlich:

```
„Salat"            0 von 9  ->  8 von 9
„alles für Salat"  6 von 9  ->  8 von 9
```

Beide Sätze liefern jetzt dieselbe Zahl. Das war der Punkt: ein schiefes
Rezept darf ein schiefes Rezept sein, aber es darf nicht die Zutatenliste
mitreissen.

### Was diese Messung NICHT sagt

* **Der Modellweg ist ungemessen.** Alle zehn Vorlagen sind Rezeptwege. Ob
  der Satz dort trägt, was ihm zugeschrieben wird („Milch für den Kaffee"),
  ist eine Behauptung aus WB-340 und steht weiter unbewiesen da — nur wurde
  sie hier auch nicht angetastet.
* **Zehn Vorlagen sind keine 64.** Die Prompt-Varianten sind an zehn
  eingefrorenen Vorlagen entschieden, die Breitenmessung prüft danach nur die
  gewählte. `regel` und `rezept` über alle 64 zu fahren hätte zwei weitere
  Läufe gekostet.
* **Die zehn Gerichte mit −1 sind nicht einzeln nachgesehen.** Ob dort ein
  Begriff wirklich am fehlenden Satz hängt oder an einer anderen Zutatenliste
  aus Stufe 1 (die von Lauf zu Lauf schwankt), ist offen. Die Summe trägt die
  Entscheidung, die einzelne Zeile nicht.
* **Ein Lauf je Stand.** Wie in WB-380: 64 Gerichte fangen einen Ausreisser
  auf, ein einzelnes Gericht nicht.

## Ein zweites Modell durch denselben Harness (WB-393)

Die Frage war nicht „wird ein Finetune besser", sondern eine billigere:
**was kostet es, ein anderes Modell zu beurteilen, wenn der Harness schon
steht?** Antwort bis hierher: einen Nachmittag und keine Zeile
Produktionscode. Gemessen wird mit demselben `scripts/breite_probe.py
--messen`, denselben 64 Gerichten, derselben DB-Kopie-Disziplin — getauscht
wird nur, was auf der Box liegt.

Jeder Lauf tract in ein **eigenes Phoenix-Projekt** (`--trace` plus
`ZETTEL_PHOENIX_PROJECT`), damit das Alltagsprojekt `Zettel Agent` nicht
sechzig Messgerichte zwischen den echten Einkäufen stehen hat; welches Modell
geantwortet hat, steht innerhalb des Projekts an `llm.model_name` (WB-395).

### Lauf 1 — `Qwen3.8-27B-Instruct`, 2026-08-30

Die Referenz, frisch gefahren statt aus dem 29.08. zitiert: seither hat
WB-397 `assistant/vorschlaege.py` angefasst, und das ist der Weg, über den
die Probe „Ja" auf jede Zeile sagt (`alle_entscheiden`). Eine Referenz, die
einen anderen Codestand misst als der Vergleichslauf, wäre keine.

```bash
ZETTEL_PHOENIX_PROJECT="Zettel Eval Qwen" \
.venv/bin/python scripts/breite_probe.py --messen --db kopie.db \
    --json roh.json --trace
```

```
64 Gerichte, 64 gelaufen, 0 mit Fehler, 7,0 min (Phase B, sechs parallel)
Wege                    58× chefkoch, 3× llm, 3× recipe
Katalogtreffer          448 von 541 Begriffen (83 %), Freitext 93 (17 %)
Quote je Gericht         Median 89 %, Mittel 84 %, Spanne 0–100 %
Gerichte bei 0 %         1 (Schrumpelfrikandel, der Fantasiename)
Rezeptentwurf           58 von 64 Zügen (91 %), 511 Zutaten
Zusatzartikel im Korb   8 von 8 (7 wörtlich, 1 zu „Toilettenpapier“ übersetzt)
Zeilen im Laden         524, davon 481 mit Menge (92 %)
Dauer je Gericht        Median 39 s, 5 s bis 71 s
```

**Der Stand von WB-386 ist reproduziert**, mit einem anderen Codestand und an
einem anderen Tag: 83 % gegen 83 %, Median 89 % gegen 89 %, ein Gericht bei
null gegen eines. Die Katalogquote hängt also nicht am Tagesrauschen der Box.
Neu ist allein die Mengenzahl — 92 % statt 71 % —, und das ist keine
Verbesserung des Modells, sondern die erste Breitenmessung von WB-385 (siehe
den Nachtrag oben).

**Der erste Anlauf ist gescheitert, und das steht hier, weil ein
verschwundener Lauf schlechter ist als ein gescheiterter.** 64 von 64
Gerichten fielen in denselben Fehler — `ChatNichtVerfuegbar: Die Box
antwortet, bedient aber noch nicht (ReadTimeout)` —, alle innerhalb von 36 s
beim Start von Phase B. `/v1/models` war unmittelbar davor und danach in
0,7 s da, ein Kontrolllauf über sechs Gerichte mit derselben Parallelität lief
sauber durch. Es war ein Aussetzer der Box und kein Befund über sie; er
kostete drei Minuten Chefkoch-Vorlauf und keine Zahl. In Phoenix stehen die
64 Fehler-Spans weiter im selben Projekt — **ohne** `llm.model_name`, genau
wie OBSERVABILITY.md es zusichert: ein Zug ohne Antwort bekommt keinen
geratenen Modellnamen. Von den 1.356 Spans des Projekts tragen 127 einen, und
alle 127 denselben.

### Lauf 2 — `Llama-3.1-Nemotron-Nano-8B`, 2026-08-30

Der Wechsel ist am 30.08. zwischen 02:06 und 02:18 Uhr passiert — die
Box-Session hat `nvidia/Llama-3.1-Nemotron-Nano-8B-v1` (BF16, 15 GiB, reines
LM, 32k Kontext) serviert, den Idle-Stop der Box für das Fenster ausgesetzt
und Qwen danach wiederhergestellt (Werkbank WB-394). Die Messung lief direkt
zwischen den beiden Sitzungen; dieser Abschnitt stand deshalb bis zum 04.09.
auf „offen", obwohl die Zahlen seit dem 30.08. im Ticket WB-393 lagen.
**Nachgerechnet am 04.09. aus den Phoenix-Spans** der beiden Projekte
(`Zettel Eval Qwen`, `Zettel Eval Nemotron`; je `chat.turn`-Span das Paar
`terms`/`products` unter `attributes.picknick`), nicht aus dem Ticket
abgeschrieben:

```
                                   Qwen3.8-27B (AWQ)   Nemotron-Nano-8B (BF16)
Züge mit Begriffen                       64 von 67             63 von 67
Katalogtreffer, Mittel je Gericht           84 %                  25 %
Katalogtreffer, Median je Gericht           88 %                   0 %
Gerichte bei 0 %                             1                    41
Gerichte im Band 0–20 %                      1                    42
Gerichte im Band 80–100 %                   43                    10
Begriffe gesamt / gefunden               564 / 463             399 / 42
zettel.rejected gesamt                       6                     2
Dauer je Zug, Median (Spanne)           37 s (2–69)           21 s (7–61)
```

(Das Ticket nennt für Qwen „Median 89 %" und „40 von 64 im Band 0–20 %" für
Nano — dieselbe Messung, aus der Probenausgabe statt aus den Spans gerechnet;
die Abweichung um einen Punkt bzw. ein Gericht kommt daher, dass hier vier
Züge ohne Begriffe herausfallen und das Verhältnis je Span gebildet wird.)

**Der Befund:** Nemotron-Nano 8B ist auf diesem Weg fast unbrauchbar — nicht
„etwas schlechter", sondern bei ganzen Gerichten vollständig daneben:
Ratatouille 0 von 13, Kartoffelsalat 0 von 5, Broccoliauflauf 0 von 4, wo
Qwen dieselben Gerichte zu 80–100 % trifft. Die halbierte Dauer ist kein
Vorteil, sondern die Folge: es liefert weniger, was der Katalog findet.
**Guided JSON lief fehlerfrei** (`rejected` 2, kein Formatfehler) — es ist
die Auswahl selbst, nicht das Format. Beide Läufe unter identischen
Bedingungen (ein gedrosselter Download lief bei beiden durch), 0 Fehler.

**Was dieser Lauf NICHT sagt:** nichts über NVIDIAs aktuelle Generation.
Das 8B-Nano ist ein Llama-3.1-Abkömmling von Anfang 2025. Dafür gibt es
Lauf 3.

### Lauf 3 — `NVIDIA-Nemotron-3.5-Lightning-30B-A3B` (W4A16), 2026-09-05

Die aktuelle Generation: hybrides MoE, 30B gesamt, 3B aktiv, erschienen am
10.08.2026; als `useful-quants/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-W4A16`
(compressed-tensors, 16,6 GiB) auf der RTX 3090 serviert, vLLM 0.27.1,
32k Kontext, `--reasoning-parser nemotron_v3`, Denken je Anfrage aus. Die
Box-Session hat das Modell über Nacht geladen und um 08:02 serviert, den
Idle-Stop fürs Fenster ausgesetzt (Serverseite laut Box-Session: KV-Pool
767.317 Token, `gpu-memory-utilization 0.94`, kein Neustart im Fenster, keine
Spekulation); gemessen 08:05–08:09 vom Laptop aus,
dasselbe Kommando wie oben mit `ZETTEL_PHOENIX_PROJECT="Zettel Eval
Nemotron 3.5"`. Rohdaten: `evals/breite_probe-2026-09-05-nemotron35.json`
(+ `.provenienz.json`).

```
                                   Qwen3.8-27B (AWQ)   Nemotron-Nano-8B   Nemotron 3.5 Lightning
                                       30.08. / 01.09.        30.08.              05.09.
Katalogtreffer der Begriffe           83 % / 84 %            11 %                 82 %  (457 von 557)
Katalogtreffer, Mittel je Gericht     84 % / 84 %            25 %                 85 %
Katalogtreffer, Median je Gericht     88 % / 88 %             0 %                 88 %
Gerichte bei 0 %                       1 / 1                 41                    0   (eins bei 14 %: der Fantasiename)
Gerichte im Band 80–100 %             43 / 43                10                   42
Rezeptentwurf entstanden              58 von 64             —                    58 von 64
Zusatzartikel im Korb                  8 von 8              —                     6 von 8
Zeilen im Laden mit Menge             92 %                  —                    91 % (Median 100 %)
zettel.rejected gesamt                 6 / —                  2                    2
Dauer je Zug, Median                  37 s / 7 s            21 s                  6 s  (0–12 s)
ganzer Lauf, Phase B                   7,0 min / —           —                    1,1 min
```

(Qwen zweimal: 30.08. auf vLLM 0.24 mit wirksamem Guided Decoding, 01.09.
auf 0.27.1 ohne — siehe den Nachtrag unten. Nemotron 3.5 lief unter den
Bedingungen des 01.09.)

**Der Befund:** Nemotron 3.5 Lightning ist auf diesem Weg **auf Augenhöhe
mit dem 27B-Referenzmodell** — 85 % / 88 % gegen 84 % / 88 %, dieselbe
Zahl der Gerichte über 80 %, kein Gericht bei null — bei Median 6 s je Zug.
**Die Dauer ist kein sauberer Modellvergleich:** die Qwen-Fassung im
syv-Container läuft mit spekulativem Decoding (DFlash2, laut Box-Session
`SPEC=dflash2`), die Nemotron-Instanz lief ohne; 6 s gegen 7 s stellt also
auch nicht-spekulativ gegen spekulativ. Gegen den 30.08.-Lauf (0.24, ohne
Spekulation, 37 s) ist der Abstand sechsfach — und ein 3B-aktives MoE ist
ohne jede Spekulation so schnell wie das dichte 27B mit. Die Box-Session hat
das im Fenster nachgemessen (Einzelstrom, Denken aus, ~27-Token-Prompt, 300
und 800 Token Antwort): **Nemotron 219–222 tok/s ohne Spekulation**, die
Qwen-Fassung laut ihrem Drop-in 226 tok/s mit DFlash2 — die
Generierungsrate ist also praktisch dieselbe, der Unterschied je Zug liegt
eher in der Zahl der erzeugten Token. (Mein eigener Qwen-Wert von 70 tok/s
vom 04.09. gilt für einen 5,1k-Prompt; die Zahlen sind nicht gegeneinander
zu stellen.) Spekulationsgewichte bringt der Nemotron-Build nicht mit
(`num_nextn_predict_layers: 0`). Was schwächer ist,
steht daneben: der Zusatzartikel („… und Klopapier") kam in 6 von 8 Zügen
mit (Qwen 8 von 8) — bei Frikadellen und Glibberschmarrn fehlte er; und
„Käse Lauch Suppe" fand nur 1 von 7 Begriffen, wo Qwen 5 von 7 fand. Das
sind zwei Gerichte von 64 und keine Aussage über das Modell; eine
Wiederholung würde sie einordnen (siehe „Was diese Messung NICHT sagt").

**Guided JSON:** der Lauf ist mit dem alten `guided_json`-Feld gefahren,
das vLLM 0.27.1 ignoriert (Nachtrag unten) — unbeschränkt also, wie der
Qwen-Lauf vom 01.09., und mit `rejected = 2` genauso formtreu. Denken war
je Anfrage aus; mit Denken landet Nemotrons Überlegung im Feld `reasoning`
und schneidet bei kleinem `max_tokens` die Antwort ab (`content: null`,
`finish_reason=length`) — gemessen, nicht vermutet.

Die Antwort auf die Eingangsfrage bleibt: ein zweites Modell zu beurteilen
kostet einen Nachmittag und keine Zeile Produktionscode — und das Ergebnis
kann eine Absage sein (Lauf 2) oder ein gleichwertiger, schnellerer Ersatz
(Lauf 3), und beides steht hier gleich.

### Nachtrag 2026-09-05 — `guided_json` war auf 0.27.1 vier Tage lang aus

Gemessen gegen die Box mit einem Prompt, der Prosa verlangt: ohne Zwang
Prosa, mit `guided_json` in `extra_body` **ebenfalls Prosa** (HTTP 200,
keine Warnung), mit `response_format: json_schema` JSON nach Schema. Die Box
lief seit dem 01.09. auf 0.27.1; Stufe 1 und 3 waren dort also unbeschränkt,
und der Lauf vom 01.09. zeigt es: 84 % / 88 %, ein Gericht bei null —
dieselben Zahlen wie beschränkt. Das ist die Breitenbestätigung dessen, was
die Tiefenmessung oben (`ohne-guided`) schon sagte: bei Temperatur 0 bindet
die Einschränkung nie. Der Agent sendet das Schema seit `6f0447b` als
`response_format`; der Befund kam von der Box-Session, die Zahl von hier.

## 128 Gerichte (2026-09-05)

Die Liste in `scripts/breite_probe.py` ist am 05.09. von 64 auf **128
Gerichte** gewachsen — dieselben elf Achsen im selben Verhältnis, je Achse
das, was die erste Hälfte noch nicht abdeckte (Kohlrouladen und Zwiebelkuchen
neben den Braten; Coq au Vin, Rendang, Ceviche; Grünkern-Bratlinge; Brezeln
und Vollkornbrot; Eier in Senfsoße und ein Kopf Blumenkohl; „Curry", „Bowl",
„Pfanne" nackt mit der Kontrolle „alles für Curry"; Spaghetti/Spagetti
Carbonara als zweites Schreibweisen-Paar; drei neue Fantasienamen; drei neue
gespeicherte). 16 Gerichte tragen einen Zusatzartikel statt 8, mit acht
neuen Wörtern (Müllbeutel, Backpapier, Küchenrolle, Spülschwamm, Duschgel,
Batterien, Kaffeefilter, noch einmal Klopapier). Die 64er-Läufe oben bleiben,
was sie sind; ab hier gilt 128.

### Lauf 4 — `Qwen3.8-27B-Instruct`, 128 Gerichte, 2026-09-05 09:41

Box wie am 01.09. (syv-Container, vLLM 0.27.1, DFlash2), Schema seit
`6f0447b` als `response_format`. Rohdaten
`evals/breite_probe-2026-09-05-qwen-128.json` (+ `.provenienz.json`),
Traces im Projekt `Zettel Eval Qwen 128`. Aus den Spans nachgerechnet
(134 `chat.turn`, 128 mit Begriffen — die sechs gespeicherten laufen zweimal):

```
128 Gerichte, 128 gelaufen, 0 Fehler, 7,4 min (Phase B, sechs parallel)
Chefkoch (Phase A)       117× ok, 8× aus dem Speicher, 3× leer (die Fantasienamen)
Wege                     119× chefkoch, 6× recipe, 3× llm
Katalogtreffer           962 von 1.107 Begriffen (87 %)   [Probenausgabe: 911 von 1.048, 87 %]
Quote je Gericht         Mittel 89 %, Median 91 %, Spanne 43–100 %
Gerichte unter 40 %      0 — kein Gericht bei null
Gerichte 80–100 %        104 von 128
zettel.rejected gesamt   1
Rezeptentwurf            119 von 128 (93 %), 1.001 Zutaten
Zusatzartikel im Korb    13 von 16 (81 %) — dreimal fehlte „Klopapier"
Zeilen im Laden          Mengenquote Mittel 90 %, Median 100 %
Dauer je Gericht         Median 20 s, 0 s bis 42 s
```

(Die Probenausgabe zählt Begriffe je Gericht und lässt die zweiten Züge der
gespeicherten Gerichte weg; die Spans zählen jeden Zug. Die Quote ist in
beiden Rechnungen 87 %.)

**Nach Achse** (Katalogtreffer der Begriffe): Schreibweise/Tippfehler 93 %,
mehrdeutig 91 %, international im Katalog 91 %, Fantasiename 91 %,
zählbar 89 %, Backen 88 %, Alltagsküche 87 %, vegetarisch 87 %,
Einwort 81 %, **international exotisch 78 %** — die Katalogluecke bleibt
die exotische Küche (Ceviche 3 von 7, Tom Kha Gai 8 von 13, Pad Thai 6
von 10).

**Was gegenüber 64 anders ist:** die Quote steigt von 83 % auf 87 %, und
das ist zuerst eine Aussage über die *zweite Hälfte*: sie ist katalognäher
gewählt (mehr Alltag und Backen, weniger Vietnam) — die 64 alten Gerichte
allein lägen weiter bei ihren 83 %. Zweitens tauchen die Fantasienamen
diesmal bei 91 % auf statt bei null: ohne Rezept rät das Modell Zutaten,
und was es rät, findet der Katalog oder nicht — das ist der Zufall eines
einzelnen Laufs, kein Fortschritt. Beides steht hier, damit die 87 % nicht
als „vier Punkte besser" gelesen werden.

### Lauf 5 — Nemotron 3.5 Lightning, 128 Gerichte, 2026-09-05 10:35

Box-Session: Nemotron serviert seit 10:35 (W4A16, `vllm-model serve
--runtime 027`, vLLM 0.27.1, 32k, ohne Spekulation), Wächter aus seit
10:34. Dasselbe Kommando, Projekt `Zettel Eval Nemotron 3.5 128`, Rohdaten
`evals/breite_probe-2026-09-05-nemotron35-128.json` (+ `.provenienz.json`).
Aus den Spans nachgerechnet (134 `chat.turn`, 128 mit Begriffen):

```
                               Qwen3.8-27B (AWQ)     Nemotron 3.5 Lightning (W4A16)
                               Lauf 4, 09:41         Lauf 5, 10:35
Gerichte / Fehler                  128 / 0                128 / 0
Katalogtreffer der Begriffe   962 von 1.107 (87 %)   997 von 1.167 (85 %)
Quote je Gericht, Mittel           89 %                   87 %
Quote je Gericht, Median           91 %                   89 %
Gerichte unter 40 %                 0                      1  (Schrumpelfrikandel, 0 von 2 geratenen Begriffen)
Gerichte 80–100 %                 104                     93
zettel.rejected gesamt              1                      1
Rezeptentwurf                 119 von 128            119 von 128
Zusatzartikel im Korb          13 von 16              13 von 16
Zeilen im Laden mit Menge      Mittel 90 %             Mittel 92 %
Dauer je Zug, Median          20 s (0–42)             6 s (0–12)
Phase B gesamt                 7,4 min                 2,2 min
```

**Der Befund auf 128:** Qwen liegt zwei Punkte vorn (87 % gegen 85 % der
Begriffe, 104 gegen 93 Gerichte über 80 %), Nemotron ist dreimal so schnell
je Zug — bei gleicher Formtreue (`rejected` je 1) und gleichem
Zusatzartikel-Ergebnis. Auf 64 waren beide gleichauf; auf 128 zeigt sich
ein kleiner, konsistenter Vorsprung des dichten 27B beim Finden von
Katalogbegriffen, vor allem in der Alltagsküche (87 % gegen 83 %) und beim
Backen (88 % gegen 84 %); bei der exotischen Küche sind beide gleich schwach
(78 % gegen 80 %). Nemotrons einziger Ausfall ist ein Fantasiegericht ohne
Rezept, bei dem es zwei Begriffe riet, die es nicht gibt — Qwen riet dort
mehr und traf. Ein Lauf je Modell; die Differenz von zwei Punkten liegt in
der Grössenordnung dessen, was ein Fantasiename allein verschiebt.

Zur Dauer: die reine Generierungsrate beider Fassungen ist laut Box-Session
praktisch gleich (Nemotron 219–222 tok/s ohne Spekulation, Qwen 226 mit
DFlash2). Dass Qwen je Zug dreimal so lange braucht, heisst bei gleicher
Rate: es erzeugt dreimal so viele Token — eine Aussage über Wortknappheit,
nicht über Geschwindigkeit. Nemotron sagt weniger und trifft dabei zwei
Punkte weniger.

## Der Wochenplaner (2026-09-06)

Eine Ebene über dem Chat-Zug: nicht ein Satz -> eine Liste, sondern **eine
Woche -> eine Liste, abzüglich dessen, was da ist.** Die vierte Modellstufe
`plan.woche` bekommt die Gerichte des Haushalts vorgelegt (eigene Rezepte
und geholte Gerichte, ein Rezept je Gericht) und ordnet sie offenen Tagen
zu; alles andere — Portionen, Summen über die Tage, Bestand abziehen,
Packungen, Preis, Rest — rechnet der Code. Design:
`docs/superpowers/specs/2026-09-06-wochenplan-design.md`; Harness:
`scripts/plan_probe.py`.

```bash
sqlite3 data/picknick.db "VACUUM INTO 'kopie.db'"
ZETTEL_PHOENIX_PROJECT="Zettel Eval Wochenplan" \
.venv/bin/python scripts/plan_probe.py --db kopie.db --trace \
    --json evals/plan_probe-2026-09-06-qwen.json
```

### Lauf 1 und 2 — `Qwen3.8-27B-Instruct`, 5 Szenarien, 2026-09-06

Echte Datenbank (Kopie): 16.746 Produkte, 57 Rezepte, davon **19 in der
Vorlage** (ein Rezept je Gericht, nur mit Zutaten). Je Szenario ein Plan,
ein Zug, dann „Ja" auf jeden belegten Tag, die Liste in den Korb, Labels.
Rohdaten und Provenienz: `evals/plan_probe-2026-09-06-qwen.*`.

| Sz | Frage | Tage offen | vorgelegt | belegt | verworfen | Rest | Zeilen | gedeckt | Freitext | Preis | Dauer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | ≤ 40 min, Bestand „500 g Kartoffeln, 6 Eier" | 3 | 3 | **3** | 0 | 12 | 11 | 1 | 1 | 36,05 € | 7,3 s |
| B | 5 Tage, 4 Personen, Budget 60 € | 5 | 19 | **5** | 0 | 34 | 43 | 0 | 10 | 133,14 € (**73 € über Budget**) | 12,2 s |
| C | Tag 2 auswärts, Bestand „Nudeln, 200 g Parmesan" | 3 | 19 | **3** | 0 | 18 | 22 | 0 | 6 | 75,61 € | 5,3 s |
| D | wie A, dann Tag 1 „Nein" → Neuplanung | 3 → 1 | 3 → 2 | 3 → **0** | 0 | 7 | 7 | 0 | 0 | 21,18 € | 7,3 s + 0,5 s |
| E | 7 Tage, ≤ 30 min (ein Gericht in der Vorlage) | 7 | 1 | **1** | 0 | 5 | 5 | 0 | 1 | 17,36 € | 3,6 s |

„Dauer" ist der ganze Zug inklusive Vorwärmen der Zuordnung für Rezepte
ohne gemerkte (`recipe.zuordnung` unter dem Plan-Span, zwei Modellstufen je
Rezept: A und B je zwei, C und E je eines). Der Planungsaufruf selbst liegt
bei 3–5 s (Prompt ~400 Token, Antwort ~60).

**Was die Zahlen sagen:**

* **`rejected = 0` in allen sechs Zügen.** Keine erfundene Gericht-id, kein
  nicht offener Tag, keine Dublette. Die Prüfung im Code hat nichts zu tun
  gehabt — sie steht trotzdem, aus demselben Grund wie in Stufe 3.
* **Der Prompt entscheidet, wie viele Tage belegt werden — und das war ein
  Fehler des ersten Laufs.** Lauf 1 stand mit „passt zu einem Tag nichts,
  lässt du ihn weg. Rate nicht." — das Modell las das als Erlaubnis zur
  Sparsamkeit: A **1 von 3**, D **1 von 3**, obwohl drei Gerichte passten.
  Lauf 2 mit „belege so viele offene Tage wie möglich; leer nur, wenn kein
  Gericht mehr übrig ist": A 3/3, C 3/3, D 3/3. Die Zusicherung (keine
  fremde ID) hängt nicht am Prompt; die Zahl der belegten Tage schon.
* **Die Neuplanung (D) ist ehrlich leer.** Nach „Nein" zu Carbonara an Tag
  1 standen die zwei übrigen Gerichte schon an Tag 2 und 3 — das Modell
  belegte nichts und nannte auch das abgelehnte nicht wieder. 0,5 s.
* **E zeigt die Grenze der Vorlage, nicht des Modells.** Unter 30 Minuten
  hat die Datenbank genau ein Gericht; sechs Tage bleiben leer, und die
  Seite sagt es. Die Antwort auf „7 Tage, 30 Minuten" ist ein grösserer
  Rezeptbestand, kein anderer Prompt.
* **Das Budget wird gerechnet, nicht eingehalten** (B: 133 € bei 60 €). Der
  Planer zeigt die Überschreitung; er plant nicht um. Ein Modell, das
  „billiger" planen soll, bräuchte Preise in der Vorlage — und die sind
  Katalogzahlen, keine Wochenkosten (43 Zeilen, davon 10 ohne Produkt).
* **Der Bestand zieht ab, wo er rechnen kann.** A: „6 Eier" deckt die Eier
  der Carbonara („gedeckt 1"); „500 g Kartoffeln" trifft keine Zeile, weil
  kein gewähltes Gericht Kartoffeln braucht — das steht so auf der Seite.
  C: „Nudeln" ohne Menge deckt nichts und steht als Hinweis; „200 g
  Parmesan" gegen 40 g + 50 g ergibt „gedeckt" — im Lauf 2 nicht, weil die
  Zuordnung „Parmesan" auf „Pecorino" zeigte (Modellwahl aus Stufe 3, nicht
  aus dem Planer).
* **Die Begründungen sind Sätze, keine Zahlen** — und nicht immer klug:
  „Spanische Churros ohne Ei" als Abendessen an Tag 3 (A, D), begründet mit
  „Ei-freies Gericht … als Dessert/Beilage". Die Vorlage kennt keinen
  Unterschied zwischen Hauptgericht und Nachtisch; ein Kennzeichen am
  Rezept wäre die Antwort, kein Prompt.

**Phoenix:** 169 Spans im Projekt, 6 `plan.woche`-Chains mit `zettel.plan.*`
(days, presented, assigned, rejected, rest, lines, covered, prewarmed), je
ein LLM-Span `plan.woche` mit Modellname und Tokenzahlen, 8
`recipe.zuordnung` mit 51 `catalog.search`-Retrievern darunter; je Plan
`plan_precision` und `plan_day`-Annotationen (`annotator_kind = HUMAN`, aus
dem „Ja auf alles" der Probe — im Betrieb aus dem echten Tippen).

### Was diese Messung NICHT sagt

Ein Lauf je Szenario, ein Modell, eine Datenbank mit 19 Gerichten zur Wahl.
Ob ein Plan **gut** ist, sagt keine dieser Zahlen — das sagt nur ein
Mensch über die Labels, und die Probe hat sie mit „Ja auf alles" bewusst
nicht geliefert. `rest` (Zutaten nur an einem Tag) ist zählbar, aber bei 19
Gerichten kaum zu drücken. Und die Vorlage trägt keinen Unterschied
zwischen Hauptgericht und Nachtisch; ein Planer, der Churros zum Abendessen
setzt, hat aus seiner Sicht nichts falsch gemacht.

### Derselbe Lauf gegen Nemotron 3.5 (2026-09-10)

Dieselben fünf Szenarien, derselbe Harness, dieselbe Datenbankkopie-Methode —
gegen **NVIDIA Nemotron 3.5 Lightning 30B-A3B** (W4A16, vLLM 0.27.1,
`--max-len 32768 --lm-only`, kein Reasoning-Parser, keine Spekulation).
Rohdaten und Provenienz: `evals/plan_probe-2026-09-10-nemotron35.*`; die
Provenienz trägt auch den KV-Cache-Zustand (931.157 Token, Concurrency 28,4,
fp8, 0 Verdrängungen), damit niemand Verdrängung für Modellleistung hält.

| Zug | Gerichte vorgelegt | Tagen zugewiesen | erfunden → verworfen | Dauer |
|---|---|---|---|---|
| A — 3 Tage, ≤ 40 min, Bestand erklärt | 3 | 3 von 3 | 0 | 4,2 s |
| B — 5 Tage, 4 Personen, Budget 60 € | 19 | 5 von 5 | 0 | 3,8 s |
| C — 3 Tage, Tag 2 auswärts | 19 | 3 von 3 | 0 | 4,4 s |
| D — wie A | 3 | 3 von 3 | 0 | 4,0 s |
| D' — nach „Nein" auf Tag 1, Neuplanung | 2 | 0 von 1 | **3** | ~1 s |
| E — 7 Tage, ≤ 30 min | 1 | 1 von 7 | **6** | 2,7 s |

Fünf Szenarien in **21 s** gesamt (Qwen: rund 36 s ohne Vorwärmen der
Zuordnung). Und der Befund, der diese Messung wert war:

* **Wo die Vorlage dünn wird, rät das schnellere Modell.** In den vier
  normalen Szenarien wählt Nemotron wie Qwen nur aus der Vorlage (verworfen
  0). In den zwei knappen — ein einziges Gericht für sieben Tage; eine
  Neuplanung mit zwei Kandidaten — nennt es **6 und 3 Gericht-ids, die
  nicht vorgelegt waren.** Qwen liess dieselben Tage leer. Alle neun wurden
  verworfen und gezählt, keine kam auf die Seite: das ist die Prüfung im
  Code bei der Arbeit, nicht ein Modellfehler, der durchrutscht.
* **Das Muster wiederholte sich vor der Kamera.** Im Take vom 10.09. schlug
  Nemotron nach dem „Nein" auf Dienstag zwei Gerichte vor, die nicht in der
  Vorlage standen — `rejected = 2`, Tag blieb offen. Der Film sagt es im
  Untertitel, statt es wegzuschneiden.
* **Die Begründungen sind etwas ausführlicher** als bei Qwen („Caesar-Salad
  passt gut als leichter Start in die Woche und teilt Zutaten wie Knoblauch
  und Weißbrot mit anderen Gerichten") und weiterhin Sätze, keine Zahlen.

Was diese Messung nicht sagt: ob 6 erfundene ids bei einem vorgelegten
Gericht „schlechter" sind als 0 leere Tage — beides ist derselbe Zustand
auf der Seite (sechs Tage leer), nur der Weg dorthin unterscheidet sich, und
der steht im Trace. Ein Lauf je Szenario, keine Wiederholung.

## Was hier schwächer ist, als es aussieht

Diese Liste gehört zum Ergebnis. Wer die Tabelle oben zitiert, muss sie
mitzitieren.

* **Zwölf Beispiele, ein Lauf je Variante, keine Wiederholungen.** `0,850`
  gegen `0,960` ist ein Unterschied von 1,1 Punkten, verteilt auf zwei
  Beispiele von zehn. Ein einziger anders gewählter Artikel bewegt den Schnitt
  um 0,1. Es gibt kein Konfidenzintervall, weil es dafür nichts zu rechnen
  gäbe.
* **Prompt B ist gegen genau den Befund geschrieben, den er behebt.** Der
  Bolognese-Fall (Zwiebeln und Knoblauch vergessen) stand vor dem Prompt fest.
  Die Regel ist zwar allgemein formuliert und nennt keine Zutat aus dem
  Dataset — aber ein unabhängiges Dataset hat sie nie gesehen. Als
  Wirksamkeitsnachweis taugt das nicht; als Hinweis, wo man weitersucht,
  schon.
* **Der LLM-Judge ist derselbe Qwen, der auch der Agent ist.** Dasselbe
  Modell, dasselbe Wissen, dieselben blinden Flecken. Ein Modell, das ein
  Produkt für passend hält, hält es auch beim zweiten Hinsehen für passend.
  Deshalb urteilt er nur dort, wo die Kategorieprüfung nichts sagen kann — und
  deshalb steht sein Wert in der Tabelle bewusst neben und nicht über den
  deterministischen.
* **Eine Dataset-Kante mass einmal die Katalog-Taxonomie statt den Agenten.**
  Sie ist korrigiert. Es ist unwahrscheinlich, dass sie die einzige war: jede
  erwartete Kategorie ist eine Behauptung darüber, wie Knuspr sein Sortiment
  einsortiert, und Knuspr hat dabei nicht mitgeredet. Ein Beispiel, das
  plötzlich auf 0 fällt, ist zuerst als Taxonomie-Frage zu lesen und erst
  danach als Agentenfehler.
* **Präzision belohnt Weglassen**, siehe oben. Die beiden Scores
  gegeneinander zu lesen ist deshalb Pflicht, nicht Kür — eine Variante, die
  nur die Präzision hebt, ist verdächtig.
* **Der Katalog ist unvollständig.** Nach dem Vollcrawl vom 2026-08-28
  (10.361 Produkte) liefern noch 4 der 170 Crawl-Begriffe keinen Treffer;
  davor waren es 59. Ein Beispiel, das in eine dieser Lücken fällt, misst den
  Crawler und nicht den Agenten. **Die hier ausgewiesenen Scores wurden auf dem
  kleineren Katalog (2.498 Produkte) gemessen** — sie sind gültig für den
  Vergleich der Varianten untereinander, aber nicht mit einem Lauf auf dem
  vollen Katalog vergleichbar. Wer neu misst, misst alle Varianten neu.
* **Ein Lauf je Modell ist kein Konfidenzintervall.** Der Qwen-Referenzlauf
  vom 30.08. trifft den vom 29.08. auf den Prozentpunkt genau, und das ist
  ein Hinweis auf Stabilität — aber zwei Läufe sind zwei Läufe. Ein
  Modellvergleich, der auf drei Prozentpunkten steht, steht auf nichts;
  einer, der auf zwanzig steht, trägt.
* **Die vier Läufe der Dataset-Version 3** (07:34–07:37) stehen noch in
  Phoenix und zeigen fast dieselben Zahlen (Präzision 0,830 / 0,943 / 0,850 /
  0,830). Sie sind nicht gelöscht, weil ein verschwundener Lauf schlechter ist
  als ein alter — aber verglichen wird die Version 4.
