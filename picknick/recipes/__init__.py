"""Rezepte und ihre Produktverknüpfungen (Spec 4, 6 und 9).

Ein Rezept ist eine benannte Zutatenliste. Zutaten sind Katalogprodukte oder
Freitext — gemischt, gleichwertig, nicht als Sonderfall (Spec 4). Aus einem
Rezept wird mit einem Griff ein Warenkorb, und aus einer erledigten Bestellung
mit einem Griff ein Rezept.

Später kürzen Rezepte die erste Agentenstufe ab: trifft eine Chat-Anfrage ein
gespeichertes Rezept, werden dessen bereits verknüpfte Produkte direkt
vorgeschlagen — ohne Modell und ohne Suche (Spec 6). Deshalb ist die
Produktverknüpfung an der Zutat und nicht bloss ein Name.

Aufgeteilt wie `orders`: `sammlung.py` hält die Rezepte selbst,
`uebernahme.py` die beiden Wege von und zur Bestellung. Die Module heissen
nicht wie die Funktionen, die hier re-exportiert werden — sonst verdeckte der
Modulname `rezept` die Funktion `rezept()`, je nach Importreihenfolge.
"""
from picknick.orders import UngueltigerPosten  # noqa: F401
from picknick.recipes.sammlung import (  # noqa: F401
    LeeresRezept, RezeptFehler, aendern, anlegen, loeschen, rezept, rezepte,
    zutat_entfernen, zutat_hinzufuegen, zutat_menge, zutaten)
from picknick.recipes.uebernahme import (  # noqa: F401
    aus_bestellung, in_den_korb)

__all__ = [
    "LeeresRezept", "RezeptFehler", "UngueltigerPosten",
    "aendern", "anlegen", "aus_bestellung", "in_den_korb", "loeschen",
    "rezept", "rezepte", "zutat_entfernen", "zutat_hinzufuegen", "zutat_menge",
    "zutaten",
]
