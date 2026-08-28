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
"""
from __future__ import annotations

import sqlite3

from picknick import db, orders
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
              decision: str = OFFEN) -> int:
    """Legt eine Vorschlagszeile an. Entweder Produkt oder Freitext.

    Die Regel „genau eines von beidem" kommt aus `orders.genau_eines()` und
    wird nicht nachgebaut: es ist buchstäblich dieselbe Regel wie beim
    Bestellposten und bei der Zutat, mit demselben CHECK dahinter.

    `rang` heisst in der Datenbank `rank` — die Spalte stammt aus Spec 4 und
    bleibt, wie sie dort steht; nach aussen heisst sie wie überall sonst im
    Projekt, wo der FTS-Rang vorkommt (`catalog.search`).
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
        "                             fallback_term, corrected_from)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_message_id, pid, text, max(1, int(qty)), search_term,
         None if rang is None else float(rang), decision, fallback_term,
         corrected_from))
    con.commit()
    return int(cur.lastrowid)


_VORSCHLAG_SQL = (
    "SELECT s.id, s.chat_message_id, s.product_id, s.free_text, s.qty,"
    "       s.search_term, s.rank AS rang, s.decision, s.decided_at,"
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
    # Wird in `liste()` gefüllt; hier gesetzt, damit eine einzeln geholte
    # Zeile dieselben Felder hat und keine Vorlage über ein fehlendes
    # stolpert.
    v.setdefault("n_alternativen", 0)
    v.setdefault("korrektur", None)
    if v["name"] is None:
        v["name"] = f"Produkt {v['product_id']} — nicht mehr auffindbar"
    return v


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
    # keine weitere Abfrage.
    nach_quelle = {z["corrected_from"]: z
                   for z in zeilen if z["ist_korrektur"]}
    for z in zeilen:
        z["n_alternativen"] = int(anzahl.get(z["id"], 0))
        z["korrektur"] = nach_quelle.get(z["id"])
    return zeilen


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


def verlauf(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Der Chat zu einer Bestellung: Nachrichten, jede mit ihren Vorschlägen.

    Der Chat hängt an der Bestellung und nicht an einer eigenen Sitzung
    (Spec 9: er gehört in den Warenkorb). Damit wandert er beim Abschicken
    mit — und die Entscheidungen bleiben bei dem Einkauf, zu dem sie gehören.
    """
    zeilen = []
    for m in con.execute(
            "SELECT id, order_id, role, content, span_id, created_at"
            "  FROM chat_message WHERE order_id = ? ORDER BY id",
            (order_id,)).fetchall():
        eintrag = dict(m)
        eintrag["vorschlaege"] = liste(con, eintrag["id"])
        zeilen.append(eintrag)
    return zeilen


def entscheiden(con: sqlite3.Connection, suggestion_id: int,
                entscheidung: str) -> dict:
    """`kept` legt in den Korb, `removed` nicht. Gibt den Vorschlag zurück.

    Zweimal „Ja" legt NICHT zweimal ein: die Entscheidung wird vorher
    verglichen. Ohne das erhöht ein doppelter Tipp — auf dem Handy schnell
    passiert — die Menge im Korb.

    Ein „Nein" NACH einem „Ja" ändert das Label und rührt den Korb nicht an.
    Das ist Absicht: `orders.einlegen()` fasst gleiche Zeilen zusammen, die
    Korbzeile kann also längst eine sein, die die Nutzerin selbst aufgestockt
    hat. Sie hier wieder herauszunehmen hiesse, fremde Mengen zu löschen. Im
    Korb steht ein Löschknopf — einen Tipp entfernt und ohne Rätselraten.
    """
    if entscheidung not in db.DECISIONS:
        raise VorschlagFehler(
            f"{entscheidung!r} ist keine Entscheidung. Erlaubt: "
            f"{', '.join(db.DECISIONS)}.")
    v = eine(con, suggestion_id)
    if v["decision"] == entscheidung:
        return v
    if entscheidung == BEHALTEN:
        orders.einlegen(con, product_id=v["product_id"],
                        free_text=v["free_text"], qty=v["qty"])
    con.execute(
        "UPDATE chat_suggestion SET decision = ?, decided_at = ? WHERE id = ?",
        (entscheidung, None if entscheidung == OFFEN else jetzt(),
         suggestion_id))
    con.commit()
    return eine(con, suggestion_id)


def korrektur(con: sqlite3.Connection, suggestion_id: int) -> dict | None:
    """Die Korrekturzeile zu einem Vorschlag, falls es eine gibt."""
    row = con.execute(_VORSCHLAG_SQL + " WHERE s.corrected_from = ?"
                      " ORDER BY s.id LIMIT 1", (suggestion_id,)).fetchone()
    return None if row is None else _auf(row)


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
    Korrektur und gibt sie unverändert zurück. Eine ZWEITE, andere Korrektur
    wird abgewiesen statt still danebengelegt — sonst stünden zwei Zeilen im
    Korb und zwei „das wäre richtig gewesen" am selben Fehlgriff.
    """
    quelle = eine(con, suggestion_id)
    vorhanden = korrektur(con, suggestion_id)
    if vorhanden is not None:
        gleich = (vorhanden["product_id"] == product_id
                  and (vorhanden["free_text"] or "") == (free_text or ""))
        if gleich:
            return vorhanden
        raise VorschlagFehler(
            f"„{quelle['name']}“ wurde schon zu „{vorhanden['name']}“ "
            "korrigiert. Im Korb steht ein Löschknopf.")

    entscheiden(con, suggestion_id, VERWORFEN)
    neu_id = vorschlag(con, quelle["chat_message_id"], product_id=product_id,
                       free_text=free_text, qty=quelle["qty"],
                       search_term=search_term or quelle["search_term"],
                       rang=rang, corrected_from=suggestion_id)
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
    """
    for v in liste(con, chat_message_id):
        if v["decision"] == OFFEN:
            entscheiden(con, v["id"], entscheidung)
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
