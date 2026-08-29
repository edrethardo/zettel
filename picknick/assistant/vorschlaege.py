"""Chatverlauf und Vorschlagsliste — und die Entscheidung, die das Label ist.

**Nichts landet ungefragt im Warenkorb** (Spec 6). Ein Chat-Zug legt Zeilen in
`chat_suggestion` und sonst nirgendwohin. Erst ein Tipp auf „Ja" schiebt eine
Zeile über `orders.einlegen()` in den Korb — durch dieselbe Tür wie jedes
Produkt aus dem Katalog, damit Mengenzusammenfassung und Ladenvorbelegung
genau einmal im Projekt stehen.

**`decision` ist das Eval-Label** (Spec 8.1). Sie bestätigt oder verwirft jede
Zeile einzeln; daraus fällt Ground Truth, ohne dass jemand annotiert. Drei
Werte, und der dritte trägt das Gewicht: `offen` heisst „nie entschieden" und
geht NICHT in die Quote ein — sonst zählte jeder Abbruch als Fehler des
Modells, und die Zahl, um die es im ganzen Projekt geht, wäre systematisch zu
schlecht.

`search_term` und `rank` stehen an der Zeile und nicht bloss im Trace. Sie
sind die Erklärung zur Annotation („woran lag es?") und überleben so auch
einen Phoenix, der gerade nicht lief.

**Seit WB-359 ist „Nein" kein Sackgassenweg mehr.** Die Kandidaten, die die
Suche zu einer Zutat vorgelegt hat, werden aufgehoben (`chat_kandidat`) statt
weggeworfen; ein „Nein" klappt sie auf, und ein Tipp legt eine davon statt
des Vorschlags in den Korb. Diese zweite Zeile ist **keine unabhängige
Entscheidung**: sie trägt `corrected_from` und ist damit als Korrektur
erkennbar. Das ist der Unterschied zwischen „das war falsch" und „das war
falsch UND das wäre richtig gewesen" — für die Evals aus WB-329/WB-330 der
wertvollste Datenpunkt, weil er die richtige Antwort benennt.

Zwei Folgen daraus, die leicht zu übersehen sind:

* **Eine Korrekturzeile zählt nicht in die Trefferquote** (`quote()`). Sie ist
  kein Vorschlag des Modells, sondern die Handbewegung danach; im Zähler
  stünde sie als zusätzlicher Treffer und schönte genau die Zahl, die den
  Fehlgriff messen soll.
* **Gewählt werden kann nur, was vorgelegt wurde.** `korrigieren()` prüft das
  Produkt gegen `chat_kandidat` — dieselbe Regel wie in `plan.choose`, nur
  für die Nutzerin. Was sie sonst noch will, kommt aus dem Katalog oder als
  Freitext (`stattdessen_freitext()`), und dann steht es auch so da.

**Seit WB-361 ist keine Entscheidung mehr endgültig.** Jede Zeile — „Ja",
„Nein" und auch eine Korrektur — lässt sich auf `offen` zurücknehmen. Das ist
Datenqualität und keine Bequemlichkeit: auf dem Handy sitzen die beiden
Knöpfe nebeneinander, und ein Fehltipp verfälscht genau die Zahlen, um die es
in diesem Projekt geht (das Label aus Spec 8.1 und, ab WB-341, das
Vorlieben-Signal). Weil die Annotationen erst beim ABSCHICKEN entstehen
(WB-329), kostet ein Rückweg vorher nichts — er muss nur angeboten werden.

Der Rückweg bringt aber eine Falle mit, die vorher keine war: **der Schutz
gegen den Doppeltipp durfte nicht länger am Vergleich der letzten Entscheidung
hängen.** „Ja, rückgängig, Ja" hätte zweimal eingelegt. Er hängt jetzt an
`chat_suggestion.eingelegt_at` — „war diese Zeile schon einmal im Korb" — und
das ist die Frage, die er die ganze Zeit stellen wollte.

**Seit WB-397 gilt das auch für den Sammelknopf.** „Alles übernehmen" war die
einzige Entscheidung im Shop ohne Rückweg — und ausgerechnet die, die eine
ganze Liste auf einmal entscheidet; elf Zeilen einzeln zurückzunehmen sind elf
Tipps auf einem Telefon. Der Rückweg heisst „Doch nicht alles"
(`sammel_zuruecknehmen`) und nimmt AUSSCHLIESSLICH zurück, was der letzte
Sammelvorgang entschieden hat. Dafür trägt jede so entschiedene Zeile die
Nummer ihres Vorgangs (`sammel_nr`); ein einzelner Tipp löscht sie wieder,
denn ab da gehört die Entscheidung der Nutzerin. Ein Rückweg, der die einzeln
gesetzte Butter mitnähme, wäre derselbe Fehler wie ein Sammelknopf, der
bestehende Entscheidungen überschreibt — nur in die andere Richtung.
"""
from __future__ import annotations

import sqlite3

from picknick import db, mengen, orders
from picknick.orders.bestellung import jetzt

ROLLE_NUTZERIN = "user"
ROLLE_AGENT = "assistant"

#: Wie `chat_suggestion.decision` heisst. Aus `db` geholt, damit es genau eine
#: Wahrheit gibt — der CHECK in der Tabelle ist dieselbe Liste.
OFFEN, BEHALTEN, VERWORFEN = db.DECISIONS


class VorschlagFehler(ValueError):
    """Ein Vorschlag oder eine Entscheidung, die es so nicht gibt."""


