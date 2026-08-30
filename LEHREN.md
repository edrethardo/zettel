# Lehren

Was dieses Projekt gekostet hat und was davon woanders gilt. Jede Lehre steht
mit der Messung da, die sie bezahlt hat — ohne Zahl wäre sie eine Meinung, und
Meinungen hat man auch ohne Projekt.

Die Reihenfolge ist die des Nutzens, nicht die der Zeit.

---

## 1. Ein grüner Test ist kein laufender Laden

Der Rezeptwechsel im Chat war mit **1.229 grünen Tests** kaputt. Gefunden wurde
es nicht in der Suite, sondern in der Datenbank des laufenden Shops:

    dish 8 „Lasagne Bolognese"  ->  recipe 13 „… mit Béchamelsoße"
    chat_rezept zu Zug 38       ->  recipe 11 „Lasagne"
    chat_message                    kein einziger neuer Zug

Im Chat stand die Karte „Lasagne" als **vorgeschlagen** und darunter das
Béchamel-Rezept als etwas, das man noch *wählen kann*. Man hatte es zwanzig
Minuten vorher gewählt.

**Was daraus folgt:** nach jeder Zusicherung, die zwei Datenquellen betrifft,
einmal in die echte Datenbank sehen. Die Tests deckten beide Seiten ab — nur
nie die Frage, ob sie dasselbe sagen.

## 2. Erst messen, warum es langsam ist. Dann optimieren

Auftrag: „Speichere Suchergebnisse, dass sie instant kommen." Die Suche
brauchte 1,85 s. Ein Zwischenspeicher hätte den zweiten Aufruf schnell gemacht
und den ersten für immer bei 1,85 s gelassen.

Gemessen:

    /katalog?q=bio joghurt natur    1,85 s
      search.count()                1.871 ms  -> die Zahl 43
      search.search()                   1,3 ms -> dieselben 43 Zeilen
      die reine FTS-Abfrage               0 ms

Der Unterschied stand im Abfrageplan: `count` lief über alle zehntausend
aktiven Produkte und stellte je Zeile eine FTS-Anfrage. Ein Wort (`CROSS JOIN`)
zwingt die Reihenfolge: **1.871 ms -> 0,3 ms.**

**Was daraus folgt:** ein Zwischenspeicher über etwas Langsamem ist eine
Wette darauf, dass niemand mehr hinsieht. Erst das Profil, dann der Speicher —
und oft entfällt er dann.

## 3. Ein Zwischenspeicher gehört dorthin, wo die Arbeit entsteht

Ein Rezeptwechsel kostete 24 bis 27 s, weil er denselben Satz noch einmal durch
beide Modellstufen schickte — auch beim Zurückwechseln zu einem Rezept, das
eine Minute vorher schon gerechnet worden war.

Die Arbeit hing aber an nichts, was ein Wechsel ändert:

    plan.zutatenbegriffe(zutaten, gericht, servings)   nur das REZEPT
    plan.choose("", aufgaben)                          nur BEGRIFFE + KATALOG

Also gehört die Zuordnung dem **Rezept** und nicht dem Zug. Gemessen:
Wechsel auf ein unbekanntes Rezept 30,2 s, auf ein gemerktes **0,29 s**,
dieselben 13 Vorschlagszeilen.

**Was daraus folgt:** vor dem Speichern die Frage stellen, wovon das Ergebnis
wirklich abhängt. Die Antwort ist selten die Ebene, auf der man gerade steht.

## 4. Ein `async def` mit einem blockierenden Aufruf hält den ganzen Dienst an

Die Chat-Eingänge waren `async def` — sie mussten es sein, weil sie den Rumpf
mit `await request.body()` lasen — und riefen darin blockierend das Modell auf.

    GET /chat, während ein Modellaufruf lief    14,17 s   (sonst 4 ms)

Für beide Nutzerinnen, auf jeder Seite. **Der Verursacher merkt es nie**: seine
eigene Anfrage antwortet ja. Drei gleichzeitige Vorwärmläufe liefen in Reihe
(31/57/57 s) statt nebeneinander — daran ist es aufgefallen.

**Was daraus folgt:** wer in einer Koroutine wartet, wartet für alle. Den
Rumpf in einer Abhängigkeit lesen und den Eingang als gewöhnliches `def`
schreiben; das Rahmenwerk legt ihn dann in den Threadpool.

## 5. Dreissig `commit` sind eine Plattenumdrehung, keine Arbeit

cProfile über einen Chat-Zug:

    chat.turn                 264 ms
      _schreiben              220 ms
        30 × sqlite3.commit   216 ms   (7,2 ms je Aufruf)

