"""Der Phoenix-Tracer: Einrichtung, Weiterreichen, Ausfallsicherheit (Spec 7.2).

Drei Dinge stehen hier, und jedes davon ist einmal bezahlt worden:

1. **Die Einrichtung wortwörtlich aus Spec 7.2.** `batch=False`, weil der
   Batch-Prozessor bei höherer Emissionsrate Spans verschluckt (gemessen:
   6.615 von 8.408 angekommen). `set_global_tracer_provider=False`, weil der
   globale Provider sonst mit anderen Tracern im selben Prozess kollidiert.
   `auto_instrument=False`, weil genau ein Instrumentor gewollt ist und nicht
   alles, was zufällig installiert ist.

2. **Der Export darf den Request nicht aufhalten** (Spec 7.3, letzter Absatz).
   `batch=False` heisst `SimpleSpanProcessor`, und der exportiert im Thread
   des Requests. Die naheliegende Annahme — „ein abgelehnter Port scheitert
   sofort" — ist falsch, und das ist der Grund für den Prozessor unten. Der
   OTLP-Exporter behandelt „Connection refused" als vorübergehenden Fehler
   und versucht es mit wachsender Pause wieder (0,88 s, 1,83 s, 4,75 s …).
   Gemessen am 2026-08-28 gegen einen geschlossenen Port:

       ein Span  ...................................  6,44 s
       fünf Spans (= ein Chat-Zug) ................. 34,93 s
       fünf Spans durch `NichtBlockierend` .......... 0,15 ms

   Ein Chat-Zug hätte also 35 Sekunden gebraucht, nur weil Phoenix nicht
   läuft. Deshalb liegt zwischen Span und Exporter der
   `NichtBlockierend`-Prozessor: eine Schlange und ein Hintergrund-Thread.

   Das ist **kein** Batching: jeder Span geht einzeln und in Reihenfolge an
   denselben `SimpleSpanProcessor`, nichts wird zusammengefasst und nichts
   wird nach Zeitplan verworfen. Genau der Unterschied zum
   `BatchSpanProcessor`, dessen Verluste oben stehen.

3. **Die LLM-Spans heissen `plan.extract` und `plan.choose`.** Spec 7.1 will
   diese Namen, Spec 7.2 will, dass die LLM-Spans vom `OpenAIInstrumentor`
   kommen — der nennt sie aber `ChatCompletion`. Beides zusammen geht nur,
   indem der Span beim Öffnen umbenannt wird; das tut `StufenBenenner` anhand
   des Kontextes, den `stufe()` setzt.

   Der naheliegende Ausweg — einen eigenen LLM-Span um den Aufruf legen —
   wäre falsch: dann lägen zwei LLM-Spans übereinander, und Phoenix rechnet
   Kosten je LLM-Span aus Tokenzahl mal Modell. Die Kosten eines Chat-Zugs
   wären doppelt so hoch wie die echten. Aus demselben Grund schreibt dieses
   Projekt **keine** Tokenzahlen von Hand: sie kommen vom Instrumentor, der
   Eingabe-, Ausgabe- und Cache-Token getrennt hält (Spec 7.3).

Ohne Einrichtung liefert `tracer()` einen No-Op-Tracer. Das ist der Zustand in
der Testsuite und auf einem Rechner ohne Phoenix: der Chat läuft, die Spans
kosten nichts, und `spans.span_id()` gibt `None` zurück statt einer
Null-ID — eine `chat_message` ohne Span soll `NULL` tragen und nicht
`"0000000000000000"`.
"""
from __future__ import annotations

import contextvars
import logging
import os
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime

from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from zettel import umgebung as umg

log = logging.getLogger(__name__)

