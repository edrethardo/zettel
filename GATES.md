# Gates

Zwei Befehle. Beide laufen ohne Netz, ohne Modell und ohne Phoenix.

```bash
.venv/bin/python checks/smoke.py     # 51 Checks, exit 0 grün / 1 rot
.venv/bin/python -m pytest -q        # 709 Tests, rund 22 s
```

Wem das zu lang ist: `.venv/bin/python -m pytest -q -n auto` verteilt die Suite
auf alle Kerne und braucht rund 14 s (`pytest-xdist`, siehe
`requirements.txt`). Das ist eine Abkürzung und keine zweite Wahrheit — beide
Wege müssen grün sein, und die Zusicherungen sind dieselben.

`checks/smoke.py` ist das Gate: eine Zeile je Frage, ein Rückgabewert.
`pytest` ist die Suite darunter — feinkörniger, aber ohne die eine Zusicherung,
um die es beim Gate geht.

## Warum es zwei sind

Die Testsuite darf man abkürzen, ein Gate nicht. `smoke.py` klickt deshalb
**den ganzen Weg am HTTP-Rand** durch und sieht nach jedem Klick in die
Datenbank; es prüft nicht Funktionen, sondern das, was ein Mensch tut. Und es
setzt eine Bedingung durch, die eine `pytest`-Konvention nur zusichern kann.

## Die Netzfreiheit ist erzwungen, nicht angenommen

Bevor `checks/smoke.py` das erste Projektmodul importiert, ersetzt es im
eigenen Prozess `socket.socket.connect`, `.connect_ex`, `.bind`, `.sendto`,
`socket.create_connection` und `socket.getaddrinfo` durch etwas, das
`NetzVerboten` wirft — für `AF_INET` und `AF_INET6`. `AF_UNIX` bleibt frei:
`socket.socketpair()` ist auf Linux der Selbst-Wecker der asyncio-Schleife,
und ein prozessinterner Socket ist kein Netz.

Der erste Check **versucht dann eine Verbindung nach `127.0.0.1:6006`** — auf
der Entwicklungsmaschine läuft dort tatsächlich ein Phoenix — und besteht
genau dann, wenn sie scheitert:

```
[ok  ] create_connection nach 127.0.0.1:6006 wird abgewiesen -- create_connection(('127.0.0.1', 6006))
[ok  ] ein rohes AF_INET-Socket kommt nicht heraus -- connect() wirft NetzVerboten
[ok  ] auch die Namensauflösung ist gesperrt -- getaddrinfo wirft NetzVerboten
[ok  ] nichts in diesem Prozess kann lauschen -- bind() wirft NetzVerboten
```

Damit ist „ohne Netz" für alle folgenden Checks eine **gemessene Eigenschaft
dieses Prozesses** und keine Behauptung im Dateikopf. Ein Modellaufruf, der
sich einschliche, ein Exporter, der doch nach Phoenix greift, eine Bibliothek,
die etwas nachlädt — alles davon fällt laut um, statt still grün zu bleiben.

Der `bind`-Check ist zugleich die Zusicherung, dass dieser Lauf **keinen
lauschenden Socket öffnet**, weder auf `0.0.0.0` noch sonst wo.

## Was das Gate abdeckt

**Netzfreiheit** (5 Checks) — die vier oben, plus: Tracing ist aus, also
greift auch der OTLP-Exporter nicht.

**Die App startet und der Katalog antwortet** (6) — `GET /` weist auf den
Katalog, `/katalog` rendert Produkte, `/produkte?q=…` liefert die
HTMX-Trefferliste, `search()` sortiert nach Wortstufe und darin nach positivem
Rang (WB-339), ein Begriff ohne Treffer liefert eine leere Liste statt eines Fehlers,
und der Kategoriebaum zählt den ganzen Teilbaum (nicht nur, was direkt an
einem Knoten hängt).

**Der ganze Weg** (6) — Katalog → einlegen → Warenkorb → abschicken →
Pick-Ansicht → abhaken → Haken zurücknehmen. Jeder Schritt wird über HTTP
geklickt und danach in der Datenbank nachgesehen: dass eine Seite einen Knopf
zeigt, sagt nichts darüber, ob der Knopf etwas tut. Der letzte Schritt prüft
`erledigt -> offen` — den Übergang, den die Spec nicht nennt.

