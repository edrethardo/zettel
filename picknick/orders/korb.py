"""Der Warenkorb — die eine Bestellung im Zustand `draft`.

Es existiert genau ein `draft`, gemeinsam für beide Personen (Spec 4).
Getrennte Warenkörbe sind bei zwei Menschen in einem Haushalt keine Funktion,
sondern eine Fehlerquelle: sonst wird Milch doppelt gekauft. Wer eingelegt
hat, wird nicht erfasst.

Durchgesetzt wird das in der Datenbank (`ux_orders_ein_draft` in `db.py`) und
nicht allein hier. Der Grund steht in `warenkorb()`.
"""
from __future__ import annotations

import sqlite3

from picknick import db, mengen, obs
from picknick.obs import labels
from picknick.orders.bestellung import (LeererWarenkorb, UngueltigerPosten,
                                        jetzt, posten, wechsle)

#: Der Laden, wenn nichts anderes bekannt ist. „egal" ist ehrlich: der Katalog
#: ist ladenneutral (Spec 5.1), und eine geratene Vorbelegung auf „rewe" wäre
#: eine Behauptung, die niemand aufgestellt hat.
LADEN_VORGABE = "egal"


def _draft_id(con: sqlite3.Connection) -> int | None:
    row = con.execute(
        "SELECT id FROM orders WHERE state = 'draft' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def warenkorb(con: sqlite3.Connection) -> int:
    """Die id des gemeinsamen Warenkorbs; legt ihn an, wenn es keinen gibt.

    Der naheliegende Aufbau — nachsehen, und wenn nichts da ist, anlegen — hat
    zwischen den beiden Schritten ein Loch: rufen zwei Anfragen gleichzeitig
    auf, sehen beide „keiner da" und legen beide einen an. Danach gibt es zwei
    Warenkörbe, und genau das darf es nie geben.

    Deshalb ist der Zwang ein partieller UNIQUE-Index in der Datenbank, und
    diese Funktion behandelt den verlorenen Wettlauf als Normalfall: schlägt
    der INSERT fehl, hat ein anderer den Korb angelegt — dann wird dessen id
    zurückgegeben. So kann kein Aufruf einen zweiten `draft` erzeugen, auch
    nicht aus einem zweiten Prozess.
    """
    vorhanden = _draft_id(con)
    if vorhanden is not None:
        return vorhanden
    try:
        cur = con.execute(
            "INSERT INTO orders (state, created_at) VALUES ('draft', ?)",
            (jetzt(),))
        con.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        con.rollback()
        vorhanden = _draft_id(con)
        if vorhanden is None:
            # Der Index hat abgelehnt, aber es steht kein draft da: dann war es
            # ein anderer Constraint und das Verschlucken wäre eine Lüge.
            raise
        return vorhanden


def warenkorb_id(con: sqlite3.Connection) -> int | None:
    """Die id des Warenkorbs, ohne einen anzulegen — `None`, wenn keiner steht.

    Das Gegenstück zu `warenkorb()`, aus demselben Grund wie `korb_anzahl()`:
    wer nur nachsieht (der Chatverlauf im Warenkorb, WB-327), soll keine
    Bestellung in die Datenbank schreiben.
    """
    return _draft_id(con)


def korb_anzahl(con: sqlite3.Connection) -> int:
    """Wie viele Zeilen im Warenkorb liegen — ohne einen anzulegen.

    Bewusst nicht über `warenkorb()`: der Kopf jeder Seite zeigt diese Zahl,
    und ein blosser Blick auf den Katalog soll keine Bestellung in die
    Datenbank schreiben.
    """
    row = con.execute(
        "SELECT count(i.id) AS n FROM orders o"
        "  LEFT JOIN order_item i ON i.order_id = o.id"
        " WHERE o.state = 'draft'").fetchone()
    return int(row["n"]) if row else 0


def genau_eines(product_id, free_text,
                was: str = "Ein Bestellposten") -> tuple[int | None, str | None]:
    """Prüft die Regel „entweder Produkt oder Freitext" vor dem INSERT.

    Die Datenbank hat denselben CHECK und ist die letzte Instanz. Hier steht
    er trotzdem, weil ein `IntegrityError` der Nutzerin nichts sagt — und weil
    ein leeres Textfeld ('' oder '   ') sonst als gültiger Freitext durchginge
    und eine namenlose Zeile im Laden erzeugte.

    Öffentlich (und mit `was` für die Anrede), weil `recipes` genau dieselbe
    Regel für `recipe_item` braucht — dieselbe Spaltenform, derselbe CHECK.
    Eine zweite Kopie liefe irgendwann auseinander, und zwar still.
    """
    text = (free_text or "").strip() or None
    try:
        pid = None if product_id in (None, "") else int(product_id)
    except (TypeError, ValueError):
        # Kommt aus einer URL und ist damit beliebig. Ein roher ValueError
        # wäre für den Aufrufer nicht von einem Programmierfehler zu
        # unterscheiden.
        raise UngueltigerPosten(
            f"{product_id!r} ist keine Produkt-id.") from None
    if (pid is None) == (text is None):
        raise UngueltigerPosten(
            f"{was} braucht genau eines von beidem: ein Produkt aus "
            "dem Katalog oder einen Freitext. "
            f"Bekommen: product_id={product_id!r}, free_text={free_text!r}.")
    return pid, text


def vorbelegter_laden(con: sqlite3.Connection, product_id=None,
                      free_text=None) -> str:
    """Der Laden, der beim Einlegen vorgeschlagen wird (Spec 4).

    Die letzte Wahl für dasselbe Produkt; für Freitext-Posten sinngemäss über
    denselben Text. Gibt es keine, dann „egal". Das ersetzt gepflegte Regeln:
    nach zwei Wochen stimmt die Vorauswahl meistens, ohne dass jemand eine
    Zuordnungstabelle führt.

    Gesucht wird über alle Bestellungen, auch den `draft` — die jüngste
    Entscheidung ist die beste Auskunft, unabhängig davon, ob sie schon
    abgeschickt wurde.
    """
    pid, text = genau_eines(product_id, free_text)
    row = con.execute(
        "SELECT store FROM order_item"
        " WHERE product_id IS ? AND free_text IS ?"
        # Absteigend nach id: die zuletzt angelegte Zeile ist die jüngste
        # Wahl. Zeitstempel hat ein Posten nicht.
        " ORDER BY id DESC LIMIT 1", (pid, text)).fetchone()
    return row["store"] if row else LADEN_VORGABE


def gebinde(con: sqlite3.Connection, product_id) -> str | None:
    """Der `unit_text` eines Produkts — die Packungsgrösse als Text.

    `None` für Freitext und für ein Produkt, das es nicht (mehr) gibt. Beides
    heisst dasselbe: es gibt keine Packungsgrösse, gegen die sich rechnen
    liesse.
    """
    if product_id is None:
        return None
    row = con.execute("SELECT unit_text FROM product WHERE id = ?",
                      (product_id,)).fetchone()
    return row["unit_text"] if row else None


def rechnung(con: sqlite3.Connection, item_id: int) -> mengen.Rechnung:
    """Die Rechnung eines Korbpostens: benötigte Menge gegen Packungsgrösse.

    **Immer aus dem GESAMTBEDARF der Zeile**, nie aus einem einzelnen Beitrag.
    Das ist die Reihenfolge aus dem Nachtrag des Tickets: zusammengezählt wird
    in `need_amount`, aufgerundet wird hier, und zwar danach.
    """
    row = con.execute(
        "SELECT need_amount, need_unit, product_id FROM order_item"
        " WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return mengen.Rechnung()
    return mengen.rechne(row["need_amount"], row["need_unit"],
                         gebinde(con, row["product_id"]))


def _neu_rechnen(con: sqlite3.Connection, item_id: int) -> int:
    """Rechnet die Packungszahl einer Zeile neu und schreibt sie.

    Die eine Stelle, an der `order_item.qty` aus einer Menge entsteht — und
    der Grund, warum sie eine eigene Funktion ist: sie muss nach JEDER
    Änderung am Bedarf laufen. Bliebe eine alte Zahl stehen, läge eine
    Packung im Korb, die niemand mehr braucht, und niemand könnte sagen,
    woher sie kommt.

        qty = max(hand_qty, ausgerechnete Packungen, 1)

    Alle drei Glieder sind nötig. `hand_qty`, damit ein von Hand eingelegter
    Posten nicht verschwindet, nur weil ein Rezept mit weniger auskäme. Die
    ausgerechnete Zahl, weil sie der eigentliche Zweck des Tickets ist. Und
    die 1, weil eine Zeile mit Menge 0 eine Lüge im Regal wäre.
    """
    row = con.execute(
        "SELECT hand_qty, qty FROM order_item WHERE id = ?",
        (item_id,)).fetchone()
    hand = int(row["hand_qty"] or 0)
    aus_menge = rechnung(con, item_id).packungen or 0
    neu = max(hand, aus_menge, 1)
    if neu != int(row["qty"]):
        con.execute("UPDATE order_item SET qty = ? WHERE id = ?",
                    (neu, item_id))
    return neu


def _bedarf_span(con: sqlite3.Connection, item_id: int, *,
                 menge, einheit, portionen=None) -> None:
    """Schreibt die Rechnung dieses Postens in einen Span (Spec 7).

    **Was gerechnet wurde, gehört in den Trace** — sonst ist später nicht
    nachvollziehbar, warum zwei Packungen im Korb liegen und nicht eine. Der
    Fall „nicht ausrechenbar" steht ausdrücklich mit drin: er ist die
    interessantere Hälfte, weil dort die Menge unverändert bleibt und die
    Zahl im Korb aus einer Vorgabe stammt statt aus einer Rechnung.

    Nur wenn eine Menge im Spiel ist. Ein Griff ins Regal („+" an der Kachel)
    rechnet nichts aus und bekommt deshalb auch keinen Span, der so aussähe,
    als hätte er es getan.
    """
    if menge is None:
        return
    row = con.execute(
        "SELECT i.qty, i.hand_qty, i.need_amount, i.need_unit, i.product_id,"
        "       p.name, p.unit_text FROM order_item i"
        "  LEFT JOIN product p ON p.id = i.product_id WHERE i.id = ?",
        (item_id,)).fetchone()
    r = mengen.rechne(row["need_amount"], row["need_unit"], row["unit_text"])
    with obs.chain("korb.menge", eingabe=str(row["name"] or "")) as span:
        obs.setze(span, {
            "picknick.item_id": item_id,
            "picknick.product_id": row["product_id"],
            "picknick.servings": portionen,
            # Der EINZELNE Beitrag dieses Einlegens …
            "picknick.need_added": float(menge),
            "picknick.need_added_unit": str(einheit or "") or None,
            # … und die Summe, die daraus wurde. Beide, weil erst der
            # Unterschied zwischen ihnen das Zusammenzählen sichtbar macht.
            "picknick.need_amount": row["need_amount"],
            "picknick.need_unit": row["need_unit"],
            "picknick.pack_text": row["unit_text"],
            "picknick.pack_amount": r.packung,
            "picknick.pack_unit": r.packung_einheit,
            "picknick.hand_qty": int(row["hand_qty"] or 0),
            "picknick.qty": int(row["qty"]),
            # Ausdrücklich als eigenes Feld und nicht bloss als fehlendes
            # `packages`: „nicht ausrechenbar" ist eine Antwort und soll sich
            # in Phoenix filtern lassen.
            "picknick.computable": r.ausrechenbar,
            "picknick.packages": r.packungen,
            "picknick.reason": r.grund,
            "picknick.assumption": r.annahme,
        })
        obs.setze_ausgabe(span, mengen.satz(r, produkt=row["name"],
                                            unit_text=row["unit_text"],
                                            qty=int(row["qty"])) or "")


def einlegen(con: sqlite3.Connection, product_id=None, free_text=None,
             qty: int = 1, store: str | None = None,
             menge=None, einheit=None, portionen=None) -> int:
    """Legt ein Produkt oder einen Freitext in den gemeinsamen Warenkorb.

    Liegt dieselbe Sache schon drin, wird die Zeile ERGÄNZT statt eine zweite
    angelegt. Zweimal „+" an derselben Kachel heisst „zwei davon" und nicht
    „zwei Zeilen, die im Laden zweimal gegriffen werden".

    **`menge` und `einheit` sind der Kern von WB-362.** Sie sagen, wie viel
    GEBRAUCHT wird — 80 g Knoblauch —, während `qty` sagt, wie viele
    PACKUNGEN verlangt sind. Wer eine Menge mitgibt, überlässt die
    Packungszahl dieser Funktion; wer keine mitgibt, verlangt Packungen und
    bekommt sie.

    Damit ergibt sich die Reihenfolge aus dem Nachtrag des Tickets von selbst:

    * Mengen werden in `need_amount` ADDIERT — über Rezepte hinweg, über die
      Produkt-ID, weil die Zeile über die Produkt-ID gefunden wird. Zwei
      Rezepte mit je 40 g stehen danach als 80 g da.
    * Aufgerundet wird erst hinterher, in `_neu_rechnen`, aus der Summe.
      Deshalb ergeben zwei mal 40 g EINE Packung à 100 g und nicht zwei.

    **Was sich nicht ausrechnen lässt, zählt als Packung.** Passt die Einheit
    des Bedarfs nicht zur Packungsgrösse („2 Stangen" gegen „ca. 500 g"), wird
    nicht geraten: die mitgegebene Packungszahl wird wie ein Handposten
    behandelt, die Menge bleibt trotzdem stehen, und die Oberfläche kann
    sagen, warum. Das ist Regel 4 des Tickets, und es hält zugleich das
    Verhalten von vor diesem Ticket für alles, was keine Menge hat.

    **Freitext bekommt keine Menge.** Ein Posten ohne Produkt hat keine
    Packungsgrösse, gegen die sich rechnen liesse — er bleibt eine Zeile mit
    einer Stückzahl, und das ist richtig so (Ticket: „Freitext-Posten lassen
    sich nicht zusammenzählen").

    Gibt die id des Postens zurück.
    """
    pid, text = genau_eines(product_id, free_text)
    if pid is not None and not con.execute(
            "SELECT 1 FROM product WHERE id = ?", (pid,)).fetchone():
        # Sonst antwortet der Fremdschlüssel mit einem IntegrityError, der
        # nach einem Fehler im Shop aussieht statt nach einer id, die es nicht
        # (mehr) gibt — ein Katalog-Lauf kann Produkte inaktiv setzen, und ein
        # altes Kachel-Formular im Browser zeigt danach ins Leere.
        raise UngueltigerPosten(f"Produkt {pid} gibt es nicht.")
    packungen = max(1, int(qty))
    if pid is None:
        menge, einheit = None, None
    laden = store if store in db.STORES else vorbelegter_laden(con, pid, text)
    korb = warenkorb(con)

    vorhanden = con.execute(
        "SELECT id, qty, hand_qty, need_amount, need_unit FROM order_item"
        " WHERE order_id = ? AND product_id IS ? AND free_text IS ?",
        (korb, pid, text)).fetchone()
    if vorhanden is None:
        cur = con.execute(
            "INSERT INTO order_item (order_id, product_id, free_text, qty,"
            "                        hand_qty, store)"
            " VALUES (?, ?, ?, ?, 0, ?)", (korb, pid, text, packungen, laden))
        item_id = int(cur.lastrowid)
        alt_menge, alt_einheit, hand = None, None, 0
    else:
        item_id = int(vorhanden["id"])
        alt_menge = vorhanden["need_amount"]
        alt_einheit = vorhanden["need_unit"]
        hand = int(vorhanden["hand_qty"] or 0)

    if menge is not None:
        summe = mengen.summiere(alt_menge, alt_einheit, menge, einheit)
        if summe is None:
            # Zwei Bedarfe, die sich nicht zusammenzählen lassen — „4 Stangen"
            # und „200 g" am selben Produkt. Addiert wird nicht: eine Zahl
            # aus zwei Einheiten wäre schlimmer als keine. Der ältere Bedarf
            # bleibt stehen, der neue zählt als Packung.
            menge = None
        else:
            con.execute(
                "UPDATE order_item SET need_amount = ?, need_unit = ?"
                " WHERE id = ?", (summe[0], summe[1], item_id))

    if menge is None or not rechnung(con, item_id).ausrechenbar:
        # Ohne Menge, oder mit einer, die zur Packung nicht passt: dann ist
        # die mitgegebene Packungszahl das Beste, was dasteht.
        hand += packungen
        con.execute("UPDATE order_item SET hand_qty = ? WHERE id = ?",
                    (hand, item_id))

    _neu_rechnen(con, item_id)
    con.commit()
    _bedarf_span(con, item_id, menge=menge, einheit=einheit,
                 portionen=portionen)
    return item_id


def _posten_im_draft(con: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    """Holt einen Posten und stellt sicher, dass er im Warenkorb liegt.

    An einer abgeschickten Bestellung wird nichts mehr geändert: sie ist die
    Abmachung, nach der er im Laden steht. Wer sie ändern will, macht eine
    neue.
    """
    row = con.execute(
        "SELECT i.id, i.qty, i.store, o.state FROM order_item i"
        "  JOIN orders o ON o.id = i.order_id WHERE i.id = ?",
        (item_id,)).fetchone()
    if row is None:
        raise UngueltigerPosten(f"Bestellposten {item_id} gibt es nicht.")
    if row["state"] != "draft":
        raise UngueltigerPosten(
            f"Bestellposten {item_id} gehört zu einer abgeschickten Bestellung "
            f"({row['state']}) und wird nicht mehr geändert.")
    return row


def menge_setzen(con: sqlite3.Connection, item_id: int, qty: int) -> int:
    """Setzt die Packungszahl einer Zeile von Hand. Unter 1 entfernt sie.

    Der Minus-Knopf muss eine Zeile auch loswerden können, ohne dass man ihn
    erst gegen den Löschknopf tauscht; und eine Zeile mit Menge 0 wäre eine
    Lüge im Regal. Gibt die neue Menge zurück, 0 für „ist weg".

    **Der Mensch hat hier das letzte Wort, auch gegen die Rechnung** (WB-362).
    Die gesetzte Zahl gilt, selbst wenn der Bedarf zwei Packungen verlangt und
    sie eine tippt: ein Minus-Knopf, der nichts tut, weil eine Rechnung
    dagegensteht, ist ein kaputter Knopf. Die benötigte Menge bleibt trotzdem
    stehen — sie ist eine Tatsache über das Rezept und keine über den Korb,
    und die Oberfläche sagt beides („1000 ml gebraucht — 2 × 500 g. Im Korb
    liegt 1.").

    Die Zahl wird als `hand_qty` gemerkt und ist damit von da an die
    Untergrenze: legt jemand später ein Rezept dazu, das mehr braucht, steigt
    sie wieder — aber unter das, was ausdrücklich verlangt wurde, fällt sie
    nicht.
    """
    _posten_im_draft(con, item_id)
    menge = int(qty)
    if menge < 1:
        entfernen(con, item_id)
        return 0
    con.execute("UPDATE order_item SET qty = ?, hand_qty = ? WHERE id = ?",
                (menge, menge, item_id))
    con.commit()
    return menge


def laden_setzen(con: sqlite3.Connection, item_id: int, store: str) -> str:
    """Setzt den Laden einer Zeile (rewe / lidl / egal)."""
    _posten_im_draft(con, item_id)
    if store not in db.STORES:
        raise UngueltigerPosten(
            f"{store!r} ist kein Laden. Erlaubt: {', '.join(db.STORES)}.")
    con.execute("UPDATE order_item SET store = ? WHERE id = ?", (store, item_id))
    con.commit()
    return store


def entfernen(con: sqlite3.Connection, item_id: int) -> None:
    """Nimmt eine Zeile aus dem Warenkorb."""
    _posten_im_draft(con, item_id)
    con.execute("DELETE FROM order_item WHERE id = ?", (item_id,))
    con.commit()


def inhalt(con: sqlite3.Connection) -> list[dict]:
    """Die Zeilen des Warenkorbs — leer, solange keiner angelegt wurde."""
    korb = _draft_id(con)
    return posten(con, korb) if korb is not None else []


def abschicken(con: sqlite3.Connection, note: str | None = None) -> dict:
    """`draft -> offen`, mit `submitted_at`. Gibt die Bestellung zurück.

    Danach gibt es keinen `draft` mehr; der nächste `warenkorb()`-Aufruf legt
    einen neuen an. Genau das ist mit „kein Kopieren beim Abschicken" gemeint
    (Spec 4): dieselbe Zeile wechselt den Zustand, die Posten bleiben liegen,
    wo sie sind.

    **Hier fällt das Eval-Label an** (Spec 8.1, WB-329): die Entscheidungen
    über die Chat-Vorschläge dieser Bestellung gehen als Annotationen auf den
    `chat.turn`-Span. Erst jetzt und nicht beim Tippen — bis zum Abschicken
    kann sie ihre Meinung ändern. `obs.labels.schreiben()` wirft nie und
    wartet nicht auf Phoenix; siehe dort.
    """
    korb = _draft_id(con)
    if korb is None or not posten(con, korb):
        raise LeererWarenkorb(
            "Der Warenkorb ist leer — eine Bestellung ohne Posten schickt "
            "niemanden in den Laden.")
    felder = {"submitted_at": jetzt()}
    if note is not None and note.strip():
        felder["note"] = note.strip()
    bestellung = wechsle(con, korb, "offen", **felder)
    # Nach dem Zustandswechsel: was hier auch schiefgeht, die Bestellung ist
    # abgeschickt. Umgekehrt wäre eine Annotation über eine Bestellung
    # geschrieben, die es dann doch nicht gab.
    labels.schreiben(con, korb)
    return bestellung
