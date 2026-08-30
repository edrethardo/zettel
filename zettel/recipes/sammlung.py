"""Die Rezeptsammlung: anlegen, bearbeiten, löschen, Zutaten pflegen.

Ein Rezept ist eine Liste von Zutaten, und eine Zutat ist entweder ein Produkt
aus dem Katalog oder ein Freitext — genau wie ein Bestellposten (Spec 4). Der
CHECK in `recipe_item` erzwingt das in der Datenbank; hier steht dieselbe Regel
noch einmal davor, damit die Nutzerin einen Satz zu lesen bekommt und keinen
`IntegrityError`.

**Der Punkt, an dem diese Datei sich entscheidet:** ein Rezept überlebt den
Katalog. Verschwundene Produkte werden `active = 0` und nie gelöscht (Spec 5.3),
und ein Rezept von letztem Herbst zeigt irgendwann auf so ein Produkt. Deshalb
wird hier NIRGENDS nach `active = 1` gefiltert. Eine Zutat, die aus dem Katalog
gefallen ist, bleibt eine Zutat des Rezepts — sie wird nur als solche markiert
(`nicht_im_katalog`). Eine stillschweigend weggelassene Zutat wäre der
schlimmste Ausgang: dann steht jemand im Laden und weiss nicht, dass etwas
fehlt.
"""
from __future__ import annotations

import sqlite3

from zettel import mengen
from zettel.orders import UngueltigerPosten
from zettel.orders import korb
from zettel.orders.bestellung import markiere_katalogstand

#: Sentinel für `aendern()`: „dieses Feld wurde nicht mitgegeben". Nötig, weil
#: `None` dort eine gültige Eingabe ist (Portionen wieder leeren).
_UNBERUEHRT = object()


class RezeptFehler(RuntimeError):
    """Etwas an einem Rezept ist nicht so, wie es sein müsste."""


class LeeresRezept(RezeptFehler):
    """Ein Rezept ohne Zutaten, dort wo Zutaten gebraucht werden."""


def _name(name) -> str:
    """Rezeptnamen prüfen. Ein namenloses Rezept findet später niemand wieder."""
    sauber = (name or "").strip()
    if not sauber:
        raise RezeptFehler(
            "Ein Rezept braucht einen Namen — eine namenlose Zeile in der Liste "
            "sucht später niemand mehr heraus.")
    return sauber


def _servings(wert):
    """`'4'` -> 4, leer -> None, Unsinn -> None.

    **Seit WB-362 ist die Portionszahl eine Rechengrösse.** Bis WB-325 stand
    hier das Gegenteil — „Portionen sind eine Notiz für Menschen" —, und das
    war damals richtig: es gab keine Mengenangaben, aus denen sich hätte
    rechnen lassen. WB-338 hat sie gebracht (Chefkoch liefert `servings` und
    je Zutat `amount` und `unit`), und damit wurde aus der Notiz die Zahl, auf
    die `mengen.faktor` die Zutatenmengen bezieht.

    Was sich dadurch NICHT ändert: ein verrutschtes Zeichen darf weiterhin
    nicht das Speichern verhindern. Unsinn wird zu `None`, und ein Rezept ohne
    Portionszahl skaliert eben nicht — es bleibt ein Rezept.
    """
    if wert is None or str(wert).strip() == "":
        return None
    try:
        zahl = int(str(wert).strip())
    except (TypeError, ValueError):
        return None
    return zahl if zahl > 0 else None


def _note(wert):
    text = (wert or "").strip()
    return text or None


