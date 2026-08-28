"""Kategoriebaum und Blättern im Katalog.

Der Baum wird nicht als eigene Tabelle gepflegt, sondern bei Bedarf aus den
Spalten `category_l1/l2/l3` der aktiven Produkte gebildet. Grund: Knuspr
liefert die Kategorien in jeder Produktantwort mit, eine zweite Tabelle wäre
eine zweite Wahrheit — und die geht beim nächsten Crawl auseinander. Der
Katalog ist klein genug, dass die Gruppierung billig ist.

Nur aktive Produkte zählen. Ein Produkt, das der letzte Lauf nicht mehr sah,
wird `active = 0` statt gelöscht (Spec 5.3); es darf aber keine Kategorie in
der Oberfläche am Leben halten, in der nichts mehr steht.
"""
from __future__ import annotations

import sqlite3

#: Die drei Ebenen in der Reihenfolge, in der sie ineinander stecken.
EBENEN = ("category_l1", "category_l2", "category_l3")

#: Spalten, die eine Produktzeile in der Kategorieansicht mitbringt.
_PRODUKT_SPALTEN = (
    "id", "source", "external_id", "name", "brand", "price_cents",
    "price_per_unit_cents", "unit_text", "unit", "image_path",
    "category_l1", "category_l2", "category_l3", "in_stock", "last_seen_at",
)


#: Dieselbe Ausschlussliste wie in der Suche (WB-343) — der Kategoriebaum darf
#: nicht anbieten, was die Suche nicht findet, sonst führt die Oberfläche in
#: eine leere Kategorie.
from picknick.catalog.search import AUSGESCHLOSSENE_KATEGORIEN


def _platzhalter() -> str:
    """Fragezeichen für die Ausschlussliste — nie die Werte in SQL einsetzen."""
    return ", ".join("?" for _ in AUSGESCHLOSSENE_KATEGORIEN)


def tree(con: sqlite3.Connection) -> list[dict]:
    """Der dreistufige Kategoriebaum der aktiven Produkte.

    Jeder Knoten ist `{"name", "anzahl", "kinder"}`. `anzahl` ist die Zahl der
    Produkte im ganzen Teilbaum, nicht nur direkt darunter — die Oberfläche
    zeigt sie neben dem Kategorienamen, und dort will niemand „Molkerei (0)"
    lesen, nur weil alle Produkte eine Ebene tiefer hängen.

    Fehlt einem Produkt eine Ebene, endet sein Ast dort. Ein Knoten „(ohne
    Kategorie)" wäre eine erfundene Kategorie; ein leerer Name in der Liste
    wäre ein toter Link.
    """
    rows = con.execute(
        "SELECT category_l1 AS l1, category_l2 AS l2, category_l3 AS l3,"
        "       count(*) AS n"
        "  FROM product WHERE active = 1"
        f"   AND coalesce(category_l1, '') NOT IN ({_platzhalter()})"
        " GROUP BY l1, l2, l3", AUSGESCHLOSSENE_KATEGORIEN).fetchall()

    baum: dict = {}
    for r in rows:
        pfad = [r["l1"], r["l2"], r["l3"]]
        knoten = baum
        for name in pfad:
            if not name:
                break
            eintrag = knoten.setdefault(name, {"anzahl": 0, "kinder": {}})
            eintrag["anzahl"] += r["n"]
            knoten = eintrag["kinder"]
    return _als_liste(baum)


def _als_liste(knoten: dict) -> list[dict]:
    """dict -> alphabetisch sortierte Liste. Erst hier wird die Form stabil."""
    return [{"name": name,
             "anzahl": eintrag["anzahl"],
             "kinder": _als_liste(eintrag["kinder"])}
            for name, eintrag in sorted(knoten.items())]


