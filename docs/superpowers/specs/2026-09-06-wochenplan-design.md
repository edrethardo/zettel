# Wochenplan — der Meal Planner als Zug (Design)

**Datum:** 2026-09-06 · **Status:** **gebaut am 06.09.** in vier Phasen
(Pläne unter `docs/superpowers/plans/2026-09-06-wochenplan-phase-*.md`),
gemessen gegen die Box (`EVALS.md`, „Der Wochenplaner"), Drehbuch in
`docs/contest/VIDEO.md` (v3, noch nicht gedreht) · **Herkunft:** Vorschlag aus dem
Familienchat vom 05.09. („Nicht nur einkaufen, sondern einkaufen nach Bestand
& Zielen") und Aarons Einwand in derselben Zeile: **„Bestand is murks".**
Der Einwand ist richtig, und dieses Dokument dreht sich um ihn.

---

## 1 Was der Vorschlag verlangt

Rahmenbedingungen hineingeben — 2.200 kcal, proteinreich, vegetarisch, max.
20 Minuten Kochzeit, 80 € pro Woche, „und was noch im Kühlschrank liegt" —
und einen Wochenplan, Rezepte, eine Einkaufsliste und wenig Rest
herausbekommen. Danach nachschieben („Mittwoch esse ich auswärts") und den
Rest neu planen lassen.

## 2 Was Zettel davon schon hat

| Stück des Vorschlags | Stand |
|---|---|
| Rezepte mit Zutaten | `recipe`, `recipe_item` (mit `amount`/`unit`), `recipe_ingredient` |
| Gerichte finden | `dish` + `dish_treffer`, Chefkoch-Abruf, 128 gemessene Gerichte |
| Satz → Produkte | `plan.extract` → `catalog.search` → `plan.choose` (`assistant/plan.py`) |
| Einkaufsliste | Korb, Bestellung, Pick-Liste (`orders/`) |
| Portionen skalieren | `recipe.servings` + `recipe_item.amount`, Zug-Portionen an `chat_rezept.portionen` |
| Packungen rechnen | `mengen.py` (Grundeinheiten, Faltung, `vergleichbar`) |
| echter Preis | `receipt_item` → `kaeufe.echte_preise()` (nur `kept`) |
| Kochzeit | `recipe.prep_minutes` / `cook_minutes` / `rest_minutes` |
| Wiederholung erkennen | `gedaechtnis` — 51 % der Zutatennamen wiederholen sich über den Rezeptbestand |

**Fehlt:** ein Planobjekt über mehrere Tage, Rahmenbedingungen als Eingabe —
und Nährwerte. Der Katalog hat sie nicht: `product` trägt Preis, Grundpreis,
Einheit, Kategorie und Bild, **keine Kalorien und kein Protein**, und kein
Rezept trägt ein Kennzeichen „vegetarisch".

## 3 Die Entscheidung: Bestand ist kein Lagerstand

**Käufe kennt der Shop, Verbrauch nicht.** Der Bon sagt, dass am Dienstag
1 kg Kartoffeln gekauft wurde. Niemand meldet, dass am Donnerstag 600 g davon
im Topf gelandet sind. Ein geführter Lagerstand ist deshalb nach wenigen
Tagen falsch — und, das ist der eigentliche Schaden, **er sieht falsch nicht
aus**: eine Zahl in einer Tabelle wirkt wie eine Messung, auch wenn sie eine
Fortschreibung von Annahmen ist. Genau diesen Fehlermodus vermeidet das
Projekt überall sonst: nichts wird erfunden, Unbestätigtes bleibt `offen`,
und ein Preis wird nicht kopiert, sondern aus dem Bon abgefragt.

Drei Wege, und warum es der mittlere wird:

| Weg | Was er verlangt | Urteil |
|---|---|---|
| **a) geführter Lagerstand** | jede Entnahme melden | verlangt Buchhaltung von einem Haushalt; die erste vergessene Entnahme macht ihn still falsch — **verworfen** |
| **b) erklärter Rahmen zum Zug** | beim Planen sagen, was da ist | „noch 500 g Kartoffeln" ist derselbe Satz wie „alles für Bolognese": Eingabe eines Zuges, kein Zustand — **gewählt** |
| **c) Bon schlägt vor, Mensch bestätigt** | ein Ja/Nein je Zeile | kein eigener Weg, sondern der **Zubringer zu (b)**: der Bon füllt den Vorschlag vor, gilt aber erst nach Bestätigung |

Der Bestand darf also **vorschlagen** — „Dienstag 1 kg Kartoffeln gekauft,
noch da?" aus `kaeufe.echte_preise()` bzw. den `kept`-Zeilen der letzten
Tage — und wird zur Tatsache erst durch dasselbe Ja/Nein, das im Chat schon
heute die Eval-Labels erzeugt. Was der Mensch bestätigt, gilt **für diesen
Plan** und wird nicht in die nächste Woche fortgeschrieben.

