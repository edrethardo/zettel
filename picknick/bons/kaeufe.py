"""Was gekauft wurde — Kassenbon und Posten in der Datenbank (WB-358).

Die Begründung des Schemas steht bei den beiden `CREATE TABLE` in
`picknick/db.py` und wird hier nicht wiederholt. Was dieses Modul dazu tut,
sind drei Regeln:

**1. Hier landen nur Laden, Datum, Artikelname, Menge und Preis.** Es gibt
keinen Weg, etwas anderes zu schreiben: das Schema kennt keine Spalte dafür,
und die einzige Quelle für einen Posten ist `zerlegen.Bon`, das den
Zahlungsteil des Bons gar nicht erst ansieht. Der Test in
`tests/test_bons_kaeufe.py` liest die ganze Datenbank als Text zurück und
sucht die Kartennummer darin — wer diese Zusage später bricht, bricht ihn.

**2. Eine Zuordnung gilt erst nach Bestätigung.** `decision` beginnt bei
`offen` und bedeutet dort dasselbe wie an `chat_suggestion`: nie entschieden.
Nur `kept` zählt als Kaufhistorie und als echter Preis. Das ist keine
Vorsicht, sondern Rechnen: ein falsch zugeordneter Kauf schiebt die Vorlieben
aus WB-341 dauerhaft in die falsche Richtung, und niemand sieht es ihm später
an.

**3. Der echte Preis wird nirgends kopiert.** `receipt_item` mit seinem
`receipt` IST die Preisbeobachtung — Produkt, Laden, Tag, bezahlter Betrag.
`echte_preise()` ist eine Abfrage darauf und keine zweite Ablage; eine
korrigierte Zuordnung ändert damit sofort auch den Preis, statt eine Kopie
zurückzulassen, die niemand nachzieht.
"""
from __future__ import annotations

import sqlite3

from picknick import db, orders
from picknick.bons import zerlegen
from picknick.orders.bestellung import jetzt

#: Wie `receipt_item.decision` heisst. Aus `db` geholt, damit es genau eine
#: Wahrheit gibt — der CHECK in der Tabelle ist dieselbe Liste, und es ist
#: dieselbe wie beim Chat-Vorschlag.
OFFEN, BESTAETIGT, VERWORFEN = db.DECISIONS


class KaufFehler(ValueError):
    """Ein Bon oder eine Entscheidung, die es so nicht gibt."""


# --------------------------------------------------------------------------
# Schreiben

def anlegen(con: sqlite3.Connection, bon: zerlegen.Bon, *,
            datei: str | None = None, ersetzen: bool = False) -> int:
    """Schreibt einen zerlegten Bon und gibt die `receipt.id` zurück.

    Alle Posten kommen als `offen` und OHNE Produkt herein. Die Zuordnung ist
    ein eigener Schritt (`bons.zuordnung`) und braucht das Modell; das Einlesen
    braucht es nicht. Genau deshalb sind es zwei Schritte: schläft die Box,
    stehen die Käufe trotzdem mit Preis und Datum da, und die Zuordnung wird
    nachgeholt.

    `ersetzen=True` wirft einen früheren Beleg zu derselben Datei weg —
    mitsamt seinen Entscheidungen, denn sie gehören zu Zeilen, die es dann
    nicht mehr gibt. Ohne das Flag ist ein zweites Einlesen ein Fehler und
    keine stille Verdopplung der Kaufhistorie.
    """
    if not bon.posten:
        raise KaufFehler("Ein Bon ohne Posten wird nicht abgelegt.")
    if bon.laden not in db.BON_STORES:
        raise KaufFehler(
            f"{bon.laden!r} ist kein Laden. Erlaubt: {', '.join(db.BON_STORES)}.")

    if datei:
        vorhanden = beleg_zu_datei(con, datei)
        if vorhanden is not None:
            if not ersetzen:
                raise KaufFehler(
                    f"{datei} ist schon eingelesen (Beleg {vorhanden['id']}). "
                    "Ein zweites Einlesen legt sonst dieselben Käufe noch "
                    "einmal an.")
            # ON DELETE CASCADE räumt die Posten mit weg — deshalb steht in
            # `db.connect()` das PRAGMA foreign_keys.
            con.execute("DELETE FROM receipt WHERE id = ?", (vorhanden["id"],))

    cur = con.execute(
        "INSERT INTO receipt (store, bought_on, file_name, total_cents,"
        "                     created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (bon.laden, bon.datum, datei, bon.summe_cents, jetzt()))
    receipt_id = int(cur.lastrowid)
    for p in bon.posten:
        con.execute(
            "INSERT INTO receipt_item (receipt_id, line_no, bon_text, qty,"
            "                          unit_cents, total_cents, decision)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (receipt_id, p.zeile, p.text, max(1, p.menge), p.einzel_cents,
             p.gesamt_cents, OFFEN))
    con.commit()
    return receipt_id


