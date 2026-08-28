# Dataset, Evaluatoren, Experiments

Zwölf feste Anfragen, drei Evaluatoren, vier Varianten desselben Agenten. Alle
Zahlen unten sind gemessen — am 2026-08-28, gegen `Qwen3.8-27B-Instruct` auf
der lokalen vLLM-Box und den echten Katalog (2.498 Produkte). Was daran
schwach ist, steht am Ende und ist nicht kurz.

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

## Das Dataset `picknick-anfragen`

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

**`picknick.weakest_rank`.** Der bm25-Rang steht am Span und ist verlockend,
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
* **Der Katalog ist unvollständig.** Von den 170 Crawl-Begriffen liefern 59 im
  heutigen Katalog gar keinen Treffer (gemessen 2026-08-28). Ein Beispiel, das
  in eine dieser Lücken fällt, misst den Crawler.
* **Die vier Läufe der Dataset-Version 3** (07:34–07:37) stehen noch in
  Phoenix und zeigen fast dieselben Zahlen (Präzision 0,830 / 0,943 / 0,850 /
  0,830). Sie sind nicht gelöscht, weil ein verschwundener Lauf schlechter ist
  als ein alter — aber verglichen wird die Version 4.
