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

from picknick import db


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
    """
    rows = con.execute(
        "SELECT o.id, o.state, o.note, o.created_at, o.submitted_at, o.done_at,"
        "       count(i.id) AS n_posten,"
        "       coalesce(sum(CASE WHEN i.picked_at IS NULL THEN 1 ELSE 0 END), 0)"
        "           AS n_offen"
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


def posten(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Alle Posten einer Bestellung, mit den Produktdaten daneben.

    `LEFT JOIN` und `coalesce(p.name, i.free_text)` sind der Kern der
    Gleichwertigkeit aus Spec 4: ein Freitext-Posten hat kein Produkt, muss
    aber überall — Warenkorb, Bestellung, Pick-Ansicht — eine Zeile mit Namen
    ergeben. Mit einem INNER JOIN wäre er stillschweigend verschwunden, und
    zwar genau in der Ansicht, in der er gebraucht wird: im Laden.
    """
    rows = con.execute(
        "SELECT i.id, i.order_id, i.product_id, i.free_text, i.qty, i.store,"
        "       i.picked_at,"
        "       coalesce(p.name, i.free_text) AS name,"
        "       p.unit_text, p.price_cents, p.image_path"
        "  FROM order_item i LEFT JOIN product p ON p.id = i.product_id"
        " WHERE i.order_id = ?"
        # Nach id: die Reihenfolge des Einlegens ist die einzige, die die
        # Nutzerin wiedererkennt.
        " ORDER BY i.id", (order_id,)).fetchall()
    eintraege = []
    for r in rows:
        e = dict(r)
        e["ist_freitext"] = e["product_id"] is None
        e["gepickt"] = e["picked_at"] is not None
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