def anlegen(con: sqlite3.Connection, name: str, servings=None,
            note: str | None = None, zutaten=None) -> int:
    """Legt ein Rezept an und gibt seine id zurück.

    `zutaten` ist eine Folge von Wörterbüchern mit `product_id` / `free_text`
    und optional `qty` — der Weg, auf dem „daraus ein Rezept machen" und die
    Tests ein fertiges Rezept in einem Zug erzeugen. Scheitert eine Zutat, ist
    auch das Rezept nicht angelegt: ein halbes Rezept ist schlechter als
    keines, weil es aussieht, als wäre es vollständig.

    Dafür werden alle Zutaten ERST geprüft und dann geschrieben. Ein
    `rollback` täte es nicht: `zutat_hinzufuegen()` committet jede Zeile
    einzeln (der Warenkorb daneben soll nicht in einer offenen Transaktion
    hängen), und was committet ist, holt kein rollback zurück.
    """
    sauber = _name(name)
    geprueft = []
    for z in (zutaten or []):
        pid, text = korb.genau_eines(z.get("product_id"), z.get("free_text"),
                                     was="Eine Zutat")
        geprueft.append((pid, text, max(1, int(z.get("qty") or 1)),
                         z.get("amount"), z.get("unit")))

    cur = con.execute(
        "INSERT INTO recipe (name, servings, note) VALUES (?, ?, ?)",
        (sauber, _servings(servings), _note(note)))
    recipe_id = int(cur.lastrowid)
    con.commit()
    try:
        for pid, text, menge, bedarf, einheit in geprueft:
            zutat_hinzufuegen(con, recipe_id, product_id=pid, free_text=text,
                              qty=menge, amount=bedarf, unit=einheit)
    except Exception:
        # Bleibt für den Fall, dass doch etwas durchrutscht — etwa eine
        # Produkt-id, die zwischen Prüfung und INSERT verschwindet.
        con.execute("DELETE FROM recipe WHERE id = ?", (recipe_id,))
        con.commit()
        raise
    return recipe_id


def aendern(con: sqlite3.Connection, recipe_id: int, name=None,
            servings=_UNBERUEHRT, note=_UNBERUEHRT) -> dict:
    """Ändert Kopfdaten eines Rezepts. Nicht mitgegebene Felder bleiben stehen.

    Die Zutaten haben ihre eigenen Funktionen — ein „alles auf einmal
    speichern" müsste die Liste vergleichen und könnte dabei eine Zeile
    verlieren, die gerade jemand anderes hinzugefügt hat.

    **Ausser bei der Portionszahl** (WB-384). Sie ist seit WB-362 keine Notiz
    mehr, sondern die Grösse, auf die sich JEDE Menge dieses Rezepts bezieht:
    `recipe_ingredient.amount` (die Liste der Quelle) und `recipe_item.amount`
    (die verknüpften Produkte, ausdrücklich „bei DER Portionszahl, die in
    `recipe.servings` steht"). Sie zu ändern, ohne die Mengen mitzurechnen,
    ist deshalb nicht bloss eine schiefe Überschrift — es ändert still die
    Bedeutung jeder künftigen Einkaufsrechnung.

    Gemessen vor dem Ticket: `servings` von 4 auf 8, die Zutaten unverändert
    bei 500,0 g Hackfleisch, und darüber las die Seite „Zutaten laut Rezept
    (für 8 Portionen)". Das ist eine Falschaussage und keine Unschärfe.

    Der andere Weg wäre gewesen, `servings` als reine Angabe der Quelle zu
    behandeln und die Überschrift umzuformulieren. Er scheidet aus, weil das
    Feld dann WEITER die Rechengrösse wäre: eine geänderte Zahl verstellte
    jeden Einkauf, nur ohne dass es irgendwo dastünde. Der Preis des
    gewählten Wegs steht dafür hier: die Zutatenliste ist danach nicht mehr
    Zeichen für Zeichen die der Quelle. Deshalb sagt der Bericht, dass
    gerechnet wurde, und der nächste Abruf des Rezepts setzt beides — Mengen
    UND Portionszahl — gemeinsam auf die Quelle zurück
    (`gerichte.speicher.merken`).

    Der Bericht hängt als `umgerechnet` am zurückgegebenen Rezept: `None`,
    wo nichts zu rechnen war.
    """
    vorher = dict(_muss_geben(con, recipe_id))
    felder, werte = [], []
    if name is not None:
        felder.append("name = ?")
        werte.append(_name(name))
    neu_servings = _UNBERUEHRT
    if servings is not _UNBERUEHRT:
        neu_servings = _servings(servings)
        felder.append("servings = ?")
        werte.append(neu_servings)
    if note is not _UNBERUEHRT:
        felder.append("note = ?")
        werte.append(_note(note))
    if felder:
        con.execute(f"UPDATE recipe SET {', '.join(felder)} WHERE id = ?",
                    (*werte, recipe_id))
        con.commit()
    bericht = _mengen_umrechnen(con, recipe_id, vorher["servings"],
                                neu_servings)
    return {**rezept(con, recipe_id), "umgerechnet": bericht}