#: Eigenes Projekt, getrennt vom Board-Projekt `Picknick` (Spec 7): dort
#: liegt, wie dieses Projekt gebaut wurde, hier, wie es sich im Betrieb
#: verhält. Das Board heisst weiterhin so — es ist die Werkbank und nicht
#: dieses Repo (WB-401).
#:
#: Hiess bis zum 30.08.2026 `Picknick Agent`. Der Name ist in Phoenix der
#: Schlüssel des Projekts, kein Etikett: die Traces von davor liegen weiter
#: unter dem alten Namen und wandern NICHT mit (siehe OBSERVABILITY.md).
PROJEKT = "Zettel Agent"

#: Phoenix nimmt Spans über OTLP/HTTP entgegen. Der Pfad gehört dazu — ohne
#: ihn antwortet Phoenix mit 404 und der Exporter meldet einen Fehler, der wie
#: ein Netzproblem aussieht.
ENDPUNKT = "http://localhost:6006/v1/traces"

ENV_AN = "ZETTEL_TRACING"
ENV_ENDPUNKT = "ZETTEL_PHOENIX_ENDPOINT"
ENV_PROJEKT = "ZETTEL_PHOENIX_PROJECT"

#: Werte, die „aus" heissen. Alles andere (auch nichts) heisst „an": ein
#: Vorführstück für Observability, das man erst einschalten muss, wäre eines,
#: das im Zweifel nichts misst.
AUS = {"0", "aus", "off", "false", "nein", "no"}

#: Wie viele Spans warten dürfen, bevor welche verloren gehen. Bei einem Shop
#: für zwei Personen sind das rund zweitausend Chat-Züge Rückstand; wer diese
#: Grenze erreicht, hat keinen Collector, sondern ein Loch. Unbegrenzt wäre
#: die Alternative — und damit ein Speicherleck bei genau dem Ausfall, gegen
#: den der Prozessor schützen soll.
SCHLANGE = 10_000

_provider = None
_instrumentiert = False
_sperre = threading.Lock()

#: Wohin und unter welchem Namen zuletzt eingerichtet wurde, und seit wann.
#: Nur zum Anzeigen (`tracerstand`) — der Betrieb liest das nirgends.
_ziel: str | None = None
_name: str | None = None
_seit: float | None = None


# --------------------------------------------------------------------------
# Einrichtung

def an(umgebung=None) -> bool:
    """Soll getract werden? `ZETTEL_TRACING=0` schaltet ab."""
    umgebung = os.environ if umgebung is None else umgebung
    return (umg.wert(ENV_AN, umgebung) or "").strip().casefold() not in AUS


def einrichten(*, projekt: str | None = None, endpunkt: str | None = None):
    """Richtet Phoenix ein und gibt den Provider zurück (oder `None`).

    Idempotent: der zweite Aufruf gibt denselben Provider zurück, ohne den
    Instrumentor ein zweites Mal zu setzen.

    **Wirft nie.** Ein fehlendes Paket, ein kaputter Endpunkt, ein Phoenix,
    das nicht läuft — nichts davon darf den Shop am Hochfahren hindern. Der
    Grund wird protokolliert und der Chat läuft ohne Trace weiter.
    """
    global _provider, _instrumentiert, _ziel, _name, _seit
    with _sperre:
        if _provider is not None:
            return _provider
        if not an():
            log.info("Tracing ist über %s abgeschaltet.", ENV_AN)
            return None
        ziel = endpunkt or umg.wert(ENV_ENDPUNKT) or ENDPUNKT
        name = projekt or umg.wert(ENV_PROJEKT) or PROJEKT
        try:
            from phoenix.otel import SimpleSpanProcessor, register

            provider = register(
                project_name=name,
                endpoint=ziel,
                auto_instrument=False, batch=False,
                set_global_tracer_provider=False)
            # `register` hat gerade einen SimpleSpanProcessor angehängt, der im
            # Request-Thread exportiert. Derselbe Prozessor noch einmal, aber
            # hinter der Schlange — `add_span_processor` ersetzt den
            # Vorgabe-Prozessor von sich aus (Phoenix-eigene Erweiterung des
            # SDK-Providers).
            prozessor = SimpleSpanProcessor(endpoint=ziel)
            # Zwischen Prozessor und Exporter, damit die Statusseite sagen
            # kann, ob Phoenix die Spans wirklich genommen hat (WB-377). Der
            # SimpleSpanProcessor schluckt das Ergebnis von `export()` selbst,
            # nach ihm ist Erfolg von Fehlschlag nicht mehr zu unterscheiden.
            prozessor.span_exporter = Buchfuehrend(prozessor.span_exporter)
            provider.add_span_processor(NichtBlockierend(prozessor))
            bestuecke(provider)

            if not _instrumentiert:
                from openinference.instrumentation.openai import (
                    OpenAIInstrumentor)
                # Die vLLM-Box ist OpenAI-kompatibel, also erfasst der
                # Instrumentor sie. Er hängt an DIESEM Provider und nicht am
                # globalen — siehe set_global_tracer_provider oben.
                OpenAIInstrumentor().instrument(tracer_provider=provider)
                _instrumentiert = True
        except Exception as e:  # noqa: BLE001 — siehe Docstring
            log.warning("Phoenix-Tracing nicht eingerichtet (%s: %s). "
                        "Der Chat läuft ohne Trace weiter.",
                        e.__class__.__name__, e)
            return None
        _provider = provider
        _ziel, _name, _seit = ziel, name, time.time()
        log.info("Phoenix-Tracing an: Projekt %r auf %s", name, ziel)
        return provider