def zuordnung_setzen(con: sqlite3.Connection, item_id: int, *,
                     product_id: int | None, note: str | None = None,
                     search_term: str | None = None,
                     rang: float | None = None) -> None:
    """Trägt einen VORSCHLAG an einer Bon-Zeile ein. Entscheidet nichts.

    Nur an einer Zeile, die noch `offen` ist: eine bestätigte oder verworfene
    Zuordnung darf ein zweiter Modelllauf nicht überschreiben — sonst wäre die
    Entscheidung eines Menschen weniger wert als der nächste Durchlauf.
    """
    if product_id is not None and not con.execute(
            "SELECT 1 FROM product WHERE id = ?", (product_id,)).fetchone():
        raise KaufFehler(f"Produkt {product_id} gibt es nicht.")
    con.execute(
        "UPDATE receipt_item"
        "   SET product_id = ?, note = ?, search_term = ?, rank = ?"
        " WHERE id = ? AND decision = ?",
        (product_id, note, search_term,
         None if rang is None else float(rang), item_id, OFFEN))
    con.commit()


def entscheiden(con: sqlite3.Connection, item_id: int,
                entscheidung: str) -> dict:
    """`kept` bestätigt die Zuordnung, `removed` verwirft sie.

    Das Produkt bleibt in beiden Fällen an der Zeile stehen. Bei `removed` ist
    es die Auskunft „das hat das Modell vorgeschlagen, und es war falsch" —
    dieselbe, aus der bei `chat_suggestion` die Trefferquote fällt. Es zu
    löschen würde die Zeile heilen und die Messung verlieren.

    Ein zweites Mal dieselbe Entscheidung ändert nichts: auf dem Handy ist ein
    Doppeltipp schnell passiert.
    """
    if entscheidung not in db.DECISIONS:
        raise KaufFehler(
            f"{entscheidung!r} ist keine Entscheidung. Erlaubt: "
            f"{', '.join(db.DECISIONS)}.")
    zeile = posten_zeile(con, item_id)
    if zeile["decision"] == entscheidung:
        return zeile
    if entscheidung == BESTAETIGT and zeile["product_id"] is None:
        # „Ja" zu nichts. Ohne Produkt gibt es keine Kaufhistorie und keinen
        # Preis, den man einem Produkt zuschreiben könnte — die Zeile bliebe
        # bestätigt und trotzdem leer.
        raise KaufFehler(
            f"„{zeile['bon_text']}“ ist keinem Produkt zugeordnet. Bestätigen "
            "lässt sich nur eine Zuordnung — such ein Produkt aus oder lass "
            "die Zeile offen.")
    con.execute(
        "UPDATE receipt_item SET decision = ?, decided_at = ? WHERE id = ?",
        (entscheidung, None if entscheidung == OFFEN else jetzt(), item_id))
    con.commit()
    return posten_zeile(con, item_id)


def korrigieren(con: sqlite3.Connection, item_id: int,
                product_id: int) -> dict:
    """Setzt ein anderes Produkt an die Zeile und bestätigt sie.

    Der Weg für den Fall, den das Ticket ausdrücklich nennt: die Zuordnung
    wird bestätigt ODER korrigiert, nicht geraten. `search_term` wird dabei
    geleert und `note` überschrieben — was hier steht, kommt jetzt von einem
    Menschen und nicht mehr aus der Suche, und ein stehengebliebener Rang
    behauptete das Gegenteil.
    """
    if not con.execute("SELECT 1 FROM product WHERE id = ?",
                       (product_id,)).fetchone():
        raise KaufFehler(f"Produkt {product_id} gibt es nicht.")
    posten_zeile(con, item_id)          # wirft, wenn es die Zeile nicht gibt
    con.execute(
        "UPDATE receipt_item"
        "   SET product_id = ?, note = ?, search_term = NULL, rank = NULL,"
        "       decision = ?, decided_at = ?"
        " WHERE id = ?",
        (product_id, "von Hand zugeordnet", BESTAETIGT, jetzt(), item_id))
    con.commit()
    return posten_zeile(con, item_id)


