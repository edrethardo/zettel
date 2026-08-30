"""Die beiden Wege zwischen Rezept und Bestellung.

Hinein: „daraus ein Rezept machen" an einer Bestellung — der Moment, in dem
die Zutaten ohnehin beisammen sind (Spec 6). Seit WB-337 gilt das auch für
den Warenkorb; die umgedrehte Begründung steht an `aus_bestellung()`.
Hinaus: „alles in den Warenkorb" — das Rezept füllt den `draft`.

Beide Richtungen benutzen die vorhandenen Bausteine (`orders.posten`,
`korb.einlegen`) und bauen nichts davon nach. Insbesondere das Zusammenfassen
gleicher Zeilen und die Vorbelegung des Ladens sind Sache von
`korb.einlegen()` und sollen es bleiben — ein Rezept, das an ihm vorbei
INSERTet, hätte zwei Zeilen Milch im Korb, wo eine hingehört.
"""
from __future__ import annotations

import sqlite3

from zettel import mengen, orders
from zettel.orders import korb
from zettel.recipes.sammlung import (LeeresRezept, RezeptFehler, anlegen,
                                       rezept)


def in_den_korb(con: sqlite3.Connection, recipe_id: int,
                portionen=None) -> dict:
    """Legt alle Zutaten eines Rezepts in den gemeinsamen Warenkorb.

    `portionen` ist die Zahl, für die diesmal gekocht wird (WB-362). Ohne
    Angabe gilt die Portionszahl des Rezepts — **die Vorgabe kommt vom
    Rezept, die Entscheidung von hier**, und das Rezept wird dabei NICHT
    geändert: „diesmal für acht" ist eine Aussage über diesen Einkauf und
    keine über das Rezept.

    Die Reihenfolge der Rechenschritte ist die aus dem Nachtrag des Tickets,
    und sie ist über zwei Module verteilt:

    1. **Hier** wird je Zutat die Menge auf die gewählten Portionen skaliert
       (`mengen.skaliere`) — linear, ohne Rundung.
    2. **In `korb.einlegen`** wird je PRODUKT zusammengezählt, über Rezepte
       und über von Hand eingelegte Posten hinweg.
    3. **Danach**, und nur danach, wird gegen die Packungsgrösse gerundet.

    Deshalb steht hier kein `ceil` und keine Packungsrechnung: jede Rundung an
    dieser Stelle wäre eine Rundung VOR dem Zusammenzählen, und genau die
    erzeugt bei zwei Rezepten à 40 g Knoblauch zwei Packungen statt einer.

    Gibt einen Bericht zurück, kein blosses „ok". Der Grund dafür ist älter
    als dieses Ticket: eine Zutat kann inzwischen aus dem Katalog gefallen
    sein (`active = 0`, Spec 5.3). Sie wird trotzdem eingelegt — das Produkt
    existiert weiter, der Name stimmt, im Laden steht es vermutlich immer noch
    im Regal — aber der Aufrufer bekommt sie in `ausgemustert` genannt und
    kann es sagen. Seit WB-362 steht im Bericht ausserdem, WAS gerechnet
    wurde: `zeilen` trägt je Zutat die benötigte Menge, die Packungszahl und
    den Grund, wenn sich nichts ausrechnen liess.

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

    basis = r["servings"]
    gewaehlt = _portionen(portionen, basis)
    faktor = mengen.faktor(basis, gewaehlt)

    eingelegt, ausgemustert, gescheitert, zeilen = [], [], [], []
    for z in r["zutaten"]:
        gebraucht = mengen.skaliere(z["amount"], basis, gewaehlt)
        try:
            item_id = korb.einlegen(
                con, product_id=z["product_id"], free_text=z["free_text"],
                qty=z["qty"], menge=gebraucht, einheit=z["unit"],
                portionen=gewaehlt)
        except orders.UngueltigerPosten as e:
            # Kann nur eine Zutat treffen, deren Produktzeile ganz verschwunden
            # ist — den Rest des Rezepts hält das nicht auf, aber verschwiegen
            # wird es auch nicht.
            gescheitert.append({**z, "grund": str(e)})
            continue
        eingelegt.append(z)
        zeilen.append(_zeile(con, z, item_id, gebraucht))
        if z["nicht_im_katalog"]:
            ausgemustert.append(z)

    bericht = {"rezept": r, "eingelegt": eingelegt,
               "ausgemustert": ausgemustert, "gescheitert": gescheitert,
               "portionen": gewaehlt, "portionen_rezept": basis,
               "faktor": faktor, "zeilen": zeilen}
    bericht["meldung"] = _meldung(bericht)
    return bericht


def _portionen(gewuenscht, vom_rezept):
    """Die Portionszahl dieses Einkaufs. Vorbelegt mit der des Rezepts.

    Unsinn und Zahlen unter 1 fallen auf die Vorgabe zurück statt das
    Einlegen zu verhindern: die Zahl kommt aus einem Formularfeld auf einem
    Telefon, und ein Tippfehler darf keinen Einkauf kosten.
    """
    try:
        zahl = int(str(gewuenscht).strip())
    except (TypeError, ValueError):
        return vom_rezept
    return zahl if zahl > 0 else vom_rezept


def _zeile(con: sqlite3.Connection, zutat: dict, item_id: int,
           gebraucht) -> dict:
    """Was aus EINER Zutat im Korb geworden ist — samt Rechenweg.

    Gelesen wird aus dem Korb und nicht aus der Zutat: die Packungszahl
    entsteht erst dort, aus der Summe über alle Rezepte. Sie hier
    auszurechnen hiesse, dieselbe Rechnung ein zweites Mal zu führen — und
    zwar ohne das Zusammenzählen, also falsch.
    """
    posten = con.execute(
        "SELECT qty, need_amount, need_unit FROM order_item WHERE id = ?",
        (item_id,)).fetchone()
    rechnung = korb.rechnung(con, item_id)
    return {
        "name": zutat["name"],
        "gebraucht": gebraucht,
        "einheit": zutat["unit"],
        "qty": int(posten["qty"]),
        "unit_text": zutat.get("unit_text"),
        "rechnung": rechnung,
        "satz": mengen.satz(rechnung, produkt=zutat["name"],
                            unit_text=zutat.get("unit_text"),
                            qty=int(posten["qty"])),
    }


def _gerechnet(zeile: dict) -> str:
    """Eine Zutat als Rechenweg: „Pomito: 1000 ml, das sind 2 × 500 g".

    Das Gebinde wird genommen, wie es am Produkt steht („0,75 l"), und nicht
    in der Grundeinheit ausgeschrieben („750 ml"): im Laden steht die Flasche
    mit dem Etikett des Katalogs im Regal, nicht mit dem der Rechnung.
    """
    r = zeile["rechnung"]
    gebinde = (zeile["unit_text"] or "").strip() or mengen.schreibe(
        r.packung, r.packung_einheit)
    return (f"{zeile['name']}: "
            f"{mengen.schreibe(r.bedarf, r.bedarf_einheit)}, "
            f"das sind {r.packungen} × {gebinde}")


def _bedarf(zeile: dict) -> str:
    """Die gebrauchte Menge einer Zeile als Text: „6 Stk"."""
    r = zeile["rechnung"]
    return mengen.schreibe(r.bedarf, r.bedarf_einheit)


def _meldung(bericht: dict) -> str:
    """Ein Satz für die Oberfläche, aus dem Bericht gebaut.

    Steht hier und nicht in der Vorlage, damit die Tests denselben Satz prüfen
    können, den die Nutzerin liest.

    Seit WB-362 steht die Portionszahl mit drin, sobald sie von der des
    Rezepts abweicht — „für 8 statt 4 Portionen". Das ist Regel 6: eine
    stumme 2 im Mengenfeld erklärt nichts, und wer nicht sieht, wofür
    gerechnet wurde, kann die Zahl auch nicht bestreiten.
    """
    name = bericht["rezept"]["name"]
    n = len(bericht["eingelegt"])
    wort = "Zutat liegt" if n == 1 else "Zutaten liegen"
    portionen = bericht.get("portionen")
    basis = bericht.get("portionen_rezept")
    # „für 1 Portionen" liest sich wie ein Fehler und ist einer.
    zahlwort = "Portion" if portionen == 1 else "Portionen"
    if portionen and basis and portionen != basis:
        kopf = (f"„{name}“ für {portionen} statt {basis} {zahlwort} — "
                f"{n} {wort} im Korb.")
    elif portionen:
        kopf = f"„{name}“ für {portionen} {zahlwort} — {n} {wort} im Korb."
    else:
        kopf = f"„{name}“ — {n} {wort} im Korb."
    teile = [kopf]
    gerechnet = [z for z in bericht.get("zeilen") or []
                 if z["rechnung"].ausrechenbar]
    if gerechnet:
        teile.append("Gerechnet: "
                     + "; ".join(_gerechnet(z) for z in gerechnet) + ".")
    offen = [z for z in bericht.get("zeilen") or []
             if z["rechnung"].bedarf is not None
             and not z["rechnung"].ausrechenbar
             and not z["rechnung"].freitext]
    if offen:
        # Regel 4: nicht raten, aber auch nicht verschweigen. Wer nicht liest,
        # dass die Menge unverändert blieb, hält die Zahl im Korb für
        # ausgerechnet.
        teile.append("Nicht ausrechenbar und deshalb unverändert: " + "; ".join(
            f"{z['name']} ({z['rechnung'].grund})" for z in offen) + ".")
    # Die Freitexte stehen eigens da und nicht in der Liste darüber (WB-385):
    # seit sie ihre Menge behalten, wären sie dort die Mehrheit, und N-mal
    # derselbe Grund („ein Freitext hat keine Packung") sagt beim zweiten Mal
    # nichts mehr. Ausserdem ist es hier eine andere Auskunft: sie sind das,
    # was der Katalog nicht führt und was anderswo besorgt werden muss —
    # dafür ist die Menge die einzige Angabe, die es überhaupt gibt.
    ohne_katalog = [z for z in bericht.get("zeilen") or []
                    if z["rechnung"].freitext
                    and z["rechnung"].bedarf is not None]
    if ohne_katalog:
        namen = "; ".join(f"{z['name']} ({_bedarf(z)})" for z in ohne_katalog)
        teile.append("Der Katalog führt sie nicht, die Menge steht trotzdem "
                     f"am Posten: {namen}.")
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

    Der Knopf dazu steht an der erledigten Bestellung, weil das der Moment
    ist, in dem die Zutaten ohnehin beisammen sind (Spec 6).

    **Der Warenkorb ist seit WB-337 nicht mehr ausgenommen, und das ist eine
    umgedrehte Entscheidung.** Hier stand bis dahin:

        „Aus einer Absicht, die noch nicht eingekauft wurde, ein Rezept zu
         machen, hiesse eine Mahlzeit zu behaupten, die es noch nicht gab."

    Der Satz stimmt für eine Chronik und nicht für dieses Feld. **Ein Rezept
    ist keine Chronik gegessener Mahlzeiten, sondern eine Einkaufsvorlage** —
    ob der Einkauf schon stattgefunden hat, ist dafür ohne Belang. Der Preis
    des alten Satzes war hoch: der natürliche Moment, in dem ein Rezept
    entsteht, ist der Chat-Zug „alles für Spaghetti Bolognese", und der
    landet im `draft`. Ein gespeichertes Rezept überspringt danach das Modell
    vollständig (`zettel.path = "recipe"`), das Anlegen zu erschweren
    arbeitet also gegen den stärksten Mechanismus des Shops.

    Der Satz bleibt hier stehen, statt gelöscht zu werden: wer später liest,
    soll sehen, dass die Frage gestellt und anders beantwortet wurde.

    Der produktive Weg aus einem Chat-Zug geht trotzdem NICHT hier durch,
    sondern über `assistant.entwurf` — er nimmt nur die Gerichtszutaten,
    nicht den ganzen Korb. Genau darum ging es im Ticket: das Klopapier liegt
    im Korb und gehört in kein Rezept.

    Menge, Produktbindung und Freitext werden übernommen; der Laden nicht: er
    hängt am Posten und wird beim nächsten Einlegen ohnehin neu vorbelegt
    (Spec 4).
    """
    b = orders.bestellung(con, order_id)
    if b is None:
        raise RezeptFehler(f"Bestellung {order_id} gibt es nicht.")
    zeilen = orders.posten(con, order_id)
    if not zeilen:
        raise LeeresRezept(
            f"Bestellung {order_id} hat keine Posten — daraus wird kein "
            "Rezept.")
    return anlegen(
        con, (name or "").strip() or _vorschlag(b),
        # Menge und Einheit gehen MIT (WB-362): eine Bestellung, die aus
        # einem Rezept entstanden ist, trägt die benötigte Menge in
        # `order_item` — und ein Rezept, das daraus wieder entsteht, soll sie
        # nicht auf dem Weg verlieren. Sonst skalierte dasselbe Rezept beim
        # zweiten Mal nicht mehr.
        zutaten=[{"product_id": z["product_id"], "free_text": z["free_text"],
                  "qty": z["qty"], "amount": z["need_amount"],
                  "unit": z["need_unit"]} for z in zeilen])
