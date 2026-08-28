"""Die Pick-Ansicht: eine Bestellung, nach Laden getrennt, zum Abhaken.

Diese Ansicht wird auf dem Handy im Laden benutzt, einhändig, mit dem Korb in
der anderen Hand. Daraus folgt alles Weitere: nach Laden gruppiert, weil man
nicht zwischen Rewe und Lidl hin und her läuft, und grosse Tap-Ziele mit Bild,
damit im Regal das Richtige gegriffen wird (Spec 9).

Kategorien- oder Gangsortierung gibt es bewusst nicht (Spec 16).
"""
from __future__ import annotations

import sqlite3

from picknick import db
from picknick.orders.bestellung import (UngueltigerPosten, bestellung,
                                        bestellungen, jetzt, posten, wechsle)

#: Überschrift je Laden. `db.STORES` gibt die Reihenfolge vor — „egal" steht
#: zuletzt, weil diese Posten in jedem der beiden Läden mitgenommen werden
#: können und deshalb keine eigene Fahrt begründen.
LADEN_TITEL = {"rewe": "Rewe", "lidl": "Lidl", "egal": "Egal wo"}


def nach_laden(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Die Posten einer Bestellung, gruppiert nach Laden.

    Nur Gruppen, in denen etwas steht: eine leere Überschrift „Lidl" liest
    sich im Laden wie ein Auftrag, den man vergessen hat.
    """
    alle = posten(con, order_id)
    gruppen = []
    for laden in db.STORES:
        zeilen = [p for p in alle if p["store"] == laden]
        if not zeilen:
            continue
        gruppen.append({
            "store": laden,
            "titel": LADEN_TITEL.get(laden, laden),
            "posten": zeilen,
            "n_offen": sum(1 for z in zeilen if not z["gepickt"]),
        })
    return gruppen


def offene(con: sqlite3.Connection) -> list[dict]:
    """Die Bestellungen, die noch eingekauft werden müssen."""
    return bestellungen(con, "offen")


def naechste(con: sqlite3.Connection) -> int | None:
    """Die Bestellung, mit der `/pick` aufmacht — die älteste offene."""
    liste = offene(con)
    return liste[0]["id"] if liste else None


def abhaken(con: sqlite3.Connection, item_id: int, gepickt: bool = True) -> dict:
    """Setzt oder löscht den Haken an einem Posten und zieht den Stand nach.

    Gibt die Bestellung zurück, wie sie danach dasteht — die Oberfläche muss
    unmittelbar zeigen können, dass mit dem letzten Haken alles fertig war.
    """
    row = con.execute(
        "SELECT i.id, i.order_id, o.state FROM order_item i"
        "  JOIN orders o ON o.id = i.order_id WHERE i.id = ?",
        (item_id,)).fetchone()
    if row is None:
        raise UngueltigerPosten(f"Bestellposten {item_id} gibt es nicht.")
    if row["state"] == "draft":
        raise UngueltigerPosten(
            "Im Warenkorb wird nichts abgehakt — erst abschicken, dann "
            "einkaufen.")
    con.execute("UPDATE order_item SET picked_at = ? WHERE id = ?",
                (jetzt() if gepickt else None, item_id))
    con.commit()
    return _stand_nachziehen(con, row["order_id"])


def _stand_nachziehen(con: sqlite3.Connection, order_id: int) -> dict:
    """Setzt `erledigt`/`offen` danach, ob noch ein Posten offen ist.

    Der Zustand wird abgeleitet und nicht von Hand gesetzt: sonst gibt es
    einen Knopf „fertig", den man drücken kann, obwohl die Hälfte fehlt — und
    eine erledigte Bestellung, in der noch etwas offen steht, ist schlimmer
    als gar keine Bestellübersicht.

    Der Rückweg gehört dazu. Ein Haken, der im Laden versehentlich gesetzt
    wurde, muss sich wegnehmen lassen, und die Bestellung ist dann wieder
    offen — kein zusätzlicher Zustand, nur derselbe Übergang rückwärts.
    """
    stand = con.execute(
        "SELECT count(*) AS n,"
        "       coalesce(sum(CASE WHEN picked_at IS NULL THEN 1 ELSE 0 END), 0)"
        "           AS offen"
        "  FROM order_item WHERE order_id = ?", (order_id,)).fetchone()
    aktuell = bestellung(con, order_id)
    if aktuell is None:
        raise UngueltigerPosten(f"Bestellung {order_id} gibt es nicht.")
    # Eine Bestellung ohne Posten kann es im Zustand `offen` nicht geben
    # (abschicken lehnt sie ab). Die Bedingung steht trotzdem hier, damit ein
    # leerer Rest nicht als „alles erledigt" durchgeht.
    fertig = stand["n"] > 0 and stand["offen"] == 0
    if fertig and aktuell["state"] == "offen":
        return wechsle(con, order_id, "erledigt", done_at=jetzt())
    if not fertig and aktuell["state"] == "erledigt":
        return wechsle(con, order_id, "offen", done_at=None)
    return aktuell
