"""Der Rezeptentwurf eines Chat-Zugs (WB-337).

**Der natürliche Moment, in dem ein Rezept entsteht, ist nicht die erledigte
Bestellung, sondern der Satz „alles für Spaghetti Bolognese".** Dort steht die
Zutatenliste schon da, und sie zu korrigieren IST das Anlegen des Rezepts.
Bis zu diesem Ticket ging die Arbeit danach verloren: die Vorschläge wanderten
in den Korb, der Korb wurde eine Bestellung, und beim nächsten Mal lief
derselbe Satz wieder über Modell und Quelle.

Das ist teuer, und zwar nicht nur an Zeit. Ein gespeichertes Rezept
**überspringt das Modell vollständig** (`zettel.path = "recipe"`): kein
Aufruf, keine Wartezeit, keine Halluzination, und es funktioniert auch, wenn
die vLLM-Box schläft. Jedes angelegte Rezept ist damit ein dauerhafter Fix
für ein Gericht — der Blätterteig in der Lasagne (WB-336) kommt nie wieder,
wenn die Lasagne einmal richtig dasteht.

Drei Entscheidungen tragen dieses Modul:

**1. Die Zugehörigkeit kostet kein Modellfeld.** Das Ticket schlug vor,
`plan.extract` um ein `gericht: true/false` je Begriff zu erweitern. Das ist
seit WB-367/WB-369 nicht mehr nötig: auf dem Chefkoch-Weg stammen die
Begriffe aus einer bekannten Zutatenliste, und `herkunft.zuordnen` ordnet sie
ihr ohne Modell wieder zu. Was eine Zutat gefunden hat, gehört zum Gericht;
was keine gefunden hat, steht für sich — das ist „Klopapier", buchstäblich
und gemessen. Ein Modellfeld hätte Token gekostet, hätte geraten werden
können und wäre bei jedem Zug neu zu prüfen gewesen.

**2. Nur der Chefkoch-Weg bekommt einen Entwurf.** Auf dem reinen Modellweg
(`weg = llm`) gibt es keine Zutatenliste, gegen die sich prüfen liesse — dort
rät das Modell die Zutaten, und genau das war der Fehler, gegen den WB-367
den Chefkoch-Abruf eingeführt hat. Ein Rezept aus geratenen Zutaten wäre
nicht bloss falsch, sondern DAUERHAFT falsch: es würde ab dem nächsten Satz
den schnellen Weg nehmen und den Fehler jedes Mal wiederholen, ohne dass
noch ein Modell dazwischensteht, das ihn korrigieren könnte. Der Rezeptweg
selbst braucht keinen Entwurf (das Rezept gibt es schon), die Auffächerung
kennt gar kein Gericht.

**3. Gespeichert wird erst beim Abschicken.** Dieselbe Regel und derselbe
Grund wie bei den Eval-Annotationen aus WB-329: bis dahin darf sie ihre
Meinung ändern. Ein Rezept, das beim Wegklicken des Chats schon in der
Sammlung stünde, wäre Müll, den niemand aufräumt.

**Wohin gespeichert wird.** In das Rezept, das der Chefkoch-Abruf ohnehin
schon angelegt hat (`dish.recipe_id`) — dort steht die Zubereitung, dort
stehen Zeiten, Portionen und Herkunft. Die behaltenen Zeilen kommen als
`recipe_item` dazu, und erst damit fängt `rezeptweg.erkenne` den nächsten
Satz ab (es übergeht Rezepte ohne Produkte). Ein zweites Rezept daneben
anzulegen hiesse, Zubereitung und Einkaufszettel desselben Gerichts auf zwei
Zeilen zu verteilen.

Der NAME wird dabei auf den des Entwurfs gesetzt, und das ist keine
Kosmetik: `rezeptweg.erkenne` sucht den Rezeptnamen im Satz, und „Spaghetti
Bolognese al Forno à la Mama" steht in keinem Satz, den jemand tippt. Trägt
den Namen schon ein anderes Rezept, entsteht eine zweite Fassung („… (2)")
statt einer stillen Überschreibung; hat ein Mensch das Rezept bereits selbst
umbenannt, gewinnt sein Name.
"""
from __future__ import annotations

import logging
import sqlite3

from zettel import mengen, recipes
from zettel.assistant import vorschlaege
from zettel.orders.bestellung import jetzt

log = logging.getLogger(__name__)

#: Die drei Zustände von `chat_suggestion.dish_item`. Die Begründung, warum
#: es drei sind und nicht zwei, steht am Schema in `zettel.db`.
KEINE_ZUTAT = None
ZUTAT = 1
HERAUSGENOMMEN = 0


