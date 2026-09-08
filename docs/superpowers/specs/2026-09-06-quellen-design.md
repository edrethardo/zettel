# Quellen für den Katalog — 72 geprüft (Design)

**Datum:** 2026-09-06 · **Status:** Entwurf, nicht abgenommen, nichts davon vor
dem Contest · **Anlass:** `2026-09-06-scraper-design.md` hat gemessen, dass 77
Zutaten aus einem Messlauf im Katalog fehlen. Dieses Dokument sucht die
Quellen, die die Lücke schliessen.

**Wie geprüft:** 72 Hosts, je **eine** Anfrage an `robots.txt`, ehrlicher
User-Agent (`zettel/1.0 (private household catalogue; source evaluation)`),
danach für jeden, der eine Sitemap ausweist, **eine** Anfrage darauf und
höchstens eine auf die erste Produkt-Sitemap darin. Kein Browser, keine
Tarnung, keine Anmeldung — dieselben Regeln, unter denen der Nachtlauf läuft.
Alle Zahlen unten sind an diesem Tag gemessen; die Skripte stehen in
Abschnitt 7.

> **Korrektur zur ersten Runde:** in einem ersten Durchgang mit `curl`
> antworteten ALDI SÜD, EDEKA, Netto und Kaufland mit **403 auf die
> robots.txt**, was hier als „Bot-Wall" notiert war. Mit einem zweiten Client
> antworten ALDI SÜD, EDEKA und Netto mit **200**. Die Wall hängt am Client,
> nicht am Ziel — nur `kaufland.de` und `shop.rewe.de` blieben in beiden
> Runden bei 403. Der Befund unten ist der aus beiden Runden.

---

## 1 Der Fund, der den grössten Teil der Lücke erklärt

Knuspr veröffentlicht eine **Produkt-Sitemap**, robots-erlaubt, in einer
einzigen Anfrage:

```
https://www.knuspr.de/sitemap_products.xml     15.161 Produkt-URLs
https://www.knuspr.de/sitemap_brands.xml        1.592
https://www.knuspr.de/sitemap_base.xml          5.692
https://www.knuspr.de/sitemap_microsites.xml      707
```

Jede URL trägt die Produkt-ID vorn: `/269-weisskohl-1-stk`. Gegen den eigenen
Katalog gehalten:

| | |
|---|---|
| Produkt-IDs in der Sitemap | **15.161** |
| davon im eigenen Katalog | 8.773 |
| **in der Sitemap, nicht im Katalog** | **6.388** |
| im Katalog, nicht in der Sitemap | 1.588 (ausgelistet oder nicht in der Sitemap geführt) |

Und die Gegenprobe an der Breitenmessung: von den **106 Zutaten, die über 128
Gerichte kein Katalogprodukt fanden, stehen 44 wörtlich in dieser Sitemap** —
darunter **Weißkohl, Frühlingszwiebel, Majoran, Safran, Sternanis, Nelken,
Cayennepfeffer, Fischsauce, Parmesan, Wirsing, Spargel, Spätzle**.

**Damit ist „Weißkohl → Mabyen Mama Weißkohl Brustmaske" erklärt:** den echten
Weißkohl gibt es bei Knuspr (ID 269). Der Shop hat ihn nur nie geholt, weil
„weisskohl" nicht auf unserem Einkaufszettel stand.

**Konsequenz für den Scraper-Plan:** der dort vorgeschlagene Kategorie-Crawl
wird durch etwas Einfacheres und Genaueres ersetzt — Sitemap holen,
ID-Menge bilden, gegen `product` differenzieren, **nur die Differenz
nachladen**. Eine Anfrage sagt, was fehlt, statt 400 Suchbegriffe zu raten.
Offen bleibt nur, über welchen Aufruf die Einzelheiten zu einer bekannten ID
kommen; die bestehende Suche findet ein Produkt auch über seinen Slug.

## 2 Nährwerte — „irgendwie wissen ja alle, wie viele Kalorien im Essen sind"

Sie wissen es, weil es **Pflicht** ist: seit der LMIV (VO (EU) 1169/2011) trägt
jede verpackte Ware eine Nährwerttabelle je 100 g. Deshalb hat sie jeder
Händler in seinen Stammdaten — und deshalb lag sie längst in der Nutzlast, die
der Nachtlauf ohnehin herunterlädt. Es gibt drei Ebenen, und der Wochenplan
braucht zwei davon:

