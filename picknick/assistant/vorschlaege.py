"""Chatverlauf und Vorschlagsliste — und die Entscheidung, die das Label ist.

**Nichts landet ungefragt im Warenkorb** (Spec 6). Ein Chat-Zug legt Zeilen in
`chat_suggestion` und sonst nirgendwohin. Erst ein Tipp auf „Ja" schiebt eine
Zeile über `orders.einlegen()` in den Korb — durch dieselbe Tür wie jedes
Produkt aus dem Katalog, damit Mengenzusammenfassung und Ladenvorbelegung
genau einmal im Projekt stehen.

**`decision` ist das Eval-Label** (Spec 8.1). Sie bestätigt oder verwirft jede
Zeile einzeln; daraus fällt Ground Truth, ohne dass jemand annotiert. Drei
Werte, und der dritte trägt das Gewicht: `offen` heisst „nie entschieden" und
geht NICHT in die Quote ein — sonst zählte jeder Abbruch als Fehler des
Modells, und die Zahl, um die es im ganzen Projekt geht, wäre systematisch zu
schlecht.

`search_term` und `rank` stehen an der Zeile und nicht bloss im Trace. Sie
sind die Erklärung zur Annotation („woran lag es?") und überleben so auch
einen Phoenix, der gerade nicht lief.
"""
from __future__ import annotations

import sqlite3

from picknick import db, orders
from picknick.orders.bestellung import jetzt

ROLLE_NUTZERIN = "user"
ROLLE_AGENT = "assistant"

#: Wie `chat_suggestion.decision` heisst. Aus `db` geholt, damit es genau eine
#: Wahrheit gibt — der CHECK in der Tabelle ist dieselbe Liste.
OFFEN, BEHALTEN, VERWORFEN = db.DECISIONS


class VorschlagFehler(ValueError):
    """Ein Vorschlag oder eine Entscheidung, die es so nicht gibt."""


def nachricht(con: sqlite3.Connection, order_id: int, rolle: str,
              inhalt: str, span_id: str | None = None) -> int:
    """Schreibt eine Chatzeile und gibt ihre id zurück.

    `span_id` bleibt oft leer und ist trotzdem hier: die Annotationen aus
    Spec 8.1 müssen später den richtigen `chat.turn`-Span treffen, und WB-328
    trägt ihn nach. Ohne Feld gäbe es dann keinen Weg zurück vom Label zum
    Trace.
    """
    cur = con.execute(
        "INSERT INTO chat_message (order_id, role, content, span_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (order_id, rolle, inhalt, span_id, jetzt()))
    con.commit()
    return int(cur.lastrowid)


def span_setzen(con: sqlite3.Connection, chat_message_id: int,
                span_id: str) -> None:
    """Trägt die Span-ID nach — der Haken, an dem WB-328 hängt.

    Getrennt vom Schreiben der Nachricht, weil der Span erst endet, wenn der
    Zug fertig ist, die Nachricht aber schon davor stehen soll.
    """
    con.execute("UPDATE chat_message SET span_id = ? WHERE id = ?",
                (span_id, chat_message_id))
    con.commit()


def vorschlag(con: sqlite3.Connection, chat_message_id: int, *,
              product_id=None, free_text=None, qty: int = 1,
              search_term: str | None = None, rang: float | None = None) -> int:
    """Legt eine Vorschlagszeile an. Entweder Produkt oder Freitext.

    Die Regel „genau eines von beidem" kommt aus `orders.genau_eines()` und
    wird nicht nachgebaut: es ist buchstäblich dieselbe Regel wie beim
    Bestellposten und bei der Zutat, mit demselben CHECK dahinter.

    `rang` heisst in der Datenbank `rank` — die Spalte stammt aus Spec 4 und
    bleibt, wie sie dort steht; nach aussen heisst sie wie überall sonst im
    Projekt, wo der FTS-Rang vorkommt (`catalog.search`).
    """
    pid, text = orders.genau_eines(product_id, free_text, was="Ein Vorschlag")
    if pid is not None and not con.execute(
            "SELECT 1 FROM product WHERE id = ?", (pid,)).fetchone():
        # Kann nur passieren, wenn zwischen Suche und Schreiben ein Crawl das
        # Produkt entfernt hat. Als IntegrityError sähe das aus wie ein Fehler
        # im Shop.
        raise VorschlagFehler(f"Produkt {pid} gibt es nicht.")
    cur = con.execute(
        "INSERT INTO chat_suggestion (chat_message_id, product_id, free_text,"
        "                             qty, search_term, rank, decision)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_message_id, pid, text, max(1, int(qty)), search_term,
         None if rang is None else float(rang), OFFEN))
    con.commit()
    return int(cur.lastrowid)


_VORSCHLAG_SQL = (
    "SELECT s.id, s.chat_message_id, s.product_id, s.free_text, s.qty,"
    "       s.search_term, s.rank AS rang, s.decision, s.decided_at,"
    "       coalesce(p.name, s.free_text) AS name,"
    "       p.unit_text, p.price_cents, p.image_path, p.active"
    "  FROM chat_suggestion s LEFT JOIN product p ON p.id = s.product_id"
    # LEFT JOIN und kein `active = 1`: derselbe Grund wie bei den Rezepten —
    # ein Vorschlag, den ein Crawl inzwischen ausgemustert hat, wird markiert
    # und nicht verschwiegen.
)