**Der Span-Baum** (8) — gegen einen `InMemorySpanExporter`, mit einem echten
`openai`-SDK auf `httpx.MockTransport`, damit der `OpenAIInstrumentor`
denselben Weg geht wie im Betrieb:

* der Baum hat genau die Form `plan.extract`, `catalog.search` ×2,
  `plan.choose`, `chat.turn`
* die Span-Kinds stimmen — `catalog.search` ist `RETRIEVER`, nicht `TOOL`
* alles hängt unter `chat.turn`: **ein** Trace, nicht fünf nebeneinander
* die LLM-Spans tragen getrennte Token-Zahlen, vom Instrumentor
* „Butter" legt fünf Dokumente mit ID, lesbarem Inhalt und positivem Score
  vor, und `rank_top` ist der beste davon
* „Zahnpasta" legt null vor — `candidates = 0`, und `rank_top` fehlt, statt
  eine erfundene 0 zu tragen
* `chat.turn` fasst zusammen: `rejected = 0`, `weakest_term = "Zahnpasta"`
* die Span-ID des Zugs steht in `chat_message.span_id` — der Haken, an dem die
  Annotationen hängen

Der Katalog dieses Laufs ist der Butter-Fall aus `OBSERVABILITY.md` in klein:
die fünf ButterBoyz stehen wörtlich so im echten Katalog.

**Die Gerichtequelle** (5, WB-338) — gegen die aufgezeichnete
Chefkoch-Antwort unter `tests/fixtures/`, also **ohne einen einzigen Socket**:
gewählt wird nach gewichteter Note (die rohe Höchstnote 5,00 aus zwei Stimmen
und die Platzhalter-Stimmen der Plus-Rezepte verlieren beide), der Abruf
schreibt Rezept, Zutaten, Zubereitung und `siteUrl` weg, der zweite Zugriff
kommt ohne Netz aus dem Speicher, ein Chat-Zug nimmt die Zutaten aus dem
Rezept (`picknick.path = chefkoch`), und ein Gericht, das noch niemand geholt
hat, bricht den Zug nicht — er läuft mit den geratenen Begriffen zu Ende und
stösst einen EIGENEN PROZESS an, statt zu warten.

**Oberbegriffe auffächern** (6, WB-368) — gegen ein Aufschnittregal mit vier
Sorten und einer fünften, die ausgemustert ist: „Aufschnitt" fächert in die
Sorten des Katalogs auf, mit echter Stückzahl und **ohne einen einzigen
Modellaufruf** (der Zugang meldet jeden Aufruf als Fehler); die ausgemusterte
Sorte wird nicht angeboten; „Tomatenmark" fächert NICHT auf und läuft mit
seinen zwei Stufen wie vorher; eine Kategorie, die dem Modell nie vorgelegt
wurde, wird verworfen und benannt; eine gewählte Sorte landet im normalen
Kandidatenablauf mit Vorschlag, Alternativen und Ja/Nein; und von mehreren
angekreuzten Sorten fällt die nicht angebotene weg.

**Die Bindung** (12) — `0.0.0.0`, `::`, die LAN-Adresse, ein Hostname und eine
leere Adresse werden abgelehnt; loopback und Tailnet erlaubt; die Vorgabe
enthält nur Erlaubtes; `PICKNICK_HOST=127.0.0.1,0.0.0.0` kommt nicht durch;
und eine Liste mit `0.0.0.0` öffnet **gar kein** Socket — auch nicht auf der
erlaubten Adresse davor.

## Was das Gate ausdrücklich NICHT abdeckt

Diese Liste ist der Grund, warum das Gate ehrlich ist. Grün heisst hier nicht
„es funktioniert", sondern „das Folgende ist geprüft und der Rest nicht".

* **Kein Browser, kein Handy.** Die Oberfläche ist über HTTP geprüft, nicht
  gerendert. Ob bei 390 px etwas quer scrollt, ob ein Tap-Ziel zu klein ist,
  ob HTMX im Safari des Handys tut, was es soll — nichts davon weiss dieses
  Gate. **Die Oberfläche wurde nie auf einem echten Handy angesehen.**
* **Kein echtes Phoenix.** Der Span-Vertrag wird geprüft, wie er *gebaut*
  wird, nicht wie er *ankommt*. Serialisierung, Attributnamen im Server,
  Projektzuordnung, ob Phoenix die `retrieval.documents` wirklich als
  Dokumentenliste rendert — dafür gibt es `scripts/trace_probe.py`, und das
  ist kein Gate, sondern Handarbeit gegen `localhost:6006`.
