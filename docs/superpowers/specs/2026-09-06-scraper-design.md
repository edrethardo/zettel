# Katalog-Crawler — was ihm fehlt und in welcher Reihenfolge (Design)

**Datum:** 2026-09-06 · **Status:** Entwurf, nicht abgenommen, **nichts davon
vor dem Contest** (Post 08.09., Frist 10.09.) · **Grundlage:** gemessen an
`data/picknick.db` (10.361 aktive Produkte, letzter guter Lauf 2026-08-28) und
an der Breitenmessung `evals/breite_probe-2026-09-05-qwen-128.json`
(Qwen, 128 Gerichte).

---

## 1 Der Befund

Der Crawler funktioniert. Was fehlt, ist nicht Technik, sondern **Umfang** —
und das ist messbar, nicht gefühlt.

Über 128 Gerichte hat der Agent **1.048 Suchbegriffe** erzeugt; **91 davon
fanden kein Katalogprodukt** (8,7 %). Auf dem Zettel landeten sie als
**145 Freitextzeilen mit 106 verschiedenen Zutatennamen.** Diese 106 zerfallen
beim Blick in den Katalog in zwei sehr verschiedene Hälften:

* **29 haben sehr wohl ein Produkt mit dem Wort im Namen** — nur das falsche:
  „Weißkohl" → *Mabyen Mama Weißkohl Brustmaske*, „Frühlingszwiebel" →
  *Lindner Landrahm Frühlingszwiebel*, „Parmesan" → *PPURA BIO Sugo
  Parmigiana*. Das ist **nicht** der Crawler, das ist das ButterBoyz-Muster
  aus dem README: Retrieval, nicht Sortiment. Es gehört in ein Ticket zur
  Suche und steht hier nur, damit es nicht dem Crawler angelastet wird.
* **77 haben gar keines:** Paprikapulver, Nelken, Sternanis, Majoran,
  Fischsauce, Sojasprossen, Staudensellerie, Cayennepfeffer, Markknochen,
  Tamarindenpaste, Hafergrütze.

**Der Satz, an dem dieser Plan hängt: „paprikapulver" steht in `BEGRIFFE`** —
und trotzdem trägt der Katalog kein einziges Produkt mit diesem Wort im Namen.
Ein Begriff in der Liste ist also **kein Beleg, dass das Regal im Katalog
steht.** Die Kategoriezahlen sagen, warum:

```
Kochhilfen, Gewürze & Salz     245 Produkte
   davon „Gewürze"              44
   davon „Gewürzmischungen"     39
   „Pfeffer"                    24 · „Kräuter" 8
Beispielnamen dort: Ostmann Hackfleisch Gewürzsalz · Ostmann Tomaten
Gewürzsalz · Ostmann Knoblauch granuliert · Maggi Würze
```