| Ebene | Quelle | Stand |
|---|---|---|
| **verpacktes Produkt** („Weihenstephan Butter 250 g") | Knuspr, Feld `composition.nutritionalValues`: kcal, kJ, Fett, gesättigte Fettsäuren, Kohlenhydrate, Zucker, **Protein**, Salz, Ballaststoffe je 100 g | **liegt vor, wird verworfen** — `parse_products` nimmt 13 von 41 Feldern |
| **generisches Lebensmittel** („200 g Vollkornbrot" aus einem Rezept) | `naehrwertrechner.de`, **11.429 Seiten**, URL trägt den BLS-Schlüssel (`/naehrwerte/B101000/Vollkornbrot/`), Datenquelle laut Seite: Bundeslebensmittelschlüssel + Herstellerangaben. Gemessen: 188 kcal, 6 g Eiweiß, 38 g KH, 1 g Fett, 9 g Ballaststoffe je 100 g | **die passende Ebene für Rezeptzutaten** — ein Produktkatalog kennt „Vollkornbrot" nicht als Zutat |
| **offen, mit Kennzeichen** (vegetarisch, vegan, bio, Allergene) | Open Food Facts, Dumps unter `static.openfoodfacts.org` (ODbL) | **die EAN gibt es doch** — siehe die Korrektur unten |

### Korrektur vom selben Tag: es gibt einen Sammelabruf, und er hat die EAN

Oben stand, die Knuspr-Nutzlast trage keine EAN. Das galt für die **Suche**.
Beim Abholen der 6.388 fehlenden Produkte kam heraus, dass die Website ihre
Daten über vier andere Endpunkte holt, die **je 100 IDs auf einmal**
beantworten (`PRODUCTS_BATCH_SIZE = 100`, `path:"/api/v1/products/card"`, beides
unverschleiert im eigenen JS-Bündel):

```
/api/v1/products/card?products=<id>&products=<id>&…&categoryType=normal
/api/v1/products?products=…
/api/v1/products/composition?products=…      <- nutritionalValues, ingredients,
/api/v1/products/categories?products=…          allergens, EAN
```

**Damit fällt der Einwand gegen Open Food Facts.** Gemessen an 5.868 frisch
geholten Produkten: EAN 38,4 %, Zutatenliste 72,3 %, Nährwerte 22,7 % (der
niedrige Anteil ist richtig — darin steckt der ganze Drogerie- und
Haushaltsteil, der keine hat). Der Abruf ist ausserdem der **höflichere**:
6.388 Produkte in 238 Anfragen statt in 6.388.

Zur zweiten Zeile gehört eine Warnung: der **BLS selbst ist lizenzpflichtig**
(Max Rubner-Institut). Für den Haushalt im Tailnet ist das eine andere Frage
als für eine Veröffentlichung; wer die Zahlen weitergeben will, klärt das
vorher. Ein Abruf von 11.429 Seiten will ausserdem begründet sein — für die
paar hundert Zutaten, die in den eigenen Rezepten wirklich vorkommen, reicht
ein gezielter Abruf mit derselben Höflichkeit wie beim Katalog.

## 3 Alle geprüften Quellen

Die Spalte „Sitemap-Befund" ist das Ergebnis **einer** Anfrage auf die erste in
der robots.txt genannte Sitemap (bzw. auf die erste produktverdächtige darin).
Sie sagt, ob überhaupt etwas Abholbares dahintersteht — nicht, dass es
Lebensmittel sind.

**Lieferdienst**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `picnic.app` | 200 | 9 URLs | Picnic (App) |
| `www.bringmeister.de` | keine Antwort | keine Sitemap | Bringmeister |
| `www.flaschenpost.de` | 200 | 126 URLs | Getraenke |
| `www.food.de` | 404 | keine Sitemap | food.de |
| `www.goflink.com` | 200 | 24 URLs | Flink |
| `www.gourmondo.de` | keine Antwort | keine Sitemap | Gourmondo |
| `www.knuspr.de` | 200 | 15.161 URLs | Rohlik-Gruppe, aktuelle Quelle |
| `www.lebensmittel.de` | 200 | 1 URL | lebensmittel.de |
| `www.mytime.de` | 200 | 375 URLs | Buenting Vollsortiment |

**Supermarkt**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `shop.rewe.de` | **403** | keine Sitemap | REWE Onlineshop |
| `www.combi.de` | 200 | 726 URLs | Combi (Buenting) |
| `www.edeka.de` | 200 | Sitemap **403** | EDEKA |
| `www.famila-nordost.de` | 200 | keine Sitemap | famila |
| `www.globus.de` | 200 | HTTP-Fehler | Globus |
| `www.kaufland.de` | **403** | keine Sitemap | Kaufland |
| `www.rewe.de` | 200 | Sitemap **403** | REWE |
| `www.tegut.de` | 404 | keine Sitemap | tegut |

**Discounter**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.aldi-nord.de` | 200 | 644 URLs | ALDI NORD |
| `www.aldi-sued.de` | 200 | Sitemap **403** | ALDI SUED |
| `www.lidl.de` | 200 | 12.794 URLs | Lidl Onlineshop |
| `www.netto-online.de` | 200 | Sitemap **403** | Netto Marken-Discount |
| `www.norma24.de` | 200 | 1.288 URLs | Norma |
| `www.penny.de` | 200 | 217 URLs | Penny (REWE-Gruppe) |

**Drogerie**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.dm.de` | 200 | 2.179 URLs | dm |
| `www.mueller.de` | 200 | 12.500 URLs | Mueller |
| `www.rossmann.de` | 200 | 5.000 URLs | Rossmann |

**Grosshandel**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.metro.de` | 200 | Sitemap **403** | METRO |
| `www.selgros.de` | 200 | 1.454 URLs | Selgros |

**Bio**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.alnatura.de` | 200 | 4.737 URLs | Alnatura |
| `www.biocompany.de` | 200 | 346 URLs | BioCompany |
| `www.denns-biomarkt.de` | 200 | HTTP-Fehler | denn's |

**Feinkost**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.frischeparadies.de` | 200 | 731 URLs | FrischeParadies |

**Marktplatz**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.amazon.de` | 200 | keine Sitemap | Amazon Fresh |

**Prospekte**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.discounto.de` | 200 | 9 URLs | discounto |
| `www.kaufda.de` | 200 | keine Sitemap | kaufda |
| `www.marktguru.de` | 200 | 6 URLs | Angebote aller Haendler |
| `www.wogibtswas.de` | 200 | 11 URLs | wogibtswas |

**Preisvergleich**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.idealo.de` | 200 | keine Sitemap | idealo |

**Crowdsourcing**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.supermarktcheck.de` | 200 | keine Sitemap | Sortiment und Preise |

**Nährwert-Datenbanken**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `fddb.info` | 200 | keine Sitemap | FDDB Lebensmitteldatenbank |
| `www.codecheck.info` | 200 | keine Sitemap | CodeCheck |
| `www.ernaehrung.de` | 200 | 194 URLs | DEBInet |
| `www.kalorientabelle.net` | 404 | keine Sitemap | Kalorientabelle |
| `www.lifesum.com` | 200 | Sitemap da, leer/kein Produkt | Lifesum |
| `www.myfitnesspal.com` | 200 | 7 URLs | MyFitnessPal |
| `www.naehrwertrechner.de` | 200 | 11.429 URLs | Naehrwertrechner (BLS-basiert) |
| `www.yazio.com` | 200 | keine Sitemap | YAZIO |

**Offene Daten**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `ciqual.anses.fr` | 200 | HTTP-Fehler | Ciqual (FR) |
| `fdc.nal.usda.gov` | 200 | keine Sitemap | USDA FoodData Central |
| `prices.openfoodfacts.org` | 200 | keine Sitemap | Open Prices |
| `static.openfoodfacts.org` | 200 | keine Sitemap | OFF Dumps |
| `world.openfoodfacts.org` | 200 | keine Sitemap | Open Food Facts |
| `world.openproductsfacts.org` | 200 | keine Sitemap | Open Products Facts |
| `www.opengtindb.org` | 200 | kein DNS | OpenGTIN |
| `www.wikidata.org` | 200 | Sitemap **403** | Wikidata |

**Referenz und Behörden**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.blsdb.de` | 404 | keine Sitemap | Bundeslebensmittelschluessel |
| `www.dge.de` | 200 | HTTP-Fehler | Deutsche Gesellschaft fuer Ernaehrung |
| `www.gepir.de` | 200 | keine Sitemap | GS1 GEPIR (GTIN) |
| `www.lebensmittelwarnung.de` | 200 | 8 URLs | Bund/Laender |
| `www.mri.bund.de` | 200 | keine Sitemap | Max Rubner-Institut (BLS) |

**Kommerzielle APIs**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `api.edamam.com` | 404 | keine Sitemap | Edamam |
| `api.nutritionix.com` | keine Antwort | keine Sitemap | Nutritionix |
| `api.spoonacular.com` | 200, `Disallow: /` | keine Sitemap | Spoonacular |

**Rezepte**

| Host | robots.txt | Sitemap-Befund | Was es ist |
|---|---|---|---|
| `www.chefkoch.de` | 200 | keine Sitemap | aktuelle Rezeptquelle |
| `www.eatsmarter.de` | 200 | 1.181 URLs | EatSmarter |
| `www.essen-und-trinken.de` | 200 | keine Sitemap | Essen und Trinken |
| `www.gutekueche.de` | 200 | 154 URLs | GuteKueche |
| `www.hellofresh.de` | 200 | 222 URLs | HelloFresh |
| `www.kochbar.de` | 200 | 50.000 URLs | kochbar |
| `www.lecker.de` | 200 | 10.000 URLs | lecker.de |
| `www.lidl-kochen.de` | 200 | 7.924 URLs | Lidl Kochen |
| `www.marleyspoon.de` | 200 | 85.127 URLs | Marley Spoon |

## 4 Was davon taugt — und was nicht

**Lebensmittelkataloge.** Ausser Knuspr bleibt wenig. `shop.rewe.de` und
`kaufland.de` sperren in beiden Runden (403); REWE verbietet in der robots.txt
zusätzlich `/restservices/` und `/*?search=`, und seine Sitemap antwortet dem
Skript mit 403. ALDI SÜD, EDEKA, Netto und METRO antworten zwar auf die
robots.txt, aber **ihre Sitemap nicht** — die Wall steht eine Ebene tiefer.
`lidl.de` liefert 12.794 Produkt-URLs, das ist aber der **Onlineshop** (Wein,
Textil, Haushalt) und nicht das Filialsortiment, das er im Laden abhakt; die
Endpunkte, die die Scraper-Anbieter empfehlen (`*search?q=*`, `*?offset=*`,
`/user-api/*`), sind dort ausdrücklich per robots verboten. `mytime.de`,
`combi.de`, `famila`, `globus.de`, `flaschenpost.de`, `goflink.com`,
`lebensmittel.de` sind offen, führen aber je Sitemap nur ein paar hundert
URLs — Kategorieseiten, keine Artikel. `food.de`, `gourmondo.de`,
`bringmeister.de` und `tegut.de` antworteten gar nicht.

**Picnic** (die Frage, die den Anstoss gab): kein Web-Katalog. Die Sitemap
kennt Seiten, Standorte und Rezepte, keine Produkte; die App-API
`storefront-prod.de.picnicinternational.com` antwortet
`422 {"code":"UNPROCESSABLE_CONTENT","message":"User is not authenticated"}` —
**Produktdaten nur hinter dem Login**, also nur mit Konto und nachgebautem
App-Protokoll. Das ist genau der Weg, den dieses Projekt nicht geht. (Und ein
Repo, das `picknick_klon` heisst, ausgerechnet Picnic auszulesen, wäre auch
ausserhalb der Technik keine gute Idee — der Namenswechsel zu „Zettel" hatte
seinen Grund.)

**Drogerie** ist der übersehene Treffer. Die Begriffsliste hat einen ganzen
Block „Haushalt, Reinigung, Hygiene" — genau die Ecke, in der Knuspr dünn ist.
`mueller.de` führt **12.500** Produkt-URLs, `rossmann.de` **5.000**, beide
robots-offen mit sauberen Produktpfaden (`/p/…-PPN…`, `/p/<EAN>`). Rossmanns
URLs enden auf der **EAN** — das wäre nebenbei der Schlüssel zu Open Food
Facts, den Knuspr nicht liefert.

**Prospekt- und Preisportale** (`marktguru.de`, `kaufda.de`, `wogibtswas.de`,
`discounto.de`) sind offen, tragen aber Angebote, keine Sortimente: sie
beantworten „was ist diese Woche billig", nicht „was gibt es". Für einen
Wochenplan nach Budget wäre das eines Tages interessant, für den Katalog
nicht. `supermarktcheck.de` (Crowdsourcing) und `idealo.de` haben keine
brauchbare Sortimentstiefe für Lebensmittel.

**Offene Daten.** Open Food Facts und die Dumps sind erreichbar und frei
(ODbL), Open Prices läuft (307.576 Preise weltweit, 7.049 Orte in Deutschland,
Länderfilter der API greift nicht — Abdeckung je Filiale damit unbelegt).
USDA FoodData Central und Ciqual sind offen, aber amerikanische bzw.
französische Lebensmittel. `opengtindb.org` löst nicht mehr auf.
Kommerzielle APIs (Spoonacular, Edamam, Nutritionix) sind entweder gesperrt
(`Disallow: /`) oder kostenpflichtig und scheiden aus, weil das Projekt ohne
API-Schlüssel auskommt.

**Rezeptquellen** — für den Wochenplan, nicht für den Katalog. Neben Chefkoch
sind offen: `kochbar.de` (50.000 Rezept-URLs), `lecker.de` (10.000),
`eatsmarter.de`, `gutekueche.de`, `marleyspoon.de`, `hellofresh.de`,
`lidl-kochen.de`. Das ist reichlich; **es fehlt dem Projekt aber nicht an
Rezepten**, sondern an Zutaten im Katalog. Eine zweite Rezeptquelle löst kein
gemessenes Problem und bleibt deshalb liegen.

## 5 Empfehlung und Reihenfolge

1. **`composition` mitschreiben** — kein neuer Abruf, keine neue Quelle, eine
   Tabelle `product_naehrwert`. Hebt die kcal/Protein-Sperre im Wochenplan.
2. **Sitemap-Differenz statt Kategorie-Crawl** — `sitemap_products.xml`
   holen, gegen `product` differenzieren, die **6.388** fehlenden gezielt
   nachladen. Danach die Breitenmessung über dieselben 128 Gerichte; die
   erwartete Wirkung ist beziffert (44 der 106 fehlenden Zutaten stehen in der
   Sitemap). Braucht den additiven Lauf aus dem Scraper-Plan.
3. **`naehrwertrechner.de` gezielt** für die Zutaten, die in den eigenen
   Rezepten vorkommen und kein Produkt haben — die generische Ebene, die ein
   Produktkatalog nicht hat. Lizenzfrage vorher klären.
4. **Drogerie als zweite Quelle prüfen** (`mueller.de`, `rossmann.de`), wenn
   nach 1–3 noch Lücken messbar sind — und **erst nachdem** entschieden ist,
   was mit Dubletten geschieht. `product` ist mit `UNIQUE (source,
   external_id)` auf mehrere Quellen vorbereitet; Suche, Korb, Rezept- und
   Bon-Zuordnung sind es nicht. Dieselbe Zahnpasta zweimal in der
   Kandidatenliste ist eine schlechtere Vorlage, kein besserer Katalog.
5. **Open Food Facts** nur, wenn „vegetarisch" wirklich gebraucht wird — und
   dann über die EAN, die jetzt aus dem `composition`-Abruf selbst kommt, als
   Vorschlag mit Bestätigung, nie als still eingeschriebene Wahrheit.

## 6 Was nicht passieren darf

Kein Ziel gegen seine `robots.txt`. Keine nachgebaute App-API mit
App-Kopfzeilen und keine Anmeldung mit einem echten Konto, um an einen Katalog
zu kommen (Picnic, REWE-App). Keine Browser-Automation gegen eine Bot-Wall —
ein 403 ist eine Antwort und wird als solche genommen. Kein bezahlter
Scraping-Dienst als Umweg um dasselbe Verbot. Kein zweiter Händler, bevor die
Dublettenfrage entschieden ist. Und das Tempo bleibt: ein breiterer Katalog
heisst **länger**, nicht schneller.

## 7 Reproduzierbar

Die drei Skripte des Sweeps liegen im Scratchpad dieser Sitzung
(`hosts.txt`, `sweep.py`, `sitemaps.py`); die drei Kernzahlen prüft man
schneller von Hand:

```bash
UA="zettel/1.0 (private household catalogue; source evaluation)"

# 1) Die Produkt-Sitemap und die Differenz zum eigenen Katalog
curl -s -A "$UA" https://www.knuspr.de/sitemap_products.xml \
  | grep -oE '<loc>https://www\.knuspr\.de/[0-9]+-' | grep -oE '[0-9]+' | sort -u > /tmp/ids
sqlite3 data/picknick.db \
  "select external_id from product where source='knuspr'" | sort -u > /tmp/haben
comm -23 /tmp/ids /tmp/haben | wc -l          # -> 6388

# 2) Die Nährwerte, die schon mitgeliefert werden
curl -s -A "$UA" "https://www.knuspr.de/services/frontend-service/search-metadata\
?search=butter&companyId=6&limit=1&offset=0" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['productList'][0]['composition'])"

# 3) Picnic: Produktdaten nur hinter dem Login
curl -s -A "$UA" https://storefront-prod.de.picnicinternational.com/api/17/user
```