# --------------------------------------------------------------------------
# Lesen

_POSTEN_SQL = (
    "SELECT i.id, i.receipt_id, i.line_no, i.bon_text, i.qty, i.unit_cents,"
    "       i.total_cents, i.product_id, i.note, i.search_term,"
    "       i.rank AS rang, i.decision, i.decided_at,"
    "       p.name AS produkt_name, p.unit_text, p.price_cents, p.image_path,"
    "       p.active"
    "  FROM receipt_item i LEFT JOIN product p ON p.id = i.product_id"
    # LEFT JOIN und kein `active = 1`: derselbe Grund wie beim Bestellposten
    # (WB-335) — ein Produkt, das ein Crawl ausgemustert hat, wird markiert
    # und nicht verschwiegen. Der Kauf hat trotzdem stattgefunden.
)


def _auf(row: sqlite3.Row) -> dict:
    z = orders.markiere_katalogstand(dict(row))
    # `ist_freitext` heisst an einer Bon-Zeile etwas anderes als im Warenkorb
    # und wäre hier ein falscher Freund: eine Zeile ohne Produkt ist kein
    # Freitext-Wunsch, sondern eine Zuordnung, die noch fehlt oder nicht
    # gelingt. Deshalb ein eigener, ehrlicher Name.
    z["ohne_produkt"] = z.pop("ist_freitext")
    z["offen"] = z["decision"] == OFFEN
    z["bestaetigt"] = z["decision"] == BESTAETIGT
    z["verworfen"] = z["decision"] == VERWORFEN
    # Angezeigt wird immer der Bon-Text; der Produktname steht daneben. Ihn zu
    # ersetzen wäre die eine Sache, die man an dieser Seite nicht tun darf:
    # bestätigt wird die Verbindung zwischen beiden, und dafür muss man beide
    # sehen.
    z["produkt_fehlt"] = z["product_id"] is None
    return z


def posten(con: sqlite3.Connection, receipt_id: int) -> list[dict]:
    """Die Zeilen eines Belegs, in der Reihenfolge des Bons."""
    return [_auf(r) for r in con.execute(
        _POSTEN_SQL + " WHERE i.receipt_id = ? ORDER BY i.line_no, i.id",
        (receipt_id,)).fetchall()]


def posten_zeile(con: sqlite3.Connection, item_id: int) -> dict:
    row = con.execute(_POSTEN_SQL + " WHERE i.id = ?", (item_id,)).fetchone()
    if row is None:
        raise KaufFehler(f"Bon-Zeile {item_id} gibt es nicht.")
    return _auf(row)


_BELEG_SQL = (
    "SELECT r.id, r.store, r.bought_on, r.file_name, r.total_cents,"
    "       r.created_at,"
    "       (SELECT count(*) FROM receipt_item i WHERE i.receipt_id = r.id)"
    "           AS n_posten,"
    "       (SELECT count(*) FROM receipt_item i WHERE i.receipt_id = r.id"
    "          AND i.decision = 'offen') AS n_offen,"
    "       (SELECT count(*) FROM receipt_item i WHERE i.receipt_id = r.id"
    "          AND i.decision = 'kept') AS n_bestaetigt"
    "  FROM receipt r"
)


def beleg(con: sqlite3.Connection, receipt_id: int) -> dict:
    row = con.execute(_BELEG_SQL + " WHERE r.id = ?", (receipt_id,)).fetchone()
    if row is None:
        raise KaufFehler(f"Beleg {receipt_id} gibt es nicht.")
    return dict(row)


def beleg_zu_datei(con: sqlite3.Connection, datei: str) -> dict | None:
    """Der Beleg zu einer abgelegten Datei — oder `None`.

    Die Bon-Seite listet Dateien, nicht Belege. Ohne diesen Weg wüsste sie
    nicht, welche davon schon gelesen sind, und böte „Auslesen" ein zweites
    Mal an.
    """
    row = con.execute(_BELEG_SQL + " WHERE r.file_name = ?",
                      (datei,)).fetchone()
    return dict(row) if row else None