def setze_provider(provider) -> None:
    """Setzt den Provider von Hand — für Tests mit In-Memory-Exporter.

    Damit prüft die Testsuite denselben Span-Vertrag wie der Betrieb, ohne
    laufendes Phoenix und ohne die Box (Spec 13).
    """
    global _provider
    with _sperre:
        _provider = provider
        if provider is not None:
            bestuecke(provider)


def bestuecke(provider) -> None:
    """Hängt den `StufenBenenner` an einen Provider. Doppelt schadet nicht."""
    if any(isinstance(p, StufenBenenner)
           for p in _prozessoren(provider)):
        return
    try:
        # Phoenix' Provider würde sonst seinen Vorgabe-Prozessor wegwerfen.
        provider.add_span_processor(StufenBenenner(),
                                    replace_default_processor=False)
    except TypeError:
        # Ein blanker SDK-Provider (Tests) kennt das Schlüsselwort nicht.
        provider.add_span_processor(StufenBenenner())


def _prozessoren(provider):
    aktiv = getattr(provider, "_active_span_processor", None)
    return getattr(aktiv, "_span_processors", ())


@contextmanager
def ohne_trace():
    """Was hier drin passiert, bekommt keinen Span.

    Gebraucht für die Modell-Discovery (`/v1/models`): der OpenAI-Instrumentor
    macht daraus einen Span vom Kind `LLM` mit dem Namen `SyncPage[Model]`,
    ohne Modellnamen und ohne Tokenzahlen. Der stünde dann als sechster Ast im
    Baum aus Spec 7.1 — und er zählte in Phoenix als LLM-Aufruf mit, obwohl
    kein Modell gefragt wurde. Eine Tabellenabfrage ist kein Inferenzschritt.
    """
    try:
        from openinference.instrumentation import suppress_tracing
    except ImportError:
        # Ohne die Instrumentierung gibt es auch nichts zu unterdrücken.
        yield
        return
    with suppress_tracing():
        yield


def tracer(name: str = "zettel"):
    """Der Tracer. Ohne Einrichtung ein No-Op — nie `None`.

    Der Aufrufer soll nie fragen müssen, ob getract wird. Ein `if tracer:` an
    jeder Stelle wäre vier Zeilen Rauschen um jeden Span und die sicherste
    Art, irgendwann eine Stelle zu vergessen.
    """
    if _provider is None:
        return trace_api.NoOpTracerProvider().get_tracer(name)
    return _provider.get_tracer(name)


