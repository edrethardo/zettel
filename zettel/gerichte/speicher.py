"""Der Zwischenspeicher für geholte Gerichte (WB-338).

**Der Normalweg zu einem Gericht, und der einzige ohne Netz.** Ein Treffer
hier kostet keine Anfrage — und weil ein Gericht nicht bei jedem Chat-Zug neu
geholt wird, kostet „alles für Pho" die Quelle genau zwei Anfragen im Leben
und nicht zwei bei jedem Satz. Seit WB-367 holt der Web-Prozess selbst, wenn
hier nichts steht; dass er es fast nie muss, ist die Leistung dieser drei
Tabellen.

Drei Tabellen, drei Aufgaben (die Begründungen stehen am Schema in
`zettel.db`):

* `dish` — die FRAGE („pho") samt Zustand und Abrufdatum.
* `dish_treffer` — die ÜBRIGEN Rezepte derselben Suche (WB-387). Chefkoch
  liefert zwölf in einer Antwort; elf davon wurden bis dahin weggeworfen,
  obwohl sie nichts kosteten. Sie stehen hier, damit ein Mensch wählen kann,
  ohne dass noch einmal gesucht wird.
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

from zettel import db
from zettel.gerichte import chefkoch

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

    **Und seit WB-337 gilt dasselbe für den NAMEN.** Weicht er vom
    `source_title` ab, hat ihn ein Mensch gesetzt — von Hand oder über einen
    Rezeptentwurf aus dem Chat. Er bleibt dann stehen. Ohne diese Zeile wäre
    der Umbenennung eine Frist gesetzt: nach 90 Tagen hiesse das Rezept
    wieder „Bolognese al Forno à la Mama", `rezeptweg.erkenne` fände es im
    Satz „alles für Bolognese" nicht mehr, und der schnelle Weg wäre ohne
    ein Zutun still verschwunden.
    """
    jetzt = _jetzt(uhr)
    titel = rezept.get("titel") or " ".join((name or "").split())
    vorhanden = con.execute(
        "SELECT id, name, source_title FROM recipe"
        " WHERE source = ? AND source_id = ?",
        (chefkoch.SOURCE, rezept.get("rezept_id"))).fetchone()
    felder = (titel, rezept.get("servings"), rezept.get("instructions"),
              rezept.get("prep_minutes"), rezept.get("cook_minutes"),
              rezept.get("rest_minutes"), rezept.get("difficulty"),
              chefkoch.SOURCE, str(rezept.get("rezept_id") or ""),
              rezept.get("site_url"), titel, rezept.get("rating"),
              rezept.get("votes"), jetzt)
    if vorhanden:
        recipe_id = int(vorhanden["id"])
        if (vorhanden["source_title"]
                and vorhanden["name"] != vorhanden["source_title"]):
            # Umbenannt von einem Menschen (siehe Docstring): sein Name
            # gewinnt. `source_title` behält daneben, wie die Quelle es
            # nennt — verloren geht dabei nichts.
            felder = (vorhanden["name"], *felder[1:])
        con.execute(
            "UPDATE recipe SET name = ?, servings = ?, instructions = ?,"
            " prep_minutes = ?, cook_minutes = ?, rest_minutes = ?,"
            " difficulty = ?, source = ?, source_id = ?, source_url = ?,"
            " source_title = ?, source_rating = ?, source_votes = ?,"
            " fetched_at = ? WHERE id = ?", (*felder, recipe_id))
        con.execute("DELETE FROM recipe_ingredient WHERE recipe_id = ?",
                    (recipe_id,))
        # **Und die gemerkte Zuordnung mit** (WB-408). Sie ist die Antwort
        # des Modells auf GENAU DIESE Zutatenliste; steht dort ab der
        # nächsten Zeile eine andere, wäre sie eine Zuordnung zu Zutaten, die
        # es nicht mehr gibt — schlimmer als keine, weil sie gültig aussieht.
        # Roh und nicht über `assistant.zuordnung.vergessen`: dieses Modul
        # kennt den Assistenten nicht und soll ihn nicht kennenlernen.
        con.execute("DELETE FROM recipe_zuordnung WHERE recipe_id = ?",
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
    # Die übrigen Treffer derselben Suche (WB-387) — nur wenn welche
    # mitgekommen sind. Die Wahl einer Alternative holt genau EIN Detail und
    # bringt keine Trefferliste mit; sie darf die vorhandene nicht löschen.
    if rezept.get("treffer"):
        treffer_merken(con, zeile(con, frage)["id"], rezept["treffer"])
    con.commit()
    return recipe_id


def treffer_merken(con: sqlite3.Connection, dish_id: int,
                   treffer: list[dict]) -> int:
    """Schreibt die Trefferliste einer Suche an das Gericht (WB-387).

    Ersetzt, was dort stand: die Liste ist die ANTWORT auf eine Suche, und
    nach einem neuen Abruf gilt die neue. Ein Rezept, das inzwischen aus den
    zwölf herausgefallen ist, soll nicht als Alternative stehen bleiben —
    angeboten wird, was die Quelle heute liefert.

    Gibt zurück, wie viele Zeilen entstanden sind. Kein `commit`: der
    Aufrufer schreibt das Rezept im selben Zug.
    """
    con.execute("DELETE FROM dish_treffer WHERE dish_id = ?", (dish_id,))
    n = 0
    for pos, t in enumerate(treffer or []):
        if not t.get("rezept_id"):
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO dish_treffer (dish_id, source_id,"
            " source_title, source_url, rating, votes, prep_minutes,"
            " difficulty, plus, gewicht, pos)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dish_id, str(t["rezept_id"]), t.get("titel") or "",
             t.get("site_url"), t.get("rating"), t.get("votes"),
             t.get("prep_minutes"), t.get("difficulty"),
             1 if t.get("plus") else 0, chefkoch.gewicht(t), pos))
        n += cur.rowcount or 0
    return n


