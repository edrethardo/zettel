"""Observability: Phoenix-Tracer und der Span-Vertrag aus Spec 7.

Was von aussen gebraucht wird, steht hier. `zettel.obs.otel` ist die
Einrichtung (Spec 7.2) und die Ausfallsicherheit (Spec 7.3),
`zettel.obs.spans` der Span-Vertrag (Spec 7.1), `zettel.obs.labels` der
Rückweg: die Entscheidungen der Nutzerin als Annotationen auf dem Span des
Zugs (Spec 8.1).
"""
from zettel.obs import labels
from zettel.obs.spans import (
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
from zettel.obs.otel import (
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
    tracerstand,
)

__all__ = [
    "ENDPUNKT", "PFAD", "PROJEKT", "abbauen", "an", "chain", "dokumente",
    "einrichten", "flush", "labels", "ohne_trace", "rang_top", "retriever",
    "schwaechste_suche",
    "setze", "setze_ausgabe", "setze_eingabe", "setze_provider", "span_id",
    "stufe", "tracer", "tracerstand",
]
