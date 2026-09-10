# Zettel — die Anleitung

Für alle im Haushalt, die damit einkaufen. Zettel ist der gemeinsame Einkaufszettel:
**sie** legt in den Korb, **er** kauft im Laden und hakt ab. Dazwischen sitzt
ein Chat, der aus einem Satz eine Liste macht — und nichts davon landet im
Korb, ohne dass jemand „Ja" tippt.

Der Shop läuft im Tailnet unter `http://<Adresse>:8730`. Es gibt **kein
Passwort**: wer im Tailnet ist, ist drin. Es wird **nie** eine Bestellung an
einen echten Händler geschickt — der Korb ist der Zettel, mehr nicht.

## Aufs Handy legen

Einmal im Browser öffnen, dann **„Zum Startbildschirm hinzufügen"** (Android:
Menü des Browsers; iPhone: Teilen-Symbol). Danach liegt Zettel als App mit
gelbem Zettel-Symbol neben den anderen und geht ohne Adressleiste auf —
im Laden ist das der Unterschied zwischen einer App und einem Lesezeichen.

## Die fünf Reiter

Unten am Bildschirm, auf jeder Seite gleich:

| Reiter | Wofür |
|---|---|
| **Katalog** | suchen und mit „+" einlegen, ohne Chat |
| **Chat** | einen Satz sagen, Vorschläge bekommen, je Zeile Ja/Nein |
| **Korb** *(mit Zahl)* | was drin ist, Mengen, Laden, „Bestellung abschicken" |
| **Pick-Liste** | die Einkaufsliste im Laden, zum Abhaken |
| **Mehr** | Rezepte · Bestellungen · Bons · „Wer bin ich?" · Status |

**Wer bin ich?** (unter *Mehr*) stellt nur ein, womit die Startseite
aufmacht: *sie* beim Katalog, *er* bei der Pick-Liste. Das ist keine
Anmeldung und schützt nichts.

## Bestellen

### Über den Katalog

Suchen, Kategorien durchstöbern, „+" an der Kachel legt das Produkt in den
Korb — ohne Seitenwechsel, die Korbzahl unten springt mit. Die Suche kennt
**Wortanfänge**: „milch" findet „Milch …", aber nicht „Landmilch". Fünf
Alltagswörter sind nachgetragen (etwa „Klopapier" → „Toilettenpapier");
alles andere schreibt man so, wie es auf der Packung steht.

### Über den Chat

Tippe, was du brauchst, so wie du es sagen würdest — *„alles für Lasagne und
Klopapier"* — und tippe **Fragen**. Der leere Chat zeigt drei Beispiele zum
Antippen.

Was dann passiert:

1. Erkennt der Shop ein **Gericht**, holt er das bestbewertete Rezept von
   Chefkoch und zeigt eine **Rezeptkarte**: Zeit, Schwierigkeit, Bewertung,
   fünf Alternativen aus derselben Suche (antippen wechselt das Rezept, ohne
   neu zu suchen), und ein Feld **„Für wie viele Portionen?"** — „Mengen neu
   rechnen" rechnet jede Zutat um. Was schon im Korb liegt, bleibt dabei
   liegen.
