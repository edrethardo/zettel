"""Observability: Phoenix-Tracer und der Span-Vertrag aus Spec 7.

Was von aussen gebraucht wird, steht hier. `picknick.obs.otel` ist die
Einrichtung (Spec 7.2) und die Ausfallsicherheit (Spec 7.3),
`picknick.obs.spans` der Span-Vertrag (Spec 7.1).
"""
from picknick.obs.spans import (
    PFAD,
    chain,
    dokumente,
    rang_top,
    retriever,
    schwaechste_suche,
    setze,
    setze_ausgabe,
    setze_eingabe,
    span_id,
)
from picknick.obs.otel import (
    ENDPUNKT,
    PROJEKT,
    abbauen,
    an,
    einrichten,
    flush,
    ohne_trace,
    setze_provider,
    stufe,
    tracer,
)

__all__ = [
    "ENDPUNKT", "PFAD", "PROJEKT", "abbauen", "an", "chain", "dokumente",
    "einrichten", "flush", "ohne_trace", "rang_top", "retriever", "schwaechste_suche",
    "setze", "setze_ausgabe", "setze_eingabe", "setze_provider", "span_id",
    "stufe", "tracer",
]
