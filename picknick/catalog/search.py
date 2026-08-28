"""Katalogsuche über SQLite-FTS5.

Der Shop ruft nie eine fremde Seite auf (Spec 3) — gesucht wird ausschliesslich
im lokalen Index, den der Crawler füllt. Diese Suche ist zugleich Stufe 2 des
Agenten (Spec 6): das Modell liefert Begriffe, der Shop sucht selbst und legt
die Kandidaten vor. Deshalb gibt `search()` den FTS-Rang mit zurück; er wird als
Score in den RETRIEVER-Span geschrieben (Spec 7.1) und ist dort die Antwort auf
die Frage, ob ein Fehlgriff am Modell lag oder daran, dass die Suche das
richtige Produkt nie vorgelegt hat.
"""
from __future__ import annotations

import re
import sqlite3

from picknick import db

#: Wie stark ein Treffer im Produktnamen gegenüber einem blossen
#: Kategorietreffer zählt. „Milch" soll die Milchflasche vor den Joghurt aus der
#: Kategorie „Milch, Molkerei & Butter" schieben — aber der Joghurt soll
#: erscheinen, denn oft ist die Kategorie das Einzige, was passt.
GEWICHT_NAME = 10.0
GEWICHT_KATEGORIE = 1.0

#: Nur die norm-Spalten werden durchsucht (siehe `db.UMLAUTE`). Die
#: unveränderten Spalten bleiben im Index, weil andere Abfragen sie benutzen.
_SUCHSPALTEN = "{norm_name norm_cat}"

#: Alles, was kein Wortzeichen ist, fliegt raus. Das ist die ganze Absicherung
#: gegen kaputte FTS5-Syntax: ein Anführungszeichen, ein `*`, ein `NEAR` in
#: Klammern — nichts davon überlebt diesen Filter, also kann nichts davon die
#: Abfrage sprengen. Weisse Liste statt Escapen, weil Escapen bei FTS5 von der
#: Position im Ausdruck abhängt und damit eine Fehlerquelle bleibt.
_WORT = re.compile(r"\w+", re.UNICODE)

#: Spalten, die ein Treffer mitbringt. Bewusst dieselben Namen wie in `product`.
_PRODUKT_SPALTEN = (
    "id", "source", "external_id", "name", "brand", "price_cents",
    "price_per_unit_cents", "unit_text", "unit", "image_path",
    "category_l1", "category_l2", "category_l3", "in_stock", "last_seen_at",
)


def begriffe(text: str) -> list[str]:
    """Freitext -> normalisierte Suchwörter. Kann leer sein."""
    return _WORT.findall(db.normalisiere(text or ""))


def fts_query(text: str) -> str | None:
    """Baut den FTS5-Ausdruck. `None`, wenn nichts Suchbares übrig bleibt.

    Jedes Wort wird als Präfix gesucht (`"milch"*`), weil im Deutschen das
    gesuchte Wort oft hinten am Kompositum klebt und der Nutzer beim Tippen
    ohnehin selten fertig ist. Mehrere Wörter werden UND-verknüpft — wer
    „passierte tomaten" tippt, will nicht jede Tomate.
    """
    woerter = begriffe(text)
    if not woerter:
        return None
    # Anführungszeichen um jedes Wort, damit ein Wort wie „and" oder „near"
    # nicht als Operator gelesen wird. Enthalten kann es keine, siehe _WORT.
    ausdruck = " ".join(f'"{w}"*' for w in woerter)
    return f"{_SUCHSPALTEN} : ({ausdruck})"


def _bm25() -> str:
    gewichte = [0.0] * len(db.FTS_SPALTEN)
    gewichte[db.FTS_SPALTEN.index("norm_name")] = GEWICHT_NAME
    gewichte[db.FTS_SPALTEN.index("norm_cat")] = GEWICHT_KATEGORIE
    return "bm25(product_fts, " + ", ".join(str(g) for g in gewichte) + ")"


def search(con: sqlite3.Connection, begriff: str,
           limit: int = 20) -> list[dict]:
    """Sucht aktive Produkte zu einem Freitextbegriff.

    Gibt Wörterbücher mit allen Produktspalten plus `rang` zurück, absteigend
    nach Relevanz. `rang` ist das negierte bm25 und damit positiv: je grösser,
    desto besser. bm25 selbst ist negativ und „kleiner ist besser" — als Score
    an Phoenix übergeben wäre das genau falsch herum lesbar, und ein Score, den
    man rückwärts lesen muss, wird irgendwann rückwärts gelesen.
    """
    query = fts_query(begriff)
    if query is None:
        return []
    spalten = ", ".join(f"p.{s}" for s in _PRODUKT_SPALTEN)
    rows = con.execute(
        f"SELECT {spalten}, -{_bm25()} AS rang"
        "  FROM product_fts f JOIN product p ON p.id = f.rowid"
        " WHERE product_fts MATCH ? AND p.active = 1"
        # Der Name als zweites Kriterium: bei gleichem Rang sonst die Reihen-
        # folge der rowids, und die ändert sich mit jedem Crawl.
        " ORDER BY rang DESC, p.name ASC"
        " LIMIT ?",
        (query, limit)).fetchall()
    return [dict(r) for r in rows]


