"""Der Zwischenspeicher für geholte Gerichte (WB-338).

**Das Einzige aus diesem Paket, das der Web-Prozess anfassen darf.** Ein
Treffer hier braucht kein Netz — und weil ein Gericht nicht bei jedem
Chat-Zug neu geholt wird, kostet „alles für Pho" die Quelle genau zweimal
zwei Anfragen im Leben und nicht zwei bei jedem Satz.

Drei Tabellen, drei Aufgaben (die Begründungen stehen am Schema in
`picknick.db`):

* `dish` — die FRAGE („pho") samt Zustand und Abrufdatum.
* `recipe` — die ANTWORT: das Rezept, jetzt mit Zubereitung, Zeiten und
  Herkunft. Dasselbe `recipe` wie die selbst angelegten Rezepte, damit ein
  geholtes Rezept in der Rezeptliste steht und gekocht werden kann.
* `recipe_ingredient` — die Zutaten, wie die Quelle sie schreibt.

**Auch das Nichts wird gemerkt.** Ein Gericht, zu dem Chefkoch nichts kennt,
bekommt `status = 'leer'`; eine Störung `status = 'fehler'`. Ohne diese
beiden Zeilen kostete jedes unbekannte Wort bei jedem Chat-Zug zwei Anfragen
an eine fremde Seite — und das wäre genau die Unhöflichkeit, die das Ticket
vermeiden will. Sie halten kürzer als ein Treffer (`ALTER`), weil ein
Rezept sich nicht ändert, eine Störung aber vorbeigeht.
"""
from __future__ import annotations

import sqlite3
import time

from picknick import db
from picknick.gerichte import chefkoch

#: Die Zustände einer Zeile in `dish`.
OFFEN = "offen"     # angefordert, der Lauf läuft noch (oder ist gestorben)
OK = "ok"           # ein Rezept liegt vor
LEER = "leer"       # die Quelle kennt das Gericht nicht
FEHLER = "fehler"   # die Quelle war nicht erreichbar oder hat Unsinn geliefert

#: Wie lange ein Treffer gilt. Ein Rezept von 2018 ist 2026 dasselbe Rezept;
#: was sich ändert, sind Bewertung und Stimmenzahl, und die entscheiden nur
#: die Wahl, nicht die Zutaten. Neunzig Tage sind trotzdem eine Grenze und
#: kein „nie wieder": ein besser bewertetes Rezept soll irgendwann eine
#: Chance bekommen.
ALTER_OK_S = 90 * 24 * 3600

#: Ein „kennt Chefkoch nicht" hält eine Woche. Kürzer als ein Treffer, weil
#: dort täglich Rezepte dazukommen — aber lang genug, dass ein Tippfehler
#: nicht bei jedem Satz eine Anfrage kostet.
ALTER_LEER_S = 7 * 24 * 3600

#: Eine Störung hält eine Stunde. Sie ist genau das, was vorbeigeht.
ALTER_FEHLER_S = 3600

#: So lange gilt ein laufender Abruf als laufend. Danach darf neu angefordert
#: werden — ein Prozess, den jemand abgeschossen hat, soll das Gericht nicht
#: für immer auf „wird geholt" festnageln.
ALTER_OFFEN_S = 300

_HOECHSTALTER = {OK: ALTER_OK_S, LEER: ALTER_LEER_S, FEHLER: ALTER_FEHLER_S,
                 OFFEN: ALTER_OFFEN_S}


def schluessel(name: str) -> str:
    """Gerichtsname -> Suchschlüssel. Dieselbe Faltung wie die Suche.

    „Gemüselasagne", „gemueselasagne" und „GEMUSELASAGNE" landen auf einer
    Zeile; sonst stünde dasselbe Gericht dreimal im Speicher und würde
    dreimal geholt.
    """
    return " ".join(db.normalisiere((name or "").strip().casefold()).split())


def _jetzt(uhr=time.time) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(uhr()))


def _sekunden(iso: str | None, uhr=time.time) -> float | None:
    """Alter eines ISO-Zeitpunkts in Sekunden, `None` wenn unlesbar."""
    if not iso:
        return None
    try:
        stand = time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None
    return max(0.0, uhr() - stand)


