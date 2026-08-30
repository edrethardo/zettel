#!/usr/bin/env python3
"""Baut den Vorführzustand für WB-400 auf der Scratch-Kopie.

Über HTTP, wo es einen Weg gibt (die Entscheidungen laufen durch den echten
Code), und direkt in SQL, wo der Weg ein Modell oder einen Laden bräuchte
(chat_kandidat, die offene Bestellung für die Pick-Ansicht)."""
import pathlib
import sqlite3, sys, urllib.request, urllib.parse

BASIS = "http://127.0.0.1:8748"
DB = sys.argv[1]

def post(pfad):
    req = urllib.request.Request(BASIS + pfad, data=b"", method="POST")
    with urllib.request.urlopen(req) as a:
        return a.status

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from picknick import db
from picknick.catalog import search

con = db.connect(DB)

# --- 1. Kandidaten für die Butter-Zeile (id 11), damit ein „Nein" die
#        Alternativen zeigt (WB-359). Der Vorrat käme sonst aus dem Zug, den
#        die Demo-DB ohne Suche (Gespeichert-Pfad) gemacht hat.
con.execute("DELETE FROM chat_kandidat WHERE suggestion_id = 11")
treffer = search.search(con, "Butter", limit=8)
for pos, t in enumerate(treffer):
    con.execute(
        "INSERT OR IGNORE INTO chat_kandidat (suggestion_id, product_id, pos,"
        " search_term, rank) VALUES (11, ?, ?, 'Butter', ?)",
        (t["id"], pos, t.get("rank")))
# Die verworfene Zeile selbst gehört mit in die Kandidaten (sie wurde ja
# vorgelegt) — sonst zieht alternativen() sie nicht ab und die Zahl stimmt.
con.execute(
    "INSERT OR IGNORE INTO chat_kandidat (suggestion_id, product_id, pos,"
    " search_term, rank) VALUES (11, 1772, 99, 'Butter', NULL)")

# --- 2. Ein schwacher Treffer (WB-358): die Kochsauce kam nur über den
#        allgemeinsten Begriff.
con.execute("UPDATE chat_suggestion SET fallback_term = 'Sauce' WHERE id = 10")
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
    # (product_id, free_text, qty, store, need_amount, need_unit, picked, fehlt)
    (64,   None,        2, 'rewe', 1000.0, 'ml',   None, None),   # Tomaten 2 x 0,7 kg
    (672,  None,        1, 'rewe', 2.0,    'große', 1,   None),   # Zwiebeln, abgehakt
    (5273, None,        1, 'rewe', 14.0,  'm.-große', None, None),
    (808,  None,        1, 'lidl', None,   None,   1,    None),   # Milch, abgehakt
    (None, 'Klopapier', 1, 'lidl', None,   None,   None, 1),      # gab's nicht
    (None, 'geriebener Käse', 1, 'egal', 400.0, 'g', None, None),
]
for p, f, q, s, na, nu, gepickt, fehlt in zeilen:
    con.execute(
        "INSERT INTO order_item (order_id, product_id, free_text, qty, store,"
        " need_amount, need_unit, picked_at, missing_at) VALUES"
        " (?,?,?,?,?,?,?,?,?)",
        (oid, p, f, q, s, na, nu,
         '2026-08-29 18:40:12' if gepickt else None,
         '2026-08-29 18:44:03' if fehlt else None))
con.commit()
print("Bühne steht — Bestellung", oid)

# WB-387: die Alternativrezepte hängen an chat_rezept.dish_id — der
# Gespeichert-Pfad des Demo-Zugs setzt ihn nicht, die Karte soll sie aber
# zeigen. (Nachtrag WB-400.)
con2 = db.connect(DB)
con2.execute("UPDATE chat_rezept SET dish_id = 1")
con2.commit()
