# Architektur und Entwurfsentscheidungen

Der vollständige Entwurf steht in
`docs/superpowers/specs/2026-08-28-zettel-design.md`. Hier stehen die
Entscheidungen, die man kennen muss, um den Code zu lesen — und die, die von
aussen wie ein Versehen aussehen und keines sind.

> Das Projekt hiess bis zum 30.08.2026 „Picknick" (WB-401, Begründung im
> README unter „Der alte Name: Picknick"). Die Namen in diesem Dokument sind
> mitgezogen; wer in Git-Historie, Phoenix-Projekten oder älteren Notizen
> unterwegs ist, findet dort weiter `picknick/`, `PICKNICK_*` und
> `Picknick Agent`.

## Die Form

Ein Prozess, eine Datei, kein Build-Schritt.

```
zettel/
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
`zettel.rejected` am Span **und** als Satz über der Vorschlagsliste: ein
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

### Was kein Modell braucht, wartet nicht auf eines

Der Rezeptwechsel war der einzige Handgriff dieser Oberfläche, der nicht nach
langsam aussah, sondern nach kaputt. Der Nutzer meldete ihn selbst: „Wenn ich
einen anderen Vorschlag aussuche lädt es ewig und dann muss ich scrollen. Das
sollte inplace ersetzen." Nachgemessen (Firefox über geckodriver, Handybreite,
echte Box, 224 Proben über 23 Sekunden):

| | vorher | nachher |
|---|---|---|
| bis die Karte des neuen Rezepts im Bild ist | **nie** | **0,84 s** |
| bis die Vorschläge stehen | 23,1 s | 23,0 s |
| „es läuft" irgendwo im Bild | in 0 von 224 Proben | in 213 von 222 |
| Antwortgrösse | 97.966 Bytes | 3.040 + 35.602 Bytes |
| Bildlauf | scrollY 9964, unverändert | auf die Auskunft geholt |

Der Hebel ist eine Aufteilung, keine Beschleunigung: **die Karte braucht kein
Modell.** Name, Gesamtzeit, Ruhezeit, Zutatenliste der Quelle, Bewertung und
Herkunft stehen in `recipe` und `recipe_ingredient`, sobald das Detail geholt
ist — gemessen 16 bis 20 ms. Die zwei Modellstufen kostet allein die
Vorschlagsliste darunter. Also antwortet der Tipp mit der Karte und lässt die
Liste nachlaufen (`hx-trigger="load"`); die Zeit bis zum Ergebnis ändert sich
dabei nicht, die Zeit bis zur Rückmeldung um zwei Grössenordnungen.

Dazu drei kleinere Griffe, die alle aus WB-372 und WB-378 stammen und hier nur
noch nachgeholt wurden:

* **das Tauschziel ist der Zug und nicht der Chat.** Der Rezeptwechsel war das
  letzte Formular des Chats, das noch `hx-target="#chat"` trug — er tauschte
  nach 23 Sekunden den kompletten Verlauf aus.
* **der Indikator ist das Tauschziel.** `#chat-laeuft` steht am Seitenfuss; in
  201 Proben über 24 Sekunden war er kein einziges Mal im Bild. Die Auskunft
  steht jetzt in dem Kasten, der gleich ersetzt wird — und darin ÜBER der
  Karte, denn die ist eine Bildschirmhöhe lang.
* **`show:` holt die Antwort ins Bild.** Ohne es ändert sich im sichtbaren
  Bereich nichts; dasselbe Mittel wie bei der Katalog-Trefferliste.

**Der alte Zug blieb dabei zunächst stehen**, im Dokument wie in der Datenbank
(`hx-swap="afterend"`). Das war die halbe Kur, und der Nutzer hat es sofort
gemeldet — siehe den nächsten Abschnitt.

### Ein Wechsel ist ein Ersatz und kein Nachtrag

„Das ist auch katastrophal. Sorg dafür dass das inplace passiert anstatt dass
gescrollt wird." (WB-403.) `afterend` hängte den neuen Zug HINTER den alten
und `show:` holte den Blick dorthin — beides zusammen ist genau der Bildlauf,
den er meint. Die eigene Datenbank zeigte den Schaden am Bestand: drei
„Pizza bufala"-Züge untereinander, zwei davon nahezu gleich, weil er zweimal
ein anderes Rezept gewählt hatte.