def nachricht(con: sqlite3.Connection, order_id: int, rolle: str,
              inhalt: str, span_id: str | None = None) -> int:
    """Schreibt eine Chatzeile und gibt ihre id zurück.

    `span_id` bleibt oft leer und ist trotzdem hier: die Annotationen aus
    Spec 8.1 müssen später den richtigen `chat.turn`-Span treffen, und WB-328
    trägt ihn nach. Ohne Feld gäbe es dann keinen Weg zurück vom Label zum
    Trace.
    """
    cur = con.execute(
        "INSERT INTO chat_message (order_id, role, content, span_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (order_id, rolle, inhalt, span_id, jetzt()))
    con.commit()
    return int(cur.lastrowid)


def span_setzen(con: sqlite3.Connection, chat_message_id: int,
                span_id: str) -> None:
    """Trägt die Span-ID nach — der Haken, an dem WB-328 hängt.

    Getrennt vom Schreiben der Nachricht, weil der Span erst endet, wenn der
    Zug fertig ist, die Nachricht aber schon davor stehen soll.
    """
    con.execute("UPDATE chat_message SET span_id = ? WHERE id = ?",
                (span_id, chat_message_id))
    con.commit()


def vorschlag(con: sqlite3.Connection, chat_message_id: int, *,
              product_id=None, free_text=None, qty: int = 1,
              search_term: str | None = None, rang: float | None = None,
              fallback_term: str | None = None,
              corrected_from: int | None = None,
              menge=None, einheit: str | None = None,
              dish_item=None, decision: str = OFFEN) -> int:
    """Legt eine Vorschlagszeile an. Entweder Produkt oder Freitext.

    Die Regel „genau eines von beidem" kommt aus `orders.genau_eines()` und
    wird nicht nachgebaut: es ist buchstäblich dieselbe Regel wie beim
    Bestellposten und bei der Zutat, mit demselben CHECK dahinter.

    `rang` heisst in der Datenbank `rank` — die Spalte stammt aus Spec 4 und
    bleibt, wie sie dort steht; nach aussen heisst sie wie überall sonst im
    Projekt, wo der FTS-Rang vorkommt (`catalog.search`).

    `menge` und `einheit` sind die BENÖTIGTE Menge aus dem Rezept (WB-369) —
    dieselben zwei Namen wie in `korb.einlegen()`, an das sie beim „Ja"
    weitergereicht werden. `qty` daneben bleibt die Packungszahl. Ohne Menge
    ist alles wie vor WB-369, und das ist der Normalfall: „Klopapier" hat
    keine.

    `dish_item` sagt, ob diese Zeile eine Zutat des Gerichts ist (WB-337):
    `1` ja, `None` nein. Auch hier ist „Klopapier" der Normalfall — es liegt
    im Korb und gehört in kein Rezept. Die dritte Möglichkeit (`0`, „von Hand
    aus dem Entwurf genommen") entsteht nicht hier, sondern erst in
    `assistant.entwurf`.
    """
    pid, text = orders.genau_eines(product_id, free_text, was="Ein Vorschlag")
    if pid is not None and not con.execute(
            "SELECT 1 FROM product WHERE id = ?", (pid,)).fetchone():
        # Kann nur passieren, wenn zwischen Suche und Schreiben ein Crawl das
        # Produkt entfernt hat. Als IntegrityError sähe das aus wie ein Fehler
        # im Shop.
        raise VorschlagFehler(f"Produkt {pid} gibt es nicht.")
    cur = con.execute(
        "INSERT INTO chat_suggestion (chat_message_id, product_id, free_text,"
        "                             qty, search_term, rank, decision,"
        "                             fallback_term, corrected_from,"
        "                             need_amount, need_unit, dish_item)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_message_id, pid, text, max(1, int(qty)), search_term,
         None if rang is None else float(rang), decision, fallback_term,
         corrected_from, None if menge is None else float(menge),
         (einheit or None), None if dish_item is None else int(dish_item)))
    con.commit()
    return int(cur.lastrowid)


_VORSCHLAG_SQL = (
    "SELECT s.id, s.chat_message_id, s.product_id, s.free_text, s.qty,"
    "       s.need_amount, s.need_unit,"
    "       s.search_term, s.rank AS rang, s.decision, s.decided_at,"
    "       s.eingelegt_at, s.zurueckgenommen, s.sammel_nr, s.dish_item,"
    "       s.corrected_from, s.fallback_term,"
    "       coalesce(p.name, s.free_text) AS name,"
    "       p.unit_text, p.price_cents, p.image_path, p.active,"
    # Wovon diese Zeile die Korrektur ist — Name und Begriff des Vorschlags,
    # den sie ersetzt. Ohne den Selbstverbund müsste die Vorlage den
    # ursprünglichen Vorschlag noch einmal nachschlagen, um „statt X"
    # schreiben zu können, und eine Korrektur ohne das „statt" ist keine.
    "       coalesce(op.name, o.free_text) AS statt_name,"
    "       o.search_term AS statt_begriff"
    "  FROM chat_suggestion s LEFT JOIN product p ON p.id = s.product_id"
    "  LEFT JOIN chat_suggestion o ON o.id = s.corrected_from"
    "  LEFT JOIN product op ON op.id = o.product_id"
    # LEFT JOIN und kein `active = 1`: derselbe Grund wie bei den Rezepten —
    # ein Vorschlag, den ein Crawl inzwischen ausgemustert hat, wird markiert
    # und nicht verschwiegen.
)


def _auf(row: sqlite3.Row) -> dict:
    # Derselbe Ableiter wie beim Bestellposten und bei der Rezeptzutat
    # (WB-335): ein Vorschlag ist dieselbe Zeile mit demselben LEFT JOIN.
    v = orders.markiere_katalogstand(dict(row))
    v["offen"] = v["decision"] == OFFEN
    v["behalten"] = v["decision"] == BEHALTEN
    v["ist_korrektur"] = v["corrected_from"] is not None
    # Die Spalte darf NULL sein (siehe `db.NACHGETRAGENE_SPALTEN`); nach
    # aussen ist „nie zurückgenommen" eine 0 und kein None, sonst müsste jede
    # Vorlage und jede Auswertung denselben Fall noch einmal abfangen.
    v["zurueckgenommen"] = int(v["zurueckgenommen"] or 0)
    # Liegt (oder lag) diese Zeile im Korb? Das ist NICHT dasselbe wie
    # `behalten`: nach einem zurückgenommenen „Ja" steht die Korbzeile
    # weiter da, und die Oberfläche muss es sagen können.
    v["im_korb"] = v["eingelegt_at"] is not None
    # Gehört die Zeile zum Rezeptentwurf dieses Zugs (WB-337)? `zum_gericht`
    # heisst „sie ist eine Zutat des Gerichts" und bleibt auch dann wahr,
    # wenn sie aus dem Entwurf genommen wurde — sonst verschwände mit der
    # Zeile auch der Knopf, der sie zurückholt. Ob sie ins Rezept geht, sagt
    # `im_rezept`.
    v["zum_gericht"] = v["dish_item"] is not None
    v["im_rezept"] = v["dish_item"] == 1
    # Was aus der benötigten Menge WÜRDE, wenn diese Zeile jetzt in den Korb
    # ginge (WB-369). Gerechnet und nicht gespeichert: die Packungsgrösse
    # steht am Produkt und kann sich beim nächsten Crawl ändern, und der Satz
    # daneben soll dann auch anders lauten.
    #
    # Es ist eine VORSCHAU auf diese eine Zeile und nicht die Zahl, die
    # hinterher im Korb steht: dort wird über alle Rezepte zusammengezählt,
    # und das kann mehr ergeben. Genau deshalb steht am Korbposten derselbe
    # Satz noch einmal, dann mit der Summe (`orders.korb.rechnung`).
    v["rechnung"] = mengen.rechne(v["need_amount"], v["need_unit"],
                                  v["unit_text"])
    v["mengensatz"] = _mengensatz(v)
    # Was die Zeile als Zahl zeigt: die ausgerechnete Packungszahl, wo es eine
    # gibt, sonst die Zahl an der Zeile. Ein „2 ×" aus dem Modell neben einem
    # ausgerechneten „1 ×" wäre zweimal dieselbe Behauptung mit zwei Zahlen.
    v["packungen"] = v["rechnung"].packungen or v["qty"]
    # Wird in `liste()` gefüllt; hier gesetzt, damit eine einzeln geholte
    # Zeile dieselben Felder hat und keine Vorlage über ein fehlendes
    # stolpert.
    v.setdefault("n_alternativen", 0)
    v.setdefault("korrektur", None)
    # Ob DIESE Zeile am Rückweg „Doch nicht alles" hängt (WB-397). Es ist
    # eine Aussage über den Zug und nicht über die Zeile — welcher
    # Sammelvorgang der jüngste ist, weiss nur, wer alle Zeilen sieht.
    v.setdefault("sammel_rueckweg", False)
    if v["name"] is None:
        v["name"] = f"Produkt {v['product_id']} — nicht mehr auffindbar"
    return v


