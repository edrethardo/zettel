# Vorführung

Zehn Minuten: ein Satz wird zu einer Einkaufsliste, der Trace beantwortet die
Schuldfrage, und die Entscheidung der Nutzerin wird zum Eval-Label.

**Eine Prüfung pro Klick.** Jeder Schritt nennt, was du tust, und die eine
Sache, die danach dastehen muss. Stimmt sie nicht, ist das die Fehlerstelle —
nicht drei Schritte später. Alle Zahlen unten sind am 2026-08-28 gegen den
echten Katalog, die echte vLLM-Box und das laufende Phoenix gemessen, nicht
erinnert.

## 0. Vorher

```bash
.venv/bin/python checks/smoke.py
```

**Prüfung:** letzte Zeile `alle 37 Checks grün -- ohne Netz, ohne Modell, ohne
Phoenix`, Rückgabewert `0`. Auf einem roten Gate fängt keine Vorführung an.

Dann die drei Dinge, die die Vorführung selbst braucht — anders als das Gate:

```bash
wake-vllm                                   # die Box; ~90 s aus dem Schlaf
curl -s localhost:6006/v1/projects >/dev/null && echo "Phoenix da"
.venv/bin/python -c "
from picknick import db
print(db.connect('data/picknick.db').execute(
  'select count(*) n from product where active=1').fetchone()['n'], 'Produkte')"
```

**Prüfung:** `Phoenix da`, und die Produktzahl ist vierstellig (2.498 am
2026-08-28). Ein leerer Katalog macht jeden folgenden Schritt sinnlos.

## 1. Starten

```bash
.venv/bin/python -m picknick.web.app
```

**Prüfung:** drei Zeilen von uvicorn, endend mit `Application startup
complete.`, und der Prozess bleibt im Vordergrund. Der Shop lauscht auf
`http://<deine Tailnet-Adresse>:8730` (beim Start selbst erkannt) **und**
`http://127.0.0.1:8730` — nicht auf `0.0.0.0`.

Das ist der erste Punkt, den man laut sagen kann:

```bash
PICKNICK_HOST=0.0.0.0 .venv/bin/python -m picknick.web.app
```

**Prüfung:** der Prozess startet **nicht**. Er bricht mit Rückgabewert `1` ab,
letzte Zeile des Tracebacks: `UnsichereBindung: Bindung auf 0.0.0.0
verweigert: das lauscht auf JEDER Schnittstelle und stellt den Shop im
nächsten fremden WLAN offen ins Netz (Spec 10).` Kein Socket ist dabei
entstanden. Es gibt kein Passwort; das Tailnet ist der ganze Schutz,
und deshalb ist die Adresse eine Weisse Liste und keine Empfehlung.

## 2. Der Katalog

Im Browser `http://127.0.0.1:8730/` öffnen.

**Prüfung:** die Weiterleitung landet auf `/katalog`, oben eine Leiste
`Katalog · Chat · Korb 0 · Rezepte · Bestellungen · Pick-Liste · Bons ·
Status · wer bin ich?`, links der Kategoriebaum (`Aufschnitt`, `Backwaren & Feingebäck`, …
jeweils mit Produktzahl), rechts Kacheln — **genau 60**, das ist die harte
Kappung, und darunter steht der Satz „Preise stammen von knuspr.de und sind
Richtwerte — eingekauft wird bei Rewe und Lidl."

**Ins Suchfeld `butter` tippen.**

**Prüfung:** die Liste tauscht sich ohne Seitenwechsel aus (HTMX holt nur
`/produkte`), und die ersten fünf Kacheln sind

```
ButterBoyz handgemachte BIO Butter Chili & Röstzwiebel   4,79 €
ButterBoyz handgemachte BIO Butter Feige & Anis          4,69 €
ButterBoyz handgemachte BIO Kräuterbutter                4,79 €
ButterBoyz handgemachte BIO Salzbutter                   4,69 €
ButterBoyz handgemachte BIO Steinpilzbutter              4,99 €
```

