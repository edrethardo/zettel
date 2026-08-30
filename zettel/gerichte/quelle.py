"""Die Gerichtequelle, wie der Chat sie sieht (WB-338, umgebaut in WB-367).

Zwei Dinge, und beide gehen über die Datenbank:

1. **Lesen** — `bereit()` und `gericht()` fragen den Zwischenspeicher. Ein
   Treffer dort kostet kein Netz, und das ist der Normalfall, sobald ein
   Gericht einmal geholt wurde.
2. **Holen** — `holen()` ruft Chefkoch AB, jetzt, in dem Prozess, der gerade
   fragt. Mit kurzer Frist, und was dabei herauskommt (Rezept, „kennt
   Chefkoch nicht", Störung), steht danach in `dish`.
3. **Wählen** — `waehlen()` tauscht das Rezept eines Gerichts gegen ein
   anderes aus DERSELBEN Suchantwort (WB-387). Das kostet eine Anfrage (das
   Detail) oder gar keine, aber nie eine zweite Suche.

**Warum das ein Umbau ist und keine Ausgangslage.** WB-338 baute genau hier
einen Riegel ein: dieses Modul kannte keine URL, trug nur einen WUNSCH ein
und startete `python -m zettel.gerichte.lauf` als eigenen Prozess — weil
Spec 3 sagte, der Web-Prozess rufe nie eine fremde Seite auf. Der Preis stand
im Entwurf und war echt: **der erste Satz zu einem neuen Gericht wurde noch
geraten**, erst der zweite bekam das Rezept.

Gemessen am 2026-08-28 trägt die Begründung nicht:

    Chili con Carne   Suche 131 ms + Detail 16 ms =  147 ms
    Kartoffelsalat    Suche  92 ms + Detail 17 ms =  109 ms
    Sushi             Suche  70 ms + Detail 20 ms =   90 ms
    Ratatouille       Suche  93 ms + Detail 20 ms =  114 ms
    ---------------------------------------------------------
    der Weg, der stattdessen genommen wurde: 35.600 ms Modell

Rund 250-mal schneller als das Ausweichen — und der Chat wartet in derselben
Sekunde ohnehin 20 bis 35 s auf die vLLM-Box, also auf eine andere Maschine
im Netz. Ein Request, der auf ein Modell warten darf, aber nicht 100 ms auf
ein Rezept, ist nicht vorsichtig, sondern inkonsequent.

**Was von Spec 3 bleibt, ist der Teil, der das Produkt trägt:** der KATALOG
wird nie live abgefragt. Suchen, Blättern, Einlegen, Abhaken, die Pick-Liste
— nichts davon fasst je das Netz an, und ein Ausfall von knuspr.de verhindert
kein Einkaufen. Fällt Chefkoch aus oder kennt das Gericht nicht, bricht auch
hier nichts: es bleibt beim Modellweg, dann aber als bewusstes Ausweichen und
nicht als Regelfall.

**Die Sperre gegen doppelte Abrufe ist die Zeile in `dish`** und kein Merker
im Speicher: zwei Web-Prozesse teilen sich keinen Merker, aber sehr wohl die
Datenbank. `holen()` trägt den Wunsch ein, BEVOR es abruft — ein zweiter Zug
zum selben Gericht sieht dann einen frischen `offen`-Eintrag und ruft nicht
noch einmal ab.
"""
from __future__ import annotations

import sqlite3
import time

from zettel.gerichte import chefkoch, lauf, speicher


def nicht_holen(con, name, **_) -> None:
    """Ein Abruf, der nichts abruft.

    Für alles, was ohne Quelle auskommen soll — Tests, Evals, ein Shop, dem
    jemand den Abruf abgedreht hat. `Quelle(holer=nicht_holen)` liest den
    Zwischenspeicher weiter; bereits geholte Gerichte werden also bedient,
    neue nicht mehr geholt.
    """
    return None