def _mengensatz(v: dict) -> str | None:
    """Der Satz zur Menge an einer Vorschlagszeile — oder `None`.

    Zwei Fälle, und der zweite ist der Grund für diese Funktion: bei einem
    PRODUKT steht die Packungsgrösse daneben, und `mengen.satz` schreibt die
    ganze Rechnung („500 g gebraucht — 1 × 500 g"). Bei einem FREITEXT gibt es
    kein Produkt und damit keine Packung; der Satz von dort läse sich als
    „die Packungsgrösse steht nicht lesbar am Produkt" und schöbe einer Zeile
    einen Mangel unter, die gar kein Produkt hat.

    Die Menge steht auch am Freitext, statt sie wegzulassen: im Laden ist
    „500 g Rinderknochen" die Auskunft, auf die es ankommt, und sie stammt
    aus dem Rezept.
    """
    if v.get("need_amount") is None:
        return None
    if v.get("product_id") is None:
        return (f"{mengen.schreibe(v['need_amount'], v['need_unit'])} "
                "gebraucht — im Katalog nicht gefunden.")
    return mengen.satz(v["rechnung"], produkt=v.get("name"),
                       unit_text=v.get("unit_text"))


def liste(con: sqlite3.Connection, chat_message_id: int) -> list[dict]:
    """Die Vorschläge einer Chatzeile, in der Reihenfolge des Anlegens.

    Jede Zeile weiss zusätzlich, wie viele Alternativen zu ihr aufgehoben sind
    (`n_alternativen`) und ob sie bereits korrigiert wurde (`korrektur`).
    Beides braucht die Oberfläche für JEDE Zeile — einzeln nachgefragt wären
    das zwei Abfragen je Vorschlag und damit ein Dutzend je Blick in den
    Warenkorb.
    """
    zeilen = [_auf(r) for r in con.execute(
        _VORSCHLAG_SQL + " WHERE s.chat_message_id = ? ORDER BY s.id",
        (chat_message_id,)).fetchall()]

    anzahl = {r["suggestion_id"]: r["n"] for r in con.execute(
        _ALTERNATIVEN_ZAEHLEN, (chat_message_id,)).fetchall()}
    # Die Korrekturen stehen in derselben Nachricht — die Verknüpfung kostet
    # keine weitere Abfrage. Zurückgenommene zählen nicht: dieselbe Regel wie
    # in `korrektur()`, und sie muss dieselbe sein, sonst zeigte die
    # Oberfläche eine Korrektur an, die die Logik längst nicht mehr kennt.
    nach_quelle = {z["corrected_from"]: z
                   for z in zeilen if z["ist_korrektur"] and not z["offen"]}
    # Der jüngste noch rücknehmbare Sammelvorgang (WB-397). Hier und nicht in
    # `_auf()`, weil es eine Aussage über den ganzen Zug ist: eine Zeile
    # allein kann nicht wissen, ob ihre Nummer die grösste ist.
    letzter = _letzter_sammelvorgang(zeilen)
    for z in zeilen:
        z["n_alternativen"] = int(anzahl.get(z["id"], 0))
        z["korrektur"] = nach_quelle.get(z["id"])
        z["sammel_rueckweg"] = (letzter is not None
                                and z["sammel_nr"] == letzter)
    return zeilen


def _letzter_sammelvorgang(zeilen: list[dict]) -> int | None:
    """Die grösste vergebene `sammel_nr` — oder `None`.

    Das ist der Sammelvorgang, den „Doch nicht alles" zurücknimmt. Die
    grösste Nummer ist immer die jüngste: ein neuer Vorgang nimmt `max + 1`,
    und weggenommen wird eine Nummer nur von oben (durch den Rückweg) oder
    zeilenweise, wenn die Nutzerin eine Zeile einzeln antippt.
    """
    return max((z["sammel_nr"] for z in zeilen if z["sammel_nr"] is not None),
               default=None)


#: Wie viele Alternativen zu einer Zeile übrig sind: die aufgehobenen
#: Kandidaten OHNE den vorgeschlagenen selbst (den sieht sie ja schon) und
#: ohne die, die ein Crawl inzwischen ausgemustert hat. Ein ausgemustertes
#: Produkt anzubieten wäre schlimmer als eine kürzere Liste — hier wird neu
#: gewählt, und was hier gewählt wird, soll es im Laden geben.
_ALTERNATIVEN_WO = (
    "  FROM chat_kandidat k"
    "  JOIN chat_suggestion s ON s.id = k.suggestion_id"
    "  JOIN product p ON p.id = k.product_id AND p.active = 1"
    " WHERE k.product_id IS NOT s.product_id"
)

_ALTERNATIVEN_ZAEHLEN = (
    "SELECT k.suggestion_id AS suggestion_id, count(*) AS n"
    + _ALTERNATIVEN_WO
    + "   AND s.chat_message_id = ? GROUP BY k.suggestion_id"
)