**Das Argument für das Anhängen trug nicht.** Angelegt wurde der zweite Zug
wegen der Eval-Labels (WB-387): jeder Zug trägt seine `mapping_precision`.
Nach Spec 8.1 zählen offene Vorschläge aber nirgends — weder im Zähler noch
im Nenner —, und beim Wechsel ist zum alten Rezept per Definition noch nichts
entschieden. Ein Zug, dessen Zeilen alle offen sind, trägt `quote = None` und
liefert überhaupt kein Label.

Also ersetzt der Wechsel den Zug an seiner Stelle. Drei Entscheidungen dazu:

* **Markiert wird, nicht gelöscht.** Die neue Chatzeile trägt in
  `chat_message.ersetzt` die id der abgelösten (dieselbe Richtung wie
  `corrected_from` in WB-359), und `verlauf()` zeigt von einer Kette nur ihr
  letztes Glied. Löschen wäre kürzer und wäre falsch: an der alten Zeile
  hängen Vorschläge, `ON DELETE CASCADE` nähme sie mit, und darunter wären
  die entschiedenen — die mit Label und Korbwirkung.
* **Was entschieden wurde, bleibt stehen.** Eine abgelöste Zeile mit einem
  „Ja" oder „Nein" fällt nicht aus dem Verlauf, sondern schrumpft auf genau
  diese Zeilen zusammen: kein Rezept mehr (es ist abgewählt), keine offenen
  Vorschläge (sie meinten es), keine Sammelknöpfe. Was übrig bleibt, ist eine
  Quittung — und der Weg zurück, denn ein „Ja" ist rücknehmbar (WB-361).
* **`ersetzt` ist auch die Sortiergrösse.** `ORDER BY coalesce(ersetzt, id)`
  hält den neuen Zug an der Stelle des alten. Sonst stünde er im Dokument
  mittendrin und nach dem nächsten Neuladen ganz unten — und ein Wechsel wäre
  wieder das, was der Nutzer gemeldet hat: etwas, das die Seite umbaut.

**Und der Bildlauf wird ausdrücklich festgehalten.** Zwischen den beiden
Hälften steht an der Stelle des Zugs nur die Karte: keine Vorschlagsliste,
keine Zutaten. Die Seite schrumpft dabei um mehrere tausend Pixel und wächst
danach wieder; Firefox' „scroll anchoring" gleicht das aus, hält sich aber an
einen Knoten seiner Wahl — am Messstand landete der Blick nach dem zweiten
und dritten Wechsel am Seitenende, 3.839 und 4.455 Pixel unter dem Tipp.
Fünfzehn Zeilen in `chat.html` merken sich stattdessen, wie weit der obere
Rand des getauschten Stücks vom Fensterrand entfernt war, und setzen das neue
genau dorthin. Das ist wörtlich, was „an Ort und Stelle" heisst.

Dazu zwei Nachzieher, die aus dem Ersetzen folgen: der Indikator ist jetzt
der Wartekasten selbst (den alten Zug gibt es nicht mehr), und die
Fehlerantwort bringt den alten Zug wieder mit — sonst bliebe eine Lücke, wo
eben noch die Karte war, und ein misslungener Wechsel sähe aus wie ein
geglückter.

Dreimal hintereinander gewechselt, an der echten Box und an einer Kopie der
echten Datenbank (`scripts/dreh/wechsel_probe.py`, Firefox über geckodriver,
390 px):

| Wechsel | scrollY | Seitenhöhe | Zug-Kästen | Karte im Bild | „läuft" im Bild |
|---|---|---|---|---|---|
| 1 → Pizza Fiji | 9889 → **9889** | 14.487 → 14.250 | 6 → **6** | 0,12 s | 142 von 144 |
| 2 → Familienpizza | 10110 → **10110** | 14.250 → 14.771 | 6 → **6** | 0,11 s | 168 von 170 |
| 3 → sehr ursprünglich | 10331 → **10331** | 14.771 → 15.677 | 6 → **6** | 0,11 s | 214 von 216 |

