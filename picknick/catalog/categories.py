"""Kategoriebaum und Blättern im Katalog.

Der Baum wird nicht als eigene Tabelle gepflegt, sondern bei Bedarf aus den
Spalten `category_l1/l2/l3` der aktiven Produkte gebildet. Grund: Knuspr
liefert die Kategorien in jeder Produktantwort mit, eine zweite Tabelle wäre
eine zweite Wahrheit — und die geht beim nächsten Crawl auseinander. Der
Katalog ist klein genug, dass die Gruppierung billig ist.

Nur aktive Produkte zählen. Ein Produkt, das der letzte Lauf nicht mehr sah,
wird `active = 0` statt gelöscht (Spec 5.3); es darf aber keine Kategorie in
der Oberfläche am Leben halten, in der nichts mehr steht.
"""
from __future__ import annotations

import sqlite3

#: Die drei Ebenen in der Reihenfolge, in der sie ineinander stecken.
EBENEN = ("category_l1", "category_l2", "category_l3")

#: Spalten, die eine Produktzeile in der Kategorieansicht mitbringt.
_PRODUKT_SPALTEN = (
    "id", "source", "external_id", "name", "brand", "price_cents",
    "price_per_unit_cents", "unit_text", "unit", "image_path",
    "category_l1", "category_l2", "category_l3", "in_stock", "last_seen_at",
)


def tree(con: sqlite3.Connection) -> list[dict]:
    """Der dreistufige Kategoriebaum der aktiven Produkte.

    Jeder Knoten ist `{"name", "anzahl", "kinder"}`. `anzahl` ist die Zahl der
    Produkte im ganzen Teilbaum, nicht nur direkt darunter — die Oberfläche
    zeigt sie neben dem Kategorienamen, und dort will niemand „Molkerei (0)"
    lesen, nur weil alle Produkte eine Ebene tiefer hängen.

    Fehlt einem Produkt eine Ebene, endet sein Ast dort. Ein Knoten „(ohne
    Kategorie)" wäre eine erfundene Kategorie; ein leerer Name in der Liste
    wäre ein toter Link.
    """
    rows = con.execute(
        "SELECT category_l1 AS l1, category_l2 AS l2, category_l3 AS l3,"
        "       count(*) AS n"
        "  FROM product WHERE active = 1"
        " GROUP BY l1, l2, l3").fetchall()

    baum: dict = {}
    for r in rows:
        pfad = [r["l1"], r["l2"], r["l3"]]
        knoten = baum
        for name in pfad:
            if not name:
                break
            eintrag = knoten.setdefault(name, {"anzahl": 0, "kinder": {}})
            eintrag["anzahl"] += r["n"]
            knoten = eintrag["kinder"]
    return _als_liste(baum)


def _als_liste(knoten: dict) -> list[dict]:
    """dict -> alphabetisch sortierte Liste. Erst hier wird die Form stabil."""
    return [{"name": name,
             "anzahl": eintrag["anzahl"],
             "kinder": _als_liste(eintrag["kinder"])}
            for name, eintrag in sorted(knoten.items())]


def by_category(con: sqlite3.Connection,
                category_l1: str | None = None,
                category_l2: str | None = None,
                category_l3: str | None = None,
                limit: int = 40, offset: int = 0) -> list[dict]:
    """Aktive Produkte einer Kategorie, blätterbar.

    Angegeben wird so viel vom Pfad, wie bekannt ist: nur `category_l1` liefert
    alles darunter. Ohne jede Angabe kommt der ganze Katalog — das ist die
    Startansicht, kein Sonderfall.
    """
    bedingungen = ["active = 1"]
    werte: list = []
    for spalte, wert in zip(EBENEN, (category_l1, category_l2, category_l3)):
        if wert is not None:
            bedingungen.append(f"{spalte} = ?")
            werte.append(wert)
    spalten = ", ".join(_PRODUKT_SPALTEN)
    rows = con.execute(
        f"SELECT {spalten} FROM product"
        f" WHERE {' AND '.join(bedingungen)}"
        # Sortiert nach Name, id als Tiebreaker: ohne eine totale Ordnung darf
        # SQLite bei LIMIT/OFFSET dieselbe Zeile zweimal oder gar nicht
        # liefern — Blättern würde dann Produkte verschlucken.
        "  ORDER BY name COLLATE NOCASE ASC, id ASC"
        "  LIMIT ? OFFSET ?", (*werte, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def count_by_category(con: sqlite3.Connection,
                      category_l1: str | None = None,
                      category_l2: str | None = None,
                      category_l3: str | None = None) -> int:
    """Wie viele aktive Produkte in der Kategorie stehen.

    Die Oberfläche braucht das für „Seite 2 von 5"; ohne die Gesamtzahl weiss
    sie erst nach der letzten leeren Seite, dass Schluss ist.
    """
    bedingungen = ["active = 1"]
    werte: list = []
    for spalte, wert in zip(EBENEN, (category_l1, category_l2, category_l3)):
        if wert is not None:
            bedingungen.append(f"{spalte} = ?")
            werte.append(wert)
    return int(con.execute(
        f"SELECT count(*) AS n FROM product WHERE {' AND '.join(bedingungen)}",
        werte).fetchone()["n"])