def flush(timeout_ms: int = 30_000) -> None:
    """Wartet, bis alle Spans draussen sind. Für Skripte, nicht für Requests.

    Ohne das endet ein kurzes Skript, bevor der Hintergrund-Thread den letzten
    Span exportiert hat — und die Verifikation (Spec 7.4) fände nichts.
    """
    if _provider is not None:
        try:
            _provider.force_flush(timeout_ms)
        except Exception as e:  # noqa: BLE001
            log.warning("force_flush: %s", e)


def abbauen() -> None:
    """Setzt alles zurück. Für Tests — im Betrieb gibt es das nicht."""
    global _provider, _ziel, _name, _seit
    with _sperre:
        _provider = None
        _ziel = _name = None
        _seit = None


def _stempel(wann: float | None) -> str | None:
    """Sekundengenau und ohne Zeitzone — wie jeder Zeitstempel im Projekt."""
    if wann is None:
        return None
    return datetime.fromtimestamp(wann).replace(microsecond=0).isoformat(sep=" ")


def tracerstand(provider=None) -> dict:
    """Was der Shop über seinen eigenen Tracer sagen darf (WB-377).

    **Rein aus dem Prozess. Kein Netzaufruf.** Das ist die harte Bedingung:
    diese Auskunft hängt an einer Seite, die jemand aufmacht, um nachzusehen —
    sie darf dabei weder Phoenix anfragen noch die vLLM-Box wecken. Alles hier
    ist entweder Konfiguration oder ein Zähler, den der laufende Prozess selbst
    hochgezählt hat.

    Deshalb steht in `seit` der Zeitpunkt der Einrichtung: die Zähler gelten
    ab da und nicht seit Anbeginn. Ein „0 verworfen" nach einem Neustart ist
    kein Freispruch, und die Seite muss das sagen dürfen.

    `gemessen` unterscheidet „nichts angekommen" von „darüber ist hier nichts
    bekannt" — letzteres ist der Zustand mit einem fremd gesetzten Provider
    (Tests, `setze_provider`), und eine 0 wäre dort erfunden.
    """
    provider = _provider if provider is None else provider
    stand = {
        "an": an(),
        "eingerichtet": provider is not None,
        # Auch im abgeschalteten Zustand ist die Frage „wohin denn?"
        # beantwortbar — und genau dort ist sie interessant.
        "endpunkt": _ziel or umg.wert(ENV_ENDPUNKT) or ENDPUNKT,
        "projekt": _name or umg.wert(ENV_PROJEKT) or PROJEKT,
        "seit": _stempel(_seit),
        "wartend": 0,
        "verworfen": 0,
        "gemessen": False,
        "angekommen": 0,
        "gescheitert": 0,
        "zuletzt_ok": None,
        "zuletzt_fehler": None,
        "grund": None,
    }
    if provider is None:
        return stand
    for p in _prozessoren(provider):
        if not isinstance(p, NichtBlockierend):
            continue
        stand["wartend"] += p.wartend
        stand["verworfen"] += p.verworfen
        buch = p.buch
        if buch is None:
            continue
        stand["gemessen"] = True
        stand["angekommen"] += buch.angekommen
        stand["gescheitert"] += buch.gescheitert
        for feld, wert in (("zuletzt_ok", buch.zuletzt_ok),
                           ("zuletzt_fehler", buch.zuletzt_fehler)):
            if wert is not None and (stand[feld] is None or wert > stand[feld]):
                stand[feld] = wert
        if buch.grund:
            stand["grund"] = buch.grund
    stand["zuletzt_ok"] = _stempel(stand["zuletzt_ok"])
    stand["zuletzt_fehler"] = _stempel(stand["zuletzt_fehler"])
    return stand


# --------------------------------------------------------------------------
# Die Buchführung: was ist wirklich angekommen?

