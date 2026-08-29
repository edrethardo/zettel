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

### Manche Wörter sind Oberbegriffe — erst die Sorte, dann das Produkt

„Aufschnitt" ist kein Produktwunsch, sondern ein Regal. Vor WB-368 kamen
darauf sechs Produkte, die zufällig das Wort im Namen tragen (Jagdwurst
Aufschnitt, Kochschinken Aufschnitt); seither kommen die **Sorten des
Katalogs** mit ihren echten Stückzahlen — Rohschinken & Bacon (58),
Kochschinken (42), Brühwurst (40), Geflügelwurst (40), Salami (34), Sülze &
Wurst in Aspik (13). Mehrere lassen sich ankreuzen, denn „Aufschnitt" heisst
oft Salami UND Kochschinken.

**Die Sorten kommen aus dem Kategoriebaum, nicht aus dem Modell.** Das ist der
ganze Unterschied: sie sind vollständig, sie sind richtig geschrieben, und
eine Sorte ohne Produkte kann in einem `GROUP BY` gar nicht erst auftauchen.
Dasselbe mit Qwen erzeugt (gemessen) lieferte „SCHWEINEBRUST" mit null
Treffern, „Bananen" doppelt und ein verstümmeltes „Birn". Ein Vektorindex
wäre hier mehr Technik für ein Problem, das der Kategoriebaum schon löst.

Das Modell wird trotzdem gebraucht, aber für die **Zuordnung, nicht die
Erfindung**: „Nudeln" ist keine Kategorie, das steckt unter „Reis, Pasta &
Getreide". Es wählt aus den 56 vorgelegten Kategorienamen — und was nicht in
der Vorlage stand, wird verworfen, genau wie eine erfundene Produkt-ID. Ist
das getippte Wort selbst ein Kategoriename, fällt auch dieser Aufruf weg: der
Katalog antwortet in 0,0 s, und zwar auch bei schlafender Box.

Ein Tipp auf „Salami" führt zurück in den gewöhnlichen Ablauf — Kandidaten,
Auswahl, Ja/Nein, Alternativen. Kein zweiter Mechanismus daneben. Und wer die
Rückfrage nicht will, überspringt sie mit einem Tipp und sucht direkt nach
dem, was er getippt hat.

### Nichts landet ungefragt im Warenkorb

Ein Chat-Zug erzeugt eine Liste in `chat_suggestion`, die zeilenweise mit „Ja"
oder „Nein" entschieden wird. Erst „Ja" legt ein. Diese Entscheidung ist
zugleich das Eval-Label (siehe `OBSERVABILITY.md`) — das Label fällt aus dem
Produkt heraus, weil die Nutzerin die Liste ohnehin durchgehen muss.

### Keine Entscheidung ist endgültig — und der Korb bleibt trotzdem stehen

Ein Tipp ist ein Tipp, kein Urteil: „Ja", „Nein" und auch eine Korrektur
lassen sich zurücknehmen (WB-361). Der Rückweg führt auf `offen` — den Zustand
VOR dem Fehltipp — und nicht auf die andere Seite; „doch behalten" wäre eine
neue Behauptung statt der Rücknahme einer alten. Er ist Datenqualität und
keine Bequemlichkeit: auf dem Telefon sitzen die beiden Knöpfe nebeneinander,
und ein Fehltipp verfälscht sonst genau die Zahlen, die dieses Projekt
interessant machen — das Eval-Label und (ab WB-341) das Vorlieben-Signal.

**Der Korb wird beim Zurücknehmen NICHT angerührt.** `orders.einlegen()` fasst
gleiche Zeilen zusammen, die Korbzeile kann also längst eine sein, die die
Nutzerin selbst aufgestockt hat; sie hier herauszunehmen hiesse, fremde Mengen
zu löschen. Im Korb steht ein Löschknopf. Verschwiegen wird es aber nicht — an
der zurückgenommenen Zeile steht, dass die Korbzeile bleibt und wo sie
wegzubekommen ist.

Der Rückweg macht dabei eine Zusicherung kaputt, die vorher hielt: **der
Schutz gegen den doppelten Tipp durfte nicht länger am Vergleich der letzten
Entscheidung hängen.** „steht schon auf `kept`" ist kein Schutz mehr, sobald
es einen Umweg über `offen` gibt — „Ja, rückgängig, Ja" liefe zweimal durch
`orders.einlegen()` und stockte die Menge auf. Er hängt jetzt an
`chat_suggestion.eingelegt_at`: „war diese Zeile schon einmal im Korb". Das
ist die Frage, die er die ganze Zeit stellen wollte.

