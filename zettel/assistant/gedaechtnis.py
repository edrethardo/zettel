"""Was der Shop einmal gewählt hat, wählt er nicht noch einmal (WB-411).

Der Nutzer: „Speichere außerdem Suchergebnisse dass sie Instant kommen
können. Melde dann dass sie cached sind und gib die Möglichkeit neu zu
suchen." — und daneben: „Der Agent braucht 20 Sekunden oder so."

**Gemessen, woraus die zwanzig Sekunden bestehen** (Phoenix, Züge vom
2026-08-30, echte Box):

    plan.extract      Median 11,53 s
    plan.choose       Median 14,71 s
    catalog.search    Median  0,00 s   (264 Suchen zusammen 0,5 s)

Die Suche im eigenen Katalog kostet nichts. Was kostet, ist die WAHL — und
die ist je Begriff eine eigene, kleine Frage: „welches dieser zwanzig
Produkte ist ‚Tomatenmark'". Dieselbe Frage stellt der Shop immer wieder.

**Gemessen, wie oft** (sieben Lasagne-Rezepte im Bestand):

    Lasagne                                    9/15 = 60 % schon bekannt
    Lasagne Bolognese                          8/13 = 62 %
    Lasagne alla Bolognese mit Béchamelsoße    8/15 = 53 %
    Mama Marias Lasagne alla Bolognese         7/12 = 58 %
    Lasagne Bolognese - die Beste              4/11 = 36 %

    „Zwiebel" 6×, „Tomatenmark" 5×, „Butter" 5×, „Milch" 5×, „Mehl" 4×

Über den ganzen Rezeptbestand wiederholen sich 51 % der Zutatennamen.

**Der Schlüssel ist die KETTE und nicht ihr erstes Wort.** „Möhren" und
„Karotten" führen zu verschiedenen Produkten (WB-340); wer nur den genauesten
Begriff merkte, gäbe die Wahl einer Kette für die einer anderen aus.

**Was NICHT gemerkt wird.** Die Kandidatenliste. Sie hängt am Katalog und
wird bei jedem Zug neu gesucht — 0,00 s. Gemerkt wird nur die Entscheidung,
und sie gilt nur, solange das gewählte Produkt in der frisch gesuchten Liste
wieder vorkommt. Damit kann eine Erinnerung nie ein Produkt vorschlagen, das
die Suche heute gar nicht mehr anbietet.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from zettel import db
from zettel.assistant import plan

#: Woher eine Wahl stammt. Heute immer `MODELL`; `MENSCH` ist der Platz für
#: WB-341 — eine bestätigte Wahl soll die geratene später schlagen.
MODELL = "modell"
MENSCH = "mensch"


def schluessel(kette) -> str:
    """Die Begriffskette als ein Schlüssel — normalisiert wie die Suche.

    Dieselbe Faltung wie `speicher.schluessel` für Gerichte und aus demselben
    Grund: „Passierte Tomaten" und „passierte tomaten" sind eine Frage und
    nicht zwei. Die REIHENFOLGE bleibt bedeutsam, denn sie ist die Rangfolge
    der Kette.
    """
    teile = [" ".join(db.normalisiere(str(k or "").strip().casefold()).split())
             for k in (kette or [])]
    return "|".join(t for t in teile if t)


def _jetzt() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lesen(con: sqlite3.Connection, aufgaben: list[dict]) -> dict[str, dict]:
    """Die gemerkten Wahlen zu diesen Aufgaben, nach Schlüssel.

    Eine Zeile ohne Produkt und mit `gewaehlt = 1` gibt es nicht: fällt das
    Produkt aus dem Katalog, nimmt `ON DELETE CASCADE` die Zeile mit, und der
    Begriff ist wieder offen. Das ist der Unterschied zu `recipe_zuordnung`,
    wo die Zeile die POSITION hält und deshalb stehenbleiben muss.
    """
    schluessel_zu_aufgabe = {}
    for a in aufgaben:
        k = schluessel(a.get("suchbegriffe") or [a.get("begriff")])
        if k:
            schluessel_zu_aufgabe.setdefault(k, a)
    if not schluessel_zu_aufgabe:
        return {}
    platz = ", ".join("?" for _ in schluessel_zu_aufgabe)
    rows = con.execute(
        f"SELECT begriff, product_id, gewaehlt, quelle FROM begriff_wahl"
        f" WHERE begriff IN ({platz})",
        tuple(schluessel_zu_aufgabe)).fetchall()
    return {r["begriff"]: dict(r) for r in rows}


def teilen(aufgaben: list[dict], gemerkt: dict) -> tuple[list[dict], list[dict]]:
    """Trennt die Aufgaben in „steht schon da" und „muss gefragt werden".

    **Eine Erinnerung zählt nur, wenn ihr Produkt heute noch vorgelegt wird.**
    Sonst stünde in der Liste ein Produkt, das die Suche gerade nicht anbietet
    — und die Zusicherung aus `plan.choose` („gewählt wird nur, was vorlag")
    hätte eine Hintertür.
    """
    bekannt, offen = [], []
    for a in aufgaben:
        k = schluessel(a.get("suchbegriffe") or [a.get("begriff")])
        eintrag = gemerkt.get(k)
        if eintrag is None:
            offen.append(a)
            continue
        if not eintrag["gewaehlt"]:
            # Das Modell wollte hier nichts. Das ist eine Antwort und keine
            # Lücke — sie noch einmal zu bestellen, kostete bei jeder Zutat
            # ohne Katalogtreffer für immer einen Modellaufruf.
            bekannt.append((a, None))
            continue
        treffer = next((p for p in (a.get("aufgehoben") or [])
                        if int(p["id"]) == int(eintrag["product_id"])), None)
        if treffer is None:
            offen.append(a)
        else:
            bekannt.append((a, treffer))
    return bekannt, offen


def auswahl_aus(bekannt: list) -> plan.Auswahl:
    """Baut aus den gemerkten Paaren eine Wahl — ohne Modell."""
    # `gedaechtnis: True` reist mit bis in die Vorschlagszeile — daran hängt
    # der Satz „… kamen aus dem Gedächtnis" und der Knopf daneben. Ohne diese
    # Markierung wäre eine erinnerte Zeile von einer frisch gewählten nicht
    # mehr zu unterscheiden, und der Nutzer könnte nicht entscheiden, ob er
    # neu suchen will.
    gewaehlt = [{"begriff": a["begriff"], "produkt": p,
                 "menge": a.get("menge") or 1, "gedaechtnis": True}
                for a, p in bekannt if p is not None]
    return plan.Auswahl(gewaehlt=gewaehlt)


def merken(con: sqlite3.Connection, aufgaben: list[dict], auswahl,
           quelle: str = MODELL) -> int:
    """Schreibt die frisch getroffenen Wahlen ins Gedächtnis.

    Gemerkt wird zu JEDER gefragten Aufgabe etwas — auch das „nichts
    genommen". Eine Aufgabe ohne Kandidaten wird übergangen: dort ist gar
    nicht gewählt worden, und ein „nichts" daraus zu machen hiesse, den
    leeren Katalogtreffer für eine Entscheidung des Modells auszugeben.
    """
    gewaehlt = {w["begriff"]: w for w in getattr(auswahl, "gewaehlt", [])}
    jetzt = _jetzt()
    n = 0
    for a in aufgaben:
        if not a.get("kandidaten"):
            continue
        kette = a.get("suchbegriffe") or [a.get("begriff")]
        k = schluessel(kette)
        if not k:
            continue
        wahl = gewaehlt.get(a["begriff"])
        produkt = (wahl or {}).get("produkt") or {}
        con.execute(
            "INSERT INTO begriff_wahl (begriff, suchbegriffe, product_id,"
            " gewaehlt, quelle, benutzt, erstellt_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?)"
            " ON CONFLICT(begriff) DO UPDATE SET"
            "   product_id = excluded.product_id,"
            "   gewaehlt = excluded.gewaehlt, quelle = excluded.quelle,"
            "   erstellt_at = excluded.erstellt_at",
            (k, json.dumps([str(x) for x in kette], ensure_ascii=False),
             int(produkt["id"]) if produkt.get("id") else None,
             1 if wahl is not None else 0, quelle, jetzt))
        n += 1
    con.commit()
    return n


def benutzt(con: sqlite3.Connection, bekannt: list) -> None:
    """Zählt mit, wie oft eine Erinnerung getragen hat.

    Nicht Buchhaltung, sondern die Zahl, an der später zu sehen ist, ob das
    Gedächtnis überhaupt etwas bringt — und welche Begriffe es tragen.
    """
    for a, _ in bekannt:
        k = schluessel(a.get("suchbegriffe") or [a.get("begriff")])
        if k:
            con.execute("UPDATE begriff_wahl SET benutzt = benutzt + 1"
                        " WHERE begriff = ?", (k,))
    con.commit()


def vergessen(con: sqlite3.Connection, aufgaben: list[dict]) -> int:
    """Wirft die Erinnerungen zu diesen Begriffen weg — „neu suchen".

    Der Weg zurück, den der Nutzer verlangt hat: „gib die Möglichkeit neu zu
    suchen". Ohne ihn wäre eine einmal danebengegriffene Wahl für immer
    festgeschrieben, und das Gedächtnis wäre ein Käfig statt einer Abkürzung.
    """
    n = 0
    for a in aufgaben:
        k = schluessel(a.get("suchbegriffe") or [a.get("begriff")])
        if k:
            n += con.execute("DELETE FROM begriff_wahl WHERE begriff = ?",
                             (k,)).rowcount or 0
    con.commit()
    return n
