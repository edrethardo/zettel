# Das 65-Sekunden-Video — Drehbuch v2 (volle Produktion)

v2 ersetzt v1 („Bildschirm + Untertitel"): jetzt mit **Stimme, Gesichts-Einstieg
und Zwei-Fenster-Regie** — links die App in Handybreite, rechts Phoenix, das den
Trace **live** aufbaut, unten ein schmaler GPU-Streifen, in dem die 3090
ausschlägt. Gefilmt wird die **Demo-Instanz** (eigener Port, Kopie der
Datenbank, keine Haushaltsdaten) — nie der echte Shop auf 8730.

> **Was gilt (Stand 05.09., zweiter Schnitt):** der Abschnitt direkt hier
> drunter. Alles ab „Alle Zeitangaben unten" ist die Geschichte des Videos —
> 28,7-s-Züge, nvtop, Shot 0 mit Gesicht, Voice-Over, Supermarkt-Shot 6b — und
> steht, weil jede Zahl darin einmal bezahlt wurde. Nichts davon ist mehr
> Anleitung.

## Stand 05.09., zweiter Schnitt — nach dem Juror-Review

Ein frischer Fable-Juror hat beide Fassungen an 60 Einzelbildern gegen die
vier Contest-Kriterien geprüft (Präsentation 4/10) und vor allem gefunden:
5 s Schwarz am Anfang, Untertitel auf Feedbreite ~6 px hoch, „7 s" im
Untertitel neben einem Phoenix-Fenster mit P50 2,8 s, Nemotron vor der Endcard
nirgends im Bild, zwei Untertitel, die das Bild nicht belegt, und ein Trailer
ohne ein Wort Englisch. Alles davon ging ohne neuen Dreh; die beiden Takes
vom Morgen (`take_lang_nemotron2`, `take_handy_nemotron`) bleiben.

**Der Render ist jetzt ein Skript.** Bis dahin stand der letzte Schritt
(Einbrennen, Endcard anhängen) in keinem Skript:

    python3 schnitt.py take_lang_nemotron2 github.com/edrethardo/zettel
    #  -> take_lang_nemotron2_final.mp4 + .srt, titelkarte.png = thumbnail.png
    python3 handy.py take_handy_nemotron zettel_linkedin_nemotron.mp4

| Datei | Was |
|---|---|
| `zettel_demo_nemotron_final.mp4` (+ `.srt`) | lange Fassung, **90,9 s**, 1920×1250: 3 s Titelkarte, 79,7 s Shots, 8 s Endcard |
| `zettel_linkedin_nemotron.mp4` | Hochkant, **44,3 s**: 40,8 s Clip mit drei Textkarten, 3,5 s Endcard |
| `thumbnail.png` | = Titelkarte (Antwortmoment, GPU 100 %, Satz darüber) — als Vorschaubild hochladen |

Was sich geändert hat, und warum:

* **Titelkarte statt Schwarz.** `VORLAUF` ist weg; `titelkarte.py` nimmt das
  Bild 0,2 s nach der Antwort (der GPU-Streifen steht noch auf 100 %),
  dunkelt es ab und schreibt den Satz darüber: „Everything for lasagna — and
  toilet paper." · One sentence in, a shopping list out · Open NVIDIA
  Nemotron 3.5 · one RTX 3090 · no cloud. Der Juror: „Gesicht oder Schnitt,
  aber nie Schwarz" — und ein Gesicht ohne Ton wäre im Feed derselbe
  Scroll-Killer. Shot 0 ist damit gestrichen, nicht vertagt.
* **Die Antwortzeit kommt aus dem Bild.** Die Marke „Antwort da" fragt den
  Server und kam 4,4 s nach dem Bild (Antwort im Bild 2,9 s nach dem Klick,
  Marke bei 7,0 s). `schnitt.py::antwort_im_bild()` sucht in der App-Spalte
  den ersten Sprung nach dem Klick („Das Modell überlegt…") und den größten
  danach und bricht ab, wenn beides nicht zur Marke passt. Untertitel 2 sagt
  jetzt **3 s**, und der Schnitt trägt keine 4 s Standbild mehr.
* **Untertitelband unter dem Bild.** Das Bild wird um 150 px erweitert
  (1920×1250); die Untertitel stehen dort mittig in ~44 px statt ~32 px
  über dem GPU-Streifen. Kein Untertitel verdeckt UI, keiner über 3,5 Wörter/s
  (`schnitt.py` warnt sonst), keiner länger als zwei Zeilen.
* **Nemotron im Bild.** Rechts im GPU-Streifen steht als Badge, was beim Take
  auf der Box lief: `model  Nemotron 3.5 Lightning 30B-A3B` /
  `quant  W4A16 · vLLM 0.27 · Arize Phoenix`. Was das Bild noch nicht
  belegt: der Modellname im Trace selbst — das bräuchte in Shot 7 den
  LLM-Span statt der `chat.turn`-Attribute und damit einen neuen Take.
* **Zwei Untertitel auf das Bild zurückgeholt.** Shot 5 beginnt oben im
  Korb, wo die Toilettenpapier-Zeile steht (gemessen: 13 bis 10,5 s vor dem
  Bestellen, dann rollt die Seite), und springt dann ans Ende der Liste; der
  Text behauptet keinen „gefunden über"-Hinweis mehr, den es in der Korbzeile
  nicht gibt. Shot 7 sagt „1 in 1,167 across 128 dishes" ohne das
  unerklärte „0 shipped"; „one line below" wurde „right below".
* **Endcard 8 s, ~45 Wörter statt ~95**, mit Link, Name und `#NVIDIAGTC`;
  die Prozentvergleiche gehören in den Post.
* **Trailer mit Text.** Drei Karten im unteren Drittel des Blatts, an Marken
  des Takes gehängt (Satz tippen · Rezeptkarte · Pick-Liste), dann 3,5 s
  Endcard mit Link — vorher endete er hart auf Schwarz und trug kein
  einziges Contest-Kriterium.

**Runde 2 desselben Jurors** auf den neuen Dateien: Präsentation 4 → 7,
NVIDIA-Technik 4 → 6, Innovation gestützt 5 → 6, Trailer 3 → 7. Nichts
überlappt, nichts abgeschnitten. Seine sechs Reste, alle umgesetzt:
Untertitel 2 geteilt an der Antwort („The wait, uncut:" · „3 s — on my
3090 … Phoenix, on the right, clocks the same turn"), Shot 7 dreizeilig mit
„Here: 0." vor der 1-in-1.167, Korbkopf 2,2 s statt 1,7, Endcard „6 s per
dish (eval)", die Zeile „Zettel — a grocery agent …" ins Band des
Vorschaubilds (kein leerer Streifen), Trailer-Karte 2 ab +2,2 s über der
Zutatenliste statt über „4,85 aus 2125 Stimmen". Endstand: lange Fassung
**90,9 s**, Trailer **44,3 s**. Was bleibt, braucht einen neuen Take: der
Modellname im Phoenix-Span, und die GPU, die im 1-s-Raster von nvidia-smi
erst rot wird, wenn die Antwort schon steht.

Nicht getan, mit Grund: kein neuer Take für den Modellnamen im Trace (~20 min
plus Box-Fenster, Nutzen: ein Beleg statt eines Badges); „gab's nicht" trifft
weiter das Klopapier, das Untertitel 5 gerade gefunden hat — der Trailer
trifft den Spinat, das wäre die bessere Wahl für einen Nachdreh.

Alle Zeitangaben unten sind **am 29.08.2026 gemessen**, nicht geschätzt:

| Messwert | Wert |
|---|---|
| Chat-Zug, warmer Gericht-Cache | **28,7 s** (zweimal gemessen: 28,7 / 28,6) |
| Chat-Zug, kalter Cache (erster Satz eines Gerichts) | 36,7 s |
| Erster Span endet (`plan.extract`) | ~12 s nach Absenden |
| Span in Phoenix **abfragbar** nach seinem Ende | +0,1–0,3 s |
| Phoenix-UI-Refresh (Streaming-Poll, im Bundle verifiziert) | alle 2,0 s |
| **Span sichtbar im Phoenix-Fenster** | **~12,5–14,5 s nach Absenden** (schlimmstenfalls Span-Ende + 2,3 s) |
| Trace „fertig" im Fenster (`chat.turn` + `plan.choose`) | ~29–31 s nach Absenden |
| RTX 3090 beim Inferenz-Start | 0 % / 44 W → **100 % / 279 W innerhalb ~1 s**, Volllast über den ganzen Zug |

Die wichtigste Konsequenz: der Trace wächst **in zwei Schüben**, nicht
kontinuierlich. Bei ~12,5–14,5 s springen auf einen Schlag 16 Zeilen ins
Phoenix-Fenster (`plan.extract` + 15 × `catalog.search`), dann passiert dort
nichts bis ~29–31 s (`plan.choose` + `chat.turn`). Dazwischen rechnet das
Modell — die GPU-Leiste unten glüht durchgehend. Genau diese zwei Momente
trägt der Schnitt.

## Prep (einmalig, ~15 Minuten)

**1. Box wecken und prüfen, dass sie antwortet:**

```bash
wake-vllm
# dann bestätigen (Endpunkt = dein ZETTEL_LLM_ENDPOINT, siehe zettel.env):
curl -s "$ZETTEL_LLM_ENDPOINT/models" | head -c 200   # Modell-ID muss erscheinen
```

**2. Demo-Datenbank bauen** — Kopie der echten ohne Haushaltsdaten (Katalog
und Crawl-Historie bleiben):

```bash
mkdir -p ~/zettel-demo/bons
cd ~/code/picknick_klon
.venv/bin/python - <<'EOF'
import sqlite3, os
ziel = os.path.expanduser("~/zettel-demo/demo.db")
if os.path.exists(ziel): os.remove(ziel)
sqlite3.connect("data/picknick.db").execute("VACUUM INTO ?", (ziel,))
d = sqlite3.connect(ziel)
for t in ["chat_kandidat","chat_sorte","chat_entwurf","chat_rezept",
          "chat_suggestion","chat_message","order_item","orders",
          "recipe_item","recipe_ingredient","recipe","receipt_item",
          "receipt","dish_treffer","dish"]:
    d.execute(f"DELETE FROM {t}")
d.commit(); d.execute("VACUUM")
print("demo.db ready,", d.execute("select count(*) from product").fetchone()[0], "products")
EOF
```

**3. Demo-Instanz zum Aufwärmen starten** — mit **eigenem Phoenix-Projekt**.
Das ist neu gegenüber v1 und in v2 Pflicht: das Phoenix-Fenster ist jetzt
durchgehend im Bild, und im Projekt `Zettel Agent` stünden echte Züge mit
echten Eingaben in der Liste — Haushaltsdaten in Pixeln. Ein frisches Projekt
enthält nur, was die Kamera sehen darf. (Der Projektname per
`ZETTEL_PHOENIX_PROJECT` ist geprüft — die Spans landen dort.)

```bash
cd ~/code/picknick_klon
ZETTEL_HOST=127.0.0.1 ZETTEL_PORT=8747 \
ZETTEL_DB=~/zettel-demo/demo.db ZETTEL_BON_DIR=~/zettel-demo/bons \
ZETTEL_PHOENIX_PROJECT="Zettel Demo Aufwaermen" \
.venv/bin/python -m zettel.web.app
# Terminal offen lassen; die PID zeigt `ss -tlnp | grep 8747`
```

**4. Gericht-Cache aufwärmen**, damit der Zug vor der Kamera den
Chefkoch-Abruf überspringt und in ~29 s läuft statt ~37 s (das Rezept kommt
dann aus dem Cache der Kopie — exakt das, was im echten Betrieb bei jedem
zweiten Satz passiert):

```bash
curl -s -m 300 -X POST http://127.0.0.1:8747/chat \
  --data-urlencode "satz=alles für Lasagne, und Klopapier" -o /dev/null
# dann NUR Chat und Bestellungen zurücksetzen — die recipe-/dish-Tabellen
# MÜSSEN bleiben, der Gericht-Cache zeigt hinein:
.venv/bin/python - <<'EOF'
import sqlite3, os
d = sqlite3.connect(os.path.expanduser("~/zettel-demo/demo.db"))
for t in ["chat_kandidat","chat_sorte","chat_entwurf","chat_rezept",
          "chat_suggestion","chat_message","order_item","orders"]:
    d.execute(f"DELETE FROM {t}")
d.commit(); print("chat reset, dish cache kept")
EOF
```

**Die `recipe*`- und `dish*`-Tabellen gehören NICHT in diesen Reset.** Der
Gericht-Cache (`dish.recipe_id`) zeigt auf das gespeicherte Rezept; wer das
Rezept löscht und den `dish` behält, schickt den nächsten Zug stumm auf den
Modell-Pfad — keine Rezeptkarte, keine Mengen. (Beim Schreiben von v1
gemessen.) Der Aufwärm-Zug wird nie *abgeschickt*, `recipe_item` bleibt also
leer, und der Kamera-Zug nimmt weiter den Chefkoch-Pfad, nicht den
Gespeichert-Pfad.

**5. Instanz neu starten mit dem Kamera-Projekt.** `Ctrl+C` im Terminal aus
Schritt 3, dann derselbe Befehl mit dem endgültigen Projektnamen — so ist das
Phoenix-Fenster beim Dreh **leer**, und der einzige Trace, der je erscheint,
ist der gefilmte (der Aufwärm-Trace liegt im Wegwerf-Projekt aus Schritt 3):

```bash
ZETTEL_HOST=127.0.0.1 ZETTEL_PORT=8747 \
ZETTEL_DB=~/zettel-demo/demo.db ZETTEL_BON_DIR=~/zettel-demo/bons \
ZETTEL_PHOENIX_PROJECT="Zettel Demo" \
.venv/bin/python -m zettel.web.app
```

**6. GPU-Streifen.** nvtop läuft **auf der Box** (die 3090 steckt dort, nicht
im Laptop — der hat eine RTX 3500). Einmalig installieren, falls es fehlt
(Stand 29.08.: fehlt), dann in einem eigenen Terminalfenster starten:

```bash
BOX=<dein-ssh-alias-für-die-gpu-box>              # steht in ~/.ssh/config
ssh "$BOX" 'sudo apt install -y nvtop'            # einmalig, fragt nach dem Passwort
gnome-terminal --title="RTX 3090" -- ssh -t "$BOX" nvtop
# Rückfall ohne Installation (weniger hübsch, tut es auch):
gnome-terminal --title="RTX 3090" -- ssh -t "$BOX" watch -n1 nvidia-smi
```

Wichtig: den **Alias** aus `~/.ssh/config` benutzen (er trägt User und Key) —
der nackte mDNS-Hostname der Box landet beim falschen User und scheitert mit
Permission denied.

**7. Fensterlayout für 1920 × 1080.** Gefilmt wird der **Laptop-Bildschirm**
(eDP-1) — er ist exakt 1920 × 1080. Sein Versatz im X-Screen (aktuell
`+5120+146`) steht in:

```bash
xrandr | grep -w connected     # eDP-1 connected 1920x1080+5120+146 …
```

Öffnen in dieser Reihenfolge, dann platzieren (Koordinaten = Versatz + Position
im Bild; bei anderem Versatz die 5120/146 anpassen):

| Fenster | Inhalt | Position im Bild | wmctrl (bei Versatz +5120+146) |
|---|---|---|---|
| Firefox 1 | `http://127.0.0.1:8747/chat`, dann `Ctrl+Shift+M` (Responsive-Modus), Größe **390 × 844** | links, 0/0, **520 × 1080** | `wmctrl -r "Zettel" -e 0,5120,146,520,1080` |
| Firefox 2 (neues Fenster, `Ctrl+N`) | `http://localhost:6006` → Projekt **Zettel Demo** → Tab **Spans** | rechts oben, 520/0, **1400 × 830** | `wmctrl -r "Phoenix" -e 0,5640,146,1400,830` |
| Terminal aus Schritt 6 | nvtop | rechts unten, 520/830, **1400 × 250** | `wmctrl -r "RTX 3090" -e 0,5640,976,1400,250` |

`wmctrl` matcht auf Titel-Teilstrings; die drei Fenster heißen „Chat —
Zettel", „Phoenix" und „RTX 3090" und sind damit eindeutig. Die GNOME-Leiste
oben (~30 px) bleibt im Bild — das ist in Ordnung, es ist ein echter Desktop.
Das Telefon-Fenster füllt so gut ein Viertel der Breite, Phoenix die
restlichen drei.

**Im Phoenix-Fenster vor dem Dreh prüfen:** oben rechts neben der
Zeitbereich-Wahl sitzt der **Streaming-Umschalter** (Puls-Symbol). Er steht ab
Werk auf „an", aber die Einstellung ist **im Browser gespeichert** — wer ihn je
ausgeschaltet hat, bekommt ein Fenster, das sich nie von selbst füllt.
Anschalten, Projekt `Zettel Demo` öffnen, Tab **Spans**: die Liste ist leer
und wartet.

**8. Aufräumen danach:** `Ctrl+C` für die Demo-Instanz (oder `kill <PID>` —
nie `pkill -f`), `rm -r ~/zettel-demo`, nvtop-Terminal schließen. Die
Wegwerf-Projekte `Zettel Demo*` können in Phoenix bleiben oder über die
Projektverwaltung gelöscht werden.

## Aufnahme

Drei getrennte Aufnahmen, zusammengesetzt wird im Schnitt:

* **Take A — Bildschirm** (ohne Ton): der komplette Durchlauf am Stück, ~3 min.
* **Take B — Gesicht** (mit Ton): Shot 0, mit dem Telefon in Augenhöhe gefilmt,
  Fensterlicht von vorn, 3–4 Versuche, den besten nehmen.
* **Take C — Voice-Over** (nur Ton): das Skript unten wortwörtlich abgelesen,
  im selben ruhigen Zimmer, Handy-Diktiergerät oder `ffmpeg -f pulse`.

**Ohne OBS** (reicht völlig, alles ist installiert):

```bash
# Take A — der Laptop-Bildschirm als 1080p30, Stoppen mit q:
ffmpeg -f x11grab -framerate 30 -video_size 1920x1080 -i :0.0+5120,146 \
       -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p ~/take_a.mkv
# Take C — Mikro separat (Pegel vorher in den GNOME-Einstellungen → Ton prüfen):
ffmpeg -f pulse -i default -ac 1 ~/voiceover.wav
```

(Der eingebaute GNOME-Recorder `Ctrl+Alt+Shift+R` nimmt den **gesamten**
X-Screen auf — beim Zweischirm-Setup 7040 px breit — und stoppt nach 30 s:
für diesen Dreh unbrauchbar.)

**Mit OBS** (wenn lieber alles in einem Werkzeug):

```bash
sudo apt install -y obs-studio
# Szene: „Bildschirmaufnahme (XSHM)" → Bildschirm eDP-1; Mikro ist als Quelle vorangelegt.
# Einstellungen → Video: 1920×1080, 30 fps → „Aufnahme starten" (mkv).
```

## Take A — der Durchlauf (Realzeit, so wird geklickt)

Eine einzige Bildschirmaufnahme, ruhige Mausführung, keine Hektik — der
Schnitt holt sich die Momente. `T` = Sekunden ab dem Klick auf **Fragen**.

| Realzeit | Tun |
|---|---|
| vor T | Aufnahme starten, 3 s Ruhe. Chatfeld leer, Phoenix-Spans-Liste leer, nvtop im Leerlauf (~44 W). |
| T−10…0 | Satz tippen: `alles für Lasagne, und Klopapier` — dann **Fragen**. |
| T+0…12 | **Hände weg.** Links dreht der Spinner, unten springt die 3090 binnen einer Sekunde auf 100 % / ~280 W. |
| T+12,5…14,5 | Rechts schlagen **16 Zeilen auf einmal** ein (`plan.extract` + 15 × `catalog.search`) — der Moment, für den das rechte Fenster da ist. Nicht klicken, nur geschehen lassen. |
| T+29…31 | Links landet die Antwort, rechts vervollständigen `plan.choose` und `chat.turn` die Liste (18 Zeilen), nvtop fällt zurück. 3 s Ruhe. |
| T+35 | Langsam zur Rezeptkarte scrollen; auf Zeit/Bewertung verweilen; die 5 Alternativ-Rezepte anreißen. |
| T+50 | Portionen **3 → 6**, **Mengen neu rechnen** — die Spinat-Zeile springt sichtbar von „600 g … 2 ×" auf „1200 g … 3 ×". |
| T+65 | **Ja** beim Spinat (auf „1200 g gebraucht — 3 × 0,54 kg" verweilen), dann runter zur Klopapier-Zeile: Cursor auf „Freitext", **Ja**. Genau diese zwei einzeln — sie sind die, die den Sammelrückweg gleich überstehen müssen. |
| T+85 | **Die Geste (WB-398).** Nach oben rollen: die Korbzahl im Kopf steht auf **2**. Wieder ans Listenende, **Alles übernehmen** — alle übrigen Zeilen gehen auf `kept`. Noch einmal nach oben: die Zahl ist **gesprungen**. Zurück ans Ende, den Satz unter dem Knopf lesen, **Doch nicht alles (n)** tippen. Die gesammelten Zeilen stehen wieder mit Ja/Nein da, **Spinat und Klopapier bleiben „im Korb"**. |
| T+110 | Oben **Korb**: einmal durchscrollen (gerechnete Mengen, Klopapier als Freitext), **Bestellung abschicken**. |
| T+125 | **Pick-Liste**: zwei Posten abhaken, bei einem **gab's nicht**. |
| T+140 | Ins Phoenix-Fenster: den `chat.turn`-Span anklicken → Trace-Ansicht. Die Span-Kette ruhig zeigen (chat.turn → plan.extract → catalog.search ×15 → plan.choose), einen `catalog.search`-RETRIEVER-Span aufklappen, bis die bewerteten Dokumente stehen, über `zettel.rejected = 0` verweilen. 5 s halten, Aufnahme stoppen. |

## Der Schnitt — Shots, Voice-Over, Untertitel

~67 s gesamt. VO wortwörtlich ablesen (Take C), Untertitel bleiben trotzdem —
LinkedIn spielt stumm. Wo Sprech- und Lesesprache auseinandergehen, weichen
sie bewusst voneinander ab.

| # | Zeit | Bild (aus Take) | Voice-Over (wortwörtlich) | Untertitel |
|---|---|---|---|---|
| 0 | 0:00–0:06 | **Take B:** Gesicht, ein Satz in die Kamera | `I built a grocery agent for my girlfriend and me — it runs on one RTX 3090 in my living room.` | `A grocery agent for our household — on one RTX 3090 in my living room.` |
| 1 | 0:06–0:12 | Take A, T−10…0: tippen, **Fragen** | `One sentence, the way we say it at home: everything for lasagna — and toilet paper. Send.` | `One sentence, like at home (the app speaks German): "everything for lasagna — and toilet paper."` |
| 2 | 0:12–0:20 | Take A, T+0…2 **hart geschnitten auf** T+11…15: Spinner links, rechts schlagen die 16 Spans ein, nvtop auf 100 % | `Right side: Phoenix, tracing the agent live. Bottom: my 3090, at full power. No cloud. No API key.` | `Phoenix traces the agent live — on the 3090 under my TV. No cloud, no API key. (~29 s, cut)` |
| 3 | 0:20–0:34 | Take A, T+29…60: Antwort, Rezeptkarte, Alternativen, Portionen 3 → 6 mit sichtbarer Neurechnung | `It found a real, top-rated recipe. Cooking time, rating, five alternatives. Guests tonight? Servings to six — every quantity rescales.` | `A real top-rated recipe in ~100 ms — set servings to 6 and every quantity and pack count rescales.` |
| 4 | 0:34–0:41 | Take A, T+65…80: **Ja** beim Spinat, **Ja** beim Klopapier-Freitext | `Nothing enters the cart without a yes. Twelve hundred grams — that's three packs. Computed, not guessed.` | `Nothing enters the cart without a Yes. "1200 g needed → 3 packs" — computed, not guessed.` |
| 4b | 0:41–0:51 | Take A: **Alles übernehmen**, Blick nach oben auf die gesprungene Korbzahl, **Doch nicht alles** (WB-397/WB-398) | `Two by hand. Then the whole list in one tap. And back again — not all of it after all. Every decision here is reversible.` | `Two confirmed by hand — then the whole list in one tap. "Not all of it after all" reopens exactly that batch; the two stay.` |
| 5 | 0:51–0:58 | Take A: Korb, Mengen, Klopapier-Zeile, **Bestellung abschicken** | `No catalog match for toilet paper — it stays on the list as free text. The cart is the order.` | `No catalog hit for toilet paper — it stays as free text instead of silently vanishing. The cart is the order.` |
| 6 | 0:58–1:04 | Take A: Pick-Liste, abhaken, **gab's nicht** | `In the store, we check things off. Wasn't there — that's an honest answer, too.` | `In the store we check items off — and "wasn't there" is the honest third state, used right here.` |
| 6b | 1:04–1:11 | **Take D (Aaron filmt selbst):** am Regal, Telefon in der Hand, mobile Ansicht, die zwei Zutaten abhaken — siehe „Der Supermarkt-Shot" unten | `And this is where it ends up: at the shelf, phone in hand.` | `Same list, at the shelf.` |
| 7 | 1:11–1:19 | Take A: Trace-Ansicht, Zoom auf die Span-Kette, RETRIEVER-Dokumente, `zettel.rejected = 0` | `Every turn is one trace. The model can only pick from what the shop retrieved. Invented products are rejected — and counted.` | `Every turn is one trace. The model may only pick from retrieved documents — invented IDs are rejected and counted. rejected = 0.` |
| 8 | 1:19–1:24 | **Endcard** (Standbild) | `Open weights. One GPU. Measured on sixty-four dishes. Link below.` | *(Text steht auf der Endcard)* |

Die Zeiten in der Spalte sind die geplanten; **die gültigen kommen aus
`schnitt.py`**, das sie am Ende des Laufs ausgibt („Zeitmarken fürs
Voice-Over"). Sie hängen an Ereignissen und nicht an der Uhr — ein Zug, der
drei Sekunden länger dauert, verschiebt alles Folgende.

**VO-Skript am Stück** (zum Ablesen; Zeitmarke = wann der Satz beginnt;
insgesamt ~150 Wörter, ruhiges Tempo):

```
0:00  I built a grocery agent for my girlfriend and me —
      it runs on one RTX 3090 in my living room.
0:06  One sentence, the way we say it at home:
      everything for lasagna — and toilet paper. Send.
0:12  Right side: Phoenix, tracing the agent live.
      Bottom: my 3090, at full power. No cloud. No API key.
0:20  It found a real, top-rated recipe.
      Cooking time, rating, five alternatives.
0:27  Guests tonight? Servings to six — every quantity rescales.
0:34  Nothing enters the cart without a yes.
      Twelve hundred grams — that's three packs. Computed, not guessed.
0:41  Two by hand. Then the whole list in one tap.
      And back again — not all of it after all.
      Every decision here is reversible.
0:51  No catalog match for toilet paper — it stays on the list as free text.
      The cart is the order.
0:58  In the store, we check things off.
      Wasn't there — that's an honest answer, too.
1:04  And this is where it ends up: at the shelf, phone in hand.
1:11  Every turn is one trace. The model can only pick from what the
      shop retrieved. Invented products are rejected — and counted.
1:19  Open weights. One GPU. Measured on sixty-four dishes. Link below.
```

Rund 175 Wörter statt 150 — die Geste aus Shot 4b und der Regal-Shot kosten
zusammen etwa 17 Sekunden. Wer bei 65 s bleiben will, kürzt Shot 3 (die
Alternativenliste) und nicht Shot 4b: 4b ist die Einstellung, die den Kern
zeigt, und die Alternativen stehen im Bild auch ohne eigenes Verweilen.

**Endcard** (Standbild, 5 s):

> *A grocery app for a multi-person household where an open NVIDIA Nemotron
> 3.5 Lightning model — 4-bit on a single RTX 3090 — turns "everything for
> lasagna, and toilet paper" into a real shopping list: fully traced in Arize
> Phoenix, evaluated across 64 dishes, zero cloud, zero API keys.*
>
> FastAPI · SQLite FTS5 · vLLM (Nemotron 3.5 Lightning 30B-A3B, W4A16 · Qwen3.8-27B as reference) · OpenTelemetry → Arize Phoenix
> 128 dishes: **85 %** of ingredients found, 87 % / 89 % per dish · 6 s per dish · the 27B reference: 87 %, three times slower · 1,364 tests · 79 checks
> Repo: `github.com/edrethardo/zettel`

(Seit 05.09.: das Video zeigt Nemotron 3.5 — Aarons Entscheidung nach Lauf 3
in EVALS.md; Qwen bleibt das Alltagsmodell der Box. `endcard.py` trägt
denselben Text.)

Die Zahlen der Endcard vor dem Dreh gegen `SHOWCASE.md`/`EVALS.md` abgleichen
— sie wachsen mit jedem Ticket.

## Warum diese Regie (wer umstellen will: nicht)

Shot 0 macht es zu einer Geschichte von jemandem statt einem Produktvideo.
Shot 2 ist die Einstellung für diese Jury: **dieselbe Sekunde** — links wartet
die App, rechts entsteht der Trace, unten zieht die 3090 279 W. Das geht nur,
weil die Spans beim Beenden exportiert werden (SimpleSpanProcessor) und die
Phoenix-UI alle 2 s nachlädt — beides gemessen, siehe Tabelle oben. Shots 3–6
sind das Produktversprechen und sein Ankommen in der echten Welt, Shot 7 ist
derselbe Zug noch einmal als bewerteter Dokumenten-Trace.

## Was beim Dreh schiefgehen kann

* **Das Phoenix-Fenster bleibt leer, obwohl der Zug läuft** → in den ersten
  ~12 s ist das **normal** (der erste Span endet erst dann — Tabelle oben).
  Bleibt es nach 16 s leer: Streaming-Umschalter prüfen (Puls-Symbol oben
  rechts; die Einstellung klebt im Browser), Tab **Spans** statt eines
  Dashboards, richtiges Projekt (`Zettel Demo`). Zur Not lädt `F5` von Hand.
* **Der Traces-Tab zeigt mitten im Zug eine Zeile namens `plan.extract`** →
  kein Fehler: solange der Wurzel-Span fehlt, zeigt Phoenix den ersten
  fertigen Kind-Span als Waisen-Zeile; am Zug-Ende wird daraus die
  `chat.turn`-Zeile. Deshalb filmt v2 den **Spans**-Tab — dort tickern die
  Zeilen ohne diesen Verwandlungstrick.
* **Der Zug endet mit „Chat nicht verfügbar"** → die Box ist wieder
  eingeschlafen. `wake-vllm`, warten bis sie antwortet, Reset aus Schritt 4,
  neuer Take.
* **Die Rezeptkarte fehlt** → Gericht-Cache oder sein Rezept wurde gelöscht
  (der Reset aus Schritt 4 darf weder `dish`/`dish_treffer` noch die
  `recipe*`-Tabellen umfassen). Neu aufbauen über Schritt 2 + 4.
* **Der Zug braucht ~37 s statt ~29 s** → der Cache war kalt; das ist der
  Erster-Satz-Pfad. Im Alltag fein, vor der Kamera zu lang — Reset nach
  Schritt 4 und den zweiten Satz filmen.
* **Vorschlagszeilen ohne Mengen** → der Zug ist auf den Modell-Pfad gefallen
  (`weg = llm`); die Antwort sagt es dazu. Gleiche Abhilfe: Schritt 4.
* **nvtop-Streifen zeigt nichts / SSH-Sitzung tot** → die Box war
  zwischenzeitlich suspendiert (nach ~120 min Leerlauf); die SSH-Sitzung
  stirbt dabei. Vor jedem Take: `wake-vllm`, nvtop-Terminal neu starten,
  kurz prüfen, dass unten Leerlaufwerte (~44 W) stehen.
* **nvtop-Fenster verdeckt oder aus dem Bild** → nach jedem Fenster-Öffnen
  die drei `wmctrl`-Zeilen aus Schritt 7 erneut ausführen; sie sind
  idempotent. Vor dem Take einmal kurz in die Aufnahmevorschau (oder ein
  Testbild) schauen: alle drei Ebenen sichtbar?
* **Ton übersteuert** → Take C mit 10 s Probesatz beginnen und den Pegel in
  den GNOME-Ton-Einstellungen so stellen, dass der Balken bei normaler
  Stimme nie den roten Bereich berührt; lieber leiser aufnehmen und im
  Schnitt anheben. Handy-Diktiergerät 20 cm seitlich vom Mund ist der
  einfachste übersteuerungsfeste Weg.
* **Wieder-Dreh** → Reset aus Schritt 4 genügt (Chat/Bestellungen leeren,
  Cache behalten). Damit das Phoenix-Fenster wieder leer beginnt, die Instanz
  mit frischem Projektnamen neu starten (`Zettel Demo 2`, …) — oder die
  alten Takes als ältere Zeilen unter dem neuen akzeptieren, der neueste
  steht oben.

## Stand: Take 4 liegt, Take 5 ist vorbereitet aber nicht gedreht (WB-398)

Der Abschnitt darunter beschreibt **Take 4**. Er ist die gültige Fassung,
solange kein neuer gedreht ist — mit den beiden abgeschwächten Untertiteln,
die er verdient (siehe unten).

**Take 5 ist vorbereitet, aber nicht aufgenommen.** Fertig und geprüft:
die neue Choreografie in `dreh.py` (Sammeln und Zurücknehmen, an der
laufenden Bühne einzeln durchgeklickt und in der Datenbank nachgesehen),
der lebendige Zeiger in `menschlich.py`, die Erkenner in `finde.py` und
`schnitt.py` mit Shot 4b und den zurückgetauschten Untertiteln. **Nicht
erledigt:** ein warnungsfreier Trockenlauf am Stück und der Take selbst
(er braucht ein Box-Fenster). Die `.srt` und die Schnittfassung unter
`~/zettel-video/` sind darum weiter die von Take 4.

## Take A liegt vorproduziert (WB-396)

Die **Bildschirmteile (Shots 1–7) sind aufgenommen** — es fehlen nur noch die
zwei Teile, die niemand ausser Aaron liefern kann: Shot 0 (Gesicht) und das
Voice-Over. Aufgenommen wurde am 29.08.2026 nicht der echte Schirm, sondern
ein verschachtelter: `Xephyr :78 -screen 1920x1080 -ac` auf `:1`. Der Grund ist
nicht Bequemlichkeit — auf `:1` liegt die angemeldete GNOME-Sitzung mit
Aarons eigenen Fenstern, und die gehört nicht in ein Contest-Video. Auf `:78`
steht nur, was für die Kamera hingestellt wurde.

Was in der Aufnahme steht: der Satz wird getippt und abgeschickt, der Zug
läuft **27 s** (gemessen: abgeschickt 20:42:57, Antwort in der Datenbank
20:43:24), der GPU-Streifen springt sichtbar auf **100 % / 276,6 W**, die
Spans schlagen bei **T+13…15 s** in einem Schub ins Phoenix-Fenster, die
Portionen gehen 3 → 6 und die Mengen rechnen sich sichtbar neu (600 g → 1200 g,
2 × → 3 ×), zwei „Ja", Korb, Bestellung, Pick-Liste, und am Ende der Trace mit
den bewerteten RETRIEVER-Dokumenten und `zettel.rejected = 0`.

**Wo es liegt** (Scratchpad der Sitzung, bewusst nicht im Repo — eine
Videodatei gehört nicht ungefragt in ein Repo, das gerade
veröffentlichungsfähig gemacht wurde), zusätzlich als Kopie unter
`~/zettel-video/`:

| Datei | Was |
|---|---|
| `zettel_shots_1-7_roh.mkv` | die ungekürzte Aufnahme am Stück, ohne Ton |
| `zettel_shots_1-7_schnitt.mp4` | nach Drehbuch geschnitten, mit 5 s schwarzem Vorlauf für Shot 0 |
| `zettel_shots_1-7.srt` | Untertitel (Spalte aus der Shot-Tabelle), nicht eingebrannt |

**Neu erzeugen** — drei Aufrufe, in dieser Reihenfolge:

```bash
bash aufnahme.sh 250     # startet ffmpeg (feste Länge!) und fährt dreh.py
python3 schnitt.py       # baut Schnittfassung + .srt aus den Zeitmarken
```

`dreh.py` fährt den Durchlauf mit `xdotool`: Tippen Zeichen für Zeichen mit
schwankendem Takt, Mausbewegung auf einem Weg mit Anlauf und Abbremsen. Das
ist kein Selbstzweck — ein Video, in dem der Zeiger springt und Text auf einen
Schlag im Feld steht, sieht nach Automat aus und nicht nach jemandem, der
einkauft.

### Zwei Rezepte im Bild — und weder „vegan" sagen noch zeigen

Der Nutzer will in Shot 3 sagen können „für jeden ist etwas dabei". Dafür muss
die **Alternativenliste aus WB-387 offen und lesbar im Bild stehen** — nicht
nur vorhanden sein. Sie steht ohnehin unter der Rezeptkarte; der Schnitt muss
den Moment nur halten, in dem beide Zeilen zu lesen sind:

    4,85 (2125 Stimmen)  Vegetarische Spinat-Gemüse-Lasagne …   <- Fokus, vorgewählt
    4,70 (5008 Stimmen)  Lasagne                                <- der Klassiker
    4,61 (1267 Stimmen)  Béchamel-Hackfleisch-Lasagne           <- sagt „Hackfleisch" im Namen

Der Satz dazu (Nutzer, bestätigt am 29.08.): *„The top-rated one is
vegetarian — 4.85 from 2,125 ratings. If you want the classic with meat, it's
right there in the list."* **Gewechselt wird nicht** — gezeigt wird, dass die
Wahl da ist, nicht wie man sie trifft; ein Wechsel kostete einen weiteren Zug
(30 s) und damit Videolänge.

**Das Spitzenrezept ist vegetarisch, nicht vegan.** Untertitel und Voice-Over
dürfen „vegan" nicht sagen: vegane Lasagne-Rezepte gibt es bei Chefkoch zwar,
aber sämtlich mit **0 Stimmen** — sie taugen weder als „sehr gut bewertet",
noch würde die Gewichtung sie je vorwählen. Ein Video, das „vegan" behauptet
und ein Rezept mit Frischkäse und Sahne zeigt, verliert genau die
Glaubwürdigkeit, um die es in diesem Beitrag geht.

### Was beim Nachdrehen schiefgeht — vier Fallen, alle bezahlt

* **Abschicken speichert das Rezept.** Nach einem vollen Durchlauf steht in
  `recipe_item`, was im Korb lag; der nächste Zug erkennt „Lasagne" als
  gespeichertes Rezept und nimmt den **Gespeichert-Pfad: 66 ms, ein einziger
  Span, keine Katalogsuchen**. Das Video wäre inhaltlich leer — kein Schub im
  Phoenix-Fenster, keine bewerteten Dokumente. Der Reset zwischen zwei Takes
  muss `recipe_item` deshalb mitnehmen. **Nicht löschen** dagegen `recipe`,
  `recipe_ingredient`, `dish` und `dish_treffer`: das ist der Gericht-Cache,
  und ohne ihn geht der Zug wieder über Chefkoch (37 s statt 29 s) oder fällt
  ganz auf den Modell-Pfad. Beide Hälften gehören zusammen — v1 ist in die
  eine Hälfte getappt, WB-396 in die andere.
* **`ffmpeg` mit fester Länge (`-t`) aufnehmen, nicht abbrechen.** Ein
  `kill -INT` erwischt ihn nicht zuverlässig: beim ersten Versuch lief er
  danach 23 Minuten weiter und rechnete in den nächsten Take hinein, und die
  MP4 blieb ohne `moov`-Atom liegen — **unlesbar, die ganze Aufnahme war
  weg**. MKV verträgt einen Abbruch, MP4 nicht.
* **Xephyr holt sich das Tastaturlayout des Elternschirms zurück.** Einmal
  `setxkbmap de` gesetzt, stand eine Stunde später wieder `us` — und
  `xdotool type` liess das „ü" in „für" stillschweigend weg. Im Bild stand
  „alles fr Lasagne". Das Layout gehört unmittelbar vor das Tippen.
* **Der Portionen-Knopf bricht ab ~540 px Fensterbreite nicht mehr um.**
  Beschriftung, Zahlenfeld und „Mengen neu rechnen" stehen dann auf **einer**
  Zeile. Wer die Abstände an einem schmaleren Fenster misst, greift 50 px
  daneben.

### Und die Warnung, die mehr wert ist als die vier Fallen

**Eine grüne Protokollzeile beweist hier nichts.** Take 3 schrieb
„Portionen 3 → 6" mit, obwohl der Klick danebengegangen war und die Mengen bei
3 Portionen blieben; gesehen wurde es erst beim Ansehen des fertigen Videos.
Prüf nach jedem entscheidenden Schritt die **Wirkung**, nicht den Klick —
`chat_rezept.portionen == 6`, `need_amount == 1200`, `order_item.missing_at`
gesetzt. Die Datenbank weiss es, das Bild ist Auslegung.

Vier Stellen, an denen ein Klickpunkt aus einem Bild stillschweigend falsch
wird — alle im Trockenlauf ohne Modell aufgelaufen und dort behoben:

1. **Firefox rollt weich.** Ein Bild, das unmittelbar nach einem Radklick
   entsteht, zeigt die Seite noch in Bewegung; der daraus gerechnete Punkt
   sitzt ein paar Pixel daneben. Vor jedem Griff warten, bis zwei Bilder
   gleich sind.
2. **Ist der Anfang der Zutatenliste schon aus dem Bild gerollt**, liefert der
   Anker eine Zeile aus der Mitte — und der Griff landet 200 px zu hoch, auf
   einer Zutat statt auf dem Portionsfeld. Der Anker taugt nur, wenn die
   erste Zeile weit genug unten steht.
3. **Die Freitextzeile ist kompakt gebaut**: ihre „Ja"-Pille verschmilzt mit
   dem Rest der Zeile zu 127 px Breite statt 44. Eine Formschranke, die auf
   44 × 44 besteht, wirft genau die Zeile weg, um die es geht.
4. **„Bis ans Ende scrollen" mit fester Klickzahl erreicht das Ende nicht** —
   die Liste ist mit sechs Portionen länger. Rollen, bis das Bild stillsteht.
   Und am Ende steht die letzte Zeile oft schon im Bild: dann darf nicht
   weiter hochgerollt werden, sonst wandert sie unten wieder hinaus.

Dazu kommt eine Eigenheit dieser Oberfläche, die jeden Bilderkenner in die
Irre führt: **die „Ja"-Pille, die eigene Chatzeile, die „im Korb"-Marke und
der obere Rand jeder Vorschlagskarte haben dieselbe helle Farbe.** Sie
unterscheiden sich nur in der Form (die Pille ist ~44 × 44 px, der Rest
170–220 px breit oder unter 30 px hoch). In WB-396 haben sich daraus **zwei
Fehler gegenseitig verdeckt**: der Erkenner meldete „Antwort da", weil er auf
die eigene Chatzeile hereinfiel — was zufällig zur richtigen Zeit geschah; die
Härtung gegen diesen Fehlgriff schloss dann die letzte Pille gleich mit aus,
und das zweite „Ja" landete auf der Butter statt auf dem Klopapier. Wer hier
ein Skript baut: such nach Form, nicht nach Farbe, und **frag für „ist die
Antwort da?" den Server** (`chat_message`) statt die Pixel.

**Der Trockenlauf, der das alles aufgedeckt hat, kostet keine Box.** Ein
gespeichertes Rezept (Zeilen in `recipe_item`) schickt den Zug über den
Gespeichert-Pfad: 66 ms, kein Modell. Eine Zeile davon als `free_text`
angelegt, und die Choreografie lässt sich vollständig üben — Portionen,
beide „Ja", Korb, Bestellung, Pick-Liste, „gab's nicht" — bis jeder Schritt
seine Wirkung meldet. Erst dann lohnt es, die Box für den echten Take zu
belegen. (Danach die Prüfstand-Zeilen aus `recipe_item` wieder löschen,
sonst nimmt der Kamera-Zug genau diesen Pfad.)

## Der Supermarkt-Shot (Shot 6b) — Drehanweisung

**Diesen Shot filmt Aaron selbst**, mit einer zweiten Kamera (oder dem
Zweitgerät): er am Regal, das Telefon in der Hand, auf dem Bildschirm die
mobile Ansicht, und er hakt die zwei Zutaten ab, die er gerade entnommen hat.
Er ist die Landung des ganzen Videos in der echten Welt — Shot 6 zeigt die
Pick-Liste auf dem Schirm, 6b zeigt, wofür sie da ist.

**Wo der Schnitt sitzt.** Davor endet **Shot 6** auf der abgehakten Zeile im
Bildschirmteil (die Zeile ist durchgestrichen, daneben steht die
„gab's nicht"-Marke). Direkt danach steht **Shot 7** (die Trace-Ansicht in
Phoenix). 6b sitzt also zwischen der Pick-Liste und dem Trace: erst die
Handlung im Laden, dann die Erklärung darunter. Länge 6–8 s, mehr trägt die
Einstellung nicht.

**Was auf dem Telefon offen sein muss:**

```
http://<laptop-im-tailnet>:8747/pick        ← die Demo-Instanz, NICHT 8730
```

* Die **Bestellung muss vorher abgeschickt sein** — die Pick-Liste entsteht
  erst beim Abschicken (`orders.abschicken`). Ohne das zeigt `/pick` eine
  leere Seite, und im Laden ist keine Zeit, das zu merken.
* **Die Demo-Datenbank, nicht der echte Einkauf.** Der echte Shop auf 8730
  trägt Haushaltsposten; wer den filmt, hakt vor der Kamera echte Einkäufe
  ab — und sie sind danach abgehakt. Die Demo-Instanz läuft auf **8747** mit
  `~/zettel-demo/demo.db` (Aufbau siehe Prep, Schritt 3/5).
* Das Telefon muss den Laptop erreichen: über das Tailnet (die Adresse steht
  in `ZETTEL_HOST`) oder dasselbe WLAN. **Vor dem Losgehen einmal am
  Telefon öffnen** — nicht erst im Laden.

**Die Pick-Liste muss dieselben zwei Posten zeigen wie der Bildschirmteil.**
Sonst passen die Szenen nicht zusammen: im Schirmteil bestätigt er Spinat und
Klopapier von Hand, und im Laden dürfen nicht plötzlich sechzehn Zeilen
stehen. Das ist **keine Selbstverständlichkeit** — siehe den nächsten
Abschnitt: nach „Alles übernehmen" und dem Rückweg liegen alle Zeilen im Korb
und damit in der Bestellung. Wer die zwei will, hat zwei Wege:

1. **Für den Regal-Shot eine eigene, kurze Bestellung abschicken** — Chat
   leeren, einen Satz ohne Sammeltipp fahren, nur Spinat und Klopapier mit
   „Ja" bestätigen, abschicken. Zwei Zeilen, fertig. Das ist der einfache Weg
   und der empfohlene: der Regal-Shot ist ohnehin eine eigene Aufnahme.
2. **Im Korb die überzähligen Zeilen löschen**, bevor abgeschickt wird — im
   Korb steht an jeder Zeile ein Löschknopf. Bei sechzehn Zeilen sind das
   vierzehn Tipps; nur sinnvoll, wenn ohnehin schon abgeschickt wurde.

**Lesbarkeit — der Bildschirm überstrahlt im Video fast immer:**

* **Telefon hochkant.** Die Ansicht ist für 390 px gebaut; quer stünde die
  Liste in einer Breite, für die sie nie entworfen wurde.
* **Helligkeit auf ~70 %, nicht auf 100 %.** Ein voll aufgedrehtes Display
  brennt in der Kamera aus, und die Schrift verschwindet in einer weissen
  Fläche. Automatische Helligkeit vorher **aus** — sonst regelt sie mitten in
  der Einstellung nach.
* **Nicht gegen das Regallicht filmen.** Neonröhren im Rücken der Kamera
  spiegeln sich im Glas; einen Schritt zur Seite, bis die Spiegelung aus dem
  Bild ist.
* **Abstand 30–40 cm**, Telefon leicht gekippt, damit die Kamera auf den
  Bildschirm schaut und nicht schräg darüber. Näher als 30 cm sieht man die
  Liste nicht mehr im Zusammenhang, weiter weg ist die Schrift nicht mehr zu
  lesen.
* **Zwei Takes**: einen mit dem Regal im Hintergrund (weiter), einen nah auf
  den Bildschirm. Der Schnitt nimmt den nahen, wenn die Schrift trägt, sonst
  den weiten mit dem Untertitel darüber.
* Der Daumen soll das Kästchen **sichtbar** treffen — die Bewegung ist die
  Aussage, nicht das Ergebnis.

## Was WB-398 gemessen hat

### Die Korbzahl geht 2 → n, aber nicht wieder zurück

Am 30.08. an der laufenden Bühne nachgemessen, zweimal (einmal über die
Oberfläche, einmal über `TestClient`):

```
zwei einzelne „Ja"      Kopfzahl  2   order_item  2
„Alles übernehmen"      Kopfzahl 17   order_item 17
„Doch nicht alles"      Kopfzahl 17   order_item 17    ← bleibt
                        decisions: 2 × kept, 15 × offen
```

**Das ist kein Fehler, sondern die Zusicherung aus WB-361/WB-397:** der Korb
wird beim Zurücknehmen NICHT angerührt. `orders.einlegen()` fasst gleiche
Zeilen zusammen, die Korbzeile kann also längst eine sein, die die Nutzerin
selbst aufgestockt hat; sie herauszunehmen hiesse, fremde Mengen zu löschen.
Die Oberfläche verschweigt es nicht — unter dem Rückweg steht „Nimmt nur
diesen einen Sammeltipp zurück — einzeln Entschiedenes bleibt stehen, und der
Korb auch", und an jeder zurückgenommenen Zeile „die Zeile bleibt im Korb;
dort steht ein Löschknopf".

**Fürs Video heisst das:** der sichtbare Beleg für die Rücknahme sind die
**Zeilen**, nicht die Zahl. Nach dem Rückweg stehen fünfzehn Zeilen wieder mit
Ja/Nein da, und mittendrin bleiben Spinat und Klopapier als „im Korb" stehen —
das ist im Bild deutlicher als eine Zahl im Kopf. Der Untertitel sagt es
darum so: *„…reopens exactly that batch; the two stay."* Ein Untertitel, der
behauptet, der Korb sei wieder bei zwei, wäre gelogen.

### Zwei neue Fallen, beide im Trockenlauf aufgelaufen

* **Der Portionsblock bricht um — und dann sitzt der Knopf UNTER dem Feld.**
  WB-396 hat Feld und Knopf über feste Abstände zum Zutatenlisten-Anker
  gegriffen (`anker + 224 px` bzw. `+ 367 px`, beide auf derselben Zeile). Im
  520-px-Fenster steht „Mengen neu rechnen" eine Zeile TIEFER; der Griff nach
  dem Feld landete auf dem Knopf, die „6" wurde ins Nichts getippt, und die
  Portionen blieben bei 3 — mit grüner Protokollzeile. Seit WB-398 sucht
  `finde.portionsfeld()` beide über ihre Form (schmaler Kasten = Feld,
  breiter = Knopf) und kommt mit beiden Layouts zurecht.
* **Die abgerundete rechte Kante jeder Vorschlagskarte trägt dieselbe Farbe
  wie die „Ja"-Pille.** Bei x=503 läuft sie über 85 % der Fensterhöhe durch.
  Ein Erkenner, der nur senkrecht schneidet (`finde.treffer`), verklebt
  dadurch Karte und Pille zu einem 200-px-Klumpen, und die Formschranke wirft
  genau die Pillen weg, die sie finden soll. `finde.flecken()` wirft erst
  Randspalten weg (Spaltendeckung > 40 %) und bestimmt danach echte
  zusammenhängende Flächen — gemessen kommen 42 × 42 px heraus. **Das ist
  dieselbe Falle wie in Take 4, nur von der anderen Seite**: dort waren es
  Chatzeile und „im Korb"-Marke, hier der Kartenrand.
* Nebenbei: **zwischen zwei Trockenläufen muss der Browser zurück auf
  `/chat`.** Ein Durchlauf endet auf der Pick-Liste, und ein `F5` lädt dann
  die Pick-Liste neu. Dafür gibt es `bereit.sh`.

### Der Zeiger im Wartefenster

`menschlich.leerlauf(sekunden, feld, abbruch=…)` bewegt den Zeiger während
der Wartezeit: winziger Drift (2–7 px), gelegentlich ein kurzes Wandern in
Leserichtung mit Rücksprung an den Zeilenanfang, und **einmal** pro Fenster
an den Rand und zurück. Die Pausen dazwischen kommen aus zwei Töpfen (0,9–2,6 s
und 3,5–8,0 s), damit die Abstände nicht um einen Mittelwert pendeln — ein
Zittern in festem Takt sieht schlimmer aus als Stillstand. Das Feld ist ein
Rechteck, das nie verlassen wird: über der Phoenix-Tabelle poppen Tooltips
auf, und ein Zeiger, der auf einer Schaltfläche parkt, färbt sie ein. Das Ende
hängt am Server (`chat_message`), nicht an einer Uhr.

Dazu `rollen()` in Schüben von zwei bis vier Radklicks mit Lesepause und
gelegentlichem Klick zurück, und `klick()` mit einer Korrekturbewegung: in
65 % der Fälle landet der Zeiger 6–15 px neben dem Ziel, hält kurz inne und
fasst nach. Ohne sie sieht jeder Klick aus wie ein `mousemove`, weil er es ist.

### Die Bühne steht in Skripten

Unter `~/zettel-video/` (nicht im Repo — eine Videodatei und ein
Tastatur-Roboter gehören nicht hinein):

| Skript | Was |
|---|---|
| `buehne.sh` | Xephyr :78, Demo-Instanz auf 8747, zwei Firefox-Fenster, GPU-Streifen; merkt jede PID in `buehne.pids` |
| `bereit.sh [--pruefstand]` | Datenbank in den Vor-Take-Zustand, Browser zurück auf `/chat` |
| `pruefstand.py hin\|weg\|reset` | Gespeichert-Pfad an/aus (16 `recipe_item`-Zeilen), Reset ohne den Gericht-Cache |
| `dreh.py [--trocken]` | der Durchlauf; `--trocken` lässt Shot 7 aus, weil es ohne Box keine Spans gibt |
| `schnitt.py` | Schnittfassung + `.srt`, und die Zeitmarken fürs Voice-Over |

Aufgeräumt wird über `buehne.pids`, **nie über `pkill -f`**: auf `:1` liegt
Aarons angemeldete Sitzung, und der echte Shop auf 8730 läuft im selben
Prozessbaum-Muster.


## Nachdreh 30.08. — was Umbenennung und Redesign an der Aufnahme kaputt gemacht haben

Der Dreh sollte wiederholt werden, weil die Seite deutlich besser geworden
ist. Die Aufnahme-Automatik (`~/picknick-video/`) war dafür **doppelt tot**,
und beide Male auf eine Art, die aussah wie ein kaputter Bilderkenner.

**1. Die Umbenennung (WB-401).** `buehne.sh` startete `picknick.web.app` mit
`PICKNICK_*`, `pruefstand.py` importierte `from picknick import db`. Die
Bühne kam gar nicht erst hoch.

**2. Das Redesign (WB-400) und der Dunkelmodus (WB-418).** Die Aufnahme sucht
ihre Ziele über Farbe und Form. Beide Tickets haben die Farbwelt verschoben:

    Akzent      grün  (62,125,51)  -> blau (36,86,184) -> hellblau (130,170,245)
    Linie       beige (229,220,201) -> grau (217,223,231) -> dunkel (49,61,74)

Mit den alten Werten fand sie **nichts**: keine Zutatenlinie, kein
Portionsfeld, keine Sammelknöpfe, keinen Bestellknopf. Drei Trockenläufe, drei
Warnungen, eine Ursache.

**Die Lehre, und sie ist die einzige, die nicht verfällt:** die Farben stehen
nicht mehr im Skript. `finde.ton("akzent")` liest sie aus `stil.css`. Wer die
Farbwelt ändert, ändert sie an einer Stelle, und die Aufnahme zieht mit.

### Vier Griffe, die der Umbau verschoben hat

* **Der „Fragen"-Knopf steht oben** (WB-416), gemessen bei y ≈ 368 statt am
  Seitenende. Das alte Suchfenster `y=(400, 1000)` griff daran vorbei.
* **Die Korbzahl klebt im Kopf.** Die zwei Fahrten hinauf und zurück, die
  WB-398 brauchte, um sie beim Springen zu zeigen, sind rund zwölf Sekunden
  Video für eine Zahl, die jetzt ohnehin dasteht. Sie sind raus.
* **Das Rad dreht dort, wo der Zeiger steht.** Nach einem Klick in die Leiste
  steht er in der Leiste, und dann scrollt nichts — die Schleife hielt das
  für „am Ende" und der Bestellknopf stand achthundert Pixel tiefer.
* **Feste Klickpunkte in der Leiste sind vorbei.** `m.klick(305, 111)` traf
  „Korb", bis das Abzeichen zweistellig wurde; danach traf derselbe Punkt
  „Rezepte". Und `korb_posten()` meldete trotzdem 19 Posten — aus der
  Datenbank. **Die Marke log.** Jetzt prüft `titel()` den Fenstertitel; das
  ist die einzige ehrliche Auskunft darüber, wo man ist.

### Und eine neue Falle beim Zurücksetzen

`recipe_zuordnung` und `begriff_wahl` (WB-408/411) gehören in den Reset.
Stehen sie da, läuft der Kamera-Zug in unter 0,1 s durch — ohne
`plan.extract`, ohne `plan.choose`, ohne einen einzigen Span. Das Video
zeigte dann eine leere Phoenix-Liste und eine GPU, die nicht ausschlägt:
genau die Falle, die WB-396 schon einmal mit `recipe_item` gestellt hat, nur
eine Ebene tiefer.

### Die Hand vor der Kamera

„Lass die Mauszeigerbewegung und das Tippen realistisch wirken."

* **Das Übersteuern geht jetzt in Bewegungsrichtung.** Eine Hand schiesst über
  das Ziel hinaus; sie landet nicht in einem zufälligen Winkel daneben. Der
  alte Zufallswinkel sah aus wie ein Zielfehler, der Überschuss längs der
  Bahn sieht aus wie ein Arm, der bremst.
* **Auf langen Wegen ein Zögern auf halber Strecke** (35 %, ab 320 px). Eine
  Bahn, die in einem Zug durchzieht, ist das deutlichste Zeichen dafür, dass
  niemand sie fährt.
* **Vertipper mit Rücktaste** (3,5 % je Zeichen, Nachbartaste auf derselben
  Belegung). Alles andere am Tippen kann eine Maschine auch: schwankende
  Abstände, Pausen an Wortgrenzen. Was sie nicht tut, ist danebengreifen und
  es merken. Nur ASCII-Buchstaben werden vertippt — ein „ü", das auf dem
  verschachtelten Schirm nicht ankommt, wäre kein Vertipper, sondern ein
  verschlucktes Zeichen (Take 3).
* **Der Rhythmus ist schubweise**: der erste Anschlag nach dem Klick ins Feld
  dauert am längsten, im Wort läuft es schneller, Grossbuchstaben und Umlaute
  kosten extra.

Nachgemessen: mit **absichtlich 50 % Vertippern** getippt, kam in der
Datenbank `alles für Lasagne, und Klopapier` an — Zeichen für Zeichen
identisch. Die Korrektur verändert den Text nicht.

### Stand

Der Trockenlauf über den Prüfstand geht durch: Satz getippt, Antwort da,
Portionen 4 → 6, zwei Zeilen einzeln bestätigt, „Alles übernehmen" 2 → 19,
„Doch nicht alles" zurück auf 17 offen mit genau den zwei bestätigten,
Bestellung abgeschickt, ein Posten abgehakt, einer als „gab's nicht"
markiert. Offen ist ein kosmetischer Punkt: der Weg von der Bestellliste in
die Pick-Liste fällt noch auf die Adresszeile zurück, statt die Bestellkarte
zu treffen.

**Der Kamera-Take mit Modell und Phoenix steht noch aus** — die Box war den
ganzen Abend am Laden.


## Der Take vom 31.08. — der letzte (WB-420)

„Wir machen später keinen neuen Take mehr." Also steht hier, was drin ist,
damit ein Schnitt ohne neue Aufnahme wiederholbar bleibt.

**Rohaufnahme:** `~/picknick-video/take_a.mkv` (275 s, 1920×1080, 30 fps,
13,1 MB), Zeitmarken daneben in `zeitmarken.txt`.
**Schnitt:** `zettel_demo_2026-08-31.mp4` (85,5 s) und `.srt`.

Drei ältere Takes desselben Abends liegen daneben und sind NICHT verwendbar,
jeder aus einem benannten Grund:

    take_a_2247.mkv   Sammelknöpfe nicht gefunden, Phoenix auf der Übersicht
    take_a_2345.mkv   Sammelknöpfe nicht gefunden
    take_a_2353.mkv   sauber, aber Shot 7 zeigt eine korb.menge-Spur

**Der letzte Lauf hat als einziger keine Warnung erzeugt** — und das ist die
Bedingung, unter der die Untertitel benutzt werden dürfen (siehe
`schnitt.py`): jede Aussage im Text ist eine Wirkung, die `dreh.py` in der
Datenbank nachgeprüft hat.

| Marke | s | was im Bild steht |
|---|---|---|
| ABGESCHICKT | 12,6 | der Satz ist weg, die 3090 springt an |
| Antwort da | 32,5 | **19,9 s** Modelllauf, zwei Stufen |
| Portionen umgestellt | 56,3 | 4 → 6, Bedarf Spinat **1200 g** |
| Ja beim Spinat / Klopapier | 76,4 / 123,0 | zwei Zeilen einzeln, eine davon Freitext |
| Alles übernehmen | 150,2 | offen 0, **Korb 2 → 14** |
| Doch nicht alles | 162,3 | offen 12, kept 2 — die zwei einzeln bestätigten bleiben |
| Bestellung abgeschickt | 196,2 | 14 Posten |
| abgehakt / gab's nicht | 218,1 / 224,1 | `missing_at` gesetzt |
| chat.turn geöffnet | 236,5 | 11 Span-Namen sichtbar |
| catalog.search geöffnet | 246,2 | Retriever-Dokumente mit Score |

Die letzte Einstellung zeigt den Trace `57c1a081…` mit **24,9 s Latenz** und
dem Baum `recipe.zuordnung → plan.extract → 12 × catalog.search →
plan.choose`; aufgeklappt ist ein Retriever-Span mit Input `"Aubergine"` und
Output `document 5005`, Score **13,08**, „Aubergine – Japanische Aubergine,
Beutel · 250 g · 2,99 €" samt `via`, Kategorie, Preis und `vorraetig: false`.

### Was am Schnitt noch fehlt

* **Shot 0 (Gesicht) und das Voice-Over** — die 5 s Schwarz am Anfang sind
  dafür reserviert, das Sprechskript steht oben.
* Die Aufnahme ist 2,5 s kürzer als der Durchlauf; abgeschnitten ist nur die
  Schlusspause nach der letzten Marke. Wer nachträgt, setzt `DAUER` in
  `aufnahme.sh` auf 300.

## Stand 05.09. — der Nemotron-Take

Nach Lauf 3 in `EVALS.md` (Nemotron 3.5 Lightning auf Augenhöhe mit Qwen,
6 s je Zug) hat Aaron entschieden: das Contest-Video zeigt Nemotron. Die
Box-Session hat das Modell 08:31 serviert und Idle-Stop wie Suspend für das
Fenster ausgesetzt — der GPU-Streifen zeigt darum bis zur letzten Sekunde
22,6 GiB resident statt der 0,1 GiB vom 02.09.

| Datei | Was |
|---|---|
| `take_lang_nemotron2.*` | der lange Take, ohne Warnung (der erste Anlauf `take_lang_nemotron.*` scheiterte an der Demo-DB, s. u.) |
| `zettel_demo_nemotron_final.mp4` | erster Schnitt (82,3 s) + Endcard, 87,6 s — **überholt vom zweiten Schnitt oben** |
| `take_handy_nemotron.*`, `zettel_linkedin_nemotron.mp4` | Hochkant-Clip, 40,8 s, 9 Tipps und 6 Radschübe gezeichnet, keiner ohne Weg — seit dem zweiten Schnitt mit Textkarten und Endcard, 44,3 s |

Was sich gegenüber dem 02.09. im Bild ändert: die Antwort kommt nach **3 s**
(Untertitel 2 nannte zuerst „7 s“ aus der Marke des Takes — die kam 4,4 s
nach dem Bild, siehe oben), die Rezeptkarte heisst „Lasagne" statt „Lasagne (2)", der Sammeltipp
legt 10 Zeilen in den Korb (Nemotron nennt zu den 16 Rezeptzutaten weniger
Suchbegriffe als Qwen, das 15 legte), Endcard und Stack nennen Nemotron.

**Der erste Anlauf und sein Grund:** `pruefstand.py reset` hatte am 04.09.
eine Regel bekommen, nummerierte Rezepte („Lasagne (2)") als Dubletten zu
löschen — „Lasagne (2)" war aber das Chefkoch-Rezept, auf das der
Gericht-Cache zeigt. Ohne es lief der Zug übers Modellgedächtnis: 8 Zeilen,
keine Karte, keine Portionen, zwei Warnungen. Regel zurückgenommen, Demo-DB
aus der Sicherung wiederhergestellt; das Rezept heisst in der Demo-DB jetzt
„Lasagne", und die App vergibt seit `0901476` keine Nummer mehr, wenn ein
geholtes Rezept einen Titel von seiner Quelle hat.

## Stand 04.09. — Endcard und eingebrannte Untertitel

Die Takes vom 02.09. (nach beiden Review-Wellen, Seitenwechsel über die
Reiterleiste, Shot 7 auf gesuchte Spans — siehe
`docs/superpowers/review/2026-09-01/REVIEW.md`, Nachtrag) sind die aktuelle
Grundlage. Dazu am 04.09. gebaut, alles in `~/picknick-video/`:

| Datei | Was |
|---|---|
| `endcard.py` → `endcard.png`, `_endcard.mkv` | die Endcard als 5-s-Standbild: Satz, Stack, Zahlen (83 % · 1.346 Tests · 79 Checks · 70 tok/s). Ohne Repo-Link, solange keiner feststeht — ein Platzhalter im Bild wäre eine Lüge |
| `zettel_demo_welle2_final.mp4` | lange Fassung **mit** Endcard (89,9 s) und **eingebrannten** Untertiteln — LinkedIn spielt stumm, und eine `.srt` lädt dort niemand hoch. Untertitel unten, mittig über der rechten Bildhälfte (`MarginL=104` im 384er-Raster von libass), 30 px, damit sie weder die App-Spalte noch den GPU-Streifen decken |
| `zettel_linkedin_welle2.mp4` | Hochkant-Clip, unverändert vom 02.09. |

**Befund am GPU-Streifen, offen:** die Box lädt das Modell nach dem Zug aus
dem Speicher (vLLM-Schlafmodus des syv-Stacks): bei 25 s stehen 100 % /
280 W / 22,8 GiB, ab ~65 s bis zum Ende **0 % / 43 W / 0,1 GiB**. Das ist
die Wahrheit über die Box — aber im Film sieht es drei Viertel der Zeit so
aus, als liefe das Modell gar nicht. Vor dem nächsten Take den Schlafmodus
aussetzen oder eine Wachhalte-Anfrage mitlaufen lassen; beides ist
Box-Konfiguration und liegt nicht im Repo.

## Der Take vom 01.09. — der Echtzeit-Take

Aufgenommen, weil der Shop seit dem Umstieg auf den syv-Stack (Docker,
requantisierte Embeddings, DFlash2) **in Echtzeit antwortet**. Der Take vom
31.08. zeigt dieselbe Anwendung mit 19,9 s Wartezeit; das ist nicht mehr die
Wahrheit über dieses System.

**Rohaufnahme:** `~/picknick-video/take_a_2026-09-01.mkv` (275 s, 1920x1080,
30 fps, 14,4 MB), Marken in `zeitmarken_2026-09-01.txt`.
**Schnitt:** `zettel_demo_2026-09-01.mp4` (84,5 s) und `.srt`.
Der Durchlauf hat **keine einzige Warnung** erzeugt — die Bedingung dafuer,
dass die Untertitel benutzt werden duerfen.

| Kennzahl | 31.08. | 01.09. |
|---|---|---|
| Antwort auf den Satz | 19,9 s | **8,8 s** |
| `chat.turn` in Phoenix | 24,9 s | **9,07 s** |
| ganzer Durchlauf | 277,5 s | 270,5 s |

Die Durchlaufzeit faellt kaum, und das ist kein Widerspruch: der Grossteil
sind Klicks, Rollwege und Lesepausen mit festem Takt. Gefallen ist der Teil,
in dem gewartet wird.

| Marke | s | was im Bild steht |
|---|---|---|
| ABGESCHICKT | 13,9 | der Satz ist weg, die 3090 springt an |
| Antwort da | 22,8 | **8,8 s** Modelllauf, zwei Stufen |
| Portionen umgestellt | 46,6 | 4 -> 6, Bedarf Spinat **1200 g** |
| Ja beim Spinat / Klopapier | 62,3 / 116,2 | zwei Zeilen einzeln, eine davon Freitext |
| Alles uebernehmen | 145,4 | offen 0, **Korb 2 -> 15** |
| Doch nicht alles | 157,4 | offen 13, kept 2 |
| Bestellung abgeschickt | 189,5 | 15 Posten |
| abgehakt / gab's nicht | 211,8 / 217,7 | `missing_at` gesetzt |
| chat.turn geoeffnet | 228,9 | 11 Span-Namen sichtbar |
| catalog.search geoeffnet | 238,9 | Retriever-Dokumente mit Score |

### Drei Fehlschlaege davor, jeder mit benanntem Grund

**1. Ein perfekter Durchlauf ohne Band.** `buehne.sh` startete Xephyr mit
`-resizeable`; damit folgt die Schirmgroesse dem Fenster, und der
Fenstermanager auf `:1` stutzte es auf 1850x1016 — 1920x1080 passt mit Rahmen
nicht auf einen 1920x1080-Schirm. ffmpeg brach in der ersten Sekunde ab
(`Capture area ... outside the screen size`), `dreh.py` lief 259 s fehlerfrei
durch und meldete am Ende „kein Schritt hat gewarnt". **Der Pruefstand des
Durchlaufs sagt nichts ueber die Aufnahme.** Behoben: `-resizeable` ist raus,
der Schirm bleibt 1920x1080, und dass das Fenster ihn nur ausschnittweise
zeigt, ist der Aufnahme egal — gegriffen wird der Schirm, nicht das Fenster.

**2. Freitextzeile nicht gefunden** (`take_a_0908_freitext-verfehlt.mkv`).
Nach `ans_ende()` steht unter der Vorschlagsliste der Rezeptentwurf, und der
ist mit sechs Portionen lang; zehn Radklicks rueckwaerts messen ihn nicht
durch. Folge waren ZWEI Warnungen aus EINER Ursache: die Klopapierzeile blieb
unbestaetigt, also stand danach nur eine Zeile auf `kept` statt zwei. Behoben:
dreissig Klicks, Rad ausdruecklich ueber der App-Spalte (WB-419).

**3. Ein Schnitt, der den Hoehepunkt uebersprang.** Shot 2 bestand aus zwei
Stuecken mit einem Sprung von `ab + 3,2` auf `ab + 11,9` — sinnvoll, solange
ein Zug 29 s dauerte. Bei 9 s liegt `ab + 11,9` HINTER der Antwort: der
Sprung uebersprang genau den Moment, den der Shot zeigen soll. Jetzt laeuft
die Wartezeit ungeschnitten durch, bis Shot 3 sie an der Antwort uebernimmt,
und der Untertitel sagt es: „The whole wait, uncut: 9 s."

**Die Lehre aus 3 gilt ueber das Video hinaus:** Ein Schnitt, der eine
Wartezeit kaschiert, ist an eine Dauer gebunden. Wird das System schneller,
kaschiert derselbe Schnitt nicht mehr — er verdeckt dann das Ergebnis.

## Der Hochkant-Clip fuers Telefon (`handy.py`)

Fuer LinkedIn: 15 s, 1080x1920, ohne Ton, nur die App. Kein eigener Dreh —
der Clip ist ein Ausschnitt aus demselben Band. Die App laeuft waehrend des
Takes ohnehin in Handybreite, ueber ihr sitzt ein gemalter Geraeterahmen.

    MAUS=0 bash aufnahme.sh 310     # Take OHNE Zeiger (siehe unten)
    python3 handy.py                # misst, malt, setzt zusammen

**Zwei Dinge, die dieser Clip anders macht als die lange Fassung.**

**1. Kein Mauszeiger.** `aufnahme.sh` kennt jetzt `MAUS=0` und gibt das an
`-draw_mouse` weiter. Ein Pfeil im Bild verraet die Aufnahme sofort — auf
einem Telefon gibt es keinen. Wegretuschieren geht nicht ehrlich: die Position
des Zeigers ist nur an den Klickmomenten bekannt, dazwischen waere jeder
uebermalte Fleck geraten. Die lange Fassung BEHAELT den Zeiger; dort ist es
ein Bildschirm, und die Zeigerbewegung ist das, was den Ablauf menschlich
aussehen laesst.

**2. Die Gesten werden gemessen, nicht gesetzt.** Die erste Fassung malte
einen Fleck fuer 0,45 s und einen Punkt, der mit konstanten 760 px/s nach oben
glitt. Beides sah falsch aus, aus demselben Grund: die Geste und das Bild
darunter wussten nichts voneinander. Die Seite rollt in SCHUEBEN (ein
Radklick, eine kurze Animation, Stillstand), der gemalte Finger glitt
gleichmaessig darueber hinweg.

`handy.py` bestimmt deshalb fuer jedes Bildpaar den senkrechten Versatz des
Inhalts (bester Ueberlappungsversatz auf einem verkleinerten Graubild).
Daraus kommt beides:

* **Wischer** — jeder zusammenhaengende Schub ist EINE Geste. Der Finger setzt
  drei Bilder vorher auf, bewegt sich um GENAU den gemessenen Versatz und hebt
  danach ab. Mehrere Schuebe werden zu mehreren kurzen Flicks; so rollt man auf
  einem Telefon wirklich. Der Schweif kommt aus der tatsaechlich gelaufenen
  Bahn und ist damit so lang, wie der Finger schnell war — ein Schweif fester
  Laenge sieht bei jedem Tempo gleich aus, und daran erkennt man eine gemalte
  Geste sofort.
* **Tipps** — der Moment ist NICHT die Zeitmarke. Die wird erst nach der
  Lesepause geschrieben und lag gemessen bis zu 0,73 s daneben. Gesucht wird
  der groesste Bildsprung im Fenster um die Marke, also das Bild, in dem die
  Oberflaeche reagiert; der Kringel beginnt zwei Bilder davor. Ein Kringel, der
  NACH der Wirkung aufblitzt, ist das Verraeterischste am ganzen Clip.

Der Kringel ist eine aufgehende Welle mit stehendem Kern, kein Fleck: ein
stehender Fleck auf dunklem Grund liest sich als Lichtreflex.

**Der Rahmen** (`handy_rahmen.png`, aus `handy.py` heraus einmal gebaut) ist
eine RGBA-Ebene mit durchsichtigem Loch bei (50, 89), 980x1741. Der Inhalt
wird exakt in dieses Loch skaliert — die Masse stehen an genau einer Stelle im
Skript und duerfen nur dort geaendert werden. Die Hoermuschel sitzt IN der
Blende und nicht als Kerbe im Bild: eine Kerbe verdeckte die Kopfzeile, um die
es geht.

**Das Drehbuch des Clips** sind die fuenf Stuecke in `SEGMENTE`. Wo eine Geste
hingehoert, steht dort NICHT — das ergibt die Messung.

## Der Hochkant-Clip ab 2026-09-01: Gesten kommen aus dem Protokoll

Der Abschnitt oben beschreibt, wie `handy.py` die Beruehrungen aus dem BILD
gemessen hat. Das gilt nicht mehr, und es galt auch nie richtig.

**Der Fehler.** `zeitmarken.txt` zaehlt ab Skriptstart, das Band ab
ffmpeg-Start; dazwischen liegen **2,08 s** (`aufnahme.sh` startet ffmpeg, dann
`sleep 2`, dann das Drehskript). `handy.py` hat `ffmpeg_start.txt` nie gelesen
und die skriptrelativen Zahlen direkt als Bandzeit eingesetzt. Das Suchfenster
des ersten Tipps lag damit mitten in der Tippphase, und `argmax` ueber
Bildspruenge lieferte dort Rauschen der Staerke 0,05 gegen 2,78 am wirklichen
Ereignis — ein Kringel beim Eintippen, der aussah, als gehoere er dorthin.
`schnitt.py:50-59` rechnet den Versatz seit jeher richtig heraus; das Wissen
ist beim Ableiten des Hochkant-Werkzeugs nicht mitgekommen.

**Die Loesung.** Das Drehskript schreibt mit, was es tut: `protokoll.py` ->
`gesten.jsonl`, eine Zeile je Klick, Radklick, Taste und Marke, mit Epoch und
Koordinate aus dem AUFRUF. `handy.py` liest das. Es wird nichts neu erhoben —
es wird nur aufgehoert, es wegzuwerfen.

Die Bildmessung ist geblieben und hat die Seite gewechselt:

* sie **prueft** die Zeitbasis am ersten Tipp (Abbruch, wenn der staerkste
  Bildsprung mehr als vier Bilder danebenliegt oder unter dem fuenffachen
  Rauschen bleibt),
* sie misst, **wie weit** ein Radklick die Seite bewegt hat — bei 1,8 px je
  Messzeile statt 7,2, mit Subpixel, und nur noch in den bekannten Fenstern.

`TIPPS`, `tippmoment()`, `tippstelle()` und `schuebe()` sind entfallen.
`SEGMENTE` steht als NAMEN von Zeitmarken; abgeschriebene Zahlen waren dreimal
veraltet.

**Betrieb:** `MAUS=0` schaltet das Protokoll ein (`aufnahme.sh`), weil MAUS=0
heisst „der Zeiger ist nicht im Bild, also muessen Beruehrungen gemalt werden".
`STAMM=<name>` legt Band, ffmpeg-Log, Protokoll und Marken unter einen
gemeinsamen Namen — getrennt sind die vier verwechselbar.

Alles Weitere, samt der Fallen, steht im Skill `.claude/skills/zettel-video/`.

