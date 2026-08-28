"""Gerichte aus einer Quelle statt aus dem Gedächtnis des Modells (WB-338).

Vier Teile:

* `chefkoch` — der Client. **Geht ins Netz**, kennt die zwei Endpunkte und
  wählt das bestbewertete Rezept.
* `speicher` — der Zwischenspeicher in der Datenbank. Ein Treffer daraus
  braucht kein Netz, und das ist der Normalfall: ein Gericht wird einmal
  geholt und danach 90 Tage lang gelesen.
* `lauf` — der Abruf. `hole_jetzt()` im Request, `--gericht`/`--alle` von
  Hand zum Vorwärmen und Nachholen.
* `quelle` — was der Chat davon sieht: lesen, und bei einem Fehlschlag im
  Speicher HOLEN statt raten (WB-367).

**Der erste Satz zu einem neuen Gericht nimmt schon das Rezept.** Bis WB-367
tat er das nicht: der Shop trug einen Wunsch ein, startete einen eigenen
Prozess und antwortete solange mit dem Modell. Der Abruf kostet gemessen 90
bis 147 ms, der Modellweg daneben 35.600 ms — die Begründung stand in
`quelle`, samt Messung. Fällt Chefkoch aus, bleibt es beim Modellweg; der
Chat bricht nicht.
"""
from picknick.gerichte.chefkoch import (
    ChefkochFehler,
    SOURCE,
    bestes,
    detail_url,
    gewicht,
    hole,
    parse_rezept,
    parse_treffer,
    parse_zutaten,
    schritte,
    such_url,
    zutat_kette,
)
from picknick.gerichte.lauf import hole_jetzt
from picknick.gerichte.quelle import Quelle, nicht_holen
from picknick.gerichte.speicher import (
    FEHLER,
    LEER,
    OFFEN,
    OK,
    bereit,
    gericht,
    merken,
    offene,
    schluessel,
    vermerken,
    wunsch,
)

__all__ = [
    "ChefkochFehler", "FEHLER", "LEER", "OFFEN", "OK", "Quelle", "SOURCE",
    "bereit", "bestes", "detail_url", "gericht", "gewicht", "hole",
    "hole_jetzt", "merken",
    "nicht_holen", "offene", "parse_rezept", "parse_treffer", "parse_zutaten",
    "schluessel", "schritte", "such_url", "vermerken", "wunsch", "zutat_kette",
]