def kandidaten_merken(con: sqlite3.Connection, suggestion_id: int,
                      kandidaten: list[dict]) -> int:
    """Hebt die vorgelegten Kandidaten einer Zutat auf (WB-359).

    Sie liegen im Chat-Zug längst vor — `suche_kette()` hat sie gesucht,
    Stufe 3 hat aus ihnen gewählt, der RETRIEVER-Span kennt sie. Bis zu
    diesem Ticket endete ihr Weg dort. Hier werden sie festgehalten, damit
    ein „Nein" sie zeigen kann, **ohne ein zweites Mal zu suchen**: eine zweite
    Suche liefe gegen einen inzwischen veränderten Katalog und zeigte im
    Zweifel etwas anderes, als das Modell vorgelegt bekam — und genau diese
    Liste ist die interessante.

    Der gewählte Kandidat wird mit aufgehoben und nicht übersprungen: die
    Tabelle soll wiedergeben, was VORLAG, nicht was übrig blieb. Beim Anzeigen
    fällt er heraus (`alternativen()`).

    Gibt zurück, wie viele Zeilen entstanden sind.
    """
    n = 0
    for pos, p in enumerate(kandidaten):
        rang = p.get("rang")
        cur = con.execute(
            # OR IGNORE deckt zwei Fälle, die beide keine Ausnahme wert sind:
            # denselben Kandidaten zweimal (die Vereinigung entdoppelt schon,
            # aber die Tabelle bürgt selbst dafür) und ein Produkt, das
            # zwischen Suche und Schreiben aus dem Katalog verschwunden ist —
            # dann fehlt eine Alternative, und der Zug steht trotzdem.
            "INSERT OR IGNORE INTO chat_kandidat"
            " (suggestion_id, product_id, pos, search_term, rank)"
            " VALUES (?, ?, ?, ?, ?)",
            (suggestion_id, int(p["id"]), pos, p.get("via"),
             None if rang is None else float(rang)))
        n += cur.rowcount or 0
    con.commit()
    return n


def alternativen(con: sqlite3.Connection, suggestion_id: int) -> list[dict]:
    """Die aufgehobenen Kandidaten einer Zeile, ohne den vorgeschlagenen.

    **Keine neue Suche.** Das ist die Zusicherung des Tickets: gezeigt wird
    genau das, was Stufe 3 vorgelegt bekam (und darüber hinaus, was die
    Anzeigegrenze zusätzlich aufgehoben hat, siehe `plan.KANDIDATEN_ANZEIGE`).

    Die Reihenfolge ist die der VORLAGE — Kette (WB-340), dann Wortstufe und
    Rang (WB-339) —, nicht der rohe bm25 über die Vereinigung hinweg. Über
    Begriffe hinweg sind bm25-Ränge nicht vergleichbar; nach `rank` sortiert
    stünde der „MIIL Körnige Frischkäse" über der Maishähnchenkeule, weil
    „Körnig" seltener ist als „Mais" (siehe `catalog.search.suche_kette`).
    """
    return [dict(r) for r in con.execute(
        "SELECT p.id, p.name, p.brand, p.unit_text, p.price_cents,"
        "       p.price_per_unit_cents, p.image_path, p.category_l1,"
        "       p.category_l2, p.category_l3,"
        "       k.search_term, k.rank AS rang, k.pos"
        + _ALTERNATIVEN_WO
        + "   AND k.suggestion_id = ? ORDER BY k.pos",
        (suggestion_id,)).fetchall()]


def eine(con: sqlite3.Connection, suggestion_id: int) -> dict:
    row = con.execute(_VORSCHLAG_SQL + " WHERE s.id = ?",
                      (suggestion_id,)).fetchone()
    if row is None:
        raise VorschlagFehler(f"Vorschlag {suggestion_id} gibt es nicht.")
    return _auf(row)


def verlauf(con: sqlite3.Connection, order_id: int,
            ab: int | None = None) -> list[dict]:
    """Der Chat zu einer Bestellung: Nachrichten, jede mit ihren Vorschlägen.

    Der Chat hängt an der Bestellung und nicht an einer eigenen Sitzung
    (Spec 9). Damit wandert er beim Abschicken mit — und die Entscheidungen
    bleiben bei dem Einkauf, zu dem sie gehören. Der eigene Ort aus WB-382
    ist eine eigene Ansicht, kein eigener Besitzer.

    `ab` schneidet vorne ab und rendert nur die Chatzeilen ab dieser id
    (WB-372). **Das ist eine Anzeigegrenze und keine Löschung** — die Zeilen
    davor stehen unangetastet in der Datenbank, und alles, was am Verlauf
    hängt (die Eval-Labels aus WB-329, die Rezeptentwürfe aus WB-337, der
    Zusammenhang, den `chat.turn` liest), sieht ihn weiter ganz. Wer die
    Grenze setzt, ist die Oberfläche und niemand sonst; die Grenze selbst
    kommt aus `verlauf_ab()`.
    """
    bedingung = "" if ab is None else " AND id >= :ab"
    return [_mit_vorschlaegen(con, m) for m in con.execute(
        _CHATZEILE + " WHERE order_id = :order" + bedingung + " ORDER BY id",
        {"order": order_id, "ab": ab}).fetchall()]


_CHATZEILE = ("SELECT id, order_id, role, content, span_id, created_at"
              "  FROM chat_message")


def _mit_vorschlaegen(con: sqlite3.Connection, row: sqlite3.Row) -> dict:
    eintrag = dict(row)
    eintrag["vorschlaege"] = liste(con, eintrag["id"])
    return eintrag


def zug(con: sqlite3.Connection, chat_message_id: int) -> dict | None:
    """EINE Chatzeile mit ihren Vorschlägen — ein Eintrag aus `verlauf()`.

    Denselben Aufbau und denselben Weg, damit ein Zug, der einzeln getauscht
    wird (WB-372), nicht anders aussehen kann als derselbe Zug im Verlauf.
    Genau dafür gibt es diese Funktion überhaupt: das Bruchstück und die
    Vollansicht dürfen nicht auseinanderlaufen — dieselbe Regel wie bei der
    Trefferliste in WB-323.
    """
    row = con.execute(_CHATZEILE + " WHERE id = ?",
                      (chat_message_id,)).fetchone()
    return None if row is None else _mit_vorschlaegen(con, row)


