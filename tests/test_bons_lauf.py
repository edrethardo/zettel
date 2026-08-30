"""Tests für die Bon-Läufe im Hintergrund (WB-358).

Die Frage dieser Datei: **wartet ein Request auf einen langen Lauf?** Er darf
nicht — OCR und ein Modellaufruf dauern Sekunden bis Minuten, und eine
schlafende Box gemessene 96 s.

Geprüft wird deshalb mit einem Lauf, der von SICH aus nicht fertig wird
(`threading.Event`), statt mit einer Wartezeit: ein Test, der `sleep()`
benutzt, misst die Auslastung der Maschine und nicht das Verhalten des Codes.
"""
import threading

import pytest

from zettel.bons import lauf


def test_starte_kehrt_zurueck_waehrend_die_arbeit_laeuft():
    weiter = threading.Event()
    drin = threading.Event()

    def arbeit(melde):
        melde("liest die Datei")
        drin.set()
        # Ohne das Signal von aussen wird dieser Lauf nie fertig — der Test
        # kann also gar nicht zufällig auf ein schnelles Ende hereinfallen.
        assert weiter.wait(5), "der Test hat den Lauf nicht freigegeben"
        return 42, "fertig"

    laeufe = lauf.Laeufe()
    stand = laeufe.starte("bon.pdf", arbeit)
    assert stand.laeuft
    assert drin.wait(5)
    assert laeufe.stand("bon.pdf").schritt == "liest die Datei"

    weiter.set()
    for _ in range(500):
        if not laeufe.laeuft("bon.pdf"):
            break
        threading.Event().wait(0.01)
    fertig = laeufe.stand("bon.pdf")
    assert fertig.zustand == lauf.FERTIG
    assert (fertig.receipt_id, fertig.meldung) == (42, "fertig")


def test_zweimal_starten_startet_nur_einmal():
    """Zwei Requests auf denselben Bon schrieben sonst denselben Beleg zweimal."""
    weiter = threading.Event()
    aufrufe = []

    def arbeit(melde):
        aufrufe.append(1)
        weiter.wait(5)
        return 1, ""

    laeufe = lauf.Laeufe()
    laeufe.starte("bon.pdf", arbeit)
    laeufe.starte("bon.pdf", arbeit)
    weiter.set()
    assert len(aufrufe) == 1


def test_eine_ausnahme_wird_zum_zustand_und_nicht_zu_stille():
    """Ein Thread hat niemanden über sich, der eine Ausnahme anzeigen könnte.

    Was hier nicht gefangen wird, landet in einem Traceback auf stderr — und
    die Seite zeigt für immer „läuft".
    """
    def arbeit(melde):
        raise RuntimeError("pdftotext endete mit Code 1")

    laeufe = lauf.Laeufe(starter=lauf.sofort)
    stand = laeufe.starte("bon.pdf", arbeit)
    assert stand.fehler
    assert "pdftotext" in stand.meldung


def test_ausnahme_ohne_text_verliert_ihren_namen_nicht():
    def arbeit(melde):
        raise ValueError()

    laeufe = lauf.Laeufe(starter=lauf.sofort)
    assert laeufe.starte("bon.pdf", arbeit).meldung == "ValueError"


def test_vergiss_raeumt_nur_beendete_laeufe():
    weiter = threading.Event()
    laeufe = lauf.Laeufe()
    laeufe.starte("laeuft.pdf", lambda melde: (weiter.wait(5), (1, ""))[1])
    laeufe.vergiss("laeuft.pdf")
    # Ein laufender Lauf bleibt: er schriebe sonst in einen Stand, den
    # niemand mehr erwartet.
    assert laeufe.stand("laeuft.pdf") is not None
    weiter.set()

    fertig = lauf.Laeufe(starter=lauf.sofort)
    fertig.starte("fertig.pdf", lambda melde: (7, "gut"))
    fertig.vergiss("fertig.pdf")
    assert fertig.stand("fertig.pdf") is None


def test_ohne_lauf_gibt_es_keinen_stand():
    assert lauf.Laeufe().stand("nie.pdf") is None
    assert lauf.Laeufe().laeuft("nie.pdf") is False


def test_seit_s_zaehlt_mit_der_uhr():
    tick = [100.0]
    laeufe = lauf.Laeufe(uhr=lambda: tick[0], starter=lambda fn: None)
    laeufe.starte("bon.pdf", lambda melde: (1, ""))
    tick[0] = 137.0
    assert laeufe.stand("bon.pdf").seit_s == pytest.approx(37.0)
