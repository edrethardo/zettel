"""Tests für Schema und Migrationen (WB-320).

Kein Netz, keine Datei: alles läuft gegen `:memory:`.
"""
import sqlite3

import pytest

from zettel import db


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.migrate(c)
    yield c
    c.close()


def _tables(con):
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    return {r["name"] for r in rows}


def test_fts5_ist_verfuegbar():
    # Trägt das ganze Katalogticket. Schlägt das fehl, ist jede weitere
    # Fehlermeldung in dieser Datei nur Folgeschaden.
    assert db.has_fts5()


def test_alle_tabellen_existieren(con):
    vorhanden = _tables(con)
    fehlend = [t for t in db.TABLES if t not in vorhanden]
    assert not fehlend, f"nicht angelegt: {fehlend}"


def test_migrate_ist_idempotent(con):
    db.migrate(con)
    db.migrate(con)
    assert not [t for t in db.TABLES if t not in _tables(con)]


def test_produkt_ist_ueber_fts_findbar(con):
    con.execute(
        "INSERT INTO product (source, external_id, name, brand, price_cents,"
        " category_l1, category_l2, category_l3)"
        " VALUES ('knuspr', '95793', 'Miil Frische Landmilch 3,8% Vollmilch',"
        " 'Miil', 119, 'Molkerei', 'Milch', 'Frischmilch')"
    )
    con.commit()

    treffer = con.execute(
        "SELECT p.name FROM product_fts f JOIN product p ON p.id = f.rowid"
        " WHERE product_fts MATCH ?", ("landmilch",)
    ).fetchall()
    assert [r["name"] for r in treffer] == ["Miil Frische Landmilch 3,8% Vollmilch"]

    # Auch über die Kategorie, weil der Katalog danach blättert.
    per_kategorie = con.execute(
        "SELECT count(*) AS n FROM product_fts WHERE product_fts MATCH ?",
        ("Frischmilch",)
    ).fetchone()["n"]
    assert per_kategorie == 1


def test_fts_folgt_aenderung_und_loeschung(con):
    con.execute(
        "INSERT INTO product (source, external_id, name) "
        "VALUES ('knuspr', '1', 'Butterkeks')"
    )
    con.commit()

    con.execute("UPDATE product SET name = 'Vollkornkeks' WHERE external_id = '1'")
    con.commit()
    assert _fts_count(con, "butterkeks") == 0, "alter Name blieb im Index stehen"
    assert _fts_count(con, "vollkornkeks") == 1

    con.execute("DELETE FROM product WHERE external_id = '1'")
    con.commit()
    assert _fts_count(con, "vollkornkeks") == 0


def _fts_count(con, begriff):
    return con.execute(
        "SELECT count(*) AS n FROM product_fts WHERE product_fts MATCH ?",
        (begriff,)
    ).fetchone()["n"]


def test_bestellposten_braucht_genau_eine_quelle(con):
    con.execute(
        "INSERT INTO orders (state, created_at) VALUES ('draft', '2026-08-28')"
    )
    oid = con.execute("SELECT id FROM orders").fetchone()["id"]

    # weder Produkt noch Freitext
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO order_item (order_id, qty) VALUES (?, 1)", (oid,))

    # beides gleichzeitig
    con.execute(
        "INSERT INTO product (source, external_id, name) "
        "VALUES ('knuspr', '2', 'Milch')"
    )
    pid = con.execute("SELECT id FROM product WHERE external_id='2'").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO order_item (order_id, product_id, free_text, qty)"
            " VALUES (?, ?, 'Milch', 1)", (oid, pid)
        )

    # jeweils eines allein geht
    con.execute(
        "INSERT INTO order_item (order_id, product_id, qty) VALUES (?, ?, 1)",
        (oid, pid))
    con.execute(
        "INSERT INTO order_item (order_id, free_text, qty)"
        " VALUES (?, 'Brötchen vom Bäcker', 2)", (oid,))
    con.commit()
    assert con.execute("SELECT count(*) AS n FROM order_item").fetchone()["n"] == 2


def test_unzulaessige_werte_werden_abgelehnt(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO orders (state, created_at)"
            " VALUES ('unterwegs', '2026-08-28')")

    con.execute("INSERT INTO orders (state, created_at) VALUES ('draft', 'x')")
    oid = con.execute("SELECT id FROM orders").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO order_item (order_id, free_text, store)"
            " VALUES (?, 'Milch', 'aldi')", (oid,))


def test_produkt_ist_je_quelle_eindeutig(con):
    con.execute(
        "INSERT INTO product (source, external_id, name) "
        "VALUES ('knuspr', '95793', 'Milch')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO product (source, external_id, name) "
            "VALUES ('knuspr', '95793', 'Milch nochmal')")


