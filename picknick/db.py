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

#: Läden, aus denen ein KASSENBON stammt (WB-358). Bewusst nicht `STORES`:
#: dort heisst der dritte Wert `egal` und ist ein WUNSCH („hol es, wo du
#: willst"). Ein Bon kommt immer aus genau einem Laden; wenn er sich nicht
#: nennt, ist das `unbekannt` und keine Freiheit.
BON_STORES = ("rewe", "lidl", "unbekannt")

#: Zustände einer Bestellung. Mehr gibt es bewusst nicht: "unterwegs" fehlt,
#: weil der Einkaufende selbst sieht, dass er im Laden steht (Spec 4).
ORDER_STATES = ("draft", "offen", "erledigt")

#: Entscheidung über einen Chat-Vorschlag. `offen` heisst "nie entschieden" und
#: geht nicht in die Trefferquote ein (Spec 8.1).
DECISIONS = ("offen", "kept", "removed")


#: Deutsche Eigenheiten, die eine reine Volltextsuche leerlaufen lassen. Der
#: unicode61-Tokenizer von FTS5 entfernt zwar Diakritika ("Spülmittel" ->
#: "spulmittel"), aber niemand tippt "spulmittel" — getippt wird "spuelmittel".
#: Beide Schreibweisen werden deshalb auf DIESELBE ASCII-Form abgebildet, und
#: zwar an beiden Enden: beim Schreiben in den Index und auf dem Suchbegriff.
#: Nur so findet "spuelmittel" das "Spülmittel" und umgekehrt.
UMLAUTE = (
    ("Ä", "ae"), ("ä", "ae"),
    ("Ö", "oe"), ("ö", "oe"),
    ("Ü", "ue"), ("ü", "ue"),
    ("ẞ", "ss"), ("ß", "ss"),
)


def normalisiere(text: str) -> str:
    """Faltet Umlaute und ß auf ihre ASCII-Umschreibung.

    Das Gegenstück zu `_norm_sql()` unten. Die beiden MÜSSEN dasselbe tun —
    laufen sie auseinander, findet die Suche stillschweigend nichts mehr.
    Kleinschreibung übernimmt der Tokenizer, hier geht es nur um die
    Buchstabenersetzung.
    """
    for zeichen, ersatz in UMLAUTE:
        text = text.replace(zeichen, ersatz)
    return text


def _norm_sql(ausdruck: str) -> str:
    """Baut denselben Ersetzungslauf als SQL-Ausdruck (geschachtelte replace).

    SQLites eingebautes `lower()` kann nur ASCII, deshalb werden Gross- und
    Kleinbuchstaben einzeln aufgeführt statt vorher kleingeschrieben.
    """
    for zeichen, ersatz in UMLAUTE:
        ausdruck = f"replace({ausdruck}, '{zeichen}', '{ersatz}')"
    return ausdruck


#: Woraus die beiden normalisierten Suchspalten entstehen. Name und Marke
#: getrennt von den Kategorien, damit die Suche einen Namenstreffer höher
#: gewichten kann als einen blossen Kategorietreffer (siehe catalog/search.py).
NORM_NAME_SQL = _norm_sql(
    "coalesce(name, '') || ' ' || coalesce(brand, '')")
NORM_CAT_SQL = _norm_sql(
    "coalesce(category_l1, '') || ' ' || coalesce(category_l2, '')"
    " || ' ' || coalesce(category_l3, '')")