class Quelle:
    """Die Gerichtequelle, wie der Chat sie sieht.

    Alles Injizierbare an einer Stelle: `holer` (was abruft), `waehler` (was
    ein vom Menschen gewähltes Rezept nachholt, WB-387), `uhr` (wovon „zu
    alt" abhängt) und `frist_s` (wie lange gewartet wird). Ein Test schiebt
    einen Holer unter, der gegen die aufgezeichneten Antworten arbeitet oder
    eine Zeitüberschreitung spielt — **kein Test geht ins Netz** (Spec 13).
    """

    def __init__(self, *, holer=None, waehler=None, uhr=time.time,
                 frist_s: float = chefkoch.TIMEOUT_SYNC_S):
        # `None` bleibt `None` und wird erst beim Abruf zu `lauf.hole_jetzt`
        # aufgelöst. Sonst hinge in jeder `Quelle` das Funktionsobjekt vom
        # Zeitpunkt ihres Baus ab — und die Sperre der Testsuite, die genau
        # diese Funktion ersetzt, griffe je nach Reihenfolge oder nicht.
        self._holer = holer
        # Der zweite Eingang (WB-387): ein Mensch wählt ein anderes Rezept
        # aus derselben Suchantwort. Ein eigener Einspritzpunkt, weil er
        # etwas anderes tut als `holer` — EINE Anfrage statt zweier, und
        # ausdrücklich keine Suche.
        self._waehler = waehler
        self._uhr = uhr
        self._frist_s = frist_s

    # -- Lesen (kein Netz) ------------------------------------------------

    def bereit(self, con: sqlite3.Connection) -> list[dict]:
        """Die Gerichte, die ohne Netz bedient werden können."""
        return speicher.bereit(con, self._uhr)

    def gericht(self, con: sqlite3.Connection, name: str) -> dict | None:
        """Ein gespeichertes Gericht samt Zutaten, oder `None`."""
        return speicher.gericht(con, name, self._uhr)

    def zeile(self, con: sqlite3.Connection, name: str):
        """Die `dish`-Zeile zu einem Gerichtsnamen, oder `None`."""
        return speicher.zeile(con, name)

    def treffer(self, con: sqlite3.Connection, dish_id: int) -> list[dict]:
        """Die Rezepte, die zu diesem Gericht zur Wahl stehen (WB-387)."""
        return speicher.treffer(con, dish_id)

    def angeboten(self, con: sqlite3.Connection, dish_id: int,
                  source_id: str) -> dict | None:
        """Der angebotene Treffer zu dieser Rezept-ID, oder `None`."""
        return speicher.angeboten(con, dish_id, source_id)

    # -- Holen (jetzt, mit Frist) -----------------------------------------

    def holen(self, con: sqlite3.Connection, name: str) -> str | None:
        """Holt dieses Gericht, wenn nötig. Wartet — kurz (WB-367).

        Gibt den Zustand des Abrufs zurück: `ok`, `leer` (Chefkoch kennt das
        Gericht nicht) oder `fehler` (Störung oder Zeitüberschreitung).
        `None` heisst „dieser Zug hat gar nicht abgerufen": kein Name, oder
        im Speicher steht schon ein frischer Eintrag — ein Rezept, ein
        gemerktes „kennt Chefkoch nicht", eine gemerkte Störung oder ein
        Abruf, der gerade in einem anderen Zug läuft.

        Wirft nicht. Was hier schiefgeht, wird als `fehler` vermerkt und
        kostet die Abkürzung, nicht den Chat-Zug.
        """
        frage = " ".join((name or "").split())
        if not frage:
            return None
        vorhanden = speicher.zeile(con, frage)
        if vorhanden is not None and speicher.frisch(vorhanden, self._uhr):
            return None

        # Der Wunsch steht VOR dem Abruf in der Tabelle, und das ist die
        # Sperre: ein zweiter Zug zu demselben Gericht sieht ab hier einen
        # frischen `offen`-Eintrag und ruft nicht noch einmal ab.
        speicher.wunsch(con, frage, self._uhr)
        holer = self._holer if self._holer is not None else lauf.hole_jetzt
        try:
            return holer(con, frage, frist_s=self._frist_s)
        except Exception as e:                   # noqa: BLE001 — bewusst breit
            # Ein Chat-Zug darf an einem kaputten Abruf nicht zerbrechen.
            # Vermerkt wird trotzdem, sonst stünde die Zeile eine Weile auf
            # `offen` und niemand wüsste, warum nichts kommt.
            speicher.vermerken(con, frage, speicher.FEHLER,
                               f"{type(e).__name__}: {e}", self._uhr)
            return speicher.FEHLER

    def waehlen(self, con: sqlite3.Connection, name: str,
                treffer: dict) -> str:
        """Ein anderes Rezept aus DERSELBEN Suchantwort (WB-387).

        `treffer` muss aus `angeboten()` stammen — was nicht angeboten wurde,
        wird auch nicht geholt.

        **Es wird nicht noch einmal gesucht.** Die zwölf Treffer liegen seit
        dem ersten Abruf im Speicher; fehlt nur das Detail des gewählten
        Rezepts, und das ist genau eine Anfrage. Ist dieses Rezept schon
        einmal geholt worden — weil jemand hin und her gewechselt hat —,
        kostet die Wahl gar keine.

        Gibt `ok` oder `fehler` zurück und wirft nicht: was hier schiefgeht,
        kostet den Wechsel und nicht den Chat-Zug. Der Zwischenspeicher bleibt
        dabei unangetastet — das bisherige Rezept steht weiter da.
        """
        frage = " ".join((name or "").split())
        if not frage or not treffer:
            return speicher.FEHLER
        vorhanden = speicher.rezept_zur_quelle(con, treffer["rezept_id"])
        if vorhanden is not None:
            # Schon geholt. Dann ist der Wechsel eine Zeile in `dish` und
            # keine Anfrage — hin und zurück kostet nichts.
            speicher.zeigt_auf(con, frage, int(vorhanden["id"]), self._uhr)
            return speicher.OK
        waehler = self._waehler if self._waehler is not None \
            else lauf.waehle_jetzt
        try:
            return waehler(con, frage, treffer, frist_s=self._frist_s)
        except Exception as e:                   # noqa: BLE001 — bewusst breit
            # Wie in `holen()`: ein Chat-Zug darf an einem kaputten Abruf
            # nicht zerbrechen. Vermerkt wird hier aber NICHTS — anders als
            # beim ersten Abruf steht ein gültiges Rezept in `dish`, und ein
            # `fehler` darüber nähme dem Nutzer für einen Fehlgriff der
            # Quelle auch noch das, was er schon hatte.
            del e
            return speicher.FEHLER