Diese Artikel sind über „hackfleisch", „tomaten" und „knoblauch" hereingefallen
— als Beifang. Ein Gewürzregal hat der Shop nie gezielt geholt.
**Der Katalog ist ein Schatten des Einkaufszettels, nicht des Sortiments.**
`begriffe.py` sagt das selbst („sie IST der Umfang des Sortiments"); neu ist
nur, dass jetzt gemessen ist, was das kostet.

Nebenbefund, offen: `scrape_run` in der lokalen Datenbank kennt **zwei Läufe,
den letzten am 28.08.** Entweder ist das eine Kopie und die Box hat neuere
Läufe — oder die nächtliche Unit läuft nicht. Vor allem anderen nachsehen.

## 2 Die Schwächen, nach Wirkung sortiert

| # | Schwäche | Wo | Was sie anrichtet |
|---|---|---|---|
| 1 | Umfang = Begriffsliste | `scrapers/begriffe.py` | **6.388 Produkte des Händlers fehlen im eigenen Katalog** (Sitemap-Differenz, 06.09.) — darunter der echte Weißkohl |
| 2 | ein Fehler beendet den ganzen Lauf | `knuspr.crawl` — ein `try` um alle Begriffe | eine Netzstörung bei Begriff 150 von 170 wirft 20 Minuten weg; kein Retry, kein Weitermachen |
| 3 | die Verwurfsschwelle ist global | `MIN_ANTEIL = 0.5` | ein einzelner Begriff, der auf 0 einbricht, fällt nicht auf — seine Produkte werden in `uebernehmen` still `active = 0` |
| 4 | keine Feldprüfung | `parse_products` / `crawl` | wird `price` umbenannt, bleibt die Zeilenzahl gleich, alle Preise werden NULL, und der Lauf gilt als **ok** |
| 5 | `--begriff` kann den Katalog nicht ändern | `crawl` → `uebernehmen` | eine Handprobe liefert ~200 Zeilen gegen 10.361 → **immer** `rejected`. Es gibt heute keinen Weg, eine Lücke zu schliessen, ohne alles neu zu crawlen |

3 und 4 sind derselbe Gedanke wie `MIN_ANTEIL`, nur eine Ebene tiefer: der
Kommentar dort („macht aus dem Katalog sonst stillschweigend zwölf Produkte")
gilt genauso für einen Begriff und für ein Feld.

## 3 Die Stücke

**1. Umfang vom Händler nehmen, nicht von uns — und zwar als Differenz.**
Am 06.09. gemessen (`2026-09-06-quellen-design.md`): Knuspr veröffentlicht
`sitemap_products.xml` mit **15.161 Produkt-URLs**, jede mit der ID vorn
(`/269-weisskohl-1-stk`). Gegen `product` gehalten fehlen dem eigenen Katalog
**6.388 Produkte** — und **44 der 106 Zutaten**, die in der Breitenmessung kein
Produkt fanden, stehen wörtlich in dieser Sitemap (Weißkohl, Frühlingszwiebel,
Majoran, Safran, Sternanis, Nelken, Fischsauce, Parmesan). Damit ist der
geplante Kategorie-Crawl vom Tisch: **eine Anfrage sagt genau, was fehlt**,
statt 400 Begriffe zu raten. Der Lauf holt die Differenz, nicht das Sortiment.
Offen ist nur, über welchen Aufruf die Einzelheiten zu einer bekannten ID
kommen — die bestehende Suche findet ein Produkt auch über seinen Slug.

**2. Die Lückenliste aus der eigenen Messung.** Ein Skript, das genau die
Rechnung aus Abschnitt 1 macht und Begriffe ausspuckt: Freitextzeilen aus
`chat_suggestion` (Vorschlag ohne `product_id`), die `zeilen[].freitext`-Namen
der `breite_probe`-Läufe und `receipt_item` ohne Zuordnung. Die Gegenprobe
gehört dazu: nach dem Lauf noch einmal zählen, wie viele der 106 jetzt ein
Produkt haben. Damit schliesst sich der Kreis **Eval → Crawler → Eval**, und
der Nutzen des Crawls ist eine Zahl statt einer Vermutung.

**3. Der additive Lauf** (`--ergaenzen`), ohne den Stück 2 nicht ausführbar
ist. Staging nur für die genannten Begriffe; `uebernehmen` **ohne** das
`UPDATE product SET active = 0` über die ganze Quelle; `MIN_ANTEIL` aus.
Ausdrücklich benannt: **nur dieser Modus darf teilweise sein.** Der Nachtlauf
bleibt vollständig-oder-verworfen, und der additive Modus wird nie die Vorgabe
der Unit.

**4. Je Begriff überleben und zählen.** `try`/`except` um den einzelnen
Begriff statt um den Lauf, ein Retry mit Rückfall (2/4/8 s) bei Netz- und
5xx-Fehlern, und eine Tabelle `scrape_run_begriff` (run_id, begriff, seiten,
produkte, status, fehler). Der Lauf scheitert erst, wenn ein Anteil der
Begriffe scheitert — nicht, wenn einer es tut. Die Statusseite liest die
Tabelle ohnehin schon; ein Begriff, der gegenüber seiner eigenen Historie
einbricht, ist dort eine Warnung, und das ist die Antwort auf Schwäche 3.

**5. Feldgesundheit als zweiter Torwächter.** Je Lauf den Anteil Zeilen mit
Preis, mit Einheit, mit Kategorie, mit Bild festhalten. Sackt einer gegenüber
dem letzten guten Lauf deutlich ab, wird der Lauf **verworfen mit
Begründung** — dieselbe Mechanik und dieselbe Begründung wie `MIN_ANTEIL`,
angewandt auf Felder statt auf Zeilen. Das ist die einzige Absicherung gegen
einen stillen Formatwechsel, der die Zeilenzahl nicht anfasst.

**6. Nährwerte mitschreiben — Zubringer für den Wochenplan. Erledigt geprüft,
offen im Code.** Die Nutzlast trägt je Produkt 41 Felder, `parse_products`
nimmt 13; unter den verworfenen steht `composition.nutritionalValues` mit
Brennwert, Fett, gesättigten Fettsäuren, Kohlenhydraten, Zucker, **Protein**,
Salz und Ballaststoffen je 100 g (gemessen 06.09.). Es fehlt also kein Abruf,
sondern nur eine Tabelle `product_naehrwert` und drei Zeilen im Parser.
Zutatenliste, Allergene und ein Kennzeichen „vegetarisch" kommen **nicht** mit,
und eine EAN auch nicht — Einzelheiten in `2026-09-06-quellen-design.md`.

**7. Bilder nachziehen** (nachrangig). `hole_bild` überspringt eine vorhandene
Datei für immer — ein geändertes Produktbild kommt nie nach, ein einmal
fehlgeschlagener Abruf wird nie wiederholt. Kosten und Nutzen sind beide
klein; steht hier, damit es nicht vergessen wird, nicht damit es bald kommt.

## 4 Reihenfolge

0. Nachsehen, ob die nächtliche Unit überhaupt läuft (Nebenbefund Abschnitt 1).
1. Stück 6 (`composition` mitschreiben) — billigster Gewinn des Plans, kein
   neuer Abruf, und der Wochenplan hängt daran.
2. Stück 3 (additiv), dann Stück 2 (Lückenliste) — zusammen der erste
   sichtbare Gewinn: die 77 fehlenden Zutaten, gemessen vorher und nachher.
3. Stück 4 und 5, bevor die Begriffsliste gross wird — mit 400 Begriffen
   trifft Schwäche 2 sonst fast sicher jeden Lauf.
4. Stück 1 ausrollen (die 6.388 aus der Sitemap-Differenz nachladen), dann
   die Breitenmessung erneut über dieselben 128 Gerichte. **Diese Zahl ist das
   Ergebnis des ganzen Plans.**
5. Stück 7, wenn sonst nichts ansteht.

## 5 Was nicht passieren darf

**Die Höflichkeit bleibt, wie sie ist:** `PAUSE_S = 1,5 s`, ein ehrlicher
User-Agent, kein Browser, keine Tarnung, nichts im Request-Pfad des Shops. Ein
breiterer Crawl heisst **länger**, nicht schneller — wenn 400 Begriffe eine
Stunde brauchen, dann braucht der Nachtlauf eine Stunde.

Kein Produkt wird gelöscht; Verschwundenes bleibt `active = 0` (Spec 5.3).
Der Nachtlauf bleibt vollständig-oder-verworfen; teilweise darf nur der
ausdrücklich angeforderte additive Lauf sein. Keine Zahl aus einem Lauf ohne
Protokollzeile in `scrape_run`. Und keine Zahl in diesem Dokument ohne
Messung — die aus Abschnitt 1 stammen aus der Datenbank vom 28.08. und der
Breitenmessung vom 05.09. und sind mit dem Skript aus Stück 2 reproduzierbar.


---

## 6 Was daraus am 06.09. wurde (nachgetragen)

Stück 1, 3 und 6 sind gebaut und gelaufen. Der Rest steht noch offen.

| | vorher | nachher |
|---|---|---|
| Produkte im Katalog | 10.361 | **16.746** |
| mit EAN | 0 | **12.274** |
| mit Nährwerten | 0 | **9.575** (9.401 davon mit kcal) |
| der 106 vermissten Zutaten haben ein Produkt | 29 | **46** |

Der Weg dorthin waren **573–582 Anfragen** an knuspr.de in drei Runden
(seriell, ≥1,5 s, ehrlicher User-Agent) — nicht die 6.388 Einzelabrufe, die
die Slug-Suche gekostet hätte. Möglich wurde das durch den Sammelabruf, den
das Quellen-Design beschreibt: 100 IDs je Anfrage.

**Neu im Code:** `product_naehrwert` und `product.ean` (`db.py`);
`parse_naehrwerte`, `parse_sitemap`, `hole_sitemap`, `fehlende_ids`,
`uebernehmen(additiv=…, gefuehrt=…)`, `verwirf_unmoegliche_kcal`
(`knuspr.py`); das zweite Schema in `knuspr_api.py`; der Einspielweg in
`nachtrag.py`. 1.390 Tests.

**Drei Funde, die erst beim Bauen auffielen:**

1. **Der Vollcrawl hätte jeden Nachtrag wieder abgemeldet**, weil kein Begriff
   nach den nachgetragenen Produkten fragt. Deshalb bekommt `uebernehmen`
   jetzt die Menge der laut Sitemap **geführten** Produkte und meldet nur ab,
   was dort fehlt. Der Nachtlauf holt sie vor dem Crawl.
2. **Eine leer gelesene Sitemap hätte den kompletten Katalog abgemeldet** —
   eine leere Menge „geführter" Produkte trifft in derselben Transaktion
   jede Zeile. `SITEMAP_MIN_ANTEIL` verhindert das: eine Sitemap, die weniger
   als die Hälfte des bekannten Katalogs nennt, wird nicht geglaubt.
3. **13 Nährwertzeilen waren physikalisch unmöglich** (bis 3.141 kcal je
   100 g). Bei den meisten sind kJ und kcal vertauscht. Sie werden
   **verworfen und nicht repariert** — dieselbe Regel wie bei einer
   erfundenen Produkt-ID.

**Was offen bleibt:** Stück 2 (Lückenliste als Skript), 4 (je Begriff
überleben und zählen), 5 (Feldgesundheit als Torwächter), 7 (Bilder). Und die
Bilder der 6.385 neuen Produkte sind noch nicht geholt. Das bricht nichts:
`web.app.bilddatei` sucht die Datei am Namen in `data/images` und gibt `None`
zurück, wenn sie fehlt — die Kachel bleibt ohne Foto, es wird nichts vom CDN
nachgeladen (Spec 5.2 bleibt gewahrt). Sie nachzuholen sind rund 6.400
Abrufe beim Bild-CDN und damit eine eigene Entscheidung.
