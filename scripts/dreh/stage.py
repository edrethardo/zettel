#!/usr/bin/env python3
"""Baut den Vorführzustand für WB-400 auf der Scratch-Kopie.

Über HTTP, wo es einen Weg gibt (die Entscheidungen laufen durch den echten
Code), und direkt in SQL, wo der Weg ein Modell oder einen Laden bräuchte
(chat_kandidat, die offene Bestellung für die Pick-Ansicht, seit Runde 4
auch die Quittung eines ersetzten Zugs)."""
import pathlib
import sqlite3, sys, urllib.request, urllib.parse

BASIS = "http://127.0.0.1:8748"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from zettel import db
from zettel.catalog import search


def post(pfad):
    req = urllib.request.Request(BASIS + pfad, data=b"", method="POST")
    with urllib.request.urlopen(req) as a:
        return a.status


def quittung_bauen(con: sqlite3.Connection) -> int | None:
    """Ein ersetzter Zug für /chat (WB-403, Runde 4) — ohne Modell.

    Der Messstand hat die Quittung drei Runden lang nie gesehen, weil keine
    Bühnenseite einen ersetzten Zug trug — und genau dort sassen 82 Texte
    unter 4,5:1. Der echte Weg dorthin (Rezeptwechsel) kostet zwei
    Modellstufen; die Bühne baut deshalb denselben ENDZUSTAND in SQL:

        * ein neuer Zug übernimmt die Karte und die OFFENEN Zeilen,
        * der alte behält die entschiedenen — und wird damit zur Quittung.

    Zwei bewusste Abweichungen vom echten Wechsel, beide unsichtbar: es gibt
    keine neue Nutzerfrage (sie wäre wortgleich, und sichtbar ist ohnehin
    nur das letzte Glied der Kette), und der neue Zug zeigt dasselbe Rezept
    statt eines anderen (gemessen wird die Quittung, nicht die Wahl). Die
    offenen Zeilen ZIEHEN UM statt neu zu entstehen — im echten Fluss blieben
    sie unsichtbar am alten Zug und der neue bekäme frische; gerendert ist
    beides dieselbe Liste unter derselben Karte.

    Läuft nach den Entscheidungen (die Quittung braucht Berührtes) und nur,
    wenn noch kein ersetzter Zug da ist — eine echte Kette (etwa aus einem
    Wechsel an der Box) wird nicht angerührt.
    """
    if con.execute("SELECT 1 FROM chat_message WHERE ersetzt IS NOT NULL"
                   " LIMIT 1").fetchone():
        return None
    alt = con.execute(
        "SELECT m.id, m.order_id, m.role, m.content, m.span_id"
        "  FROM chat_message m JOIN chat_rezept r ON r.chat_message_id = m.id"
        " ORDER BY m.id DESC LIMIT 1").fetchone()
    if alt is None:
        return None
    beruehrt = con.execute(
        "SELECT count(*) n FROM chat_suggestion WHERE chat_message_id = ?"
        "   AND (decision <> 'offen' OR eingelegt_at IS NOT NULL)",
        (alt["id"],)).fetchone()["n"]
    if not beruehrt:
        # Ohne Entscheidung bliebe vom alten Zug nichts sichtbar — dann gäbe
        # es keine Quittung zu messen, nur ein Loch.
        return None
    cur = con.execute(
        "INSERT INTO chat_message (order_id, role, content, span_id, ersetzt,"
        " created_at) SELECT order_id, role, content, span_id,"
        " coalesce(ersetzt, id), datetime('now') FROM chat_message"
        " WHERE id = ?", (alt["id"],))
    neu = cur.lastrowid
    con.execute("UPDATE chat_rezept SET chat_message_id = ?"
                " WHERE chat_message_id = ?", (neu, alt["id"]))
    con.execute("UPDATE chat_suggestion SET chat_message_id = ?"
                " WHERE chat_message_id = ? AND decision = 'offen'"
                "   AND eingelegt_at IS NULL", (neu, alt["id"]))
    con.commit()
    return neu