def belege(con: sqlite3.Connection) -> list[dict]:
    """Alle Belege, neueste zuerst.

    Sortiert nach dem EINKAUFSTAG und erst danach nach der id: die Reihenfolge
    des Einlesens sagt nichts über den Einkauf aus, und wer drei alte Bons
    nacheinander hochlädt, will sie nicht in der Reihenfolge des Hochladens
    sehen.
    """
    return [dict(r) for r in con.execute(
        _BELEG_SQL + " ORDER BY r.bought_on IS NULL, r.bought_on DESC,"
                     " r.id DESC").fetchall()]


def bilanz(con: sqlite3.Connection, receipt_id: int) -> dict:
    """Wieviel des Belegs bestätigt ist — und was die Posten zusammen kosten.

    `summe_posten_cents` neben `total_cents` stehen zu lassen ist Absicht: die
    Differenz ist das Pfand und alles, was die Zerlegung übersehen hat. Eine
    einzelne Zahl könnte das nicht sagen.
    """
    zeilen = posten(con, receipt_id)
    bestaetigt = sum(1 for z in zeilen if z["bestaetigt"])
    verworfen = sum(1 for z in zeilen if z["verworfen"])
    entschieden = bestaetigt + verworfen
    return {
        "posten": len(zeilen),
        "bestaetigt": bestaetigt,
        "verworfen": verworfen,
        "offen": len(zeilen) - entschieden,
        "summe_posten_cents": sum(z["total_cents"] for z in zeilen),
        # `None` und nicht 0.0, solange nichts entschieden wurde: eine 0 hiesse
        # „alles falsch", wo „noch nichts gesagt" richtig ist (wie in
        # `assistant.vorschlaege.quote`).
        "quote": (bestaetigt / entschieden) if entschieden else None,
    }


# --------------------------------------------------------------------------
# Der echte Preis
#
# Das ist der Punkt, an dem der Vorbehalt „die Preise im Katalog sind
# Knuspr-Preise" für die tatsächlich gekauften Produkte verschwindet. Er
# verschwindet aber nur für BESTÄTIGTE Zeilen: eine offene Zuordnung ist eine
# Vermutung, und eine Vermutung darf keinen Preis behaupten.

_PREIS_SQL = (
    "SELECT i.product_id, i.bon_text, i.qty, i.unit_cents, i.total_cents,"
    "       r.store, r.bought_on, i.receipt_id"
    "  FROM receipt_item i JOIN receipt r ON r.id = i.receipt_id"
    " WHERE i.decision = 'kept' AND i.product_id IS NOT NULL"
)


def echte_preise(con: sqlite3.Connection,
                 product_id: int | None = None) -> list[dict]:
    """Bestätigte Käufe als Preisbeobachtungen, neueste zuerst.

    Je Zeile: Produkt, Laden, Einkaufstag, Menge und der Betrag, der auf dem
    Bon stand. `stueck_cents` ist der Einzelpreis, wenn der Bon ihn nennt —
    und sonst der Zeilenbetrag bei Menge 1. Bei Menge 2 ohne Mengenzeile
    bleibt er leer, statt zu dividieren: eine Division wäre eine Behauptung
    über Rundung, die der Bon nicht deckt.
    """
    sql = _PREIS_SQL
    args: tuple = ()
    if product_id is not None:
        sql += " AND i.product_id = ?"
        args = (product_id,)
    sql += " ORDER BY r.bought_on IS NULL, r.bought_on DESC, i.id DESC"
    zeilen = []
    for r in con.execute(sql, args).fetchall():
        z = dict(r)
        z["stueck_cents"] = (z["unit_cents"] if z["unit_cents"] is not None
                             else (z["total_cents"] if z["qty"] == 1 else None))
        zeilen.append(z)
    return zeilen


def letzter_preis(con: sqlite3.Connection, product_id: int) -> dict | None:
    """Die jüngste bestätigte Preisbeobachtung zu einem Produkt, oder `None`."""
    treffer = echte_preise(con, product_id)
    return treffer[0] if treffer else None
