"""Ein Datei-Upload aus einem HTML-Formular, von Hand zerlegt (WB-344).

**Warum von Hand.** Aus demselben Grund, aus dem `app.eingaben()` schon den
urlencodierten Rumpf selbst liest: `python-multipart` steht nicht in Spec 15,
und Starlette 1.6 verweigert `request.form()` ohne dieses Paket — gemessen am
2026-08-28, sogar für Formulare, die es selbst zerlegen könnte. Es blieben drei
Wege: eine Abhängigkeit dazunehmen, den Upload per JavaScript in Base64
umschreiben, oder die vierzig Zeilen hier. Der JavaScript-Weg fällt aus, weil
ein Formular ohne JavaScript dann nichts mehr hochlädt und `curl` zum Sonder-
fall würde; die Abhängigkeit fällt aus, weil die Liste in Spec 15 eine Zusage
ist und keine Empfehlung. Also von Hand.

Der Zerleger ist bewusst klein und deckt genau ab, was Browser und `curl -F`
schicken. Was er NICHT kann, steht bei `zerlege()` — und zwar als Liste, nicht
als Fussnote.
"""
from __future__ import annotations

import re

#: `filename*=UTF-8''…` (RFC 2231) wird bewusst nicht ausgewertet: Browser
#: schicken den Namen im Formular-Upload als schlichtes `filename="…"`, und der
#: Name wird ohnehin bereinigt und dient nur als Erinnerungsstütze.
_FELD = re.compile(rb'name="([^"]*)"')
_DATEI = re.compile(rb'filename="([^"]*)"')


class Teil:
    """Ein Feld aus dem Formular: Name, Dateiname (falls Datei), Inhalt."""

    def __init__(self, name: str, dateiname: str | None, inhalt: bytes):
        self.name = name
        self.dateiname = dateiname
        self.inhalt = inhalt

    @property
    def ist_datei(self) -> bool:
        return self.dateiname is not None


def grenze(content_type: str | None) -> bytes | None:
    """Die Grenzzeichenkette aus dem `Content-Type` — oder `None`.

    `None` heisst: das ist kein Formular-Upload. Die Route soll das als
    Fehlbedienung melden und nicht raten.
    """
    if not content_type:
        return None
    typ, _, rest = content_type.partition(";")
    if typ.strip().lower() != "multipart/form-data":
        return None
    for stueck in rest.split(";"):
        schluessel, _, wert = stueck.partition("=")
        if schluessel.strip().lower() == "boundary":
            wert = wert.strip().strip('"')
            if wert:
                return wert.encode("latin-1", "replace")
    return None


def zerlege(rumpf: bytes, grenzwert: bytes) -> list[Teil]:
    """Zerlegt einen `multipart/form-data`-Rumpf in seine Teile.

    Getrennt wird an `--<grenze>`. Das ist erlaubt und nicht bloss bequem: die
    Grenze ist per Definition eine Zeichenfolge, die im Inhalt nicht vorkommt —
    dafür wählt der Absender sie aus. Käme sie doch vor, wäre der Rumpf selbst
    kaputt und jeder andere Zerleger ebenso ratlos.

    Nicht abgedeckt, weil ein Formular-Upload es nicht schickt: verschachteltes
    `multipart/mixed`, `Content-Transfer-Encoding: base64`, RFC-2231-Namen.
    Ein Teil ohne verwertbaren Kopf wird übergangen statt geraten.
    """
    teile: list[Teil] = []
    trenner = b"--" + grenzwert
    for stueck in rumpf.split(trenner)[1:]:
        if stueck.startswith(b"--"):
            break                              # Schlussgrenze, danach nur Epilog
        # Nach der Grenze steht ein Zeilenende, dann kommen die Köpfe.
        if stueck.startswith(b"\r\n"):
            stueck = stueck[2:]
        elif stueck.startswith(b"\n"):
            stueck = stueck[1:]
        koepfe, trennung, inhalt = stueck.partition(b"\r\n\r\n")
        if not trennung:
            koepfe, trennung, inhalt = stueck.partition(b"\n\n")
        if not trennung:
            continue
        # Das Zeilenende VOR der nächsten Grenze gehört zur Grenze, nicht zum
        # Inhalt. Ohne diese zwei Zeilen hätte jede hochgeladene Datei zwei
        # Bytes zu viel — einem PDF sieht man das nicht an, einer Prüfsumme
        # schon.
        if inhalt.endswith(b"\r\n"):
            inhalt = inhalt[:-2]
        elif inhalt.endswith(b"\n"):
            inhalt = inhalt[:-1]

        treffer_feld = _FELD.search(koepfe)
        if not treffer_feld:
            continue
        treffer_datei = _DATEI.search(koepfe)
        teile.append(Teil(
            treffer_feld.group(1).decode("utf-8", "replace"),
            (treffer_datei.group(1).decode("utf-8", "replace")
             if treffer_datei else None),
            inhalt))
    return teile


def datei(rumpf: bytes, content_type: str | None,
          feld: str = "datei") -> Teil | None:
    """Der erste Datei-Teil mit diesem Feldnamen — oder `None`."""
    grenzwert = grenze(content_type)
    if grenzwert is None:
        return None
    for teil in zerlege(rumpf, grenzwert):
        if teil.name == feld and teil.ist_datei:
            return teil
    return None