**Auch der Sammelknopf hat einen Rückweg** (WB-397). „Alles übernehmen" war
danach die einzige Entscheidung im Shop ohne einen — und ausgerechnet die, die
eine ganze Liste auf einmal entscheidet; elf Zeilen einzeln zurückzunehmen
sind elf Tipps auf einem Telefon. „Doch nicht alles" nimmt den letzten
Sammelvorgang zurück und **ausschliesslich ihn**:

```
Butter einzeln „Ja"     -> kept   (ihre Entscheidung)
Spinat einzeln „Ja"     -> kept   (ihre Entscheidung)
„Alles übernehmen"      -> 9 weitere auf kept
„Doch nicht alles"      -> NUR diese 9 zurück auf offen
```

Das ist die Zusicherung des Sammelknopfs, rückwärts gelesen: er rührt
ausdrücklich nur die OFFENEN Zeilen an, damit ein einziger Tipp nicht die
Labels umkippt, die die Nutzerin einzeln gesetzt hat. Ein Rückweg, der Butter
und Spinat mitnähme, wäre derselbe Fehler in die andere Richtung.

Dafür trägt jede gesammelt entschiedene Zeile die Nummer ihres Vorgangs
(`chat_suggestion.sammel_nr`), und ein EINZELNER Tipp löscht sie wieder — ab
da gehört die Entscheidung ihr. Eine Gruppierung über `decided_at` täte es
nicht: `jetzt()` ist sekundengenau, ein einzelnes „Ja" in derselben Sekunde
fiele in die Gruppe, und ein Sammelvorgang über eine Sekundengrenze zerfiele
in zwei. Der Knopf steht dort, wo eben noch der Sammelknopf stand — der
verschwindet ja genau dann, wenn nichts mehr offen ist, also unmittelbar nach
dem Sammeltipp.

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

### Der Chat hat einen eigenen Ort und hängt trotzdem am Korb

Spec 9 sagte bis WB-382 wörtlich: *„Chat — gehört zum Warenkorb, kein eigener
Ort."* Der Satz beantwortete zwei Fragen auf einmal, und nur eine der beiden
Antworten trug.

**Was bleibt: der BESITZER.** Der Verlauf hängt an der Bestellung
(`chat_message.order_id`), nicht an einer Sitzung und nicht an einem Gerät.
Daran hängt mehr, als es aussieht: er wandert beim Abschicken mit, die
Entscheidungen bleiben bei dem Einkauf, zu dem sie gehören, und aus genau
diesen Entscheidungen werden die Eval-Labels (WB-329). Zwei Telefone sehen
denselben Chat, wie sie denselben Korb sehen — genau ein `draft`, gemeinsam
(Spec 4). **Am Datenmodell hat WB-382 nichts geändert.**

**Was nicht mehr trägt: der gemeinsame PLATZ.** Aus „gehört zum Warenkorb"
folgt nicht „muss auf derselben Seite stehen". Die Vermutung dahinter war, dass
man beim Bestätigen den Korb wachsen sehen will. Sie hat den Shop seine grösste
Seite gekostet: 257 KB vor WB-372, danach 45 KB, und beides drängelte sich um
denselben Raum — der Korb stand über 157 Vorschlagszeilen, an die als Nächstes
noch das vorgeschlagene Rezept sollte.

**Das Ankommen im Korb ist dabei die eigentliche Sache** und nicht die Adresse.
Wer im Chat „Ja" tippt, muss ohne Seitenwechsel merken, dass etwas angekommen
ist. Es sagen jetzt drei Stellen, und jede tut etwas, was die anderen nicht
können:

* **die Zeile selbst** — „Liegt jetzt im Korb — 5 Sachen drin." Nur in der
  Antwort auf genau diesen Tipp, nicht im gerenderten Verlauf: dort ist nichts
  „gerade" passiert. Sie ist die wichtigste der drei, weil dort der Daumen
  steht — die Knöpfe sitzen mitten in einer Liste von 150 Zeilen.
* **die Korbbrücke** über dem Verlauf — „5 Sachen im Korb — ansehen". Sie klebt
  beim Scrollen oben und wird bei jeder Entscheidung out-of-band getauscht, die
  Zahl zählt also sichtbar hoch. Sie ist zugleich der Weg in den Korb, ohne die
  quer scrollende Navigationsleiste.
* **der Kopfzähler** aus WB-372 — er steht ohnehin auf jeder Seite.