**Das ist der Aufbau der Pointe.** Die Suche hält handgemachte Spezialbutter
für die beste Antwort auf „butter". Merken; in Schritt 4 kommt es wieder.

Die URL ändert sich beim Tippen **nicht** (kein `hx-push-url`) — ein Reload
landet wieder im unbeschränkten Katalog. Bekannt, siehe `DESIGN.md`.

## 3. Der Chat

Auf **Chat** klicken, dann unten ins Chat-Feld:

**Prüfung vorab:** der Chat ist seit WB-382 eine eigene Seite (`/chat`). Über
dem Verlauf steht die Korbbrücke — solange nichts drin liegt: „Noch nichts im
Korb. Was du hier mit „Ja“ bestätigst, landet dort."

> `dazu brauche ich noch Zahnpasta und Butter`

**Prüfung:** nach rund 2,5 Sekunden steht über der Liste

```
2 Begriffe aus dem Satz, 1 davon im Katalog gefunden.
Ohne Katalogtreffer und deshalb als Freitext: „Zahnpasta“.
```

und darunter **zwei Zeilen mit je einem Ja/Nein-Knopf**:

| Zeile | Herkunft |
|---|---|
| `1 × Zahnpasta` — *Freitext — im Katalog nicht gefunden* | `Zahnpasta` |
| `1 × ButterBoyz handgemachte BIO Salzbutter` — 100 g · 4,69 € | `Butter · Rang 4.0` |

Drei Dinge stehen hier auf dem Schirm und sind je einen Satz wert:

* **Nichts ist im Korb gelandet.** Die Zahl neben *Korb* oben ist unverändert.
  Ein Vorschlag ist ein Vorschlag.
* **Kein Begriff ist verschwunden.** „Zahnpasta" hat null Treffer und steht
  trotzdem da — als Freitext, den sie im Laden selbst sucht. Eine still
  fallengelassene Zutat merkt man erst vor dem Regal.
* **An jeder Zeile steht, woraus sie entstand**: der Suchbegriff und der Rang
  der Suche. Das ist die Vorbereitung auf Schritt 4.

### 3a. „Nein" — und die Alternativen sind schon da (WB-359)

Auf **Nein** an der Butterzeile tippen. Die Zeile verschwindet nicht, sie
klappt auf:

```
14 Alternativen aus derselben Suche
  Weihenstephan Butter          250 g · 2,89 €   [Das]
  Kerrygold irische Butter      250 g · 3,59 €   [Das]
  …
  [Butter                    ]  [Nichts davon]
```

**Prüfung:** dieselben Kandidaten, die Stufe 3 vorlagen — **es wird nicht neu
gesucht**. Ein Tipp auf *Das* legt die Alternative in den Korb, und die
Butterzeile bleibt als `removed` stehen, mit „statt …" an der neuen Zeile.
Damit ist die Korrektur als Korrektur erkennbar und nicht als zwei lose
Entscheidungen — in Schritt 5 wird daraus eine `correction`-Annotation, die
sagt, **was richtig gewesen wäre**.