def _mengen_umrechnen(con: sqlite3.Connection, recipe_id: int, von,
                      auf) -> dict | None:
    """Rechnet alle Mengen eines Rezepts auf eine andere Portionszahl um.

    Gerechnet wird mit `mengen.skaliere` und Zeile für Zeile, nicht mit einem
    `amount * ?` in SQL: `zettel.mengen` ist die eine Stelle, an der eine
    Menge skaliert wird (WB-362), und ein zweiter Rechenausdruck hier wäre
    der Anfang einer zweiten Mengenrechnung. Ein Rezept hat zwei Dutzend
    Zeilen — der Weg über Python kostet nichts.

    `None`, wo nichts zu rechnen war. **Und das ist der halbe Punkt des
    Tickets**: fehlt eine der beiden Zahlen, gibt es keinen Faktor, und dann
    bleiben die Mengen, wie sie sind (Regel 4). Wer an einem Rezept ohne
    Portionszahl eine 4 einträgt, sagt damit „diese Mengen sind für 4" — er
    rechnet nichts um, denn es gibt nichts, wovon aus.
    """
    if auf is _UNBERUEHRT or not von or not auf or von == auf:
        return None
    faktor = mengen.faktor(von, auf)
    # Beide Mengenspalten des Rezepts, und beide meinen dieselbe Portionszahl:
    # `recipe_ingredient.amount` ist die Liste, wie die Quelle sie schreibt,
    # `recipe_item.amount` die Menge am verknüpften Produkt. Eine von beiden
    # stehenzulassen hiesse, die Lüge aus dem Ticket auf die andere Hälfte zu
    # verschieben.
    n = {"recipe_ingredient": 0, "recipe_item": 0}
    for tabelle in ("recipe_ingredient", "recipe_item"):
        for row in con.execute(
                f"SELECT id, amount FROM {tabelle}"
                f" WHERE recipe_id = ? AND amount IS NOT NULL",
                (recipe_id,)).fetchall():
            con.execute(f"UPDATE {tabelle} SET amount = ? WHERE id = ?",
                        (mengen.skaliere(row["amount"], von, auf), row["id"]))
            n[tabelle] += 1
    con.commit()
    return {"von": von, "auf": auf, "faktor": faktor,
            "n_zutaten": n["recipe_ingredient"],
            "n_produkte": n["recipe_item"],
            "meldung": _umrechnungssatz(von, auf, n)}


def _umrechnungssatz(von: int, auf: int, n: dict) -> str:
    """Was gerade mit den Mengen geschehen ist, als ein Satz.

    Steht hier und nicht in der Vorlage, damit die Tests denselben Satz
    prüfen, den die Nutzerin liest — dieselbe Begründung wie bei
    `recipes.uebernahme._meldung`.
    """
    wort = "Portion" if auf == 1 else "Portionen"
    teile = [f"Das Rezept gilt jetzt für {auf} statt {von} {wort}."]
    gerechnet = []
    if n["recipe_ingredient"]:
        gerechnet.append(f"{n['recipe_ingredient']} Zutaten laut Rezept")
    if n["recipe_item"]:
        gerechnet.append(f"{n['recipe_item']} verknüpfte Produkte")
    if gerechnet:
        teile.append("Mitgerechnet: " + " und ".join(gerechnet) + ".")
    else:
        # Ein Rezept ohne eine einzige Mengenangabe. Die Portionszahl steht
        # danach trotzdem anders da, und zu schweigen hiesse, eine Rechnung
        # zu behaupten, die es nicht gab.
        teile.append("An keiner Zeile steht eine Menge — es war nichts "
                     "umzurechnen.")
    return " ".join(teile)


