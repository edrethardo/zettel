# Adversarial UI-Review — Handybild, erster Eindruck für Fremde

**Datum:** 2026-09-01 · **Stand:** Commit `60b168d` · **Bilder:** [`bilder/`](bilder/) — 12 Seiten (davor; der Satz danach heisst `nach_*`, siehe Nachtrag)
headless bei 390 × 844 (Falzlinie rot) und 12 Standbilder des Demo-Pfads aus dem Rohband
des Handy-Clips (`take_ein_sprung.mkv`).
**Reviewer:** drei Agenten, die nur den Bilderordner kannten — Juror (10 s), Design-Lead,
Mobile-Prüfer. Rohfunde: [`rohfunde_juror.md`](rohfunde_juror.md) ·
[`rohfunde_design.md`](rohfunde_design.md) · [`rohfunde_mobil.md`](rohfunde_mobil.md).
**Gegenprobe:** jeder Fund gegen `stil.css`, Templates und die Datenbank geprüft; Zahlen
nachgemessen. Verworfene Funde stehen unten mit Begründung.

## Gesamturteil

Der Kern hält, und alle drei sagen es unabhängig: aus einem Satz wird sichtbar ein Korb, eine
Bestellung und eine abhakbare Pick-Liste, jedes „Ja" hat eine sichtbare Folge und einen
Rückweg, Zielgrößen (44 px) und Kontraste (5,1–15,3:1) stimmen gemessen. Was den Eindruck
kostet, ist der Rahmen: eine Startseite, die sich in drei Absätzen erklärt statt etwas zu
zeigen; ein Demo-Pfad, in dem dieselbe Zutat in zwei Bediensystemen gleichzeitig steht; und
Entwicklersprache auf Seiten, die in der Hauptnavigation liegen. **Noten 4 / 4 / 3** —
„ausreichend": der Ablauf trägt, die Erscheinung verrät den Prototyp.

## Rangliste

Sortiert nach **Wirkung auf den ersten Eindruck × Häufigkeit im Demo-Pfad**. Aufwand
(S/M/L) ist eine zweite Spalte, kein Sortierkriterium. „Stimmen" = wie viele der drei
Reviewer den Fund unabhängig genannt haben.

