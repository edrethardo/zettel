"""Der Warenkorb — die eine Bestellung im Zustand `draft`.

Es existiert genau ein `draft`, gemeinsam für beide Personen (Spec 4).
Getrennte Warenkörbe sind bei zwei Menschen in einem Haushalt keine Funktion,
sondern eine Fehlerquelle: sonst wird Milch doppelt gekauft. Wer eingelegt
hat, wird nicht erfasst.

Durchgesetzt wird das in der Datenbank (`ux_orders_ein_draft` in `db.py`) und
nicht allein hier. Der Grund steht in `warenkorb()`.
"""
from __future__ import annotations

import sqlite3

from picknick import db
from picknick.orders.bestellung import (LeererWarenkorb, UngueltigerPosten,
                                        jetzt, posten, wechsle)

#: Der Laden, wenn nichts anderes bekannt ist. „egal" ist ehrlich: der Katalog
#: ist ladenneutral (Spec 5.1), und eine geratene Vorbelegung auf „rewe" wäre
#: eine Behauptung, die niemand aufgestellt hat.
LADEN_VORGABE = "egal"


def _draft_id(con: sqlite3.Connection) -> int | None:
    row = con.execute(
        "SELECT id FROM orders WHERE state = 'draft' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def warenkorb(con: sqlite3.Connection) -> int:
    """Die id des gemeinsamen Warenkorbs; legt ihn an, wenn es keinen gibt.

    Der naheliegende Aufbau — nachsehen, und wenn nichts da ist, anlegen — hat
    zwischen den beiden Schritten ein Loch: rufen zwei Anfragen gleichzeitig
    auf, sehen beide „keiner da" und legen beide einen an. Danach gibt es zwei
    Warenkörbe, und genau das darf es nie geben.

    Deshalb ist der Zwang ein partieller UNIQUE-Index in der Datenbank, und
    diese Funktion behandelt den verlorenen Wettlauf als Normalfall: schlägt
    der INSERT fehl, hat ein anderer den Korb angelegt — dann wird dessen id
    zurückgegeben. So kann kein Aufruf einen zweiten `draft` erzeugen, auch
    nicht aus einem zweiten Prozess.
    """
    vorhanden = _draft_id(con)
    if vorhanden is not None:
        return vorhanden
    try:
        cur = con.execute(
            "INSERT INTO orders (state, created_at) VALUES ('draft', ?)",
            (jetzt(),))
        con.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        con.rollback()
        vorhanden = _draft_id(con)
        if vorhanden is None:
            # Der Index hat abgelehnt, aber es steht kein draft da: dann war es
            # ein anderer Constraint und das Verschlucken wäre eine Lüge.
            raise
        return vorhanden


def warenkorb_id(con: sqlite3.Connection) -> int | None:
    """Die id des Warenkorbs, ohne einen anzulegen — `None`, wenn keiner steht.

    Das Gegenstück zu `warenkorb()`, aus demselben Grund wie `korb_anzahl()`:
    wer nur nachsieht (der Chatverlauf im Warenkorb, WB-327), soll keine
    Bestellung in die Datenbank schreiben.
    """
    return _draft_id(con)


def korb_anzahl(con: sqlite3.Connection) -> int:
    """Wie viele Zeilen im Warenkorb liegen — ohne einen anzulegen.

    Bewusst nicht über `warenkorb()`: der Kopf jeder Seite zeigt diese Zahl,
    und ein blosser Blick auf den Katalog soll keine Bestellung in die
    Datenbank schreiben.
    """
    row = con.execute(
        "SELECT count(i.id) AS n FROM orders o"
        "  LEFT JOIN order_item i ON i.order_id = o.id"
        " WHERE o.state = 'draft'").fetchone()
    return int(row["n"]) if row else 0


def genau_eines(product_id, free_text,
                was: str = "Ein Bestellposten") -> tuple[int | None, str | None]:
    """Prüft die Regel „entweder Produkt oder Freitext" vor dem INSERT.

    Die Datenbank hat denselben CHECK und ist die letzte Instanz. Hier steht
    er trotzdem, weil ein `IntegrityError` der Nutzerin nichts sagt — und weil
    ein leeres Textfeld ('' oder '   ') sonst als gültiger Freitext durchginge
    und eine namenlose Zeile im Laden erzeugte.

    Öffentlich (und mit `was` für die Anrede), weil `recipes` genau dieselbe
    Regel für `recipe_item` braucht — dieselbe Spaltenform, derselbe CHECK.
    Eine zweite Kopie liefe irgendwann auseinander, und zwar still.
    """
    text = (free_text or "").strip() or None
    try:
        pid = None if product_id in (None, "") else int(product_id)
    except (TypeError, ValueError):
        # Kommt aus einer URL und ist damit beliebig. Ein roher ValueError
        # wäre für den Aufrufer nicht von einem Programmierfehler zu
        # unterscheiden.
        raise UngueltigerPosten(
            f"{product_id!r} ist keine Produkt-id.") from None
    if (pid is None) == (text is None):
        raise UngueltigerPosten(
            f"{was} braucht genau eines von beidem: ein Produkt aus "
            "dem Katalog oder einen Freitext. "
            f"Bekommen: product_id={product_id!r}, free_text={free_text!r}.")
    return pid, text


def vorbelegter_laden(con: sqlite3.Connection, product_id=None,
                      free_text=None) -> str:
    """Der Laden, der beim Einlegen vorgeschlagen wird (Spec 4).

    Die letzte Wahl für dasselbe Produkt; für Freitext-Posten sinngemäss über
    denselben Text. Gibt es keine, dann „egal". Das ersetzt gepflegte Regeln:
    nach zwei Wochen stimmt die Vorauswahl meistens, ohne dass jemand eine
    Zuordnungstabelle führt.

    Gesucht wird über alle Bestellungen, auch den `draft` — die jüngste
    Entscheidung ist die beste Auskunft, unabhängig davon, ob sie schon
    abgeschickt wurde.
    """
    pid, text = genau_eines(product_id, free_text)
    row = con.execute(
        "SELECT store FROM order_item"
        " WHERE product_id IS ? AND free_text IS ?"
        # Absteigend nach id: die zuletzt angelegte Zeile ist die jüngste
        # Wahl. Zeitstempel hat ein Posten nicht.
        " ORDER BY id DESC LIMIT 1", (pid, text)).fetchone()
    return row["store"] if row else LADEN_VORGABE


def einlegen(con: sqlite3.Connection, product_id=None, free_text=None,
             qty: int = 1, store: str | None = None) -> int:
    """Legt ein Produkt oder einen Freitext in den gemeinsamen Warenkorb.

    Liegt dieselbe Sache schon drin, wird die Menge erhöht statt eine zweite
    Zeile angelegt. Zweimal „+" an derselben Kachel heisst „zwei davon" und
    nicht „zwei Zeilen, die im Laden zweimal gegriffen werden".

    Gibt die id des Postens zurück.
    """
    pid, text = genau_eines(product_id, free_text)
    if pid is not None and not con.execute(
            "SELECT 1 FROM product WHERE id = ?", (pid,)).fetchone():
        # Sonst antwortet der Fremdschlüssel mit einem IntegrityError, der
        # nach einem Fehler im Shop aussieht statt nach einer id, die es nicht
        # (mehr) gibt — ein Katalog-Lauf kann Produkte inaktiv setzen, und ein
        # altes Kachel-Formular im Browser zeigt danach ins Leere.
        raise UngueltigerPosten(f"Produkt {pid} gibt es nicht.")
    menge = max(1, int(qty))
    laden = store if store in db.STORES else vorbelegter_laden(con, pid, text)
    korb = warenkorb(con)

    vorhanden = con.execute(
        "SELECT id, qty FROM order_item"
        " WHERE order_id = ? AND product_id IS ? AND free_text IS ?",
        (korb, pid, text)).fetchone()
    if vorhanden:
        con.execute("UPDATE order_item SET qty = ? WHERE id = ?",
                    (vorhanden["qty"] + menge, vorhanden["id"]))
        con.commit()
        return int(vorhanden["id"])

    cur = con.execute(
        "INSERT INTO order_item (order_id, product_id, free_text, qty, store)"
        " VALUES (?, ?, ?, ?, ?)", (korb, pid, text, menge, laden))
    con.commit()
    return int(cur.lastrowid)


def _posten_im_draft(con: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    """Holt einen Posten und stellt sicher, dass er im Warenkorb liegt.

    An einer abgeschickten Bestellung wird nichts mehr geändert: sie ist die
    Abmachung, nach der er im Laden steht. Wer sie ändern will, macht eine
    neue.
    """
    row = con.execute(
        "SELECT i.id, i.qty, i.store, o.state FROM order_item i"
        "  JOIN orders o ON o.id = i.order_id WHERE i.id = ?",
        (item_id,)).fetchone()
    if row is None:
        raise UngueltigerPosten(f"Bestellposten {item_id} gibt es nicht.")
    if row["state"] != "draft":
        raise UngueltigerPosten(
            f"Bestellposten {item_id} gehört zu einer abgeschickten Bestellung "
            f"({row['state']}) und wird nicht mehr geändert.")
    return row


def menge_setzen(con: sqlite3.Connection, item_id: int, qty: int) -> int:
    """Setzt die Menge einer Zeile. Menge unter 1 entfernt sie.

    Der Minus-Knopf muss eine Zeile auch loswerden können, ohne dass man ihn
    erst gegen den Löschknopf tauscht; und eine Zeile mit Menge 0 wäre eine
    Lüge im Regal. Gibt die neue Menge zurück, 0 für „ist weg".
    """
    _posten_im_draft(con, item_id)
    menge = int(qty)
    if menge < 1:
        entfernen(con, item_id)
        return 0
    con.execute("UPDATE order_item SET qty = ? WHERE id = ?", (menge, item_id))
    con.commit()
    return menge


def laden_setzen(con: sqlite3.Connection, item_id: int, store: str) -> str:
    """Setzt den Laden einer Zeile (rewe / lidl / egal)."""
    _posten_im_draft(con, item_id)
    if store not in db.STORES:
        raise UngueltigerPosten(
            f"{store!r} ist kein Laden. Erlaubt: {', '.join(db.STORES)}.")
    con.execute("UPDATE order_item SET store = ? WHERE id = ?", (store, item_id))
    con.commit()
    return store


def entfernen(con: sqlite3.Connection, item_id: int) -> None:
    """Nimmt eine Zeile aus dem Warenkorb."""
    _posten_im_draft(con, item_id)
    con.execute("DELETE FROM order_item WHERE id = ?", (item_id,))
    con.commit()


def inhalt(con: sqlite3.Connection) -> list[dict]:
    """Die Zeilen des Warenkorbs — leer, solange keiner angelegt wurde."""
    korb = _draft_id(con)
    return posten(con, korb) if korb is not None else []


def abschicken(con: sqlite3.Connection, note: str | None = None) -> dict:
    """`draft -> offen`, mit `submitted_at`. Gibt die Bestellung zurück.

    Danach gibt es keinen `draft` mehr; der nächste `warenkorb()`-Aufruf legt
    einen neuen an. Genau das ist mit „kein Kopieren beim Abschicken" gemeint
    (Spec 4): dieselbe Zeile wechselt den Zustand, die Posten bleiben liegen,
    wo sie sind.
    """
    korb = _draft_id(con)
    if korb is None or not posten(con, korb):
        raise LeererWarenkorb(
            "Der Warenkorb ist leer — eine Bestellung ohne Posten schickt "
            "niemanden in den Laden.")
    felder = {"submitted_at": jetzt()}
    if note is not None and note.strip():
        felder["note"] = note.strip()
    return wechsle(con, korb, "offen", **felder)