Zum Gegenstück: an einer Zutat, die der Katalog nicht hat („Sellerie"), steht
statt der Liste „Mehr hat der Katalog dazu nicht hergegeben" und daneben das
Freitextfeld. Das ist dort der richtige Ausgang und nicht der Notausgang.

### 3b. „rückgängig" — ein Fehltipp ist nicht endgültig (WB-361)

An der Zahnpastazeile auf **Ja** tippen.

**Prüfung (WB-382):** die Seite wechselt NICHT. An der Zeile steht „Liegt jetzt
im Korb — 1 Sache drin.", die Korbbrücke über dem Verlauf wird zu „1 Sache im
Korb — ansehen", und die Zahl oben im Kopf springt auf 1. Drei Stellen für
dieselbe Auskunft: die Zeile, weil dort der Daumen steht; die Brücke, weil sie
beim Scrollen oben klebt; der Kopf, weil er auf jeder Seite steht.

Dann auf das kleine **rückgängig** neben „im Korb". Die Zeile steht wieder mit
Ja/Nein da, und darunter steht:

```
Zurückgenommen — die Zeile bleibt im Korb; dort steht ein Löschknopf.
```

**Prüfung:** die Korbzahl oben bleibt bei 1. Das ist Absicht und keine Panne —
`orders.einlegen()` fasst gleiche Zeilen zusammen, die Korbzeile kann also
längst eine sein, die sie selbst aufgestockt hat; sie hier herauszunehmen
hiesse, fremde Mengen zu löschen. Verschwiegen wird es trotzdem nicht.

Jetzt **noch einmal Ja**. Die Korbzahl bleibt bei **1**.

Das ist der interessante Teil: der Schutz gegen den doppelten Tipp hing bis
WB-361 am Vergleich der letzten Entscheidung („steht schon auf `kept`"), und
der Umweg über „rückgängig" hätte ihn ausgehebelt — zweimal einlegen, Menge 2.
Er hängt jetzt daran, ob DIESE Zeile schon einmal im Korb war
(`chat_suggestion.eingelegt_at`).

**Warum das mehr ist als Bequemlichkeit:** auf dem Telefon sitzen „Ja" und
„Nein" nebeneinander. Ein Fehltipp würde in Phoenix zu einem `removed`-Label
(Schritt 5) und die Trefferquote drücken, obwohl der Agent nichts falsch
gemacht hat. Weil die Annotationen erst beim ABSCHICKEN entstehen, kostet der
Rückweg vorher nichts — der Fehltipp hinterlässt kein Label, nur ein
`withdrawn = 1` in den Metadaten. **Der Rückweg ist Datenqualität.**

### 3b′. „Doch nicht alles" — auch die Sammelentscheidung geht zurück (WB-397)

Die Geste, die vorführt, worum es geht. Sie braucht eine längere Liste — der
Bolognese-Zug aus 3c oder 4 tut es. Der Reihe nach:

1. Zwei Zeilen **einzeln** auf *Ja* tippen. Sagen wir Butter und Spinat.
2. Auf **Alles übernehmen**. Der Rest der Liste geht in einem Zug auf `kept`,
   und die beiden Sammelknöpfe verschwinden — es ist nichts mehr offen.
3. An ihrer Stelle steht jetzt **Doch nicht alles (9)** und darunter:

```
Nimmt nur diesen einen Sammeltipp zurück — einzeln Entschiedenes bleibt
stehen, und der Korb auch.
```

4. Antippen.

**Prüfung — und das ist der ganze Punkt:** die neun Zeilen des Sammeltipps
stehen wieder mit Ja/Nein da, **Butter und Spinat bleiben auf „im Korb"**. Das
ist die Zusicherung des Sammelknopfs, rückwärts gelesen: er rührt nur die
offenen Zeilen an, damit ein Tipp nicht die Labels umkippt, die sie einzeln
gesetzt hat — und ein Rückweg, der die beiden mitnähme, wäre derselbe Fehler
in die andere Richtung. Der Shop weiss das, weil jede gesammelt entschiedene
Zeile die Nummer ihres Vorgangs trägt (`chat_suggestion.sammel_nr`); ein
einzelner Tipp löscht sie wieder.

**Prüfung — die Korbzahl oben bleibt.** Wie überall seit WB-361: was im Korb
liegt, gehört dem Korb. Und wer jetzt noch einmal auf **Alles übernehmen**
tippt, sieht die Korbzahl NICHT steigen — der Schutz aus WB-361 (`eingelegt_at`)
trägt auch hier, weil der Rückweg Zeile für Zeile durch dieselbe Tür geht.

### 3c. „alles für Pho" — die Quelle gegen das Gedächtnis (WB-338, WB-367)

Der Fall, für den das Ticket geschrieben wurde. Vorher einmal sicherstellen,
dass das Gericht wirklich neu ist (sonst zeigt der Zug nur den
Zwischenspeicher):

```bash
sqlite3 data/picknick.db "DELETE FROM dish WHERE name = 'pho'"
```

Ins Chat-Feld:

> `alles für Pho`

**Ein Zug, und er nimmt schon das Rezept.** Der Shop stellt fest, dass er zu
„Pho" nichts gespeichert hat, holt es bei Chefkoch (gemessen 90 bis 147 ms)
und antwortet damit:

```
„Pho Bo - Vietnamesische Rindfleischsuppe" von Chefkoch — 23 Zutaten im
Rezept, 19 davon auf dem Zettel, 11 im Katalog gefunden. Bestbewertetes
Rezept zum Gericht (4.84 aus 62 Stimmen). Ohne Katalogtreffer und deshalb
als Freitext: „Markknochen", „Nelken", „Sternanis", „Fischsauce", …
```

Mit Zwiebeln, Zimtstangen, Ingwer, Thai-Basilikum, Mie Nudeln, Rinderfilet,
Zitronen und Chilisauce in der Liste.

**Wogegen das antritt**, gemessen am 2026-08-28 gegen die echte Box: das
Modell weiss nicht, was Pho ist. Es hängt sich an „Rind", wiederholt
3.358 Zeichen lang dieselben drei Begriffe und liefert entdoppelt

```
Rinderhack · Rinderknochen · Rinderbrust
```

Alle drei finden ein Katalogprodukt — „KIKOK Hähnchenbrust mit Knochen",
„Mark&Fein BIO Rind Gulasch". **Keines davon gehört in eine Pho.** Das ist
der Grund, warum „hat etwas gefunden" die falsche Zahl ist. Bis WB-367 war
genau diese Liste die Antwort auf den ERSTEN Satz — der Abruf lief daneben in
einem eigenen Prozess, und erst der zweite Satz bekam das Rezept.

Vier Dinge sind hier einen Satz wert:

* **Die acht Freitexte sind kein Makel, sondern die ehrliche Hälfte.** Der
  Katalog hat keine Sternanis und keine Fischsauce; sie stehen als Freitext
  da, statt still zu verschwinden.
* **Der erste Zug dauert länger als jeder weitere**, und zwar grob doppelt so
  lange: der Gerichtsname kommt aus Stufe 1 (ein Modelllauf), und die
  Zutatenliste des Rezepts braucht wieder das Modell, um Suchbegriffe daraus
  zu machen. Der Abruf dazwischen ist der billigste Teil des Zugs.
* **Der Trace sagt, woher die Zutaten kamen**: `picknick.path = chefkoch`,
  `picknick.dish = "Pho"`, `picknick.dish_fetch = ok`. Steht dort `llm` mit
  gesetztem `dish`, hat der Abruf nicht getragen — `dish_fetch` sagt dann
  `leer` oder `fehler`.
* **Das Rezept ist jetzt unter *Rezepte*** — mit 17 Schritten Zubereitung,
  90 Minuten Vorbereitung, 480 Minuten Kochzeit und einem Link auf die
  Originalseite. Wer abends um sieben Pho anfängt, sollte das vorher wissen.

### Und beim Abschicken wird daraus ein Rezept (WB-337)

Seit WB-337 endet dieselbe Antwort mit einem Satz mehr — hier gemessen am
2026-08-28 gegen die echte Box und den echten Katalog, mit dem Satz
„alles für Spaghetti Bolognese, und Klopapier“:

```
„Die echte Sauce Bolognese“ von Chefkoch — 13 Zutaten im Rezept, 11 davon
auf dem Zettel, 10 im Katalog gefunden. […] Daraus kann ein Rezept
„Spaghetti Bolognese“ werden — 11 Zutaten, gespeichert erst beim Abschicken.
```

Unter den Vorschlägen steht dann ein **Rezeptentwurf**: der Name (überschreibbar),
die elf Gerichtszutaten mit ihren Mengen, je ein „raus“ daneben und ein
„Kein Rezept daraus“ darunter. **Das Klopapier steht nicht darin** — es liegt
im Korb und gehört in kein Rezept. Die Trennung kostet keinen Modellaufruf:
was aus Chefkochs Zutatenliste stammt, findet seine Zutat wieder
(`assistant.herkunft`), was daneben im Satz stand, nicht.

### Und das Klopapier verschwindet nicht mehr (WB-370)

Bei genau dieser Messung fiel auf, dass die zweite Hälfte des Satzes gar
nicht auf dem Zettel stand: elf Begriffe kamen zurück, alle elf aus der
Zutatenliste. Der Rest ging als Prompt-Zeile mit, und das Modell durfte ihn
übergehen — 32 von 35 Gerichten taten das (gemessen am 2026-08-28, siehe
OBSERVABILITY.md). Seit WB-370 steht er nicht mehr im Prompt, sondern wird im
Code angehängt. Derselbe Satz mit einem anderen Gericht, gegen die echte Box,
**vorher und nachher**:

```
vorher   alles für Quiche Lorraine, und Klopapier
         -> 6 Produkte, 0 Freitext.  Klopapier kommt nirgends vor —
            weder in der Liste noch in der Antwort.

nachher  alles für Quiche Lorraine, und Klopapier
         -> 6 Produkte, 1 Freitext: „Klopapier“.
            „„Klopapier“ stand daneben im Satz und liegt als eigene
             Zeile dabei — nicht im Rezept.“
```

Der Preis steht in derselben Zeile: ohne Prompt-Zeile übersetzt das Modell
nichts mehr, und „Klopapier“ findet die Präfixsuche im Katalog nicht — es
bleibt Freitext. Gemessen übersetzt hat die Box den Rest allerdings in **null
von 35** Zügen; die Prompt-Zeile kostete den stillen Verlust und brachte
nichts dafür.

Beim **Abschicken** entsteht das Rezept, mit den bestätigten Zutaten und
ihren Mengen (125 g Butter, 1000 g Hackfleisch, 600 ml Tomaten …). Danach:

```
zweiter Satz „heute Spaghetti Bolognese“ -> weg = recipe, 11 Vorschläge,
                                            null Modellaufrufe
```

Geprüft mit einem Modell, das bei jedem Aufruf wirft — es wurde nie gefragt.
**Jedes so angelegte Rezept ist ein dauerhafter Fix für ein Gericht**, und
der Weg ist der schnellste, den der Shop hat.

**Zum Ausfall:** wer sehen will, was ohne Chefkoch passiert, zieht das Netz
ab und fragt nach einem Gericht, das noch nicht im Speicher steht. Der Zug
läuft mit den geratenen Begriffen zu Ende und sagt es: *„Chefkoch war für
„…" nicht zu erreichen (eine Stunde gemerkt, danach wird es neu versucht) —
die Zutaten hier hat das Modell aus dem Gedächtnis genannt."*

Zum Gegenstück ein Gericht, das das Modell kennt: `alles für Gemüselasagne`
liefert über die Quelle 13 Produkte und **null** Freitext — die Quelle
verschlechtert den Weg also nicht, der vorher schon funktionierte.

## 4. Der Trace — die Schuldfrage

`http://localhost:6006` öffnen, Projekt **`Picknick Agent`**, obersten Trace
anklicken.

**Prüfung:** ein Baum aus fünf Spans, in dieser Reihenfolge und
Verschachtelung:

```
CHAIN        chat.turn        „dazu brauche ich noch Zahnpasta und Butter“
 ├ LLM       plan.extract
 ├ RETRIEVER catalog.search
 ├ RETRIEVER catalog.search
 └ LLM       plan.choose
```

**`plan.extract` anklicken.**

**Prüfung:** Output wörtlich
`{"begriffe": [{"begriff": "Zahnpasta", "menge": 1}, {"begriff": "Butter", "menge": 1}]}`,
Token `252 prompt / 39 completion`, Modell `Qwen3.8-27B-Instruct`. Das Modell
hat den Katalog dabei **nicht gesehen** — es liefert Begriffe, keine Produkte.

**Den `catalog.search`-Span mit Input `Butter` anklicken.**

**Prüfung:** Phoenix zeigt eine **Dokumentenliste mit Scores**, nicht einen
JSON-Klumpen — weil der Span-Kind `RETRIEVER` ist und nicht `TOOL`:

```
4,0057  #1771  ButterBoyz handgemachte BIO Butter Chili & Röstzwiebel · 100 g · 4,79 €
4,0057  #1772  ButterBoyz handgemachte BIO Butter Feige & Anis        · 100 g · 4,69 €
3,9633  #1757  ButterBoyz handgemachte BIO Kräuterbutter              · 100 g · 4,79 €
3,9633  #1766  ButterBoyz handgemachte BIO Salzbutter                 · 100 g · 4,69 €
3,9633  #1768  ButterBoyz handgemachte BIO Steinpilzbutter            · 100 g · 4,99 €
```

**Den zweiten `catalog.search`-Span anklicken** (Input `Zahnpasta`).

**Prüfung:** `picknick.candidates = 0`, keine Dokumente — und **kein**
`picknick.rank_top`. Die fehlende Zahl ist Absicht: eine 0 dort wäre eine
erfundene Messung.

**Zurück auf `chat.turn`, Attribute aufklappen.**

**Prüfung:**

```
picknick.rejected     = 0
picknick.weakest_term = Zahnpasta
picknick.weakest_rank = 0.0
picknick.terms = 2   picknick.products = 1   picknick.free_text = 1
session.id = korb-1
```

**Das ist die Pointe.** `rejected = 0` heisst: das Modell hat **nichts
erfunden** — es durfte nur aus der vorgelegten Liste wählen und hat das getan.
Und die Liste enthielt keine normale Butter, sondern fünfmal ButterBoyz. Der
Fehlgriff „4,69 € Spezialbutter" gehört damit dem **Retrieval**, nicht dem
Modell — und man sieht es, ohne den Code zu kennen.

Die Umkehrung ist genauso ablesbar: stünde eine normale Butter unter den fünf
Dokumenten und das Modell hätte trotzdem die Trüffelbutter genommen, wäre die
Schuld beim Modell. `rejected > 0` wäre der dritte Fall — ein Modell, das eine
ID nennt, die nie vorlag. Der Shop verwirft die dann und repariert sie nicht.

`session.id = korb-1` ist die kleine Zugabe: mehrere Sätze zu **einem**
Einkauf liegen in Phoenix als eine Sitzung zusammen.

## 5. Die Entscheidung wird zum Eval-Label

Zurück im Shop, in der Vorschlagsliste:

* bei **Zahnpasta** auf **Ja**
* bei **ButterBoyz Salzbutter** auf **Nein**

**Prüfung:** die Zahl neben *Korb* springt auf `1`, die Zahnpasta-Zeile liegt
als Freitext im Korb, die Butter-Zeile ist als entschieden markiert und bleibt
sichtbar stehen.

Dann über die Korbbrücke („1 Sache im Korb — ansehen") auf den **Warenkorb** —
dort steht die Liste allein, ohne den Verlauf darunter — und
**Bestellung abschicken**.

**Prüfung:** Weiterleitung auf `/bestellungen`, die Bestellung steht auf
`offen`. Und in Phoenix, auf **demselben** `chat.turn`-Span:

```
identifier              annotator_kind  label     score  explanation
picknick-suggestion-41  HUMAN           kept       1,0   Zahnpasta
picknick-suggestion-42  HUMAN           removed    0,0   Butter
picknick-turn-22        HUMAN           —          0,5   1 von 2 entschiedenen Vorschlägen behalten.
```

(Die Zahlen in den `identifier` sind die Datenbank-IDs des gemessenen Laufs;
bei dir stehen andere. Dass sie aus der Datenbank kommen, ist der Punkt:
zweimal abschicken ergibt denselben Bestand, nicht den doppelten.)

Zurücklesen, ohne der Oberfläche zu glauben:

```bash
.venv/bin/python -c "
from phoenix.client import Client
print(Client(base_url='http://localhost:6006').spans
      .get_span_annotations_dataframe(span_ids=['<span-id>'],
          project_identifier='Picknick Agent')
      [['annotator_kind','identifier','result.label','result.score']])"
```

**Der Satz dazu:** `annotator_kind = HUMAN`. Das ist kein Formfeld. Die
Nutzerin hat die Liste durchgesehen, weil sie einkaufen wollte — nicht, weil
jemand einen Annotationsauftrag verteilt hat. Es ist Ground Truth ohne
Annotationskosten, und die Erklärung an jeder Annotation ist der Suchbegriff:
damit steht direkt an der Zeile, welcher Begriff schlecht gemappt hat.

`mapping_precision` hat **kein** Label. „0,5" ist die Aussage; ein „gut"/
„schlecht" daneben wäre eine Schwelle, die niemand festgelegt hat. Und ein Zug,
über dessen Vorschläge nie entschieden wurde, bekommt **gar keinen** Score —
eine fehlende Zahl ist ehrlicher als eine erfundene.

## 6. Der Vergleich (optional, ~4 Minuten)

```bash
.venv/bin/python evals/experiment.py basis prompt-b
```

**Prüfung:** am Ende die Vergleichstabelle mit
`kategorie_praezision 0,850 gegen 0,960` und
`zutaten_vollstaendigkeit 0,611 gegen 0,750`, dazu je eine Phoenix-URL.

**Und der Satz, der dazugehört:** zwölf Beispiele, ein Lauf, keine
Wiederholungen — der Unterschied bei der Präzision kommt aus zwei Beispielen,
und bei einem davon gewinnt Prompt B, indem er einen Artikel **weglässt**.
Warum das trotzdem ein Ergebnis ist und was es nicht ist, steht in
`EVALS.md`; die Liste dort gehört zur Zahl.

## 7. Phoenix aus, Shop an

Der Abschluss, wenn Zeit ist. Phoenix stoppen, dann noch einen Chat-Satz
schicken.

**Prüfung:** der Zug antwortet **unverändert schnell**. Keine Wartezeit, keine
Fehlermeldung, nur ein leiserer Trace-Ordner. Gemessen: fünf Spans gegen einen
geschlossenen Port kosten `0,15 ms` statt `34,93 s` — der
`NichtBlockierend`-Prozessor legt sie in eine Schlange, der Request läuft
weiter. Ein Observability-Werkzeug, das den Shop anhält, wäre das Gegenteil
von Betrieb.

## Wenn etwas nicht stimmt

| Symptom | Ursache |
|---|---|
| Chatband „Modell wacht auf … noch ~90 s" | Die Box schläft. `wake-vllm` und warten; der Rest des Shops läuft weiter, der Rezeptweg auch. |
| Chat antwortet „Das Modell hat den Satz nicht in Suchbegriffe zerlegt" | Kaputte Modellantwort. Kein Fehler des Shops — die Seite bleibt heil, die Nutzerin bekommt einen Satz. |
| Kein Trace in Phoenix | `PICKNICK_TRACING` steht auf `0`, oder Phoenix lief beim Start des Shops nicht (der Tracer wird beim Hochfahren eingerichtet). Shop neu starten. |
| Katalogseite leer | Kein Crawl gelaufen. `/status` sagt es im Klartext. |
| Prozess startet nicht, `Konnte nicht auf … binden` | `tailscaled` ist noch nicht da. `PICKNICK_HOST=127.0.0.1` für die Vorführung. |