def treffer(con: sqlite3.Connection, dish_id: int,
            grenze: int | None = chefkoch.ANGEBOT) -> list[dict]:
    """Die Rezepte, die zu diesem Gericht zur Wahl stehen (WB-387).

    Bestgewichtet zuerst — dieselbe Reihenfolge wie `chefkoch.zur_wahl`, nur
    aus der Datenbank statt aus der Antwort. Das erste Element ist damit das
    vorausgewählte Rezept.

    **Plus-Rezepte stehen nicht drin.** Sie tragen erfundene Stimmenzahlen
    (`chefkoch.PLUS_UEBERGEHEN`) und werden auch nicht vorausgewählt; was
    nicht gewählt werden darf, soll auch nicht zur Wahl stehen.

    Was die Suchantwort nicht hergibt — Gesamtzeit und Zutatenzahl —, kommt
    aus `recipe`, WO ES SCHON GEHOLT WURDE. Der Verbund läuft über die
    Rezept-ID der Quelle und kostet keine Anfrage: die vorausgewählte Zeile
    hat diese Zahlen immer, eine nie geholte Alternative hat sie nicht, und
    dann steht dort nichts statt einer geschätzten Zahl.
    """
    # **Die Spaltennamen sind die von `chefkoch.parse_treffer`** (`rezept_id`,
    # `titel`, `site_url`). Ein gespeicherter Treffer soll aussehen wie ein
    # frisch geholter — dann nimmt `chefkoch.hole_detail` beide, und es gibt
    # nicht zwei Gestalten desselben Dings im Projekt.
    rows = con.execute(
        "SELECT t.id, t.source_id AS rezept_id, t.source_title AS titel,"
        "       t.source_url AS site_url, t.rating,"
        "       t.votes, t.prep_minutes, t.difficulty, t.gewicht, t.pos,"
        "       r.id AS recipe_id, r.prep_minutes AS r_prep,"
        "       r.cook_minutes AS r_cook, r.rest_minutes AS r_rest,"
        "       (SELECT count(*) FROM recipe_ingredient i"
        "         WHERE i.recipe_id = r.id) AS n_zutaten"
        "  FROM dish_treffer t"
        "  LEFT JOIN recipe r ON r.source = ? AND r.source_id = t.source_id"
        " WHERE t.dish_id = ? AND t.plus = 0"
        " ORDER BY t.gewicht DESC, t.votes DESC, t.pos",
        (chefkoch.SOURCE, dish_id)).fetchall()
    liste = [dict(r) for r in rows]
    return liste[:grenze] if grenze else liste