Der Blick steht still, und die Zahl der Zug-Kästen auch. Die 1.190 Pixel, um
die die Seite über drei Wechsel wächst, sind kein Zuwachs an Zügen: die drei
Rezepte sind verschieden gross (24,7 / 29,8 / 35,6 KB Antwort). Zwischen den
beiden Hälften schrumpft die Seite auf rund 11.000 Pixel — dann steht an der
Stelle des Zugs nur die Karte — und kommt danach zurück.

### Umgehängt wird erst, wenn der Zug steht

Die Zweiteilung aus WB-402 hat eine Nahtstelle, und sie ist im Betrieb
aufgerissen (WB-406). Die erste Hälfte holte das Detail **und hängte
`dish.recipe_id` gleich um**; sie kostet kein Modell und kommt deshalb immer
durch. Die zweite läuft durch die zwei Modellstufen und kann ausfallen. Dann
stand die Wahl in der Datenbank, ohne dass je ein Zug zu ihr entstanden wäre.

Gemessen am laufenden Shop, 2026-08-30, aus dem Verlauf des Nutzers: zwei
Wechsel, alle vier Requests mit 200 beantwortet — und danach

    dish 8 „Lasagne Bolognese"  ->  recipe 13 „Lasagne alla Bolognese
                                    mit Béchamelsoße"
    chat_rezept zu Zug 38       ->  recipe 11 „Lasagne"
    chat_message                    kein einziger neuer Zug

Im Chat stand die Karte „Lasagne", markiert als „vorgeschlagen", und darunter
„Lasagne alla Bolognese mit Béchamelsoße" als etwas, das man noch **wählen
kann**. Man hatte es zwanzig Minuten vorher gewählt.

Die Phoenix-Spuren nennen den Auslöser: beide `chat.turn` endeten nach genau
3,04 s mit `ChatNichtVerfuegbar` — dem health-Timeout aus `zettel.llm.wake`.
**Der Wecker hatte recht**: eine rohe `/v1/models`-Anfrage lief zur selben
Zeit in dieselbe Zeitüberschreitung. Nicht der Weckzustand war der Fehler,
sondern was der Wechsel aus ihm machte.

Also hängt jetzt nur die zweite Hälfte um, und scheitert sie, zeigt das
Gericht wieder auf das Rezept, **das der Chat zeigt** — die Karte des Zugs,
der stehen bleibt. Das ist dieselbe Zusicherung wie beim Ersetzen des Zugs:
was im Dokument steht und was in der Datenbank steht, soll dasselbe sein.
Das geholte Detail bleibt liegen; es kostet nichts und macht den nächsten
Versuch anfragenfrei (WB-387).

Und die Meldung hat aufgehört, den Wechsel zu behaupten. Sie lautete „„X" ist
jetzt das Rezept zu „Y". <Weckzustand>" — sie stimmte sogar, und genau das
war der Schaden.

### Die Zuordnung gehört dem Rezept, nicht dem Zug

„Der Wechsel klappt nichteinmal. Fix das so dass es schnell ist. Fix es vor
allem im Design." (WB-408.) Er dauerte 24 bis 27 Sekunden, weil er denselben
Satz noch einmal durch `chat.turn` schickte — beide Modellstufen, jedes Mal,
auch beim Zurückwechseln zu einem Rezept, das eine Minute vorher schon
gerechnet worden war.

**Das war kein fehlender Zwischenspeicher, sondern eine falsche Zugehörigkeit.**
Auf dem Quellenweg hängt die Modellarbeit an nichts, was ein Wechsel ändert:

| Stufe | Eingabe | hängt ab von |
|---|---|---|
| `plan.zutatenbegriffe` | Zutatenliste, Titel, Portionen | **nur dem Rezept** |
| `catalog.search` | die Begriffe | Rezept + Katalog |
| `plan.choose` | Begriffe und Kandidaten, **ohne den Satz** (WB-386) | Rezept + Katalog |

Der Satz steht seit WB-386 ausdrücklich nicht mehr in Stufe 3 — er kostete
dort die ganze Zutatenliste, sobald das geholte Rezept nicht zu ihm passte.
Damit ist die gesamte Modellarbeit eines Rezeptzugs eine **reine Funktion des
Rezepts und des Katalogs**. Sie wurde bloss jedem Zug einzeln in Rechnung
gestellt.