class EntwurfFehler(ValueError):
    """Ein Entwurf oder eine Änderung daran, die es so nicht gibt."""


# --------------------------------------------------------------------------
# Anlegen und Lesen

def merken(con: sqlite3.Connection, chat_message_id: int, *, dish: str,
           recipe_id: int | None = None) -> None:
    """Legt den Entwurf zu einer Antwortzeile an. Ohne Gericht kein Entwurf.

    `dish` ist der Name, unter dem die Nutzerin das Gericht genannt hat, und
    zugleich der vorbelegte Rezeptname — **nicht** der Titel des
    Chefkoch-Rezepts. „Spaghetti Bolognese" findet der Rezeptweg beim
    nächsten Satz wieder, „Bolognese wie in Italien (das Original!)" nicht.

    `INSERT OR IGNORE`: ein zweiter Aufruf zu derselben Zeile darf einen
    Namen, den die Nutzerin schon überschrieben hat, nicht zurücksetzen.
    """
    name = " ".join((dish or "").split())
    if not name:
        return
    con.execute(
        "INSERT OR IGNORE INTO chat_entwurf"
        " (chat_message_id, dish, name, recipe_id, verworfen, created_at)"
        " VALUES (?, ?, ?, ?, 0, ?)",
        (chat_message_id, name, name, recipe_id, jetzt()))
    con.commit()


def _kopf(con: sqlite3.Connection, chat_message_id: int):
    return con.execute(
        "SELECT chat_message_id, dish, name, recipe_id, verworfen, saved_at"
        "  FROM chat_entwurf WHERE chat_message_id = ?",
        (chat_message_id,)).fetchone()


def zu_nachricht(con: sqlite3.Connection, chat_message_id: int,
                 vorgeschlagen: list[dict] | None = None) -> dict | None:
    """Der Entwurf einer Chatzeile samt seiner Zeilen — oder `None`.

    `vorgeschlagen` ist die bereits geholte Vorschlagsliste dieser Zeile. Die
    Oberfläche hat sie ohnehin (der ganze Verlauf wird gerendert), und ohne
    diesen Weg wäre jeder Blick in den Warenkorb eine zweite Abfrage je Zug —
    mit anderen Wörterbüchern, an denen die Bilder dann fehlten.

    `zeilen` sind die Gerichtszutaten in der Reihenfolge des Vorschlagens,
    jede mit `im_rezept` (geht sie hinein?) und `verworfen` (hat sie „Nein"
    bekommen?). Herausgenommene und verworfene bleiben in der Liste: sie
    müssen zurückgeholt werden können, und eine Liste, aus der Zeilen
    verschwinden, erklärt nicht, warum sie kürzer wurde.

    `n_drin` ist die Zahl, die zählt — was beim Abschicken wirklich ins
    Rezept ginge. Steht sie auf 0, entsteht kein Rezept, und die Oberfläche
    sagt es, statt es geschehen zu lassen.
    """
    kopf = _kopf(con, chat_message_id)
    if kopf is None:
        return None
    if vorgeschlagen is None:
        vorgeschlagen = vorschlaege.liste(con, chat_message_id)
    zeilen = [z for z in vorgeschlagen
              if z["zum_gericht"] and not z["ist_korrektur"]]
    for z in zeilen:
        # Eine Korrektur ersetzt ihre Quelle auch im Rezept (WB-359 trifft
        # WB-337): im Korb liegt das andere Hackfleisch, also gehört es auch
        # ins Rezept. Die Quellzeile steht weiter da — sie ist das Label
        # „so nicht" —, aber ins Rezept geht die Korrektur.
        k = z.get("korrektur")
        z["rezeptzeile"] = k if (k is not None and k["behalten"]) else z
    return {
        "chat_message_id": int(kopf["chat_message_id"]),
        "dish": kopf["dish"],
        "name": kopf["name"],
        "recipe_id": kopf["recipe_id"],
        "verworfen": bool(kopf["verworfen"]),
        "saved_at": kopf["saved_at"],
        "zeilen": zeilen,
        "n_drin": sum(1 for z in zeilen if _geht_ins_rezept(z)),
    }


