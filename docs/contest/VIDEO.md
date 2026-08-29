# Das 65-Sekunden-Video — Drehbuch v2 (volle Produktion)

v2 ersetzt v1 („Bildschirm + Untertitel"): jetzt mit **Stimme, Gesichts-Einstieg
und Zwei-Fenster-Regie** — links die App in Handybreite, rechts Phoenix, das den
Trace **live** aufbaut, unten ein schmaler GPU-Streifen, in dem die 3090
ausschlägt. Gefilmt wird die **Demo-Instanz** (eigener Port, Kopie der
Datenbank, keine Haushaltsdaten) — nie der echte Shop auf 8730.

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
# dann bestätigen (Endpunkt = dein PICKNICK_LLM_ENDPOINT, siehe picknick.env):
curl -s "$PICKNICK_LLM_ENDPOINT/models" | head -c 200   # Modell-ID muss erscheinen
```

**2. Demo-Datenbank bauen** — Kopie der echten ohne Haushaltsdaten (Katalog
und Crawl-Historie bleiben):

```bash
mkdir -p ~/picknick-demo/bons
cd ~/code/picknick_klon
.venv/bin/python - <<'EOF'
import sqlite3, os
ziel = os.path.expanduser("~/picknick-demo/demo.db")
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
durchgehend im Bild, und im Projekt `Picknick Agent` stünden echte Züge mit
echten Eingaben in der Liste — Haushaltsdaten in Pixeln. Ein frisches Projekt
enthält nur, was die Kamera sehen darf. (Der Projektname per
`PICKNICK_PHOENIX_PROJECT` ist geprüft — die Spans landen dort.)

```bash
cd ~/code/picknick_klon
PICKNICK_HOST=127.0.0.1 PICKNICK_PORT=8747 \
PICKNICK_DB=~/picknick-demo/demo.db PICKNICK_BON_DIR=~/picknick-demo/bons \
PICKNICK_PHOENIX_PROJECT="Picknick Demo Aufwaermen" \
.venv/bin/python -m picknick.web.app
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
d = sqlite3.connect(os.path.expanduser("~/picknick-demo/demo.db"))
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
PICKNICK_HOST=127.0.0.1 PICKNICK_PORT=8747 \
PICKNICK_DB=~/picknick-demo/demo.db PICKNICK_BON_DIR=~/picknick-demo/bons \
PICKNICK_PHOENIX_PROJECT="Picknick Demo" \
.venv/bin/python -m picknick.web.app
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
| Firefox 1 | `http://127.0.0.1:8747/chat`, dann `Ctrl+Shift+M` (Responsive-Modus), Größe **390 × 844** | links, 0/0, **520 × 1080** | `wmctrl -r "Picknick" -e 0,5120,146,520,1080` |
| Firefox 2 (neues Fenster, `Ctrl+N`) | `http://localhost:6006` → Projekt **Picknick Demo** → Tab **Spans** | rechts oben, 520/0, **1400 × 830** | `wmctrl -r "Phoenix" -e 0,5640,146,1400,830` |
| Terminal aus Schritt 6 | nvtop | rechts unten, 520/830, **1400 × 250** | `wmctrl -r "RTX 3090" -e 0,5640,976,1400,250` |

`wmctrl` matcht auf Titel-Teilstrings; die drei Fenster heißen „Chat —
Picknick", „Phoenix" und „RTX 3090" und sind damit eindeutig. Die GNOME-Leiste
oben (~30 px) bleibt im Bild — das ist in Ordnung, es ist ein echter Desktop.
Das Telefon-Fenster füllt so gut ein Viertel der Breite, Phoenix die
restlichen drei.

**Im Phoenix-Fenster vor dem Dreh prüfen:** oben rechts neben der
Zeitbereich-Wahl sitzt der **Streaming-Umschalter** (Puls-Symbol). Er steht ab
Werk auf „an", aber die Einstellung ist **im Browser gespeichert** — wer ihn je
ausgeschaltet hat, bekommt ein Fenster, das sich nie von selbst füllt.
Anschalten, Projekt `Picknick Demo` öffnen, Tab **Spans**: die Liste ist leer
und wartet.

**8. Aufräumen danach:** `Ctrl+C` für die Demo-Instanz (oder `kill <PID>` —
nie `pkill -f`), `rm -r ~/picknick-demo`, nvtop-Terminal schließen. Die
Wegwerf-Projekte `Picknick Demo*` können in Phoenix bleiben oder über die
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
| T+65 | **Ja** beim Spinat (auf „1200 g gebraucht — 3 × 0,54 kg" verweilen), dann runter zur Klopapier-Zeile: Cursor auf „Freitext", **Ja**. |
| T+85 | Oben **Korb**: einmal durchscrollen (gerechnete Mengen, Klopapier als Freitext), **Bestellung abschicken**. |
| T+100 | **Pick-Liste**: zwei Posten abhaken, bei einem **gab's nicht**. |
| T+115 | Ins Phoenix-Fenster: den `chat.turn`-Span anklicken → Trace-Ansicht. Die Span-Kette ruhig zeigen (chat.turn → plan.extract → catalog.search ×15 → plan.choose), einen `catalog.search`-RETRIEVER-Span aufklappen, bis die bewerteten Dokumente stehen, über `picknick.rejected = 0` verweilen. 5 s halten, Aufnahme stoppen. |

## Der Schnitt — Shots, Voice-Over, Untertitel

~67 s gesamt. VO wortwörtlich ablesen (Take C), Untertitel bleiben trotzdem —
LinkedIn spielt stumm. Wo Sprech- und Lesesprache auseinandergehen, weichen
sie bewusst voneinander ab.

| # | Zeit | Bild (aus Take) | Voice-Over (wortwörtlich) | Untertitel |
|---|---|---|---|---|
| 0 | 0:00–0:06 | **Take B:** Gesicht, ein Satz in die Kamera | `I built a grocery agent for my girlfriend and me — it runs on one RTX 3090 in my living room.` | `A grocery agent for two — on one RTX 3090 in my living room.` |
| 1 | 0:06–0:12 | Take A, T−10…0: tippen, **Fragen** | `One sentence, the way we say it at home: everything for lasagna — and toilet paper. Send.` | `One sentence, like at home (the app speaks German): "everything for lasagna — and toilet paper."` |
| 2 | 0:12–0:20 | Take A, T+0…2 **hart geschnitten auf** T+11…15: Spinner links, rechts schlagen die 16 Spans ein, nvtop auf 100 % | `Right side: Phoenix, tracing the agent live. Bottom: my 3090, at full power. No cloud. No API key.` | `Phoenix traces the agent live — on the 3090 under my TV. No cloud, no API key. (~29 s, cut)` |
| 3 | 0:20–0:34 | Take A, T+29…60: Antwort, Rezeptkarte, Alternativen, Portionen 3 → 6 mit sichtbarer Neurechnung | `It found a real, top-rated recipe. Cooking time, rating, five alternatives. Guests tonight? Servings to six — every quantity rescales.` | `A real top-rated recipe in ~100 ms — set servings to 6 and every quantity and pack count rescales.` |
| 4 | 0:34–0:41 | Take A, T+65…80: **Ja** beim Spinat, **Ja** beim Klopapier-Freitext | `Nothing enters the cart without a yes. Twelve hundred grams — that's three packs. Computed, not guessed.` | `Nothing enters the cart without a Yes. "1200 g needed → 3 packs" — computed, not guessed.` |
| 5 | 0:41–0:48 | Take A, T+85…95: Korb, Mengen, Klopapier-Zeile, **Bestellung abschicken** | `No catalog match for toilet paper — it stays on the list as free text. The cart is the order.` | `No catalog hit for toilet paper — it stays as free text instead of silently vanishing. The cart is the order.` |
| 6 | 0:48–0:54 | Take A, T+100…110: Pick-Liste, abhaken, **gab's nicht** | `In the store, we check things off. Wasn't there — that's an honest answer, too.` | `In the store: check items off. "Wasn't there" is an honest third state.` |
| 7 | 0:54–1:02 | Take A, T+115…: Trace-Ansicht, Zoom auf die Span-Kette, RETRIEVER-Dokumente, `picknick.rejected = 0` | `Every turn is one trace. The model can only pick from what the shop retrieved. Invented products are rejected — and counted.` | `Every turn is one trace. The model may only pick from retrieved documents — invented IDs are rejected and counted. rejected = 0.` |
| 8 | 1:02–1:07 | **Endcard** (Standbild) | `Open weights. One GPU. Measured on sixty-four dishes. Link below.` | *(Text steht auf der Endcard)* |

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
0:41  No catalog match for toilet paper — it stays on the list as free text.
      The cart is the order.
0:48  In the store, we check things off.
      Wasn't there — that's an honest answer, too.
0:54  Every turn is one trace. The model can only pick from what the
      shop retrieved. Invented products are rejected — and counted.
1:02  Open weights. One GPU. Measured on sixty-four dishes. Link below.
```

**Endcard** (Standbild, 5 s):

> *A grocery app for a two-person household where a 27B open model —
> quantized to fit a single NVIDIA RTX 3090 — turns "everything for lasagna,
> and toilet paper" into a real shopping list: fully traced in Arize Phoenix,
> evaluated across 64 dishes, zero cloud, zero API keys.*
>
> FastAPI · SQLite · vLLM (Qwen, open weights) · OpenTelemetry → Arize Phoenix
> Recipe eval **83 %** across 64 dishes (up from 76 %) · 1,150 tests · 76 checks
> Repo: `<Link>`

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
  Dashboards, richtiges Projekt (`Picknick Demo`). Zur Not lädt `F5` von Hand.
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
  mit frischem Projektnamen neu starten (`Picknick Demo 2`, …) — oder die
  alten Takes als ältere Zeilen unter dem neuen akzeptieren, der neueste
  steht oben.