class Buchfuehrend(SpanExporter):
    """Zählt mit, was der eigentliche Exporter losgeworden ist (WB-377).

    **Warum überhaupt.** Ohne diese Schicht kann der Shop über seinen eigenen
    Tracer nur sagen, dass er eingerichtet ist — nicht, ob je ein Span in
    Phoenix gelandet ist. Der `SimpleSpanProcessor` ruft `export()` auf,
    verwirft das Ergebnis und protokolliert höchstens eine Ausnahme; danach
    sind „angekommen" und „abgelehnt" nicht mehr zu unterscheiden. Eine
    Statusseite, die daraufhin „Tracing läuft" behauptet, wäre genau die
    stille Lüge, gegen die diese Seite gebaut ist.

    **Was hier NICHT passiert:** kein Neuversuch, kein Puffer, keine
    Veränderung am Ergebnis. Die Zähler sind eine Nebenwirkung, der Exportweg
    bleibt derselbe — bis auf eine Ausnahme, die hier zu `FAILURE` wird statt
    durch den Prozessor zu fliegen. Das ist kein Verschlucken: der Grund steht
    danach in `grund` und auf der Statusseite, statt nur im Log.

    Die Zähler sind Prozesszähler, keine Datenbank. Ein Neustart setzt sie auf
    null, und die Seite sagt das dazu — sonst läse sich „0 verworfen" wie ein
    Freispruch für die ganze Vergangenheit.
    """

    def __init__(self, inner: SpanExporter):
        self._inner = inner
        self._sperre = threading.Lock()
        self.angekommen = 0
        self.gescheitert = 0
        self.zuletzt_ok: float | None = None
        self.zuletzt_fehler: float | None = None
        self.grund: str | None = None

    def export(self, spans):
        n = len(spans)
        try:
            ergebnis = self._inner.export(spans)
        except Exception as e:  # noqa: BLE001
            self._buche(0, n, f"{e.__class__.__name__}: {e}")
            return SpanExportResult.FAILURE
        if ergebnis is SpanExportResult.SUCCESS:
            self._buche(n, 0, None)
        else:
            # Der OTLP-Exporter hat hier schon mehrfach vergeblich versucht zu
            # senden und den eigentlichen Grund nur ins Log geschrieben.
            self._buche(0, n, "Der Exporter meldet FAILURE — Phoenix nimmt "
                              "die Spans nicht an (Grund steht im Log).")
        return ergebnis

    def _buche(self, ok: int, weg: int, grund: str | None) -> None:
        jetzt = time.time()
        with self._sperre:
            if ok:
                self.angekommen += ok
                self.zuletzt_ok = jetzt
            if weg:
                self.gescheitert += weg
                self.zuletzt_fehler = jetzt
                self.grund = grund

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self._inner.force_flush(timeout_millis))

    def shutdown(self) -> None:
        self._inner.shutdown()


# --------------------------------------------------------------------------
# Der Prozessor, der den Request nicht aufhält

class _Ende:
    """Zeichen für den Arbeiter, dass Schluss ist."""