def _geht_ins_rezept(zeile: dict) -> bool:
    """Landet diese Vorschlagszeile beim Abschicken im Rezept?

    Drei Bedingungen, und jede steht für eine eigene Aussage der Nutzerin:
    sie gehört zum Gericht (`dish_item = 1`), sie wurde nicht aus dem Entwurf
    genommen (dieselbe Spalte auf 0), und sie wurde behalten — entweder
    selbst oder über eine Korrektur, die an ihre Stelle getreten ist.

    **`offen` reicht nicht.** Ein Vorschlag, über den nie entschieden wurde,
    liegt auch nicht im Korb; ihn ins Rezept zu schreiben hiesse, eine Zutat
    zu behaupten, die die Nutzerin nie angesehen hat.
    """
    if not zeile["im_rezept"]:
        return False
    if zeile["behalten"]:
        return True
    k = zeile.get("korrektur")
    return bool(k is not None and k["behalten"])


def zu_bestellung(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Alle Entwürfe einer Bestellung, in der Reihenfolge der Züge."""
    rows = con.execute(
        "SELECT e.chat_message_id AS id FROM chat_entwurf e"
        "  JOIN chat_message m ON m.id = e.chat_message_id"
        " WHERE m.order_id = ? ORDER BY e.chat_message_id",
        (order_id,)).fetchall()
    entwuerfe = [zu_nachricht(con, int(r["id"])) for r in rows]
    return [e for e in entwuerfe if e is not None]


# --------------------------------------------------------------------------
# Ändern — alles vor dem Abschicken, alles rücknehmbar

def _muss_geben(con: sqlite3.Connection, chat_message_id: int):
    kopf = _kopf(con, chat_message_id)
    if kopf is None:
        raise EntwurfFehler(
            "Zu dieser Antwort gibt es keinen Rezeptentwurf (mehr).")
    return kopf


def benennen(con: sqlite3.Connection, chat_message_id: int,
             name: str) -> dict:
    """Setzt den Rezeptnamen des Entwurfs.

    Ein leerer Name fällt auf das Gericht zurück, statt das Speichern zu
    verhindern: das Feld steht auf einem Telefon, und ein versehentlich
    geleertes Feld darf kein Rezept kosten. Der Grund ist derselbe wie bei
    `recipes._name` — ein namenloses Rezept sucht später niemand heraus.
    """
    kopf = _muss_geben(con, chat_message_id)
    sauber = " ".join((name or "").split()) or kopf["dish"]
    con.execute("UPDATE chat_entwurf SET name = ? WHERE chat_message_id = ?",
                (sauber, chat_message_id))
    con.commit()
    return zu_nachricht(con, chat_message_id)


def verwerfen(con: sqlite3.Connection, chat_message_id: int,
              ja: bool = True) -> dict:
    """„Daraus soll kein Rezept werden" — und der Weg zurück.

    Der Entwurf bleibt dabei stehen, samt Namen und Zeilen. Ein verworfener
    Entwurf, der beim Zurücknehmen neu zusammengesucht werden müsste, wäre
    kein Rückweg, sondern ein zweiter Anlauf (dieselbe Überlegung wie beim
    Rückweg aus WB-361).
    """
    _muss_geben(con, chat_message_id)
    con.execute(
        "UPDATE chat_entwurf SET verworfen = ? WHERE chat_message_id = ?",
        (1 if ja else 0, chat_message_id))
    con.commit()
    return zu_nachricht(con, chat_message_id)


def zeile_setzen(con: sqlite3.Connection, suggestion_id: int,
                 drin: bool) -> dict:
    """Nimmt eine Zutat aus dem Entwurf — oder wieder hinein.

    **Das ist etwas anderes als „Nein".** „Nein" heisst „dieses Produkt ist
    falsch" und ist das Eval-Label; hier heisst es „das kaufe ich, aber es
    gehört nicht ins Rezept" — der Parmesan, den es sowieso immer gibt. Beide
    Wege führen dazu, dass die Zeile nicht im Rezept steht, und nur einer
    davon ist ein Urteil über das Modell. Sie in einen Knopf zu legen hiesse,
    genau die Zahl zu verfälschen, um die es im Projekt geht.

    Eine Zeile, die nie zum Gericht gehörte (`dish_item IS NULL`), wird
    abgewiesen: Klopapier ins Rezept zu holen ist keine Änderung am Entwurf,
    sondern der Fehler, gegen den dieses Ticket geschrieben wurde.
    """
    v = vorschlaege.eine(con, suggestion_id)
    if not v["zum_gericht"]:
        raise EntwurfFehler(
            f"„{v['name']}“ ist keine Zutat des Gerichts — die Zeile gehört "
            "in den Korb und nicht ins Rezept.")
    con.execute("UPDATE chat_suggestion SET dish_item = ? WHERE id = ?",
                (ZUTAT if drin else HERAUSGENOMMEN, suggestion_id))
    con.commit()
    return vorschlaege.eine(con, suggestion_id)


def bedarf_setzen(con: sqlite3.Connection, suggestion_id: int, menge,
                  einheit: str | None = None) -> dict:
    """Ändert die benötigte Menge einer Zutat des Entwurfs.

    Es ist die MENGE aus dem Rezept, die hier geändert wird („500 g"), nicht
    die Packungszahl — dieselbe Unterscheidung wie in
    `recipes.zutat_menge_setzen`, und aus demselben Grund: die eine wächst
    mit den Portionen, die andere nicht.

    Die Einheit bleibt, wo keine mitgegeben wird. Sie stammt aus der
    Zutatenliste der Quelle und ist schon in der Grundeinheit; eine leere
    Angabe hiesse „Stück" und machte aus 500 g fünfhundert Packungen.

    **Was schon im Korb liegt, ändert sich davon nicht.** Die Korbzeile ist
    beim „Ja" entstanden und kann längst eine sein, die die Nutzerin selbst
    aufgestockt hat — sie hier nachzurechnen hiesse, fremde Mengen zu
    überschreiben (dieselbe Zurückhaltung wie bei der Rücknahme in WB-361).
    Im Korb steht ein Mengenfeld, einen Tipp entfernt.
    """
    v = vorschlaege.eine(con, suggestion_id)
    if not v["zum_gericht"]:
        raise EntwurfFehler(
            f"„{v['name']}“ ist keine Zutat des Gerichts — hier gibt es "
            "keine Menge aus einem Rezept.")
    bedarf = _zahl(menge)
    normiert = mengen.in_grundeinheit(bedarf, einheit or v["need_unit"])
    con.execute(
        "UPDATE chat_suggestion SET need_amount = ?, need_unit = ?"
        " WHERE id = ?",
        (normiert[0] if normiert else None,
         normiert[1] if normiert else None, suggestion_id))
    con.commit()
    return vorschlaege.eine(con, suggestion_id)


def _zahl(wert):
    """`'500'` -> 500.0, `'0,5'` -> 0.5, leer oder Unsinn -> `None`.

    Wörtlich dieselbe Regel wie `recipes.sammlung._amount`: getippt wird auf
    einem deutschen Telefon, und eine geleerte Menge ist eine gültige Angabe
    („dazu steht nichts im Rezept") und keine Null.
    """
    if wert in (None, ""):
        return None
    try:
        zahl = float(str(wert).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return zahl if zahl > 0 else None


# --------------------------------------------------------------------------
# Speichern — beim Abschicken, und nie auf Kosten der Bestellung

def speichern(con: sqlite3.Connection, order_id: int) -> list[dict]:
    """Macht aus den Entwürfen einer Bestellung Rezepte. **Wirft nie.**

    Der Aufrufer ist `orders.abschicken()`, und eine Bestellung darf an einem
    Rezept nicht scheitern — dieselbe Zusicherung wie bei den Annotationen
    aus WB-329 (`obs.labels.schreiben`), und aus demselben Grund: die
    Bestellung ist der Nutzweg, das Rezept ist der Ertrag daneben. Was hier
    schiefgeht, steht im Protokoll; die Entscheidungen stehen weiter in
    `chat_suggestion`, das Rezept lässt sich also nachtragen, die Bestellung
    nicht wiederholen.

    Gibt je Entwurf einen Bericht zurück: was daraus wurde und warum nicht.
    """
    berichte = []
    try:
        entwuerfe = zu_bestellung(con, order_id)
    except Exception as e:  # noqa: BLE001 — siehe Docstring
        log.warning("Rezeptentwürfe nicht ermittelt (%s: %s).",
                    e.__class__.__name__, e)
        return []
    for e in entwuerfe:
        try:
            berichte.append(_einen_speichern(con, e))
        except Exception as ex:  # noqa: BLE001
            log.warning("Rezept „%s“ nicht gespeichert (%s: %s).",
                        e["name"], ex.__class__.__name__, ex)
            berichte.append({"entwurf": e, "recipe_id": None,
                             "grund": str(ex), "zutaten": 0})
    return berichte


def _einen_speichern(con: sqlite3.Connection, e: dict) -> dict:
    """Ein Entwurf -> ein Rezept. Die drei Ausgänge stehen im Bericht."""
    if e["saved_at"]:
        # Ein zweites Abschicken derselben Bestellung gibt es nicht (der
        # Zustand wechselt), ein zweiter Aufruf von `speichern()` sehr wohl.
        # Er soll denselben Bestand ergeben und nicht den doppelten —
        # dieselbe Überlegung wie beim `identifier` der Annotationen.
        return _bericht(e, e["recipe_id"], "schon gespeichert", 0)
    if e["verworfen"]:
        return _bericht(e, None, "die Nutzerin wollte kein Rezept", 0)
    zeilen = [z["rezeptzeile"] for z in e["zeilen"] if _geht_ins_rezept(z)]
    if not zeilen:
        # „Verwirft sie alle Gerichtszutaten, entsteht kein Rezept." Ein
        # Rezept ohne Zutaten wäre eine leere Zeile in der Sammlung, die
        # niemand mehr aufräumt — und `recipes.in_den_korb` weist es ohnehin
        # ab.
        return _bericht(e, None, "keine Zutat behalten", 0)

    recipe_id = _ziel(con, e)
    for z in zeilen:
        recipes.zutat_hinzufuegen(
            con, recipe_id, product_id=z["product_id"],
            free_text=z["free_text"], qty=z["qty"],
            # Die benötigte Menge aus der Zutatenliste der Quelle — bei DER
            # Portionszahl, die im Rezept steht (WB-362). Ohne sie skalierte
            # das Rezept beim nächsten „für 8 statt 4" nicht mehr.
            amount=z["need_amount"], unit=z["need_unit"])
    con.execute(
        "UPDATE chat_entwurf SET saved_at = ?, recipe_id = ?"
        " WHERE chat_message_id = ?",
        (jetzt(), recipe_id, e["chat_message_id"]))
    con.commit()
    return _bericht(e, recipe_id, None, len(zeilen))


def _ziel(con: sqlite3.Connection, e: dict) -> int:
    """In welches Rezept die Zutaten gehen — und wie es danach heisst.

    Erste Wahl ist das Rezept des Abrufs (`chat_entwurf.recipe_id`): dort
    stehen Zubereitung, Zeiten, Portionen und Herkunft. Ist es verschwunden
    (jemand hat es gelöscht), entsteht ein neues, und der Entwurf trägt
    genug, um es zu füllen — der Name und die Zeilen.
    """
    vorhanden = None
    if e["recipe_id"]:
        vorhanden = con.execute(
            "SELECT id, name, source_title FROM recipe WHERE id = ?",
            (e["recipe_id"],)).fetchone()
    if vorhanden is None:
        return recipes.anlegen(con, _freier_name(con, e["name"], None))

    recipe_id = int(vorhanden["id"])
    name = _freier_name(con, e["name"], recipe_id)
    if vorhanden["source_title"] and vorhanden["name"] != vorhanden["source_title"]:
        # Jemand hat dieses Rezept schon umbenannt. Sein Name gewinnt: das
        # ist die Arbeit eines Menschen, und der vorbelegte Entwurfsname
        # („Bolognese") ist bloss das Wort aus dem Satz. Dieselbe Rücksicht
        # nimmt `gerichte.speicher.merken` beim erneuten Abruf.
        return recipe_id
    if name != vorhanden["name"]:
        recipes.aendern(con, recipe_id, name=name)
    return recipe_id


def _freier_name(con: sqlite3.Connection, name: str,
                 ausser: int | None) -> str:
    """Der Name, wenn ihn noch niemand trägt — sonst „… (2)", „… (3)", …

    **Kein stilles Überschreiben.** Trägt ein anderes Rezept den Namen
    schon, entsteht eine zweite Fassung; das Ticket lässt beides zu (fragen
    oder zweite Fassung), und fragen kann hier niemand: gespeichert wird
    beim Abschicken, und da schaut die Nutzerin auf die Bestellliste und
    nicht auf eine Rückfrage.

    Der Rezeptweg leidet darunter nicht: er sucht Namen im Satz, und
    „Bolognese (2)" steht in keinem — der Satz trifft weiter die erste
    Fassung. Wer die zweite will, benennt sie um.
    """
    sauber = " ".join((name or "").split()) or "Rezept"
    versuch, n = sauber, 1
    while True:
        row = con.execute(
            "SELECT id FROM recipe WHERE name = ? COLLATE NOCASE"
            "   AND id IS NOT ? LIMIT 1", (versuch, ausser)).fetchone()
        if row is None:
            return versuch
        n += 1
        versuch = f"{sauber} ({n})"


def _bericht(e: dict, recipe_id, grund, zutaten: int) -> dict:
    return {"entwurf": e, "recipe_id": recipe_id, "grund": grund,
            "zutaten": zutaten}