#: Generierte Spalten — SQLite rechnet sie bei jedem Lesen aus, sie belegen
#: keinen Platz und können nicht veralten. Deshalb VIRTUAL und nicht STORED:
#: `ALTER TABLE ADD COLUMN` erlaubt in SQLite ohnehin nur VIRTUAL.
NORM_SPALTEN = (
    ("norm_name", NORM_NAME_SQL),
    ("norm_cat", NORM_CAT_SQL),
)

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
    # ----------------------------------------------------------------------
    # Kassenbons (WB-358). Eigene Tabellen, ausdrücklich NICHT `orders`:
    #
    # Eine Bestellung ist ein PLAN — was gekauft werden soll, aus dem
    # Knuspr-Katalog, mit Ladenwunsch und einem Zustand, der von `draft` nach
    # `erledigt` läuft. Ein Kassenbon ist eine TATSACHE — was gekauft wurde,
    # in welchem Laden, an welchem Tag, zu welchem Preis. Beides in dieselbe
    # Tabelle zu legen hiesse: `orders`-Zeilen, die nie geplant wurden, ein
    # `state`, der für einen Bon nichts bedeutet, und eine Preisspalte an
    # `order_item`, die bei jeder anderen Zeile leer stünde.
    #
    # `receipt` trägt AUSSCHLIESSLICH Laden, Datum, Datei und Summe. Es gibt
    # hier keine Spalte für Kartennummer, VU-Nummer, Terminal-ID, Trace- oder
    # Belegnummer — nicht „die füllen wir nicht", sondern es gibt sie nicht.
    # Das ist die strukturelle Hälfte der Zusage aus WB-358; die andere ist
    # `bons.zerlegen`, das den Zahlungsteil des Bons gar nicht erst ansieht.
    """
    CREATE TABLE IF NOT EXISTS receipt (
        id          INTEGER PRIMARY KEY,
        store       TEXT NOT NULL
                        CHECK (store IN ('rewe', 'lidl', 'unbekannt')),
        -- Der Einkaufstag vom Bon (ISO), nicht der Tag des Einlesens.
        bought_on   TEXT,
        -- Der Dateiname unter data/bons/, damit ein zweites Einlesen
        -- derselben Datei nicht eine zweite Kaufhistorie erzeugt. Nur der
        -- NAME, nie der Inhalt.
        file_name   TEXT UNIQUE,
        total_cents INTEGER,
        created_at  TEXT NOT NULL
    )
    """,
    # `receipt_item` ist zugleich die Preisbeobachtung: Produkt, Laden, Datum
    # und bezahlter Betrag stehen nach dem Verbund mit `receipt` vollständig
    # da. Eine zweite Tabelle „echte Preise" wäre eine Kopie derselben Zeilen
    # und könnte auseinanderlaufen, sobald jemand eine Zuordnung korrigiert.
    #
    # `decision` ist dieselbe Spalte mit derselben Bedeutung wie an
    # `chat_suggestion`: die Zuordnung ist ein VORSCHLAG, bis ein Mensch sie
    # bestätigt. Nur `kept` zählt als echter Preis und als Kaufhistorie — ein
    # falsch zugeordneter Kauf verfälscht die Vorlieben dauerhaft.
    """
    CREATE TABLE IF NOT EXISTS receipt_item (
        id          INTEGER PRIMARY KEY,
        receipt_id  INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
        line_no     INTEGER NOT NULL,
        -- Wie es auf dem Bon steht: „SCHIN.-KAE. CRO.". Bleibt unverändert,
        -- weil ein gewachsener Katalog später einen zweiten Versuch erlaubt.
        bon_text    TEXT NOT NULL,
        qty         INTEGER NOT NULL DEFAULT 1,
        unit_cents  INTEGER,
        -- Der Zeilenbetrag vom Bon. Das ist der echte bezahlte Preis.
        total_cents INTEGER NOT NULL,
        product_id  INTEGER REFERENCES product(id),
        -- Was das Modell aus der Abkürzung gemacht hat („Schinken-Käse-
        -- Croissant"). Erklärt die Zuordnung an der Zeile und überlebt einen
        -- Phoenix, der gerade nicht lief.
        note        TEXT,
        search_term TEXT,
        rank        REAL,
        decision    TEXT NOT NULL DEFAULT 'offen'
                        CHECK (decision IN ('offen', 'kept', 'removed')),
        decided_at  TEXT
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
    # Genau EIN Warenkorb, gemeinsam für beide (Spec 4). Der Zwang steht
    # bewusst hier und nicht nur in `orders.warenkorb()`: eine Python-Funktion
    # kann zwei gleichzeitige Aufrufe nicht daran hindern, beide „kein draft
    # vorhanden" zu lesen und beide einen anzulegen. Ein partieller UNIQUE-Index
    # kann es — der zweite INSERT scheitert, egal aus welchem Prozess er kommt.
    # Nur `state = 'draft'` ist eingeschränkt; von `offen` und `erledigt` darf
    # es beliebig viele geben.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_ein_draft"
    " ON orders(state) WHERE state = 'draft'",
    "CREATE INDEX IF NOT EXISTS ix_order_item_order ON order_item(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_recipe_item_recipe ON recipe_item(recipe_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_message_order ON chat_message(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_sugg_message ON chat_suggestion(chat_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_active ON product(active)",
    "CREATE INDEX IF NOT EXISTS ix_receipt_item_receipt"
    " ON receipt_item(receipt_id)",
    # Die Frage, die dieser Index beantwortet: „was hat DIESES Produkt in
    # Wirklichkeit gekostet?" Sie wird je Produktkachel gestellt, nicht einmal
    # im Nachtlauf.
    "CREATE INDEX IF NOT EXISTS ix_receipt_item_product"
    " ON receipt_item(product_id) WHERE product_id IS NOT NULL",
]

# Volltextsuche über die Felder, nach denen im Katalog gesucht wird. `external
# content` heisst: der Index speichert den Text nicht doppelt, sondern verweist
# auf product.rowid. Dafür müssen die Trigger unten den Index von Hand
# nachziehen — vergisst man einen, findet die Suche stillschweigend zu wenig.
FTS_SCHEMA = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS product_fts USING fts5(
        name, brand, category_l1, category_l2, category_l3,
        norm_name, norm_cat,
        content='product', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_ai AFTER INSERT ON product BEGIN
        INSERT INTO product_fts (rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES (new.id, new.name, new.brand,
                new.category_l1, new.category_l2, new.category_l3,
                new.norm_name, new.norm_cat);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_ad AFTER DELETE ON product BEGIN
        INSERT INTO product_fts (product_fts, rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES ('delete', old.id, old.name, old.brand,
                old.category_l1, old.category_l2, old.category_l3,
                old.norm_name, old.norm_cat);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_au AFTER UPDATE ON product BEGIN
        INSERT INTO product_fts (product_fts, rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES ('delete', old.id, old.name, old.brand,
                old.category_l1, old.category_l2, old.category_l3,
                old.norm_name, old.norm_cat);
        INSERT INTO product_fts (rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES (new.id, new.name, new.brand,
                new.category_l1, new.category_l2, new.category_l3,
                new.norm_name, new.norm_cat);
    END
    """,
]

