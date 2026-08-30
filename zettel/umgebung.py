"""Die Umgebungsvariablen — und der Rückfall auf ihre alten Namen (WB-401).

Das Projekt hiess bis zum 30.08.2026 „Picknick" und heisst seither „Zettel":
„Picnic" ist ein echter Lebensmittel-Lieferdienst, auch in Deutschland, und
dieses Repo wird öffentlich. Mit dem Namen sind zwölf Umgebungsvariablen
gewandert, `PICKNICK_*` -> `ZETTEL_*`.

**Die alten Namen bleiben gültig.** Nicht aus Bequemlichkeit, sondern weil
sonst ein laufender Haushalt bricht: die echte Adresse der vLLM-Box steht in
einer gitignorten Datei auf genau einer Maschine (WB-388), und ein Umbau im
Repo erreicht sie nicht. Ein Shop, der nach einem Neustart plötzlich gegen
`localhost:8000` redet, weil die Variable jetzt anders heisst, sagt nicht
„falsch konfiguriert" — er sagt „Modell nicht erreichbar" und sieht aus wie
eine schlafende Box. Genau diese Sorte Fehler kostet Stunden.

Gelesen wird deshalb in dieser Reihenfolge:

1. `ZETTEL_X` aus der Prozessumgebung,
2. `PICKNICK_X` aus der Prozessumgebung — mit **einer** Warnung im Log,
3. dasselbe Paar in der privaten Datei (`zettel.env`, sonst `picknick.env`),
   siehe `llm.client`,
4. die Vorgabe im Code.

Der neue Name schlägt den alten immer. Wer beide setzt, bekommt den neuen und
keine Warnung: das ist der Zustand während einer Umstellung und kein Fehler.

## Wie lange der Rückfall bleibt

**Bis zum 01.03.2027**, danach darf er ersatzlos weg (`RUECKFALL_BIS`).

Sechs Monate, und die Zahl ist nicht gewürfelt. Sie muss zwei Dinge
überdauern: den GTC-Auftritt samt der Pause danach, in der niemand am Projekt
arbeitet — und mindestens einen vollständigen Neustart jeder Maschine, die
diesen Shop betreibt. Kürzer wäre eine Wette darauf, dass der Nutzer die
Warnung liest, bevor er zwei Monate nicht hinsieht. Länger wäre ein alter
Name, der in einem öffentlichen Repo dauerhaft mitläuft — und genau den
loszuwerden ist der Zweck der Umbenennung.

Wegräumen heisst dann: dieses Modul auf `umgebung.get()` eindampfen, die
`ALT_*`-Konstanten löschen, `picknick.env` aus `.gitignore` und dem Reader in
`llm.client` nehmen, den Rollen-Cookie-Rückfall in `web.app` nehmen und die
Rückfall-Absätze aus README und OBSERVABILITY streichen.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

#: Der heutige und der alte Namensanfang. Alle zwölf Variablen unterscheiden
#: sich in nichts weiter — deshalb wird der alte Name abgeleitet und nicht
#: als zweite Liste gepflegt, die auseinanderlaufen kann.
PRAEFIX = "ZETTEL_"
ALT_PRAEFIX = "PICKNICK_"

#: Ab hier darf der Rückfall verschwinden. Steht als Datum und nicht als
#: „irgendwann": ein Übergang ohne Ende ist keiner.
RUECKFALL_BIS = "2027-03-01"

#: Die privaten Betriebswerte im Projektverzeichnis (WB-388). Gelesen wird die
#: neue Datei, und wenn es sie nicht gibt, die alte — dieselbe Begründung wie
#: bei den Variablennamen. Nur `llm.client` benutzt sie; die Pfade stehen hier,
#: damit es genau eine Stelle gibt, die die beiden Namen kennt.
WURZEL = Path(__file__).resolve().parent.parent
ENV_DATEI = WURZEL / "zettel.env"
ALT_ENV_DATEI = WURZEL / "picknick.env"

#: Schon gewarnt — je Variable einmal je Prozess. Ein Web-Prozess fragt
#: `ZETTEL_HOST` beim Start und `ZETTEL_PHOENIX_ENDPOINT` bei jedem
#: Statusaufruf; eine Warnung je Aufruf wäre nach einem Tag ein Logfile aus
#: einer einzigen Zeile und nach zwei Tagen eine, die niemand mehr liest.
_gewarnt: set[str] = set()


def alt_name(name: str) -> str:
    """`ZETTEL_DB` -> `PICKNICK_DB`. Für alles andere: derselbe Name.

    Ein Name ohne unser Präfix hat keinen alten Zwilling — `PATH` oder
    `OPENAI_API_KEY` sollen hier nicht versehentlich zu `PICKNICK_PATH`
    werden.
    """
    if not name.startswith(PRAEFIX):
        return name
    return ALT_PRAEFIX + name[len(PRAEFIX):]


def warnen(alt: str, neu: str) -> None:
    """Sagt einmal je altem Namen, dass er noch gelesen wurde.

    Absichtlich über beliebige Namenspaare und nicht nur über Variablen:
    `llm.client` meldet damit auch die alte DATEI (`picknick.env`), und zwei
    Formulierungen für dieselbe Sache wären zwei Wahrheiten.
    """
    if alt in _gewarnt:
        return
    _gewarnt.add(alt)
    log.warning(
        "%s wird noch gelesen, heisst aber %s (WB-401: das Projekt heisst "
        "seit dem 30.08.2026 „Zettel“). Der alte Name gilt noch bis %s "
        "— bitte umstellen.", alt, neu, RUECKFALL_BIS)


def wert(name: str, umgebung=None) -> str | None:
    """Der Wert von `name`, ersatzweise der seines alten Namens — sonst `None`.

    `None` heisst „nicht gesetzt" und ein leerer String heisst „gesetzt, aber
    leer". Der Unterschied trägt: `ZETTEL_HOST=""` ist ein
    Konfigurationsfehler, den `web.app` mit einem Fehler beantwortet und nicht
    stillschweigend zur Vorgabe glättet. Deshalb wird hier auf `is not None`
    geprüft und nicht auf Wahrheitswert.
    """
    umgebung = os.environ if umgebung is None else umgebung
    gefunden = umgebung.get(name)
    if gefunden is not None:
        return gefunden
    alt = alt_name(name)
    if alt == name:
        return None
    gefunden = umgebung.get(alt)
    if gefunden is not None:
        warnen(alt, name)
    return gefunden