Die Datenbank lief in WAL, aber mit `synchronous = FULL`. Mit `NORMAL` — der
üblichen Stellung dazu — sind es **16 ms**; die Testsuite fiel nebenbei von
73 s auf 43 s.

**Was daraus folgt:** wenn ein Profil eine runde Zahl je Aufruf zeigt (7,2 ms,
und zwar immer), ist es kein Rechenaufwand, sondern Warten auf Hardware.

## 6. Ein Timeout ist eine Behauptung über die Welt

Der Weckruf prüfte die Modellbox mit **3 s** Timeout. Gemessen: Median **2 ms**,
langsamste von fünfzehn Proben 163 ms. Die drei Sekunden wurden voll bezahlt,
sooft die Box nicht bediente — und standen dann in jedem Seitenaufruf.

**Was daraus folgt:** jeden Timeout gegen eine Messung halten. Das Tausendfache
des Üblichen ist kein Sicherheitsabstand, sondern eine ungeprüfte Annahme.

---

## 7. Ohne Rauschband sagt eine Messung nichts

Die Frage: verträgt Stufe 1 dieselbe Zerlegung in gleichzeitige Anfragen wie
Stufe 3? Die Probe fuhr 15 Rezepte in drei Varianten, **und `ganz` mindestens
zweimal**.

    Variante   Sekunden  Ketten  Abdeckung  Dubletten  Deckung
    ganz           9,9s    11,0       69%          0     100%
    2 Spuren       6,6s    11,0       69%          4      87%
    4 Spuren       5,5s    11,0       68%          6      87%

**Das Rauschband war null**: dreissig Läufe von `ganz`, keine einzige
Abweichung, identische Zeiten auf die Zehntelsekunde. Damit war jede Abweichung
der zerlegten Läufe dem Zerlegen zuzuschreiben — und nicht dem Zufall. Ohne das
Band hätte ich 13 % Abweichung für Modellstreuung halten können.

**Was daraus folgt:** die Kontrollvariante gegen sich selbst fahren, bevor man
Varianten vergleicht. Es kostet einen Lauf und entscheidet, ob die anderen
etwas bedeuten.

## 8. Eine Messung darf ein Vorhaben auch beerdigen

Dieselbe Probe hat das Zerlegen von Stufe 1 **verworfen**, obwohl es 4,4 s
gespart hätte: 14 von 15 Rezepten änderten sich, es entstanden Dubletten (0 ->
4 -> 6), und bei einem Rezept lieferte eine Spur mit drei Zutaten gar keine
Begriffe — `PlanFehler`, beide Läufe, reproduzierbar.

Die Abweichung allein wäre kein Urteil gewesen („Möhren" statt „Karotten" kann
gleich gut sein). Die Dubletten sind eines: dieselbe Zutat zweimal auf dem
Zettel sieht man.

**Was daraus folgt:** eine Probe, die nur bestätigen darf, ist keine. Das
Skript bleibt im Baum — es ist die Antwort auf „warum eigentlich nicht" und
lässt sich gegen ein anderes Modell noch einmal fahren.

## 9. Ein Zwischenspeicher, den man nicht verlassen kann, ist ein Käfig

Das Gedächtnis für Begriffe macht Züge sofort — und schriebe eine einmal
danebengegriffene Wahl für immer fest. Deshalb steht an jedem Zug **„Neu
suchen"**: derselbe Satz noch einmal, ohne Gedächtnis, und das Ergebnis
überschreibt es.

Dabei ist die Trennung entscheidend: **gelesen wird nicht, geschrieben schon.**
Wäre auch das Schreiben abgeschaltet, hiesse „neu suchen" nur „diesmal
anders", und beim nächsten Satz stünde die verworfene Wahl wieder da.

**Was daraus folgt:** zu jedem Gedächtnis gehört ein Weg, es zu übergehen —
und der muss das Gedächtnis erneuern, nicht bloss umgehen.

---

## 10. „Port offen" ist nicht „lädt"

Die Modellbox meldete stundenlang „port is open but not serving yet — vLLM is
loading". Ich habe das drei Mal wiederholt, statt es zu prüfen. Der Nutzer, der
am Rechner sass, hatte das bessere Argument: **er hörte den Lüfter.**

Der wahre Zustand: die Engine war tot, und der socket-aktivierte Proxy nahm
weiter Verbindungen an. Ein Dienst, der seit Stunden „lädt", lädt nicht.

**Was daraus folgt:** die Auskunft eines Werkzeugs ist eine Messung, ihre
Beschriftung eine Vermutung. „Port nimmt an, aber nach 25 s kein Byte" ist die
Messung; „lädt" war meine Erfindung.