def loeschen(con: sqlite3.Connection, recipe_id: int) -> None:
    """Löscht ein Rezept samt Zutaten.

    Die Zutaten gehen per `ON DELETE CASCADE` mit. Bestellungen, die einmal
    aus diesem Rezept entstanden sind, bleiben unberührt: sie sind eigene
    Zeilen in `order_item` und keine Verweise hierher — was eingekauft wurde,
    ändert sich nicht dadurch, dass jemand ein Rezept wegwirft.
    """
    _muss_geben(con, recipe_id)
    con.execute("DELETE FROM recipe WHERE id = ?", (recipe_id,))
    con.commit()


#: Die Kopfdaten eines Rezepts. Seit WB-338 gehört die Zubereitung dazu —
#: mit Zeiten, Schwierigkeit und Herkunft. Ein selbst angelegtes Rezept hat
#: davon nichts, und das ist kein Sonderfall: alle diese Spalten sind NULL,
#: die Ansicht lässt den Abschnitt dann weg, und `note` bleibt die Stelle für
#: eigene Notizen.
_KOPF_SPALTEN = (
    "id, name, servings, note, instructions, prep_minutes, cook_minutes,"
    " rest_minutes, difficulty, source, source_id, source_url, source_title,"
    " source_rating, source_votes, fetched_at")


def _muss_geben(con: sqlite3.Connection, recipe_id: int) -> sqlite3.Row:
    row = con.execute(f"SELECT {_KOPF_SPALTEN} FROM recipe WHERE id = ?",
                      (recipe_id,)).fetchone()
    if row is None:
        raise RezeptFehler(f"Rezept {recipe_id} gibt es nicht.")
    return row


def _zutat_aufbereiten(row: sqlite3.Row) -> dict:
    """Eine Zeile aus `recipe_item` in das, was die Oberfläche braucht.

    `nicht_im_katalog` entsteht nicht hier, sondern in
    `orders.markiere_katalogstand()` — es ist buchstäblich dieselbe Regel wie
    beim Bestellposten, und zwei Kopien liefen irgendwann auseinander (WB-335).
    Für Freitext ist es immer falsch: Freitext war nie im Katalog und
    behauptet das auch nicht.
    """
    z = markiere_katalogstand(dict(row))
    # Die benötigte Menge als Satz — „3 Stange" und nicht „3 stange"
    # (WB-362). Die Grundeinheit wird gefaltet gespeichert, damit „Zehe(n)"
    # und „Zehen" dieselbe sind; zum Lesen taugt das nicht, und die Vorlage
    # soll die Regel nicht ein zweites Mal kennen müssen.
    z["mengentext"] = mengen.schreibe(z.get("amount"), z.get("unit"))
    # Dieselbe Einheit ohne die Zahl — für das Feld, in dem die Menge
    # geändert wird: dort steht die Zahl schon im Eingabefeld.
    z["einheit_text"] = mengen.schreibe(1, z.get("unit")).split(" ", 1)[-1]
    # Dasselbe noch einmal als FELDINHALT — und leer, wo keine Einheit
    # gespeichert ist (WB-375). `einheit_text` taugt dafür nicht: es schreibt
    # „Stk", auch wenn in der Spalte NULL steht. Ein vorbelegtes Feld ist aber
    # keine Anzeige, sondern der Wert, der beim nächsten Abschicken
    # zurückkommt — es darf nichts behaupten, was nicht in der Datenbank
    # steht.
    z["einheit_feld"] = z["einheit_text"] if z.get("unit") else ""
    if z["name"] is None:
        # Kann nur passieren, wenn eine Produktzeile trotz Fremdschlüssel
        # verschwunden ist. Dann ist der Name weg — aber die Zeile bleibt
        # sichtbar, weil eine verschwundene Zutat schlimmer ist als eine
        # namenlose.
        z["name"] = f"Produkt {z['product_id']} — nicht mehr auffindbar"
    return z