## 4 Der Zug: `plan.woche`

Eine vierte Modellstufe neben `extract` und `choose`, mit derselben tragenden
Regel: **das Modell erfindet nichts.**

* **Vorgelegt** bekommt es die Gerichte, die der Haushalt hat — die eigene
  Rezeptsammlung und die bereits abgerufenen `dish`-Zeilen, je mit Name,
  Portionen, Zeiten und Zutatennamen. Keinen Katalog.
* **Zurückgeben** darf es nur `recipe_id`s aus dieser Vorlage, je Tag eine
  (oder keine). Ein Rezept, das nicht vorgelegt wurde, wird **verworfen und
  gezählt**, nicht repariert — wie `Auswahl.verworfen` in `plan.choose`.
* **Was es nicht macht: rechnen.** Portionen skalieren, Mengen
  zusammenzählen, Packungen aufrunden, Preis und Kochzeit summieren macht
  der Code (`mengen.py`, `recipe_item.amount`, `echte_preise`). Das Modell
  ordnet Gerichte Tagen zu; jede Zahl, die im Plan steht, ist gerechnet und
  nicht generiert.

Die Einkaufsliste entsteht danach ohne Modell: alle Zutaten des Plans
zusammenlegen, den erklärten Bestand abziehen, den Rest über den bestehenden
Weg (`gedaechtnis` → `catalog.search` → `plan.choose`) auf Produkte abbilden
und als Vorschläge vorlegen — Zeile für Zeile bestätigbar wie heute.

„Mittwoch esse ich auswärts" ist kein neuer Mechanismus: derselbe Zug mit
einem Tag weniger und den bereits bestätigten Tagen als Festlegung.

## 5 Rahmenbedingungen — welche ehrlich prüfbar sind

| Bedingung | prüfbar? | woraus |
|---|---|---|
| Kochzeit | **ja** | `prep_minutes + cook_minutes + rest_minutes`; fehlt bei selbst angelegten Rezepten → Rezept gilt als „Zeit unbekannt", nicht als „passt" |
| Budget | **ja** | `product.price_cents`, wo vorhanden korrigiert durch `echte_preise()` |
| Portionen / Personenzahl | **ja** | `recipe.servings` + `recipe_item.amount` |
| wenig Rest | **ja** | Überschneidung der Zutaten zwischen den Tagen — zählbar, kein Modellurteil |
| Bestand berücksichtigen | **ja**, nach Abschnitt 3 | erklärter Rahmen |
| **kcal, Protein** | **ja, sobald sie geschrieben werden** | am 06.09. gemessen: `composition.nutritionalValues` steht in der Knuspr-Nutzlast, die der Nachtlauf ohnehin holt, und wird von `parse_products` verworfen — siehe `2026-09-06-quellen-design.md` |
| **vegetarisch** | **noch nein, aber nicht mehr unmöglich** | kein Kennzeichen an `recipe` und keines im Katalog. Seit dem Sammelabruf gibt es EAN und Zutatenliste je Produkt — daraus „vegetarisch" zu schliessen wäre aber eine Folgerung und keine Angabe, und an einer Fleischbrühe scheitert sie. Bleibt draussen, bis jemand ein Kennzeichen setzt |

**Die Regel bleibt: keine geschätzte Zahl.** Ein Modell, das Kalorien rät,
liefert erfundene Zahlen in einem Projekt, dessen einzige Zusage lautet, nichts
zu erfinden — und in einem Ernährungsplan sind erfundene Zahlen die
schädlichste Sorte. **Geraten werden muss aber nichts mehr:** die Nährwerte je
100 g stehen bereits in jeder Antwort, die der Nachtlauf herunterlädt
(gemessen 06.09., `2026-09-06-quellen-design.md`); zusammen mit `unit_text` und
`mengen.py` ist der Wert je Packung gerechnet. **Bedingung für kcal und
Protein in diesem Plan ist deshalb genau ein Stück: `composition`
mitschreiben.** Solange das nicht steht, bleiben beide draussen — nicht
geschätzt. „Vegetarisch" bleibt in jedem Fall draussen: das ist ein
Kennzeichen am Rezept, das der Haushalt setzt.

## 6 Daten

Zwei Tabellen, so klein wie möglich:

* **`plan`** — id, Zeitraum (von/bis), Zustand (`entwurf`/`bestellt`), die
  Rahmenbedingungen **als eingegebener Text plus die daraus gelesenen
  Zahlen**, `span_id` des Zuges.