## 11. Was die Kamera zeigt, muss die Datenbank belegen — und umgekehrt

Die Aufnahme meldete dreimal „Korb geöffnet (19 Posten)", während im Fenster
die Rezeptliste stand. Die Meldung las die Datenbank; geöffnet hatte sie
nichts. Der Klick lag auf einer festen Koordinate, und die Leiste hatte sich
verschoben, als das Korb-Abzeichen zweistellig wurde.

Seither prüft der Fenstertitel — die einzige ehrliche Auskunft darüber, wo man
ist. Umgekehrt gilt dasselbe: jeder Klick der Aufnahme prüft seine **Wirkung**
in der Datenbank, und der Lauf endet mit „kein Schritt hat gewarnt" oder gar
nicht.

**Was daraus folgt:** eine Erfolgsmeldung, die etwas anderes misst als die
Handlung, ist schlimmer als keine.

## 12. Eine Generalprobe, die einen anderen Weg nimmt, prüft einen anderen Weg

Der Prüfstand fährt die Choreografie über ein gespeichertes Rezept — 66 ms,
kein Modell. Er lief fehlerfrei. Vor der Kamera fand dieselbe Automatik die
Sammelknöpfe nicht.

Der Grund: auf dem Modellweg folgt unter der Vorschlagsliste noch der
**Rezeptentwurf**, acht Zeilen, rund 900 px. Auf dem Gespeichert-Pfad gibt es
ihn nicht. Also lag im Trockenlauf das Seitenende genau auf den Knöpfen und im
Take neunhundert Pixel darunter.

**Was daraus folgt:** jede Abkürzung in einer Probe ist ein Unterschied, den
die Probe nicht prüft. Sie aufschreiben — und die Schritte, die davon abhängen,
gegen die Wirkung absichern statt gegen die Geometrie.

## 13. Zahlen, die zweimal irgendwo stehen, stehen einmal falsch

Die Aufnahme suchte ihre Ziele über Farben, die im Skript standen. Zwei
Umbauten später waren alle falsch:

    Akzent   grün (62,125,51) -> blau (36,86,184) -> hellblau (130,170,245)
    Linie    beige (229,220,201) -> grau (217,223,231) -> dunkel (49,61,74)

Beide Male sah es aus wie ein kaputter Bilderkenner und war eine veraltete
Zahl. Sie kommen jetzt aus dem Stylesheet.

**Was daraus folgt:** eine Konstante, die anderswo schon steht, ist keine
Konstante, sondern eine Kopie mit Verfallsdatum.

---

## 14. Der Nutzer sieht die Wirkung, nicht die Absicht

Drei Sätze, die alle gut gemeint waren und alle falsch:

* **„Gleich noch einmal ‚Fragen' tippen."** — als Antwort auf eine Box, die aus
  dem Schlaf gemessene 96 s braucht. Der Satz war schon getippt und schon
  abgeschickt. Jetzt fragt der Shop von selbst noch einmal.
* **„‚X' ist jetzt das Rezept zu ‚Y'."** — nach einem Wechsel, dessen Zug nie
  lief. Der Satz stimmte sogar, und genau das war der Schaden.
* **Das Eingabefeld am Seitenende.** Gemessen: y = 5.280 auf einer 5.364 px
  hohen Seite, **6,1 Bildschirmhöhen** scrollen — bei zwei Zügen im Verlauf.

**Was daraus folgt:** die Oberfläche einmal mit der Uhr und dem Lineal ansehen,
nicht mit dem Kopf. Sechs Bildschirme misst man in drei Minuten und diskutiert
sie sonst monatelang nicht.

## 15. Vier Mal war eine Modellaufgabe eine Datenbankabfrage

Sorten zu einem Oberbegriff (`GROUP BY` über den Kategoriebaum), die Zuordnung
von Mengen zu Zutaten (Wortvergleich), die Zugehörigkeit einer Zeile zum
Gericht (Struktur), die Alternativrezepte (standen längst in `dish_treffer`).
Jedes Mal war der erste Entwurf ein Prompt.

Beim Auffächern liess sich der Unterschied sogar messen: der Katalog liefert
sechs echte Sorten mit echten Stückzahlen, das Modell erfand
„Schweinebrust" (0 Treffer), doppelte Einträge und „Birn".

**Was daraus folgt:** bevor eine Aufgabe an das Modell geht, die Frage stellen,
ob die eigene Datenbank sie schon beantwortet. Sie ist schneller, billiger und
erfindet nichts.
