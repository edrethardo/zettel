"""Die Zuordnung gehört dem REZEPT und nicht dem Zug (WB-408).

Der Nutzer hat den Anlass geliefert: „Das Wechseln von einem Vorschlag zum
anderen sollte quasi sofort funktionieren." Es dauerte 24 bis 27 Sekunden,
weil ein Wechsel denselben Satz noch einmal durch `chat.turn` schickte — also
durch beide Modellstufen, auch beim Zurückwechseln zu einem Rezept, das eine
Minute vorher schon gerechnet worden war.

**Das war ein Fehler im Entwurf und keine fehlende Optimierung.** Auf dem
Quellenweg hängen die beiden teuren Stufen an nichts, was ein Wechsel ändert:

    plan.zutatenbegriffe(zutaten, gericht, servings)   nur das REZEPT
    plan.choose("", aufgaben)                          nur BEGRIFFE + KATALOG

Der Satz geht seit WB-386 ausdrücklich NICHT mehr in Stufe 3 (er kostete dort
die ganze Zutatenliste, sobald das geholte Rezept nicht zu ihm passte). Damit
ist die ganze Modellarbeit eines Rezeptzugs eine **reine Funktion des Rezepts
und des Katalogs** — und sie wurde trotzdem bei jedem Zug neu bezahlt.

Also wird sie einmal gerechnet und danach gelesen. Was hier steht, ist die
Antwort des Modells zu genau diesem Rezept:

    suchbegriffe   die Kette aus Stufe 1 („passierte Tomaten", „Tomaten")
    menge          die Packungszahl aus Stufe 1
    product_id     was Stufe 3 daraus gewählt hat — oder NULL
    gewaehlt       ob Stufe 3 überhaupt gewählt hat

**`gewaehlt` und `product_id` sind zwei Fragen und keine.** `NULL` bei
`gewaehlt = 1` heisst „das Produkt gibt es nicht mehr" (`ON DELETE SET NULL`),
`gewaehlt = 0` heisst „das Modell hat zu diesem Begriff nichts genommen". Das
erste wird beim nächsten Zug neu gefragt, das zweite nicht — sonst kostete
jede Zutat ohne Katalogtreffer für immer einen Modellaufruf.

**Was hier NICHT steht.** Alles, was ohnehin ohne Modell entsteht: `bedarf`,
`einheit` und die Herkunftszutat kommen aus `assistant.herkunft` und werden
beim Lesen neu gerechnet. Eine gespeicherte Kopie davon wäre eine zweite
Wahrheit, die still veralten kann.

**Und der Rest des Satzes gehört nicht dazu.** „alles für Lasagne, und
Klopapier" — das Klopapier ist keine Zutat dieses Rezepts und wird weiter je
Zug behandelt (`_rest_sichern`). Ein Begriff ohne gemerkte Zuordnung geht
ganz normal an das Modell; gelesen wird, was da ist.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from zettel.assistant import plan


def _jetzt() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lesen(con: sqlite3.Connection, recipe_id: int) -> list[dict] | None:
    """Die gemerkte Zuordnung eines Rezepts — oder `None`.

    `None` heisst „für dieses Rezept ist noch nichts gerechnet". Eine leere
    Liste gibt es nicht: ein Rezept ohne einen einzigen Begriff wäre kein
    Ergebnis, sondern ein Fehlschlag, und der wird nicht gemerkt.

    Die Reihenfolge ist die von Stufe 1. Sie trägt keine Bedeutung für die
    Suche, aber die Vorschlagsliste steht in ihr da, und eine Liste, die sich
    bei jedem Aufruf anders sortiert, wäre eine andere Antwort.
    """
    rows = con.execute(
        "SELECT pos, suchbegriffe, menge, product_id, wahl_menge, gewaehlt"
        "  FROM recipe_zuordnung WHERE recipe_id = ? ORDER BY pos",
        (int(recipe_id),)).fetchall()
    if not rows:
        return None
    gemerkt = []
    for r in rows:
        try:
            kette = json.loads(r["suchbegriffe"])
        except (TypeError, ValueError):
            # Eine Zeile, die sich nicht lesen lässt, ist keine Zuordnung.
            # Sie fällt heraus und wird beim nächsten Zug neu gefragt — das
            # ist billiger als ein Zug, der an ihr zerbricht.
            continue
        if not kette:
            continue
        gemerkt.append({"suchbegriffe": [str(k) for k in kette],
                        "menge": r["menge"],
                        "product_id": r["product_id"],
                        "wahl_menge": r["wahl_menge"],
                        "gewaehlt": bool(r["gewaehlt"])})
    return gemerkt or None


def schreiben(con: sqlite3.Connection, recipe_id: int,
              begriffe: list[dict], auswahl) -> int:
    """Merkt die Antwort beider Modellstufen zu einem Rezept.

    Gibt zurück, wie viele Zeilen entstanden sind. Ersetzt, was dastand: eine
    neue Rechnung zu demselben Rezept ist die gültige.

    **Geschrieben wird nur, was das Modell wirklich geliefert hat.** Ein
    Notbehelf aus `chefkoch.zutat_kette` (Stufe 1 ausgefallen) hat hier
    nichts verloren — er würde sich als gemerkte Modellantwort ausgeben und
    das Rezept für immer auf der schlechteren Zerlegung festhalten. Der
    Aufrufer entscheidet das; diese Funktion schreibt, was sie bekommt.
    """
    gewaehlt = {w["begriff"]: w for w in getattr(auswahl, "gewaehlt", [])}
    con.execute("DELETE FROM recipe_zuordnung WHERE recipe_id = ?",
                (int(recipe_id),))
    jetzt = _jetzt()
    n = 0
    for pos, b in enumerate(begriffe or []):
        kette = [str(k) for k in (b.get("suchbegriffe") or []) if k]
        if not kette:
            continue
        wahl = gewaehlt.get(kette[0])
        produkt = (wahl or {}).get("produkt") or {}
        con.execute(
            "INSERT INTO recipe_zuordnung (recipe_id, pos, suchbegriffe,"
            " menge, product_id, wahl_menge, gewaehlt, erstellt_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(recipe_id), pos, json.dumps(kette, ensure_ascii=False),
             b.get("menge"),
             int(produkt["id"]) if produkt.get("id") else None,
             (wahl or {}).get("menge"), 1 if wahl is not None else 0, jetzt))
        n += 1
    con.commit()
    return n


def vergessen(con: sqlite3.Connection, recipe_id: int) -> None:
    """Wirft die Zuordnung weg — das Rezept ist ein anderes geworden.

    Aufgerufen, wo die Zutatenliste neu geschrieben wird (`speicher.merken`).
    Eine Zuordnung zu Zutaten, die es nicht mehr gibt, wäre schlimmer als
    keine: sie sähe gültig aus.
    """
    con.execute("DELETE FROM recipe_zuordnung WHERE recipe_id = ?",
                (int(recipe_id),))


def begriffe_aus(gemerkt: list[dict]) -> list[dict]:
    """Die Ketten für Stufe 2, in der Form, die Stufe 1 geliefert hätte."""
    return [{"suchbegriffe": list(g["suchbegriffe"]), "menge": g["menge"]}
            for g in gemerkt]


def offen(gemerkt: list[dict], aufgaben: list[dict]) -> list[dict]:
    """Die Aufgaben, zu denen NICHTS gemerkt ist — sie brauchen das Modell.

    Zwei Fälle führen hierher, und beide sind gewollt:

    * der Rest des Satzes („und Klopapier") — er gehört keinem Rezept
    * ein Produkt, das aus dem Katalog verschwunden ist (`product_id` ist
      `NULL`, obwohl gewählt wurde)

    Ein Begriff, zu dem das Modell ausdrücklich NICHTS genommen hat, steht
    nicht darin. Er wäre sonst der teuerste Eintrag der Liste: jede Zutat
    ohne Katalogtreffer kostete bei jedem Zug aufs Neue einen Modellaufruf,
    und die Antwort wäre jedes Mal dieselbe.
    """
    bekannt = {g["suchbegriffe"][0]: g for g in gemerkt if g["suchbegriffe"]}
    fehlt = []
    for a in aufgaben:
        g = bekannt.get(a["begriff"])
        if g is None or (g["gewaehlt"] and g["product_id"] is None):
            fehlt.append(a)
    return fehlt


def auswahl_aus(gemerkt: list[dict], aufgaben: list[dict]) -> plan.Auswahl:
    """Baut die Wahl aus dem Gemerkten — ohne Modell.

    **Das Produkt wird unter den vorgelegten Kandidaten gesucht**, und nicht
    aus der Produkttabelle geladen. Der Grund ist derselbe wie in
    `plan.choose`: an dem vorgelegten Kandidaten hängen `via` (über welchen
    Begriff der Kette er kam, WB-340) und `rang` — beides steht später an der
    Vorschlagszeile und an der Eval-Annotation. Ein direkt geladenes Produkt
    trüge dort eine Lücke, wo eine Erklärung stehen soll.

    Steht das gemerkte Produkt nicht mehr in den Kandidaten, gilt es als
    nicht gewählt: dann hat sich der Katalog bewegt, und `offen()` schickt
    denselben Begriff an das Modell.
    """
    nach_begriff = {g["suchbegriffe"][0]: g for g in gemerkt
                    if g["suchbegriffe"]}
    auswahl = plan.Auswahl()
    for a in aufgaben:
        g = nach_begriff.get(a["begriff"])
        if g is None or not g["gewaehlt"] or not g["product_id"]:
            continue
        treffer = next((k for k in (a.get("aufgehoben") or [])
                        if int(k["id"]) == int(g["product_id"])), None)
        if treffer is None:
            continue
        auswahl.gewaehlt.append({"begriff": a["begriff"], "produkt": treffer,
                                 "menge": g["wahl_menge"] or g["menge"] or 1})
    return auswahl


def verschmelzen(gemerkt_auswahl, frische_auswahl) -> plan.Auswahl:
    """Gemerkte und frisch gewählte Zeilen zu EINER Wahl.

    Die frische gewinnt bei gleichem Begriff — sie ist die jüngere Antwort
    auf dieselbe Frage. `verworfen` kommt nur aus der frischen: was gemerkt
    ist, wurde damals schon geprüft.
    """
    frische = list(getattr(frische_auswahl, "gewaehlt", []))
    neu_dabei = {w["begriff"] for w in frische}
    behalten = [w for w in getattr(gemerkt_auswahl, "gewaehlt", [])
                if w["begriff"] not in neu_dabei]
    # `Auswahl` ist eingefroren (`frozen=True`) — hier entsteht eine neue und
    # keine veränderte. Das ist kein Umweg, sondern der Grund, warum sie es
    # ist: eine Wahl, die nach der Prüfung noch bearbeitet werden könnte,
    # wäre keine Zusicherung mehr.
    return plan.Auswahl(
        gewaehlt=[*behalten, *frische],
        verworfen=list(getattr(frische_auswahl, "verworfen", [])),
        roh=getattr(frische_auswahl, "roh", ""))
