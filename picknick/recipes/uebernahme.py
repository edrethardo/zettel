"""Die beiden Wege zwischen Rezept und Bestellung.

Hinein: „daraus ein Rezept machen" an einer erledigten Bestellung — der
einzige Moment, in dem die Zutaten ohnehin beisammen sind (Spec 6).
Hinaus: „alles in den Warenkorb" — das Rezept füllt den `draft`.

Beide Richtungen benutzen die vorhandenen Bausteine (`orders.posten`,
`korb.einlegen`) und bauen nichts davon nach. Insbesondere das Zusammenfassen
gleicher Zeilen und die Vorbelegung des Ladens sind Sache von
`korb.einlegen()` und sollen es bleiben — ein Rezept, das an ihm vorbei
INSERTet, hätte zwei Zeilen Milch im Korb, wo eine hingehört.
"""
from __future__ import annotations

import sqlite3

from picknick import orders
from picknick.orders import korb
from picknick.recipes.sammlung import (LeeresRezept, RezeptFehler, anlegen,
                                       rezept)


def in_den_korb(con: sqlite3.Connection, recipe_id: int) -> dict:
    """Legt alle Zutaten eines Rezepts in den gemeinsamen Warenkorb.

    Gibt einen Bericht zurück, kein blosses „ok". Der Grund ist der Kern
    dieses Tickets: eine Zutat kann inzwischen aus dem Katalog gefallen sein
    (`active = 0`, Spec 5.3). Sie wird trotzdem eingelegt — das Produkt
    existiert weiter, der Name stimmt, im Laden steht es vermutlich immer noch
    im Regal — aber der Aufrufer bekommt sie in `ausgemustert` genannt und
    kann es sagen. Stillschweigend weglassen wäre der schlimmste Ausgang.

    Ein Rezept ohne Zutaten wird abgelehnt statt geräuschlos nichts zu tun:
    sonst drückt jemand den Knopf, es passiert nichts, und er hält den Shop
    für kaputt. Ausserdem entstünde dabei ein leerer `draft` in der Datenbank,
    nur weil jemand geschaut hat.
    """
    r = rezept(con, recipe_id)
    if not r["zutaten"]:
        raise LeeresRezept(
            f"„{r['name']}“ hat keine Zutaten — daraus wird "
            "kein Einkauf. Trag erst ein, was hineingehört.")

    eingelegt, ausgemustert, gescheitert = [], [], []
    for z in r["zutaten"]:
        try:
            korb.einlegen(con, product_id=z["product_id"],
                          free_text=z["free_text"], qty=z["qty"])
        except orders.UngueltigerPosten as e:
            # Kann nur eine Zutat treffen, deren Produktzeile ganz verschwunden
            # ist — den Rest des Rezepts hält das nicht auf, aber verschwiegen
            # wird es auch nicht.
            gescheitert.append({**z, "grund": str(e)})
            continue
        eingelegt.append(z)
        if z["nicht_im_katalog"]:
            ausgemustert.append(z)

    bericht = {"rezept": r, "eingelegt": eingelegt,
               "ausgemustert": ausgemustert, "gescheitert": gescheitert}
    bericht["meldung"] = _meldung(bericht)
    return bericht


def _meldung(bericht: dict) -> str:
    """Ein Satz für die Oberfläche, aus dem Bericht gebaut.

    Steht hier und nicht in der Vorlage, damit die Tests denselben Satz prüfen
    können, den die Nutzerin liest.
    """
    name = bericht["rezept"]["name"]
    n = len(bericht["eingelegt"])
    wort = "Zutat liegt" if n == 1 else "Zutaten liegen"
    teile = [f"„{name}“ — {n} {wort} im Korb."]
    if bericht["ausgemustert"]:
        namen = ", ".join(z["name"] for z in bericht["ausgemustert"])
        teile.append(
            f"Nicht mehr im Katalog: {namen} — liegt trotzdem im Korb, "
            "aber der Preis kann alt sein und im Laden musst du selbst "
            "schauen.")
    if bericht["gescheitert"]:
        namen = ", ".join(z["name"] for z in bericht["gescheitert"])
        teile.append(f"Nicht eingelegt: {namen}.")
    return " ".join(teile)


def _vorschlag(b: dict) -> str:
    """Der vorgeschlagene Rezeptname aus einer Bestellung."""
    stand = b.get("done_at") or b.get("submitted_at") or b.get("created_at")
    if stand:
        return f"Einkauf vom {str(stand)[:10]}"
    return f"Bestellung Nr. {b['id']}"


def aus_bestellung(con: sqlite3.Connection, order_id: int,
                   name: str | None = None) -> int:
    """Macht aus den Posten einer Bestellung ein Rezept. Gibt dessen id zurück.

    Der Knopf dazu steht an der erledigten Bestellung, weil das der einzige
    Moment ist, in dem die Zutaten ohnehin beisammen sind (Spec 6). Der
    Warenkorb ist ausgenommen: aus einer Absicht, die noch nicht eingekauft
    wurde, ein Rezept zu machen, hiesse eine Mahlzeit zu behaupten, die es
    noch nicht gab. Abgeschickte offene Bestellungen sind erlaubt — wer schon
    im Laden steht, weiss meistens schon, dass daraus ein Rezept werden soll.

    Menge, Produktbindung und Freitext werden übernommen; der Laden nicht: er
    hängt am Posten und wird beim nächsten Einlegen ohnehin neu vorbelegt
    (Spec 4).
    """
    b = orders.bestellung(con, order_id)
    if b is None:
        raise RezeptFehler(f"Bestellung {order_id} gibt es nicht.")
    if b["state"] == "draft":
        raise RezeptFehler(
            "Aus dem Warenkorb wird kein Rezept — schick ihn erst ab. Ein "
            "Rezept beschreibt einen Einkauf, den es gab.")
    zeilen = orders.posten(con, order_id)
    if not zeilen:
        raise LeeresRezept(
            f"Bestellung {order_id} hat keine Posten — daraus wird kein "
            "Rezept.")
    return anlegen(
        con, (name or "").strip() or _vorschlag(b),
        zutaten=[{"product_id": z["product_id"], "free_text": z["free_text"],
                  "qty": z["qty"]} for z in zeilen])