class NichtBlockierend(SpanProcessor):
    """Reicht beendete Spans über eine Schlange an einen anderen Prozessor.

    Der Request-Thread legt den Span nur ab (Mikrosekunden) und läuft weiter;
    der Export samt Wartezeit, Neuversuch und Fehlschlag passiert in einem
    Hintergrund-Thread. Damit ist die harte Anforderung aus Spec 7.3 erfüllt:
    **fällt Phoenix aus, fällt der Shop nicht aus.**

    Was hier NICHT passiert: zusammenfassen, umsortieren, nach Zeitplan
    verwerfen. Der innere Prozessor sieht dieselben Spans in derselben
    Reihenfolge wie ohne diese Schicht.
    """

    def __init__(self, inner: SpanProcessor, *, grenze: int = SCHLANGE):
        self._inner = inner
        self._schlange: queue.Queue = queue.Queue(maxsize=grenze)
        self.verworfen = 0
        self._thread = threading.Thread(
            target=self._arbeiten, name="zettel-spans", daemon=True)
        self._thread.start()

    @property
    def wartend(self) -> int:
        """Wie viele Spans gerade in der Schlange stehen."""
        return self._schlange.qsize()

    @property
    def buch(self) -> "Buchfuehrend | None":
        """Die Buchführung hinter diesem Prozessor, falls es eine gibt.

        Gesucht statt gemerkt: den Prozessor bauen auch Tests, und die hängen
        einen In-Memory-Exporter ohne Buchführung hinein. `None` heisst dann
        ehrlich „darüber ist hier nichts bekannt" und nicht „nichts
        angekommen".
        """
        exporter = getattr(self._inner, "span_exporter", None)
        return exporter if isinstance(exporter, Buchfuehrend) else None

    # on_start läuft im Request-Thread und ist billig (die Prozessoren setzen
    # dort höchstens Attribute) — er muss synchron bleiben, sonst wäre der
    # Span beim Setzen schon vorbei.
    def on_start(self, span, parent_context=None) -> None:
        self._inner.on_start(span, parent_context)

    def on_end(self, span) -> None:
        try:
            self._schlange.put_nowait(span)
        except queue.Full:
            self.verworfen += 1
            if self.verworfen == 1:
                log.warning(
                    "Span-Schlange voll (%d) — Spans gehen verloren. Der "
                    "Collector nimmt nichts mehr an.", self._schlange.maxsize)

    def _arbeiten(self) -> None:
        while True:
            posten = self._schlange.get()
            try:
                if isinstance(posten, _Ende):
                    return
                if isinstance(posten, threading.Event):
                    # Eine Marke aus force_flush: alles davor ist durch.
                    posten.set()
                    continue
                self._inner.on_end(posten)
            except Exception as e:  # noqa: BLE001
                # Ein Exportfehler ist genau das, wogegen dieser Thread da
                # ist. Er darf ihn nicht mitnehmen.
                log.debug("Span nicht exportiert: %s", e)
            finally:
                self._schlange.task_done()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        marke = threading.Event()
        try:
            self._schlange.put_nowait(marke)
        except queue.Full:
            return False
        if not marke.wait(timeout_millis / 1000.0):
            return False
        return bool(self._inner.force_flush(timeout_millis))

    def shutdown(self) -> None:
        self.force_flush(5_000)
        try:
            self._schlange.put_nowait(_Ende())
        except queue.Full:
            pass
        self._thread.join(timeout=5.0)
        self._inner.shutdown()


# --------------------------------------------------------------------------
# Die Namen der LLM-Stufen

#: Der Name, den der nächste vom Instrumentor geöffnete Span bekommen soll.
#: Ein `contextvars`-Wert und keine Instanzvariable, weil der Instrumentor
#: nichts von diesem Projekt weiss und der Web-Prozess mehrere Anfragen
#: gleichzeitig bedient.
_STUFE: contextvars.ContextVar = contextvars.ContextVar(
    "zettel_stufe", default=None)


@contextmanager
def stufe(name: str):
    """Benennt den nächsten Span, den der OpenAI-Instrumentor öffnet.

    Genau den nächsten: die Marke wird beim ersten Span verbraucht. Sonst
    hiessen auch Spans, die ein zweiter Aufruf im selben Block erzeugt,
    `plan.extract` — und der Baum aus Spec 7.1 hätte zwei gleichnamige Äste,
    die nichts unterscheidet.
    """
    kasten = {"name": name}
    marke = _STUFE.set(kasten)
    try:
        yield
    finally:
        _STUFE.reset(marke)


class StufenBenenner(SpanProcessor):
    """Benennt den Instrumentor-Span in `plan.extract`/`plan.choose` um.

    `on_start` ist der letzte Moment, in dem ein Span noch umbenannt werden
    kann: beim Beenden ist er ein `ReadableSpan` und der Name steht fest.
    """

    def on_start(self, span, parent_context=None) -> None:
        kasten = _STUFE.get()
        if kasten and kasten.get("name"):
            span.update_name(kasten["name"])
            kasten["name"] = None

    def on_end(self, span) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
