"""Bestellungen: Zustände, Posten, Übersicht.

Der Warenkorb ist eine Bestellung im Zustand `draft` (Spec 4). Es gibt kein
zweites Modell und kein Kopieren beim Abschicken — nur einen Zustandswechsel.
Deshalb steht hier alles, was für alle drei Zustände gleich gilt; die beiden
Seiten davon liegen in `warenkorb.py` (einlegen, abschicken) und `pick.py`
(abhaken).

Genau drei Zustände: `draft -> offen -> erledigt`. „Unterwegs" gibt es
ausdrücklich nicht — der Einkaufende sieht selbst, dass er im Laden steht.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from picknick import db, mengen


class BestellFehler(RuntimeError):
    """Etwas an einer Bestellung ist nicht so, wie es sein müsste."""


class FalscherZustand(BestellFehler):
    """Ein Übergang, den die Zustandsmaschine nicht kennt."""


class LeererWarenkorb(BestellFehler):
    """Abschicken ohne Posten — eine leere Bestellung hilft niemandem."""


class UngueltigerPosten(ValueError):
    """Ein Bestellposten, den es so nicht geben darf."""


#: Erlaubte Übergänge. `erledigt -> offen` ist KEIN vierter Zustand und keine
#: Aufweichung von Spec 4, sondern der Rückweg für den Fehlgriff: wer im Laden
#: daneben tippt und einen Haken wieder wegnimmt, hat eine Bestellung, die noch
#: nicht fertig ist — und die Übersicht muss das zeigen, statt sie fälschlich
#: unter „erledigt" abzulegen.
UEBERGAENGE = {
    "draft": ("offen",),
    "offen": ("erledigt",),
    "erledigt": ("offen",),
}

#: Spalten einer Bestellung, in der Reihenfolge des Schemas.
_ORDER_SPALTEN = ("id", "state", "note", "created_at", "submitted_at", "done_at")


def jetzt() -> str:
    """Zeitstempel im selben Format wie überall sonst im Projekt.

    Sekundengenau und ohne Zeitzone: dieselbe ISO-Form, die der Crawler in
    `scrape_run` schreibt und die `datetime.fromisoformat` wieder einliest.
    Mikrosekunden hätten hier keinen Nutzen und machen Zeitstempel in der
    Oberfläche nur unlesbar.
    """
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def bestellung(con: sqlite3.Connection, order_id: int) -> dict | None:
    """Eine Bestellung als Wörterbuch, oder `None`."""
    row = con.execute(
        f"SELECT {', '.join(_ORDER_SPALTEN)} FROM orders WHERE id = ?",
        (order_id,)).fetchone()
    return dict(row) if row else None


def _mit_zaehlern(con: sqlite3.Connection, state: str) -> list[dict]:
    """Bestellungen eines Zustands, jede mit Zahl der Posten und der offenen.

    Die Zähler kommen aus derselben Abfrage statt aus einem Aufruf je
    Bestellung: die Übersicht zeigt beide Zahlen an jeder Zeile, und N+1
    Abfragen für eine Zahl, die SQLite nebenbei mitliefert, wären Verschwendung.

    Seit WB-373 sind es drei Zahlen und nicht zwei, und `n_geholt` ist nicht
    `n_posten - n_offen`: dazwischen liegen die Posten, die es im Laden nicht
    gab. Ohne die eigene Zahl liesse sich „9 geholt, 2 gab's nicht" nicht
    schreiben — und genau das ist der Unterschied zwischen einer ehrlichen
    erledigten Bestellung und einer, die vollständig aussieht.
    """
    rows = con.execute(
        "SELECT o.id, o.state, o.note, o.created_at, o.submitted_at, o.done_at,"
        "       count(i.id) AS n_posten,"
        "       coalesce(sum(CASE WHEN i.picked_at IS NULL"
        "                          AND i.missing_at IS NULL"
        "                         THEN 1 ELSE 0 END), 0) AS n_offen,"
        "       coalesce(sum(CASE WHEN i.picked_at IS NOT NULL"
        "                         THEN 1 ELSE 0 END), 0) AS n_geholt,"
        "       coalesce(sum(CASE WHEN i.missing_at IS NOT NULL"
        "                         THEN 1 ELSE 0 END), 0) AS n_fehlt"
        "  FROM orders o LEFT JOIN order_item i ON i.order_id = o.id"
        " WHERE o.state = ?"
        " GROUP BY o.id", (state,)).fetchall()
    return [dict(r) for r in rows]


def bestellungen(con: sqlite3.Connection, state: str | None = None) -> list[dict]:
    """Die Bestellübersicht: offene oben, erledigte darunter (Spec 9).

    Offene aufsteigend nach Abschickzeit — wer am längsten wartet, steht oben.
    Erledigte absteigend, weil dort nur die letzten interessieren. Der `draft`
    kommt nicht vor: er ist der Warenkorb und hat seine eigene Ansicht.
    """
    if state is not None:
        if state not in db.ORDER_STATES:
            raise FalscherZustand(f"{state!r} ist kein Bestellzustand.")
        liste = _mit_zaehlern(con, state)
        schluessel = {"draft": "created_at", "offen": "submitted_at",
                      "erledigt": "done_at"}[state]
        umgekehrt = state == "erledigt"
        return sorted(liste, key=lambda b: (b[schluessel] or "", b["id"]),
                      reverse=umgekehrt)
    return bestellungen(con, "offen") + bestellungen(con, "erledigt")


def markiere_katalogstand(eintrag: dict) -> dict:
    """Setzt `ist_freitext` und `nicht_im_katalog` an einer Zeile mit Produkt.

    Die EINE Stelle im Projekt, an der dieses Feld entsteht. Bestellposten,
    Rezeptzutat und Chat-Vorschlag sind dieselbe Sache — eine Zeile, die
    entweder auf ein Produkt zeigt oder Freitext ist, per `LEFT JOIN` mit
    `product.active` daneben. Drei Kopien der Regel hätten irgendwann drei
    Namen und zwei Bedeutungen; wer den Namen ändert, ändert ihn hier und
    damit überall.

    Freitext ist NIE „nicht mehr im Katalog": er war nie darin und behauptet
    das auch nicht. Ohne diese Unterscheidung bekäme jede Freitext-Zeile eine
    Warnung, die nicht stimmt — und eine Warnung, die überall steht, liest
    im Laden niemand mehr.

    `active` kommt aus dem JOIN. Fehlt die Spalte oder ist sie `NULL` (die
    Produktzeile ist ganz verschwunden), gilt die Zeile als nicht mehr im
    Katalog: lieber ein Hinweis zu viel als eine verschwiegene Lücke.
    """
    eintrag["ist_freitext"] = eintrag.get("product_id") is None
    eintrag["nicht_im_katalog"] = (
        not eintrag["ist_freitext"] and (eintrag.get("active") or 0) != 1)
    return eintrag


def posten(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Alle Posten einer Bestellung, mit den Produktdaten daneben.

    `LEFT JOIN` und `coalesce(p.name, i.free_text)` sind der Kern der
    Gleichwertigkeit aus Spec 4: ein Freitext-Posten hat kein Produkt, muss
    aber überall — Warenkorb, Bestellung, Pick-Ansicht — eine Zeile mit Namen
    ergeben. Mit einem INNER JOIN wäre er stillschweigend verschwunden, und
    zwar genau in der Ansicht, in der er gebraucht wird: im Laden.

    `p.active` steht aus demselben Grund mit im SELECT. Produkte werden beim
    Crawl nie gelöscht, sondern auf `active = 0` gesetzt (Spec 5.3) — ein
    Posten aus einer älteren Bestellung zeigt also irgendwann auf ein Produkt,
    das es im Laden womöglich nicht mehr gibt. Die Rezeptansicht sagt das
    längst; solange diese Abfrage `active` nicht mitlas, war die Information
    ab dem Warenkorb weg, und der Schaden fiel dort an, wo er am teuersten
    ist: vor dem Regal.
    """
    rows = con.execute(
        "SELECT i.id, i.order_id, i.product_id, i.free_text, i.qty, i.store,"
        "       i.picked_at, i.missing_at,"
        "       i.need_amount, i.need_unit, i.hand_qty,"
        "       coalesce(p.name, i.free_text) AS name,"
        "       p.unit_text, p.price_cents, p.image_path, p.active"
        "  FROM order_item i LEFT JOIN product p ON p.id = i.product_id"
        " WHERE i.order_id = ?"
        # Nach id: die Reihenfolge des Einlegens ist die einzige, die die
        # Nutzerin wiedererkennt.
        " ORDER BY i.id", (order_id,)).fetchall()
    eintraege = []
    for r in rows:
        e = markiere_katalogstand(dict(r))
        e["gepickt"] = e["picked_at"] is not None
        e["fehlt"] = e["missing_at"] is not None
        # Ein Feld statt zweier Flaggen, damit Vorlage und Test denselben
        # Namen benutzen wie die Zustandsmaschine in `pick.py` — und damit
        # „abgehakt und vermisst zugleich" auch dann nicht darstellbar ist,
        # wenn eine fremde Hand die Datenbank anfasst: `gepickt` gewinnt.
        e["stand"] = ("gepickt" if e["gepickt"]
                      else "fehlt" if e["fehlt"] else "offen")
        # Was gerechnet wurde, steht an der Zeile und nicht nur im Trace
        # (WB-362, Regel 6): eine stumme 2 im Mengenfeld ist genau das, was
        # dieses Ticket verhindern soll. Der Satz entsteht in `mengen` und
        # nicht in der Vorlage, damit die Tests denselben prüfen, den die
        # Nutzerin liest.
        rechnung = mengen.rechne(e["need_amount"], e["need_unit"],
                                 e["unit_text"])
        e["rechnung"] = rechnung
        e["bedarf_satz"] = mengen.satz(rechnung, produkt=e["name"],
                                       unit_text=e["unit_text"],
                                       qty=int(e["qty"]))
        eintraege.append(e)
    return eintraege


