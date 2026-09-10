# Betrieb

Wie dieser Shop bei mir läuft: zwei systemd-**User**-Units, ein nächtlicher
Katalog-Lauf, Sicherungen. Nichts davon braucht man, um das Projekt zu lesen
oder auszuprobieren ([`GETTING-STARTED.md`](GETTING-STARTED.md)) — es ist die
Betriebsanleitung für eine Maschine, die das dauerhaft tut.

Zwei systemd-**User**-Units unter `deploy/`, kein root:

| Unit | was |
|---|---|
| `zettel.service` | der Web-Prozess |
| `zettel-crawl.service` | ein Katalog-Lauf plus Sicherung, einmalig |
| `zettel-crawl.timer` | startet den Lauf nachts um 03:30 |

### Installieren

```bash
mkdir -p ~/.config/systemd/user
# Verweise statt Kopien: die Unit im Repo bleibt die Quelle der Wahrheit, und
# eine Änderung an ihr ist nach `daemon-reload` sofort die, die läuft. Eine
# Kopie wäre eine zweite Fassung, die still veraltet.
ln -sf "$PWD"/deploy/zettel.service       ~/.config/systemd/user/
ln -sf "$PWD"/deploy/zettel-crawl.service ~/.config/systemd/user/
ln -sf "$PWD"/deploy/zettel-crawl.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now zettel.service
systemctl --user enable --now zettel-crawl.timer
```

### Etwas vorübergehend anders stellen

Was nur für eine Weile gilt, gehört **nicht** in die Unit, sondern daneben —
sonst steht der Ausnahmezustand irgendwann als Dauerzustand im Repo:

```bash
mkdir -p ~/.config/systemd/user/zettel.service.d
cat > ~/.config/systemd/user/zettel.service.d/10-beispiel.conf <<'EOF'
[Service]
Environment=ZETTEL_LLM_ENDPOINT=http://127.0.0.1:9/v1
Environment=ZETTEL_WAKE_CMD=/bin/true
EOF
systemctl --user daemon-reload && systemctl --user restart zettel
```

`systemctl --user status zettel` zeigt jede solche Datei unter **Drop-In** an
— sie kann also nicht in Vergessenheit geraten, solange jemand hinsieht.
Genau dieses Paar hat am 31.08. den Shop von der Modellbox ferngehalten,
während eine andere Sitzung sie belegte: ein toter Endpunkt und ein
Weckbefehl, der nichts tut. Wieder weg mit `rm` und `daemon-reload`.

`zettel-crawl.service` wird **nicht** aktiviert — sie hat absichtlich keinen
`[Install]`-Abschnitt. Der Timer startet sie; von Hand geht
`systemctl --user start zettel-crawl.service`.

Nachsehen:

```bash
systemctl --user status zettel.service
systemctl --user list-timers zettel-crawl.timer
journalctl --user -u zettel-crawl.service -n 50
```

Im Shop selbst steht dasselbe unter **`/status`**: letzter Lauf, Produktzahl
und vor allem die verworfenen Läufe mit Begründung.

### Ein Befehl, den du selbst entscheiden musst

```bash
loginctl enable-linger <benutzer>
```

Gemessen am 2026-08-28: `loginctl show-user <benutzer> -p Linger` sagt ohne
diesen Befehl `Linger=no`.
Ohne Linger beendet systemd alle User-Dienste beim Abmelden — der Shop wäre
dann nur da, solange eine grafische Sitzung offen ist, und der nächtliche Crawl
liefe gar nicht. Der Befehl ist eine Änderung an der Maschine und nicht am
Projekt; er steht deshalb hier und wird nicht mitinstalliert.

**Die Gefahr daran ist entschärft** (31.08.): mit Linger startet der Shop
schon vor der Anmeldung, und dann steht tailscaled unter Umständen noch
nicht. Er sucht seine Bindeadressen nur einmal und lauschte dann allein auf
loopback — grüner Dienst, unerreichbares Telefon. `deploy/warte-auf-tailnet.sh`
wartet als `ExecStartPre` bis zu 60 s darauf und lässt danach trotzdem
starten. Die Entscheidung bleibt deine; sie kostet jetzt nur nichts mehr.

### Was der nächtliche Lauf tut

`zettel-crawl.service` ruft `python -m zettel.scrapers.nachtlauf` auf:

