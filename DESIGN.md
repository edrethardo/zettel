# Architektur und Entwurfsentscheidungen

Der vollständige Entwurf steht in
`docs/superpowers/specs/2026-08-28-picknick-design.md`. Hier stehen die
Entscheidungen, die man kennen muss, um den Code zu lesen — und die, die von
aussen wie ein Versehen aussehen und keines sind.

## Die Form

Ein Prozess, eine Datei, kein Build-Schritt.

```
picknick/
  db.py              Schema, Migrationen, FTS5-Index, Umlautnormalisierung
  catalog/           search.py (FTS5) · categories.py (Baum, Blättern)
  orders/            bestellung.py (Zustände) · korb.py (einlegen, abschicken)
                     pick.py (im Laden abhaken)
  recipes/           sammlung.py · uebernahme.py (Rezept <-> Bestellung)
  assistant/         chat.py (der Zug) · plan.py (die zwei Modellstufen)
                     rezeptweg.py · vorschlaege.py (Vorschlag -> Entscheidung)
  llm/               client.py (OpenAI-kompatibel) · wake.py (Wake-on-LAN)
  obs/               otel.py (Tracer) · spans.py (Vertrag) · labels.py (Rückweg)
  scrapers/          knuspr.py · begriffe.py (170 Begriffe) · nachtlauf.py
  web/app.py         FastAPI, Jinja2, HTMX — server-gerendert
  betrieb.py         Sicherung und Statusbericht
evals/               dataset.py · experiment.py   (Handarbeit, kein Gate)
checks/smoke.py      das Gate
```

SQLite mit WAL. Kein ORM, kein Migrationsframework: `db.migrate()` ist
idempotentes SQL, das Web, Crawler und Evals sich teilen.

## Die tragenden Entscheidungen

### Der Shop sucht, nicht das Modell

Der Agent hat drei Stufen, und die mittlere ist kein Modellaufruf:

1. `plan.extract` — das Modell macht aus dem Satz **nur Suchbegriffe mit
   Mengen**, je Zutat mehrere, vom genauesten zum allgemeinsten
   („Auberginen", „Aubergine"). Es sieht keinen einzigen Katalogeintrag.
2. `catalog.search` — **der Shop** sucht, jeden Begriff einmal, vereinigt die
   Treffer nach Produkt-ID und legt die Kandidaten vor. Nicht „der erste
   Begriff, der etwas findet, gewinnt": das ist gemessen schlechter, weil das
   Fertiggericht „Gemüse-Auberginen-Masala" den höheren bm25-Rang hat als die
   echte Aubergine. Vorgelegt wird beides; die Wahl gehört in Stufe 3.
3. `plan.choose` — das Modell wählt **aus dieser Liste**. Nennt es eine ID,
   die nicht vorgelegt wurde, wird der Vorschlag verworfen und nicht
   repariert.

Ein LLM, das Produkt-IDs frei ausgeben darf, halluziniert Produkt-IDs — und
eine halluzinierte ID sieht in der Datenbank aus wie eine echte, bis jemand im
Laden vor einem Regal steht. Die Zahl der verworfenen Antworten steht als
`picknick.rejected` am Span **und** als Satz über der Vorschlagsliste: ein
Modell, das erfindet, soll man sehen können.

**Guided Decoding erzwingt die Form, nicht die Wahrheit.** vLLM kann die
Antwort per `guided_json` in ein Schema zwingen; eine gültige Ganzzahl kann
trotzdem eine erfundene sein. Die Prüfung gegen die vorgelegten Kandidaten
bleibt deshalb im Code und wandert nicht in eine Serveroption, die beim
nächsten vLLM-Update anders heisst.

### Kein Begriff verschwindet still

Findet die Suche nichts, wählt das Modell nichts, oder erfindet es etwas — der
Begriff bleibt als **Freitext-Vorschlag** stehen. Der Katalog hat Lücken; eine
stillschweigend fallengelassene Zutat merkt man erst im Laden.

Dasselbe auf dem Rezeptweg: „alles für Spaghetti Bolognese, und Klopapier"
trifft das Rezept, und „Klopapier" bleibt als Freitext daneben liegen.