def frisch(zeile, uhr=time.time) -> bool:
    """Gilt dieser Speichereintrag noch?

    Ein unlesbares Datum gilt als alt: lieber einmal zu viel geholt als ein
    Eintrag, der nie wieder erneuert wird.
    """
    if zeile is None:
        return False
    grenze = _HOECHSTALTER.get(zeile["status"])
    if grenze is None:
        return False
    stempel = zeile["fetched_at"] or zeile["requested_at"]
    alter = _sekunden(stempel, uhr)
    return alter is not None and alter < grenze


_DISH_SQL = ("SELECT id, name, query, status, source, recipe_id, error,"
             "       requested_at, fetched_at FROM dish")


def zeile(con: sqlite3.Connection, name: str):
    """Die `dish`-Zeile zu einem Gerichtsnamen, oder `None`."""
    return con.execute(f"{_DISH_SQL} WHERE name = ?",
                       (schluessel(name),)).fetchone()


def wunsch(con: sqlite3.Connection, name: str, uhr=time.time) -> dict:
    """Trägt ein Gericht als „soll geholt werden" ein und gibt die Zeile zurück.

    Idempotent: eine bestehende Zeile wird auf `offen` zurückgesetzt und neu
    datiert. Genau darüber läuft auch die Sperre gegen doppelte Läufe — die
    Zeile ist prozessübergreifend, ein Merker im Speicher eines Web-Prozesses
    wäre es nicht.
    """
    frage = " ".join((name or "").split())
    if not frage:
        raise ValueError("Ohne Gerichtsnamen gibt es nichts zu holen.")
    con.execute(
        "INSERT INTO dish (name, query, status, source, requested_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET query = excluded.query,"
        "     status = excluded.status, requested_at = excluded.requested_at,"
        "     error = NULL",
        (schluessel(frage), frage, OFFEN, chefkoch.SOURCE, _jetzt(uhr)))
    con.commit()
    return dict(zeile(con, frage))


def vermerken(con: sqlite3.Connection, name: str, status: str,
              fehler: str | None = None, uhr=time.time) -> None:
    """Hält fest, dass ein Abruf nichts (`leer`) oder Ärger (`fehler`) ergab."""
    frage = " ".join((name or "").split())
    con.execute(
        "INSERT INTO dish (name, query, status, source, error, requested_at,"
        "                  fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET status = excluded.status,"
        "     error = excluded.error, fetched_at = excluded.fetched_at",
        (schluessel(frage), frage, status, chefkoch.SOURCE, fehler,
         _jetzt(uhr), _jetzt(uhr)))
    con.commit()


def merken(con: sqlite3.Connection, name: str, rezept: dict,
           uhr=time.time) -> int:
    """Schreibt ein geholtes Rezept in die Sammlung. Gibt die `recipe.id`.

    **In einem Zug**, und ein bereits vorhandenes Rezept derselben Quelle
    wird ÜBERSCHRIEBEN statt ein zweites Mal angelegt: sonst stünde „Pho Bo"
    nach dem dritten Abruf dreimal in der Rezeptliste.

    Die verknüpften Produkte (`recipe_item`) bleiben unberührt. Hat jemand
    dem Rezept von Hand Produkte zugeordnet, überlebt das eine Erneuerung —
    das ist die Arbeit eines Menschen, und die zu löschen wäre der teuerste
    Nebeneffekt, den diese Funktion haben könnte.
    """
    jetzt = _jetzt(uhr)
    titel = rezept.get("titel") or " ".join((name or "").split())
    vorhanden = con.execute(
        "SELECT id FROM recipe WHERE source = ? AND source_id = ?",
        (chefkoch.SOURCE, rezept.get("rezept_id"))).fetchone()
    felder = (titel, rezept.get("servings"), rezept.get("instructions"),
              rezept.get("prep_minutes"), rezept.get("cook_minutes"),
              rezept.get("rest_minutes"), rezept.get("difficulty"),
              chefkoch.SOURCE, str(rezept.get("rezept_id") or ""),
              rezept.get("site_url"), titel, rezept.get("rating"),
              rezept.get("votes"), jetzt)
    if vorhanden:
        recipe_id = int(vorhanden["id"])
        con.execute(
            "UPDATE recipe SET name = ?, servings = ?, instructions = ?,"
            " prep_minutes = ?, cook_minutes = ?, rest_minutes = ?,"
            " difficulty = ?, source = ?, source_id = ?, source_url = ?,"
            " source_title = ?, source_rating = ?, source_votes = ?,"
            " fetched_at = ? WHERE id = ?", (*felder, recipe_id))
        con.execute("DELETE FROM recipe_ingredient WHERE recipe_id = ?",
                    (recipe_id,))
    else:
        cur = con.execute(
            "INSERT INTO recipe (name, servings, instructions, prep_minutes,"
            " cook_minutes, rest_minutes, difficulty, source, source_id,"
            " source_url, source_title, source_rating, source_votes,"
            " fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            felder)
        recipe_id = int(cur.lastrowid)

    for pos, z in enumerate(rezept.get("zutaten") or []):
        con.execute(
            "INSERT INTO recipe_ingredient (recipe_id, pos, gruppe, raw_name,"
            " name, amount, unit, usage_info) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (recipe_id, pos, z.get("gruppe"), z.get("raw_name"),
             z.get("name") or z.get("raw_name"), z.get("amount"),
             z.get("unit"), z.get("usage_info")))

    frage = " ".join((name or "").split())
    con.execute(
        "INSERT INTO dish (name, query, status, source, recipe_id,"
        "                  requested_at, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET status = excluded.status,"
        "     recipe_id = excluded.recipe_id, error = NULL,"
        "     fetched_at = excluded.fetched_at",
        (schluessel(frage), frage, OK, chefkoch.SOURCE, recipe_id, jetzt,
         jetzt))
    con.commit()
    return recipe_id