def by_category(con: sqlite3.Connection,
                category_l1: str | None = None,
                category_l2: str | None = None,
                category_l3: str | None = None,
                limit: int = 40, offset: int = 0) -> list[dict]:
    """Aktive Produkte einer Kategorie, blätterbar.

    Angegeben wird so viel vom Pfad, wie bekannt ist: nur `category_l1` liefert
    alles darunter. Ohne jede Angabe kommt der ganze Katalog — das ist die
    Startansicht, kein Sonderfall.
    """
    # WB-343: dieselbe Ausschlussliste wie in der Suche. Auch hier, damit
    # niemand über eine von Hand gebaute Adresse in eine Kategorie gerät, die
    # der Baum gar nicht mehr anbietet.
    bedingungen = ["active = 1",
                   f"coalesce(category_l1, '') NOT IN ({_platzhalter()})"]
    werte: list = list(AUSGESCHLOSSENE_KATEGORIEN)
    for spalte, wert in zip(EBENEN, (category_l1, category_l2, category_l3)):
        if wert is not None:
            bedingungen.append(f"{spalte} = ?")
            werte.append(wert)
    spalten = ", ".join(_PRODUKT_SPALTEN)
    rows = con.execute(
        f"SELECT {spalten} FROM product"
        f" WHERE {' AND '.join(bedingungen)}"
        # Sortiert nach Name, id als Tiebreaker: ohne eine totale Ordnung darf
        # SQLite bei LIMIT/OFFSET dieselbe Zeile zweimal oder gar nicht
        # liefern — Blättern würde dann Produkte verschlucken.
        "  ORDER BY name COLLATE NOCASE ASC, id ASC"
        "  LIMIT ? OFFSET ?", (*werte, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def count_by_category(con: sqlite3.Connection,
                      category_l1: str | None = None,
                      category_l2: str | None = None,
                      category_l3: str | None = None) -> int:
    """Wie viele aktive Produkte in der Kategorie stehen.

    Die Oberfläche braucht das für „Seite 2 von 5"; ohne die Gesamtzahl weiss
    sie erst nach der letzten leeren Seite, dass Schluss ist.
    """
    # WB-343: dieselbe Ausschlussliste wie in der Suche. Auch hier, damit
    # niemand über eine von Hand gebaute Adresse in eine Kategorie gerät, die
    # der Baum gar nicht mehr anbietet.
    bedingungen = ["active = 1",
                   f"coalesce(category_l1, '') NOT IN ({_platzhalter()})"]
    werte: list = list(AUSGESCHLOSSENE_KATEGORIEN)
    for spalte, wert in zip(EBENEN, (category_l1, category_l2, category_l3)):
        if wert is not None:
            bedingungen.append(f"{spalte} = ?")
            werte.append(wert)
    return int(con.execute(
        f"SELECT count(*) AS n FROM product WHERE {' AND '.join(bedingungen)}",
        werte).fetchone()["n"])


# --------------------------------------------------------------------------
# Oberbegriffe und ihre Sorten (WB-368)
#
# Wer „Aufschnitt" tippt, meint kein Produkt, sondern eine Warengruppe — und
# die Auswahl dazu muss nicht erfunden werden, sie steht bereits im Baum:
#
#     Aufschnitt -> Rohschinken & Bacon (58)  Kochschinken (42)
#                   Geflügelwurst (40)        Brühwurst (40)
#                   Salami (34)               Sülze & Wurst in Aspik (13)
#
# **Das ist besser als eine Modellantwort, und zwar aus einem Grund: die
# Zahlen sind echt.** Angeboten wird nur, was wirklich im Katalog steht.
# Dasselbe mit Qwen erzeugt (gemessen 2026-08-28) lieferte „SCHWEINEBRUST"
# (null Treffer), „Bananen" doppelt und ein verstümmeltes „Birn". Der Katalog
# tut das nicht — eine Sorte ohne aktive Produkte taucht in einem GROUP BY
# gar nicht erst auf.

#: Ab wie vielen Unterkategorien eine L1-Kategorie ein OBERBEGRIFF ist.
#:
#: Vier, und das ist eine Abwägung mit zwei Seiten. Nach unten: eine Kategorie
#: mit zwei Sorten ist keine Auswahl, sondern ein Umweg — wer „Kräuter" tippt,
#: soll nicht erst zwischen zwei Kästchen wählen, um dorthin zu kommen, wo er
#: ohne Auffächerung sofort gewesen wäre. Nach oben: die Liste, die dem Modell
#: vorgelegt wird, soll überschaubar bleiben. Gemessen am echten Katalog
#: (2026-08-28, 10.361 Produkte): von 139 L1-Kategorien haben 56 mindestens
#: vier Sorten, das sind 1.036 Zeichen Vorlage. Mit drei wären es 78.
MIN_SORTEN = 4


def sorten(con: sqlite3.Connection, category_l1: str) -> list[dict]:
    """Die Unterkategorien einer L1-Kategorie, mit ihrer echten Produktzahl.

    `[{"name": "Salami", "anzahl": 34}, …]`, nach Anzahl absteigend — die
    grösste Sorte zuerst, weil sie die wahrscheinlichste ist.

    **Eine Sorte ohne aktive Produkte kommt hier nicht vor.** Das ist keine
    Prüfung, sondern die Bauart der Abfrage: gezählt werden Produktzeilen, und
    wo keine steht, entsteht auch keine Gruppe. Genau deshalb kann diese Liste
    nichts anbieten, was es nicht gibt.
    """
    rows = con.execute(
        "SELECT category_l2 AS name, count(*) AS anzahl"
        "  FROM product WHERE active = 1 AND category_l1 = ?"
        "   AND coalesce(category_l2, '') <> ''"
        f"   AND coalesce(category_l1, '') NOT IN ({_platzhalter()})"
        " GROUP BY category_l2"
        # Anzahl absteigend, bei Gleichstand der Name — ohne die zweite
        # Spalte hinge die Reihenfolge zweier gleich grosser Sorten an der
        # Speicherreihenfolge und änderte sich mit jedem Crawl.
        " ORDER BY anzahl DESC, name COLLATE NOCASE ASC",
        (category_l1, *AUSGESCHLOSSENE_KATEGORIEN)).fetchall()
    return [{"name": r["name"], "anzahl": int(r["anzahl"])} for r in rows]


def oberbegriffe(con: sqlite3.Connection, *,
                 min_sorten: int = MIN_SORTEN) -> list[dict]:
    """Alle L1-Kategorien mit genug Sorten, um eine Auswahl zu sein.

    `[{"name", "anzahl", "sorten": [{"name", "anzahl"}, …]}, …]`,
    alphabetisch. Eine Abfrage für den ganzen Katalog: die Namen daraus sind
    die Vorlage für das Modell (`assistant.plan.extract_plan`), die Sorten
    darunter sind das, was der Nutzerin angeboten wird.
    """
    rows = con.execute(
        "SELECT category_l1 AS l1, category_l2 AS l2, count(*) AS anzahl"
        "  FROM product WHERE active = 1"
        "   AND coalesce(category_l1, '') <> ''"
        "   AND coalesce(category_l2, '') <> ''"
        f"   AND coalesce(category_l1, '') NOT IN ({_platzhalter()})"
        " GROUP BY l1, l2"
        " ORDER BY l1 COLLATE NOCASE ASC, anzahl DESC, l2 COLLATE NOCASE ASC",
        AUSGESCHLOSSENE_KATEGORIEN).fetchall()

    nach_l1: dict[str, list[dict]] = {}
    for r in rows:
        nach_l1.setdefault(r["l1"], []).append(
            {"name": r["l2"], "anzahl": int(r["anzahl"])})
    return [{"name": name,
             "anzahl": sum(s["anzahl"] for s in liste),
             "sorten": liste}
            for name, liste in nach_l1.items()
            if len(liste) >= min_sorten]
