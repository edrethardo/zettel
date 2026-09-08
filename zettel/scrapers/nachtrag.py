"""Der Nachtrag: die Lücke zwischen Produkt-Sitemap und eigenem Katalog.

Der Vollcrawl holt, wonach die Begriffsliste fragt. Am 2026-09-06 gemessen
(`docs/superpowers/specs/2026-09-06-quellen-design.md`): Knuspr führt laut
`sitemap_products.xml` 15.161 Produkte, der eigene Katalog kannte davon 8.773 —
**6.388 fehlten**, darunter der echte Weißkohl (ID 269), den der Chat-Agent in
der Breitenmessung nicht fand, weil „weisskohl" auf keinem Einkaufszettel
stand. 44 der 106 Zutaten, die über 128 Gerichte kein Produkt fanden, stehen in
dieser Sitemap.

**Der Unterschied zum Vollcrawl ist nicht die Technik, sondern die Zusage.**
Ein Vollcrawl weiss, was es gibt, und darf deshalb abmelden, was er nicht mehr
gesehen hat. Ein Nachtrag fragt einen Ausschnitt ab und weiss über den Rest
nichts — er meldet nie etwas ab und wird nie an `MIN_ANTEIL` gemessen
(`knuspr.uebernehmen(additiv=True)`). Beides steht in `scrape_run` als eigener
Lauf, damit die Statusseite den Unterschied sieht.

Zwei Wege herein, derselbe Ausgang:

* `nachtragen(con, produkte)` — rohe Produktobjekte, wie sie die Knuspr-Antwort
  liefert. Das ist die Schnittstelle; woher sie kommen, ist dieser Datei egal.
  Beide Formate sind erlaubt und dürfen in derselben Liste stehen: die Suche
  (`knuspr.parse_products`) und der Sammelabruf (`knuspr_api.parse_produkte`).
  Welches vorliegt, entscheidet der Aufbau der Zeile, nicht ein Schalter.
* `aus_jsonl(con, pfad)` — dieselben Objekte, eines je Zeile, aus einer Datei.
  Der Weg für einen Abruf, der ausserhalb dieses Prozesses gelaufen ist: erst
  holen, ansehen, dann einspielen. Was einmal auf der Platte liegt, muss nicht
  noch einmal beim Händler geholt werden.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from zettel.scrapers import knuspr, knuspr_api


def _nur_composition(obj) -> bool:
    """Eine Zeile, die allein den `composition`-Abruf trägt.

    Der Fall, für den es diese Unterscheidung gibt: Produkte, die längst im
    Katalog stehen, aber über die Suche hereinkamen und deshalb weder EAN noch
    Nährwerte haben. Für sie wird NUR
    `/api/v1/products/composition` geholt — Name, Preis und Kategorie sind
    bekannt und sollen nicht aus einem zweiten Abruf überschrieben werden.
    Eine solche Zeile darf deshalb **kein Produkt anlegen und keines ändern**;
    sie ergänzt.
    """
    # `productName` ist das Unterscheidungsmerkmal, nicht `composition`: eine
    # Zeile aus der SUCHE trägt beides — Produkt und Nährwerte im selben
    # Objekt. Wer nur auf `composition` prüft, hält jedes Suchergebnis für
    # eine Ergänzung und legt kein einziges Produkt mehr an.
    return (isinstance(obj, dict)
            and isinstance(obj.get("composition"), dict)
            and not obj.get("productName")
            and not any(isinstance(obj.get(k), dict)
                        for k in ("card", "product", "categories")))


def _ist_sammelabruf(obj) -> bool:
    """Kommt diese Zeile aus `/api/v1/…` oder aus der Suche?

    Erkannt wird am AUFBAU und nicht an einem Kennzeichen, das wir selbst
    hineingeschrieben hätten: eine Sammelabruf-Zeile bündelt die Antworten
    mehrerer Endpunkte unter `card`/`product`/`composition`/`categories`, eine
    Suchzeile IST das Produkt und trägt `productName` direkt.
    """
    return isinstance(obj, dict) and any(
        isinstance(obj.get(k), dict) for k in ("card", "product", "categories"))


def _als_payload(produkte) -> dict:
    """Rohe Produktobjekte in die Form bringen, die die Parser erwarten.

    Absichtlich keine zweite Parserfamilie: `parse_products` und
    `parse_naehrwerte` sind an der echten Antwort getestet, und ein Nachtrag,
    der seine eigenen Felder liest, würde beim nächsten Formatwechsel anders
    kaputtgehen als der Nachtlauf.
    """
    return {"data": {"productList": list(produkte),
                     "totalHits": len(produkte)}}


def nachtragen(con: sqlite3.Connection, produkte, *,
               jetzt: str | None = None) -> dict:
    """Rohe Produktobjekte -> Katalog, additiv. Gibt den Laufbericht zurück."""
    jetzt = jetzt or time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = con.execute(
        "INSERT INTO scrape_run (source, started_at) VALUES (?, ?)",
        (knuspr.SOURCE, jetzt))
    run_id = cur.lastrowid
    con.commit()
    try:
        ergaenzung = [p for p in produkte if _nur_composition(p)]
        sammel = [p for p in produkte
                  if _ist_sammelabruf(p) and not _nur_composition(p)]
        suche = [p for p in produkte
                 if not _ist_sammelabruf(p) and not _nur_composition(p)]
        payload = _als_payload(suche)
        zeilen = knuspr.parse_products(payload) + knuspr_api.parse_produkte(sammel)
        naehrwerte = (knuspr.parse_naehrwerte(payload)
                      + knuspr_api.parse_naehrwerte(sammel)
                      + knuspr_api.parse_naehrwerte(ergaenzung))
        knuspr._staging_leeren(con)
        knuspr._staging_schreiben(con, zeilen)
        knuspr._naehrwerte_schreiben(con, naehrwerte)
        con.commit()
        n_aktiv = knuspr.uebernehmen(con, jetzt, additiv=True)
        ean_neu = _eans_nachtragen(con, ergaenzung)
        status, fehler = "ok", None
    except Exception as e:                      # noqa: BLE001 — wie im Crawl
        zeilen = naehrwerte = []
        n_aktiv = ean_neu = None
        status, fehler = "error", f"{type(e).__name__}: {e}"
    con.execute(
        "UPDATE scrape_run SET finished_at = ?, status = ?, n_products = ?,"
        " error = ? WHERE id = ?",
        (time.strftime("%Y-%m-%dT%H:%M:%S"), status, n_aktiv, fehler, run_id))
    con.commit()
    return {"run_id": run_id, "status": status, "n_products": n_aktiv,
            "error": fehler, "nachgetragen": len(zeilen),
            "mit_naehrwert": len(naehrwerte), "ean_nachgetragen": ean_neu}


def _eans_nachtragen(con, eintraege) -> int:
    """Die EAN an Produkte schreiben, die es schon gibt. Gibt die Zahl zurück.

    Nur dort, wo bisher keine steht: eine EAN ist die Kennung der Ware, sie
    ändert sich nicht, und eine abweichende zweite wäre ein Fund für einen
    Menschen und kein Grund, die erste zu überschreiben.
    """
    n = 0
    for e in eintraege:
        comp = e.get("composition") or {}
        ean = comp.get("ean")
        pid = e.get("productId") or comp.get("productId")
        if not ean or pid in (None, ""):
            continue
        n += con.execute(
            "UPDATE product SET ean = ? WHERE source = ? AND external_id = ?"
            " AND ean IS NULL", (str(ean), knuspr.SOURCE, str(pid))).rowcount
    con.commit()
    return n


def aus_jsonl(con: sqlite3.Connection, pfad: str | Path, *,
              jetzt: str | None = None) -> dict:
    """Ein Produktobjekt je Zeile. Leere und kaputte Zeilen werden gezählt.

    Eine kaputte Zeile bricht den Nachtrag NICHT ab: die Datei ist das
    Ergebnis eines Abrufs, der Stunden gedauert haben kann, und eine
    unlesbare Zeile darin ist kein Grund, die anderen wegzuwerfen. Wie viele
    es waren, steht im Bericht.
    """
    produkte, kaputt = [], 0
    for zeile in Path(pfad).read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            obj = json.loads(zeile)
        except json.JSONDecodeError:
            kaputt += 1
            continue
        if isinstance(obj, dict):
            produkte.append(obj)
        else:
            kaputt += 1
    bericht = nachtragen(con, produkte, jetzt=jetzt)
    bericht["gelesen"] = len(produkte)
    bericht["kaputte_zeilen"] = kaputt
    return bericht


def main(argv=None) -> int:
    """`python -m zettel.scrapers.nachtrag --jsonl data/knuspr_neu.jsonl`

    Kein Netz in diesem Einstiegspunkt, und das ist Absicht: der Abruf beim
    Händler und das Einspielen in die Datenbank sind zwei Schritte, damit
    zwischen ihnen jemand die Datei ansehen kann.
    """
    import argparse

    from zettel import db as datenbank
    from zettel import umgebung

    p = argparse.ArgumentParser(description="Fehlende Produkte nachtragen.")
    p.add_argument("--jsonl", required=True,
                   help="Datei mit einem rohen Produktobjekt je Zeile")
    p.add_argument("--db", default=None,
                   help=f"Vorgabe: $ZETTEL_DB oder {datenbank.DEFAULT_DB}")
    p.add_argument("--probe", action="store_true",
                   help="nur lesen und zählen, nichts schreiben")
    args = p.parse_args(argv)

    db_path = args.db or umgebung.wert("ZETTEL_DB") or datenbank.DEFAULT_DB
    con = datenbank.connect(db_path)
    try:
        datenbank.migrate(con)
        if args.probe:
            zeilen = [z for z in Path(args.jsonl).read_text(
                encoding="utf-8").splitlines() if z.strip()]
            sitemap_ids = {str(json.loads(z).get("productId")) for z in zeilen
                           if z.strip().startswith("{")}
            fehlen = knuspr.fehlende_ids(
                con, {i: "" for i in sitemap_ids if i != "None"})
            print(f"Datei: {len(zeilen)} Zeilen, {len(sitemap_ids)} Produkt-IDs")
            print(f"davon im Katalog noch nicht vorhanden: {len(fehlen)}")
            return 0
        bericht = aus_jsonl(con, args.jsonl)
    finally:
        con.close()
    print(f"Lauf {bericht['run_id']}: {bericht['status']}")
    print(f"  gelesen: {bericht['gelesen']} Produkte"
          f" ({bericht['kaputte_zeilen']} kaputte Zeilen)")
    print(f"  nachgetragen: {bericht['nachgetragen']},"
          f" davon mit Nährwerten: {bericht['mit_naehrwert']}")
    if bericht.get("ean_nachgetragen"):
        print(f"  EAN nachgetragen: {bericht['ean_nachgetragen']}")
    print(f"  Katalog danach aktiv: {bericht['n_products']}")
    if bericht["error"]:
        print(f"  Fehler: {bericht['error']}")
    return 0 if bericht["status"] == "ok" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