def verlauf_ab(con: sqlite3.Connection, order_id: int,
               zuege: int | None) -> int | None:
    """Wo die letzten `zuege` Züge anfangen. `None` heisst: der ganze Verlauf.

    Gezählt werden ZÜGE und nicht Chatzeilen: ein Zug ist eine Frage und ihre
    Antwort, und ein Schnitt zwischen beiden ergäbe eine Vorschlagsliste ohne
    den Satz, aus dem sie entstanden ist — genau die Angabe, an der sich
    später ablesen lässt, warum etwas vorgeschlagen wurde. Deshalb liegt die
    Grenze immer auf einer Zeile der Nutzerin.

    Gibt `None` zurück, wenn es ohnehin nicht mehr Züge gibt als erlaubt: dann
    ist nichts eingeklappt und die Oberfläche soll auch nicht so tun.
    """
    if zuege is None or zuege <= 0:
        return None
    row = con.execute(
        "SELECT min(id) AS ab FROM ("
        "  SELECT id FROM chat_message WHERE order_id = ? AND role = ?"
        "   ORDER BY id DESC LIMIT ?)",
        (order_id, ROLLE_NUTZERIN, zuege)).fetchone()
    ab = row["ab"] if row else None
    if ab is None:
        return None
    # Nichts abzuschneiden ist kein Abschneiden. Ohne diese Prüfung stünde
    # über einem kurzen Verlauf ein Knopf „0 ältere Züge anzeigen".
    aelteste = con.execute(
        "SELECT min(id) AS erste FROM chat_message WHERE order_id = ?",
        (order_id,)).fetchone()["erste"]
    return None if aelteste is None or ab <= aelteste else ab


def umfang(con: sqlite3.Connection, order_id: int,
           bis: int | None = None) -> dict:
    """Wie viel Verlauf da ist: Chatzeilen, Züge, Vorschläge, Entscheidungen.

    `bis` zählt nur, was VOR dieser Chatzeile liegt — also genau den Teil, den
    die Anzeigegrenze einklappt. Ohne Angabe ist es der ganze Verlauf.

    Die Zahlen stehen in der Oberfläche an zwei Stellen, und beide Male tragen
    sie dieselbe Last: am eingeklappten Teil sagen sie, ob dort noch Arbeit
    wartet („84 Vorschläge, 12 entschieden"), und in der Rückfrage vor dem
    Leeren sagen sie, was verloren ginge. „Ältere Züge" ohne Zahl wäre an
    beiden Stellen eine Beruhigung statt einer Auskunft.
    """
    nur_aeltere = "" if bis is None else " AND id < :bis"
    nur_aeltere_m = "" if bis is None else " AND m.id < :bis"
    zeilen = con.execute(
        "SELECT count(*) AS nachrichten,"
        "       coalesce(sum(CASE WHEN role = :nutzerin THEN 1 ELSE 0 END), 0)"
        "           AS zuege"
        "  FROM chat_message WHERE order_id = :order" + nur_aeltere,
        {"order": order_id, "nutzerin": ROLLE_NUTZERIN, "bis": bis}).fetchone()
    vorschlaege = con.execute(
        "SELECT count(*) AS vorschlaege,"
        "       coalesce(sum(CASE WHEN s.decision <> :offen THEN 1 ELSE 0 END),"
        "                0) AS entschieden"
        "  FROM chat_suggestion s"
        "  JOIN chat_message m ON m.id = s.chat_message_id"
        " WHERE m.order_id = :order" + nur_aeltere_m,
        {"order": order_id, "offen": OFFEN, "bis": bis}).fetchone()
    return {**dict(zeilen), **dict(vorschlaege)}


def leeren(con: sqlite3.Connection, order_id: int) -> dict:
    """Löscht den Chatverlauf einer Bestellung. **Rührt den Korb nicht an.**

    Der einzige Reset war bis WB-372 das Abschicken — und `orders.abschicken()`
    wirft bei leerem Korb. Der gemessene Stand am 2026-08-29 war genau der
    Zustand, aus dem es dadurch keinen Ausweg gab: 0 Posten, 34 Chatzeilen.
    Sie kam da nur heraus, indem sie etwas einlegte und eine Bestellung
    abschickte, die sie nicht wollte.

    **Was hier gelöscht wird, ist nicht wiederherstellbar**, und es ist mehr
    als Text: an den Vorschlägen hängen die Entscheidungen, aus denen beim
    Abschicken die Eval-Labels werden (Spec 8.1, WB-329). Deshalb steht in der
    Oberfläche eine Rückfrage davor, die das ausspricht, und deshalb gibt
    diese Funktion zurück, was sie weggeräumt hat — die Meldung danach soll
    die Zahlen nennen können.

    Gelöscht wird nur `chat_message`; Vorschläge, Kandidaten, Sorten und
    Rezeptentwürfe gehen per `ON DELETE CASCADE` mit. `order_item` hängt an
    der Bestellung und nicht an der Nachricht — der Korb bleibt also stehen,
    und zwar nicht aus Versehen: eine Zeile, die durch ein „Ja" eingelegt
    wurde, ist ab dem „Ja" ein Posten wie jeder andere.
    """
    weg = umfang(con, order_id)
    con.execute("DELETE FROM chat_message WHERE order_id = ?", (order_id,))
    con.commit()
    return weg


def _pruefe_entscheidung(entscheidung: str) -> None:
    if entscheidung not in db.DECISIONS:
        raise VorschlagFehler(
            f"{entscheidung!r} ist keine Entscheidung. Erlaubt: "
            f"{', '.join(db.DECISIONS)}.")