def haupt(pfad: str) -> None:
    con = db.connect(pfad)

    # --- 1. Kandidaten für die Butter-Zeile (id 11), damit ein „Nein" die
    #        Alternativen zeigt (WB-359). Der Vorrat käme sonst aus dem Zug,
    #        den die Demo-DB ohne Suche (Gespeichert-Pfad) gemacht hat.
    con.execute("DELETE FROM chat_kandidat WHERE suggestion_id = 11")
    treffer = search.search(con, "Butter", limit=8)
    for pos, t in enumerate(treffer):
        con.execute(
            "INSERT OR IGNORE INTO chat_kandidat (suggestion_id, product_id,"
            " pos, search_term, rank) VALUES (11, ?, ?, 'Butter', ?)",
            (t["id"], pos, t.get("rank")))
    # Die verworfene Zeile selbst gehört mit in die Kandidaten (sie wurde ja
    # vorgelegt) — sonst zieht alternativen() sie nicht ab und die Zahl
    # stimmt.
    con.execute(
        "INSERT OR IGNORE INTO chat_kandidat (suggestion_id, product_id, pos,"
        " search_term, rank) VALUES (11, 1772, 99, 'Butter', NULL)")

    # --- 2. Ein schwacher Treffer (WB-358): die Kochsauce kam nur über den
    #        allgemeinsten Begriff.
    con.execute(
        "UPDATE chat_suggestion SET fallback_term = 'Sauce' WHERE id = 10")
    con.commit()

    # --- 3. Entscheidungen über HTTP — der echte Weg, samt Korb.
    for sid in (13, 4, 5, 15, 16, 17):
        post(f"/chat/vorschlag/{sid}/entscheiden?decision=kept")
    post("/chat/vorschlag/11/entscheiden?decision=removed")

    # --- 4. Eine offene Bestellung für die Pick-Ansicht: drei Zustände,
    #        zwei Läden. Direkt in SQL — abschicken würde den Chat mitnehmen.
    con.execute("DELETE FROM orders WHERE state = 'offen'")
    cur = con.execute(
        "INSERT INTO orders (state, created_at, submitted_at) VALUES"
        " ('offen', '2026-08-29 18:02:11', '2026-08-29 18:31:40')")
    oid = cur.lastrowid
    zeilen = [
        # (product_id, free_text, qty, store, need_amount, need_unit,
        #  picked, fehlt)
        (64,   None,        2, 'rewe', 1000.0, 'ml',   None, None),
        (672,  None,        1, 'rewe', 2.0,    'große', 1,   None),
        (5273, None,        1, 'rewe', 14.0,  'm.-große', None, None),
        (808,  None,        1, 'lidl', None,   None,   1,    None),
        (None, 'Klopapier', 1, 'lidl', None,   None,   None, 1),
        (None, 'geriebener Käse', 1, 'egal', 400.0, 'g', None, None),
    ]
    for p, f, q, s, na, nu, gepickt, fehlt in zeilen:
        con.execute(
            "INSERT INTO order_item (order_id, product_id, free_text, qty,"
            " store, need_amount, need_unit, picked_at, missing_at) VALUES"
            " (?,?,?,?,?,?,?,?,?)",
            (oid, p, f, q, s, na, nu,
             '2026-08-29 18:40:12' if gepickt else None,
             '2026-08-29 18:44:03' if fehlt else None))
    con.commit()
    print("Bühne steht — Bestellung", oid)

    # WB-387: die Alternativrezepte hängen an chat_rezept.dish_id — der
    # Gespeichert-Pfad des Demo-Zugs setzt ihn nicht, die Karte soll sie
    # aber zeigen. (Nachtrag WB-400.)
    con2 = db.connect(pfad)
    con2.execute("UPDATE chat_rezept SET dish_id = 1")
    con2.commit()

    # --- 5. Die Quittung (Runde 4) — NACH den Entscheidungen, denn sie
    #        besteht aus genau ihnen.
    neu = quittung_bauen(con2)
    print("Quittung steht — Zug", neu) if neu else print(
        "Quittung schon da oder nichts Berührtes — nichts gebaut")


if __name__ == "__main__":
    haupt(sys.argv[1])