def wechsle(con: sqlite3.Connection, order_id: int, ziel: str,
            **zeitstempel) -> dict:
    """Führt einen Zustandswechsel aus, wenn die Maschine ihn kennt.

    Der Umweg über diese Funktion statt eines direkten UPDATE ist Absicht: so
    gibt es genau eine Stelle, an der ein Zustand entsteht, und ein neuer
    Zustand kann nicht versehentlich durch ein UPDATE irgendwo im Web-Modul
    eingeführt werden.
    """
    aktuell = bestellung(con, order_id)
    if aktuell is None:
        raise BestellFehler(f"Bestellung {order_id} gibt es nicht.")
    if ziel not in UEBERGAENGE.get(aktuell["state"], ()):
        raise FalscherZustand(
            f"Bestellung {order_id} steht auf {aktuell['state']!r} und kann "
            f"nicht nach {ziel!r} — erlaubt sind "
            f"{UEBERGAENGE.get(aktuell['state'], ())}.")
    felder = ["state = ?"]
    werte: list = [ziel]
    for spalte, wert in zeitstempel.items():
        if spalte not in ("submitted_at", "done_at", "note"):
            raise BestellFehler(f"{spalte} ist kein Feld, das ein Wechsel setzt.")
        felder.append(f"{spalte} = ?")
        werte.append(wert)
    con.execute(f"UPDATE orders SET {', '.join(felder)} WHERE id = ?",
                (*werte, order_id))
    con.commit()
    return bestellung(con, order_id)