def entscheiden(con: sqlite3.Connection, suggestion_id: int,
                entscheidung: str, *, sammel: int | None = None) -> dict:
    """`kept` legt in den Korb, `removed` nicht. Gibt den Vorschlag zurück.

    **`offen` ist der Rückweg** (WB-361): jede Entscheidung lässt sich
    zurücknehmen, und zwar auf den Zustand VOR dem Tipp — unentschieden, beide
    Knöpfe wieder da. Nicht auf „doch behalten": das wäre eine neue Behauptung
    statt der Rücknahme einer alten. Auf dem Handy sitzen „Ja" und „Nein"
    nebeneinander, und ein Fehltipp verfälscht sonst genau die Daten, um die
    es hier geht (das Eval-Label aus Spec 8.1 und das Vorlieben-Signal).

    **Zweimal „Ja" legt NICHT zweimal ein — und „Ja, rückgängig, Ja" auch
    nicht.** Der Schutz hängt an `eingelegt_at` und ausdrücklich nicht mehr am
    Vergleich mit der letzten Entscheidung: sobald es einen Rückweg gibt, ist
    „steht schon auf kept" kein Schutz mehr, sondern ein Loch — der Umweg über
    `offen` machte aus einem doppelten Tipp zwei Einlagen und aus einer Menge
    zwei. Gefragt ist „war diese Zeile schon einmal im Korb", und das
    beantwortet nur die Spalte.

    Ein „Nein" NACH einem „Ja" ändert das Label und rührt den Korb nicht an —
    und **eine Rücknahme genauso wenig**. Das ist Absicht:
    `orders.einlegen()` fasst gleiche Zeilen zusammen, die Korbzeile kann also
    längst eine sein, die die Nutzerin selbst aufgestockt hat. Sie hier wieder
    herauszunehmen hiesse, fremde Mengen zu löschen. Im Korb steht ein
    Löschknopf — einen Tipp entfernt und ohne Rätselraten. Die Oberfläche sagt
    das nach einer Rücknahme ausdrücklich, statt es zu verschweigen
    (`im_korb`).

    `sammel` trägt die Nummer des Sammelvorgangs, wenn dieser Tipp aus
    `alle_entscheiden()` kommt (WB-397). Ohne sie ist es ein EINZELNER Tipp —
    und der macht die Zeile zu ihrer eigenen: `sammel_nr` fällt auf NULL, und
    „Doch nicht alles" lässt sie danach stehen. Das gilt auch, wenn der Tipp
    am Zustand nichts ändert; wer eine gesammelt verworfene Zeile einzeln
    bestätigt, hat sie bestätigt.
    """
    _pruefe_entscheidung(entscheidung)
    v = eine(con, suggestion_id)
    if v["decision"] == entscheidung:
        if sammel is None and v["sammel_nr"] is not None:
            con.execute("UPDATE chat_suggestion SET sammel_nr = NULL"
                        " WHERE id = ?", (suggestion_id,))
            con.commit()
            return eine(con, suggestion_id)
        return v
    # Der eigentliche Schutz: eingelegt wird, wenn diese Zeile noch NIE im
    # Korb war. Alles andere ist ein Label-Wechsel.
    if entscheidung == BEHALTEN and not v["im_korb"]:
        # **Hier springt die Rechnung aus WB-362 an** (WB-369): mit `menge`
        # zählt `korb.einlegen` je Produkt zusammen und rundet erst danach
        # auf die Packungsgrösse. Ohne Menge legt es wie vorher `qty`
        # Packungen ein — der Weg für alles, was aus keinem Rezept stammt.
        # Die Portionszahl wandert MIT in den Trace (WB-384). Sie ändert am
        # Einlegen nichts — `korb.einlegen` benutzt sie allein für
        # `picknick.servings` —, aber ohne sie bliebe ausgerechnet der Weg
        # ohne die Zahl, die seit diesem Ticket gewählt werden kann. Lokal
        # eingebunden, weil `zugrezept` eine Ebene über dieser Datei sitzt.
        from picknick.assistant import zugrezept
        orders.einlegen(con, product_id=v["product_id"],
                        free_text=v["free_text"], qty=v["qty"],
                        menge=v["need_amount"], einheit=v["need_unit"],
                        begriff=v["search_term"],
                        portionen=zugrezept.gewaehlte_portionen(
                            con, v["chat_message_id"]))
        eingelegt = jetzt()
    else:
        eingelegt = v["eingelegt_at"]
    # Ein zurückgenommener Fehltipp hinterlässt kein Label (die Annotationen
    # entstehen erst beim Abschicken, WB-329) — ohne diesen Zähler sähe
    # später also niemand, wie oft danebengetippt wurde. Er zählt die
    # Rücknahmen, nicht die Entscheidungen: „zweimal umentschieden" ist etwas
    # anderes als „zweimal daneben".
    zurueck = v["zurueckgenommen"] + (1 if entscheidung == OFFEN else 0)
    con.execute(
        "UPDATE chat_suggestion SET decision = ?, decided_at = ?,"
        "       eingelegt_at = ?, zurueckgenommen = ?, sammel_nr = ?"
        " WHERE id = ?",
        (entscheidung, None if entscheidung == OFFEN else jetzt(),
         eingelegt, zurueck, sammel, suggestion_id))
    con.commit()
    return eine(con, suggestion_id)


def korrektur(con: sqlite3.Connection, suggestion_id: int) -> dict | None:
    """Die GELTENDE Korrekturzeile zu einem Vorschlag, falls es eine gibt.

    Eine zurückgenommene Korrektur (`offen`) ist hier keine mehr (WB-361).
    Das ist der ganze Sinn des Rückwegs an dieser Stelle: eine falsche
    Korrektur ist schlimmer als der Fehlgriff, den sie geradezieht — sie
    behauptet „das wäre richtig gewesen". Wer sie zurücknimmt, bekommt die
    Alternativen wieder aufgeklappt und darf eine andere wählen; bliebe die
    zurückgenommene Zeile als Korrektur stehen, versperrte sie genau das.
    """
    row = con.execute(_VORSCHLAG_SQL + " WHERE s.corrected_from = ?"
                      "   AND s.decision <> ? ORDER BY s.id LIMIT 1",
                      (suggestion_id, OFFEN)).fetchone()
    return None if row is None else _auf(row)


def _korrekturzeile(con: sqlite3.Connection, suggestion_id: int,
                    product_id, free_text) -> dict | None:
    """Die Korrekturzeile mit GENAU dieser Wahl — auch eine zurückgenommene.

    Getrennt von `korrektur()`, weil zwei verschiedene Fragen dahinterstehen:
    „gilt hier schon eine Korrektur" (die blockiert) und „gab es diese
    Korrektur schon einmal" (die wird wiederverwendet statt verdoppelt).
    """
    row = con.execute(
        "SELECT id FROM chat_suggestion WHERE corrected_from = ?"
        "   AND product_id IS ? AND coalesce(free_text, '') = ?"
        " ORDER BY id LIMIT 1",
        (suggestion_id, product_id, free_text or "")).fetchone()
    return None if row is None else eine(con, int(row["id"]))