def test_eine_alte_datenbank_bekommt_die_neuen_spalten(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` sieht eine vorhandene Tabelle nicht an.

    Die Datenbank auf diesem Rechner ist gewachsen und nicht frisch — ohne
    diesen Pfad fiele die neue Spalte erst im Betrieb auf, mit „no such
    column" mitten in einem Chat-Zug. Geprüft wird beides: die Spalte kommt
    dazu, und die vorhandenen Zeilen überleben es.
    """
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.executescript("""
        CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT, created_at TEXT);
        CREATE TABLE chat_message (id INTEGER PRIMARY KEY, order_id INTEGER,
                                   role TEXT, content TEXT, span_id TEXT,
                                   created_at TEXT);
        CREATE TABLE chat_suggestion (
            id INTEGER PRIMARY KEY, chat_message_id INTEGER, product_id INTEGER,
            free_text TEXT, qty INTEGER NOT NULL DEFAULT 1, search_term TEXT,
            rank REAL, decision TEXT NOT NULL DEFAULT 'offen', decided_at TEXT);
        INSERT INTO chat_message (id, order_id, role, content, created_at)
             VALUES (1, 1, 'assistant', 'Vorschläge', 'x');
        INSERT INTO chat_suggestion (chat_message_id, free_text, search_term)
             VALUES (1, 'Butter', 'Butter');
    """)
    alt.commit()
    alt.close()

    con = db.connect(pfad)
    try:
        db.migrate(con)
        spalten = [r[1] for r in con.execute("PRAGMA table_info(chat_suggestion)")]
        assert "corrected_from" in spalten and "fallback_term" in spalten
        assert con.execute("SELECT free_text FROM chat_suggestion"
                           ).fetchone()[0] == "Butter"
        assert con.execute("SELECT count(*) FROM chat_kandidat").fetchone()[0] == 0
        db.migrate(con)          # und noch einmal, ohne „duplicate column"
    finally:
        con.close()


def test_ein_altes_ja_gilt_als_eingelegt(tmp_path):
    """WB-361: der Schutz gegen den Doppeltipp hängt an `eingelegt_at`.

    Eine gewachsene Datenbank kennt die Spalte nicht; ihre `kept`-Zeilen
    kämen mit NULL heraus, und dann wäre der Schutz ausgerechnet für die
    vorhandenen Zeilen ausgehebelt — zurücknehmen und noch einmal „Ja" legte
    ein zweites Mal ein. Der Anfangswert ist `decided_at`: vor diesem Ticket
    fielen entscheiden und einlegen zusammen.
    """
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.executescript("""
        CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT, created_at TEXT);
        CREATE TABLE chat_message (id INTEGER PRIMARY KEY, order_id INTEGER,
                                   role TEXT, content TEXT, span_id TEXT,
                                   created_at TEXT);
        CREATE TABLE chat_suggestion (
            id INTEGER PRIMARY KEY, chat_message_id INTEGER, product_id INTEGER,
            free_text TEXT, qty INTEGER NOT NULL DEFAULT 1, search_term TEXT,
            rank REAL, decision TEXT NOT NULL DEFAULT 'offen', decided_at TEXT);
        INSERT INTO chat_message (id, order_id, role, content, created_at)
             VALUES (1, 1, 'assistant', 'Vorschläge', 'x');
        INSERT INTO chat_suggestion (chat_message_id, free_text, search_term,
                                     decision, decided_at)
             VALUES (1, 'Butter', 'Butter', 'kept', '2026-08-01T10:00:00'),
                    (1, 'Milch', 'Milch', 'removed', '2026-08-01T10:01:00'),
                    (1, 'Reis', 'Reis', 'offen', NULL);
    """)
    alt.commit()
    alt.close()

    con = db.connect(pfad)
    try:
        db.migrate(con)
        zeilen = con.execute("SELECT decision, eingelegt_at, zurueckgenommen"
                             "  FROM chat_suggestion ORDER BY id").fetchall()
        assert [r["eingelegt_at"] for r in zeilen] == [
            "2026-08-01T10:00:00", None, None]
        # Der Zähler beginnt leer und wird beim Lesen zu 0 (`_auf`) — hier
        # steht nur, dass die Migration nichts erfindet.
        assert [r["zurueckgenommen"] for r in zeilen] == [None, None, None]

        # Und die Nachtragung läuft genau einmal: ein danach zurückgenommenes
        # „Ja" darf beim nächsten `migrate()` nicht wieder als eingelegt
        # dastehen … und ein zurückgenommenes bleibt eingelegt, weil es das
        # im Korb ja auch ist.
        con.execute("UPDATE chat_suggestion SET decision = 'offen',"
                    " decided_at = NULL WHERE id = 1")
        con.commit()
        db.migrate(con)
        assert con.execute("SELECT eingelegt_at FROM chat_suggestion"
                           " WHERE id = 1").fetchone()[0] == "2026-08-01T10:00:00"
    finally:
        con.close()


def test_ein_alter_korbposten_behaelt_seine_menge(tmp_path):
    """WB-362: `qty` war eine Eingabe und wird ein Ergebnis.

    Eine gewachsene Datenbank hat Posten, deren Menge jemand gesetzt oder ein
    Rezept mitgebracht hat — nichts davon ist aus einer benötigten Menge
    gerechnet. Genau das bedeutet `hand_qty`, also ist `qty` der richtige
    Anfangswert. Bliebe die Spalte NULL, zählte der Altbestand als „keine
    Packung verlangt", und der Korb fiele beim nächsten Rezept auf eine
    Packung zurück: ein Posten, der bei einer Migration schrumpft, ist ein
    verlorener Posten.
    """
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.executescript("""
        CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT, created_at TEXT);
        CREATE TABLE order_item (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
            product_id INTEGER, free_text TEXT,
            qty INTEGER NOT NULL DEFAULT 1,
            store TEXT NOT NULL DEFAULT 'egal', picked_at TEXT);
        CREATE TABLE recipe (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                             servings INTEGER, note TEXT);
        CREATE TABLE recipe_item (
            id INTEGER PRIMARY KEY, recipe_id INTEGER NOT NULL,
            product_id INTEGER, free_text TEXT,
            qty INTEGER NOT NULL DEFAULT 1);
        INSERT INTO orders (id, state, created_at)
             VALUES (1, 'draft', '2026-08-01 10:00:00');
        INSERT INTO order_item (order_id, free_text, qty)
             VALUES (1, 'Butter', 3), (1, 'Hefe', 1);
        INSERT INTO recipe (id, name, servings) VALUES (1, 'Sugo', 4);
        INSERT INTO recipe_item (recipe_id, free_text, qty)
             VALUES (1, 'Tomaten', 2);
    """)
    alt.commit()
    alt.close()

    con = db.connect(pfad)
    try:
        db.migrate(con)
        zeilen = con.execute("SELECT qty, hand_qty, need_amount, need_unit"
                             "  FROM order_item ORDER BY id").fetchall()
        assert [(r["qty"], r["hand_qty"]) for r in zeilen] == [(3, 3), (1, 1)]
        # Erfunden wird dabei nichts: eine benötigte Menge stand nie da und
        # steht auch danach nicht da.
        assert [r["need_amount"] for r in zeilen] == [None, None]
        assert [r["need_unit"] for r in zeilen] == [None, None]

        zutat = con.execute("SELECT qty, amount, unit FROM recipe_item"
                            ).fetchone()
        assert (zutat["qty"], zutat["amount"], zutat["unit"]) == (2, None, None)

        # Und die Nachtragung läuft genau einmal: wer die Menge danach von
        # Hand herunternimmt, findet sie beim nächsten Start nicht wieder oben.
        con.execute("UPDATE order_item SET qty = 1, hand_qty = 1 WHERE id = 1")
        con.commit()
        db.migrate(con)
        assert con.execute("SELECT hand_qty FROM order_item WHERE id = 1"
                           ).fetchone()[0] == 1
    finally:
        con.close()


def test_ein_alter_posten_gilt_nach_der_migration_nicht_als_vermisst(tmp_path):
    """WB-373: `missing_at` kommt ohne Nachtrag für den Altbestand — richtig.

    Ein Posten aus der Zeit davor wurde entweder abgehakt oder steht noch
    offen; vermisst wurde keiner, denn den Weg gab es nicht. NULL ist hier die
    Wahrheit und keine Lücke — und ein abgehakter Posten darf durch die
    Migration nicht plötzlich in beiden Spalten stehen.
    """
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.executescript("""
        CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT, created_at TEXT);
        CREATE TABLE order_item (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
            product_id INTEGER, free_text TEXT,
            qty INTEGER NOT NULL DEFAULT 1,
            store TEXT NOT NULL DEFAULT 'egal', picked_at TEXT);
        INSERT INTO orders (id, state, created_at)
             VALUES (1, 'offen', '2026-08-01 10:00:00');
        INSERT INTO order_item (order_id, free_text, picked_at)
             VALUES (1, 'Butter', '2026-08-01 11:00:00'), (1, 'Hefe', NULL);
    """)
    alt.commit()
    alt.close()

    con = db.connect(pfad)
    try:
        db.migrate(con)
        zeilen = con.execute("SELECT picked_at, missing_at FROM order_item"
                             " ORDER BY id").fetchall()
        assert [r["missing_at"] for r in zeilen] == [None, None]
        assert zeilen[0]["picked_at"] == "2026-08-01 11:00:00"
    finally:
        con.close()
