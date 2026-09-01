"""Die vLLM-Box wecken, ohne den Request zu blockieren (Spec 6 und 11).

Die Box ist ein eigener Rechner im LAN und **suspendiert nach etwa 120 Minuten
Leerlauf**. Daraus folgt alles, was dieses Modul tut:

* **Eine schlafende und eine tote Box sehen vom Client aus identisch aus.**
  „Connection refused", „No route to host", eine Anfrage, die nichts
  zurückgibt — das ist der normale Ruhezustand, kein Fehler. Deshalb behauptet
  dieses Modul nirgends, die Box sei „aus". Es sagt „bedient", „wacht auf"
  oder „nicht erreichbar", und das letzte erst, wenn ein Weckversuch
  tatsächlich gelaufen und fehlgeschlagen ist.
* **Wecken dauert.** Gemessen: 96 s von kalt bis bedienbar, in zwei Wartezeiten
  — Resume der Maschine, dann vLLM, das die Gewichte lädt. Der Web-Prozess
  kann darauf nicht warten, also wird `wake-vllm` im Hintergrund gestartet und
  der Aufrufer bekommt sofort einen Zustand zurück, aus dem die Oberfläche
  „Modell wacht auf … ~90 s" mit laufendem Zähler bauen kann.
* **`wakeonlan` allein genügt nicht** und ist schlimmer als nichts: es kehrt
  sofort zurück und sagt nichts. `wake-vllm` schickt das Magic Packet *und*
  wartet, bis wirklich bedient wird. Es ist idempotent.

Fällt das Modell aus, ist nur der Chat betroffen. Suchen, Einlegen, Bestellen
und Abhaken laufen weiter (Spec 11) — dieses Modul wird deshalb nirgends beim
Start des Web-Prozesses aufgerufen.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

import httpx

from zettel import umgebung as umg
from zettel.llm.client import (KonfigurationsFehler, endpunkt_aus_umgebung,
                               schluessel_aus_umgebung)

ENV_WECKBEFEHL = "ZETTEL_WAKE_CMD"

#: Liegt in `~/.local/bin`. Als Name und nicht als Pfad, damit eine andere
#: Maschine ihn woanders haben darf; gefunden wird er über `shutil.which`.
DEFAULT_WECKBEFEHL = "wake-vllm"

# Befund von `health()` — was die Box in diesem Moment tut.
BEDIENT = "bedient"          # /v1/models antwortet mit einem Modell
LAEDT = "laedt"              # der Port nimmt an, aber vLLM ist noch nicht so weit
STILL = "still"              # niemand nimmt die Verbindung an

# Zustand von `Wecker.zustand()` — was die Oberfläche zeigen soll.
WACHT_AUF = "wacht_auf"
NICHT_ERREICHBAR = "nicht_erreichbar"

#: Was die Oberfläche als Erwartung anzeigt.
#:
#: War 90 s, gemessen 2026-08-09 gegen den nackten vLLM-Prozess (96 s). Seit
#: dem Umstieg auf den syv-Container am 2026-09-01 kommen Docker, ein
#: grösserer torch.compile-Lauf und CUDA-Graphen dazu; die Nachbarsitzung
#: nennt 3 bis 4 Minuten. Beim ersten echten Kaltstart danach (2026-09-01,
#: Demo-Instanz) lagen zwischen dem abgeschickten Satz und der bedienenden
#: Engine **5 min 35 s** — gelesen aus `created` in `/v1/models` gegen den
#: Zeitstempel des Zuges.
#:
#: **Die Zahl ist nicht sauber:** ob der Desktop zusätzlich aus S3 aufwachen
#: musste, lässt sich hinterher nicht mehr sagen, und genau das macht den
#: Unterschied zwischen 3,5 und 5,5 Minuten aus. Deshalb 300 und nicht 340:
#: der obere Wert enthält vermutlich Fremdzeit. Zu niedrig ist das schlechtere
#: Ende — der Zähler läuft dann über seine eigene Erwartung hinaus, und die
#: Oberfläche sieht kaputt aus, während alles nach Plan läuft.
WECKDAUER_S = 300.0

#: Ab hier lief der Weckruf so lange, dass er nicht mehr als „gleich soweit"
#: durchgehen kann. Doppelte Weckdauer plus Luft — die Regel bleibt, die Zahl
#: wächst mit der Weckdauer mit. Bei 300 s wäre sie nach dem Umstieg nur noch
#: das 1,25-fache eines normalen Kaltstarts gewesen: ein Lauf, der ordentlich
#: lädt, hätte als Fehlschlag gegolten.
WECKFRIST_S = 600.0

#: Nach einem fehlgeschlagenen Weckruf so lange nicht erneut wecken. Ohne das
#: startet eine Oberfläche, die jede Sekunde nachfragt, jede Sekunde einen
#: neuen Prozess.
NEUVERSUCH_S = 60.0

#: Kurz — dieser Aufruf sitzt im Request-Pfad. Die Box antwortet im LAN in
#: Millisekunden oder gar nicht.
#:
#: **Eine Sekunde und nicht mehr drei** (WB-409). Gemessen 2026-08-30 gegen
#: die echte Box, `GET /v1/models`, 15 Proben: Median 2 ms, langsamste
#: 163 ms — der erste Aufruf mit der mDNS-Auflösung. Drei Sekunden waren das
#: Tausendfache des Üblichen und wurden trotzdem VOLL bezahlt, sooft die Box
#: nicht bediente: `/chat/zustand` brauchte dann 3,07 s, bei einem Ziel von
#: unter 100 ms für alles, was kein Modell fragt.
#:
#: Eine Sekunde ist immer noch das Sechsfache der langsamsten Messung. Was
#: darüber liegt, ist nicht „langsam", sondern der socket-aktivierte
#: Endpunkt, der annimmt und dann zwei Minuten Gewichte lädt — und genau das
#: heisst `LAEDT`.
HEALTH_TIMEOUT_S = 1.0

#: So lange gilt ein Befund als frisch (WB-409). Die Oberfläche fragt alle
#: fünf Sekunden nach, und ein Chat-Zug fragt zwei- bis dreimal in derselben
#: Sekunde — für dieselbe Auskunft. Ohne diese Frist kostet jede dieser
#: Fragen eine eigene Runde zur Box, und bei einer Box, die NICHT bedient,
#: kostet sie den vollen Timeout.
#:
#: Vier Sekunden sind kürzer als der Abstand der Abfragen und viel kürzer als
#: die Weckdauer (90 s), um die es dem Zähler geht. Was hier veraltet, ist
#: höchstens eine Sekunde Anzeige — und ein Zug, der gegen eine gerade
#: gestorbene Box startet, scheitert am echten Aufruf und nicht am Befund.
HEALTH_CACHE_S = 4.0


@dataclass(frozen=True)
class Zustand:
    """Was die Oberfläche über das Modell sagen darf.

    `seit_s` und `erwartet_s` sind der Zähler: „Modell wacht auf … noch ~N s".
    `grund` ist Klartext für den ausgegrauten Chat (Spec 11) — er behauptet
    nie, die Box sei aus.
    """
    zustand: str
    modell: str | None = None
    seit_s: float | None = None
    erwartet_s: float = WECKDAUER_S
    grund: str | None = None

    @property
    def bedient(self) -> bool:
        return self.zustand == BEDIENT

    @property
    def rest_s(self) -> float | None:
        """Grobe Restzeit für den Zähler — `None`, sobald sie abgelaufen ist.

        Bis WB-378 stand hier ein `max(0.0, …)`, und damit blieb nach 90 s
        unbegrenzt „noch ~0 s" stehen. Ein Zähler, der auf null klemmt, sagt
        weniger als gar keiner: er behauptet, es sei gleich soweit, während
        die Nutzerin längst länger wartet als angekündigt. Ist die Erwartung
        durch, gibt es keine Restzeit mehr, sondern einen Satz — siehe
        `ueberfaellig`.

        Auch die letzte Sekunde zählt schon nicht mehr: `~0 s` wäre wieder
        genau die Auskunft, die keine ist.
        """
        if self.seit_s is None:
            return None
        rest = self.erwartet_s - self.seit_s
        return rest if rest >= 1.0 else None

    @property
    def ueberfaellig(self) -> bool:
        """Es läuft, dauert aber länger als angekündigt.

        Der Unterschied zu „kein Zähler": `rest_s` ist in beiden Fällen
        `None`, und die Oberfläche muss „noch ~40 s", „es dauert länger als
        sonst" und „gar keine Angabe" auseinanderhalten können.
        """
        return self.seit_s is not None and self.rest_s is None


@dataclass(frozen=True)
class Befund:
    """Das Ergebnis eines einzelnen Blicks auf `/v1/models`."""
    zustand: str
    modell: str | None = None
    grund: str | None = None


def health(endpunkt: str | None = None, *, http=None,
           timeout_s: float = HEALTH_TIMEOUT_S, umgebung=None) -> Befund:
    """Ein Blick auf `/v1/models`, mit kurzem Timeout und ohne Ausnahme.

    Unterschieden wird dasselbe, was `wake-vllm --check` unterscheidet: nimmt
    überhaupt jemand die Verbindung an (`LAEDT`) oder nicht (`STILL`). Das ist
    die einzige billige Auskunft, die es gibt — und der Unterschied
    entscheidet, ob überhaupt geweckt werden muss: bei `LAEDT` ist die Box
    längst wach und vLLM lädt nur noch.
    """
    ziel = endpunkt or endpunkt_aus_umgebung(umgebung)
    # Der Schlüssel gehört auch an die Gesundheitsprobe. Ein Endpunkt, der
    # einen verlangt, antwortet ohne ihn mit 401 — und 401 fällt unten in den
    # LAEDT-Zweig, also „vLLM lädt noch". Das ist eine Auskunft, die nie
    # umschlägt: der Shop meldete gegen den syv-Stack dauerhaft „Box wacht
    # auf", während die Box tadellos bediente (2026-09-01).
    kopf = {"Authorization": f"Bearer {schluessel_aus_umgebung(umgebung)}"}
    eigener = http is None
    client = http if http is not None else httpx.Client(timeout=timeout_s)
    try:
        antwort = client.get(f"{ziel.rstrip('/')}/models", timeout=timeout_s,
                             headers=kopf)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        # Es kam keine Verbindung zustande: abgelehnt, kein Weg zum Host, Name
        # nicht auflösbar — oder der Verbindungsversuch lief ins Leere. Der
        # Timeout gehört ausdrücklich hierher: eine suspendierte Maschine
        # antwortet nicht einmal auf ARP, und wer das als „lädt noch" deutet,
        # weckt sie nie.
        return Befund(STILL, grund=f"niemand nimmt an ({e.__class__.__name__})")
    except httpx.HTTPError as e:
        # Die Verbindung stand, aber es kam nichts Brauchbares zurück: der
        # Rechner ist wach, es bedient nur noch nichts.
        return Befund(LAEDT, grund=f"keine brauchbare Antwort ({e.__class__.__name__})")
    finally:
        if eigener:
            client.close()

    if antwort.status_code != 200:
        return Befund(LAEDT, grund=f"HTTP {antwort.status_code}")
    try:
        daten = antwort.json().get("data") or []
        kuerzel = daten[0]["id"]
    except (ValueError, LookupError, TypeError, AttributeError):
        return Befund(LAEDT, grund="Antwort ohne Modell — vLLM lädt vermutlich noch")
    return Befund(BEDIENT, modell=kuerzel)


def _starte_im_hintergrund(befehl: list[str]):
    """Startet `wake-vllm` und kehrt sofort zurück. Nie `.wait()`.

    `start_new_session`, damit der Weckvorgang nicht mit dem stirbt, der ihn
    angestossen hat — ein neu gestarteter Web-Prozess soll die halb wache Box
    nicht auf halbem Weg verlieren. Die Ausgabe geht ins Leere: interessant
    ist hier nur der Endcode, alles andere beantwortet der nächste `health()`.
    """
    return subprocess.Popen(
        befehl, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


class Wecker:
    """Hält den einen laufenden Weckvorgang und meldet den Zustand.

    Eine Instanz je Prozess (siehe `wecker()`), denn die Regel „höchstens ein
    Weckvorgang gleichzeitig" lässt sich nur dort durchsetzen, wo alle
    Requests durchmüssen. `zustand()` blockiert nie länger als der kurze
    health-Timeout.
    """

    def __init__(self, endpunkt: str | None = None, *, http=None, starter=None,
                 uhr=time.monotonic, weckbefehl: str | None = None,
                 umgebung=None):
        umgebung = os.environ if umgebung is None else umgebung
        self.endpunkt = endpunkt or endpunkt_aus_umgebung(umgebung)
        self.weckbefehl = (weckbefehl or umg.wert(ENV_WECKBEFEHL, umgebung)
                           or DEFAULT_WECKBEFEHL)
        # Beide injizierbar, damit kein Test ins Netz geht und keiner die
        # echte Box weckt.
        self._http = http
        self._starter = starter or _starte_im_hintergrund
        self._uhr = uhr
        self._sperre = threading.Lock()
        self._prozess = None
        self._beginn: float | None = None
        self._fehlschlag: float | None = None
        self._grund: str | None = None
        # Der letzte Befund und wann er entstand (WB-409).
        self._befund: Befund | None = None
        self._befund_um: float | None = None

    def zustand(self) -> Zustand:
        """Bedient / wacht auf / nicht erreichbar — und weckt, falls nötig.

        Der health-Aufruf steht bewusst ausserhalb der Sperre: er dauert bis
        zu drei Sekunden, und solange sollen sich parallele Requests nicht
        gegenseitig aufhalten. Die Entscheidung darüber, ob geweckt wird,
        steht dann vollständig innerhalb der Sperre — sonst starten zwei
        Requests zwei Weckvorgänge.
        """
        befund = self._befund_frisch()
        with self._sperre:
            if befund.zustand == BEDIENT:
                self._vergiss_weckvorgang()
                return Zustand(BEDIENT, modell=befund.modell)

            jetzt = self._uhr()
            if self._beginn is None:
                self._beginn = jetzt
            seit = jetzt - self._beginn

            if self._laeuft_noch():
                if seit > WECKFRIST_S:
                    return Zustand(
                        NICHT_ERREICHBAR, seit_s=seit,
                        grund=(f"{self.weckbefehl} läuft seit {seit:.0f} s und die "
                               "Box bedient immer noch nicht. Ein Blick auf die "
                               "Maschine selbst hilft jetzt mehr als ein weiterer "
                               "Weckruf."))
                return Zustand(WACHT_AUF, seit_s=seit,
                               grund="Weckruf läuft, vLLM lädt die Gewichte.")

            if self._prozess is not None:
                # Der Weckruf ist durch, die Box bedient trotzdem nicht.
                code = self._prozess.returncode
                self._prozess = None
                self._fehlschlag = jetzt
                self._grund = (
                    f"{self.weckbefehl} endete mit Code {code}, {self.endpunkt} "
                    "bedient trotzdem nicht. Nächster Versuch in "
                    f"{NEUVERSUCH_S:.0f} s.")
                return Zustand(NICHT_ERREICHBAR, seit_s=seit, grund=self._grund)

            if befund.zustand == LAEDT:
                # Der Port nimmt an: die Box ist wach, vLLM ist nur noch nicht
                # so weit. Ein Weckruf würde daran nichts ändern.
                return Zustand(
                    WACHT_AUF, seit_s=seit,
                    grund=f"Die Box antwortet, bedient aber noch nicht ({befund.grund}).")

            if (self._fehlschlag is not None
                    and jetzt - self._fehlschlag < NEUVERSUCH_S):
                return Zustand(NICHT_ERREICHBAR, seit_s=seit, grund=self._grund)

            return self._wecke(jetzt)

    def _befund_frisch(self) -> Befund:
        """Der letzte Blick auf die Box, wenn er jünger als `HEALTH_CACHE_S` ist.

        Steht ausserhalb der Sperre wie der `health()`-Aufruf selbst: zwei
        Requests, die gleichzeitig nachfragen, sollen sich nicht aufhalten.
        Im schlimmsten Fall messen beide — das ist eine Runde zu viel und
        keine falsche Auskunft.
        """
        jetzt = self._uhr()
        letzter, um = self._befund, self._befund_um
        if letzter is not None and um is not None \
                and jetzt - um < HEALTH_CACHE_S:
            return letzter
        befund = health(self.endpunkt, http=self._http)
        self._befund, self._befund_um = befund, jetzt
        return befund

    def _laeuft_noch(self) -> bool:
        return self._prozess is not None and self._prozess.poll() is None

    def _vergiss_weckvorgang(self) -> None:
        self._prozess = None
        self._beginn = None
        self._fehlschlag = None
        self._grund = None

    def _wecke(self, jetzt: float) -> Zustand:
        pfad = shutil.which(self.weckbefehl)
        if not pfad:
            # Kein Grund, die Box zu verdächtigen: hier fehlt ein Werkzeug.
            # Ein systemd-User-Service erbt `~/.local/bin` nicht zwingend.
            self._fehlschlag = jetzt
            self._grund = (
                f"{self.weckbefehl!r} ist im PATH nicht zu finden. Das ist ein "
                f"Konfigurationsfehler — mit {ENV_WECKBEFEHL} lässt sich der "
                "volle Pfad setzen. Über den Zustand der Box sagt es nichts.")
            return Zustand(NICHT_ERREICHBAR, seit_s=jetzt - self._beginn,
                           grund=self._grund)
        try:
            self._prozess = self._starter([pfad])
        except OSError as e:
            self._fehlschlag = jetzt
            self._grund = f"{pfad} liess sich nicht starten: {e}"
            return Zustand(NICHT_ERREICHBAR, seit_s=jetzt - self._beginn,
                           grund=self._grund)
        self._beginn = jetzt
        self._fehlschlag = None
        self._grund = None
        return Zustand(WACHT_AUF, seit_s=0.0,
                       grund=(f"Weckruf gestartet. Aus dem Schlaf dauert es "
                              f"etwa {WECKDAUER_S:.0f} s."))


_WECKER: Wecker | None = None
_WECKER_SPERRE = threading.Lock()


def wecker() -> Wecker:
    """Der eine Wecker dieses Prozesses.

    Erst beim ersten Aufruf gebaut, und der erste Aufruf gehört in den
    Chat-Pfad — nicht in den Start des Web-Prozesses. Der Shop soll ohne
    Modell hochkommen (Spec 11).
    """
    global _WECKER
    with _WECKER_SPERRE:
        if _WECKER is None:
            _WECKER = Wecker()
        return _WECKER


def zustand() -> Zustand:
    """Bequemlichkeit für den Chat-Pfad: der Zustand des gemeinsamen Weckers.

    Ein Konfigurationsfehler wird hier nicht durchgereicht, sondern als
    `NICHT_ERREICHBAR` mit Grund gemeldet: der Chat darf ausgrauen, der Rest
    des Shops darf davon nichts merken.
    """
    try:
        return wecker().zustand()
    except KonfigurationsFehler as e:
        return Zustand(NICHT_ERREICHBAR, grund=str(e))
