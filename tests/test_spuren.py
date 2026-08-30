"""Stufe 3 läuft in mehreren gleichzeitigen Anfragen (WB-412).

Der Nutzer: „Der Agent braucht 20 Sekunden oder so."

Gemessen (Phoenix, Züge vom 2026-08-30): `plan.extract` 11,53 s Median,
`plan.choose` 14,71 s. Stufe 3 ist der teuerste Teil — und sie besteht aus
lauter kleinen, unabhängigen Fragen: „welches dieser zwanzig Produkte ist
‚Tomatenmark'". Die Box bedient vier Anfragen nebeneinander mit 74,8 tok/s
gegen 24,9 einzeln.

Gemessen an einem echten Rezept (Pho Bo, 17 Begriffe mit Kandidaten, echte
Box, echter Katalog):

    1 Spur    16,7 s   11 gewählt
    2 Spuren  11,1 s   11 gewählt   1,51×
    4 Spuren   9,4 s   11 gewählt   1,78×
    6 Spuren   8,7 s   12 gewählt   1,92×

Vier, weil danach kaum noch etwas kommt.

Diese Datei prüft nicht die Zeit — die misst die Maschine, auf der sie läuft
— sondern die drei Zusagen, die das Zerlegen nicht brechen darf: dieselbe
Antwort, dieselbe Reihenfolge, und keine Spur, die aus einer fremden
Kandidatenliste wählt.
"""
from __future__ import annotations

import json

from zettel.assistant import plan
from zettel.llm.client import Antwort


class Zaehlend:
    """Ein Modell, das jede Anfrage mitschreibt und je Begriff das erste
    vorgelegte Produkt nimmt."""

    def __init__(self):
        self.anfragen: list[str] = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **_):
        text = nachrichten[-1]["content"]
        self.anfragen.append(text)
        daten = json.loads(text.split("Kandidaten:", 1)[1]) \
            if "Kandidaten:" in text else None
        return Antwort(content=json.dumps({"auswahl": self._waehle(text)}),
                       reasoning_content=None, modell="fake",
                       finish_reason="stop")

    @staticmethod
    def _waehle(text: str) -> list[dict]:
        # Der Prompt trägt die Aufgaben als JSON. Wir nehmen je Begriff den
        # ersten Kandidaten — das genügt, um Reihenfolge und Zuordnung zu
        # prüfen, und erfindet nichts.
        anfang = text.index("[")
        aufgaben = json.loads(text[anfang:])
        return [{"begriff": a["begriff"],
                 "produkt_id": a["kandidaten"][0]["id"], "menge": 1}
                for a in aufgaben if a.get("kandidaten")]


def _aufgaben(n: int) -> list[dict]:
    return [{"begriff": f"Zutat {i}", "menge": 1,
             "kandidaten": [{"id": 100 + i, "name": f"Produkt {i}",
                             "unit_text": "1 Stk", "price_cents": 100}]}
            for i in range(n)]


def test_eine_kurze_liste_bleibt_eine_anfrage():
    """Vier Anfragen mit je zwei Begriffen zahlen viermal den Systemprompt."""
    zugang = Zaehlend()
    auswahl = plan.choose(zugang, "", _aufgaben(plan.SPUREN_AB - 1),
                          guided=False)
    assert len(zugang.anfragen) == 1, len(zugang.anfragen)
    assert len(auswahl.gewaehlt) == plan.SPUREN_AB - 1


def test_eine_lange_liste_wird_zerlegt():
    zugang = Zaehlend()
    auswahl = plan.choose(zugang, "", _aufgaben(16), guided=False)
    assert len(zugang.anfragen) == plan.SPUREN, len(zugang.anfragen)
    assert len(auswahl.gewaehlt) == 16


def test_das_ergebnis_ist_dasselbe_wie_ohne_zerlegen():
    """Die Zerlegung ist eine Frage der Zeit und keine der Antwort."""
    aufgaben = _aufgaben(16)
    einzeln = plan.choose(Zaehlend(), "", aufgaben, guided=False, spuren=1)
    verteilt = plan.choose(Zaehlend(), "", aufgaben, guided=False)
    assert [(w["begriff"], w["produkt"]["id"]) for w in verteilt.gewaehlt] \
        == [(w["begriff"], w["produkt"]["id"]) for w in einzeln.gewaehlt]


def test_die_reihenfolge_ist_die_der_aufgaben():
    """Sie ist die Reihenfolge der Vorschlagsliste — sie darf nicht davon
    abhängen, welche Spur zuerst fertig war."""
    aufgaben = _aufgaben(16)
    auswahl = plan.choose(Zaehlend(), "", aufgaben, guided=False)
    assert [w["begriff"] for w in auswahl.gewaehlt] \
        == [a["begriff"] for a in aufgaben]


def test_eine_spur_kann_nicht_aus_einer_fremden_liste_waehlen():
    """Die Zusicherung aus `choose()` wird durch das Zerlegen schärfer, nicht
    weicher: eine Spur kennt nur ihre eigenen Kandidaten."""

    class Fremd(Zaehlend):
        def chat(self, nachrichten, **_):
            self.anfragen.append(nachrichten[-1]["content"])
            # Immer dieselbe ID — sie gehört genau einer Spur.
            return Antwort(content=json.dumps(
                {"auswahl": [{"begriff": "Zutat 0", "produkt_id": 100,
                              "menge": 1}]}),
                reasoning_content=None, modell="fake", finish_reason="stop")

    auswahl = plan.choose(Fremd(), "", _aufgaben(16), guided=False)
    assert len(auswahl.gewaehlt) == 1, auswahl.gewaehlt
    assert auswahl.gewaehlt[0]["produkt"]["id"] == 100
    # Die anderen drei Spuren haben dieselbe ID genannt und sie NICHT bekommen.
    assert len(auswahl.verworfen) == plan.SPUREN - 1, auswahl.verworfen
    assert all(v["grund"] == "nicht vorgelegt" for v in auswahl.verworfen)
