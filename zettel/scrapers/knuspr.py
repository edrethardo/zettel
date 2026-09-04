"""Katalog-Crawler für knuspr.de.

Reines HTTP, kein Browser. Die Schnittstelle ist gemessen, nicht geraten
(Spec-Anhang A):

    GET https://www.knuspr.de/services/frontend-service/search-metadata
        ?search=<begriff>&companyId=6&limit=200&offset=<n>
    -> 200, JSON, data.productList[], data.totalHits

Dieses Modul ist der einzige Teil des Projekts, der ins Netz geht — und es
läuft als eigener Prozess mit eigenem Timer, nie im Request-Pfad des Shops
(Spec 3). Fällt es aus, wird der Katalog alt; der Shop funktioniert weiter.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

BASE = "https://www.knuspr.de"
SEARCH_PATH = "/services/frontend-service/search-metadata"
CDN = "https://cdn.knuspr.de"

SOURCE = "knuspr"
COMPANY_ID = 6
PAGE_SIZE = 200

#: Ein Lauf, der weniger als diesen Anteil des letzten guten Laufs liefert,
#: wird verworfen. Eine Captcha-Wand oder ein Formatwechsel macht aus dem
#: Katalog sonst stillschweigend zwölf Produkte (Spec 5.3).
MIN_ANTEIL = 0.5

#: Pause zwischen Anfragen. Bei einem nächtlichen Lauf ist Tempo egal,
#: Höflichkeit nicht.
PAUSE_S = 1.5

STAGING_DDL = """
CREATE TABLE IF NOT EXISTS product_staging (
    source               TEXT NOT NULL,
    external_id          TEXT NOT NULL,
    name                 TEXT NOT NULL,
    brand                TEXT,
    price_cents          INTEGER,
    price_per_unit_cents INTEGER,
    unit_text            TEXT,
    unit                 TEXT,
    image_path           TEXT,
    category_l1          TEXT,
    category_l2          TEXT,
    category_l3          TEXT,
    in_stock             INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, external_id)
)
"""

FELDER = ("source", "external_id", "name", "brand", "price_cents",
          "price_per_unit_cents", "unit_text", "unit", "image_path",
          "category_l1", "category_l2", "category_l3", "in_stock")


def _cents(preis) -> int | None:
    """`{"full": 1.19, "currency": "€"}` -> 119.

    Knuspr liefert Euro als Fliesskommazahl. Gerechnet wird im Shop
    ausschliesslich in Cent, damit sich Rundungsfehler nicht aufaddieren.
    """
    if not isinstance(preis, dict):
        return None
    voll = preis.get("full")
    if voll in (None, ""):
        return None
    try:
        return int(round(float(voll) * 100))
    except (TypeError, ValueError):
        return None


def _kategorien(cats) -> tuple[str | None, str | None, str | None]:
    """Die drei Ebenen aus `categories`, nach `level` einsortiert.

    Knuspr liefert sie absteigend (level 3 zuerst) und nicht immer vollständig,
    deshalb wird nach `level` zugeordnet statt nach Position.
    """
    ebenen: dict[int, str] = {}
    for c in cats or []:
        if not isinstance(c, dict):
            continue
        try:
            lvl = int(c.get("level"))
        except (TypeError, ValueError):
            continue
        if lvl in (1, 2, 3) and c.get("name"):
            ebenen.setdefault(lvl, c["name"])
    return ebenen.get(1), ebenen.get(2), ebenen.get(3)


#: „0,25 g" — eine Zahl unter 1 mit der KLEINEN Einheit. Knuspr liefert
#: `textualAmount` bei 167 aktiven Produkten so (UI-Review 2026-09-01,
#: Fund 5); gemeint ist die Zahl in kg bzw. l, mitskaliert wurde die Einheit
#: nicht. Belegt ist das durch die Produktnamen selbst („Byodo Tagliatelle
#: … 250g", „Piccolinis 9×30 g") und dadurch, dass der Lebensmittelhandel
#: nichts unter 1 g oder 1 ml verkauft — Bruchteile eines Gramms gibt es
#: nicht. `price_cents / price_per_unit_cents` (239/956 = 0,25) passt zur
#: Kilo-Lesart, beweist sie aber nicht: der Quotient sagt nur, dass die Zahl
#: in derselben Einheit steht wie der Nenner des Grundpreises, nicht welche
#: das ist. Kilo und Liter („0,75 l") sind richtig und bleiben.
#: Knuspr schreibt durchweg das Komma, nie den Punkt — deshalb kennt das
#: Muster nur das Komma.
_KLEINE_EINHEIT_UNTER_EINS = re.compile(r"^0,(\d+)\s*(g|ml)$")

#: Dieselbe Verwechslung ohne führende Null (2026-09-04): „1,5 ml" für die
#: 1,5-Liter-Flasche, „1 g" für ein Kilo Kaffeebohnen — das erste Produkt der
#: Katalogseite stand so da. Hier hilft die Null-Regel nicht, dafür beweist
#: der GRUNDPREIS die Lesart, und zwar wirklich: Knuspr gibt ihn je Kilo bzw.
#: Liter an, und `price_cents / zahl` trifft ihn genau dann, wenn die Zahl in
#: Kilo bzw. Litern steht. Stünde sie in Gramm, läge der Quotient um den
#: Faktor 1000 daneben. Gemessen an der Demo-Datenbank: 12 aktive Zeilen mit
#: g/ml und Zahl < 10, alle 12 so belegt — und keine einzige echte
#: Kleinstmenge, deren Grundpreis zur Gramm-Lesart passte. Ab 10 greift die
#: Regel nicht mehr: 12 ml Aroma gibt es, 12 Liter Aroma nicht.
_KLEINE_ZAHL_KLEINE_EINHEIT = re.compile(r"^(\d+(?:,\d+)?)\s*(g|ml)$")
_GROSSE_EINHEIT = {"g": "kg", "ml": "l"}


def normalisiere_einheit(text, unit, price_cents=None, price_per_unit_cents=None):
    """`„0,25 g"` -> `„250 g"`; `„1,5 ml"` mit passendem Grundpreis -> `„1,5 l"`.

    Alles andere unverändert — auch `None`. Ohne Preise greift nur die
    Null-Regel; die Grosse-Einheit-Regel braucht den Beleg.
    """
    if not text:
        return text
    roh = str(text).strip()
    treffer = _KLEINE_EINHEIT_UNTER_EINS.match(roh)
    if treffer and unit in ("g", "ml"):
        nachkomma, einheit = treffer.groups()
        # „0,25" -> 250, „0,225" -> 225, „0,5" -> 500: die Nachkommastellen
        # auf drei auffüllen und als ganze Zahl lesen. Mehr als drei Stellen
        # gäbe Bruchteile von Gramm — die gibt es nicht, also bleibt so ein
        # Text stehen.
        if len(nachkomma) > 3:
            return text
        return f"{int(nachkomma.ljust(3, '0'))} {einheit}"
    treffer = _KLEINE_ZAHL_KLEINE_EINHEIT.match(roh)
    if not treffer or unit not in ("g", "ml"):
        return text
    if not price_cents or not price_per_unit_cents:
        return text
    zahl = float(treffer.group(1).replace(",", "."))
    if not 0 < zahl < 10:
        return text
    # Zwei Prozent oder drei Cent Spiel: Knuspr rundet den Grundpreis.
    if abs(price_cents / zahl - price_per_unit_cents) > max(3, 0.02 * price_per_unit_cents):
        return text
    return f"{treffer.group(1)} {_GROSSE_EINHEIT[treffer.group(2)]}"


def repariere_einheiten(con) -> int:
    """Bestehende Zeilen nachziehen. Gibt die Zahl der geänderten zurück.

    Der nächste Crawl täte es auch (`ON CONFLICT … unit_text = excluded`),
    aber der ist nicht sicher vor dem nächsten Blick auf die Seite.
    Idempotent: eine normalisierte Zeile trifft das Muster nicht mehr.
    """
    zeilen = con.execute(
        "SELECT id, unit_text, unit, price_cents, price_per_unit_cents"
        " FROM product WHERE unit IN ('g', 'ml')").fetchall()
    n = 0
    for z in zeilen:
        neu = normalisiere_einheit(z["unit_text"], z["unit"],
                                   z["price_cents"], z["price_per_unit_cents"])
        if neu != z["unit_text"]:
            con.execute("UPDATE product SET unit_text = ? WHERE id = ?",
                        (neu, z["id"]))
            n += 1
    con.commit()
    return n


def parse_products(payload: dict) -> list[dict]:
    """Antwort -> Liste von Zeilen für `product`. Reine Funktion, kein Netz."""
    daten = (payload or {}).get("data") or {}
    zeilen = []
    for p in daten.get("productList") or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("productId")
        name = p.get("productName")
        if pid in (None, "") or not name:
            # Ohne Kennung oder Namen ist der Eintrag im Katalog wertlos —
            # lieber überspringen als eine Zeile ohne Aussage anlegen.
            continue
        l1, l2, l3 = _kategorien(p.get("categories"))
        zeilen.append({
            "source": SOURCE,
            "external_id": str(pid),
            "name": name,
            "brand": p.get("brand"),
            "price_cents": _cents(p.get("price")),
            "price_per_unit_cents": _cents(p.get("pricePerUnit")),
            "unit_text": normalisiere_einheit(p.get("textualAmount"),
                                              p.get("unit"),
                                              _cents(p.get("price")),
                                              _cents(p.get("pricePerUnit"))),
            "unit": p.get("unit"),
            "image_path": p.get("imgPath"),   # wird beim Crawl lokalisiert
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": l3,
            "in_stock": 1 if p.get("inStock") else 0,
        })
    return zeilen


def total_hits(payload: dict) -> int:
    try:
        return int(((payload or {}).get("data") or {}).get("totalHits") or 0)
    except (TypeError, ValueError):
        return 0


def such_url(begriff: str, offset: int = 0, limit: int = PAGE_SIZE) -> str:
    from urllib.parse import urlencode
    q = urlencode({"search": begriff, "companyId": COMPANY_ID,
                   "limit": limit, "offset": offset})
    return f"{BASE}{SEARCH_PATH}?{q}"


# --------------------------------------------------------------------------
# Bilder

def hole_bild(http, img_path: str, ziel_dir: Path) -> str | None:
    """Lädt ein Produktbild herunter und gibt den lokalen Pfad zurück.

    Bilder werden lokal abgelegt und nicht per Hotlink eingebunden: sonst
    hängt der Shop im Tailnet an `cdn.knuspr.de` und sieht kaputt aus, sobald
    die URLs rotieren (Spec 5.2).
    """
    if not img_path:
        return None
    ziel_dir = Path(ziel_dir)
    ziel_dir.mkdir(parents=True, exist_ok=True)
    name = img_path.rsplit("/", 1)[-1]
    ziel = ziel_dir / name
    if ziel.exists():
        return str(ziel)
    try:
        antwort = http.get(f"{CDN}{img_path}")
        inhalt = getattr(antwort, "content", None)
        if not inhalt:
            return None
        ziel.write_bytes(inhalt)
        return str(ziel)
    except Exception:
        # Ein fehlendes Bild darf einen Katalogeintrag nicht verhindern.
        return None


# --------------------------------------------------------------------------
# Lauf

def _letzter_guter_lauf(con) -> int:
    row = con.execute(
        "SELECT n_products FROM scrape_run"
        " WHERE source = ? AND status = 'ok' AND n_products IS NOT NULL"
        " ORDER BY id DESC LIMIT 1", (SOURCE,)).fetchone()
    return int(row["n_products"]) if row else 0


def _staging_leeren(con) -> None:
    con.execute(STAGING_DDL)
    con.execute("DELETE FROM product_staging")


def _staging_schreiben(con, zeilen) -> None:
    spalten = ", ".join(FELDER)
    platz = ", ".join("?" for _ in FELDER)
    con.executemany(
        f"INSERT OR REPLACE INTO product_staging ({spalten}) VALUES ({platz})",
        [tuple(z[f] for f in FELDER) for z in zeilen])


def uebernehmen(con, jetzt: str) -> int:
    """Staging -> `product`, in EINER Transaktion.

    Nichts wird gelöscht: Produkte, die im Lauf fehlten, werden `active = 0`.
    Ein DELETE würde Verweise aus alten Bestellungen und Rezepten zerreissen
    (Spec 5.3).
    """
    spalten = ", ".join(FELDER)
    with con:
        con.execute("UPDATE product SET active = 0 WHERE source = ?", (SOURCE,))
        con.execute(f"""
            INSERT INTO product ({spalten}, last_seen_at, active)
            SELECT {spalten}, ?, 1 FROM product_staging
            -- `WHERE true` ist Pflicht, nicht Zierde: nach INSERT…SELECT kann
            -- SQLites Parser das ON von ON CONFLICT nicht vom ON eines Joins
            -- unterscheiden und bricht mit «near "DO": syntax error» ab.
            WHERE true
            ON CONFLICT (source, external_id) DO UPDATE SET
                name                 = excluded.name,
                brand                = excluded.brand,
                price_cents          = excluded.price_cents,
                price_per_unit_cents = excluded.price_per_unit_cents,
                unit_text            = excluded.unit_text,
                unit                 = excluded.unit,
                image_path           = excluded.image_path,
                category_l1          = excluded.category_l1,
                category_l2          = excluded.category_l2,
                category_l3          = excluded.category_l3,
                in_stock             = excluded.in_stock,
                last_seen_at         = excluded.last_seen_at,
                active               = 1
        """, (jetzt,))
        n = con.execute(
            "SELECT count(*) AS n FROM product WHERE source = ? AND active = 1",
            (SOURCE,)).fetchone()["n"]
    return int(n)


def crawl(con: sqlite3.Connection, http, begriffe, *,
          image_dir: str | Path | None = None,
          pause_s: float = PAUSE_S,
          schlafen=time.sleep,
          jetzt: str | None = None) -> dict:
    """Ein vollständiger Katalog-Lauf.

    `http` muss ein `.get(url)` anbieten, dessen Ergebnis `.json()` und
    `.content` kennt — httpx erfüllt das, Tests reichen einen Doppelgänger
    herein. Gibt eine Zusammenfassung mit `status` zurück:
    `ok` | `rejected` | `error`.
    """
    jetzt = jetzt or time.strftime("%Y-%m-%dT%H:%M:%S")
    cur = con.execute(
        "INSERT INTO scrape_run (source, started_at) VALUES (?, ?)",
        (SOURCE, jetzt))
    run_id = cur.lastrowid
    con.commit()

    def abschluss(status, n=None, fehler=None):
        con.execute(
            "UPDATE scrape_run SET finished_at = ?, status = ?, n_products = ?,"
            " error = ? WHERE id = ?",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), status, n, fehler, run_id))
        con.commit()
        return {"run_id": run_id, "status": status,
                "n_products": n, "error": fehler}

    try:
        _staging_leeren(con)
        gesehen: set[str] = set()

        for i, begriff in enumerate(begriffe):
            offset = 0
            while True:
                if (i or offset) and pause_s:
                    schlafen(pause_s)
                payload = http.get(such_url(begriff, offset)).json()
                zeilen = parse_products(payload)
                if not zeilen:
                    break
                neue = [z for z in zeilen if z["external_id"] not in gesehen]
                if image_dir:
                    for z in neue:
                        z["image_path"] = hole_bild(http, z["image_path"],
                                                    Path(image_dir))
                if neue:
                    _staging_schreiben(con, neue)
                    gesehen.update(z["external_id"] for z in neue)
                con.commit()

                offset += len(zeilen)
                if offset >= total_hits(payload):
                    break

        n_neu = con.execute(
            "SELECT count(*) AS n FROM product_staging").fetchone()["n"]
    except Exception as e:                      # noqa: BLE001 — bewusst breit
        # Ein Abbruch mitten im Lauf lässt `product` unangetastet: übernommen
        # wird erst unten, und nur am Stück.
        return abschluss("error", None, f"{type(e).__name__}: {e}")

    vorher = _letzter_guter_lauf(con)
    if vorher and n_neu < vorher * MIN_ANTEIL:
        return abschluss(
            "rejected", n_neu,
            f"nur {n_neu} Produkte gegenüber {vorher} im letzten guten Lauf "
            f"(Schwelle {MIN_ANTEIL:.0%}) — Katalog unverändert gelassen")

    n_aktiv = uebernehmen(con, jetzt)
    return abschluss("ok", n_aktiv)
