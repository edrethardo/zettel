"""Der Wochenplan — der Meal Planner als Zug (2026-09-06-wochenplan-design.md).

Vier Module in Phase 1, keines davon fragt ein Modell:

* `rahmen.py` — die Rahmenbedingungen aus dem Formular und der Bestandstext.
* `speicher.py` — `plan`, `plan_tag`, `plan_bestand`: anlegen, belegen,
  entscheiden, laden.
* `vorlage.py` — was zur Wahl steht: ein Rezept je Gericht, mit Zeit und
  Zutatennamen. Dieselbe Liste für Mensch und Modell.
* `liste.py` — die Einkaufsliste: zusammenlegen, Bestand abziehen, in den
  bestehenden Korb.
* `zug.py` (Phase 2) — der Zug `plan.woche`: das Modell belegt die offenen
  Tage, nur aus der Vorlage, mit Span und Verwurf-Zählung.
* `bestand.py` (Phase 3) — der Bon schlägt vor, der Mensch bestätigt: Käufe
  der letzten Tage, die eine Zeile der Liste treffen, als offene Vorschläge.
* `naehrwert.py` — kcal und Eiweiss je Tag und Portion, gerechnet aus
  `product_naehrwert` × Bedarf in Gramm; was nicht rechenbar ist, wird
  gezählt und benannt, nicht mit Null gefüllt.

Die tragende Regel ist die des ganzen Projekts: **nichts wird erfunden.**
Jede Zahl am Plan ist gerechnet; der Bestand ist ein erklärter Rahmen zu
diesem Plan und kein Lagerstand.
"""
from zettel.wochenplan.bestand import vorschlagen as bestand_vorschlagen  # noqa: F401
from zettel.wochenplan.liste import einkaufsliste, in_den_korb  # noqa: F401
from zettel.wochenplan.naehrwert import anreichern as naehrwerte  # noqa: F401
from zettel.wochenplan.rahmen import (  # noqa: F401
    Rahmen, aus_formular, bestand_aus_text)
from zettel.wochenplan.speicher import (  # noqa: F401
    AUS_BON, ENTWURF, ERKLAERT, IM_KORB, WochenplanFehler, aktuell, alle,
    anlegen, auswaerts_setzen, bestand_entfernen, bestand_entscheiden,
    bestand_hinzufuegen, laden, loeschen, status_setzen, tag_entscheiden,
    tag_setzen)
from zettel.wochenplan.vorlage import gerichte  # noqa: F401
from zettel.wochenplan.zug import Planer, WEG_PLAN  # noqa: F401

__all__ = [
    "AUS_BON", "ENTWURF", "ERKLAERT", "IM_KORB", "Planer", "Rahmen",
    "WEG_PLAN", "WochenplanFehler",
    "aktuell", "alle", "anlegen", "aus_formular", "auswaerts_setzen",
    "bestand_aus_text", "bestand_entfernen", "bestand_entscheiden",
    "bestand_vorschlagen",
    "bestand_hinzufuegen", "einkaufsliste", "gerichte", "in_den_korb",
    "naehrwerte",
    "laden", "loeschen", "status_setzen", "tag_entscheiden", "tag_setzen",
]