_ZUTAT_SQL = (
    "SELECT ri.id, ri.recipe_id, ri.product_id, ri.free_text, ri.qty,"
    "       ri.amount, ri.unit,"
    "       coalesce(p.name, ri.free_text) AS name,"
    "       p.unit_text, p.price_cents, p.image_path, p.active"
    "  FROM recipe_item ri LEFT JOIN product p ON p.id = ri.product_id"
    # LEFT JOIN, nicht INNER, und ohne `active = 1`: beides würde genau die
    # Zutat verschlucken, um derentwillen dieses Ticket geschrieben wurde.
    " WHERE ri.recipe_id = ?"
    " ORDER BY ri.id")


def zutaten(con: sqlite3.Connection, recipe_id: int) -> list[dict]:
    """Alle Zutaten eines Rezepts, in der Reihenfolge des Anlegens."""
    return [_zutat_aufbereiten(r)
            for r in con.execute(_ZUTAT_SQL, (recipe_id,)).fetchall()]


def rezept(con: sqlite3.Connection, recipe_id: int) -> dict:
    """Ein Rezept mit seinen Zutaten. Wirft, wenn es das Rezept nicht gibt.

    Seit WB-338 kommt zweierlei dazu, das nichts mit dem Einkaufen zu tun hat
    und alles mit dem Kochen:

    * `zubereitung` — die Schritte, aus dem gespeicherten Text getrennt.
      Ein Absatz mit 3.924 Zeichen als eine Wand ist auf einem Telefon am
      Herd unbrauchbar; die Struktur steht bereits im Text und wird hier
      nur nicht zusammengezogen.
    * `rezeptzutaten` — die Zutatenliste, wie die Quelle sie schreibt („500
      ml Tomaten, passierte"). Das ist NICHT `zutaten`: dort stehen die
      Produkte, die dafür gekauft werden.

    Beides ist leer, wenn niemand ein Rezept geholt hat — die Ansicht lässt
    die Abschnitte dann weg, statt leere Überschriften zu zeigen.
    """
    from zettel.gerichte import chefkoch, speicher

    kopf = dict(_muss_geben(con, recipe_id))
    kopf["zutaten"] = zutaten(con, recipe_id)
    kopf["n_zutaten"] = len(kopf["zutaten"])
    kopf["n_ausgemustert"] = sum(1 for z in kopf["zutaten"]
                                 if z["nicht_im_katalog"])
    kopf["zubereitung"] = chefkoch.schritte(kopf.get("instructions"))
    kopf["rezeptzutaten"] = speicher.zutaten(con, recipe_id)
    return kopf


