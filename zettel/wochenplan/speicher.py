"""Der Plan in der Datenbank: anlegen, Tage belegen, entscheiden, laden.

Drei Tabellen (`plan`, `plan_tag`, `plan_bestand`, siehe `db.py`) und keine
vierte. Vor allem keine, in der ein Vorrat über den Plan hinaus lebt: der
Bestand hängt am Plan und fällt mit ihm — das ist die Entscheidung aus
Abschnitt 3 des Entwurfs („Bestand ist kein Lagerstand"), strukturell
gemacht.

`decision` an Tag und Bestandszeile ist dieselbe Spalte mit derselben
Bedeutung wie an `chat_suggestion`: `offen` bis ein Mensch tippt, dann `kept`
oder `removed`, und `offen` ist der Rückweg. Am Tag ist das zugleich das
Eval-Label des Planers (Abschnitt 7).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from zettel import db
from zettel.assistant.zugrezept import gesamtzeit, zeitsatz
from zettel.wochenplan.rahmen import Rahmen

ENTWURF = "entwurf"
IM_KORB = "im_korb"

ERKLAERT = "erklaert"
AUS_BON = "aus_bon"


class WochenplanFehler(RuntimeError):
    """Etwas, das die Oberfläche als Satz zeigt — kein Fehler des Shops."""


def _jetzt() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _pruefe_entscheidung(entscheidung: str) -> None:
    if entscheidung not in db.DECISIONS:
        raise WochenplanFehler(
            f"„{entscheidung}“ ist keine Entscheidung — erlaubt sind "
            + ", ".join(db.DECISIONS) + ".")


# --------------------------------------------------------------------------
# Anlegen

def anlegen(con: sqlite3.Connection, rahmen: Rahmen, *,
            von: date | str | None = None, jetzt: str | None = None) -> int:
    """Legt einen Plan mit `rahmen.tage` leeren Tagen an. Gibt die id zurück.

    Der erklärte Bestand kommt mit — als `kept`: was jemand hingeschrieben
    hat, ist bestätigt. Nur was der Bon vorschlägt (`aus_bon`), wartet auf
    ein Ja (Phase 3).

    `von` ist Tag 1; ohne Angabe heute. Ein Plan, der „morgen" beginnen soll,
    bekommt das Datum vom Aufrufer — hier wird nicht geraten, wann gekocht
    wird.
    """
    if isinstance(von, str):
        von = date.fromisoformat(von)
    start = von or date.today()
    stempel = jetzt or _jetzt()
    cur = con.execute(
        "INSERT INTO plan (created_at, von, tage, personen, max_minuten,"
        " budget_cents, kcal_ziel, rahmen_text, satz, vorlieben, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (stempel, start.isoformat(), int(rahmen.tage), rahmen.personen,
         rahmen.max_minuten, rahmen.budget_cents, rahmen.kcal_ziel,
         rahmen.text, rahmen.satz, rahmen.vorlieben, ENTWURF))
    plan_id = int(cur.lastrowid)
    for pos in range(int(rahmen.tage)):
        con.execute(
            "INSERT INTO plan_tag (plan_id, pos, datum, portionen)"
            " VALUES (?, ?, ?, ?)",
            (plan_id, pos, (start + timedelta(days=pos)).isoformat(),
             rahmen.personen))
    for b in rahmen.bestand:
        con.execute(
            "INSERT INTO plan_bestand (plan_id, name, menge, einheit,"
            " herkunft, decision, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan_id, b["name"], b.get("menge"), b.get("einheit"), ERKLAERT,
             "kept", stempel))
    con.commit()
    return plan_id


# --------------------------------------------------------------------------
# Tage

def _tag(con: sqlite3.Connection, tag_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM plan_tag WHERE id = ?",
                      (int(tag_id),)).fetchone()
    if row is None:
        raise WochenplanFehler(f"Tag {tag_id} gibt es nicht.")
    return row


def tag_setzen(con: sqlite3.Connection, tag_id: int, recipe_id,
               portionen=None, *, grund: str | None = None) -> dict:
    """Ein Rezept an einen Tag — von Hand oder vom Modell.

    Setzt die Entscheidung auf `offen` zurück: ein anderes Rezept ist ein
    neuer Vorschlag, und ein altes „Ja" gälte sonst für etwas, das niemand
    gesehen hat. `portionen` bleibt, wenn keine mitkommt — die Zahl gehört
    dem Tag, nicht dem Rezept.
    """
    tag = _tag(con, tag_id)
    rid = int(recipe_id) if recipe_id not in (None, "", 0, "0") else None
    if rid is not None and con.execute(
            "SELECT 1 FROM recipe WHERE id = ?", (rid,)).fetchone() is None:
        raise WochenplanFehler(f"Rezept {rid} gibt es nicht.")
    if portionen in (None, ""):
        port = tag["portionen"]
    else:
        try:
            port = max(1, int(str(portionen).strip()))
        except (TypeError, ValueError):
            port = tag["portionen"]
    con.execute(
        "UPDATE plan_tag SET recipe_id = ?, portionen = ?, auswaerts = 0,"
        " grund = ?, decision = 'offen', decided_at = NULL WHERE id = ?",
        (rid, port, grund, int(tag_id)))
    con.commit()
    return dict(_tag(con, tag_id))


def auswaerts_setzen(con: sqlite3.Connection, tag_id: int) -> dict:
    """„Mittwoch esse ich auswärts": kein Rezept, und der Tag ist entschieden.

    `kept`, nicht `offen`: auswärts ist eine Festlegung des Menschen und keine
    Frage an ihn. Beim Neuplanen (Phase 2) bleibt der Tag stehen.
    """
    _tag(con, tag_id)
    con.execute(
        "UPDATE plan_tag SET recipe_id = NULL, auswaerts = 1, grund = NULL,"
        " decision = 'kept', decided_at = ? WHERE id = ?",
        (_jetzt(), int(tag_id)))
    con.commit()
    return dict(_tag(con, tag_id))


def tag_entscheiden(con: sqlite3.Connection, tag_id: int,
                    entscheidung: str) -> dict:
    """`kept`, `removed` — oder `offen` als Rückweg, wie überall."""
    _pruefe_entscheidung(entscheidung)
    _tag(con, tag_id)
    con.execute(
        "UPDATE plan_tag SET decision = ?, decided_at = ? WHERE id = ?",
        (entscheidung, None if entscheidung == "offen" else _jetzt(),
         int(tag_id)))
    con.commit()
    return dict(_tag(con, tag_id))


# --------------------------------------------------------------------------
# Bestand

def bestand_hinzufuegen(con: sqlite3.Connection, plan_id: int, name: str,
                        menge=None, einheit=None, *, product_id=None,
                        herkunft: str = ERKLAERT, receipt_item_id=None,
                        decision: str | None = None) -> int:
    """Eine Bestandszeile. Erklärtes ist `kept`, Vorgeschlagenes `offen`."""
    if herkunft not in (ERKLAERT, AUS_BON):
        raise WochenplanFehler(f"„{herkunft}“ ist keine Herkunft.")
    name = " ".join(str(name or "").split())
    if not name:
        raise WochenplanFehler("Ein Bestand ohne Namen ist keiner.")
    if decision is None:
        decision = "kept" if herkunft == ERKLAERT else "offen"
    _pruefe_entscheidung(decision)
    cur = con.execute(
        "INSERT INTO plan_bestand (plan_id, product_id, name, menge, einheit,"
        " herkunft, receipt_item_id, decision, decided_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(plan_id), product_id, name, menge, einheit, herkunft,
         receipt_item_id, decision,
         None if decision == "offen" else _jetzt()))
    con.commit()
    return int(cur.lastrowid)


def bestand_entscheiden(con: sqlite3.Connection, bestand_id: int,
                        entscheidung: str) -> dict:
    _pruefe_entscheidung(entscheidung)
    cur = con.execute(
        "UPDATE plan_bestand SET decision = ?, decided_at = ? WHERE id = ?",
        (entscheidung, None if entscheidung == "offen" else _jetzt(),
         int(bestand_id)))
    if cur.rowcount == 0:
        raise WochenplanFehler(f"Bestand {bestand_id} gibt es nicht.")
    con.commit()
    return dict(con.execute("SELECT * FROM plan_bestand WHERE id = ?",
                            (int(bestand_id),)).fetchone())


def bestand_entfernen(con: sqlite3.Connection, bestand_id: int) -> None:
    con.execute("DELETE FROM plan_bestand WHERE id = ?", (int(bestand_id),))
    con.commit()


# --------------------------------------------------------------------------
# Lesen

_REZEPT_SQL = (
    "SELECT r.id, r.name, r.servings, r.prep_minutes, r.cook_minutes,"
    "       r.rest_minutes, r.source, r.source_url,"
    "       (SELECT count(*) FROM recipe_ingredient i"
    "         WHERE i.recipe_id = r.id) AS n_rezeptzutaten,"
    "       (SELECT count(*) FROM recipe_item ri"
    "         WHERE ri.recipe_id = r.id) AS n_zutaten"
    "  FROM recipe r WHERE r.id = ?")


def rezeptkopf(con: sqlite3.Connection, recipe_id) -> dict | None:
    """Name, Portionen, Zeit — was eine Tageszeile über ihr Rezept zeigt."""
    if recipe_id is None:
        return None
    row = con.execute(_REZEPT_SQL, (int(recipe_id),)).fetchone()
    if row is None:
        return None
    k = dict(row)
    k["minuten"] = gesamtzeit(k)
    k["zeitsatz"] = zeitsatz(k["minuten"])
    k["n_zutaten"] = k["n_rezeptzutaten"] or k["n_zutaten"]
    return k


def tage(con: sqlite3.Connection, plan_id: int) -> list[dict]:
    rows = con.execute("SELECT * FROM plan_tag WHERE plan_id = ? ORDER BY pos",
                       (int(plan_id),)).fetchall()
    fertig = []
    for r in rows:
        t = dict(r)
        t["rezept"] = rezeptkopf(con, t["recipe_id"])
        t["wochentag"] = date.fromisoformat(t["datum"]).weekday()
        t["offen"] = t["decision"] == "offen"
        t["behalten"] = t["decision"] == "kept"
        # Ein Tag ZÄHLT für die Einkaufsliste, wenn ein Rezept dransteht und
        # niemand „Nein" gesagt hat. `offen` zählt mit — der Plan steht so
        # da, und eine Liste, die offene Tage wegliesse, sähe leerer aus als
        # der Plan darüber.
        t["zaehlt"] = bool(t["recipe_id"]) and t["decision"] != "removed"
        fertig.append(t)
    return fertig


def bestand(con: sqlite3.Connection, plan_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT b.*, p.name AS produkt_name, p.unit_text,"
        "       r.bought_on AS gekauft_am, r.store AS laden"
        "  FROM plan_bestand b"
        "  LEFT JOIN product p ON p.id = b.product_id"
        "  LEFT JOIN receipt_item i ON i.id = b.receipt_item_id"
        "  LEFT JOIN receipt r ON r.id = i.receipt_id"
        " WHERE b.plan_id = ? ORDER BY b.herkunft, b.id", (int(plan_id),)).fetchall()
    fertig = []
    for r in rows:
        b = dict(r)
        b["offen"] = b["decision"] == "offen"
        b["behalten"] = b["decision"] == "kept"
        fertig.append(b)
    return fertig


def laden(con: sqlite3.Connection, plan_id: int) -> dict:
    """Der ganze Plan: Kopf, Tage, Bestand, Zusammenfassung. Wirft, wenn es
    ihn nicht gibt."""
    row = con.execute("SELECT * FROM plan WHERE id = ?",
                      (int(plan_id),)).fetchone()
    if row is None:
        raise WochenplanFehler(f"Plan {plan_id} gibt es nicht.")
    p = dict(row)
    p["tage_liste"] = tage(con, plan_id)
    p["bestand"] = bestand(con, plan_id)
    p["bis"] = (date.fromisoformat(p["von"])
                + timedelta(days=max(0, int(p["tage"]) - 1))).isoformat()
    p["zusammenfassung"] = zusammenfassung(p)
    return p


def zusammenfassung(p: dict) -> dict:
    """Die gerechneten Zahlen über die Tage — nichts davon kommt vom Modell."""
    belegt = [t for t in p["tage_liste"] if t["recipe_id"]]
    minuten = [t["rezept"]["minuten"] for t in belegt
               if t["rezept"] and t["rezept"]["minuten"] is not None]
    grenze = p.get("max_minuten")
    return {
        "tage": len(p["tage_liste"]),
        "belegt": len(belegt),
        "auswaerts": sum(1 for t in p["tage_liste"] if t["auswaerts"]),
        "offen": sum(1 for t in belegt if t["offen"]),
        "behalten": sum(1 for t in belegt if t["behalten"]),
        "verworfen": sum(1 for t in belegt if t["decision"] == "removed"),
        "kochzeit": sum(minuten) if minuten else None,
        "kochzeit_satz": zeitsatz(sum(minuten)) if minuten else None,
        "zeit_unbekannt": len(belegt) - len(minuten),
        "ueber_zeit": (sum(1 for m in minuten if m > grenze)
                       if grenze else 0),
    }


def rahmen_von(plan: dict) -> Rahmen:
    """Der Rahmen eines geladenen Plans — für Vorlage und Zug."""
    return Rahmen(tage=plan["tage"], personen=plan["personen"],
                  max_minuten=plan["max_minuten"],
                  budget_cents=plan["budget_cents"],
                  kcal_ziel=plan.get("kcal_ziel"),
                  satz=plan.get("satz"), vorlieben=plan.get("vorlieben"))


def aktuell(con: sqlite3.Connection) -> dict | None:
    """Der jüngste Plan, oder `None`."""
    row = con.execute("SELECT id FROM plan ORDER BY id DESC LIMIT 1").fetchone()
    return laden(con, int(row["id"])) if row else None


def alle(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT p.*, (SELECT count(*) FROM plan_tag t"
        "              WHERE t.plan_id = p.id AND t.recipe_id IS NOT NULL)"
        "           AS belegt"
        "  FROM plan p ORDER BY p.id DESC").fetchall()
    return [dict(r) for r in rows]


def status_setzen(con: sqlite3.Connection, plan_id: int, status: str,
                  *, order_id=None, span_id=None) -> None:
    if status not in (ENTWURF, IM_KORB):
        raise WochenplanFehler(f"„{status}“ ist kein Zustand.")
    con.execute(
        "UPDATE plan SET status = ?,"
        " order_id = coalesce(?, order_id), span_id = coalesce(?, span_id)"
        " WHERE id = ?", (status, order_id, span_id, int(plan_id)))
    con.commit()


def loeschen(con: sqlite3.Connection, plan_id: int) -> None:
    con.execute("DELETE FROM plan WHERE id = ?", (int(plan_id),))
    con.commit()