#: Reihenfolge der Spalten in `product_fts`. bm25() bekommt seine Gewichte
#: positionsweise — steht hier etwas anderes als oben, gewichtet die Suche
#: stillschweigend die falsche Spalte.
FTS_SPALTEN = ("name", "brand", "category_l1", "category_l2", "category_l3",
               "norm_name", "norm_cat")

FTS_TRIGGER = ("product_fts_ai", "product_fts_ad", "product_fts_au")


#: Jede Tabelle, die `migrate()` anlegt. Der Test prüft gegen genau diese Liste,
#: damit eine vergessene Tabelle auffällt und nicht erst im Betrieb.
TABLES = (
    "product", "orders", "order_item", "recipe", "recipe_item",
    "chat_message", "chat_suggestion", "receipt", "receipt_item",
    "scrape_run", "product_fts",
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


def _spalten(con: sqlite3.Connection, tabelle: str,
             pragma: str = "table_info") -> list[str]:
    """Spaltennamen einer Tabelle.

    `table_xinfo` statt `table_info` für `product`: VIRTUAL generierte Spalten
    tauchen in `table_info` gar nicht auf — ohne das hielte die Migration sie
    für fehlend und liefe beim zweiten Aufruf in «duplicate column name».
    """
    return [r[1] for r in con.execute(f"PRAGMA {pragma}({tabelle})")]


def _norm_spalten_nachziehen(con: sqlite3.Connection) -> None:
    """Ergänzt `product` um die normalisierten Suchspalten, falls sie fehlen.

    Bewusst per ALTER statt im CREATE TABLE: so gibt es einen Codepfad für die
    frische und die gewachsene Datenbank, und `CREATE TABLE IF NOT EXISTS`
    kann eine bestehende Tabelle nicht stillschweigend übergehen.
    """
    vorhanden = _spalten(con, "product", "table_xinfo")
    for name, ausdruck in NORM_SPALTEN:
        if name not in vorhanden:
            con.execute(f"ALTER TABLE product ADD COLUMN {name} TEXT"
                        f" GENERATED ALWAYS AS ({ausdruck}) VIRTUAL")


def _fts_nachziehen(con: sqlite3.Connection) -> bool:
    """Wirft einen veralteten FTS-Index weg. Gibt zurück, ob neu gebaut wurde.

    `CREATE VIRTUAL TABLE IF NOT EXISTS` ändert eine bestehende Tabelle nicht,
    und ein Index ohne die norm-Spalten fände die Umlautschreibweisen nie.
    Ein Index ist reine Ableitung aus `product` — ihn wegzuwerfen und aus dem
    Inhalt neu aufzubauen kostet nichts ausser Zeit.
    """
    vorhanden = _spalten(con, "product_fts")
    if not vorhanden or list(vorhanden) == list(FTS_SPALTEN):
        return False
    for trigger in FTS_TRIGGER:
        # Die alten Trigger kennen die neuen Spalten nicht und würden nach
        # CREATE TRIGGER IF NOT EXISTS unverändert stehen bleiben.
        con.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    con.execute("DROP TABLE product_fts")
    return True


def migrate(con: sqlite3.Connection) -> None:
    """Legt das vollständige Schema an. Mehrfach aufrufbar."""
    for stmt in SCHEMA:
        con.execute(stmt)
    _norm_spalten_nachziehen(con)
    neu_gebaut = _fts_nachziehen(con)
    for stmt in FTS_SCHEMA:
        con.execute(stmt)
    if neu_gebaut:
        con.execute("INSERT INTO product_fts (product_fts) VALUES ('rebuild')")
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
