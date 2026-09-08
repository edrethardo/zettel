"""kcal und Eiweiss je Tag — gerechnet aus den Packungen, nie geschätzt.

Der Entwurf (Abschnitt 5) liess kcal und Protein draussen, solange der
Katalog keine Nährwerte trug. Seit dem 06.09. schreibt der Nachtlauf
`composition` mit (`product_naehrwert`, 9.431 Produkte mit kcal), und damit
ist die Bedingung erfüllt, die dort steht: **kein geratener Wert, jede Zahl
eine Rechnung** — Bedarf in Gramm × Nährwert je 100 g, über die Zutaten
eines Tages summiert, durch die Portionen geteilt.

Was NICHT gerechnet werden kann, wird gezählt und benannt, nicht mit Null
aufgefüllt:

* eine Zutat ohne Produkt (Freitext) — der Katalog kennt sie nicht;
* ein Produkt ohne Nährwertzeile — Klopapier hat keine, Kartoffeln vom Markt
  auch nicht immer;
* ein Bedarf, der nicht in Gramm oder Millilitern steht („2 Stück", „1
  Bund") — 100 g je Stück wäre eine Annahme, und Annahmen werden hier nicht
  gemacht. ml zählt wie g, mit derselben benannten Annahme wie in `mengen`.

Die Zahl am Tag sagt deshalb immer dazu, aus wie vielen Zutaten sie
entstanden ist: „≈ 1.240 kcal je Portion, 11 von 14 Zutaten gerechnet". Ein
Tag, an dem keine einzige Zutat rechenbar war, trägt keine Zahl.
"""
from __future__ import annotations

import sqlite3

from zettel import mengen
from zettel.wochenplan import liste

#: Die Bezugsmenge, auf die sich die Nährwerte beziehen müssen. Der Händler
#: schreibt „100 g", „100g" oder „100 ml"; alles andere wird nicht umgerechnet.
BEZUG_G = 100.0


def _bezug_ok(dose) -> bool:
    text = str(dose or "").replace(" ", "").casefold()
    return text in ("100g", "100ml", "100gramm", "100milliliter")


def naehrwert(con: sqlite3.Connection, product_id) -> dict | None:
    row = con.execute(
        "SELECT dose, kcal, protein FROM product_naehrwert WHERE product_id = ?",
        (int(product_id),)).fetchone()
    return dict(row) if row else None


def zeile_rechnen(con: sqlite3.Connection, z: dict) -> tuple[float, float] | str:
    """`(kcal, protein)` einer Einkaufszeile — oder der Grund, warum nicht."""
    if not z.get("product_id"):
        return "kein Produkt"
    nw = naehrwert(con, z["product_id"])
    if nw is None or nw.get("kcal") is None:
        return "keine Nährwertangabe"
    if not _bezug_ok(nw.get("dose")):
        return f"Bezug „{nw.get('dose')}“ nicht je 100 g"
    basis = mengen.in_grundeinheit(z.get("bedarf"), z.get("einheit"))
    if basis is None:
        return "keine Menge"
    menge, einheit = basis
    if einheit not in (mengen.GRAMM, mengen.MILLILITER):
        return f"Menge in {mengen.einheit_text(einheit)}, nicht in g"
    faktor = menge / BEZUG_G
    return (faktor * float(nw["kcal"]), faktor * float(nw.get("protein") or 0.0))


def je_tag(con: sqlite3.Connection, recipe_id, portionen) -> dict | None:
    """kcal und Eiweiss eines Tages, gesamt und je Portion.

    `None` ohne Rezept. Mit Rezept immer ein Wörterbuch — auch wenn nichts
    rechenbar war: dann `kcal is None` und `offen` nennt alle Zutaten.
    """
    if not recipe_id:
        return None
    zeilen = liste.zeilen_je_rezept(con, int(recipe_id), portionen)
    kcal = protein = 0.0
    gerechnet = 0
    offen = []
    for z in zeilen:
        ergebnis = zeile_rechnen(con, z)
        if isinstance(ergebnis, str):
            offen.append({"name": z.get("name"), "grund": ergebnis})
            continue
        kcal += ergebnis[0]
        protein += ergebnis[1]
        gerechnet += 1
    n = len(zeilen)
    if gerechnet == 0:
        return {"kcal": None, "protein": None, "kcal_je_portion": None,
                "protein_je_portion": None, "gerechnet": 0, "n": n,
                "offen": offen, "portionen": portionen}
    teiler = max(1, int(portionen or 1))
    return {"kcal": round(kcal), "protein": round(protein, 1),
            "kcal_je_portion": round(kcal / teiler),
            "protein_je_portion": round(protein / teiler, 1),
            "gerechnet": gerechnet, "n": n, "offen": offen,
            "portionen": portionen}


def anreichern(con: sqlite3.Connection, plan: dict) -> dict:
    """Hängt jedem Tag sein `naehrwert` an und dem Plan den Wochenschnitt.

    Der Schnitt zählt nur Tage mit Zahl; ein Tag ohne rechenbare Zutat
    drückt ihn nicht auf null. `kcal_ziel` (je Person und Tag) wird daneben
    gelegt, nicht verrechnet: die Seite zeigt beide, der Mensch vergleicht.
    """
    mit_zahl = []
    for t in plan["tage_liste"]:
        t["naehrwert"] = je_tag(con, t["recipe_id"], t["portionen"])
        if t["naehrwert"] and t["naehrwert"]["kcal"] is not None:
            mit_zahl.append(t["naehrwert"])
    z = plan.setdefault("zusammenfassung", {})
    if mit_zahl:
        z["kcal_je_portion"] = round(
            sum(n["kcal_je_portion"] for n in mit_zahl) / len(mit_zahl))
        z["protein_je_portion"] = round(
            sum(n["protein_je_portion"] for n in mit_zahl) / len(mit_zahl), 1)
    else:
        z["kcal_je_portion"] = None
        z["protein_je_portion"] = None
    z["naehrwert_tage"] = len(mit_zahl)
    ziel = plan.get("kcal_ziel")
    z["kcal_ziel"] = ziel
    z["kcal_abstand"] = (z["kcal_je_portion"] - ziel
                         if ziel and z["kcal_je_portion"] is not None else None)
    return plan