def angeboten(con: sqlite3.Connection, dish_id: int, source_id: str,
              grenze: int | None = chefkoch.ANGEBOT) -> dict | None:
    """Der Treffer zu dieser Rezept-ID — aber nur, wenn er angeboten WURDE.

    Dieselbe Zusicherung wie bei den Produkt-IDs in `plan.choose` und bei den
    Sorten in `oberbegriffe.gewaehlte`, nur für dieses Formular: gewählt
    werden kann, was vorlag. Eine ID aus einem von Hand gebauten Formular
    fällt hier weg, statt eine fremde Seite nach einer beliebigen Zahl zu
    fragen.
    """
    gesucht = str(source_id or "").strip()
    if not gesucht:
        return None
    return next((t for t in treffer(con, dish_id, grenze)
                 if t["rezept_id"] == gesucht), None)


def rezept_zur_quelle(con: sqlite3.Connection, source_id: str):
    """Das gespeicherte Rezept zu einer Rezept-ID der Quelle, oder `None`.

    Der Weg an der Quelle vorbei (WB-387): wer zwischen zwei Rezepten hin und
    her wechselt, soll beim Zurückwechseln keine Anfrage kosten. Geholt wurde
    es schon einmal, und ein Rezept ändert sich nicht.
    """
    return con.execute(
        "SELECT id, name FROM recipe WHERE source = ? AND source_id = ?",
        (chefkoch.SOURCE, str(source_id or ""))).fetchone()


def zeigt_auf(con: sqlite3.Connection, name: str, recipe_id: int,
              uhr=time.time) -> None:
    """Lässt ein Gericht auf ein anderes, bereits geholtes Rezept zeigen.

    Nur der Verweis ändert sich — die Trefferliste bleibt, wie sie ist, und
    das Rezept, das vorher dranhing, bleibt in der Sammlung stehen. Es ist
    dieselbe Haltung wie bei den Zutaten in `merken`: was schon da ist,
    gehört dem, der es hat.
    """
    jetzt = _jetzt(uhr)
    frage = " ".join((name or "").split())
    con.execute(
        "UPDATE dish SET status = ?, recipe_id = ?, error = NULL,"
        "                fetched_at = ? WHERE name = ?",
        (OK, int(recipe_id), jetzt, schluessel(frage)))
    con.commit()


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


def ohne_treffer(con: sqlite3.Connection) -> list[dict]:
    """Geholte Gerichte, zu denen keine Trefferliste mitgeschrieben wurde.

    Der Altbestand von WB-387: alles, was vor diesem Ticket geholt wurde, hat
    ein Rezept und keine Alternativen — die elf anderen wurden damals
    weggeworfen, und aus einem gespeicherten Rezept lassen sie sich nicht
    zurückgewinnen. Nachtragen kann sie nur ein neuer Abruf, und der gehört
    nicht in eine Migration: er geht ins Netz.

    Diese Liste ist deshalb das, was `lauf --ohne-treffer` von Hand
    nachholt — mit `PAUSE_S` zwischen den Gerichten, wie jeder andere Lauf.
    """
    rows = con.execute(
        f"{_DISH_SQL} WHERE status = ? AND recipe_id IS NOT NULL"
        "   AND id NOT IN (SELECT dish_id FROM dish_treffer)"
        " ORDER BY id", (OK,)).fetchall()
    return [dict(r) for r in rows]


def offene(con: sqlite3.Connection) -> list[dict]:
    """Die Wünsche, die noch niemand geholt hat — für den Lauf ohne Argument."""
    rows = con.execute(f"{_DISH_SQL} WHERE status = ? ORDER BY requested_at",
                       (OFFEN,)).fetchall()
    return [dict(r) for r in rows]
