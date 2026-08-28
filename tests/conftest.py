"""Voreinstellungen für die ganze Testsuite.

**Kein Test schickt Spans an ein laufendes Phoenix.** Das ist nicht bloss die
Regel „kein Test geht ins Netz" (Spec 13), sondern Selbstschutz: auf diesem
Rechner läuft Phoenix auf `localhost:6006`, und `picknick.web.app` richtet den
Tracer schon beim Import ein. Ohne die Zeile unten liefe jeder Testlauf mit in
das Projekt `Picknick Agent` — und die Zahlen, mit denen `scripts/trace_probe.py`
die Verifikation aus Spec 7.4 belegt, wären ein Gemisch aus echten Chat-Zügen
und Testrauschen.

Hart gesetzt und nicht `setdefault`: eine Umgebung, in der jemand
`PICKNICK_TRACING=1` exportiert hat, soll die Suite nicht umkonfigurieren
können.

Was die Span-Tests brauchen, richten sie selbst ein — mit einem
In-Memory-Exporter (`tests/test_obs.py`).
"""
import os

os.environ["PICKNICK_TRACING"] = "0"

import pytest  # noqa: E402

from picknick import obs  # noqa: E402


@pytest.fixture(autouse=True)
def _kein_tracer_uebrig():
    """Räumt einen Provider weg, den ein Test gesetzt hat.

    `obs` hält den Provider im Modul, also über den Test hinaus. Bliebe er
    stehen, schriebe der nächste Test seine Spans in den Exporter des
    vorigen — und ein Test, der zufällig als zweiter läuft, sähe Spans, die
    er nicht erzeugt hat.
    """
    yield
    obs.abbauen()