def rezepte(con: sqlite3.Connection) -> list[dict]:
    """Die Rezeptliste, alphabetisch, je mit Zahl der Zutaten.

    `n_ausgemustert` steht schon in der Liste und nicht erst in der Ansicht:
    wer ein Rezept in den Korb legen will, soll vorher sehen, dass eine Zutat
    nicht mehr im Katalog ist — nicht erst im Laden.

    **Zwei Zahlen, nicht eine (WB-375).** `n_zutaten` sind die verknüpften
    PRODUKTE, `n_rezeptzutaten` die Zutatenliste, wie das Rezept sie schreibt.
    Bei einem geholten Rezept (WB-338) ist die erste 0 und die zweite 23 — die
    Liste behauptete dann „0 Zutaten" von einem Rezept, das eine volle Seite
    davon hat. Als Unterabfrage und nicht als zweiter LEFT JOIN: zwei Joins auf
    dasselbe Rezept multiplizieren sich, und `count(ri.id)` zählte danach 23×
    zu viel.
    """
    rows = con.execute(
        "SELECT r.id, r.name, r.servings, r.note, r.source, r.source_url,"
        "       r.prep_minutes, r.cook_minutes,"
        "       (r.instructions IS NOT NULL) AS hat_zubereitung,"
        "       (SELECT count(*) FROM recipe_ingredient zi"
        "         WHERE zi.recipe_id = r.id) AS n_rezeptzutaten,"
        "       count(ri.id) AS n_zutaten,"
        "       coalesce(sum(CASE WHEN ri.product_id IS NOT NULL"
        "                          AND coalesce(p.active, 0) <> 1"
        "                         THEN 1 ELSE 0 END), 0) AS n_ausgemustert"
        "  FROM recipe r"
        "  LEFT JOIN recipe_item ri ON ri.recipe_id = r.id"
        "  LEFT JOIN product p ON p.id = ri.product_id"
        " GROUP BY r.id"
        # COLLATE NOCASE, damit „Bolognese" und „bolognese" nebeneinander
        # stehen statt in zwei Blöcken.
        " ORDER BY r.name COLLATE NOCASE, r.id").fetchall()
    return [dict(r) for r in rows]


