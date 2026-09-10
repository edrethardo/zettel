"""Stufe 5: der Rahmen aus einem Satz — und die Prüfung, die ihn ehrlich hält.

`wochenplan.rahmen` hat lange begründet, warum Zahlen aus Feldern kommen:
eine Zahl, die ein Modell aus einem Satz liest, kann es erfunden haben.
Diese Datei stellt beide Fälle her — das Modell LIEST („700 Kalorien" ->
kcal 700) und das Modell WEISS („viel Protein" -> kcal 2000) — und prüft,
dass nur der erste durchkommt. Die Prüfung steht im Code, nicht im Prompt.
"""
import json

import pytest
from fastapi.testclient import TestClient

from zettel import db, wochenplan
from zettel.assistant import chat as chatmodul
from zettel.assistant import plan as stufen
from zettel.llm import wake
from zettel.llm.client import Antwort
from zettel.web import app as webapp
from zettel.wochenplan import zug

SATZ = ("eine Mahlzeit pro Tag, 700 Kalorien, viel Protein, "
        "Kartoffeln, Eier und Nudeln sind da")


class FakeLLM:
    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        self.aufrufe.append({"nachrichten": list(nachrichten), **weitere})
        if not self.antworten:
            raise AssertionError("Mehr Modellaufrufe als Antworten.")
        naechste = self.antworten.pop(0)
        if isinstance(naechste, Exception):
            raise naechste
        return Antwort(content=naechste, reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def __init__(self, zustand=wake.BEDIENT):
        self._z = zustand

    def zustand(self):
        if self._z == wake.BEDIENT:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(self._z, seit_s=3.0, grund="schläft")


def _lesung(**felder):
    """Eine Modellantwort, wie Stufe 5 sie erwartet — Felder mit Vorgabe null."""
    roh = {"tage": None, "personen": None, "max_minuten": None,
           "budget_euro": None, "kcal": None, "mahlzeiten_pro_tag": None,
           "vorlieben": None, "bestand": []}
    roh.update(felder)
    return json.dumps(roh)


# --------------------------------------------------------------------------
# Die Stufe selbst

def test_liest_was_im_satz_steht():
    llm = FakeLLM(_lesung(kcal=700, mahlzeiten_pro_tag=1,
                          vorlieben="viel Protein",
                          bestand=["Kartoffeln", "Eier", "Nudeln"]))
    lesung = stufen.rahmen_lesen(llm, SATZ)
    assert lesung.werte == {"kcal": 700, "mahlzeiten_pro_tag": 1,
                            "vorlieben": "viel Protein",
                            "bestand": ["Kartoffeln", "Eier", "Nudeln"]}
    assert lesung.verworfen == []
    # Der Satz geht wörtlich als Benutzerteil ans Modell — nichts drumherum.
    assert llm.aufrufe[0]["nachrichten"][1]["content"] == SATZ


def test_eine_zahl_die_nicht_im_satz_steht_wird_verworfen():
    # „viel Protein" -> 2000 kcal ist WISSEN, nicht Lesen. Und 4 Personen
    # stehen auch nirgends.
    llm = FakeLLM(_lesung(kcal=2000, personen=4, bestand=["Eier"]))
    lesung = stufen.rahmen_lesen(llm, SATZ)
    assert lesung.werte == {"bestand": ["Eier"]}
    assert [(v["feld"], v["wert"]) for v in lesung.verworfen] == [
        ("personen", 4), ("kcal", 2000)]


def test_zahlwoerter_belegen_eine_zahl():
    # „eine Mahlzeit" belegt die 1, „zwei Personen" die 2 — ohne Ziffer.
    llm = FakeLLM(_lesung(mahlzeiten_pro_tag=1, personen=2))
    lesung = stufen.rahmen_lesen(llm, "zwei Personen, eine Mahlzeit am Tag")
    assert lesung.werte == {"mahlzeiten_pro_tag": 1, "personen": 2,
                            "bestand": []}


def test_eine_mahlzeit_pro_tag_ist_keine_tageszahl():
    # Take v5d vom 10.09.: „eine Mahlzeit pro Tag" wurde zu tage=1 — die
    # „eine" stand im Satz, aber vor „Mahlzeit", nicht vor „Tag".
    llm = FakeLLM(_lesung(tage=1, mahlzeiten_pro_tag=1),
                  _lesung(tage=3), _lesung(tage=5))
    erste = stufen.rahmen_lesen(llm, SATZ)
    assert "tage" not in erste.werte and erste.werte["mahlzeiten_pro_tag"] == 1
    assert erste.verworfen[0]["feld"] == "tage"
    assert stufen.rahmen_lesen(llm, "für drei Tage, 2 Personen").werte["tage"] == 3
    assert stufen.rahmen_lesen(llm, "plan 5 days please").werte["tage"] == 5


def test_bestand_muss_ein_stueck_des_satzes_sein():
    llm = FakeLLM(_lesung(bestand=["Kartoffeln", "Zwiebeln", "6 Eier"]))
    lesung = stufen.rahmen_lesen(llm, "Kartoffeln und 6 Eier sind da")
    assert lesung.werte["bestand"] == ["Kartoffeln", "6 Eier"]
    assert lesung.verworfen == [{"feld": "bestand", "wert": "Zwiebeln",
                                 "grund": "steht nicht im Satz"}]


def test_vorliebe_braucht_ein_wort_aus_dem_satz():
    # „eiweissreich" ist durch „Protein" belegt; „vegetarisch" durch nichts.
    llm = FakeLLM(_lesung(vorlieben="eiweissreich"),
                  _lesung(vorlieben="vegetarisch"))
    assert stufen.rahmen_lesen(llm, "viel Protein bitte").werte["vorlieben"] == "eiweissreich"
    zweite = stufen.rahmen_lesen(llm, "viel Protein bitte")
    assert "vorlieben" not in zweite.werte
    assert zweite.verworfen[0]["feld"] == "vorlieben"


def test_leerer_satz_fragt_das_modell_nicht():
    llm = FakeLLM()
    assert stufen.rahmen_lesen(llm, "   ").werte == {}
    assert llm.aufrufe == []


def test_aus_lesung_baut_denselben_rahmen_wie_das_formular():
    r = wochenplan.aus_lesung(
        {"kcal": 700, "vorlieben": "viel Protein",
         "bestand": ["500 g Kartoffeln", "6 Eier", "Nudeln"]}, SATZ)
    assert (r.tage, r.personen, r.kcal_ziel) == (5, 2, 700)   # Vorgaben wie im Formular
    assert r.satz == SATZ and r.vorlieben == "viel Protein"
    assert [(b["menge"], b["einheit"], b["name"]) for b in r.bestand] == [
        (500.0, "g", "Kartoffeln"), (6.0, None, "Eier"), (None, None, "Nudeln")]


def test_die_vorliebe_steht_in_der_vorlage_der_stufe_4():
    text = stufen.wochenvorlage(
        [{"tag": 1, "name": "Mo", "offen": True, "festgelegt": None}],
        [{"id": 1, "name": "Omelett", "minuten": 10, "zutaten": ["Eier"]}],
        personen=2, vorlieben="viel Protein")
    assert "Rahmen: 2 Personen, Vorliebe: viel Protein" in text


# --------------------------------------------------------------------------
# Der Weg durch die Seite

@pytest.fixture
def con(db_datei):
    c = db.connect(db_datei)
    yield c
    c.close()


def _client(db_datei, tmp_path, *antworten):
    chat = chatmodul.Chat(FakeLLM(*antworten), wecker=Box())
    return TestClient(webapp.create_app(db_path=db_datei,
                                        image_dir=tmp_path / "bilder",
                                        chat=chat, planer=zug.Planer(chat)))


def test_der_satz_steht_ueber_der_maske(db_datei, tmp_path):
    with _client(db_datei, tmp_path) as c:
        text = c.get("/plan/neu").text
    assert 'name="satz"' in text and 'action="/plan/verstehen"' in text
    # Die Maske bleibt darunter, sichtbar getrennt.
    assert text.index('name="satz"') < text.index("oder von Hand") < text.index('name="tage"')


def test_der_satz_fuellt_die_maske_und_legt_den_plan_an(db_datei, tmp_path, con):
    # Erste Antwort: die Lesung. Ohne Rezepte gibt es danach nichts zur
    # Wahl — der Planer fragt das Modell dann gar nicht (kein zweiter Aufruf).
    with _client(db_datei, tmp_path,
                 _lesung(kcal=700, mahlzeiten_pro_tag=1,
                         vorlieben="viel Protein",
                         bestand=["Kartoffeln", "Eier", "Nudeln"])) as c:
        r = c.post("/plan/verstehen", data={"satz": SATZ})
    assert r.status_code == 200
    p = wochenplan.laden(con, 1)
    assert p["kcal_ziel"] == 700 and p["satz"] == SATZ
    assert p["vorlieben"] == "viel Protein"
    assert [b["name"] for b in p["bestand"]] == ["Kartoffeln", "Eier", "Nudeln"]
    assert "Gelesen aus" in r.text and "Vorliebe: viel Protein" in r.text
    assert "Ziel 700 kcal" in r.text


def test_erfundene_zahlen_kommen_nicht_auf_die_seite(db_datei, tmp_path, con):
    with _client(db_datei, tmp_path,
                 _lesung(kcal=2000, budget_euro=80, bestand=["Eier"])) as c:
        r = c.post("/plan/verstehen", data={"satz": SATZ})
    p = wochenplan.laden(con, 1)
    assert p["kcal_ziel"] is None and p["budget_cents"] is None
    assert "2 Angabe(n) des Modells standen nicht im Satz" in r.text


def test_ohne_satz_bleibt_das_formular(db_datei, tmp_path, con):
    with _client(db_datei, tmp_path) as c:
        r = c.post("/plan/verstehen", data={"satz": "  "})
    assert r.status_code == 200 and "Da stand kein Satz" in r.text
    assert con.execute("SELECT count(*) FROM plan").fetchone()[0] == 0


def test_schlaeft_die_box_bleibt_das_formular(db_datei, tmp_path, con):
    chat = chatmodul.Chat(FakeLLM(), wecker=Box(wake.STILL))
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=tmp_path / "b",
                                      chat=chat, planer=zug.Planer(chat))) as c:
        r = c.post("/plan/verstehen", data={"satz": SATZ})
    assert r.status_code == 200 and 'name="satz"' in r.text
    assert con.execute("SELECT count(*) FROM plan").fetchone()[0] == 0
