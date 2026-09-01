# Fallen beim Drehen — alle teuer bezahlt

Jede hat mindestens einen Take gekostet. Die Reihenfolge ist die, in der sie
zuschlagen.

## Der Prüfstand im Kamera-Take

`bereit.sh --pruefstand` legt ein gespeichertes Rezept an. Der Zug nimmt dann den
Gespeichert-Pfad: **66 ms, kein Modellaufruf, kein einziger Span.** Das Video ist
technisch fehlerfrei und inhaltlich leer — rechts kein Trace, unten keine GPU.

**Für den Kamera-Take immer `./bereit.sh` ohne Flag.** `recipe_item` muss 0 sein.

## Ein perfekter Durchlauf ohne Band

Xephyr mit `-resizeable` lässt die Schirmgrösse dem Fenster folgen, und der
Fenstermanager stutzt es auf 1850×1016. ffmpeg bricht in der ersten Sekunde ab
(`Capture area 1920x1080 ... outside the screen size`), das Drehbuch läuft 259 s
fehlerfrei durch und meldet „kein Schritt hat gewarnt".

**Der Prüfstand des Durchlaufs sagt nichts über die Aufnahme.** Nach jedem Take
`ls take_a.mkv` und `ffprobe` — beides, immer.

## Die Box war aus

`vllm-idle-stop` hält nach 10 min Leerlauf an. Der Kaltstart dauert seit dem
Container-Umstieg **5 min 35 s** (gemessen), das Drehbuch gibt nach 120 s auf.
Fünf Warnungen, ein Band für nichts.

`bereit.sh` weckt und wärmt inzwischen selbst. Wenn es „FEHLER: Box bedient
nicht" sagt, ist der Take vorbei, bevor er beginnt — nicht trotzdem starten.

## Das Band ist kürzer als der Durchlauf

Die Frist in `aufnahme.sh` ist fest. Wächst die Choreografie (mehr Vorschläge,
längere Liste), reicht sie nicht mehr, und **der Schluss fehlt** — also genau die
Einstellung, auf der die Pointe steht. Grosszügig ansetzen; das Ende wird
ohnehin weggeschnitten.

## Patch und Aufnahme in einem Hintergrundbefehl

Zweimal passiert: das Patch-Skript scheitert an einer Zusicherung, die Aufnahme
läuft trotzdem los und dreht vier Minuten mit dem **unveränderten** Drehbuch.

**Erst patchen, Ergebnis prüfen, dann drehen.** Zwei Schritte, von denen der
zweite auf dem ersten aufbaut, gehören nicht in dieselbe unbeaufsichtigte Kette.

## Zwei Zeitachsen

`zeitmarken.txt` zählt ab Skriptstart, das Band ab ffmpeg-Start. Dazwischen
liegen **2,08 s** (`aufnahme.sh` startet ffmpeg, dann `sleep 2`, dann das
Drehskript).

    Bandzeit = Ereignis-Epoch − `start:` aus ffmpeg.log

Nicht `ffmpeg_start.txt` — das ist der Zeitpunkt des `date`-Aufrufs, 58 ms vor
dem ersten Bild. Als Plausibilitätsprüfung taugt es, als Bezug nicht.

`handy.py` hat diesen Versatz sechs Takes lang nicht verrechnet. Folge: das
Suchfenster des ersten Tipps lag mitten in der Tippphase, und der Kringel
erschien beim Eintippen.

## Suchen statt wissen

`argmax` über Bildsprünge liefert **immer** einen Wert, auch im leeren Fenster —
gemessen Stärke 0,05 gegen 2,78 am wirklichen Ereignis. Ein Werkzeug, das nicht
„ich weiss es nicht" sagen kann, sagt irgendetwas, und das Ergebnis sieht
plausibel aus.

**Gesten kommen aus `gesten.jsonl`.** Wenn das Protokoll fehlt: neu drehen, nicht
schätzen. `handy.py` bricht deshalb ab, statt zurückzufallen.

## Klicken, während die Seite rollt

`ruhe()` gibt nach seiner Frist das letzte Bild zurück, auch wenn es noch
wandert. Für eine Suche richtig, für einen Klick falsch: „Alles übernehmen" wurde
gedrückt, während die Seite noch 4 Pixel je Bild lief.