def _amount(wert):
    """`'500'` -> 500.0, `'0,5'` -> 0.5, leer oder Unsinn -> None.

    Komma und Punkt gelten beide: getippt wird auf einem deutschen Telefon
    („0,5"), und ein aus dem Rezept übernommener Wert kommt als Zahl. Unsinn
    wird `None` und nicht 0 — „null Gramm Butter" wäre eine Behauptung, die
    niemand aufgestellt hat, und sie würde beim Zusammenzählen mitgerechnet.
    """
    if wert in (None, ""):
        return None
    try:
        zahl = float(str(wert).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return zahl if zahl > 0 else None


def zutat_hinzufuegen(con: sqlite3.Connection, recipe_id: int, product_id=None,
                      free_text=None, qty: int = 1, amount=None,
                      unit=None) -> int:
    """Legt eine Zutat an. Gibt es sie schon, wird die Menge erhöht.

    Dieselbe Regel wie im Warenkorb: zweimal dasselbe heisst „zwei davon" und
    nicht „zwei Zeilen". Die Prüfung „genau eines von beidem" kommt aus
    `orders.korb`, weil es buchstäblich dieselbe Regel ist wie beim
    Bestellposten — zwei Kopien davon liefen irgendwann auseinander.

    `amount` und `unit` sind die BENÖTIGTE Menge bei der Portionszahl des
    Rezepts (WB-362) — „500 ml", nicht „1 Packung". Sie sind das, was mit den
    Portionen wächst; `qty` wächst nicht mit. Wird eine vorhandene Zutat ein
    zweites Mal angelegt, addiert sich die Menge nach denselben Regeln wie im
    Korb (`mengen.summiere`): passen die Einheiten nicht zusammen, bleibt die
    ältere stehen, statt zwei Einheiten zu einer Zahl zu verrühren.
    """
    _muss_geben(con, recipe_id)
    pid, text = korb.genau_eines(product_id, free_text, was="Eine Zutat")
    if pid is not None and not con.execute(
            "SELECT 1 FROM product WHERE id = ?", (pid,)).fetchone():
        raise UngueltigerPosten(f"Produkt {pid} gibt es nicht.")
    menge = max(1, int(qty))
    bedarf = _amount(amount)

    vorhanden = con.execute(
        "SELECT id, qty, amount, unit FROM recipe_item"
        " WHERE recipe_id = ? AND product_id IS ? AND free_text IS ?",
        (recipe_id, pid, text)).fetchone()
    if vorhanden:
        summe = mengen.summiere(vorhanden["amount"], vorhanden["unit"],
                                bedarf, unit)
        con.execute(
            "UPDATE recipe_item SET qty = ?, amount = ?, unit = ? WHERE id = ?",
            (vorhanden["qty"] + menge,
             summe[0] if summe else vorhanden["amount"],
             summe[1] if summe else vorhanden["unit"], vorhanden["id"]))
        con.commit()
        return int(vorhanden["id"])

    normiert = mengen.in_grundeinheit(bedarf, unit)
    cur = con.execute(
        "INSERT INTO recipe_item (recipe_id, product_id, free_text, qty,"
        "                         amount, unit) VALUES (?, ?, ?, ?, ?, ?)",
        (recipe_id, pid, text, menge,
         normiert[0] if normiert else None,
         normiert[1] if normiert else None))
    con.commit()
    return int(cur.lastrowid)


def zutat_menge_setzen(con: sqlite3.Connection, item_id: int, amount=None,
                       unit=None) -> dict:
    """Setzt die benötigte MENGE einer Zutat (nicht die Packungszahl).

    Getrennt von `zutat_menge()`, weil es zwei verschiedene Grössen sind und
    ein gemeinsames „Menge setzen" die Verwechslung einbauen würde, um die es
    in diesem Ticket geht. Eine leere Angabe LÖSCHT die Menge — dann skaliert
    die Zutat nicht mehr und zählt wieder als Packung, und das muss
    rücknehmbar sein.

    **Menge und Einheit hängen dabei nicht mehr aneinander (WB-375).** Vorher
    lief beides durch `in_grundeinheit()`, und weil das bei leerer Menge
    `None` liefert, löschte ein geleertes Mengenfeld die EINHEIT gleich mit.
    Wer „500 g" korrigieren wollte, bekam nach dem Neutippen „500 Stk" — eine
    stille Verfälschung der Mengenrechnung, gegen die Zurücknehmen nicht half.
    Ein `unit=None` heisst darum ausdrücklich „nicht mitgeschickt, lass die
    bisherige stehen"; nur ein leerer String nimmt die Einheit weg.
    """
    zeile = _zutat(con, item_id)
    menge = _amount(amount)
    roh = zeile["unit"] if unit is None else str(unit).strip()
    basis = mengen.grundeinheit(roh) if roh else None
    neue_einheit, faktor = basis if basis else (None, 1.0)
    neue_menge = None if menge is None else menge * faktor
    con.execute("UPDATE recipe_item SET amount = ?, unit = ? WHERE id = ?",
                (neue_menge, neue_einheit, item_id))
    con.commit()
    return {"id": int(zeile["id"]), "amount": neue_menge,
            "unit": neue_einheit}


def _zutat(con: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = con.execute("SELECT id, recipe_id, qty, amount, unit"
                      "  FROM recipe_item WHERE id = ?",
                      (item_id,)).fetchone()
    if row is None:
        raise UngueltigerPosten(f"Zutat {item_id} gibt es nicht.")
    return row


def zutat_menge(con: sqlite3.Connection, item_id: int, qty: int) -> int:
    """Setzt die Menge einer Zutat. Menge unter 1 entfernt sie.

    Wie im Warenkorb: der Minus-Knopf muss die Zeile auch loswerden können.
    Gibt die neue Menge zurück, 0 für „ist weg".
    """
    _zutat(con, item_id)
    menge = int(qty)
    if menge < 1:
        zutat_entfernen(con, item_id)
        return 0
    con.execute("UPDATE recipe_item SET qty = ? WHERE id = ?", (menge, item_id))
    con.commit()
    return menge


def zutat_entfernen(con: sqlite3.Connection, item_id: int) -> None:
    """Nimmt eine Zutat aus dem Rezept."""
    _zutat(con, item_id)
    con.execute("DELETE FROM recipe_item WHERE id = ?", (item_id,))
    con.commit()
