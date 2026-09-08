"""Was zur Wahl steht: die Gerichte, die der Haushalt hat.

Die Vorlage für zwei Leser — das `<select>` am Tag (Phase 1) und das Modell
in `plan.woche` (Phase 2). Beide bekommen DIESELBE Liste; das ist der Kern
der Zusicherung aus Abschnitt 4 des Entwurfs: das Modell darf nur wählen,
was hier steht, und was hier steht, kann auch ein Mensch wählen.

**Ein Rezept je Gericht.** Die Datenbank hält zu „Lasagne" sieben Rezepte —
das gewählte und die Alternativen aus `dish_treffer`, die jemand einmal
angesehen hat. Zur Wahl steht das, worauf das Gericht ZEIGT (`dish.recipe_id`),
dazu jedes Rezept ohne Quelle (die eigene Sammlung). Sonst stünden sieben
Lasagnen nebeneinander, und ein Modell, das „Abwechslung" versteht, plante
Lasagne, Lasagne Bolognese und Lasagne alla Bolognese.

**Zeit unbekannt heisst nicht „passt".** Mit `max_minuten` fällt weg, was
bekanntermassen zu lang ist; was keine Zeit hat (selbst angelegt), bleibt
mit `passt_zeit = None` und wird so gezeigt.
"""
from __future__ import annotations

import sqlite3

from zettel.assistant.zugrezept import gesamtzeit, zeitsatz
from zettel.wochenplan.rahmen import Rahmen

_SQL = (
    "SELECT r.id, r.name, r.servings, r.prep_minutes, r.cook_minutes,"
    "       r.rest_minutes, r.source, r.source_rating, r.source_votes,"
    "       (SELECT count(*) FROM dish d WHERE d.recipe_id = r.id"
    "           AND d.status = 'ok') AS gezeigt,"
    "       (SELECT count(*) FROM recipe_item ri"
    "         WHERE ri.recipe_id = r.id) AS n_verknuepft"
    "  FROM recipe r"
    " WHERE r.source IS NULL"
    "    OR EXISTS (SELECT 1 FROM dish d WHERE d.recipe_id = r.id"
    "                  AND d.status = 'ok')"
    " ORDER BY r.name COLLATE NOCASE, r.id")


def _zutatennamen(con: sqlite3.Connection, recipe_id: int) -> list[str]:
    """Die Zutaten als Namen — aus der Quelle, sonst aus den verknüpften
    Produkten. Ohne Mengen: die Vorlage sagt, WAS ein Gericht braucht; wie
    viel, rechnet die Einkaufsliste."""
    rows = con.execute(
        "SELECT name FROM recipe_ingredient WHERE recipe_id = ?"
        " ORDER BY pos, id", (recipe_id,)).fetchall()
    namen = [r["name"] for r in rows if r["name"]]
    if namen:
        return namen
    rows = con.execute(
        "SELECT coalesce(p.name, ri.free_text) AS name"
        "  FROM recipe_item ri LEFT JOIN product p ON p.id = ri.product_id"
        " WHERE ri.recipe_id = ? ORDER BY ri.id", (recipe_id,)).fetchall()
    return [r["name"] for r in rows if r["name"]]


def gerichte(con: sqlite3.Connection,
             rahmen: Rahmen | None = None) -> list[dict]:
    """Die Rezepte, die zur Wahl stehen — mit Zeit, Portionen, Zutatennamen.

    Nur Rezepte mit mindestens einer Zutat: ein leeres Rezept ergäbe einen
    Tag ohne Einkauf, und der sähe aus wie geplant.
    """
    grenze = rahmen.max_minuten if rahmen else None
    liste = []
    for r in con.execute(_SQL).fetchall():
        k = dict(r)
        zutaten = _zutatennamen(con, int(k["id"]))
        if not zutaten:
            continue
        k["zutaten"] = zutaten
        k["n_zutaten"] = len(zutaten)
        k["minuten"] = gesamtzeit(k)
        k["zeitsatz"] = zeitsatz(k["minuten"])
        if k["minuten"] is None:
            k["passt_zeit"] = None
        elif grenze:
            k["passt_zeit"] = k["minuten"] <= grenze
        else:
            k["passt_zeit"] = True
        if k["passt_zeit"] is False:
            continue
        liste.append(k)
    return liste


def nach_id(vorgelegt: list[dict]) -> dict[int, dict]:
    return {int(g["id"]): g for g in vorgelegt}