def _statt(con: sqlite3.Connection, suggestion_id: int, *, product_id=None,
           free_text=None, search_term=None, rang=None) -> dict:
    """Legt die Korrekturzeile an — der gemeinsame Kern der beiden Ausgänge.

    Drei Dinge passieren hier zusammen, und keines davon darf ohne die anderen
    passieren:

    1. Der ursprüngliche Vorschlag wird `removed`. Er bleibt stehen, mit
       Produkt, Suchbegriff und Rang — er IST das Eval-Label „so nicht".
    2. Eine zweite Zeile entsteht, `kept`, mit `corrected_from` auf die erste.
       Das ist der Unterschied zwischen einer Korrektur und zwei losen
       Entscheidungen; ohne den Verweis wäre später nicht mehr zu sagen, ob
       korrigiert oder einfach etwas dazugelegt wurde.
    3. Sie geht durch `entscheiden()` in den Korb — dieselbe Tür wie jedes
       „Ja", damit Mengenzusammenfassung und Ladenvorbelegung genau einmal im
       Projekt stehen.

    **Zweimal dieselbe Korrektur legt nicht zweimal ein.** Auf dem Handy ist
    ein Doppeltipp schnell passiert; die zweite Runde findet die vorhandene
    Korrekturzeile und entscheidet sie noch einmal — was dank `eingelegt_at`
    kein zweites Mal einlegt. Das gilt auch über eine Rücknahme hinweg
    (WB-361): „Das", rückgängig, „Das" ergibt eine Korbzeile und nicht zwei.
    Deshalb wird hier nach der Zeile mit DIESER Wahl gesucht und nicht nur
    nach der geltenden Korrektur.

    Eine ZWEITE, andere Korrektur wird abgewiesen statt still danebengelegt —
    sonst stünden zwei Zeilen im Korb und zwei „das wäre richtig gewesen" am
    selben Fehlgriff. Nach einer Rücknahme ist der Platz wieder frei: dann ist
    eine andere Wahl genau der Weg, für den der Rückweg da ist.
    """
    quelle = eine(con, suggestion_id)
    dieselbe = _korrekturzeile(con, suggestion_id, product_id, free_text)
    if dieselbe is not None:
        # Auch eine zurückgenommene wird hier wiederbelebt statt verdoppelt:
        # eine zweite Zeile auf dasselbe Produkt wäre zweimal dasselbe „das
        # wäre richtig gewesen" — und zweimal im Korb.
        # Der Fehlgriff bleibt dabei verworfen: eine Korrektur, deren Quelle
        # offen dasteht, wäre keine Korrektur, sondern eine zweite Meinung.
        entscheiden(con, suggestion_id, VERWORFEN)
        return entscheiden(con, dieselbe["id"], BEHALTEN)
    vorhanden = korrektur(con, suggestion_id)
    if vorhanden is not None:
        raise VorschlagFehler(
            f"„{quelle['name']}“ wurde schon zu „{vorhanden['name']}“ "
            "korrigiert. Im Korb steht ein Löschknopf.")

    entscheiden(con, suggestion_id, VERWORFEN)
    neu_id = vorschlag(con, quelle["chat_message_id"], product_id=product_id,
                       free_text=free_text, qty=quelle["qty"],
                       search_term=search_term or quelle["search_term"],
                       rang=rang, corrected_from=suggestion_id,
                       # Die benötigte Menge gehört der ZUTAT und nicht dem
                       # Produkt (WB-369): wer „Nein" sagt und ein anderes
                       # Hackfleisch wählt, braucht immer noch 500 g. Ohne
                       # diese Zeile verlöre ausgerechnet die Korrektur die
                       # Menge — und im Korb läge eine Packung nach
                       # Bauchgefühl.
                       menge=quelle["need_amount"],
                       einheit=quelle["need_unit"],
                       # Und dasselbe für die Zugehörigkeit zum Gericht
                       # (WB-337): wer „Nein" sagt und ein anderes
                       # Hackfleisch wählt, kocht immer noch dieselbe
                       # Bolognese. Ohne diese Zeile fiele ausgerechnet die
                       # korrigierte Zutat aus dem Rezept — und zwar die,
                       # bei der die Nutzerin am genauesten hingesehen hat.
                       dish_item=quelle["dish_item"])
    return entscheiden(con, neu_id, BEHALTEN)


def korrigieren(con: sqlite3.Connection, suggestion_id: int,
                product_id: int) -> dict:
    """Legt eine der aufgehobenen Alternativen statt des Vorschlags ein.

    **Nur was vorgelegt wurde.** Das Produkt muss in `chat_kandidat` zu genau
    dieser Zeile stehen; eine beliebige ID von aussen wird abgewiesen. Das ist
    dieselbe Regel wie in `plan.choose` — sie gilt hier nicht, weil der
    Nutzerin misstraut würde, sondern weil „Alternative aus der Vorlage"
    eine Aussage über die Herkunft ist: nur so heisst die Korrektur später
    wirklich „aus derselben Liste hätte das Modell das Richtige nehmen
    können". Wer etwas ganz anderes will, nimmt den Katalog oder den Freitext.

    Der Suchbegriff und der Rang wandern von der KANDIDATENZEILE mit, nicht
    vom ursprünglichen Vorschlag: sie sagen, über welchen Begriff der Kette
    dieses Produkt hereinkam, und das ist bei einer Alternative oft ein
    anderer als beim Fehlgriff.
    """
    treffer = [a for a in alternativen(con, suggestion_id)
               if int(a["id"]) == int(product_id)]
    if not treffer:
        raise VorschlagFehler(
            f"Produkt {product_id} stand nicht in der Vorlage zu diesem "
            "Vorschlag — such es im Katalog oder schreib es als Freitext.")
    a = treffer[0]
    return _statt(con, suggestion_id, product_id=int(a["id"]),
                  search_term=a["search_term"], rang=a["rang"])


def stattdessen_freitext(con: sqlite3.Connection, suggestion_id: int,
                         text: str = "") -> dict:
    """„Nichts davon, ich schreibe es selbst" — der Ausgang bei einer Lücke.

    Bei „Sellerie" (2 Kandidaten, beide daneben) ist das nicht der
    Notausgang, sondern der RICHTIGE Ausgang: eine Lücke im Katalog
    verschwindet nicht dadurch, dass man mehr Kandidaten anfragt. Die Zutat als
    Freitext im Korb ist ehrlicher als ein Beinahe-Treffer, den im Laden
    niemand wiedererkennt.

    Ohne eigenen Text steht der Suchbegriff da — meistens genau das Wort, das
    sie ohnehin geschrieben hätte.
    """
    wort = " ".join((text or "").split()) or (
        eine(con, suggestion_id)["search_term"] or "")
    if not wort:
        raise VorschlagFehler("Schreib hin, was du stattdessen brauchst.")
    return _statt(con, suggestion_id, free_text=wort, search_term=wort)