def suche_kette(con: sqlite3.Connection, suchbegriffe, *, limit: int = 20,
                obergrenze: int | None = None) -> list[dict]:
    """Sucht eine ganze Begriffskette und VEREINIGT die Treffer (WB-340).

    Stufe 1 liefert je Zutat mehrere Begriffe, vom genauesten zum
    allgemeinsten („Auberginen", „Aubergine"). Hier wird jeder davon gesucht;
    die Treffer werden **nach Produkt-ID entdoppelt** und als eine
    Kandidatenliste zurückgegeben. Jeder Treffer trägt zusätzlich `via`: den
    Begriff, der ihn gebracht hat. Ohne dieses Feld wäre nach der Vereinigung
    nicht mehr zu sagen, warum ein Produkt vorlag — und genau das will der
    Span-Vertrag (Spec 7.1) beantworten können.

    **Nicht „der erste Begriff, der etwas findet, gewinnt".** Das ist gemessen
    und schlechter: „Auberginen" findet das Fertiggericht
    „Gemüse-Auberginen-Masala" mit Rang 16,55, die echte Aubergine kommt erst
    über den Singular und mit niedrigerem Rang (13,08). Wer nach dem ersten
    Fund abbricht, zurrt das Fertiggericht fest. Die Vereinigung legt beides
    vor und lässt Stufe 3 entscheiden.

    **Die Reihenfolge ist die der KETTE, nicht die des Rangs.** Der genaueste
    Begriff zuerst, innerhalb eines Begriffs nach dessen Rang. Global nach
    `rang` zu sortieren wäre falsch, und zwar gemessen: bm25 ist über Abfragen
    hinweg nicht geeicht (siehe `obs.spans.schwaechste_suche`), ein seltenes
    Wort bekommt strukturell einen höheren Rang als ein häufiges. Für die Kette
    [Mais, Süßmais, Körnig] steht dann der „MIIL Körniger Frischkäse" mit 14,01
    ÜBER der Maishähnchenkeule mit 9,70 — nicht weil er besser passt, sondern
    weil „Körnig" seltener ist als „Mais". Nach Rang sortiert bekäme Stufe 3
    drei Frischkäse zuerst vorgelegt. Die Kettenreihenfolge ist die einzige
    Rangfolge, die hier etwas bedeutet: sie kommt vom Modell und ist nach
    Genauigkeit geordnet.

    `obergrenze` begrenzt die Zahl der Kandidaten je Zutat, und das Budget wird
    **vom genauen Ende der Kette her ausgegeben**: der erste Begriff bekommt
    seine Treffer ganz, dann der zweite, und der letzte bekommt, was übrig ist.
    Mit der Vorgabe (`plan.MAX_KANDIDATEN` = 2 × `plan.KANDIDATEN`) passen die
    ersten beiden Begriffe immer vollständig hinein; gekürzt wird nur am
    allgemeinen Ende. Das ist auch das Ende, an dem das Modell entgleist —
    gemessen wurden als letzte Begriffe „Körnig", „Papikra", „Konzenzrat". Ein
    solcher Begriff findet irgendetwas, und was er findet, soll als Letztes
    stehen und als Erstes wegfallen.

    Gesucht wird trotzdem jeder Begriff: was die Kette gebracht hat, steht
    dadurch vollständig im RETRIEVER-Span, auch wenn die Obergrenze das Ende
    abschneidet.
    """
    gewaehlt: list[dict] = []
    gesehen: set[int] = set()
    for begriff in suchbegriffe:
        for p in search(con, begriff, limit=limit):
            pid = int(p["id"])
            if pid in gesehen:
                # Schon über einen genaueren Begriff vorgelegt. Die Herkunft
                # bleibt beim ersten — er beschreibt die Zutat besser.
                continue
            gesehen.add(pid)
            if obergrenze is not None and len(gewaehlt) >= obergrenze:
                continue
            gewaehlt.append({**p, "via": begriff})
    return gewaehlt