Also steht sie jetzt am Rezept (`recipe_zuordnung`): je Begriff die Kette aus
Stufe 1 und das Produkt aus Stufe 3. Ein Zug baut seine Vorschlagsliste daraus
zusammen; gerechnet wird einmal.

Gemessen am laufenden Shop, echte Box, echter Katalog (2026-08-30):

| | Zeit |
|---|---|
| Wechsel auf ein **unbekanntes** Rezept | 30,2 s |
| Wechsel auf ein **gemerktes** Rezept | **0,29 s** |
| die Karte davor (unverändert, WB-402) | 0,04 s |

Hundertmal schneller, und die Vorschlagsliste ist dieselbe — dieselben 13
Zeilen, dieselben Produkte.

**Drei Folgen, die nicht Geschwindigkeit heissen.**

* **Ein Wechsel braucht kein Modell mehr.** Zu einem gemerkten Rezept läuft er
  auch bei schlafender Box. Genau daran waren die zwei letzten Versuche des
  Nutzers gescheitert (WB-406).
* **Dasselbe Rezept kostet die GPU nie zweimal** — auch nicht beim nächsten
  „alles für Lasagne" in einer Woche.
* **Die Zuordnung ist auswertbar geworden.** „Welche Produkte hat das Modell
  diesem Rezept zugeordnet" war vorher eine Frage an die Traces und ist jetzt
  eine Abfrage. Ein Eval kann dieselbe Zeile bewerten, die der Shop benutzt.

