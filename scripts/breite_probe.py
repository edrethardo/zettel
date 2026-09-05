"""Der ganze Rezeptweg, über 60+ Gerichte, bis auf die Einkaufsliste (WB-380).

KEIN Test. Diese Probe geht ins Netz (Chefkoch) und an die echte vLLM-Box.
Sie beantwortet die Frage, die bisher niemand gestellt hat: **wie trägt der
Weg als Ganzes** — Satz -> Zutat -> Suchbegriff -> Produkt -> Korbposten ->
Einkaufsliste — und **an welchem Glied gehen die Mengen verloren?**

    .venv/bin/python scripts/breite_probe.py --messen --db kopie.db
    .venv/bin/python scripts/breite_probe.py --messen --db kopie.db \
        --gerichte 6 --parallel 3          # kurzer Probelauf
    .venv/bin/python scripts/breite_probe.py --achsen   # nur die Liste zeigen

Mit `--trace` gehen die Spans der Züge nach Phoenix — für einen Eval-Lauf in
ein EIGENES Projekt, damit das Alltagsprojekt sauber bleibt (WB-393/395):

    ZETTEL_PHOENIX_PROJECT="Zettel Eval Qwen" \
    .venv/bin/python scripts/breite_probe.py --messen --db kopie.db --trace

Fehlt Phoenix, scheitert die Probe daran nicht: die Einrichtung wirft nie,
und der Export läuft in einem Hintergrund-Thread (`zettel.obs.otel`).

`--db` ist die KOPIE, aus der die Probe ihre Arbeitskopien zieht
(`sqlite3 data/picknick.db "VACUUM INTO 'kopie.db'"`). Sie wird gelesen und —
in Phase A — um Chefkoch-Rezepte ergänzt; die echte Datei fasst die Probe
nie an. **Jedes Gericht bekommt seine EIGENE Arbeitskopie**, und das ist
keine Vorsicht, sondern eine Messvoraussetzung: der Warenkorb ist im Schema
genau einer, und 63 Gerichte in denselben Korb legten Mengen über Gerichte
hinweg zusammen (`orders.korb.einlegen` addiert je Produkt). Die Zahl
„Korbposten mit Menge" wäre danach nicht mehr die eines Gerichts.

## Zwei Phasen

**Phase A — vorwärmen, seriell, höflich.** Jedes Gericht wird EINMAL bei
Chefkoch geholt (`gerichte.lauf.hole_eines` mit `chefkoch.PAUSE_S`), genau wie
das Kommando von Hand es tut. Danach steht in `dish` ein Ergebnis: ein
Rezept, ein „kennt Chefkoch nicht" (`leer`) oder eine Störung. Die Messung
läuft anschliessend gegen einen warmen Speicher — **sie misst also den
Normalfall des Shops und nicht den allerersten Satz zu einem Gericht.** Der
Unterschied ist benannt und nicht versteckt: beim allerersten Satz holt der
Chat im Request (WB-367), das kostet ~100 ms und ändert am Rest nichts.

**Phase B — messen, parallel.** Je Gericht: eine eigene Arbeitskopie, ein
`Chat.turn()`, dann **„Ja" auf JEDE Vorschlagszeile** (`alle_entscheiden`),
dann `korb.abschicken()` und `pick.nach_laden()` — die Einkaufsliste, wie sie
im Laden auf dem Telefon steht. Erst dort endet die Kette, und erst dort
lässt sich sagen, ob eine Menge angekommen ist.

Das „Ja auf alles" ist die günstigste Annahme für die Mengen und wird auch so
gelesen: wer Zeilen wegtippt, bekommt weniger, nie mehr. Eine Quote, die
darunter schlecht ist, ist unter jeder Nutzung schlecht.

## Die Varietät ist der Kern, nicht die Zahl

`GERICHTE` deckt zwölf Achsen ab (`--achsen` zeigt die Verteilung). Eine
lange Liste ähnlicher Gerichte wäre eine schlechtere Messung als eine
kürzere, gut gestreute — deshalb steht an jedem Gericht, wofür es dasteht,
und die Auswertung bricht die Quoten nach Achse auf.

## Das Ergebnis vom 2026-08-29

64 Gerichte, Qwen3.8-27B-Instruct, 10.361 aktive Produkte, `--parallel 6`,
6,6 min für Phase B. Kein Lauf ist gescheitert. Die ganze Ausgabe steht in
EVALS.md; kurz:

    Wege                    58× chefkoch, 3× llm, 3× recipe
    Katalogtreffer          411 von 533 Begriffen (77 %), Median je Gericht 83 %
    Rezeptentwurf           58 von 64 Zügen (91 %), 512 Zutaten
    Zusatzartikel im Korb   8 von 8 (die achte übersetzt, siehe unten)
    Zeilen im Laden         558 Vorschläge -> 558 Korbposten -> 558 Listenzeilen
    davon mit Menge         394 (71 %)

**Keine Zeile geht verloren, 164 Mengen schon.** Die Kette trägt jede
Vorschlagszeile bis auf die Einkaufsliste; die Menge trägt sie nicht überall
mit. Aufgeteilt:

    115 Zeilen  Freitext MIT Menge — `korb.einlegen` gibt einem Posten ohne
                Produkt keine Menge (dokumentiert, aber der einzige echte
                Bruch: der Vorschlag zeigt sie, die Einkaufsliste nicht)
     49 Zeilen  hatten nie eine (Vorrat, „n. B.", der Modellweg)
      0 Zeilen  zwischen Korbposten und Einkaufsliste — 375 -> 375

Von den Mengen, die es bis auf die Liste schaffen, sind **68 % nicht gegen
die Packung rechenbar** (157 von 485 sind es). Die Einheiten dahinter sind
nicht die erwarteten: „Zehe" kommt 8-mal vor, „Stk" 95-mal und „EL" 40-mal.
Der grosse Fall ist „2 Zwiebeln" gegen „1 kg Netz", nicht der Knoblauch.

**Zwei Gerichte verlieren ALLES, und der Grund ist derselbe:** Stufe 3 wählt
gegen den SATZ, nicht gegen das Rezept. Passen die beiden nicht zusammen —
„Salat" holt „KFC Coleslaw", „Kartoffelpürree" holt „Schweinefilet auf
Süßkartoffelpüree" —, lehnt das Modell jeden einzelnen Kandidaten ab, auch
Milch und Butter. Beide reproduzierbar; die Kontrollzeile „Salat als ganzer
Satz" (derselbe Zettel, nur „alles für Salat" statt „Salat") kommt auf 6 von
9. Ein drittes Gericht (Bibimbap) stand im grossen Lauf auf 0 und einzeln
nachgefahren auf 7 — das war ein Ausreisser unter sechs parallelen Anfragen
und kein Muster.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from zettel import db, obs, orders, recipes  # noqa: E402
from zettel.assistant import chat as chatmodul  # noqa: E402
from zettel.assistant import herkunft, oberbegriffe, vorschlaege  # noqa: E402
from zettel.gerichte import chefkoch, quelle  # noqa: E402
from zettel.gerichte import lauf as gerichtelauf  # noqa: E402
from zettel.llm import wake  # noqa: E402
from zettel.llm.client import Modellzugang  # noqa: E402
from zettel.orders import korb, pick  # noqa: E402

# --------------------------------------------------------------------------
# Die Gerichte

#: Die zwölf Achsen aus dem Ticket, in der Reihenfolge der Tabelle dort.
#: Sie stehen als Konstanten da, damit ein Tippfehler in einem Gericht nicht
#: still eine dreizehnte Achse erfindet.
ALLTAG = "Alltagsküche"
INTERNATIONAL = "international, im Katalog"
EXOTISCH = "international, exotisch"
VEGETARISCH = "vegetarisch/vegan"
BACKEN = "Backen"
ZAEHLBAR = "zählbare Einheiten"
EINWORT = "Einwort-Gericht"
MEHRDEUTIG = "mehrdeutig"
SCHREIBWEISE = "Schreibweise/Tippfehler"
FANTASIE = "Fantasiename"
GESPEICHERT = "bereits gespeichert"
#: Die dreizehnte Spalte der Tabelle ist keine eigene Liste, sondern ein
#: Zusatz AUF anderen Gerichten („alles für X, und Klopapier"). Sie liegt
#: quer über die Achsen, weil der WB-370-Fall genau das ist: ein Artikel
#: neben einem beliebigen Gericht. Wo sie greift, steht `rest` am Gericht.
MIT_REST = "mit Zusatzartikel"

#: Ein Gericht ist `(Name, Achse, Satz|None, Rest|None)` — und, wo es
#: nötig ist, `(…, Chefkochname)` als fünftes Feld.
#:
#: Der Rest wechselt von Gericht zu Gericht, wie in `rest_probe.py`:
#: „Klopapier“ findet der Katalog nicht, „Spülmittel“ und „Zahnpasta“
#: schon, „Katzenstreu“ seit WB-343 nicht mehr. Ein einziges Wort über
#: alle Gerichte hinweg misst das Wort und nicht den Weg.
#:
#: `Satz` ist gesetzt, wo der gemessene Satz NICHT „alles für <Name>" lauten
#: soll — bei den mehrdeutigen Wörtern nämlich, wo genau die nackte Eingabe
#: die Frage ist („Salat"), und bei den gespeicherten Rezepten, deren Name im
#: Satz stehen MUSS, damit der Rezeptweg greift (`rezeptweg.erkenne`).
GERICHTE: list[tuple[str, str, str | None, str | None]] = [
    # -- Deutsche Alltagsküche: der Normalfall ------------------------------
    ("Rouladen", ALLTAG, None, None),
    ("Frikadellen", ALLTAG, None, "Klopapier"),
    ("Grünkohl mit Pinkel", ALLTAG, None, None),
    ("Maultaschen", ALLTAG, None, None),
    ("Semmelknödel", ALLTAG, None, None),
    ("Königsberger Klopse", ALLTAG, None, None),
    ("Sauerbraten", ALLTAG, None, None),
    ("Rotkohl", ALLTAG, None, None),
    # -- International, Zutaten sollte der Katalog kennen -------------------
    ("Chili con Carne", INTERNATIONAL, None, "Spülmittel"),
    ("Moussaka", INTERNATIONAL, None, None),
    ("Gnocchi mit Salbeibutter", INTERNATIONAL, None, None),
    ("Shakshuka", INTERNATIONAL, None, None),
    ("Paella", INTERNATIONAL, None, None),
    ("Quiche Lorraine", INTERNATIONAL, None, None),
    ("Tortilla Espanola", INTERNATIONAL, None, None),
    # -- International, exotische Zutaten: die Katalogluecke ----------------
    ("Pho Bo", EXOTISCH, None, None),
    ("Bibimbap", EXOTISCH, None, None),
    ("Pad Thai", EXOTISCH, None, "Zahnpasta"),
    ("Ramen", EXOTISCH, None, None),
    ("Tom Kha Gai", EXOTISCH, None, None),
    ("Massaman Curry", EXOTISCH, None, None),
    ("Okonomiyaki", EXOTISCH, None, None),
    # -- Vegetarisch/vegan: keine Fleischzeile zum Festhalten ---------------
    ("Gemüselasagne", VEGETARISCH, None, None),
    ("Kichererbsencurry", VEGETARISCH, None, None),
    ("Linsenbolognese", VEGETARISCH, None, None),
    ("Falafel", VEGETARISCH, None, None),
    ("Chili sin Carne", VEGETARISCH, None, "Katzenstreu"),
    ("Ofengemüse mit Feta", VEGETARISCH, None, None),
    # -- Backen: Mengen in g, viele Grundzutaten ----------------------------
    ("Apfelkuchen", BACKEN, None, None),
    ("Käsekuchen", BACKEN, None, None),
    ("Pizzateig", BACKEN, None, None),
    ("Waffeln", BACKEN, None, None),
    ("Zwetschgendatschi", BACKEN, None, None),
    ("Marmorkuchen", BACKEN, None, "Taschentücher"),
    # -- Zählbare Einheiten: die bekannte Lücke aus WB-362 ------------------
    # Zehe, Stange, Bund, Scheibe, Stk — Einheiten, die sich gegen eine
    # Packungsgrösse in Gramm nicht rechnen lassen.
    ("Knoblauchsuppe", ZAEHLBAR, None, None),
    ("Spargel mit Sauce Hollandaise", ZAEHLBAR, None, None),
    ("Petersilienpesto", ZAEHLBAR, None, None),
    ("Toast Hawaii", ZAEHLBAR, None, None),
    ("Gefüllte Paprika", ZAEHLBAR, None, None),
    ("Lauchkuchen", ZAEHLBAR, None, None),
    # -- Einwort-Gerichte: die Auffächerung darf nicht anspringen (WB-368) --
    ("Lasagne", EINWORT, None, None),
    ("Risotto", EINWORT, None, None),
    ("Gulasch", EINWORT, None, None),
    ("Bratkartoffeln", EINWORT, None, None),
    ("Kaiserschmarrn", EINWORT, None, None),
    ("Hühnerfrikassee", EINWORT, None, None),
    # -- Mehrdeutig: nackt eingetippt, weil genau das die Frage ist ---------
    # Die ersten vier stehen so im Eingabefeld, wie jemand sie tippt: EIN
    # Wort, kein Satz. „Salat als ganzer Satz" daneben ist die KONTROLLE —
    # dasselbe Gericht, dieselbe Zutatenliste, nur der Satz ist ein Satz.
    # Ohne sie liesse sich nicht trennen, was an der Mehrdeutigkeit liegt und
    # was an der nackten Eingabe.
    ("Auflauf", MEHRDEUTIG, "Auflauf", None),
    ("Salat", MEHRDEUTIG, "Salat", None),
    ("Suppe", MEHRDEUTIG, "Suppe", None),
    ("Eintopf", MEHRDEUTIG, "Eintopf", None),
    ("Salat als ganzer Satz", MEHRDEUTIG, "alles für Salat", None, "Salat"),
    ("Nudeln mit Pesto", MEHRDEUTIG, None, None),
    # -- Schreibweisen und Tippfehler --------------------------------------
    # „Käse Lauch Suppe" und „Käse-Lauch-Suppe" sind DASSELBE Gericht in zwei
    # Schreibweisen und stehen beide da: der Unterschied zwischen ihnen ist
    # die Messung.
    ("Käse Lauch Suppe", SCHREIBWEISE, None, None),
    ("Käse-Lauch-Suppe", SCHREIBWEISE, None, None),
    ("Spagetti Bolognese", SCHREIBWEISE, None, None),   # ein t zu wenig
    ("Kartoffelpürree", SCHREIBWEISE, None, None),      # ein r zu viel
    ("Broccoliauflauf", SCHREIBWEISE, None, None),      # Katalog: Brokkoli
    ("Cordon bleu", SCHREIBWEISE, None, "Alufolie"),    # klein, französisch
    # -- Fantasienamen: der Rückfallweg ------------------------------------
    ("Schrumpelfrikandel", FANTASIE, None, None),
    ("Zwuckelpfanne mit Gnubbeln", FANTASIE, None, None),
    ("Glibberschmarrn", FANTASIE, None, "Klopapier"),
    # -- Bereits gespeichert: `weg = recipe`, ohne Modell -------------------
    # Diese drei laufen ZWEIMAL (siehe `_lauf`): der erste Zug legt das
    # Rezept an, der zweite ist der gemessene. Anders ist der Rezeptweg auf
    # dieser Datenbank nicht zu erreichen — keines der vorhandenen Rezepte
    # hat verknüpfte Produkte. Steht in `weg` trotzdem `chefkoch`, hat der
    # Vorlauf kein Rezept ergeben, und die Zeile sagt es.
    ("Ratatouille", GESPEICHERT, None, None),
    ("Quesadillas", GESPEICHERT, None, None),
    ("Kartoffelsalat", GESPEICHERT, None, "Spülmittel"),
    # ======================================================================
    # Die zweite Hälfte (2026-09-05): von 64 auf 128, dieselben elf Achsen
    # im selben Verhältnis. Nicht mehr vom Gleichen, sondern je Achse das,
    # was die erste Hälfte noch nicht abdeckt — bei jedem Gericht steht,
    # wofür es dasteht. Die Zusatzartikel wechseln weiter je Gericht.
    # ======================================================================
    # -- Alltagsküche, zweite Runde: Beilagen und Eintöpfe statt Braten -----
    ("Schnitzel mit Pommes", ALLTAG, None, "Müllbeutel"),   # zwei Gerichte in einem Namen
    ("Kartoffelpuffer", ALLTAG, None, None),                # fast nur Grundzutaten
    ("Linsensuppe mit Würstchen", ALLTAG, None, None),      # Hülsenfrucht + Wurst
    ("Kohlrouladen", ALLTAG, None, None),                   # ganzer Kohlkopf, zählbar
    ("Schweinebraten", ALLTAG, None, None),                 # ein Stück Fleisch nach Gewicht
    ("Spinat mit Spiegelei und Kartoffeln", ALLTAG, None, None),  # drei Bestandteile
    ("Hackbraten", ALLTAG, None, None),                     # Hack + Brötchen + Ei
    ("Zwiebelkuchen", ALLTAG, None, None),                  # herzhaft gebacken
    # -- International, zweite Runde: Klassiker, deren Zutaten der Katalog hat
    ("Carbonara", INTERNATIONAL, None, "Backpapier"),       # Guanciale vs. Speck
    ("Coq au Vin", INTERNATIONAL, None, None),              # Wein als Zutat
    ("Hähnchen Tikka Masala", INTERNATIONAL, None, None),   # Gewürzmischung
    ("Pulled Pork", INTERNATIONAL, None, None),             # englischer Name, deutscher Katalog
    ("Fajitas", INTERNATIONAL, None, None),                 # Tortillas + Streifen
    ("Minestrone", INTERNATIONAL, None, None),              # viele Gemüse, kleine Mengen
    ("Souvlaki", INTERNATIONAL, None, None),                # Spiesse: zählbar
    # -- International, exotisch, zweite Runde: andere Küchen als Thai/Vietnam
    ("Laksa", EXOTISCH, None, None),                        # Kokos + Currypaste
    ("Rendang", EXOTISCH, None, None),                      # Galgant, Kaffirlimette
    ("Butter Chicken", EXOTISCH, None, "Küchenrolle"),      # indisch, englischer Name
    ("Gyoza", EXOTISCH, None, None),                        # Teigblätter
    ("Kimchi Jjigae", EXOTISCH, None, None),                # Kimchi, Gochugaru
    ("Tacos al Pastor", EXOTISCH, None, None),              # Achiote, Ananas
    ("Ceviche", EXOTISCH, None, None),                      # roher Fisch, Limette
    # -- Vegetarisch/vegan, zweite Runde: Suppe, Bratling, Auflauf ---------
    ("Kürbissuppe", VEGETARISCH, None, "Spülschwamm"),
    ("Tofu-Curry", VEGETARISCH, None, None),                # Tofu im Katalog?
    ("Auberginen-Parmigiana", VEGETARISCH, None, None),     # Bindestrich im Namen
    ("Bohnen-Burger", VEGETARISCH, None, None),
    ("Grünkern-Bratlinge", VEGETARISCH, None, None),        # seltenes Getreide
    ("Spinatknödel", VEGETARISCH, None, None),
    # -- Backen, zweite Runde: Hefe, Brot, Torte ---------------------------
    ("Bienenstich", BACKEN, None, None),                    # Hefeteig + Creme
    ("Brezeln", BACKEN, None, None),                        # Natron/Lauge
    ("Zimtschnecken", BACKEN, None, None),
    ("Schwarzwälder Kirschtorte", BACKEN, None, None),      # Kirschwasser, Sahne
    ("Brownies", BACKEN, None, "Duschgel"),
    ("Vollkornbrot", BACKEN, None, None),                   # Mehlsorten
    # -- Zählbare Einheiten, zweite Runde: Scheiben, Köpfe, Bund, Stück -----
    ("Bruschetta", ZAEHLBAR, None, None),                   # Scheiben Brot
    ("Eier in Senfsoße", ZAEHLBAR, None, None),             # Eier in Stück
    ("Gefüllte Zucchini", ZAEHLBAR, None, None),            # Zucchini in Stück
    ("Blumenkohl mit Butterbröseln", ZAEHLBAR, None, None), # ein Kopf
    ("Zwiebelsuppe", ZAEHLBAR, None, None),                 # Zwiebeln in Stück
    ("Lachs mit Dillsoße", ZAEHLBAR, None, None),           # ein Bund Dill
    # -- Einwort-Gerichte, zweite Runde -----------------------------------
    ("Pfannkuchen", EINWORT, None, None),
    ("Schnitzel", EINWORT, None, None),                     # nackt, ohne Beilage
    ("Gulaschsuppe", EINWORT, None, "Klopapier"),
    ("Grießbrei", EINWORT, None, None),
    ("Reibekuchen", EINWORT, None, None),                   # Kartoffelpuffer, anderer Name
    ("Tzatziki", EINWORT, None, None),
    # -- Mehrdeutig, zweite Runde: wieder nackt, mit Kontrolle --------------
    ("Curry", MEHRDEUTIG, "Curry", None),
    ("Nudeln", MEHRDEUTIG, "Nudeln", None),
    ("Bowl", MEHRDEUTIG, "Bowl", None),
    ("Kuchen", MEHRDEUTIG, "Kuchen", None),
    ("Pfanne", MEHRDEUTIG, "Pfanne", None),
    ("Curry als ganzer Satz", MEHRDEUTIG, "alles für Curry", None, "Curry"),
    # -- Schreibweisen und Tippfehler, zweite Runde -------------------------
    ("Spaghetti Carbonara", SCHREIBWEISE, None, None),      # richtig — die Kontrolle
    ("Spagetti Carbonara", SCHREIBWEISE, None, None),       # ein h zu wenig
    ("Gnocci", SCHREIBWEISE, None, None),                   # ein h zu wenig
    ("Cesar Salad", SCHREIBWEISE, None, None),              # Caesar, englisch
    ("Kürbisuppe", SCHREIBWEISE, None, None),               # ein s zu wenig
    ("Zaziki", SCHREIBWEISE, None, None),                   # Tzatziki, eingedeutscht
    # -- Fantasienamen, zweite Runde ---------------------------------------
    ("Knusperzwerg-Auflauf", FANTASIE, None, "Batterien"),
    ("Brummelbeer-Torte", FANTASIE, None, None),
    ("Flitzepfanne Deluxe", FANTASIE, None, None),
    # -- Bereits gespeichert, zweite Runde ---------------------------------
    ("Kürbisrisotto", GESPEICHERT, None, None),
    ("Tomatensuppe", GESPEICHERT, None, None),
    ("Nudelsalat", GESPEICHERT, None, "Kaffeefilter"),
]

#: Gegen die Box parallel. Sie verträgt es (`rest_probe.py` fährt sechs), und
#: 127 Züge nacheinander wären anderthalb Stunden statt einer Viertelstunde.
PARALLEL = 6


def satz_von(g) -> str:
    name, _achse, satz, rest = g[:4]
    if satz is not None:
        return satz
    grund = f"alles für {name}"
    return f"{grund}, und {rest}" if rest else grund


def chefkochname_von(g) -> str:
    """Unter welchem Namen dieses Gericht bei Chefkoch gesucht wird.

    Meist der Name selbst. Das fünfte Feld gibt es, weil zwei Zeilen dasselbe
    Gericht in zwei Sätzen messen können — sie brauchen zwei Namen für die
    Tabelle und denselben für die Quelle, sonst misst die Kontrolle ein
    anderes Rezept als der Fall, gegen den sie steht.
    """
    return g[4] if len(g) > 4 and g[4] else g[0]


# --------------------------------------------------------------------------
# Phase A — vorwärmen


def vorwaermen(basis_db: str, gerichte, *, schreib=print) -> dict:
    """Holt jedes Gericht EINMAL bei Chefkoch, seriell, mit Pause.

    Gibt `{Gericht: Zustand}` zurück (`ok`, `leer`, `fehler`, `speicher` für
    „stand schon frisch da"). Der Zustand ist ein Messwert und keine
    Nebensache: er sagt, ob das Gericht überhaupt eine Quelle hatte.

    **Die Pause ist der Grund, warum diese Phase seriell läuft.** Chefkoch
    ist eine fremde Seite; 63 Gerichte parallel abzurufen wäre genau das
    Überrennen, das der bestehende Client (`chefkoch.PAUSE_S`) vermeidet.
    """
    con = db.connect(basis_db)
    db.migrate(con)
    q = quelle.Quelle()
    http = httpx.Client(timeout=chefkoch.TIMEOUT_S,
                        headers={"User-Agent": chefkoch.USER_AGENT})
    zustaende: dict[str, str] = {}
    try:
        for i, g in enumerate(gerichte):
            name = chefkochname_von(g)
            zeile = q.zeile(con, name)
            if zeile is not None and q.gericht(con, name) is not None:
                zustaende[g[0]] = "speicher"
                schreib(f"  speicher: {name}")
                continue
            if i:
                time.sleep(chefkoch.PAUSE_S)
            zustaende[g[0]] = gerichtelauf.hole_eines(
                con, http, name, schreib=lambda z: schreib("  " + z))
    finally:
        http.close()
        con.close()
    return zustaende


# --------------------------------------------------------------------------
# Phase B — ein Gericht, der ganze Zug

#: Wie viele Zeichen ein Gerichtsname in der Tabelle bekommt.
_NAME = 30


def _kopie(basis_db: str, ziel: str, sperre: Lock) -> None:
    """Eine eigene Arbeitskopie, per `VACUUM INTO`.

    `VACUUM INTO` und nicht `shutil.copy`: die Basis läuft im WAL-Modus, und
    eine Dateikopie ohne das `-wal` daneben wäre der Stand von vorgestern.
    Unter der Sperre, weil sechs gleichzeitige Vacuums auf dieselbe Quelle
    nur Kopf schütteln lassen und nichts einbringen — es dauert je 50 ms.
    """
    with sperre:
        if os.path.exists(ziel):
            os.remove(ziel)
        con = db.connect(basis_db)
        try:
            con.execute("VACUUM INTO ?", (ziel,))
        finally:
            con.close()


def _quellzutaten(con, ergebnis, gericht: str) -> list[dict]:
    """Die Zutatenliste, gegen die dieser Zug gelaufen ist.

    Drei Wege, drei Herkünfte — und die Unterscheidung gehört in die Messung
    und nicht unter den Tisch: auf dem Chefkoch-Weg ist es das geholte
    Rezept, auf dem Rezeptweg das gespeicherte, auf dem Modellweg gibt es
    keine (das Modell hat sich die Zutaten gedacht, es gibt nichts, wogegen
    man messen könnte).
    """
    if ergebnis.weg == chatmodul.WEG_QUELLE:
        voll = quelle.Quelle().gericht(con, ergebnis.gericht or gericht)
        return list(voll["zutaten"]) if voll else []
    if ergebnis.weg == chatmodul.WEG_REZEPT and ergebnis.rezepte:
        zutaten = []
        for name in ergebnis.rezepte:
            for r in recipes.rezepte(con):
                if r["name"] == name:
                    zutaten.extend(recipes.zutaten(con, int(r["id"])))
                    break
        return zutaten
    return []


def _mit_menge(zutat: dict) -> bool:
    """Nennt die Quelle zu dieser Zutat eine Menge?

    Dieselbe Regel wie `herkunft._menge`: Chefkochs `0.0` heisst „nach
    Belieben" und ist keine Menge. Wer sie mitzählte, machte aus „Salz und
    Pfeffer" einen verlorenen Bedarf.
    """
    try:
        return float(zutat.get("amount")) > 0
    except (TypeError, ValueError):
        return False


def _lauf(gericht, basis_db: str, ordner: Path, zugang, sperre: Lock,
          kopiersperre: Lock, *, faecher_pruefen: bool = True) -> dict:
    """EIN Gericht: Satz -> Vorschläge -> Ja -> Korb -> Einkaufsliste.

    Alles auf einer eigenen Arbeitskopie, die am Ende gelöscht wird. Was hier
    schiefgeht, wird zu `fehler` am Ergebnis und kostet nicht den Lauf: ein
    Gericht, an dem die Box abbricht, ist ein Messwert.
    """
    name, achse, _satz, rest = gericht[:4]
    satz = satz_von(gericht)
    ziel = str(ordner / f"{abs(hash(name)) % 10**9}.db")
    d: dict = {"gericht": name, "achse": achse, "satz": satz, "rest": rest,
               "fehler": None}
    t0 = time.monotonic()
    con = None
    try:
        _kopie(basis_db, ziel, kopiersperre)
        con = db.connect(ziel)
        db.migrate(con)
        # Die Auffächerung WÜRDE hier anspringen — die Frage der EINWORT-
        # Achse (WB-368). Sie wird vor dem Zug gestellt, weil der Zug sie
        # nicht mehr stellt, sobald ein Gericht im Speicher steht.
        d["faecher_moeglich"] = bool(
            faecher_pruefen and oberbegriffe.aus_katalog(con, satz))

        agent = chatmodul.Chat(zugang, wecker=wake.Wecker(),
                               quelle=quelle.Quelle())
        # **Der Vorlauf für die Achse „bereits gespeichert"** — und der Grund
        # dafür ist ein Befund: KEINES der fünf Rezepte der echten Datenbank
        # hat verknüpfte Produkte (`recipe_item` ist überall leer), weil alle
        # aus Chefkoch stammen. `rezeptweg.erkenne` übergeht Rezepte ohne
        # Zutaten, der Rezeptweg ist auf dieser Datenbank also gar nicht zu
        # erreichen. Er wird deshalb hier erst HERGESTELLT, und zwar auf dem
        # Weg, auf dem er im Betrieb entsteht (WB-337): ein Chefkoch-Zug,
        # „Ja" auf alles, abschicken — danach steht ein Rezept mit Produkten
        # da. Das ist kein Kunstgriff, sondern das zweite Mal Kochen.
        if achse == GESPEICHERT:
            vor = agent.turn(con, satz)
            if vor.chat_message_id is not None:
                vorschlaege.alle_entscheiden(con, vor.chat_message_id,
                                             vorschlaege.BEHALTEN)
            try:
                korb.abschicken(con)
            except orders.LeererWarenkorb:
                pass
            d["vorlauf"] = vor.weg
        ergebnis = agent.turn(con, satz)
        d["weg"] = ergebnis.weg
        d["abruf"] = ergebnis.abruf
        d["gericht_erkannt"] = ergebnis.gericht
        d["quelle_name"] = ergebnis.quelle_name
        d["begriffe"] = len(ergebnis.begriffe)
        d["verworfen"] = len(ergebnis.verworfen)
        d["katalogtreffer"] = ergebnis.n_produkte
        d["freitext"] = ergebnis.n_freitext
        d["entwurf"] = ergebnis.entwurf
        d["entwurf_zutaten"] = ergebnis.entwurf_zutaten
        d["meldung"] = ergebnis.meldung

        zutaten = _quellzutaten(con, ergebnis, chefkochname_von(gericht))
        d["zutaten_quelle"] = len(zutaten)
        d["zutaten_quelle_mit_menge"] = sum(1 for z in zutaten
                                            if _mit_menge(z))

        # -- Glied 2: hat die Herkunftszuordnung die Zutat wiedergefunden? --
        # `ergebnis.begriffe` trägt seit WB-369 die Herkunftszutat am
        # Begriff. Was dort nie auftaucht, ist die Zutat, deren Menge schon
        # VOR der Suche verloren ging.
        zugeordnet: set[str] = set()
        for b in ergebnis.begriffe:
            for teil in str(b.get("zutat") or "").split(", "):
                if teil.strip():
                    zugeordnet.add(teil.strip())
        d["zutaten_zugeordnet"] = sum(
            1 for z in zutaten
            if (z.get("raw_name") or z.get("name") or "") in zugeordnet)
        d["zutaten_zugeordnet_mit_menge"] = sum(
            1 for z in zutaten if _mit_menge(z)
            and (z.get("raw_name") or z.get("name") or "") in zugeordnet)
        d["ohne_zutat"] = [b["suchbegriffe"][0] for b in ergebnis.begriffe
                           if not b.get("zutat")]

        # -- Die Vorschlagszeilen ------------------------------------------
        v = ergebnis.vorschlaege
        d["vorschlaege"] = len(v)
        d["mit_menge"] = sum(1 for z in v if z["need_amount"] is not None)
        d["ausrechenbar"] = sum(1 for z in v if z["rechnung"].ausrechenbar)
        # Die Zeilen, die eine Menge tragen, aber KEIN Produkt: sie verlieren
        # sie beim „Ja" (`korb.einlegen` gibt einem Freitext keine Menge).
        d["freitext_mit_menge"] = sum(
            1 for z in v
            if z["need_amount"] is not None and z["product_id"] is None)
        # Derselbe Wortvergleich wie im Shop (`chat._rest_sichern`) und wie
        # in `rest_probe.py`: „Klopapier" kann als Freitext ODER als
        # gefundenes Produkt dastehen, und ein Zeichenkettenvergleich hielte
        # den Treffer für ein Fehlen.
        d["rest_da"] = bool(rest) and any(
            herkunft.punkte(str(z["search_term"] or z["free_text"] or ""),
                            rest) >= herkunft.SCHWELLE for z in v)

        # -- „Ja" auf alles, dann der Korb ---------------------------------
        if ergebnis.chat_message_id is not None:
            vorschlaege.alle_entscheiden(con, ergebnis.chat_message_id,
                                         vorschlaege.BEHALTEN)
        order_id = ergebnis.order_id
        posten = orders.posten(con, order_id)
        d["korbposten"] = len(posten)
        d["korb_mit_menge"] = sum(1 for p in posten
                                  if p["need_amount"] is not None)

        # -- Und die Einkaufsliste ------------------------------------------
        # Abgeschickt wird wirklich: die Pick-Liste zeigt `offen`e
        # Bestellungen, und eine Messung am Warenkorb wäre eine Messung an
        # einer anderen Ansicht.
        try:
            korb.abschicken(con)
            d["abgeschickt"] = True
        except orders.LeererWarenkorb:
            d["abgeschickt"] = False
        zeilen = [z for gruppe in pick.nach_laden(con, order_id)
                  for z in gruppe["posten"]]
        d["listenzeilen"] = len(zeilen)
        d["liste_zeigt_menge"] = sum(1 for z in zeilen if z["bedarf_text"])
        d["liste_ohne_menge"] = [z["name"] for z in zeilen
                                 if not z["bedarf_text"]]
        d["zeilen"] = [{"name": z["name"], "qty": z["qty"],
                        "bedarf": z["bedarf_text"],
                        "gebinde": z["gebinde_text"],
                        "freitext": z["ist_freitext"]} for z in zeilen]
    except Exception as e:                       # noqa: BLE001 — Probe
        d["fehler"] = f"{type(e).__name__}: {e}"
    finally:
        if con is not None:
            con.close()
        for endung in ("", "-wal", "-shm"):
            try:
                os.remove(ziel + endung)
            except OSError:
                pass
    d["dauer_s"] = round(time.monotonic() - t0, 1)

    with sperre:
        if d["fehler"]:
            print(f"  {name[:_NAME]:<{_NAME}} FEHLER {d['fehler'][:60]}")
        else:
            print(f"  {name[:_NAME]:<{_NAME}} {d['weg']:<8} "
                  f"{d['zutaten_quelle']:>2}Z {d['begriffe']:>2}B "
                  f"{d['katalogtreffer']:>2}K {d['freitext']:>2}F  "
                  f"Menge {d['mit_menge']:>2}V -> {d['korb_mit_menge']:>2}P "
                  f"-> {d['liste_zeigt_menge']:>2}L "
                  f"({d['dauer_s']:.0f} s)")
    return d


# --------------------------------------------------------------------------
# Auswertung


def _quote(teil: int, ganz: int) -> str:
    return "—" if not ganz else f"{teil / ganz * 100:.0f} %"


def _verteilung(werte: list[float], stufen=(0, 20, 40, 60, 80, 100)) -> str:
    """Ein Balken je Zwanzigerschritt. **Der Mittelwert allein lügt hier.**

    „Im Schnitt 70 % im Katalog" heisst nichts, wenn die Hälfte bei 95 % und
    die andere bei 40 % liegt — genau der Fall, den Punkt 3 des Tickets
    verlangt. Die Klassen sind links offen und rechts geschlossen, damit die
    100 % nicht in einem eigenen Rand verschwinden.
    """
    if not werte:
        return "    (keine Werte)"
    zeilen = []
    for unten, oben in zip(stufen, stufen[1:]):
        if unten == 0:
            treffer = [w for w in werte if unten <= w <= oben]
            beschriftung = f"{unten:>3}–{oben:>3} %"
        else:
            treffer = [w for w in werte if unten < w <= oben]
            beschriftung = f"{unten:>3}–{oben:>3} %"
        n = len(treffer)
        zeilen.append(f"    {beschriftung}  {'█' * n}{'' if n else '·'} {n}")
    return "\n".join(zeilen)


def _summe(daten: list[dict], feld: str) -> int:
    return sum(int(d.get(feld) or 0) for d in daten)


def bericht(ergebnisse: list[dict], zustaende: dict, dauer: float) -> None:
    ok = [d for d in ergebnisse if not d["fehler"]]
    kaputt = [d for d in ergebnisse if d["fehler"]]

    # -- 1. Die Tabelle je Gericht -----------------------------------------
    print()
    print("=" * 118)
    print("JE GERICHT")
    print("=" * 118)
    kopf = (f"{'Gericht':<{_NAME}} {'Achse':<24} {'weg':<8} {'abr':<6} "
            f"{'Zut':>3} {'Beg':>3} {'Kat':>3} {'Fre':>3} {'Men':>3} "
            f"{'Aus':>3} {'Ent':>3} {'Kor':>3} {'Lis':>3} {'R':>1} "
            f"{'s':>5}")
    print(kopf)
    print("-" * 118)
    for d in ergebnisse:
        if d["fehler"]:
            print(f"{d['gericht'][:_NAME]:<{_NAME}} {d['achse'][:24]:<24} "
                  f"FEHLER  {d['fehler'][:60]}")
            continue
        rest = "—" if not d["rest"] else ("ja" if d["rest_da"] else "NEIN")
        print(f"{d['gericht'][:_NAME]:<{_NAME}} {d['achse'][:24]:<24} "
              f"{d['weg']:<8} {str(d['abruf'] or '—'):<6} "
              f"{d['zutaten_quelle']:>3} {d['begriffe']:>3} "
              f"{d['katalogtreffer']:>3} {d['freitext']:>3} "
              f"{d['mit_menge']:>3} {d['ausrechenbar']:>3} "
              f"{d['entwurf_zutaten']:>3} {d['korb_mit_menge']:>3} "
              f"{d['liste_zeigt_menge']:>3} {rest:>1.1} "
              f"{d['dauer_s']:>5.0f}")
    print("-" * 118)
    print("Zut = Zutaten der Quelle · Beg = Begriffe aus Stufe 1 · "
          "Kat = Katalogtreffer · Fre = Freitext")
    print("Men = Vorschläge mit Menge · Aus = davon ausrechenbar · "
          "Ent = Zutaten im Rezeptentwurf")
    print("Kor = Korbposten mit Menge · Lis = Einkaufslistenzeilen mit "
          "Menge · R = Zusatzartikel im Korb")

    # -- 2. Die Zusammenfassung --------------------------------------------
    print()
    print("=" * 118)
    print("ZUSAMMENFASSUNG")
    print("=" * 118)
    print(f"  {len(ergebnisse)} Gerichte, {len(ok)} gelaufen, "
          f"{len(kaputt)} mit Fehler, {dauer / 60:.1f} min")
    print(f"  Chefkoch (Phase A): "
          + ", ".join(f"{n}× {z}"
                      for z, n in Counter(zustaende.values()).most_common()))
    print("  Wege: " + ", ".join(
        f"{n}× {w}" for w, n in
        Counter(d["weg"] for d in ok).most_common()))
    print("  Abruf im Zug: " + ", ".join(
        f"{n}× {a}" for a, n in
        Counter(str(d["abruf"] or "—") for d in ok).most_common()))

    # **Nur die Wege, die überhaupt Begriffe gemacht haben.** Der Rezeptweg
    # hat null Begriffe und trotzdem Katalogtreffer (seine Produkte stehen
    # schon im Rezept) — in derselben Quote ergäbe das über 100 %.
    mit_stufe1 = [d for d in ok if d["begriffe"]]
    begriffe = _summe(mit_stufe1, "begriffe")
    treffer = _summe(mit_stufe1, "katalogtreffer")
    freitext = _summe(mit_stufe1, "freitext")
    print()
    print(f"  Begriffe insgesamt ({len(mit_stufe1)} Gerichte mit Stufe 1): "
          f"{begriffe}")
    print(f"    davon mit Katalogprodukt: {treffer} "
          f"({_quote(treffer, begriffe)})")
    print(f"    davon als Freitext liegen geblieben: {freitext} "
          f"({_quote(freitext, begriffe)})")
    mit_entwurf = [d for d in ok if d["entwurf"]]
    print(f"  Rezeptentwurf entstanden: {len(mit_entwurf)} von {len(ok)} "
          f"({_quote(len(mit_entwurf), len(ok))}), zusammen "
          f"{_summe(ok, 'entwurf_zutaten')} Zutaten")
    mit_rest = [d for d in ok if d["rest"]]
    rest_da = [d for d in mit_rest if d["rest_da"]]
    print(f"  Zusatzartikel im Satz: {len(rest_da)} von {len(mit_rest)} "
          f"landeten im Korb ({_quote(len(rest_da), len(mit_rest))})")
    # Der Vergleich ist ein WORTvergleich (`herkunft.punkte`) und kennt keine
    # Synonyme. Übersetzt der Modellweg „Klopapier" zu „Toilettenpapier",
    # liegt der Artikel im Korb und wird hier trotzdem nicht gezählt. Die
    # Zeilen stehen darunter, damit sich das von Hand prüfen lässt, statt
    # eine zu niedrige Zahl unwidersprochen stehen zu lassen.
    for d in (e for e in mit_rest if not e["rest_da"]):
        print(f"    nicht erkannt: {d['gericht']} + „{d['rest']}“ — "
              f"im Korb liegen: "
              + ", ".join(z["name"][:28] for z in (d.get("zeilen") or [])))
    faecher = [d for d in ok if d.get("faecher_moeglich")]
    angesprungen = [d for d in faecher if d["weg"] == chatmodul.WEG_FAECHER]
    print(f"  Auffächerung wäre möglich gewesen bei {len(faecher)} Gerichten "
          f"({', '.join(d['gericht'] for d in faecher) or '—'});")
    print(f"    tatsächlich angesprungen ist sie bei {len(angesprungen)}.")
    dauern = sorted(d["dauer_s"] for d in ok)
    if dauern:
        print(f"  Dauer je Gericht: Median {statistics.median(dauern):.0f} s, "
              f"schnellstes {dauern[0]:.0f} s, langsamstes {dauern[-1]:.0f} s")

    # -- 3. Die Verteilung, nicht nur der Mittelwert ------------------------
    print()
    print("-" * 118)
    print("VERTEILUNG — Katalogtreffer je Gericht (nicht der Mittelwert)")
    print("-" * 118)
    quoten = [d["katalogtreffer"] / d["begriffe"] * 100
              for d in ok if d["begriffe"]]
    print(_verteilung(quoten))
    if quoten:
        print(f"    Mittelwert {statistics.mean(quoten):.0f} %, "
              f"Median {statistics.median(quoten):.0f} %, "
              f"Spanne {min(quoten):.0f}–{max(quoten):.0f} %")

    print()
    print("-" * 118)
    print("VERTEILUNG — Anteil der Einkaufszeilen MIT Menge je Gericht")
    print("-" * 118)
    mengenquoten = [d["liste_zeigt_menge"] / d["listenzeilen"] * 100
                    for d in ok if d.get("listenzeilen")]
    print(_verteilung(mengenquoten))
    if mengenquoten:
        print(f"    Mittelwert {statistics.mean(mengenquoten):.0f} %, "
              f"Median {statistics.median(mengenquoten):.0f} %, "
              f"Spanne {min(mengenquoten):.0f}–{max(mengenquoten):.0f} %")

    # -- 4. Nach Achse ------------------------------------------------------
    print()
    print("-" * 118)
    print("NACH ACHSE — hier steht, ob die Varietät etwas zutage gefördert "
          "hat")
    print("-" * 118)
    print(f"  {'Achse':<26} {'n':>2} {'Beg':>4} {'Kat':>4} {'Quote':>6} "
          f"{'Men':>4} {'Kor':>4} {'Lis':>4} {'Quote':>6}")
    nach: dict[str, list[dict]] = defaultdict(list)
    for d in ok:
        nach[d["achse"]].append(d)
    for achse in (ALLTAG, INTERNATIONAL, EXOTISCH, VEGETARISCH, BACKEN,
                  ZAEHLBAR, EINWORT, MEHRDEUTIG, SCHREIBWEISE, FANTASIE,
                  GESPEICHERT):
        gruppe = nach.get(achse) or []
        if not gruppe:
            continue
        b, k = _summe(gruppe, "begriffe"), _summe(gruppe, "katalogtreffer")
        m = _summe(gruppe, "mit_menge")
        p = _summe(gruppe, "korb_mit_menge")
        li = _summe(gruppe, "liste_zeigt_menge")
        lz = _summe(gruppe, "listenzeilen")
        print(f"  {achse:<26} {len(gruppe):>2} {b:>4} {k:>4} "
              f"{_quote(k, b):>6} {m:>4} {p:>4} {li:>4} {_quote(li, lz):>6}")
    quer = [d for d in ok if d["rest"]]
    if quer:
        print(f"  {MIT_REST + ' (quer)':<26} {len(quer):>2} "
              f"{_summe(quer, 'begriffe'):>4} "
              f"{_summe(quer, 'katalogtreffer'):>4} "
              f"{_quote(_summe(quer, 'katalogtreffer'), _summe(quer, 'begriffe')):>6}"
              f" {_summe(quer, 'mit_menge'):>4} "
              f"{_summe(quer, 'korb_mit_menge'):>4} "
              f"{_summe(quer, 'liste_zeigt_menge'):>4} "
              f"{_quote(_summe(quer, 'liste_zeigt_menge'), _summe(quer, 'listenzeilen')):>6}")

    # -- 5. Die Mengenkette, Glied für Glied --------------------------------
    mengenkette(ok)

    # -- 6. Die zehn schlechtesten Gerichte ---------------------------------
    schlechteste(ergebnisse)


def mengenkette(ok: list[dict]) -> None:
    """Der Nachtrag des Tickets: **an welchem Glied gehen die Mengen weg?**

    Ein Balken je Glied, wie das Ticket es verlangt — und die vier Kandidaten
    ausdrücklich getrennt, weil sie verschiedene Reparaturen brauchen. Die
    Zähleinheit wechselt unterwegs (Zutaten -> Begriffe -> Vorschläge ->
    Posten -> Zeilen) und das steht dabei: mehrere Zutaten können zu einem
    Begriff werden, mehrere Begriffe zu einem Posten. Wer die Zahlen als eine
    einzige Quote läse, läse sie falsch.
    """
    print()
    print("=" * 118)
    print("DIE MENGENKETTE — Satz -> Zutat -> Suchbegriff -> Produkt -> "
          "Korbposten -> Einkaufsliste")
    print("=" * 118)
    # Nur die Wege, die überhaupt eine Zutatenliste hatten: auf dem Modellweg
    # gibt es keine Quelle, gegen die sich eine verlorene Menge messen liesse.
    mit_quelle = [d for d in ok if d["zutaten_quelle"]
                  and d["weg"] == chatmodul.WEG_QUELLE]
    z_alle = _summe(mit_quelle, "zutaten_quelle")
    z_menge = _summe(mit_quelle, "zutaten_quelle_mit_menge")
    z_zug = _summe(mit_quelle, "zutaten_zugeordnet_mit_menge")
    v_menge = _summe(mit_quelle, "mit_menge")
    v_aus = _summe(mit_quelle, "ausrechenbar")
    v_frei = _summe(mit_quelle, "freitext_mit_menge")
    p_menge = _summe(mit_quelle, "korb_mit_menge")
    l_menge = _summe(mit_quelle, "liste_zeigt_menge")
    l_alle = _summe(mit_quelle, "listenzeilen")

    def balken(n: int, bezug: int) -> str:
        return "█" * round(40 * n / bezug) if bezug else ""

    print(f"  {len(mit_quelle)} Gerichte auf dem Chefkoch-Weg — nur "
          "dort gibt es eine Quelle,\n  gegen die sich eine verlorene Menge "
          "überhaupt messen lässt.\n")
    for beschriftung, n, einheit in [
            ("Zutaten in der Quelle", z_alle, "Zutaten"),
            ("… davon mit einer Menge", z_menge, "Zutaten"),
            ("… davon einem Begriff zugeordnet", z_zug, "Zutaten"),
            ("Vorschlagszeilen mit Menge", v_menge, "Zeilen"),
            ("… davon gegen die Packung rechenbar", v_aus, "Zeilen"),
            ("Korbposten mit Menge", p_menge, "Posten"),
            ("Einkaufslistenzeilen mit Menge", l_menge, "Zeilen")]:
        print(f"  {beschriftung:<38} {n:>4} {einheit:<8} "
              f"{balken(n, z_alle)}")
    print(f"\n  Einkaufslistenzeilen insgesamt: {l_alle} — "
          f"{_quote(l_menge, l_alle)} tragen eine Menge.")

    print()
    print("  Wo die Menge bleibt — die vier Kandidaten des Tickets:")
    print(f"    1. Die Quelle nennt keine Menge          "
          f"{z_alle - z_menge:>4} von {z_alle} Zutaten "
          f"({_quote(z_alle - z_menge, z_alle)})")
    print('       („Salz und Pfeffer n. B.“ — Chefkochs 0.0 ist '
          'keine Menge.)')
    print(f"    2. Die Zuordnung findet die Zutat nicht  "
          f"{z_menge - z_zug:>4} von {z_menge} Zutaten mit Menge "
          f"({_quote(z_menge - z_zug, z_menge)})")
    print("       (WB-369/WB-371. Mehrere Zutaten können auf EINEN Begriff")
    print("        fallen — dann zählt eine davon hier als nicht "
          "zugeordnet,")
    print("        und ihre Menge steckt trotzdem in der Summe des anderen.)")
    print(f"    3. Die Einheit ist nicht rechenbar       "
          f"{v_menge - v_aus:>4} von {v_menge} Zeilen mit Menge "
          f"({_quote(v_menge - v_aus, v_menge)})")
    print('       (WB-362, „4 Zehen“ gegen „100 g“. Die '
          'Menge steht trotzdem')
    print("        auf der Liste — nur die Packungszahl ist geraten.)")
    print(f"    4. Etwas dazwischen verliert sie         "
          f"{v_frei:>4} Zeilen: Freitext MIT Menge")
    print("       (`orders.korb.einlegen` gibt einem Posten ohne Produkt")
    print("        keine Menge. Dokumentiert, aber es IST der Bruch: der")
    print("        Vorschlag zeigt sie, die Einkaufsliste nicht mehr.)")

    fehlt = v_menge - p_menge - v_frei
    print(f"\n    Unerklärt bliebe: {fehlt} Zeilen "
          f"(Vorschläge mit Menge {v_menge} − Korbposten mit Menge "
          f"{p_menge} − Freitext {v_frei}).")
    print("    Ist die Zahl negativ, hat der Korb ZUSAMMENGEZOGEN: zwei "
          "Vorschläge")
    print("    auf dasselbe Produkt werden ein Posten — das ist kein "
          "Verlust.")
    print(f"    Von Korbposten zu Einkaufsliste: {p_menge} -> {l_menge}. "
          "Jede Differenz")
    print("    hier wäre ein Fehler in der Anzeige (WB-381) und kein "
          "bekannter Verlust.")

    # Der Rezeptweg getrennt: dort gibt es gar keine Suchbegriffe, also auch
    # kein Glied 2 und 3. Ihn in dieselbe Spalte zu schreiben hätte
    # ausgerechnet den Weg schlechtgerechnet, der die Mengen am besten überträgt.
    rez = [d for d in ok if d["weg"] == chatmodul.WEG_REZEPT]
    if rez:
        print()
        print(f"  Der Rezeptweg getrennt ({len(rez)} Gerichte): "
              f"{_summe(rez, 'zutaten_quelle_mit_menge')} von "
              f"{_summe(rez, 'zutaten_quelle')} Rezeptzutaten tragen eine "
              "Menge,")
        print(f"    {_summe(rez, 'mit_menge')} Vorschlagszeilen, "
              f"{_summe(rez, 'korb_mit_menge')} Korbposten, "
              f"{_summe(rez, 'liste_zeigt_menge')} von "
              f"{_summe(rez, 'listenzeilen')} Listenzeilen mit Menge.")
        print("    Kein Modell, keine Suche, keine Zuordnung — Glied 2 "
              "und 3 gibt es hier nicht.")


def schlechteste(ergebnisse: list[dict], n: int = 10) -> None:
    """Die zehn schlechtesten Gerichte NAMENTLICH, mit Grund.

    Eine Durchschnittsquote verdeckt genau die Fälle, für die sich das lohnt
    (Punkt 2 des Tickets). Sortiert wird nach einer Note, die drei Dinge
    zusammenzieht — Katalogtreffer, Mengen auf der Einkaufsliste und ob
    überhaupt etwas herauskam. Ein Fehler ist immer das Schlechteste.
    """
    def note(d: dict) -> float:
        if d["fehler"]:
            return -1.0
        if not d.get("listenzeilen"):
            return 0.0
        katalog = (d["katalogtreffer"] / d["begriffe"]) if d["begriffe"] else 0
        menge = d["liste_zeigt_menge"] / d["listenzeilen"]
        return round(katalog + menge, 4)

    def grund(d: dict) -> str:
        if d["fehler"]:
            return d["fehler"][:70]
        teile = []
        if not d.get("listenzeilen"):
            teile.append("nichts auf der Liste")
        if d["weg"] == chatmodul.WEG_LLM:
            teile.append(f"kein Rezept (abruf={d['abruf'] or '—'}), "
                         "Zutaten geraten")
        if d["begriffe"] and d["katalogtreffer"] / d["begriffe"] < 0.6:
            teile.append(f"{d['freitext']} von {d['begriffe']} Begriffen "
                         "ohne Katalogprodukt")
        if d.get("listenzeilen") and not d["liste_zeigt_menge"]:
            teile.append("keine einzige Menge auf der Liste")
        elif d.get("listenzeilen"):
            teile.append(f"{d['listenzeilen'] - d['liste_zeigt_menge']} von "
                         f"{d['listenzeilen']} Listenzeilen ohne Menge")
        if d["rest"] and not d["rest_da"]:
            teile.append("Zusatzartikel „" + d["rest"]
                         + "“ fehlt")
        return "; ".join(teile) or "unauffällig"

    print()
    print("=" * 118)
    print(f"DIE {n} SCHLECHTESTEN — namentlich, mit Grund")
    print("=" * 118)
    for d in sorted(ergebnisse, key=note)[:n]:
        print(f"  {d['gericht'][:_NAME]:<{_NAME}} {note(d):>5.2f}  "
              f"{grund(d)}")


# --------------------------------------------------------------------------


def achsen_zeigen() -> None:
    print(f"{len(GERICHTE)} Gerichte über "
          f"{len(set(g[1] for g in GERICHTE))} Achsen:\n")
    nach: dict[str, list[str]] = defaultdict(list)
    for g in GERICHTE:
        nach[g[1]].append(g[0])
    for achse, namen in nach.items():
        print(f"  {achse:<26} {len(namen):>2}  {', '.join(namen)}")
    mit_rest = [g for g in GERICHTE if g[3]]
    print(f"\n  {MIT_REST + ' (quer)':<26} {len(mit_rest):>2}  "
          + ", ".join(f"{g[0]} + {g[3]}" for g in mit_rest))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", help="KOPIE der Datenbank — nicht data/picknick.db")
    p.add_argument("--messen", action="store_true")
    p.add_argument("--achsen", action="store_true",
                   help="nur die Gerichtsliste und ihre Achsen zeigen")
    p.add_argument("--gerichte", type=int, default=len(GERICHTE))
    p.add_argument("--nur", action="append", default=[],
                   help="nur diese Gerichte (mehrfach erlaubt) — für einen "
                        "Probelauf, ohne die Liste anzufassen")
    p.add_argument("--parallel", type=int, default=PARALLEL)
    p.add_argument("--json", default=None,
                   help="Rohdaten je Gericht hierhin schreiben")
    p.add_argument("--ohne-vorwaermen", action="store_true",
                   help="Phase A überspringen (misst dann den KALTEN Weg)")
    p.add_argument("--trace", action="store_true",
                   help="Spans nach Phoenix schicken. Projekt über "
                        "ZETTEL_PHOENIX_PROJECT, Endpunkt über "
                        "ZETTEL_PHOENIX_ENDPOINT — ein Eval-Lauf gehört in "
                        "sein EIGENES Projekt (WB-393), nicht ins "
                        "Alltagsprojekt. Ohne laufendes Phoenix läuft die "
                        "Probe trotzdem durch; die Spans gehen dann verloren.")
    args = p.parse_args()

    if args.achsen:
        achsen_zeigen()
        return 0
    if not args.db:
        print("--db fehlt (die KOPIE der Datenbank).")
        return 2
    if Path(args.db).resolve() == Path(db.DEFAULT_DB).resolve():
        print("Nicht auf data/picknick.db proben — erst kopieren "
              "(sqlite3 VACUUM INTO).")
        return 2

    gerichte = GERICHTE[:args.gerichte]
    if args.nur:
        gewuenscht = {n.casefold() for n in args.nur}
        gerichte = [g for g in GERICHTE if g[0].casefold() in gewuenscht]
        if not gerichte:
            print(f"Kein Gericht heisst so: {', '.join(args.nur)}")
            return 2
    con = db.connect(args.db)
    db.migrate(con)
    n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                    ).fetchone()["n"]
    con.close()
    zustand = wake.zustand()
    print(f"Katalog: {n} aktive Produkte in {args.db}")
    print(f"Box: {zustand.zustand} {zustand.modell or ''}")
    print(f"Gerichte: {len(gerichte)} über "
          f"{len(set(g[1] for g in gerichte))} Achsen\n")
    if not zustand.bedient:
        print("Die Box bedient nicht — erst `wake-vllm`.")
        return 1
    if not args.messen:
        print("Nichts zu tun — `--messen` oder `--achsen` angeben.")
        return 2
    if args.trace:
        # `einrichten()` wirft nie: fehlt Phoenix oder das Paket, läuft die
        # Probe ohne Trace weiter — genau die Bedingung aus WB-395. Erst ab
        # hier, nicht per Vorgabe: ohne den Schalter soll ein Messlauf das
        # Alltagsprojekt „Zettel Agent" nicht mit 60 Gerichten fluten.
        if obs.einrichten() is not None:
            stand = obs.tracerstand()
            print(f"Trace: Projekt {stand['projekt']!r} auf {stand['endpunkt']}")
        else:
            print("Trace: nicht eingerichtet — die Probe läuft ohne "
                  "(Grund steht im Log, z. B. ZETTEL_TRACING=0).")

    t0 = time.monotonic()
    zustaende: dict[str, str] = {}
    if not args.ohne_vorwaermen:
        print("=" * 74)
        print("PHASE A — Chefkoch, seriell, mit Pause")
        print("=" * 74)
        zustaende = vorwaermen(args.db, gerichte)
        print(f"\n  {time.monotonic() - t0:.0f} s\n")

    print("=" * 74)
    print("PHASE B — der ganze Zug, je Gericht auf eigener Arbeitskopie")
    print("=" * 74)
    ordner = Path(args.db).resolve().parent / "breite_probe_kopien"
    ordner.mkdir(parents=True, exist_ok=True)
    zugang = Modellzugang()
    sperre, kopiersperre = Lock(), Lock()
    t1 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        ergebnisse = list(pool.map(
            lambda g: _lauf(g, args.db, ordner, zugang, sperre, kopiersperre),
            gerichte))
    dauer = time.monotonic() - t1
    try:
        ordner.rmdir()
    except OSError:
        pass

    if args.json:
        Path(args.json).write_text(
            json.dumps(ergebnisse, ensure_ascii=False, indent=1,
                       default=str), encoding="utf-8")
        print(f"\n  Rohdaten: {args.json}")
    bericht(ergebnisse, zustaende, dauer)
    if args.trace:
        # Der Export läuft in einem Hintergrund-Thread (`NichtBlockierend`);
        # ohne Warten endete das Skript vor den letzten Spans.
        obs.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
