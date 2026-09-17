"""Das Veröffentlichungs-Gate selbst geprüft.

Ein Gate, das nur grün werden kann, prüft nichts. Diese Datei stellt beide
Fassungen her, die es unterscheiden soll — und die Richtung, die man beim
Schreiben eines solchen Checks am leichtesten vergisst: dass er auch dann
anschlagen muss, wenn die Behauptung STEHENBLEIBT, obwohl die Messung
inzwischen da ist.
"""
from pathlib import Path

import pytest

from checks import veroeffentlichung as gate

LAUF = [Path("evals/plan_probe-2026-09-30-nemotron35.json")]
MIT = f"5 scenarios — on the Qwen3.8-27B reference, {gate.VORBEHALT}: rejected 0"
OHNE = "5 scenarios: rejected 0 in all six turns"


def test_ohne_messung_muss_der_vorbehalt_stehen():
    assert gate.pruefe_modellbehauptung([], MIT) == "kein Lauf, Vorbehalt steht"


def test_ohne_messung_und_ohne_vorbehalt_schlaegt_an():
    # Der Fall, den es zu verhindern gilt: der Post steht unter einer
    # Nemotron-Überschrift, die Zahl daneben ist auf Qwen gemessen, und
    # nichts sagt es.
    with pytest.raises(AssertionError, match="kein Nemotron-Lauf"):
        gate.pruefe_modellbehauptung([], OHNE)


def test_mit_messung_muss_der_vorbehalt_weg():
    # Die Richtung, die man vergisst: die Messung ist nachgeholt, der
    # Vorbehalt steht noch da und macht die eigene Arbeit kleiner.
    with pytest.raises(AssertionError, match="liegt vor"):
        gate.pruefe_modellbehauptung(LAUF, MIT)


def test_mit_messung_und_ohne_vorbehalt_ist_grün():
    assert gate.pruefe_modellbehauptung(LAUF, OHNE) == "Lauf liegt, Vorbehalt ist weg"


def test_zahlen_gruppiert_nach_wert_und_nennt_die_datei(tmp_path, monkeypatch):
    """Die Fehlermeldung muss sagen, WER was behauptet — sonst sucht man."""
    monkeypatch.setattr(gate, "WURZEL", tmp_path)
    (tmp_path / "a.md").write_text("1.480 Tests", encoding="utf-8")
    (tmp_path / "b.md").write_text("1,480 tests", encoding="utf-8")
    (tmp_path / "c.md").write_text("1.364 Tests", encoding="utf-8")
    gefunden = gate._zahlen(r"([\d][\d.,]{2,})\s*[Tt]ests",
                            ["a.md", "b.md", "c.md"])
    # Punkt und Komma sind dieselbe Zahl — das Repo schreibt beides.
    assert gefunden == {1480: ["a.md", "b.md"], 1364: ["c.md"]}


# --------------------------------------------------------------------------
# Fremde Rezepte (WB-604)

def test_eine_echte_chefkoch_antwort_faellt_auf():
    """Der Regressionstest zum Fund: jede der drei Spuren muss rot werden.

    Bis WB-604 lagen zwei echte Chefkoch-Antworten unter `tests/fixtures/`
    und waren über `raw.githubusercontent.com` abrufbar. Ein Gate, das nie
    rot war, ist nicht geprüft — deshalb wird hier jede Spur einzeln
    hergestellt: der Bildserver, eine der drei IDs, die wirklich hier lagen,
    und eine beliebige fremde ID im Rezeptlink. Die letzte ist die
    wichtigste: sie greift auch bei einer Antwort, die es heute noch gar
    nicht gibt.
    """
    echte_id = gate.ECHTE_IDS[0]
    # Zusammengesetzt, damit diese Datei sich nicht selbst meldet — genau die
    # Vorsichtsmassnahme, die das Gate für seine eigenen Konstanten trifft.
    fremde_id = "40295812" + "34567890"
    seiten = {
        "cdn.json": f'"url": "https://img.{gate.CDN_HOST}/rezepte/1/x.jpg"',
        "alt.py": f'CHEFKOCH_ID = "{echte_id}"',
        "neu.json": ('{"siteUrl": "https://www.chefkoch.de/rezepte/'
                     f'{fremde_id}/Etwas.html"}}'),
    }
    funde = gate.chefkoch_funde(sorted(seiten), seiten.get)
    getroffen = {f.split(":")[0] for f in funde}
    assert getroffen == set(seiten), funde
    assert any(gate.CDN_HOST in f for f in funde)
    assert any(echte_id in f for f in funde)
    assert any(fremde_id in f for f in funde)


def test_die_erfundenen_fixtures_kommen_durch():
    """Die Gegenrichtung — ein Gate, das immer rot ist, taugt auch nichts.

    Geprüft wird nicht ein Beispiel, sondern das, was wirklich im Repo
    liegt: die beiden erfundenen Fixtures und die kurzen Rezept-IDs, die die
    übrigen Tests benutzen (`/rezepte/42/`).
    """
    fixtures = sorted((gate.WURZEL / "tests" / "fixtures")
                      .glob("chefkoch_*.json"))
    assert fixtures, "keine Chefkoch-Fixture gefunden — falscher Pfad?"
    namen = [str(p.relative_to(gate.WURZEL)) for p in fixtures]
    namen.append("tests/test_entwurf.py")
    assert gate.chefkoch_funde(namen, gate.text_von) == []
