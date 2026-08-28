"""Tests für Schema und Migrationen (WB-320).

Kein Netz, keine Datei: alles läuft gegen `:memory:`.
"""
import sqlite3

import pytest

from picknick import db


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