1. **Crawlen** über die Begriffsliste in `zettel/scrapers/begriffe.py` —
   170 Begriffe, von „milch" über „passierte tomaten" bis „spuelmaschinentabs".
   Knuspr hat keinen Endpunkt für den ganzen Katalog, nur die Suche; der
   Katalog dieses Shops ist damit genau so breit wie diese Liste. Sie lässt
   sich ohne Neuausrollen ersetzen: `ZETTEL_BEGRIFFE` nimmt einen Dateipfad
   (ein Begriff je Zeile) oder eine kommagetrennte Aufzählung.
   Geschätzt 15–25 Minuten, beim ersten Mal länger, weil die Bilder einzeln
   geladen werden. Zum Schluss werden die Mengenangaben nachgezogen: der Crawl
   schreibt nur die Zeilen richtig, die er angefasst hat, alle anderen sagen
   sonst weiter „0,25 g" statt „250 g". Das kostet nichts und lässt sich
   beliebig oft wiederholen.
2. **Miniaturen ableiten** zu den frisch geholten Fotos — die Kacheln der
   Suche zeigen ein verkleinertes Bild und nicht das Original, und umgerechnet
   wird das hier und nicht im Web-Prozess, wo eine Seite auf dem Telefon
   darauf warten müsste. Auch das läuft, wenn der Crawl gescheitert ist:
   heruntergeladen sind die Bilder trotzdem.
3. **Sichern** der Datenbank mit `VACUUM INTO` in ein Verzeichnis
   `sicherungen` **neben der Datenbank** (mit der Vorgabe also
   `data/sicherungen/`), sieben Stände, ältere werden abgeräumt. `VACUUM INTO` und nicht `cp`: die
   Datenbank läuft im WAL-Modus, eine Kopie mitten in einem Schreibvorgang kann
   kaputt sein und das fällt erst beim Zurückspielen auf. Gesichert wird auch
   dann, wenn der Crawl scheitert — schützenswert sind Bestellungen, Rezepte
   und Chatverläufe, der Katalog liesse sich morgen neu holen.

Ein Lauf, der den Katalog nicht aktualisiert hat, endet mit Rückgabewert 1 und
steht damit als `failed` in `systemctl --user status`. Das gilt auch für einen
absichtlich verworfenen Lauf: „gewollt" heisst nicht „unauffällig".

Von Hand, ohne 20 Minuten zu warten:

```bash
.venv/bin/python -m zettel.scrapers.nachtlauf --begriff milch --keine-sicherung
```

Zurückspielen ist ein Kopieren, mehr nicht:

```bash
systemctl --user stop zettel.service
cp data/sicherungen/zettel-2026-08-30T03-30-00.db data/picknick.db
systemctl --user start zettel.service
```

### Der Timer ist `Persistent=true`

Der Laptop schläft nachts zugeklappt. Ohne `Persistent=true` fiele jeder Lauf
ersatzlos aus, dessen Zeitpunkt in eine geschlossene Nacht fällt — der Katalog
würde nie aktualisiert, und nichts davon sähe nach einem Fehler aus. Mit
`Persistent=true` merkt sich systemd den letzten Start auf der Platte und holt
einen verpassten Lauf nach, sobald die Maschine wieder da ist. Der Lauf fehlt
also nicht, er kommt spät.

### Was am Betrieb schwach ist, und zwar bewusst

- **Der Shop schläft mit dem Laptop.** Ist der Laptop aus oder zugeklappt,
  ist der Shop weg — auch für sie, auch mitten im Einkaufen. Das ist bekannt
  und akzeptiert (Spec 12); ein Umzug auf einen Dauerläufer ist später ein
  reiner Ortswechsel von Prozess und Datei.
- **Die Tailscale-Adresse wird beim Start erfragt, nicht konfiguriert**
  (`eigene_tailnet_adresse()` in `zettel/web/app.py`) — ein
  Umzug oder eine neue Adresse brauchen nichts als einen Neustart. Die
  Kehrseite: startet der Dienst, bevor `tailscaled` steht, findet er keine
  Tailnet-Adresse und lauscht nur auf loopback, bis er neu startet — vorher
  scheiterte der Start und `Restart=on-failure` versuchte es erneut. Praktisch
  tritt das nicht auf, solange die User-Dienste erst mit der Anmeldung starten
  (`Linger=no`). `ZETTEL_HOST` erzwingt eine feste Adresse.
- **Beim Hochfahren steht `tailscaled` unter Umständen später als der Shop.**
  Ein User-Manager kann darauf nicht ordnen, deshalb versucht
  `zettel.service` es mit `Restart=on-failure` alle 10 Sekunden erneut,
  statt so zu tun, als gäbe es das Rennen nicht.
- **Die Sicherungen liegen neben der Datenbank**, auf derselben Platte. Sie
  helfen gegen einen kaputten Crawl-Lauf und gegen einen Fehlgriff von Hand —
  nicht gegen einen Plattenschaden.
