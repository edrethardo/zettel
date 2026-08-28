"""Der Riegel zwischen dem Shop und einer fremden Seite (WB-338, Spec 3).

**Der Web-Prozess ruft nie eine fremde Seite auf.** Das ist keine Absicht,
die man vergessen kann, sondern hier eine Struktur: dieses Modul importiert
kein `httpx` und kennt keine URL. Es tut genau zwei Dinge:

1. Es trägt einen WUNSCH in `dish` ein (`speicher.wunsch`).
2. Es startet `python -m picknick.gerichte.lauf` als EIGENEN PROZESS und
   kehrt sofort zurück.

Der Abruf läuft damit hinter demselben Riegel wie der Katalog-Crawler — ein
eigener Prozess, der ins Netz geht, während der Shop es nie tut. Und er läuft
nicht im Request-Pfad: `anfordern()` wartet nicht, der laufende Chat-Zug geht
in der Zwischenzeit den Modellweg weiter (`picknick.bons.lauf` ist dafür das
Vorbild — starten und sofort zurückkehren, den Stand nachfragen).

**Der zweite Zug ist der, der zählt.** „alles für Pho" beim ersten Mal geht
noch übers Modell und sagt das auch; zwei Sekunden später steht das Rezept
im Speicher und jeder weitere Satz mit „Pho" darin nimmt es. Das ist der
Preis dafür, dass niemand im Request auf eine fremde Seite wartet — und es
ist derselbe Handel wie beim Wecken der Modellbox (Spec 6): anstossen,
zurückgeben, gleich nochmal fragen.

Fällt der Start schief (kein Dateipfad zur Datenbank, kein Python, was auch
immer), passiert nichts weiter: der Wunsch steht in der Tabelle und der
nächste Lauf ohne Argument holt ihn nach. **Nichts davon darf den Chat
zerbrechen** — der ganze Startvorgang liegt deshalb in einem `try`.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from picknick.gerichte import speicher

#: Das Modul, das den Abruf macht. Als `-m`, damit derselbe Interpreter und
#: derselbe Suchpfad gelten wie im Shop.
MODUL = "picknick.gerichte.lauf"

WURZEL = Path(__file__).resolve().parents[2]


def nicht_holen(argv) -> None:
    """Ein `starter`, der nichts startet.

    Die Vorgabe für alles, was ohne Quelle auskommen soll — Tests, Evals,
    ein Shop, dem jemand den Abruf abgedreht hat. Der Wunsch steht dann
    trotzdem in `dish` und ein Lauf von Hand holt ihn.
    """


def _als_prozess(argv) -> None:
    """Startet den Lauf abgekoppelt und wartet nicht auf ihn.

    `start_new_session=True`: der Abruf soll einen Neustart des Shops
    überleben und nicht an dessen Prozessgruppe hängen. Ausgabe nach
    `DEVNULL`, weil der Lauf seinen Zustand in die Datenbank schreibt und
    nicht auf ein Terminal, das niemand liest.
    """
    subprocess.Popen(argv, cwd=str(WURZEL), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def db_pfad(con: sqlite3.Connection) -> str | None:
    """Der Dateipfad der geöffneten Datenbank, oder `None` bei `:memory:`.

    Der eigene Prozess braucht ihn, und eine sqlite-Verbindung kennt ihn:
    `PRAGMA database_list`. Ihn stattdessen durch `create_app` und `Chat`
    durchzureichen hiesse, dieselbe Angabe an drei Stellen zu führen, wo eine
    schon dasteht — und die dritte wäre eines Tages die falsche.
    """
    try:
        for _, name, datei in con.execute("PRAGMA database_list"):
            if name == "main":
                return datei or None
    except sqlite3.Error:
        return None
    return None


class Quelle:
    """Die Gerichtequelle, wie der Chat sie sieht.

    Alles Injizierbare an einer Stelle: `starter` (was den Lauf startet) und
    `uhr` (wovon „zu alt" abhängt). Ein Test schiebt einen Starter unter, der
    mitschreibt oder den Abruf synchron gegen eine Fixture fährt — kein Test
    startet einen Prozess und keiner geht ins Netz (Spec 13).

    `Quelle(starter=nicht_holen)` schaltet den Abruf ab, ohne den Speicher
    abzuschalten: bereits geholte Gerichte werden weiter bedient.
    """

    def __init__(self, *, starter=None, uhr=time.time,
                 python: str | None = None):
        self._starter = starter if starter is not None else _als_prozess
        self._uhr = uhr
        self._python = python or sys.executable

    # -- Lesen (kein Netz, kein Prozess) ----------------------------------

    def bereit(self, con: sqlite3.Connection) -> list[dict]:
        """Die Gerichte, die ohne Netz bedient werden können."""
        return speicher.bereit(con, self._uhr)

    def gericht(self, con: sqlite3.Connection, name: str) -> dict | None:
        """Ein gespeichertes Gericht samt Zutaten, oder `None`."""
        return speicher.gericht(con, name, self._uhr)

    # -- Anfordern (ein eigener Prozess, kein Warten) ---------------------

    def anfordern(self, con: sqlite3.Connection, name: str) -> bool:
        """Sorgt dafür, dass dieses Gericht geholt wird. Wartet NICHT.

        Gibt zurück, ob dieser Aufruf einen Lauf angestossen hat. `False`
        heisst: brauchte es nicht (liegt schon vor, wurde gerade erst
        versucht, läuft bereits) oder ging nicht (kein Dateipfad) — in beiden
        Fällen ist nichts kaputt, der Aufrufer geht den bisherigen Weg.

        **Die Sperre gegen doppelte Läufe ist die Zeile in `dish`** und kein
        Merker im Speicher: zwei Web-Prozesse teilen sich keinen Merker, aber
        sehr wohl die Datenbank.
        """
        frage = " ".join((name or "").split())
        if not frage:
            return False
        vorhanden = speicher.zeile(con, frage)
        if vorhanden is not None and speicher.frisch(vorhanden, self._uhr):
            # Frisch heisst hier je nach Zustand: liegt vor, kennt Chefkoch
            # nicht, ist gerade schiefgegangen, oder wird gerade geholt. In
            # allen vier Fällen wäre eine zweite Anfrage an eine fremde Seite
            # umsonst.
            return False

        pfad = db_pfad(con)
        if not pfad:
            # `:memory:` — ein eigener Prozess sähe eine leere Datenbank.
            return False
        speicher.wunsch(con, frage, self._uhr)
        try:
            self._starter([self._python, "-m", MODUL, "--db", pfad,
                           "--gericht", frage])
        except Exception:                        # noqa: BLE001 — bewusst breit
            # Ein Chat-Zug darf an einem fehlgeschlagenen Prozessstart nicht
            # zerbrechen. Der Wunsch steht in der Tabelle; ein Lauf ohne
            # Argument holt ihn nach.
            return False
        return True
