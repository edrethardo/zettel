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
from contextlib import contextmanager

from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import SpanProcessor

log = logging.getLogger(__name__)

#: Eigenes Projekt, getrennt vom Board-Projekt `Picknick` (Spec 7): dort liegt,
#: wie dieses Projekt gebaut wurde, hier, wie es sich im Betrieb verhält.
PROJEKT = "Picknick Agent"

#: Phoenix nimmt Spans über OTLP/HTTP entgegen. Der Pfad gehört dazu — ohne
#: ihn antwortet Phoenix mit 404 und der Exporter meldet einen Fehler, der wie
#: ein Netzproblem aussieht.
ENDPUNKT = "http://localhost:6006/v1/traces"

ENV_AN = "PICKNICK_TRACING"
ENV_ENDPUNKT = "PICKNICK_PHOENIX_ENDPOINT"
ENV_PROJEKT = "PICKNICK_PHOENIX_PROJECT"

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


# --------------------------------------------------------------------------
# Einrichtung

def an(umgebung=None) -> bool:
    """Soll getract werden? `PICKNICK_TRACING=0` schaltet ab."""
    umgebung = os.environ if umgebung is None else umgebung
    return (umgebung.get(ENV_AN) or "").strip().casefold() not in AUS


def einrichten(*, projekt: str | None = None, endpunkt: str | None = None):
    """Richtet Phoenix ein und gibt den Provider zurück (oder `None`).

    Idempotent: der zweite Aufruf gibt denselben Provider zurück, ohne den
    Instrumentor ein zweites Mal zu setzen.

    **Wirft nie.** Ein fehlendes Paket, ein kaputter Endpunkt, ein Phoenix,
    das nicht läuft — nichts davon darf den Shop am Hochfahren hindern. Der
    Grund wird protokolliert und der Chat läuft ohne Trace weiter.
    """
    global _provider, _instrumentiert
    with _sperre:
        if _provider is not None:
            return _provider
        if not an():
            log.info("Tracing ist über %s abgeschaltet.", ENV_AN)
            return None
        ziel = endpunkt or os.environ.get(ENV_ENDPUNKT) or ENDPUNKT
        name = projekt or os.environ.get(ENV_PROJEKT) or PROJEKT
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
            provider.add_span_processor(
                NichtBlockierend(SimpleSpanProcessor(endpoint=ziel)))
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


def tracer(name: str = "picknick"):
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
    global _provider
    with _sperre:
        _provider = None


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
            target=self._arbeiten, name="picknick-spans", daemon=True)
        self._thread.start()

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
    "picknick_stufe", default=None)


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