* **Kein echtes Chefkoch.** Der Weg von der Antwort bis in die
  Vorschlagsliste ist geprüft, die ANTWORT nicht: dass `api.chefkoch.de` noch
  antwortet, noch dieses Format liefert und uns noch lässt, weiss dieses Gate
  nicht. Dafür gibt es `scripts/record_chefkoch.py` (erneuert die Fixture —
  bricht danach ein Test, hat sich das Format geändert) und
  `scripts/gericht_probe.py` (die Handprobe gegen die echte Quelle und die
  echte Box). Ebenso wenig geprüft ist die rechtliche Seite: robots.txt
  erlaubt die beiden Endpunkte, die Nutzungsbedingungen sind nicht gelesen
  (siehe README).
* **Keine echte Frist.** Dass `Quelle.holen()` im Zug abruft, mit der kurzen
  Frist, und bei einem Ausfall auf das Modell zurückfällt, ist geprüft (WB-367);
  dass `httpx` nach zwei Sekunden gegen ein wirklich langsames
  api.chefkoch.de abbricht, nicht — im Gate ist der Abruf ein Doppelgänger,
  der sofort antwortet oder sofort wirft. Die echte Frist misst
  `scripts/gericht_probe.py`.
* **Keine echten Annotationen.** Was `obs.labels.annotationen()` *berechnet*,
  ist geprüft; dass Phoenix sie annimmt, dem richtigen Span zuordnet und unter
  `annotator_kind = HUMAN` wiederfindet, prüft nur
  `scripts/label_probe.py` — von Hand, gegen ein laufendes Phoenix.
* **Keine vLLM-Box.** Das Modell ist ein `httpx.MockTransport`. Ob
  `Qwen3.8-27B-Instruct` auf einen echten Satz brauchbare Begriffe liefert,
  ob `guided_json` von *diesem* vLLM angenommen wird, ob das Aufwecken der Box
  klappt — nichts davon. Dafür gibt es `scripts/chat_probe.py` und
  `scripts/modell_probe.py`.
* **Kein Knuspr.** Der Crawler wird gegen eine aufgezeichnete Antwort
  geprüft (`tests/fixtures/knuspr_milch.json`). Ob die echte Seite noch dieses
  Format liefert, sieht man erst am nächsten nächtlichen Lauf — und daran, ob
  `/status` einen verworfenen Lauf meldet.
* **Die Evals.** `evals/` ist Handarbeit und kein Gate. Die rein rechnenden
  Teile (Bewertung, Zuordnung, Urteilsparser) stehen in `tests/test_evals.py`;
  ein Experimentlauf selbst braucht Phoenix und die Box. Siehe `EVALS.md`.
* **Kein uvicorn, kein systemd.** Geprüft wird, dass `pruefe_host` und
  `sockets_bauen` `0.0.0.0` **ablehnen** — nicht, dass ein laufender
  Serverprozess auf der richtigen Adresse lauscht. Die systemd-Units unter
  `deploy/` werden von keinem Check angefasst. **`Persistent=true` ist per Text
  belegt, nicht per Verhalten:** dass systemd einen verpassten nächtlichen Lauf
  nach dem Aufklappen des Laptops nachholt, steht in der Unit und in der
  Dokumentation — beobachtet hat es niemand.
* **Der Vollcrawl.** Er ist inzwischen gelaufen (2026-08-28): **36 min 57 s,
  10.361 Produkte**, danach 4 von 170 Begriffen ohne Treffer. Die vorher im
  README geschätzten 15–25 Minuten lagen um rund die Hälfte zu niedrig — das
  Gate misst das nicht und wird es nie messen, es geht per Konstruktion nicht
  ins Netz. Wer die Dauer wissen will, startet
  `python -m picknick.scrapers.nachtlauf` von Hand.
* **Die Sicherungen.** Dass `VACUUM INTO` eine brauchbare Datei schreibt,
  prüfen die Tests. Dass ein Zurückspielen im Ernstfall den Shop rettet, hat
  niemand geübt. Und die Sicherungen liegen auf **derselben Platte** wie die
  Datenbank: sie helfen gegen einen kaputten Crawl-Lauf und gegen einen
  Fehlgriff von Hand, nicht gegen einen Plattenschaden.