### Nichts landet ungefragt im Warenkorb

Ein Chat-Zug erzeugt eine Liste in `chat_suggestion`, die zeilenweise mit „Ja"
oder „Nein" entschieden wird. Erst „Ja" legt ein. Diese Entscheidung ist
zugleich das Eval-Label (siehe `OBSERVABILITY.md`) — das Label fällt aus dem
Produkt heraus, weil die Nutzerin die Liste ohnehin durchgehen muss.

### „Nein" verwirft nicht bloss, es zeigt die Alternativen

Die Alternativen gibt es längst: `suche_kette()` legt sie vor, Stufe 3 wählt
aus ihnen, der RETRIEVER-Span kennt sie. Bis WB-359 endete ihr Weg dort — nur
der gewählte Kandidat wurde gespeichert, der Rest weggeworfen, bevor ein
Mensch ihn sehen konnte. Seither liegen sie in `chat_kandidat` (eine eigene
Tabelle und kein JSON in einer Spalte: es sind Verweise auf `product`, und
Name, Preis und Bild sollen beim Anzeigen aus dem Katalog kommen und nicht aus
einer eingefrorenen Kopie von gestern). Ein „Nein" klappt sie auf, ein Tipp
legt eine davon statt des Vorschlags in den Korb — **ohne eine zweite Suche**,
denn eine zweite Suche liefe gegen einen veränderten Katalog und zeigte im
Zweifel etwas anderes, als das Modell vorgelegt bekam.

Die Korrekturzeile trägt `corrected_from` und ist damit als **Korrektur**
erkennbar und nicht als zweite, unabhängige Entscheidung. Ohne diesen Verweis
wäre hinterher nicht zu unterscheiden, ob die Nutzerin einen Fehlgriff
geradegezogen oder einfach etwas dazugelegt hat — und genau dieser Unterschied
ist der Eval-Wert des Ganzen.

Passt nichts, bleibt der Weg zum **Freitext** offen. Bei einer echten
Katalog-Lücke ist das nicht der Notausgang, sondern der richtige Ausgang: zu
„Sellerie" kennt der Katalog einen Geflügelsalat und ein Hühnerfrikassee, und
daran ändert auch eine grössere Kandidatenzahl nichts.

### Zwei Grenzen, weil zwei verschiedene Dinge knapp sind

Bis WB-359 bediente eine Zahl beide Zwecke, und deshalb sah eine einbegriffige
Zutat wie „Butter" genau **vier** Alternativen.

| Grenze | je Begriff | wofür | was sie kostet |
|---|---|---|---|
| `plan.KANDIDATEN_MODELL` | 5 | was Stufe 3 im Prompt sieht | Token: 63 Kandidaten waren 8.115 Zeichen (WB-340) |
| `plan.KANDIDATEN_ANZEIGE` | 15 | was aufgehoben und der Nutzerin gezeigt wird | Datenbankzeilen; die FTS-Abfrage holt intern ohnehin `max(limit × 5, 100)` |

Gemessen am echten Katalog (10.361 Produkte, 2026-08-28) — Alternativen sind
die Kandidaten ohne den vorgeschlagenen:

```
Kette                          Kandidaten alt (5/10)   neu (15/30)
[Butter]                                 5                 15
[Schmand]                                5                 15
[Schmand, Sahne]                        10                 26
[Lasagneplatten, Lasagne]                5                 12
[Tomatenmark]                            5                 11
[Zucchini]                               5                 15
[Sellerie, Sellerieknolle]               2                  2
```