* **`plan_tag`** — plan_id, Datum, `recipe_id` (nullable: „auswärts"),
  Portionen für diesen Tag, `decision` `offen`/`kept`/`removed` wie überall.

Der erklärte Bestand hängt **am Plan**, nicht am Haushalt: `plan_bestand`
(plan_id, Freitext oder `product_id`, Menge, Einheit, Herkunft
`erklaert`/`aus_bon`, `decision`). Damit ist strukturell unmöglich, was
Abschnitt 3 ausschliesst — es gibt keine Tabelle, in der ein Vorrat über die
Woche hinaus lebt.

## 7 Observability

Ein `chain("plan.woche")`-Span je Zug, darunter die vorhandenen
`catalog.search`-Retriever der Einkaufsliste. Attribute in der Art der
bestehenden: `zettel.plan.vorgelegt` (Zahl der vorgelegten Rezepte),
`zettel.plan.verworfen` (erfundene `recipe_id`s — dieselbe Zahl, dieselbe
Bedeutung wie im Chat), `zettel.plan.tage`, `zettel.plan.rest` (Zutaten, die
nur an einem Tag vorkommen).

Labels aus der Nutzung, wie in `obs/labels.py`: je Tag `kept`/`removed`, je
Plan ein Score „behaltene Tage / entschiedene Tage". Damit ist der Planer vom
ersten Zug an messbar, ohne einen einzigen Annotationsauftrag — und ein
zweites Modell ist auf derselben Vorlage vergleichbar wie schon Nemotron
gegen Qwen auf den 128 Gerichten.

## 8 Umfang

**Enthalten (Welle 1):** Plan über n Tage, Rahmen Kochzeit/Budget/Portionen,
erklärter Bestand mit Bon-Vorschlägen, Einkaufsliste in den bestehenden Korb,
Neuplanung einzelner Tage, Spans und Labels.

**Bewusst nicht enthalten:** kcal und Protein (Abschnitt 5), automatische
Ernährungsempfehlungen jeder Art, geführter Lagerstand, Haltbarkeiten und
Verfallsdaten, Portionsvorschläge nach Person, Kochplan-Erinnerungen,
Einkaufsoptimierung über mehrere Läden.

## 9 Reihenfolge — und was daraus wurde (06.09.)

1. ✅ `plan`/`plan_tag`/`plan_bestand` und die Ansicht ohne Modell
   (`zettel/wochenplan/{rahmen,speicher,vorlage,liste}.py`, `/plan`).
   **Abweichung vom Entwurf:** die Mengen kommen nicht aus
   `recipe_item.amount` (die Tabelle war leer), sondern in dieser
   Rangfolge: verknüpfte Produkte, sonst `recipe_zuordnung` +
   `herkunft.zuordnen` über `recipe_ingredient`, sonst die Zutatenliste als
   Freitext. `plan.status` heisst `entwurf`/`im_korb`, nicht „bestellt".
2. ✅ `plan.woche` (`assistant/plan.py::woche`, `wochenplan/zug.py`):
   Verwurf gezählt (`zettel.plan.rejected`), Span `plan.woche` mit
   `zettel.plan.{days,fixed,presented,assigned,rejected,rest,lines,covered,
   prewarmed,stock_suggested}`, Labels `plan_precision`/`plan_day` beim
   Übergeben in den Korb. Neuplanung = derselbe Zug; „Nein" schliesst das
   Gericht für diesen Zug aus.
3. ✅ Bon-Vorschläge (`wochenplan/bestand.py`): bestätigte Käufe der letzten
   sieben Tage, die eine Zeile der Liste treffen, als `aus_bon`/`offen`.
4. ⏳ kcal und Protein: `composition` wird seit dem 06.09. mitgeschrieben
   (9.431 Produkte mit kcal), die Summe je Tag ist noch nicht gebaut — sie
   wäre eine Rechnung über `product_naehrwert × bedarf`, kein Modell.

**Gemessen** (`scripts/plan_probe.py`, Qwen3.8-27B, 5 Szenarien): rejected
0 in sechs Zügen; die Zahl der belegten Tage hing am Prompt (1/3 → 3/3 nach
einer Korrektur) und an der Vorlage (≤ 30 min: ein Gericht). Details in
`EVALS.md`.

## 10 Was nicht passieren darf

Keine geschätzte Kalorienzahl. Kein Rezept im Plan, das nicht vorgelegt
wurde — erfundene IDs werden verworfen und gezählt, nie repariert. Kein
Vorrat, der eine Woche überlebt, ohne dass ein Mensch ihn bestätigt hat.
Keine Zahl im Plan, die das Modell gerechnet hat statt der Code. Und der
Planer fasst den bestehenden Chat-Zug nicht an: er benutzt ihn.