def _auf(row: sqlite3.Row) -> dict:
    # Derselbe Ableiter wie beim Bestellposten und bei der Rezeptzutat
    # (WB-335): ein Vorschlag ist dieselbe Zeile mit demselben LEFT JOIN.
    v = orders.markiere_katalogstand(dict(row))
    v["offen"] = v["decision"] == OFFEN
    v["behalten"] = v["decision"] == BEHALTEN
    if v["name"] is None:
        v["name"] = f"Produkt {v['product_id']} — nicht mehr auffindbar"
    return v


def liste(con: sqlite3.Connection, chat_message_id: int) -> list[dict]:
    """Die Vorschläge einer Chatzeile, in der Reihenfolge des Anlegens."""
    return [_auf(r) for r in con.execute(
        _VORSCHLAG_SQL + " WHERE s.chat_message_id = ? ORDER BY s.id",
        (chat_message_id,)).fetchall()]


def eine(con: sqlite3.Connection, suggestion_id: int) -> dict:
    row = con.execute(_VORSCHLAG_SQL + " WHERE s.id = ?",
                      (suggestion_id,)).fetchone()
    if row is None:
        raise VorschlagFehler(f"Vorschlag {suggestion_id} gibt es nicht.")
    return _auf(row)


def verlauf(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Der Chat zu einer Bestellung: Nachrichten, jede mit ihren Vorschlägen.

    Der Chat hängt an der Bestellung und nicht an einer eigenen Sitzung
    (Spec 9: er gehört in den Warenkorb). Damit wandert er beim Abschicken
    mit — und die Entscheidungen bleiben bei dem Einkauf, zu dem sie gehören.
    """
    zeilen = []
    for m in con.execute(
            "SELECT id, order_id, role, content, span_id, created_at"
            "  FROM chat_message WHERE order_id = ? ORDER BY id",
            (order_id,)).fetchall():
        eintrag = dict(m)
        eintrag["vorschlaege"] = liste(con, eintrag["id"])
        zeilen.append(eintrag)
    return zeilen


def entscheiden(con: sqlite3.Connection, suggestion_id: int,
                entscheidung: str) -> dict:
    """`kept` legt in den Korb, `removed` nicht. Gibt den Vorschlag zurück.

    Zweimal „Ja" legt NICHT zweimal ein: die Entscheidung wird vorher
    verglichen. Ohne das erhöht ein doppelter Tipp — auf dem Handy schnell
    passiert — die Menge im Korb.

    Ein „Nein" NACH einem „Ja" ändert das Label und rührt den Korb nicht an.
    Das ist Absicht: `orders.einlegen()` fasst gleiche Zeilen zusammen, die
    Korbzeile kann also längst eine sein, die die Nutzerin selbst aufgestockt
    hat. Sie hier wieder herauszunehmen hiesse, fremde Mengen zu löschen. Im
    Korb steht ein Löschknopf — einen Tipp entfernt und ohne Rätselraten.
    """
    if entscheidung not in db.DECISIONS:
        raise VorschlagFehler(
            f"{entscheidung!r} ist keine Entscheidung. Erlaubt: "
            f"{', '.join(db.DECISIONS)}.")
    v = eine(con, suggestion_id)
    if v["decision"] == entscheidung:
        return v
    if entscheidung == BEHALTEN:
        orders.einlegen(con, product_id=v["product_id"],
                        free_text=v["free_text"], qty=v["qty"])
    con.execute(
        "UPDATE chat_suggestion SET decision = ?, decided_at = ? WHERE id = ?",
        (entscheidung, None if entscheidung == OFFEN else jetzt(),
         suggestion_id))
    con.commit()
    return eine(con, suggestion_id)


def alle_entscheiden(con: sqlite3.Connection, chat_message_id: int,
                     entscheidung: str) -> list[dict]:
    """„Alles übernehmen" / „Alles verwerfen" für die offenen Zeilen.

    Nur die offenen: eine bereits getroffene Entscheidung wird von einem
    Sammelknopf nicht überschrieben — sonst kippt ein einziger Tipp die Labels
    um, die die Nutzerin einzeln gesetzt hat.
    """
    for v in liste(con, chat_message_id):
        if v["decision"] == OFFEN:
            entscheiden(con, v["id"], entscheidung)
    return liste(con, chat_message_id)


def quote(con: sqlite3.Connection, chat_message_id: int) -> dict:
    """`mapping_precision` für eine Chatzeile: behalten / entschieden.

    Spec 8.1. Offene Vorschläge stehen im Nenner NICHT — daher `quote = None`,
    solange nichts entschieden wurde. Eine 0.0 an dieser Stelle wäre gelogen:
    sie hiesse „alles falsch", wo „noch nichts gesagt" richtig ist.
    """
    zeilen = liste(con, chat_message_id)
    behalten = sum(1 for v in zeilen if v["decision"] == BEHALTEN)
    verworfen = sum(1 for v in zeilen if v["decision"] == VERWORFEN)
    entschieden = behalten + verworfen
    return {"vorgeschlagen": len(zeilen), "behalten": behalten,
            "verworfen": verworfen, "offen": len(zeilen) - entschieden,
            "quote": (behalten / entschieden) if entschieden else None}
