"""SQLite-Schema und Migrationen für Picknick.

Eine Datenbankdatei, ein Schema, keine ORM-Schicht. `migrate()` ist idempotent
und wird beim Start jedes Prozesses aufgerufen — Web, Crawler und Evals teilen
sich dieselbe Datei.

Abweichung von der Spec (Abschnitt 4), bewusst und hier dokumentiert:
Die Spec nennt die Bestelltabelle `order`. Das ist ein SQL-Schlüsselwort und
müsste in jeder Abfrage gequotet werden — eine Falle, die jedes Folgeticket
einmal stellen würde. Die Tabelle heisst deshalb `orders`. Alle Spalten sind
unverändert übernommen.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = "data/picknick.db"

#: Läden, aus denen ein Bestellposten geholt wird. Der Katalog ist ladenneutral
#: (Spec 5.1) — die Wahl hängt am Posten, nicht am Produkt.
STORES = ("rewe", "lidl", "egal")

#: Zustände einer Bestellung. Mehr gibt es bewusst nicht: "unterwegs" fehlt,
#: weil der Einkaufende selbst sieht, dass er im Laden steht (Spec 4).
ORDER_STATES = ("draft", "offen", "erledigt")

#: Entscheidung über einen Chat-Vorschlag. `offen` heisst "nie entschieden" und
#: geht nicht in die Trefferquote ein (Spec 8.1).
DECISIONS = ("offen", "kept", "removed")


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS product (
        id                   INTEGER PRIMARY KEY,
        source               TEXT    NOT NULL,
        external_id          TEXT    NOT NULL,
        name                 TEXT    NOT NULL,
        brand                TEXT,
        price_cents          INTEGER,
        price_per_unit_cents INTEGER,
        unit_text            TEXT,
        unit                 TEXT,
        image_path           TEXT,
        category_l1          TEXT,
        category_l2          TEXT,
        category_l3          TEXT,
        in_stock             INTEGER NOT NULL DEFAULT 1,
        last_seen_at         TEXT,
        active               INTEGER NOT NULL DEFAULT 1,
        UNIQUE (source, external_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id           INTEGER PRIMARY KEY,
        state        TEXT NOT NULL CHECK (state IN ('draft', 'offen', 'erledigt')),
        note         TEXT,
        created_at   TEXT NOT NULL,
        submitted_at TEXT,
        done_at      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_item (
        id         INTEGER PRIMARY KEY,
        order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        product_id INTEGER REFERENCES product(id),
        free_text  TEXT,
        qty        INTEGER NOT NULL DEFAULT 1,
        store      TEXT NOT NULL DEFAULT 'egal'
                        CHECK (store IN ('rewe', 'lidl', 'egal')),
        picked_at  TEXT,
        -- Genau eines von beidem, nie beides und nie keines: das Freitext-
        -- Ventil ist gleichwertig, nicht ein Sonderfall (Spec 4).
        CHECK ((product_id IS NULL) <> (free_text IS NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipe (
        id       INTEGER PRIMARY KEY,
        name     TEXT NOT NULL,
        servings INTEGER,
        note     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipe_item (
        id         INTEGER PRIMARY KEY,
        recipe_id  INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
        product_id INTEGER REFERENCES product(id),
        free_text  TEXT,
        qty        INTEGER NOT NULL DEFAULT 1,
        CHECK ((product_id IS NULL) <> (free_text IS NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_message (
        id         INTEGER PRIMARY KEY,
        order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        -- Span-ID des zugehörigen chat.turn (Spec 7.1). Die Annotationen aus
        -- Spec 8.1 hängen später genau hier dran.
        span_id    TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_suggestion (
        id              INTEGER PRIMARY KEY,
        chat_message_id INTEGER NOT NULL
                            REFERENCES chat_message(id) ON DELETE CASCADE,
        product_id      INTEGER REFERENCES product(id),
        free_text       TEXT,
        qty             INTEGER NOT NULL DEFAULT 1,
        search_term     TEXT,
        rank            REAL,
        decision        TEXT NOT NULL DEFAULT 'offen'
                            CHECK (decision IN ('offen', 'kept', 'removed')),
        decided_at      TEXT,
        CHECK ((product_id IS NULL) <> (free_text IS NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scrape_run (
        id          INTEGER PRIMARY KEY,
        source      TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        status      TEXT,
        n_products  INTEGER,
        error       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_order_item_order ON order_item(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_recipe_item_recipe ON recipe_item(recipe_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_message_order ON chat_message(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_sugg_message ON chat_suggestion(chat_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_active ON product(active)",
]

# Volltextsuche über die Felder, nach denen im Katalog gesucht wird. `external
# content` heisst: der Index speichert den Text nicht doppelt, sondern verweist
# auf product.rowid. Dafür müssen die Trigger unten den Index von Hand
# nachziehen — vergisst man einen, findet die Suche stillschweigend zu wenig.
FTS_SCHEMA = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS product_fts USING fts5(
        name, brand, category_l1, category_l2, category_l3,
        content='product', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_ai AFTER INSERT ON product BEGIN
        INSERT INTO product_fts (rowid, name, brand,
                                 category_l1, category_l2, category_l3)
        VALUES (new.id, new.name, new.brand,
                new.category_l1, new.category_l2, new.category_l3);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_ad AFTER DELETE ON product BEGIN
        INSERT INTO product_fts (product_fts, rowid, name, brand,
                                 category_l1, category_l2, category_l3)
        VALUES ('delete', old.id, old.name, old.brand,
                old.category_l1, old.category_l2, old.category_l3);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_au AFTER UPDATE ON product BEGIN
        INSERT INTO product_fts (product_fts, rowid, name, brand,
                                 category_l1, category_l2, category_l3)
        VALUES ('delete', old.id, old.name, old.brand,
                old.category_l1, old.category_l2, old.category_l3);
        INSERT INTO product_fts (rowid, name, brand,
                                 category_l1, category_l2, category_l3)
        VALUES (new.id, new.name, new.brand,
                new.category_l1, new.category_l2, new.category_l3);
    END
    """,
]

#: Jede Tabelle, die `migrate()` anlegt. Der Test prüft gegen genau diese Liste,
#: damit eine vergessene Tabelle auffällt und nicht erst im Betrieb.
TABLES = (
    "product", "orders", "order_item", "recipe", "recipe_item",
    "chat_message", "chat_suggestion", "scrape_run", "product_fts",
)


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Öffnet die Datenbank und richtet sie ein.

    `:memory:` wird durchgereicht, damit Tests ohne Datei auskommen. Bei einem
    Dateipfad wird das übergeordnete Verzeichnis angelegt.
    """
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    # Ohne das setzt SQLite Fremdschlüssel nicht durch — und ON DELETE CASCADE
    # oben wäre stille Dekoration.
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def migrate(con: sqlite3.Connection) -> None:
    """Legt das vollständige Schema an. Mehrfach aufrufbar."""
    for stmt in SCHEMA:
        con.execute(stmt)
    for stmt in FTS_SCHEMA:
        con.execute(stmt)
    con.commit()


def has_fts5() -> bool:
    """Ist FTS5 in dieses SQLite einkompiliert?

    Auf dieser Maschine ja (sqlite 3.37.2, in Spec-Anhang A verifiziert). Der
    Aufruf gibt einem Prozess auf einer fremden Maschine trotzdem eine
    verständliche Antwort statt eines rohen OperationalError.
    """
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()