**Vor jedem Klick, der auf einen Rollweg folgt, `steht()`.** Es wartet, bis das
Blatt wirklich steht, und warnt, wenn es nicht still wurde.

## Zwei Mechanismen für dieselbe Absicht

Die „zwei Klicks zurück" (damit die Zeile nicht unter der Chat-Leiste klebt) und
die spätere Zentrierung wollten dasselbe. Beide gleichzeitig: erst mittig bei
y=495, dann wieder bei y=723. Der gröbere gewann, weil er später kam.

**Wer einen Mechanismus ersetzt, entfernt den alten.**

## Pauschale Kürzungen treffen auch die Pointe

`TEMPO = 0.10` machte aus 2,5 s sichtbarer Wirkung 0,25 s. „Gab's nicht" war im
Video, aber niemand konnte es sehen.

**`zeigen()` geht am Tempo vorbei.** Wer kürzt, muss benennen, was nicht gekürzt
wird.

## Das Rad dreht, wo der Zeiger steht

Nach einem Klick in die Adressleiste steht er ausserhalb des Blatts. Dann rollt
nichts, das Bild ändert sich nicht, und jede „bis ans Ende"-Schleife hält das für
„am Ende". Dreimal hat das den Bestellknopf gekostet, der achthundert Pixel
tiefer stand.

`m.rollen(..., x=260, y=600)` — die Position immer mitgeben.

## Die Sammelknöpfe stehen nicht am Seitenende

Unter der Vorschlagsliste folgt der **Rezeptentwurf**, mit sechs Portionen lang
genug, dass zehn Radklicks rückwärts ihn nicht durchmessen. Dann steht kein „Ja"
im Bild, und der Take meldet „Zeile nicht gefunden".

Rückwärts wird in grösseren Schritten gesucht, aber **nicht beliebig gross**: bei
der Suche nach den Sammelknöpfen rollt es nach oben, und ein zu grosser Schritt
schiebt sie unten aus dem Bild — dann ist der Take kaputt statt kürzer.

## Farben stehen im Blatt, nicht im Drehbuch

`finde.ton()` liest sie aus `stil.css`. Zweimal stand hier eine veraltete Kopie
(nach dem Farbwechsel und nach dem Dunkelmodus), und beide Male sah es aus wie
ein kaputter Bilderkenner.

## Die Ende-Taste springt

Sie bringt in einem Schlag ans Blattende und ist damit schneller als jedes Rad.
In einem **stummen** Hochkant-Clip ist ein Sprung aber nicht erklärbar: das Bild
ist plötzlich woanders, ohne dass eine Hand zu sehen wäre.

Zweiter Grund, und der ist messbar: die Taste bewegt die Seite ohne Radklick.
`handy.py` sperrt deshalb das Fenster um jeden Tastendruck — sonst schlüge es
den Sprung dem benachbarten Radschub zu, und dessen Finger zöge quer durchs
Bild. Und die Radklicks danach treffen ein Blatt, das schon unten steht. In
einem Take waren das **sechs Schübe ohne Weg und drei hinter der Sperre**.

In der langen Fassung mit sichtbarem Zeiger ist die Taste in Ordnung.

## Die Abbruchbedingung, die nie griff

`ans_ende()` rollt und vergleicht danach zwei Bilder: sind sie gleich, ist das
Blatt unten. Die Pause dazwischen stand auf 0,3 s — und lief durch
`TEMPO = 0,10`, war also **0,03 s**. Der Schnappschuss fiel mitten in die
Rollanimation, zwei Bilder waren nie gleich, die Schleife fuhr jedes Mal ihre
volle Rundenzahl.

Doppelt so grosse Wischer änderten daran nichts (20,4 s gegen 20,1 s) — die
Wischergrösse war nie das Problem, die verschluckte Pause war es.

**Pausen, die keine Lesepausen sind, gehen am TEMPO vorbei.** Die hier ist die
Zeit, die der Browser zum Ausrollen braucht; die kann man nicht kürzen, nur
unbrauchbar machen.

## Zwei Marken, die gleich anfangen

`handy.py` löst Segmentgrenzen über den ANFANG eines Markennamens auf. Zwei
Marken „Sammelknöpfe bei …" an verschiedenen Stellen des Laufs sind deshalb
eine stille Falle — es gewinnt die erste. Namen eindeutig halten.