def zutaten(con: sqlite3.Connection, recipe_id: int) -> list[dict]:
    """Die Zutatenliste eines Rezepts, so wie die Quelle sie schreibt."""
    rows = con.execute(
        "SELECT id, pos, gruppe, raw_name, name, amount, unit, usage_info"
        "  FROM recipe_ingredient WHERE recipe_id = ? ORDER BY pos, id",
        (recipe_id,)).fetchall()
    return [dict(r) for r in rows]


def gericht(con: sqlite3.Connection, name: str, uhr=time.time) -> dict | None:
    """Ein gespeichertes Gericht samt Zutaten — oder `None`.

    `None` heisst „dafür ist nichts (mehr) da": nie gefragt, noch im Abruf,
    nichts gefunden, oder der Eintrag ist zu alt. In allen diesen Fällen soll
    der Aufrufer den bisherigen Weg gehen, statt zu warten oder zu scheitern.
    """
    row = zeile(con, name)
    if row is None or row["status"] != OK or not row["recipe_id"]:
        return None
    if not frisch(row, uhr):
        return None
    rezept = con.execute(
        "SELECT id, name, servings, instructions, prep_minutes, cook_minutes,"
        "       rest_minutes, difficulty, source, source_id, source_url,"
        "       source_title, source_rating, source_votes, fetched_at"
        "  FROM recipe WHERE id = ?", (row["recipe_id"],)).fetchone()
    if rezept is None:
        # Das Rezept ist gelöscht worden. Der Verweis steht dank ON DELETE
        # SET NULL nicht mehr da — dann ist der Speicher eben leer.
        return None
    return {**dict(row), "rezept": dict(rezept),
            "zutaten": zutaten(con, int(rezept["id"]))}


def bereit(con: sqlite3.Connection, uhr=time.time) -> list[dict]:
    """Alle Gerichte, die JETZT ohne Netz bedient werden können.

    Das ist die Liste, in der der Chat-Zug den Satz nach einem Gerichtsnamen
    absucht — genauso, wie `rezeptweg` die Rezeptnamen absucht. Ein Gericht
    ohne verwertbares Rezept steht nicht drin und kann den Modellweg deshalb
    auch nicht abschneiden.
    """
    rows = con.execute(
        f"{_DISH_SQL} WHERE status = ? AND recipe_id IS NOT NULL"
        " ORDER BY length(query) DESC, id", (OK,)).fetchall()
    return [dict(r) for r in rows if frisch(r, uhr)]


def offene(con: sqlite3.Connection) -> list[dict]:
    """Die Wünsche, die noch niemand geholt hat — für den Lauf ohne Argument."""
    rows = con.execute(f"{_DISH_SQL} WHERE status = ? ORDER BY requested_at",
                       (OFFEN,)).fetchall()
    return [dict(r) for r in rows]
