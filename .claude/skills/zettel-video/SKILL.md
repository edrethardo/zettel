---
name: zettel-video
description: Use when a Zettel demo video is to be shot or re-cut — "nimm die Demo neu auf", "mach ein Video für LinkedIn", "dreh das nochmal", "der Clip soll kürzer sein", or when a take came out wrong (kein Band, falsche Geste, zu lang).
version: 2
---

# Ein Zettel-Video drehen

Zwei Fassungen, ein Werkzeugkasten in `~/picknick-video/`. Beide werden von einem
Skript gefahren, nicht von Hand: die Aufnahme läuft Minuten am Stück, und ein
Sprungschnitt innerhalb einer Einstellung ist verboten.

| | lange Fassung | Hochkant-Clip |
|---|---|---|
| wofür | Contest, Repo | LinkedIn |
| Bild | zwei Fenster + GPU-Streifen | nur die App-Spalte, im Handy-Rahmen |
| Zeiger | **sichtbar** (macht es menschlich) | **nicht aufgenommen**, Berührungen werden gemalt |
| Drehbuch | `dreh.py` | `dreh_handy.py` |
| Schnitt | `schnitt.py` | `handy.py` |
| Doku | `docs/contest/VIDEO.md` | dieselbe Datei, Abschnitt „Hochkant-Clip" |

## Der Ablauf

**1. Bühne aufbauen.**

    cd ~/picknick-video && ./buehne.sh

Danach die Fensternummern festhalten — `bereit.sh` liest sie aus `fenster.txt`:

    export DISPLAY=:78
    APP=$(xdotool search --onlyvisible --name "Chat — Zettel" | head -1)
    PHX=$(xdotool search --onlyvisible --name "Zettel Demo - Projects" | head -1)
    echo "APP=$APP PHX=$PHX" > ~/picknick-video/fenster.txt

Prüfen: `DISPLAY=:78 xdotool getdisplaygeometry` muss **1920 1080** sagen.

**2. Vor-Take-Zustand.**

    ./bereit.sh