Der einbegriffige Fall ist der, um den es geht: dort waren es 5 Kandidaten und
damit **4 Alternativen**, jetzt 15 und damit 14. Bei „Sellerie" ändert die
grössere Grenze nichts, und das ist kein Fehler, sondern der Katalog — mehr als
einen Geflügelsalat und ein Hühnerfrikassee gibt es dort nicht. Die Oberfläche
sagt das hin („Mehr hat der Katalog zu ‚Sellerie' nicht hergegeben.") statt es
zu verschweigen, und daneben steht das Freitextfeld.

Gesucht wird trotzdem nur EINMAL: die Modellvorlage entsteht aus der
aufgehobenen Liste (`catalog.search.kuerze_kette`) und ist damit garantiert
eine Teilmenge davon. Liefe sie auseinander, zeigte „Nein" nicht mehr die
Liste, aus der gewählt wurde.

### Ein Modell für Korb, Bestellung und Pick-Liste

Der Warenkorb **ist** die Bestellung im Zustand `draft`; die abgeschickte ist
dieselbe Zeile in `offen`, die abgehakte dieselbe in `erledigt`. Kein
Kopieren, kein zweites Modell.

`erledigt -> offen` ist ein Übergang, **den die Spec nicht nennt**, und er ist
trotzdem drin: wer im Laden danebentippt und einen Haken wieder wegnimmt, hat
eine Bestellung, die noch nicht fertig ist, und die Übersicht muss das zeigen
statt sie fälschlich unter „erledigt" abzulegen. Der Zustand wird abgeleitet
(„kein Posten mehr offen"), nicht von Hand gesetzt — sonst gäbe es einen Knopf
„fertig", den man drücken kann, obwohl die Hälfte fehlt.

### Die Bindung ist die einzige Sicherheitsgrenze

Es gibt kein Passwort. Der Rahmen ist das Tailnet, und deshalb wird die
Bindeadresse **aktiv geprüft, bevor ein Socket entsteht**: erlaubt sind
loopback und eine Adresse aus `100.64.0.0/10`. Alles andere — `0.0.0.0`, `::`,
die LAN-Adresse des Laptops, ein Hostname — wird mit einem Fehler abgelehnt
statt stillschweigend korrigiert. Weissliste, weil eine Schwarzliste nur die
Schreibweisen abfängt, an die jemand gedacht hat.

Uvicorn kann pro Aufruf nur eine Adresse binden, „localhost und Tailnet" sind
zwei — der Prozess baut die Sockets deshalb selbst und übergibt sie. Der
naheliegende Ausweg wäre `0.0.0.0`; genau der ist verboten.

Das **Rollen-Cookie ist keine Authentifizierung.** Es ist frei wählbar, wird
nirgends geprüft und steuert nur, welche Ansicht `/` zeigt. Wer daran je eine
Berechtigung aufhängt, hat eine Zugangskontrolle gebaut, die jeder Besucher
selbst ausstellt.

### Ausfälle sind örtlich

* **Modell weg** → nur der Chat ist betroffen. `turn()` wirft
  `ChatNichtVerfuegbar` mit dem Zustand des Weckers; Katalog, Korb, Pick-Liste
  und der Rezeptweg laufen weiter.
* **Phoenix weg** → gar nichts ist betroffen. Siehe `OBSERVABILITY.md`.
* **Katalog alt** → ein Hinweisband, kein stilles Weiteraltern; `/status`
  nennt verworfene Läufe mit Begründung.

Das Rendern des Warenkorbs fragt die Box **nicht** — der Zustand wird per HTMX
nachgeladen. Sonst hinge jeder Blick in den Korb am health-Timeout.

## Kleinere Entscheidungen, die überraschen

**Der Kategoriebaum hat keine Tabelle.** Er wird aus `category_l1/l2/l3` der
aktiven Produkte gebildet. Knuspr liefert die Kategorien in jeder
Produktantwort mit; eine zweite Tabelle wäre eine zweite Wahrheit, und die
geht beim nächsten Crawl auseinander. Ein Knoten zählt seinen ganzen Teilbaum,
sonst stünde „Molkerei (0)" in der Oberfläche.

**Ausgelistete Produkte werden `active = 0`, nicht gelöscht.** Eine Bestellung
von letzter Woche soll lesbar bleiben. Die Rezeptansicht zeigt „nicht mehr im
Katalog" ausdrücklich an.

**FTS5 wird über eine Weisse Liste gefüttert, nicht über Escaping.** Alles,
was kein Wortzeichen ist, fliegt vor der Abfrage raus. Escaping hängt bei FTS5
von der Position im Ausdruck ab und bleibt damit eine Fehlerquelle.

**`python-multipart` ist keine Abhängigkeit.** Starlette 1.6 verweigert
`request.form()` ohne das Paket — auch für schlicht urlencodierte Formulare,
die es selbst parsen könnte (gemessen 2026-08-28). Der Rumpf wird deshalb mit
`parse_qsl` von Hand gelesen. Wenige Zeilen, und die Abhängigkeitsliste bleibt
bei dem, was die Spec nennt. Dateiuploads gibt es in diesem Shop keine.

**HTMX liegt als Datei im Repo**, kein CDN. Das Tailnet ist nicht zwingend
online; im ausgelieferten HTML steht kein Verweis auf eine fremde Domain.

**Jede HTMX-Antwort benutzt dieselbe Vorlage wie der Vollbild-Aufruf.** Sonst
entwickelt sich das Bruchstück von der ersten Ansicht weg und niemand merkt
es. Alle Formulare funktionieren auch ohne JavaScript — HTMX bekommt das
Bruchstück, ein Formular ohne JS eine `303`-Weiterleitung.

**Rezepte greifen über den Namen, nicht über Ähnlichkeit.** Ein Rezept greift
dann und nur dann, wenn sein Name im Satz steht. „Nudelauflauf trifft Lasagne"
wäre gelegentlich hilfreich und gelegentlich falsch — und ein Vorschlag,
dessen Zustandekommen niemand erklären kann, ist in einem Projekt, das sich
über Nachvollziehbarkeit definiert, der falsche Handel.

**Denken ist für die zwei Agentenstufen abgeschaltet** (`enable_thinking:
false`, je Anfrage, der Server bleibt unverändert). Nicht aus Geschmack: der
erste Lauf gegen die echte Box brach mit „kein JSON" ab, weil Qwens
Denk-Vorrede rund tausend Token gegen `max_tokens` zählte und das JSON
**abgeschnitten** ankam. Ein grösseres Budget hätte nur die Wartezeit
verlängert.

## Was am Entwurf schwach ist

* **Die Suche kennt nur Präfixe, keine Infixe.** „milch" findet „Landmilch"
  nicht über den Namen (nur über die Kategorie), „Klopapier" findet nie
  „Toilettenpapier" — dort ist das Umformulieren ausdrücklich Aufgabe des
  Modells. Ein deutscher Katalog steckt die Substantive gern ans Ende von
  Komposita; das ist die grösste einzelne Schwäche des Retrievals.
* **Listen sind hart bei 60 Produkten gekappt, ohne Blätterfunktion.**
  `categories.by_category()` kann blättern, die Oberfläche benutzt es nicht.
  Wer in einer grossen Warengruppe stöbert, sieht die ersten sechzig
  alphabetisch und danach nichts. Auf dem Handy ist das erträglich, richtig
  ist es nicht.
* **HTMX setzt die URL nicht um** (kein `hx-push-url`). Suche und
  Kategoriewechsel tauschen nur die Liste; ein Reload landet wieder im
  unbeschränkten Katalog. Der Zurück-Knopf des Browsers führt aus der Ansicht
  heraus statt einen Schritt zurück.
* **Warenkorb und Pick-Ansicht zeigen NICHT, dass ein Produkt aus dem Katalog
  verschwunden ist.** Nur die Rezeptansicht tut es. Wer eine Bestellung
  abschickt, deren Produkt der letzte Crawl ausgelistet hat, erfährt es erst
  im Laden. Offenes Ticket **WB-335**.
* **`korb_anzahl` zählt Zeilen, nicht Stück.** Zwei Packungen Butter sind eine
  Zeile; im Kopf steht `1`. Für den Zweck („liegt was drin?") reicht es, aber
  die Zahl heisst nicht, was sie zu heissen scheint.
* **Freitext wird gross-/kleinschreibungsempfindlich zusammengeführt.**
  `einlegen()` vergleicht den Text exakt, also liegen „Butter" und „butter" als
  zwei Zeilen im Korb. Der Chat entdoppelt beim Schreiben zwar mit `casefold`,
  aber wer beides von Hand eintippt, bekommt zwei Zeilen.
* **Die Oberfläche wurde nie in einem echten Browser auf einem Handy
  angesehen.** Sie ist für 390 px entworfen und über HTTP-Tests geprüft; ob
  bei 390 px wirklich nichts quer scrollt, hat niemand mit Augen bestätigt.
