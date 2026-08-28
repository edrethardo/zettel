"""Bons auslesen, ohne den Request-Pfad zu blockieren (WB-358, Spec 3 und 11).

Der Shop ruft keine fremde Seite auf. Er startet aber lokale Programme
(`pdftotext`, `tesseract`) und fragt die vLLM-Box im LAN, und beides kann
dauern: OCR auf einem 1284 × 10131 grossen Screenshot liegt im zweistelligen
Sekundenbereich, ein Modellaufruf über zwanzig Bon-Zeilen ebenso, und eine
schlafende Box braucht gemessene 96 s, bis sie bedient. Eine Route, die darauf
wartet, hält den Prozess fest und läuft am Telefon in einen Timeout, den
niemand erklären kann.

Deshalb dieselbe Bauart wie beim Wecken (`picknick.llm.wake.Wecker`): der
Request STARTET etwas und kehrt sofort zurück, die Oberfläche fragt den Stand
nach. Eine Instanz je Prozess, denn nur dort lässt sich „höchstens ein Lauf je
Bon" durchsetzen — zwei gleichzeitige Läufe über dieselbe Datei schrieben
denselben Beleg zweimal.

**Was dieses Modul ausdrücklich nicht ist:** eine Aufgabenschlange, die einen
Neustart übersteht. Ein Lauf lebt im Prozess; startet der Web-Prozess neu, ist
er vergessen und der Bon steht wieder als „noch nicht ausgelesen" da. Das ist
der richtige Verlust: ein halb geschriebener Beleg wäre schlimmer, und
`kaeufe.anlegen()` schreibt in einem Zug.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

#: Was ein Lauf gerade tut.
LAEUFT = "laeuft"
FERTIG = "fertig"
FEHLER = "fehler"

#: Wie lange die Oberfläche zwischen zwei Nachfragen wartet. Zwei Sekunden:
#: kurz genug, dass ein PDF-Lauf sofort fertig aussieht, lang genug, dass ein
#: dreiminütiger OCR-Lauf keine hundert Anfragen erzeugt.
NACHFRAGE_S = 2


@dataclass(frozen=True)
class Stand:
    """Der Stand eines Laufs — genau das, was die Seite anzeigen soll."""
    datei: str
    zustand: str
    schritt: str = ""
    meldung: str = ""
    receipt_id: int | None = None
    seit_s: float = 0.0

    @property
    def laeuft(self) -> bool:
        return self.zustand == LAEUFT

    @property
    def fehler(self) -> bool:
        return self.zustand == FEHLER


class Laeufe:
    """Die laufenden und die zuletzt beendeten Bon-Läufe dieses Prozesses.

    `uhr` und `starter` sind injizierbar, damit ein Test den Lauf synchron
    ausführen kann: ein Test, der auf einen Thread wartet, ist ein Test, der
    irgendwann flackert.
    """

    def __init__(self, uhr=time.monotonic, starter=None):
        self._uhr = uhr
        self._starter = starter or _im_thread
        self._sperre = threading.Lock()
        self._staende: dict[str, Stand] = {}
        self._beginn: dict[str, float] = {}

    # -- Abfragen ---------------------------------------------------------

    def stand(self, datei: str) -> Stand | None:
        """Der Stand zu einer Datei, oder `None`, wenn nie einer lief."""
        with self._sperre:
            s = self._staende.get(datei)
            if s is None:
                return None
            return replace(s, seit_s=self._uhr() - self._beginn.get(datei, 0.0))

    def laeuft(self, datei: str) -> bool:
        s = self.stand(datei)
        return s is not None and s.laeuft

    def vergiss(self, datei: str) -> None:
        """Wirft einen beendeten Stand weg — z. B. wenn der Bon gelöscht wird.

        Ein laufender Lauf wird NICHT vergessen: er würde weiterlaufen und
        anschliessend in einen Stand schreiben, den niemand mehr erwartet.
        """
        with self._sperre:
            s = self._staende.get(datei)
            if s is not None and not s.laeuft:
                self._staende.pop(datei, None)
                self._beginn.pop(datei, None)

    # -- Starten ----------------------------------------------------------

    def starte(self, datei: str, arbeit) -> Stand:
        """Startet `arbeit(melde)` im Hintergrund und gibt sofort zurück.

        `arbeit` bekommt eine Funktion `melde(text)`, mit der sie ihren
        Schritt bekanntgibt („liest die Datei", „ordnet dem Katalog zu"), und
        gibt `(receipt_id, meldung)` zurück. Eine Ausnahme daraus wird zum
        Zustand `FEHLER` mit ihrem Text — der Thread stirbt nicht still.

        Läuft für diese Datei schon etwas, wird NICHT ein zweites Mal
        gestartet; zurück kommt der Stand des laufenden.
        """
        with self._sperre:
            vorhanden = self._staende.get(datei)
            if vorhanden is not None and vorhanden.laeuft:
                return replace(vorhanden,
                               seit_s=self._uhr() - self._beginn[datei])
            self._beginn[datei] = self._uhr()
            self._staende[datei] = Stand(datei=datei, zustand=LAEUFT,
                                         schritt="wird vorbereitet")
        self._starter(lambda: self._ausfuehren(datei, arbeit))
        return self.stand(datei)

    def _ausfuehren(self, datei: str, arbeit) -> None:
        def melde(text: str) -> None:
            with self._sperre:
                s = self._staende.get(datei)
                if s is not None and s.laeuft:
                    self._staende[datei] = replace(s, schritt=text)

        try:
            receipt_id, meldung = arbeit(melde)
        except Exception as e:  # noqa: BLE001
            # Absichtlich alles: dieser Thread hat niemanden über sich, der
            # eine Ausnahme noch anzeigen könnte. Was hier nicht gefangen
            # wird, verschwindet in einem Traceback auf stderr, und die Seite
            # zeigt für immer „läuft".
            text = str(e) or e.__class__.__name__
            with self._sperre:
                self._staende[datei] = Stand(datei=datei, zustand=FEHLER,
                                             meldung=text)
            return
        with self._sperre:
            self._staende[datei] = Stand(datei=datei, zustand=FERTIG,
                                         meldung=meldung or "",
                                         receipt_id=receipt_id)


def _im_thread(fn) -> None:
    """Ein Daemon-Thread. `daemon=True`, damit ein Neustart nicht wartet.

    Der Lauf schreibt seinen Beleg in einem einzigen `INSERT`-Block
    (`kaeufe.anlegen`); ein abgeschnittener Lauf hinterlässt deshalb entweder
    einen vollständigen Beleg oder gar keinen.
    """
    threading.Thread(target=fn, daemon=True).start()


def sofort(fn) -> None:
    """Ein `starter`, der die Arbeit im aufrufenden Thread erledigt.

    Für Tests und für Skripte, die ohnehin nichts anderes tun. Damit ist der
    Stand nach `starte()` bereits `FERTIG` oder `FEHLER`, und kein Test muss
    auf einen Thread warten.
    """
    fn()