Die Marke „im Korb" an der entschiedenen Zeile bleibt daneben stehen. Sie ist
ein ZUSTAND und sieht nach dem Neuladen genauso aus; was fehlte, war das
EREIGNIS.

**Gemessen, weil sonst niemand es glaubt** (`scripts/groesse_probe.py`, 17
Züge, 153 Vorschläge, derselbe Verlauf wie in WB-372):

| | vorher (eine Seite) | nachher |
|---|---|---|
| Seite mit dem Chat | 50.934 Bytes | 44.088 Bytes |
| Korb für sich | — | 8.054 Bytes |
| ein „Ja" | 10.412 Bytes | **2.249 Bytes** |
| Formulare je „Ja" | 21 | 3 |

Der Tipp ist beim Umzug nebenbei um drei Viertel billiger geworden, und das war
kein eigener Handgriff: er trug den ganzen Korb mit — Zeilen mit Bildern,
Mengenformularen und Ladenauswahl —, weil der Korb danebenstand. Jetzt trägt er
die Brücke, die Kopfzahl und einen Satz.

**Kein Redirect-Karussell.** Ein „Ja" bleibt eine HTMX-Teilantwort und springt
nicht auf den Korb und zurück; ohne JavaScript geht dieselbe Adresse wie vorher
auf die Chatseite.

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

Die Tailnet-Adresse selbst steht seit WB-388 nicht mehr im Code: sie ist die
eines privaten Geräts, und das Repo ist zur Veröffentlichung gedacht. Der
Start erfragt sie über die Routing-Tabelle (`eigene_tailnet_adresse()`); ohne
Tailnet bindet der Shop nur loopback, und `PICKNICK_HOST` erzwingt eine
Adresse — die trotzdem durch die Weissliste muss.

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

Das Rendern der Chatseite fragt die Box **nicht** — der Zustand wird per HTMX
nachgeladen. Sonst hinge jeder Blick am health-Timeout. Der Aufruf, der das
Nachladen macht, ist zugleich der, der `wake-vllm` anstösst; er steht deshalb
seit WB-382 nur noch auf `/chat` und nicht mehr im Warenkorb — ein Weckruf beim
Blick in den Korb wäre einer für nichts.

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