* **Die Kosten in Phoenix.** Die Token-Zahlen sind geprüft, die Kostenzahl
  nicht. `Qwen3.8-27B-Instruct` dürfte in Phoenix' Preistabelle fehlen; es ist
  nie nachgesehen worden.
* **Die Kassenbons (WB-344 und WB-358).** `tests/test_web_bons.py`,
  `test_bons_lesen.py`, `test_bons_kaeufe.py`, `test_bons_lauf.py` und
  `test_web_bonlesen.py` prüfen Upload, Zerlegung, Ablage und Zuordnung —
  **das Gate selbst fasst nichts davon an.** Was dort ungemessen bleibt:
  * **Der Lidl-Weg ist gebaut, aber nie gelaufen.** `tesseract` ist auf dieser
    Maschine nicht installiert. Der Bild-Weg sagt das sauber und macht die
    Seite nicht kaputt (geprüft), aber ob OCR auf dem Screenshot wirklich
    brauchbaren Text liefert, weiss niemand. Der Screenshot ist gerenderter
    Text und kein Foto von Papier, die Erkennung SOLLTE also gut sein — das
    ist eine Erwartung, keine Messung.
  * **Nur ein einziger echter Bon.** Gemessen wurde an genau einem Rewe-eBon
    (18 Posten). Eine zweite Filiale, ein Bon mit Rabattzeile, ein Bon über
    zwei Seiten — nichts davon ist je durch dieses Modul gelaufen.
  * **Die Zuordnung braucht die Box.** Die Tests fahren gegen einen Fake-LLM.
    Wie gut `Qwen3.8-27B-Instruct` die Abkürzungen wirklich auflöst, steht in
    der Handprobe im Docstring von `picknick/bons/zuordnung.py`: 18 von 18
    Zeilen fanden ein Produkt, **14 davon das richtige**.
  * **Der Hintergrundlauf überlebt keinen Neustart.** `bons.lauf` lebt im
    Prozess. Wird der Web-Prozess mitten in einem Lauf neu gestartet, steht
    der Bon wieder als „noch nicht ausgelesen" da.
* **Die Korrektur an einem Vorschlag (WB-359).** `tests/test_assistant.py`,
  `test_web_chat.py` und `test_labels.py` decken sie ab — aufgehobene
  Kandidaten, „Nein" klappt sie auf, ein Tipp legt eine statt des Vorschlags
  ein, der Freitext-Ausgang, die `correction`-Annotation. **Das Gate selbst
  fasst nichts davon an**: es fährt einen Chat-Zug, entscheidet aber keine
  Zeile. Ungemessen bleibt dabei:
  * **Wie viele Alternativen der echte Katalog hergibt, hängt am Katalog.**
    Der Rauchtest-Katalog hat fünf Butter; die Zahlen aus `DESIGN.md` (Butter
    15, Schmand 26, Sellerie 2) stammen aus einer Handprobe am 2026-08-28
    gegen die 10.361 echten Produkte und sind nirgends festgeschrieben. Ein
    Crawl kann sie verschieben.
  * **Die aufgeklappte Liste ist nie auf einem Handy gesehen worden.** Geprüft
    sind die Regeln (44 px Tap-Ziele, `max-height: 60vh` mit eigenem Scroll)
    und das gerenderte HTML — nicht der Daumen auf 360 px.
* **WB-335 ist offen.** Warenkorb und Pick-Ansicht zeigen nicht, dass ein
  Produkt aus dem Katalog verschwunden ist. Nur die Rezeptansicht tut es. Das
  Gate deckt diesen Fall nicht ab, weil es ihn im Produkt nicht gibt.

## Wenn das Gate rot wird

Jede rote Zeile nennt Erwartung und Befund:

```
[FAIL] der Kategoriebaum steht -- AssertionError: Ebene 1: [...], erwartet [...]
```

Am Ende steht die Liste der roten Checks und der Rückgabewert ist `1`. Es gibt
kein `2` und kein „übersprungen": dieses Gate kann immer laufen, weil es
nichts braucht ausser Python und dem Repo. Genau das ist sein Zweck — alles,
was eine laufende Maschine anderswo braucht, ist absichtlich draussen und steht
in der Liste oben.