| # | Fund | Stimmen | Beleg | Ursache (Gegenprobe) | Aufwand |
|---|---|---|---|---|---|
| 1 | **Die Startseite verspricht nichts.** `/chat` ist über dem Falz zu 70 % leer, drei graue Absätze erklären den Chat, ein Beispiel zum Antippen fehlt; das einzige Bedienelement liegt oben außerhalb der Daumenzone. Ein Juror weiß nach 10 s nicht, was die App kann. | 3 | `seite_01`, `chat_01`: Text y 150–410, leer bis 844 | `chat.html`: Erklärtext statt Vorführung; kein Beispielsatz-Chip | M |
| 2 | **Zwei Bediensysteme für dieselbe Zutat.** Nach „Fragen" stehen Ja/Nein-Karten *und* darunter der „Rezeptentwurf" mit Zahl/Einheit/ok/raus für dieselben Produkte; jede Entwurfszeile sagt in Monospace „wartet auf dein Ja oben". Fremde verstehen nicht, ob sie einmal oder zweimal bestätigen. Der Entwurf ist ein Nebenpfad (Rezept speichern) mit dem Gewicht des Hauptpfads. | 3 | `chat_04`, `chat_05`: Karten y 140–500, Entwurf ab y 665 | `_entwurf.html` + `_vorschlag.html` rendern beide voll; Entwurf ist nicht eingeklappt | L |
| 3 | **Entwicklersprache in der Hauptnavigation.** „Status" zeigt Ports, Hostnamen, Trace-URLs und Chips wie `kept`/`removed`; „Bons" hat ein unbestyltes englisches Datei-Feld („Browse… No file selected.") und einen Absatz über `tesseract-ocr` und „Systemänderung"; die Vorschlagskarte zeigt „Käse · Rang 6.0". | 3 | `seite_10`, `seite_09` y 165–200 und 385–480, `chat_05` y 268 | `status.html` ist eine Diagnoseseite im Hauptmenü; `bons/lesen.py:OCR_FEHLT_TEXT` (tesseract fehlt auf dieser Maschine, also sieht man es); `_vorschlag.html:58` gibt `rang` mit `%.1f` aus | S–M |
| 4 | **Die Zutatentabelle bricht Wörter.** „Lavend / elblüte / n", „Pfeffer, schwarzer      , aus der Mühle", Mengenspalte dreizeilig („1 gr. / Dose/n"). Sieht nach kaputtem Layout aus. | 3 | `seite_05` y 1290–1660 | `stil.css:867` `a.name { display:flex }` — der Hinweis wird zum Flex-Geschwister, der Name wird gequetscht; Mengenspalte 8ch Mono | S |
| 5 | **Falsche Einheiten im Katalog.** „…1,5 L" mit Unterzeile „1,5 ml", „…250 g" mit „0,25 g". Wer eine falsche Zahl sieht, glaubt keiner mehr. | 2 | `seite_02` y 405 und 560 | Datenfehler, kein Layout: `product.unit_text` = „0,75 ml" für 750 ml; **107 aktive Produkte** tragen „0,x g/ml" (durch 1000 geteilt, kleine Einheit behalten) | M |
| 6 | **Die Reiterleiste ist nie ganz.** Acht Ziele, auf jeder Seite rechts ein Fetzen („Be", „Re", „Pick-l"); die Reihenfolge verschiebt sich, weil das aktive Ziel nach links rückt — auf Rezepte/Pick/Bons/Status sind Chat und Korb unsichtbar. Die 3-px-Bildlaufleiste liest sich als Unterstrich unter dem *Nachbar*-Reiter (im Desktop-Firefox des Contest-Videos dauerhaft sichtbar). | 3 | alle `seite_*` y 0–51 | `basis.html` + `stil.css:213–249`: bewusste Wahl (WB-400), aber acht Ziele passen nicht in 390 px | M |
| 7 | **Bestellung ohne Summe, ohne Zeilen, ohne Quittung.** Im Korb steht keine Gesamtsumme vor „Bestellung abschicken"; die Bestellkarte ist ein kommagetrennter 13-px-Klumpen aus 15 Namen; nach dem Abschicken gibt es keine Erfolgsmeldung, nur die neue Karte. | 3 | `chat_09` Knopf y 815, `chat_10`/`seite_08` Karte y 230–465 | `_korb.html`: kein `summe`; `bestellungen.html:37` `join(', ')` | M |
| 8 | **Der Warenkorb versteckt seinen Knopf.** „Bestellung abschicken" erst nach ~2 500 px Scrollweg, nichts klebt; unter fast jeder Zeile eine dreizeilige Rechen-Entschuldigung („2 Stk lässt sich nicht gegen die Packung rechnen…"); ein Auswahlfeld „Egal wo" ohne Beschriftung. | 3 | `chat_08` y 535–580, `chat_09` | `_korb.html`, `warenkorb.html` | M |
| 9 | **Die Oberfläche liest sich als ihre eigene Anleitung.** Fast jede Seite erklärt sich in 1–4 Absätzen bei 13 px (`--t-fein`); auf „Bestellungen" ist dieser Text der ganze Inhalt. | 2 | `seite_01`, `_05`, `_06`, `_09`, `_10`, `chat_08` | Textmenge in den Templates; `--t-fein: 13px` | M |
| 10 | **Die Rezeptliste sieht ungepflegt aus.** „Caesar Salad" ×2, „Caesar Salat", „Lasagne (2)", dazwischen „Einkauf vom 2026-08-28 · 0 Zutaten"; jede Zeile trägt dieselbe Metazeile „noch nichts verknüpft · mit Zubereitung · von chefkoch". | 3 | `seite_04` y 375–740, 1165, 1830 | Demo-Daten (Chefkoch-Import ohne Dublettenprüfung; Bestellung als Rezept angelegt) + `rezepte.html` Metazeile | S (Daten) / M |
| 11 | **Fünfzehn orange Warnknöpfe in der Pick-Liste.** Jede Zeile hat „gab's nicht" auf eigener Zeile mit 40 px Leerraum davor, Zeilenhöhe ~140 px; die Knöpfe konkurrieren mit den Häkchen. | 1 | `seite_07`, `chat_11` | `_pick.html`, `stil.css:620ff` | S |
| 12 | **Textlinks als Bedienelemente.** „raus", „zurück", „rückgängig", „Kein Rezept daraus", „Original auf Chefkoch" sind 11–16 px hohe nackte Links neben 44-px-Knöpfen. | 1 | `chat_04` „raus" 28 × 11 px, `seite_07` „zurück" | kein `min-height: var(--tap)` auf diesen Links | S |
| 13 | **Monospace, wo es nichts bedeutet.** Preise, Mengen, Hinweise, Kartentitel („1 Stunde 5 Minuten"), Chips — 13 Stellen; wirkt wie Terminalausgabe. | 1 | `seite_02` y 405, `seite_05` y 155, `chat_04` | `stil.css`: `var(--mono)` 13×; `tabular-nums` würde für Zahlen reichen | S |
| 14 | **Sechs Knopfstile für gleichwertige Aktionen.** „Fragen" gefüllt, „Suchen"/„Dazu" umrandet (beide schicken ein Feld ab), Ja grün / Nein rot, ok dunkel + raus als Link, orange Warnknöpfe, „Zum Chat" weiß / „Zum Katalog" grau. | 1 | `seite_01/02/06`, `chat_04/05` | gewachsen; keine Primär/Sekundär-Regel | M |
| 15 | **Rhythmus kippt.** Sechs Seiten sind unter dem Falz leer, die anderen Endlos-Scroller ohne Zwischenüberschriften; der einzige gelbe Kasten („60 von 10066") sieht aus wie eine Warnung. | 1 | `seite_08` leer ab 480, `seite_03` 12+ Karten | — | S–M |
| 16 | Kleinkram: Korbzahl-Badge 12 × 11 px mit 6-px-Ziffer (`.korbzahl` bei 0); Einheiten „el"/„paket" in Kleinbuchstaben; Arbeitszustand nur „Das Modell überlegt …" in 13 px Grau; das klebende Eingabefeld verdeckt die erste Zeile darunter; „Züge ohne Trace" ohne Leerzustand. | 1–2 | `seite_01` y 17–27, `chat_03`, `chat_06`, `seite_10` y 490–580 | — | S |

## Nachtrag 2026-09-01 — Welle 1 umgesetzt

Plan: [`../../plans/2026-09-01-ui-review-verbesserungen.md`](../../plans/2026-09-01-ui-review-verbesserungen.md).
Stand nach Welle 1: Commit `305af45`, 1323 Tests grün. Der Bildersatz danach liegt als
[`bilder/nach_*.png`](bilder/) — 18 Seiten, dieselbe Strecke (390 px, Shop auf `127.0.0.1:8748`
gegen eine Kopie der Demo-Datenbank). Nur eine Farbfassung: die Seite ist seit `4e6d21f` dunkel
ohne Media-Query, Hell- und Dunkelbild waren pixelgleich.

| # | Stand | Commits | Beleg danach |
|---|---|---|---|
| 1 | **erledigt** — drei Beispiele zum Antippen statt drei Absätzen; der Chip schlägt das leere Feld | `0ea0ccb`, `b1198d7` | `nach_01` |
| 2 | **erledigt** — der Rezeptentwurf ist ein `<details>`, zu bis man ihn will (70 px zu, offen auf Wunsch); die Ja/Nein-Karten sind der einzige Hauptweg | `3c1c3c3` | `nach_03` (zu), `nach_04` (auf) |
| 3 | **teilweise** — „Rang 6.0" weg von der Vorschlagskarte (`4b81fec`); Status und „wer bin ich?" in einer Fusszeile, die Leiste zählt sieben Ziele (`50afea3`, `7704809`, `376e8cc`); der OCR-Hinweis auf Bons sagt zuerst, was geht (`33205d6`). **Offen:** die Hostnamen auf `/status` (Welle 2) und `_bonstand.html:94`, das beim Bon-Abgleich weiter „Rang" ausgibt — dieselbe Zahl, andere Seite. **Korrektur:** „Browse… No file selected" war die Browsersprache des Review-Firefox, nicht die App; der Knopf ist seit WB-400 gestylt (`stil.css`, `input[type=file]`) — das Bild zeigte das Wort, nicht die Gestalt. | s. o. | `nach_15`, `nach_16`, `nach_17` |
| 4 | **erledigt** — der Griff in der Zutatentabelle ist ein Block, die Menge erbt die Schrift; kein Wortbruch mehr in „Lavendelblüten", „Champignons" | `2ac6759`, `d4abcc4` | `nach_08` |
| 5 | **erledigt** — Datenfehler im Knuspr-Scraper: eine Kilozahl mit Grammeinheit („0,25 g" für 250 g) wird zu Gramm. **Korrektur:** es waren **167** aktive Produkte, nicht 107; die Regel lautet „Zahl < 1 und Einheit g/ml". Beide Datenbanken repariert (Sicherungen `data/sicherungen/picknick-2026-09-01-vor-einheiten.db` und `demo.db.vor-einheiten` neben der Demo-DB). **Rest:** 12 Zeilen je DB tragen denselben Fehler mit Zahl ≥ 1 — 5× „1 g" (Kaffeebohnen 1 kg), 2× „1 ml" (Saft 1 L), 3× „1,5 ml" (Reiniger 1,5 L), 2× „2,4 g" (Hundefutter 6 × 400 g). Die Regel darf nicht einfach erweitert werden: 27 echte Gramm-Zeilen (Safran, Hefe …) stehen im selben Bereich. `repariere_einheiten` hat keinen Aufrufer ausserhalb der Tests — die Reparatur lief von Hand. | `bc8fd96`, `5429e17` | `nach_05`, `nach_06` |
| 6 | **teilweise** — sieben Ziele statt acht (Aufgabe 3); die Leiste scrollt weiter. Rest: Welle 2 | `50afea3` | alle `nach_*` y 0–51 |
| 7 | **erledigt** — Summe vor dem Bestellknopf („Zusammen etwa 39,70 €", mit Begründung für „etwa"); Bestellkarte zeigt vier Zeilen und „und 11 weitere"; nach dem Abschicken eine Quittung mit Posten, Summe und dem Weg zur Pick-Liste | `8573970`, `9e70595`, `81d9c49`, `427d92d` | `nach_10`, `nach_11`, `nach_12` |
| 8 | **teilweise** — Summe und Bestellknopf kleben am unteren Rand des Korbs. Rechen-Entschuldigungen und „Egal wo": Welle 2 | `a21aea7` | `nach_10` |
| 9 | **teilweise** — die Chat-Prosa ist weg (Aufgabe 6). Rest: Welle 2, mit dem Nutzer | `0ea0ccb` | `nach_01` |
| 10 | offen — Welle 2 (Demo-Daten) | — | `nach_07` |
| 11 | **erledigt** — „gab's nicht" steht neben dem Text, der Knopf darf zwei Zeilen hoch sein (78 × 46 px); Zeilen 111–172 px statt ~140 px mit Knopf darunter, kein Wortbruch | `f22e2c7`, `2f75e2f`, `305af45` | `nach_13`, `nach_14` |
| 12 | **verworfen** — Gegenprobe: „raus", „zurück", „rückgängig", „Kein Rezept daraus" sind `button.mini.zurueck` mit 44 px Mindesthöhe; nur der *Text* ist 13 px und unterstrichen. WB-361 hat den Rahmen absichtlich entfernt, damit „rückgängig" nicht wie eine dritte Wahl neben Ja/Nein aussieht. Wahrnehmungsbefund, kein Bedienfehler | — | — |
| 13 | **teilweise** — Mengenspalte der Zutatentabelle (Aufgabe 1). 12 weitere `var(--mono)`-Stellen: Welle 2 | `d4abcc4` | `nach_08` |
| 14–16 | offen — Welle 2 | — | — |

*Zu `nach_10`: die klebende Kasse steht im Ganzseitenbild an der Stelle des ursprünglichen Fensterrands (y ≈ 1300–1500) und lässt an ihrer statischen Position eine Lücke — `position: sticky` und Ganzseitenaufnahme vertragen sich nicht; der Knopf ist da, siehe `test_die_kasse_umschliesst_summe_und_knopf`.*

*Zu Fund 5: die Regel läuft bei jedem Knuspr-Crawl (`parse_products` → `normalisiere_einheit`, der Upsert überschreibt `unit_text`), aber ungeprüft — ein echter Artikel unter einem Gramm (Safran) würde von ihr um den Faktor 1000 verschrieben. Nur `repariere_einheiten` für den Altbestand ist einmalig, und das hat keinen Aufrufer. Die Gegenprobe läge in derselben Nutzlast: Preis geteilt durch Grundpreis ergibt die Menge in der Grundeinheit und belegt die Kilo-Lesart Zeile für Zeile, statt sie aus der Zahl zu raten. Offen, Entscheidung Welle 2.*

*Zwei weitere Punkte für Welle 2, beide aus dem Abschluss-Review: Der Entwurf (`_entwurf.html`) klappt nach JEDEM Zug wieder zu, nicht nur nach Ja/Nein — Name, Menge, raus/wieder rein tauschen denselben `#zug-<id>` aus; drei Mengen heisst dreimal aufklappen. Alternative: mit `open` neu rendern, wenn der Tausch von innen kam. Und seit der Anker nach dem Abschicken weg ist (`f76b566`), gibt es keinen Sprung mehr zur gerade abgeschickten Karte — „Pick-Liste“ in der Quittung könnte ein Link auf `/pick` werden.*

**Zu beachten vor dem nächsten Take:** `dreh_handy.py` klickt auf Koordinaten; Aufgabe 3, 6 und 8
verschieben Elemente (Fusszeile, Chips, klebende Kasse). Trockenlauf vor dem Video.

## Was funktioniert (von allen dreien unabhängig genannt)

* **Der Ablauf schließt sich sichtbar.** Satz → 15 Artikel mit Bild im Korb → Bestellung → Pick-Liste, in der „gab's nicht" den Zähler ehrlich auf „13 zu holen, 1 gab's nicht" stellt.
* **Jedes „Ja" hat Folge und Rückweg.** Gedimmter Name, grüner Chip „im Korb", „rückgängig", die Korbzahl zählt mit, oben „1 Sache im Korb — ansehen".
* **Die Pick-Liste hat die richtige Semantik**: Haken, Durchstreichung, brauner Kasten für „gab's nicht", Zähler-Pill — drei Zustände ohne Lesen unterscheidbar.
* **Handwerk stimmt gemessen**: Knöpfe 44 px, Hilfstext 5,8–6,5:1, schwächste Stelle 4,8:1; leere Zustände (Korb, 404, Rolle) sind knapp und führen weiter.

## Verworfen (mit Begründung)

| Fund | Warum verworfen |
|---|---|
| Graue Platzhalter statt Produktbildern im Katalog und in der Pick-Liste (Juror 2, Design 2, Mobile —) | **Artefakt der Aufnahme.** Der Server lieferte alle 42 Bilder mit `200 image/webp`, das Bild hat Inhalt; der graue Pixel ist exakt `#dfe2e4 × brightness(0.86)` — der `<img>`-Hintergrund, gemalt bevor headless Firefox das lazy geladene WebP dekodiert hatte. Die Korb-Standbilder aus dem echten Firefox zeigen dieselben Artikel mit Bild. |
| Rote Fehlerbox „Tracing ist abgeschaltet (`ZETTEL_TRACING`)" auf Status (Juror 3) | **Artefakt der Bühne**: der Review-Shop lief mit `ZETTEL_TRACING=0`. Vorgabe ist an; der Dienst zeigt die Box nicht. Der Rest der Status-Seite bleibt Fund 3. |
| Kontrast zu schwach (angedeutet bei Monospace-Hinweisen) | Gemessen am CSS: alle Textpaare 5,1–15,3:1; der Mobile-Prüfer maß dasselbe am Bild (schwächste Stelle 4,8:1 > 4,5). Die Hinweise sind **klein**, nicht kontrastarm — das steht unter Fund 9. |
| Abhak-Kästchen 32 × 32 px unter Zielgröße (Mobile 2) | Das Ziel ist das ganze `<label class="haken">` mit `min-height: 64px` (`_pick.html:41`, `stil.css:620`); nur das gezeichnete Kästchen ist 32 px. Bleibt als Affordanz-Frage in Fund 16, nicht als Hürde. |
| „Das Ja, auf das der Entwurf verweist, ist nicht im Bild" (Design, Mobile) | Scrollposition des Standbilds `chat_04` — die Karten stehen im DOM über dem Entwurf (`chat_05`). Die Verdopplung selbst bleibt Fund 2. |
| „Die Status-Seite gesteht, dass sie das Modell nicht beweisen kann" (Juror 3) | Fehllesung: der Satz erklärt den Abschnitt „Züge ohne Trace". In Fund 3 aufgegangen. |

## Wie der Review lief

Zweiter Shop-Prozess auf `127.0.0.1:8748` gegen eine Kopie von `demo.db` (mit
`pruefstand.frisch()`); Snap-Firefox headless mit Profil unter `$HOME` (Snap sieht `/tmp`
nicht); Standbilder per `ffmpeg -ss` aus dem Rohband, Bandzeit = Skriptzeit + 2,09 s.
Reviewer-Prompts verlangten mindestens acht Funde und genau drei Dinge, die funktionieren
— die drei Listen stimmen bei „was funktioniert" wörtlich überein, ohne sich zu kennen.