2. Je Zutat kommt **eine Vorschlagszeile**: Foto, Produkt, die benötigte
   Menge aus dem Rezept und daraus die Packungszahl (*„1200 g gebraucht —
   3 × 0,54 kg"*), darunter der Suchbegriff, über den das Produkt gefunden
   wurde. Steht dort *„Nur über den allgemeinen Begriff … gefunden — sieh
   die Alternativen an"*, war der Treffer wackelig.
3. Was der Katalog nicht kennt, steht als **Freitext** mit gestricheltem
   Platzhalter dabei (*„süße Sahne — im Katalog nicht gefunden"*). Es
   verschwindet nicht, es kommt als eigene Zeile in den Korb.

**Nichts davon ist im Korb**, bis du entscheidest:

| Knopf | Wirkung |
|---|---|
| **Ja** | legt die Zeile in den Korb; die Zeile sagt *„im Korb"* |
| **Nein** | klappt die **Alternativen** aus derselben Suche auf — eine antippen legt sie statt des Vorschlags ein; *„Nichts davon"* macht die Zutat zum Freitext |
| **rückgängig** | nimmt ein Ja oder Nein zurück auf *offen* — nicht ins Gegenteil |
| **Alles übernehmen** / **Alles verwerfen** | entscheidet über alles, was noch **offen** ist; von Hand Entschiedenes bleibt |
| **Doch nicht alles** | nimmt genau diesen Sammelschritt zurück |
| **Neu suchen** | denselben Satz noch einmal, ohne Gedächtnis — was dabei herauskommt, gilt ab dann |
| **Verlauf leeren** | der Chat fängt leer an, der Korb bleibt |

Bei einem **Oberbegriff** („Aufschnitt", „Käse") fragt der Chat erst nach
der **Sorte** — ankreuzen, weiter, dann kommen die Produkte.

**Der Rezeptentwurf.** Unter den Zeilen steht *„Rezeptentwurf „Lasagne" —
14 von 14 Zutaten im Rezept"*. Beim Abschicken der Bestellung wird daraus
ein Rezept in der Sammlung — mit dem Namen, den du dort einträgst („Name
merken"), und nur mit den Zutaten, die du drin lässt. „Daraus wird kein
Rezept" lässt es bleiben, „Doch ein Rezept daraus" holt es zurück. Der
nächste Satz zum selben Gericht antwortet dann **aus der Sammlung, ohne
Modell** — in Sekundenbruchteilen.

### Wenn das Modell schläft

Das Modell läuft auf einem eigenen Rechner, der nach zwei Stunden Ruhe
schlafen geht. Steht im Chat **„Modell wacht auf … noch ~90 s"**, hat der
Shop ihn gerade geweckt und fragt von selbst weiter, bis die Antwort da ist
— nichts anfassen, nichts noch einmal schicken. Solange ist nur der Chat
betroffen: Katalog, Korb, Pick-Liste und gespeicherte Rezepte gehen weiter.
„Nicht erreichbar" steht erst da, wenn ein Weckversuch wirklich gescheitert
ist.

## Der Korb

Ein gemeinsamer Korb — was hier steht, sieht auch der andere.

* **−  1  +** ändert die Menge; unter 1 fliegt die Zeile raus, mit Rückweg.
* **Laden egal | Rewe | Lidl** je Zeile: wo es gekauft werden soll. Die
  Pick-Liste gruppiert danach.
* **×** löscht die Zeile — und bietet sofort *„wiederherstellen"* an.
* **„Etwas, das der Katalog nicht hat…" → Dazu** legt eine Freitextzeile
  ein.
* Die Summe ist eine **Schätzung**: die Preise kommen aus einem nächtlichen
  Katalog-Lauf (Knuspr, nicht Rewe), Freitextzeilen haben keinen Preis
  („1 Posten ohne Preis"). Steht *„Preise sind N Tage alt"* über der Seite,
  ist der Lauf ausgeblieben.
* **Bestellung abschicken** macht aus dem Korb eine Bestellung, die auf der
  Pick-Liste steht. Der Chatverlauf wandert mit ihr; der nächste Korb fängt
  leer an.

## Einkaufen mit der Pick-Liste

Die Pick-Liste öffnet die **älteste offene Bestellung** („Einkauf Nr. 1 —
Noch 13 zu holen"), gruppiert nach Laden: *Egal wo*, dann Rewe, dann Lidl.
Jede Zeile zeigt, was das Rezept braucht (*„1200 g gebraucht — dafür 3 ×
0,54 kg"*).

| Geste | Bedeutung |
|---|---|
| **Kästchen** antippen | gepickt — die Zeile wird durchgestrichen |
| **gab's nicht** | das Regal war leer. Zählt als erledigt, ohne so zu tun, als wäre es gekauft; **zurück** nimmt es zurück |
| noch einmal antippen | wieder offen |

Ohne Empfang springt ein Tipp zurück — dann einfach noch einmal tippen,
sobald das Netz wieder da ist. Weitere offene Bestellungen stehen unten auf
der Seite; jede hat ihre eigene Liste.

**Pick-Liste löschen** steht ganz unten auf der Seite: eine Bestellung, die
nicht mehr gebraucht wird, verschwindet samt Posten und Chatverlauf — nach
einer Rückfrage, die sagt, was dranhängt. Der Warenkorb ist davon nicht
betroffen, und was beim Abschicken als Entscheidung gezählt wurde, bleibt
gezählt.

Unter **Mehr → Bestellungen** steht, was abgeschickt wurde, mit dem Stand
(„15 von 15 noch zu holen"). **„Daraus ein Rezept machen"** an einer
erledigten Bestellung legt sie als Rezept in die Sammlung.

## Wochenplan

Unter **Mehr → Wochenplan**. Oben vier Zahlen — Tage, Personen, höchstens
Minuten am Herd, Budget — und ein Satz: was noch da ist („500 g Kartoffeln,
6 Eier, Nudeln"). **Plan anlegen**, dann **Woche planen**: das Modell
belegt die Tage mit Gerichten aus deinen Rezepten und aus dem, was du im
Chat schon einmal gefragt hast. Es erfindet nichts; zu jedem Tag steht ein
Satz, warum.

* **Oder ein Satz.** Über der Maske steht seit dem 10.09. ein Feld: „eine
  Mahlzeit pro Tag, 700 Kalorien, viel Protein, Kartoffeln, Eier und Nudeln
  sind da" — **Verstehen und planen** liest den Satz in die Felder, legt den
  Plan an und belegt die Tage in einem Zug. Jede Zahl, die das Modell
  einträgt, muss im Satz stehen; was es dazuerfindet, wird verworfen und
  gezählt, und die Seite sagt, wie viele. Der Satz steht danach über dem Plan,
  damit du vergleichen kannst, was verstanden wurde. Eine Mahlzeit je Tag
  ist die Bauart — „drei Mahlzeiten" nimmt er nicht an und sagt es.
* **Ja / Nein** an jedem Tag. „Nein" heisst: nicht das — **Offene Tage neu
  planen** belegt nur diesen Tag neu, die anderen bleiben. **auswärts** für
  Tage ohne Kochen. Ein Tag lässt sich auch von Hand belegen (Liste am Tag,
  Portionen daneben, **Setzen**).
* **Was noch da ist**: gilt nur für diesen Plan. Was du hinschreibst, zählt
  sofort; was der Bon der letzten Tage vorschlägt („noch da?"), zählt erst
  nach deinem Ja. **Was sagt der Bon?** fragt die bestätigten Käufe der
  letzten sieben Tage ab.
* **Einkaufsliste**: alle Zutaten der Woche zusammengezählt, abzüglich
  Bestand — gedeckte Zeilen sind durchgestrichen, „noch 300 g" steht dran,
  darunter Vorschau-Preis, Budget und wie viele Zutaten nur an einem Tag
  gebraucht werden. **Einkaufsliste in den Korb** legt sie in den Korb;
  ab da ist alles wie beim Bestellen.

## Rezepte

**Mehr → Rezepte** ist die Sammlung: was der Chat beim Abschicken angelegt
hat, was aus Bestellungen gemacht wurde, und was du selbst anlegst. In einem
Rezept lassen sich Name, Portionen und Zutaten ändern (Portionen ändern
rechnet alle Mengen mit), Zutaten mit dem Katalog verknüpfen, und **„Alles
in den Warenkorb"** legt es komplett ein — mit Bericht, was gefunden wurde
und was als Freitext geht. Löschen fragt zweimal; es ist der teuerste Knopf
im Shop.

## Kassenbons

**Mehr → Bons**: ein Foto oder PDF des Kassenbons hochladen. Der Shop liest
die Zeilen aus (dafür muss die Texterkennung auf dem Rechner installiert
sein — sonst sagt die Seite das) und ordnet sie Produkten zu; jede Zeile
wird bestätigt oder mit der Suche korrigiert. So bekommen die Käufe echte
Preise und der Katalog echte Packungen.

## Status

**Mehr → Status** ist die Wartungsseite: ob die Spans nach Phoenix gehen,
wie viele Vorschläge behalten/verworfen/offen sind (das sind die Labels,
aus denen der Agent bewertet wird), welches Modell antwortet, wie alt der
Katalog ist und welche nächtlichen Läufe verworfen wurden — mit Grund.

## Wenn etwas nicht stimmt

* **Preis falsch** — Richtwert aus dem Knuspr-Katalog, nicht der Rewe-Preis.
* **Produkt nicht gefunden** — Wortanfang tippen, wie es auf der Packung
  steht; sonst Freitext.
* **Falscher Vorschlag im Chat** — „Nein" und Alternativen ansehen; die
  Entscheidung ist ein Label und hilft beim nächsten Mal wirklich weiter.
* **Chat sagt „nicht erreichbar"** — der Modellrechner ist nicht
  aufgewacht. Alles außer dem Chat geht weiter; gespeicherte Rezepte sogar
  im Chat.