**Ein geholtes Rezept hat zwei Zutatenlisten, und das ist Absicht.**
`recipe_ingredient` ist, was im Rezept steht („500 ml Tomaten, passierte");
`recipe_item` ist, was dafür gekauft wird (Katalogprodukt oder Freitext).
Beides in eine Tabelle zu legen hiesse, jeder Zeile eine Menge in Packungen
UND eine in Litern zu geben. Der Nebeneffekt ist die Weiche zwischen zwei
Wegen: solange `recipe_item` leer ist, fängt `rezeptweg.erkenne` den Chat-Zug
nicht ab (es hätte nichts vorzuschlagen) und die Quelle übernimmt. Ordnet
jemand von Hand Produkte zu, greift wieder der Rezeptweg — und das ist
richtig, denn dann stehen dort die Produkte, die ein Mensch ausgesucht hat.

**Die Zutat hinter einem Suchbegriff wird gerechnet, nicht erfragt** (WB-369).
Der Chat-Weg trug lange keine Mengen: das Modell macht aus Chefkochs
Zutatenliste Suchbegriffe, und `amount`/`unit` fielen dabei weg. Das sieht nach
einer Modellaufgabe aus („sag mir zu jedem Begriff die Zutat") und ist keine —
der Begriff ist ja AUS dem Zutatennamen gemacht, seine Wörter stehen also noch
darin. `assistant.herkunft` vergleicht sie und braucht dafür weder einen
zweiten Aufruf noch eine Prompt-Zeile. Gemessen an den drei echten
Chefkoch-Zügen der Datenbank: **37 von 38 Begriffen fanden ihre Zutat, keiner
die falsche.** Dieselbe Sorte Befund wie in WB-368, wo sich eine vermeintliche
Modellaufgabe als `GROUP BY` herausstellte.

Die Gegenrichtung wäre gewesen, das Modell die Zuordnung mitliefern zu lassen
(eine Nummer je Zeile, gegen die Zutatenliste geprüft wie die Produkt-IDs in
`plan.choose`). Sie kostet Token, Prompt-Fläche und eine neue
Halluzinationsfläche — und der Code kann es messbar. **Wo die Zuordnung nicht
eindeutig ist, gibt es keine Menge**, und die Zeile verhält sich wie vorher:
eine fehlende Menge kostet eine Packung zu viel, eine falsche wäre eine Zahl im
Korb, die aussieht wie eine gerechnete.

**Der Abruf bei Chefkoch läuft im Request — und lief es zwei Tickets lang
nicht** (WB-338, revidiert in WB-367). Die erste Fassung trug einen Wunsch in
`dish` ein und startete `python -m picknick.gerichte.lauf` als eigenen
Prozess, weil Spec 3 sagte, der Web-Prozess rufe nie eine fremde Seite auf.
Der Preis stand hier benannt: der erste Satz zu einem neuen Gericht ging noch
übers Modell, erst der zweite nahm das Rezept.

**Die Begründung trug nicht, und das ist gemessen** (2026-08-28, gegen
api.chefkoch.de):

| Gericht | Suche | Detail | zusammen |
|---|---|---|---|
| Chili con Carne | 131 ms | 16 ms | **147 ms** |
| Kartoffelsalat | 92 ms | 17 ms | **109 ms** |
| Sushi | 70 ms | 20 ms | **90 ms** |
| Ratatouille | 93 ms | 20 ms | **114 ms** |
| *der Weg, der stattdessen genommen wurde: das Modell (Pho)* | | | **35.600 ms** |

Rund 250-mal schneller als das Ausweichen — und der Chat blockiert in
derselben Sekunde 20 bis 35 s auf der vLLM-Box, also auf einer anderen
Maschine im LAN. Ein Chat, der auf ein Modell warten darf, aber nicht 100 ms
auf ein Rezept, ist inkonsequent. Spec 3 wurde deshalb nicht gestrichen,
sondern verengt auf den Teil, der das Produkt trägt: **der KATALOG wird nie
live abgefragt.** Dort ist der Abruf teuer (36 min Vollcrawl), das Ergebnis
darf altern, und ein Ausfall von knuspr.de darf das Einkaufen nicht
verhindern.

**Vom Hintergrundlauf bleibt das Kommando, nicht der Prozessstart aus dem
Shop.** `python -m picknick.gerichte.lauf --gericht/--alle` trägt weiterhin
das Vorwärmen („hol die zehn Gerichte, die wir dauernd kochen"), das
Nachholen dessen, was eine Störung liegen liess, und die Frage, ob die Quelle
überhaupt noch antwortet. Was verschwunden ist, ist der `subprocess.Popen`
aus dem Web-Prozess: ein Lauf, auf dessen Ergebnis niemand wartete, war nur
die Umgehung von Spec 3 und hatte einen eigenen stillen Fehlerfall (ein
Prozessstart, der schiefging, ohne dass es jemand sah).

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
  ist es nicht. Seit WB-375 sagt die Liste es wenigstens („60 von 657") —
  geschwiegen hat sie vorher, und das war die schlimmere Hälfte: wer sein
  Produkt nicht sah, hielt den Katalog für lückenhaft statt seine Suche für zu
  weit.
* **Ein noch unbekanntes Gericht kostet ZWEI Modellläufe** (WB-367). Der
  Gerichtsname steht nirgends im Satz markiert; er kommt aus Stufe 1, und die
  ist ein Modelllauf von 20 bis 35 s. Erst danach lässt sich das Rezept
  holen — und dessen Zutatenliste braucht wieder das Modell, um Suchbegriffe
  daraus zu machen. Der erste Satz zu einem neuen Gericht dauert also grob
  doppelt so lange wie jeder weitere, und die geratene Zutatenliste aus
  Stufe 1 wird dabei weggeworfen. Der Handel ist bewusst: lieber einmal
  länger warten als eine geratene Liste bekommen und es nicht merken. Billiger
  würde es nur mit einer Gerichtserkennung ohne Modell — und die wäre wieder
  Raten, diesmal an einer Stelle, an der es niemand sieht.
* **Die Frist ist eine je Anfrage, keine für den ganzen Abruf.** 2 s für die
  Suche und 2 s für das Detail; im schlimmsten Fall wartet der Chat also vier
  Sekunden, bevor er auf das Modell zurückfällt. Gemessen sind es 90 bis
  147 ms, und neben 35 s Modelllauf fällt auch der schlimmste Fall nicht auf
  — aber „zwei Sekunden Frist" heisst eben nicht „nach zwei Sekunden ist
  Schluss".
* **Ein Rezept je Gericht, und die Wahl ist eine Formel.** Chefkoch kennt 85
  Pho-Rezepte; genommen wird das mit der höchsten gewichteten Note. Ob es das
  passendste ist, weiss niemand — „Gemüselasagne" liefert eine
  Spinat-Gemüse-Lasagne, und das ist eine Auslegung, keine Übersetzung.
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