Das weckt die Box, wartet bis sie wirklich bedient, schickt einen Wegwerf-Zug zum
Warmlaufen, leert Chat und Bestellungen, stellt den Katalog-Zeitstempel auf heute
(sonst steht „Preise sind N Tage alt" über jedem Bild) und fährt den Browser
zurück auf `/chat`.

**`--pruefstand` NIEMALS für einen Kamera-Take** — siehe FALLEN.md.

**3. Drehen.**

    # lange Fassung
    bash aufnahme.sh 310

    # Hochkant, mit Gestenprotokoll
    MAUS=0 SKRIPT=dreh_handy.py STAMM=take_2026-09-02 bash aufnahme.sh 75

Die Länge ist eine feste ffmpeg-Frist. Sie muss **über** der Laufzeit des
Drehbuchs liegen; ist sie zu knapp, fehlt der Schluss und der Take ist hin.

**4. Das Urteil lesen — vor allem anderen.**

    *** kein Schritt hat gewarnt — jede Wirkung ist in der Datenbank belegt

Steht dort etwas anderes, ist der Take unbrauchbar. Die Untertitel dürfen nur
benutzt werden, wenn kein Schritt gewarnt hat: jede Aussage im Text ist eine
Wirkung, die das Drehbuch in der Datenbank nachgeprüft hat.

Und getrennt davon prüfen, dass es **ein Band gibt** — ein fehlerfreier Durchlauf
sagt nichts über die Aufnahme.

**5. Schneiden.**

    python3 schnitt.py                                   # lang
    .venv/bin/python ~/picknick-video/handy.py <STAMM>   # hochkant

`handy.py` druckt am Ende immer eine Bilanz. Sie gehört gelesen:

    10 Tipps protokolliert, 10 gezeichnet, 0 ausserhalb der Segmente
    18 Radschübe protokolliert, 8 gezeichnet, 6 ohne Weg, 4 hinter Tastaturbildlauf

**6. Bühne abräumen** — über die gemerkten PIDs, **nie** `pkill -f`: auf `:1`
läuft Aarons echte Sitzung.

    while read -r name pid; do kill "$pid" 2>/dev/null; done < buehne.pids

## Wie die Gesten in den Hochkant-Clip kommen

Nicht durch Bilderkennung. Das Drehbuch schreibt mit, was es tut
(`protokoll.py` → `gesten.jsonl`), `handy.py` liest das. Die Bildmessung ist
geblieben, aber sie **prüft** nur noch: die Zeitbasis am ersten Tipp, und wie
weit ein Radklick die Seite wirklich bewegt hat.

* **Zeit** kommt aus dem Protokoll — nur das Drehskript weiss, wann es klickt.
* **Weg** kommt aus dem Bild — nur das Bild weiss, wie weit die Seite rutscht.
* **Ort** kommt aus dem Aufruf des Drehskripts. Nichts wird geschätzt.

`SEGMENTE` in `handy.py` steht als **Namen** von Zeitmarken, nie als Zahlen.
Zahlen aus `zeitmarken.txt` abzuschreiben ist dreimal schiefgegangen.

Wer eine neue Geste in `menschlich.py` baut, meldet sie über `protokoll.notiere`.
Das Zählwerk in `_xdo` fängt Vergessen ab — der Take scheitert dann, nicht das
Video.

## Ein Weg nach unten, nicht ans Ende und zurück

Wer eine Stelle weit unten sucht, wischt **nach unten, bis sie im Bild ist** —
und hört dann auf. Nicht ans Blattende fahren und rückwärts suchen.

Gemessen am 2026-09-01, derselbe Ablauf:

| | ans Ende und zurück | ein Weg nach unten |
|---|---|---|
| letzte Zeile **und** Sammelknöpfe | 30,6 s | **14,1 s** |
| zweiter Griff auf die Knöpfe | 12,7 s | **0,3 s** |
| Korb → Bestellknopf | 10,9 s | **4,5 s** |
| Radschübe mit Finger | 7 von 16 | **11 von 11** |

Es ist nicht nur schneller. **Ein Wischer, der aufhört, wenn das Ziel da ist,
sieht aus wie eine Hand; einer, der blind bis zum Anschlag zieht, nicht.** Und
was am Anschlag noch klickt, bewegt nichts mehr — das sind die Schübe, die in
der Bilanz als „ohne Weg" auftauchen und im Bild fehlen.

Anker nutzen, wo es einen gibt: die Sammelknöpfe stehen direkt unter der
letzten Vorschlagszeile. Wer sie findet, hat die Zeile schon — sie ist die
unterste Pille darüber. Eine Suche statt zwei.

## Länge

Der Clip ist so lang wie der Ablauf. Gekürzt wird an drei Stellen, nie an der
Wartezeit auf das Modell:

* `TEMPO` / `ZEIGER_TEMPO` in `dreh_handy.py` — Lesepausen und Zeigerwege.
* `ruhe(grenze)` — wie lange auf Stillstand gewartet wird.
* Suchschritte der Rückwärtssuchen.

**Was das Ergebnis zeigt, wird ausgenommen.** `zeigen()` geht am `TEMPO` vorbei;
sonst wird aus 2,5 s Wirkung 0,25 s und die Pointe blitzt vorbei. Wer kürzt, muss
sagen, was nicht gekürzt wird.

Die Modelllaufzeit schwankt (gemessen 6,0 bis 11,1 s) und der Clip mit ihr. Wer
eine harte Obergrenze braucht, muss einen Schritt aus der Choreografie nehmen —
nicht warten, bis ein Take zufällig schnell ist.

## Bevor etwas schiefgeht

`FALLEN.md` in diesem Ordner: jede Falle, die schon einmal einen Take gekostet
hat, mit Ursache und Gegenmittel. **Vor dem ersten Dreh eines Tages einmal lesen**
— sie sind alle teuer bezahlt.