def alle_entscheiden(con: sqlite3.Connection, chat_message_id: int,
                     entscheidung: str) -> list[dict]:
    """„Alles übernehmen" / „Alles verwerfen" für die offenen Zeilen.

    Nur die offenen: eine bereits getroffene Entscheidung wird von einem
    Sammelknopf nicht überschrieben — sonst kippt ein einziger Tipp die Labels
    um, die die Nutzerin einzeln gesetzt hat.

    **`offen` ist auch hier der Rückweg** (WB-397) und führt nach
    `sammel_zuruecknehmen()`. Dieselbe Adresse und kein vierter Wert, genau
    wie bei der einzelnen Zeile in WB-361: es ist keine neue Sache, die man
    mit einem Zug tun kann, sondern die dritte Entscheidung, die es seit
    Spec 8.1 gibt. Wörtlich gelesen täte `offen` hier nichts — offene Zeilen
    auf offen zu setzen ist Leerlauf —, und diese Bedeutung ist frei.

    Jeder Vorgang bekommt eine eigene Nummer, damit der Rückweg weiss, was er
    anfassen darf. War nichts offen, entsteht auch keine: ein Sammelvorgang
    ohne Zeilen wäre ein Rückweg, der nichts zurücknimmt.
    """
    _pruefe_entscheidung(entscheidung)
    if entscheidung == OFFEN:
        return sammel_zuruecknehmen(con, chat_message_id)
    nummer = _naechste_sammelnummer(con, chat_message_id)
    for v in liste(con, chat_message_id):
        if v["decision"] == OFFEN:
            entscheiden(con, v["id"], entscheidung, sammel=nummer)
    return liste(con, chat_message_id)


def _naechste_sammelnummer(con: sqlite3.Connection,
                           chat_message_id: int) -> int:
    """`max + 1` über die Zeilen DIESES Zugs.

    Je Zug gezählt und nicht projektweit: der Rückweg steht am Zug, und
    „welcher Sammelvorgang war hier der letzte" ist die einzige Frage, die je
    gestellt wird. Nummern werden dabei wiederverwendet — nach einer Rücknahme
    ist die grösste wieder frei, und weil immer nur von oben zurückgenommen
    wird, bleibt die grösste trotzdem die jüngste.
    """
    row = con.execute(
        "SELECT coalesce(max(sammel_nr), 0) + 1 AS nr FROM chat_suggestion"
        " WHERE chat_message_id = ?", (chat_message_id,)).fetchone()
    return int(row["nr"])


def sammel_zuruecknehmen(con: sqlite3.Connection,
                         chat_message_id: int) -> list[dict]:
    """„Doch nicht alles" — der letzte Sammelvorgang dieses Zugs zurück.

    **Ausschliesslich, was DIESER Vorgang entschieden hat.** Der Sammelknopf
    rührt nur die offenen Zeilen an, damit ein einziger Tipp nicht die Labels
    umkippt, die die Nutzerin einzeln gesetzt hat — und diese Zusicherung muss
    rückwärts genauso gelten:

        Butter einzeln „Ja"     -> kept   (ihre Entscheidung)
        Spinat einzeln „Ja"     -> kept   (ihre Entscheidung)
        „Alles übernehmen"      -> 9 weitere auf kept
        „Doch nicht alles"      -> NUR diese 9 zurück auf offen

    Ein Rückweg, der Butter und Spinat mitnähme, wäre derselbe Fehler wie ein
    Sammelknopf, der bestehende Entscheidungen überschreibt — nur in die
    andere Richtung. Deshalb hängt er an `sammel_nr` und nicht an
    „alles, was gerade kept ist".

    Der Weg führt Zeile für Zeile durch `entscheiden()` und nicht über ein
    `UPDATE … WHERE sammel_nr = ?`. Das ist der Punkt, an dem der Schutz aus
    WB-361 hängt: `eingelegt_at` bleibt stehen, der Korb wird nicht angerührt,
    und die Rücknahmen werden gezählt — Sammeln, Zurücknehmen, Sammeln legt
    dadurch jede Sache genau einmal ein.

    Gibt es keinen Sammelvorgang, passiert nichts. Ein Doppeltipp auf dem
    Handy soll keine Fehlermeldung ergeben; er hat schlicht nichts mehr zu
    tun. Ein zweiter Tipp, NACHDEM ein neuer Vorgang darüberliegt, nimmt den
    vorletzten zurück — Vorgang für Vorgang rückwärts, so wie es kam.
    """
    zeilen = liste(con, chat_message_id)
    nummer = _letzter_sammelvorgang(zeilen)
    if nummer is None:
        return zeilen
    for v in zeilen:
        if v["sammel_nr"] == nummer:
            entscheiden(con, v["id"], OFFEN)
    return liste(con, chat_message_id)


def quote(con: sqlite3.Connection, chat_message_id: int) -> dict:
    """`mapping_precision` für eine Chatzeile: behalten / entschieden.

    Spec 8.1. Offene Vorschläge stehen im Nenner NICHT — daher `quote = None`,
    solange nichts entschieden wurde. Eine 0.0 an dieser Stelle wäre gelogen:
    sie hiesse „alles falsch", wo „noch nichts gesagt" richtig ist.
    """
    # Korrekturzeilen zählen NICHT mit (WB-359). Sie sind kein Vorschlag des
    # Modells, sondern die Handbewegung danach — als zusätzliches `kept`
    # schönten sie ausgerechnet die Zahl, die den Fehlgriff messen soll: wer
    # korrigiert, hätte die Trefferquote gehoben. Der Fehlgriff selbst steht
    # weiter im Nenner und als `removed` im Zähler-Nichts.
    zeilen = [v for v in liste(con, chat_message_id) if not v["ist_korrektur"]]
    behalten = sum(1 for v in zeilen if v["decision"] == BEHALTEN)
    verworfen = sum(1 for v in zeilen if v["decision"] == VERWORFEN)
    entschieden = behalten + verworfen
    return {"vorgeschlagen": len(zeilen), "behalten": behalten,
            "verworfen": verworfen, "offen": len(zeilen) - entschieden,
            "quote": (behalten / entschieden) if entschieden else None}