`gewaehlt` und `product_id` beantworten dabei zwei verschiedene Fragen. `NULL`
bei `gewaehlt = 1` heisst „das Produkt ist aus dem Katalog gefallen" (`ON
DELETE SET NULL`) und wird neu gefragt; `gewaehlt = 0` heisst „das Modell
wollte hier nichts" und bleibt zu — sonst kostete jede Zutat ohne
Katalogtreffer für immer einen Modellaufruf, und die Antwort wäre jedes Mal
dieselbe. Ein Notbehelf aus `chefkoch.zutat_kette` wird nicht gemerkt: er gäbe
sich für immer als Modellantwort aus. Und ein neu geholtes Rezept vergisst
seine Zuordnung, denn sie gehört zu einer Zutatenliste, die es nicht mehr gibt.

### Gerechnet wird, während der Mensch liest

Das Gemerkte macht den zweiten Tipp sofort. Der ERSTE kostete weiter dreissig
Sekunden — und „quasi sofort" war die Bitte, nicht „beim zweiten Mal sofort".

Also rechnet die Rezeptkarte die obersten **drei** Alternativen vor, während
das Rezept gelesen wird: drei `hx-post` auf `/chat/<mid>/rezept/vorwaermen`
mit `hx-trigger="load delay:1s"`, um je eine Sekunde gestaffelt. Die Box
bedient vier Anfragen nebeneinander mit 74,8 tok/s gegen 24,9 einzeln; ein
eigener Zug soll dabei trotzdem nicht hinten anstehen.

Vier Regeln halten das im Rahmen:

* **Nur am jüngsten Zug.** Die Karten weiter oben stehen im Verlauf und nicht
  vor Augen; für sie zu rechnen hiesse, bei jedem Blick in den Chat die halbe
  Rezeptliste durch das Modell zu schicken.
* **Vorwärmen ist keine Wahl.** Der Zeiger des Gerichts wird nicht angefasst
  (`Quelle.bereitstellen` hängt hinterher zurück), und ein Zug entsteht nicht.
* **Es antwortet immer mit 204.** Niemand hat etwas gefragt; was hier
  schiefgeht, kostet die Wartezeit des nächsten Tipps und sonst nichts. Eine
  Seite, die jemand gerade ansieht, darf daran nicht zerbrechen.
* **Der zweite Aufruf rechnet nicht.** Steht die Zuordnung, ist der Eingang
  eine Abfrage — sonst kostete jedes Ansehen der Seite drei Modellläufe.

Der Span heisst `recipe.zuordnung` und nicht `chat.turn`. Hier antwortet
niemand jemandem: kein Satz, keine Nutzerin, keine Vorschlagsliste. Ein
Vorwärmlauf unter demselben Namen verdürbe jede Auswertung über Züge.

### Unter 100 ms — und dabei bleibt es, während das Modell antwortet

„Schau dass alles in unter 100ms geht." (WB-409.) Drei Dinge standen dem im
Weg, und keines davon war die Arbeit selbst.

**1. Der Shop stand still, solange irgendwo ein Modell antwortete.** Die
Chat-Eingänge waren `async def` — sie mussten es sein, weil sie den Rumpf
selbst mit `await request.body()` lasen — und riefen darin blockierend das
Modell auf. Damit hielt jeder Zug die Ereignisschleife an:

| | vorher | nachher |
|---|---|---|
| `GET /chat`, während ein Modellaufruf lief | **14,17 s** | **6,6 ms** |
| drei gleichzeitige Vorwärmläufe | 31 / 57 / 57 s (in Reihe) | nebeneinander |

Für BEIDE Nutzerinnen, und für jede Seite — auch die Pick-Liste im Laden. Die
Kur ist eine Abhängigkeit: `formular()` liest den Rumpf auf der
Ereignisschleife, der Eingang selbst ist ein gewöhnliches `def`, und FastAPI
führt ein solches im Threadpool aus.

**2. Ein Chat-Zug machte dreissig fsyncs.** cProfile über einen Zug aus dem
Gedächtnis, echte Datenbank, echter Katalog:

    chat.turn                            264 ms
      _schreiben                         220 ms
        30 × sqlite3.commit              216 ms   (7,2 ms je Aufruf)
      _aus_quelle                         43 ms
        _suchen (13 FTS-Ketten)           31 ms

Die Datenbank lief in WAL, aber mit `synchronous = FULL` — jedes `commit`
drückte die WAL durch. Das ist die Plattenumdrehung und nicht die Arbeit.
`synchronous = NORMAL` ist die übliche Stellung dazu: **264 ms -> 16 ms**, und
die Testsuite fiel nebenbei von 73 s auf 43 s. Verlieren kann das nur ein
Stromausfall, und dann die letzten Sekunden; ein Absturz des Shops nicht.

**3. Der health-Timeout war das Tausendfache der Messung.** Drei Sekunden,
während `GET /v1/models` im Median 2 ms braucht und im schlechtesten von
fünfzehn Fällen 163 ms — und die drei Sekunden wurden voll bezahlt, sooft die
Box nicht bediente. Jetzt eine Sekunde, immer noch das Sechsfache der
langsamsten Messung, dazu ein Befund, der vier Sekunden gilt: dieselbe Frage
in derselben Sekunde geht nicht dreimal zur Box.

Gemessen am laufenden Shop, **während im Hintergrund ein echter Modellaufruf
von 22,2 s lief**:

| | |
|---|---|
| `GET /chat` | 6,6 ms |
| `GET /pick` | 2,3 ms |
| `GET /warenkorb` | 1,8 ms |
| `GET /katalog` | 57,6 ms |
| `GET /chat/zustand` | 50,1 ms |
| **Rezeptwechsel, erste Hälfte** | **3,8 ms** |
| **Rezeptwechsel, zweite Hälfte** | **46,2 ms** |

Ein kompletter Wechsel in 50 ms, neben einem laufenden Modellaufruf. Vorher
waren es 26,7 s, und die Seite daneben stand.

### Die Suche war nie langsam — die Zählung war es

„Speichere außerdem Suchergebnisse dass sie Instant kommen können." Bevor
etwas zwischengespeichert wurde, stand die Frage, warum es überhaupt dauert:

    /katalog?q=bio joghurt natur   1,85 s
      search.count()               1.871 ms   -> 43
      search.search()                  1,3 ms -> dieselben 43
      die reine FTS-Abfrage              0 ms

Beide Abfragen haben dieselbe Bedingung. Der Unterschied stand im Plan:

    count   SEARCH p USING INDEX ix_product_active (active=?)
            SCAN f VIRTUAL TABLE INDEX 0:=M7
    search  SCAN f VIRTUAL TABLE INDEX 0:M7
            SEARCH p USING INTEGER PRIMARY KEY (rowid=?)

`count` lief über alle zehntausend aktiven Produkte und stellte je Zeile eine
FTS-Anfrage; `search` liest aus der FTS heraus, weil `ORDER BY bm25` den
Planer dazu zwingt. `CROSS JOIN` ist in SQLite kein anderer Join, sondern die
Anweisung, die Reihenfolge nicht zu vertauschen — **1.871 ms -> 0,3 ms**
(WB-410).

**Ein Zwischenspeicher hätte das verdeckt.** Er hätte die zweite Suche schnell
gemacht und die erste bei 1,85 s gelassen, und niemand hätte je wieder
hingesehen. Die Katalogsuche braucht heute keinen: sie liegt zwischen 13 und
58 ms.

### Was der Shop einmal gewählt hat, wählt er nicht noch einmal

Gespeichert gehört etwas anderes — das, was wirklich kostet (WB-411):

    plan.extract      Median 11,53 s
    plan.choose       Median 14,71 s
    catalog.search    Median  0,00 s   (264 Suchen zusammen 0,5 s)

Die Suche im eigenen Katalog ist gratis. Teuer ist die **Wahl**, und sie ist
je Begriff eine eigene kleine Frage: „welches dieser zwanzig Produkte ist
‚Tomatenmark'". Der Shop stellte sie immer wieder.

Wie oft, ist gemessen. Über sieben Lasagne-Rezepte kannte jedes 36 bis 62 %
seiner Begriffsketten schon aus den anderen — „Zwiebel" sechsmal,
„Tomatenmark", „Butter", „Milch" je fünfmal; über den ganzen Rezeptbestand
wiederholen sich 51 % der Zutatennamen.

`begriff_wahl` merkt die Kette und das gewählte Produkt. Der Schlüssel ist die
KETTE und nicht ihr erstes Wort: „Möhren" und „Karotten" führen zu
verschiedenen Produkten (WB-340). Die **Kandidatenliste** wird nicht gemerkt —
sie kostet nichts und wird jedes Mal neu gesucht; eine Erinnerung gilt nur,
solange ihr Produkt heute wieder vorgelegt wird. Damit bekommt die Zusicherung
aus `plan.choose` keine Hintertür.

Dazu die beiden Hälften, die der Nutzer verlangt hat: **gemeldet** wird es in
der Antwort („2 von 2 Zeilen kamen aus dem Gedächtnis"), damit der Satz im
Verlauf steht und das Neuladen übersteht — und **„Neu suchen"** an jedem Zug
mit Vorschlägen fragt noch einmal, ohne Gedächtnis, und überschreibt es.
Gelesen wird dann nicht, geschrieben schon: sonst hiesse „neu suchen" nur
„diesmal anders", und beim nächsten Satz stünde die verworfene Wahl wieder da.

**Ehrlich zur Reichweite.** Drei nicht verwandte Gerichte nacheinander —
Käse-Lauch-Suppe, Kartoffelgratin, Zwiebelsuppe — teilten **keine einzige**
Begriffskette (0 von 18). Das Gedächtnis zahlt sich innerhalb einer
Rezeptfamilie aus, nicht darüber hinaus.

### Stufe 3 fragt in vier Spuren gleichzeitig

Für ein wirklich neues Gericht blieben die zwanzig Sekunden. Stufe 3 besteht
aus lauter unabhängigen Fragen, und die Box bedient vier Anfragen nebeneinander
mit 74,8 tok/s gegen 24,9 einzeln. Gemessen an Pho Bo (17 Begriffe mit
Kandidaten, echte Box, echter Katalog):

| Spuren | Zeit | gewählt | |
|---|---|---|---|
| 1 | 16,7 s | 11 | |
| 2 | 11,1 s | 11 | 1,51× |
| **4** | **9,4 s** | **11** | **1,78×** |
| 6 | 8,7 s | 12 | 1,92× |

Vier, weil danach kaum noch etwas kommt — von vier auf sechs sind es 0,7 s,
und jede weitere Spur zahlt den Systemprompt noch einmal.

**Jede Spur bekommt nur ihre eigenen Kandidaten**, und damit wird die
Zusicherung schärfer statt weicher: eine Antwort kann kein Produkt aus einer
anderen Spur nennen. Verteilt wird reihum und nicht in Blöcken, damit jede
Spur einen Querschnitt bekommt; die Reihenfolge der Antwort bleibt die der
Aufgaben, denn sie ist die Reihenfolge der Vorschlagsliste.

Im Trace heissen jetzt vier Spans `plan.choose` statt eines — das ist die
Wahrheit über vier Modellaufrufe. `obs.stufe(..., mehrfach=True)` sorgt
dafür, dass sie alle so heissen und nicht nur der schnellste.

Ein ganzer Zug zu einem **neuen** Gericht, gemessen an der echten Box:

| | |
|---|---|
| eine Spur, leeres Gedächtnis | 29,1 s |
| vier Spuren, leeres Gedächtnis | **16,6 s** |
| dasselbe Gericht noch einmal | **< 0,1 s** |

### Stufe 1 verträgt das Zerlegen NICHT — gemessen, nicht vermutet

Nach WB-412 lag es nahe, dasselbe mit Stufe 1 zu machen: sie ist mit 11,53 s
Median der zweitteuerste Teil, und die Zerlegung von Stufe 3 hatte nichts
gekostet. **Es ist aber nicht dieselbe Aufgabe.** Stufe 3 beantwortet je
Begriff eine für sich stehende Frage; Stufe 1 bekommt EINE Zutatenliste und
soll daraus EINE Liste machen — sie sieht heute alles auf einmal und nutzt
das.

`scripts/spur_probe.py` hat es ausgemessen: 15 Rezepte ab acht Zutaten, drei
Varianten, je zwei Läufe, 88 Messzeilen (`evals/spur_probe-2026-08-30.jsonl`).

| Variante | Sekunden | Ketten | Abdeckung | Dubletten | Deckung |
|---|---|---|---|---|---|
| ganz | 9,9 s | 11,0 | 69 % | **0** | **100 %** |
| 2 Spuren | 6,6 s | 11,0 | 69 % | 4 | 87 % |
| 4 Spuren | 5,5 s | 11,0 | 68 % | 6 | 87 % |

**Das Rauschband ist null.** Dreissig Läufe von `ganz`, keine einzige
Abweichung, identische Zeiten auf die Zehntelsekunde — bei Temperatur 0 mit
guided JSON ist diese Stufe reproduzierbar. Damit ist jede Abweichung der
zerlegten Läufe dem Zerlegen zuzuschreiben und nicht dem Zufall. Genau dafür
war das Band da.

Drei Befunde, und der dritte entscheidet:

* **14 von 15 Rezepten ändern sich.** Im schlimmsten Fall stimmen nur 62 %
  der Ketten überein (Ratatouille), 67 % bei „Einfache Lasagne Bolognese".
  *Das allein wäre kein Urteil* — eine andere Kette kann gleich gut sein
  („Möhren" statt „Karotten"). Die Probe misst Abweichung, nicht Güte.
* **Dubletten, wo es vorher keine gab** (0 -> 4 -> 6, in zwei Rezepten).
  Keine Spur sieht die andere, also kommt dieselbe Zutat zweimal auf den
  Zettel. Das ist kein Geschmacksurteil, das sieht man.
* **Ein Totalausfall.** Bei „Käse-Lauch-Suppe mit Hackfleisch" lieferte eine
  Spur mit drei Zutaten gar keine Begriffe — `PlanFehler`, und die ganze
  Stufe fällt auf `chefkoch.zutat_kette` zurück, die gemessen 10 von 14
  statt 12 von 12 trifft. Beide Läufe, reproduzierbar.

Für 4,4 gesparte Sekunden. **Also nicht gebaut** (WB-413). Die Probe bleibt
im Baum: sie ist die Antwort auf „warum eigentlich nicht", und sie lässt sich
gegen ein anderes Modell noch einmal fahren.

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
Tailnet bindet der Shop nur loopback, und `ZETTEL_HOST` erzwingt eine
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
`dish` ein und startete `python -m zettel.gerichte.lauf` als eigenen
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
Shop.** `python -m zettel.gerichte.lauf --gericht/--alle` trägt weiterhin
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
