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

### Lauf 2 — Nemotron-Nano: offen

Blockiert auf den Modellwechsel auf der Box; der ist Nutzersache und hat
hinter dem Videodreh zu warten. Der Harness ist vorbereitet, das Kommando ist
dasselbe mit `ZETTEL_PHOENIX_PROJECT="Zettel Eval Nemotron"`. Zu prüfen
ist dabei ausdrücklich, ob Guided JSON mit dem Tool-Parser des Nano
zusammenarbeitet — **tut es das nicht, ist das der Befund** und nicht der
Anlass für einen Umweg.

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
