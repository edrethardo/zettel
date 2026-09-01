"""Warenkorb, Bestellung und Pick-Liste (Spec 4 und 9).

Ein Modell für alle drei: der Warenkorb IST die Bestellung im Zustand `draft`,
die abgeschickte ist dieselbe Zeile in `offen`, die abgehakte dieselbe in
`erledigt`. Kein Kopieren, kein zweites Modell, kein vierter Zustand.

Die drei Teile liegen getrennt — `bestellung.py` (Zustände und Abfragen),
`korb.py` (einlegen, ändern, abschicken), `pick.py` (im Laden abhaken) — und
werden hier gebündelt, damit Aufrufer schlicht `from zettel import orders`
schreiben können. Die Datei heisst `korb.py` und nicht `warenkorb.py`, weil
sonst der Modulname die hier re-exportierte Funktion `warenkorb()`
überdecken würde und `from zettel.orders import warenkorb` je nach
Importreihenfolge etwas anderes lieferte.
"""
from zettel.orders.bestellung import (  # noqa: F401
    UEBERGAENGE, BestellFehler, FalscherZustand, LeererWarenkorb,
    UngueltigerPosten, bestellung, bestellungen, jetzt,
    markiere_katalogstand, posten, wechsle)
from zettel.orders.pick import (  # noqa: F401
    LADEN_TITEL, POSTEN_STAENDE, abhaken, naechste, nach_laden, offene,
    setze_stand)
from zettel.orders.korb import (  # noqa: F401
    LADEN_VORGABE, abschicken, einlegen, entfernen, gebinde, genau_eines,
    inhalt, korb_anzahl, laden_setzen, menge_setzen, rechnung, summe,
    vorbelegter_laden, warenkorb, warenkorb_id, wieder_einlegen)

__all__ = [
    "UEBERGAENGE", "BestellFehler", "FalscherZustand", "LeererWarenkorb",
    "UngueltigerPosten", "LADEN_TITEL", "LADEN_VORGABE", "POSTEN_STAENDE",
    "abhaken", "abschicken", "bestellung", "bestellungen", "einlegen",
    "entfernen", "gebinde", "genau_eines", "inhalt", "jetzt", "korb_anzahl",
    "laden_setzen", "markiere_katalogstand", "menge_setzen", "nach_laden",
    "naechste", "offene", "posten", "rechnung",
    "setze_stand", "summe", "vorbelegter_laden", "warenkorb", "warenkorb_id",
    "wechsle", "wieder_einlegen",
]
