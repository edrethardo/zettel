"""Gerichte aus einer Quelle statt aus dem Gedächtnis des Modells (WB-338).

Drei Teile, streng getrennt, weil sie in verschiedenen Prozessen laufen:

* `chefkoch` — der Client. **Geht ins Netz** und läuft deshalb nie im
  Request-Pfad des Shops (Spec 3), genau wie der Katalog-Crawler.
* `speicher` — der Zwischenspeicher in der Datenbank. Das Einzige, was der
  Web-Prozess anfasst. Ein Treffer daraus braucht kein Netz.
* `quelle` — der Riegel dazwischen: der Web-Prozess trägt einen WUNSCH ein
  und startet `picknick.gerichte.lauf` als eigenen Prozess. Er wartet nicht
  darauf; der laufende Chat-Zug geht in der Zwischenzeit den Modellweg
  weiter. Fällt die Quelle aus, bricht nichts — es bleibt beim Modellweg.
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
    "bereit", "bestes", "detail_url", "gericht", "gewicht", "hole", "merken",
    "nicht_holen", "offene", "parse_rezept", "parse_treffer", "parse_zutaten",
    "schluessel", "schritte", "such_url", "vermerken", "wunsch", "zutat_kette",
]
