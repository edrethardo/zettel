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
    ean                  TEXT,
    PRIMARY KEY (source, external_id)
)
"""

FELDER = ("source", "external_id", "name", "brand", "price_cents",
          "price_per_unit_cents", "unit_text", "unit", "image_path",
          "category_l1", "category_l2", "category_l3", "in_stock", "ean")

NAEHRWERT_STAGING_DDL = """
CREATE TABLE IF NOT EXISTS naehrwert_staging (
    source            TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    dose              TEXT,
    kj                REAL,
    kcal              REAL,
    fett              REAL,
    gesaettigt        REAL,
    kohlenhydrate     REAL,
    zucker            REAL,
    protein           REAL,
    salz              REAL,
    ballaststoffe     REAL,
    ohne_zusatzstoffe INTEGER,
    zusatzstoff_score INTEGER,
    PRIMARY KEY (source, external_id)
)
"""

NAEHRWERT_FELDER = ("source", "external_id", "dose", "kj", "kcal", "fett",
                    "gesaettigt", "kohlenhydrate", "zucker", "protein", "salz",
                    "ballaststoffe", "ohne_zusatzstoffe", "zusatzstoff_score")

#: Knuspr-Name -> unser Spaltenname. Nur diese elf Werte werden übernommen;
#: was `nutritionalValues` sonst noch bringt, bleibt liegen, statt eine Spalte
#: zu bekommen, die niemand liest.
NAEHRWERT_NAMEN = {
    "dose": "dose",
    "energyValueKJ": "kj",
    "energyValueKcal": "kcal",
    "fats": "fett",
    "saturatedFattyAcids": "gesaettigt",
    "carbohydrates": "kohlenhydrate",
    "sugars": "zucker",
    "proteins": "protein",
    "salt": "salz",
    "fiber": "ballaststoffe",
}


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


#: Mehr Kalorien als reines Fett kann 100 g nicht haben — Fett liegt bei rund
#: 900 kcal je 100 g, und darüber gibt es kein Lebensmittel.
MAX_KCAL_JE_100 = 900.0

#: Bezugsmengen, für die diese Schranke überhaupt gilt. „100g" ohne Leerzeichen
#: kommt im Katalog genauso vor wie „100 g".
_JE_100 = ("100 g", "100g", "100 ml", "100ml")


def verwirf_unmoegliche_kcal(zeile: dict) -> dict:
    """Setzt `kcal` auf `None`, wenn der Wert je 100 g unmöglich ist.

    Gemessen am 2026-09-06: 13 von 9.414 Zeilen standen über 900 kcal je
    100 g. Bei den meisten sind **kJ und kcal vertauscht** (1.935 kJ = 462 kcal
    bei den Hafer-Kakao-Keksen), bei einem ist das Komma verrutscht (3.141
    statt 314,1 beim Ziegencamembert), einer ist die Angabe für eine ganze
    Box statt für 100 g.

    **Verworfen und nicht repariert** — dieselbe Regel wie bei einer
    erfundenen Produkt-ID (`plan.choose`). Das Vertauschen sieht offensichtlich
    aus, ist aber eine Vermutung über den Fehler des Händlers, und ein
    Wochenplan, der auf vermuteten Kalorien rechnet, ist schlechter als einer,
    der die Angabe fehlen lässt. `kj` bleibt stehen: dieser Wert ist in allen
    dreizehn Fällen plausibel, und wer will, rechnet ihn selbst um.
    """
    dose = (zeile.get("dose") or "").strip().lower()
    kcal = zeile.get("kcal")
    if kcal is not None and dose in _JE_100 and kcal > MAX_KCAL_JE_100:
        zeile = dict(zeile, kcal=None)
    return zeile


def _naehrwert(p: dict) -> dict | None:
    """`composition` eines Produkts -> eine Zeile, oder `None`.

    **Warum das hier steht und nicht in `parse_products`:** die Nährwerte
    gehören einer eigenen Tabelle (siehe `product_naehrwert` in `db.py`), und
    ein Produkt ohne Angabe soll dort KEINE Zeile bekommen — nicht eine mit
    lauter NULL. Wer die Zeile hat, hat eine Angabe; wer keine hat, hat keine.

    `None` gibt es deshalb in genau zwei Fällen: kein `composition`, oder ein
    `nutritionalValues` ohne einen einzigen Nährwert. Ein Block, in dem nur
    `dose` steht, ist keine Angabe — und `withoutAdditives` allein ist auch
    keine: sonst hiesse „hat eine Zeile in `product_naehrwert`" mal „hat
    Nährwerte" und mal „hat eine Zusatzstoffbewertung des Händlers", und
    genau diese Zweideutigkeit soll die eigene Tabelle vermeiden. Die beiden
    Zusatzstoff-Spalten sind Beifang der Zeile, nie ihr Grund.
    """
    comp = p.get("composition")
    if not isinstance(comp, dict):
        return None
    werte = comp.get("nutritionalValues")
    if not isinstance(werte, dict):
        werte = {}
    zeile = {}
    for knuspr_name, spalte in NAEHRWERT_NAMEN.items():
        wert = werte.get(knuspr_name)
        if wert in (None, ""):
            zeile[spalte] = None
            continue
        if spalte == "dose":
            zeile[spalte] = str(wert)
            continue
        try:
            zeile[spalte] = float(wert)
        except (TypeError, ValueError):
            # Eine Zahl, die keine ist, wird verworfen und nicht geraten.
            zeile[spalte] = None
    ohne = comp.get("withoutAdditives")
    score = comp.get("additiveScoreMax")
    zeile["ohne_zusatzstoffe"] = None if ohne is None else int(bool(ohne))
    try:
        zeile["zusatzstoff_score"] = None if score is None else int(score)
    except (TypeError, ValueError):
        zeile["zusatzstoff_score"] = None
    hat_naehrwert = any(zeile[s] is not None
                        for s in NAEHRWERT_NAMEN.values() if s != "dose")
    return zeile if hat_naehrwert else None


def parse_naehrwerte(payload: dict) -> list[dict]:
    """Antwort -> Zeilen für `naehrwert_staging`. Reine Funktion, kein Netz.

    Bewusst ein zweiter Durchgang über dieselbe Nutzlast statt eines zweiten
    Rückgabewerts an `parse_products`: dessen Zeilen gehen Spalte für Spalte
    nach `product_staging`, und ein Fremdkörper darin fiele beim nächsten
    `executemany` auf die Füsse.
    """
    daten = (payload or {}).get("data") or {}
    zeilen = []
    for p in daten.get("productList") or []:
        if not isinstance(p, dict):
            continue
        pid = p.get("productId")
        if pid in (None, "") or not p.get("productName"):
            continue
        werte = _naehrwert(p)
        if werte is None:
            continue
        zeilen.append(verwirf_unmoegliche_kcal(
            {"source": SOURCE, "external_id": str(pid), **werte}))
    return zeilen


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
            # Die Suche liefert keine EAN — der Sammelabruf schon
            # (`knuspr_api`). `None` heisst hier „diese Antwort weiss es
            # nicht" und darf eine bekannte EAN nicht löschen; dafür sorgt
            # das COALESCE im Upsert.
            "ean": None,
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
# Die Produkt-Sitemap: was der Händler führt, ohne dass jemand Begriffe rät
#
# Gemessen am 2026-09-06 (`docs/superpowers/specs/2026-09-06-quellen-design.md`):
# `sitemap_products.xml` nennt 15.161 Produkte, der eigene Katalog kannte davon
# 8.773. Die Sitemap ist damit die einzige Stelle im ganzen Projekt, die sagt,
# was FEHLT — die Suche kann das nicht, sie findet nur, wonach gefragt wurde.
#
# Erlaubt: `knuspr.de/robots.txt` sperrt allein `/regal/*`, und eine Sitemap
# ist die ausdrückliche Einladung, sie zu lesen.

SITEMAP_URL = f"{BASE}/sitemap_products.xml"

#: Wie viel des bekannten Katalogs eine Sitemap mindestens nennen muss, damit
#: sie als gelesen gilt. **Ohne diese Schwelle ist die Sitemap die
#: gefährlichste Datei im Projekt:** eine leere Antwort ergäbe eine leere
#: Menge, und eine leere Menge „geführter" Produkte meldet in `uebernehmen`
#: den gesamten Katalog ab — lautlos, in einer Transaktion, nachts.
#: Dieselbe Begründung wie bei `MIN_ANTEIL`, nur ist der Schaden hier grösser.
SITEMAP_MIN_ANTEIL = 0.5


def sitemap_glaubwuerdig(con, gefuehrt) -> bool:
    """Nennt diese Sitemap genug vom bekannten Katalog, um ihr zu folgen?

    Beim allerersten Lauf gibt es nichts zu vergleichen; dann entscheidet
    allein, dass überhaupt etwas drinsteht.
    """
    bekannt = con.execute(
        "SELECT count(*) AS n FROM product WHERE source = ? AND active = 1",
        (SOURCE,)).fetchone()["n"]
    if not gefuehrt:
        return False
    return len(gefuehrt) >= bekannt * SITEMAP_MIN_ANTEIL

#: `https://www.knuspr.de/269-weisskohl-1-stk` -> ("269", "weisskohl-1-stk").
#: Die ID steht in der URL; genau deshalb reicht EINE Anfrage, um die
#: vollständige Artikelliste zu kennen.
_SITEMAP_URL_MUSTER = re.compile(
    r"<loc>\s*https://www\.knuspr\.de/(\d+)-([^<\s]*)\s*</loc>")


def parse_sitemap(xml: str) -> dict[str, str]:
    """Sitemap-XML -> `{produkt_id: slug}`. Reine Funktion, kein Netz.

    Kein XML-Parser: die Datei ist 1,8 MB flaches `<url><loc>`, und ein
    Muster über den Text hat hier keinen Nachteil ausser dem, dass es ein
    Muster ist. Zeilen, die nicht auf ein Produkt zeigen (Kategorien, Marken),
    passen nicht und fallen still weg — sie stehen in einer anderen Sitemap.
    """
    return {pid: slug for pid, slug in _SITEMAP_URL_MUSTER.findall(xml or "")}


def hole_sitemap(http) -> dict[str, str]:
    """Eine Anfrage. `http.get(url).text` muss das XML liefern."""
    antwort = http.get(SITEMAP_URL)
    text = getattr(antwort, "text", None)
    if text is None:
        text = (getattr(antwort, "content", b"") or b"").decode("utf-8", "replace")
    return parse_sitemap(text)


def fehlende_ids(con, sitemap: dict[str, str]) -> list[str]:
    """Welche Produkte der Sitemap der eigene Katalog nicht kennt.

    Gefragt wird gegen ALLE Zeilen der Quelle, auch die inaktiven: ein
    ausgelistetes Produkt ist bekannt und soll nicht als Lücke wieder
    hereinkommen. Sortiert nach ID, damit ein abgebrochener Nachtrag
    wiederholbar an derselben Stelle weitermacht.
    """
    haben = {str(r["external_id"]) for r in con.execute(
        "SELECT external_id FROM product WHERE source = ?", (SOURCE,))}
    return sorted((i for i in sitemap if i not in haben), key=int)


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
    """Die Zwischenablagen frisch anlegen — verworfen und neu, nicht geleert.

    `CREATE TABLE IF NOT EXISTS` sieht eine vorhandene Tabelle gar nicht an:
    eine Staging-Tabelle aus der Zeit vor einer neuen Spalte behielte ihre
    alten Spalten und der nächste Lauf bräche mit „has no column named …" ab
    (2026-09-06 genau so passiert, als `ean` dazukam). Bei einer Tabelle mit
    dauerhaften Daten wäre DROP die falsche Antwort — hier ist es die
    richtige: zwischen zwei Läufen hat Staging keinen Inhalt, der jemandem
    gehört. Deshalb steht diese Tabelle auch nicht in `db.SCHEMA`.
    """
    con.execute("DROP TABLE IF EXISTS product_staging")
    con.execute("DROP TABLE IF EXISTS naehrwert_staging")
    con.execute(STAGING_DDL)
    con.execute(NAEHRWERT_STAGING_DDL)


def _staging_schreiben(con, zeilen) -> None:
    spalten = ", ".join(FELDER)
    platz = ", ".join("?" for _ in FELDER)
    con.executemany(
        f"INSERT OR REPLACE INTO product_staging ({spalten}) VALUES ({platz})",
        [tuple(z[f] for f in FELDER) for z in zeilen])


def _naehrwerte_schreiben(con, zeilen) -> None:
    if not zeilen:
        return
    spalten = ", ".join(NAEHRWERT_FELDER)
    platz = ", ".join("?" for _ in NAEHRWERT_FELDER)
    con.executemany(
        f"INSERT OR REPLACE INTO naehrwert_staging ({spalten}) VALUES ({platz})",
        [tuple(z[f] for f in NAEHRWERT_FELDER) for z in zeilen])


def uebernehmen(con, jetzt: str, *, additiv: bool = False,
                gefuehrt: set[str] | None = None) -> int:
    """Staging -> `product`, in EINER Transaktion.

    Nichts wird gelöscht: Produkte, die im Lauf fehlten, werden `active = 0`.
    Ein DELETE würde Verweise aus alten Bestellungen und Rezepten zerreissen
    (Spec 5.3).

    **`additiv` lässt genau diese Abmeldung weg.** Ein Lauf, der absichtlich
    nur einen Ausschnitt geholt hat (die Differenz zur Produkt-Sitemap, eine
    Handprobe mit `--begriff`), weiss nichts über die Produkte, die er NICHT
    angefragt hat — und darf sie deshalb auch nicht für verschwunden erklären.
    Der Vollcrawl weiss es und tut es weiter; er ist die einzige Stelle, an
    der „war im Lauf nicht dabei" gleich „gibt es nicht mehr" bedeutet.

    **`gefuehrt` ist die Korrektur an genau diesem Schluss.** Er stimmt nur,
    solange der Lauf das ganze Sortiment abfragt — und das tut er nicht: die
    Begriffsliste holt einen Ausschnitt (2026-09-06 gemessen: 8.773 von
    15.161). Ohne diese Menge meldet der nächste Vollcrawl jedes Produkt
    wieder ab, das ein Nachtrag geholt hat, weil kein Begriff danach fragt —
    der Nachtrag wäre eine Nacht später weg. Wer die Produkt-Sitemap gelesen
    hat, weiss dagegen, was der Händler noch führt, und meldet nur ab, was
    dort NICHT mehr steht. Ohne `gefuehrt` bleibt es beim alten Verhalten.
    """
    spalten = ", ".join(FELDER)
    with con:
        if not additiv:
            if gefuehrt is None:
                con.execute("UPDATE product SET active = 0 WHERE source = ?",
                            (SOURCE,))
            else:
                # Eine temporäre Tabelle statt eines `NOT IN (?, ?, …)` mit
                # 15.000 Platzhaltern: SQLite hat eine Obergrenze für
                # Variablen, und sie liegt darunter.
                con.execute("CREATE TEMP TABLE IF NOT EXISTS gefuehrt_ids"
                            " (external_id TEXT PRIMARY KEY)")
                con.execute("DELETE FROM gefuehrt_ids")
                con.executemany("INSERT OR IGNORE INTO gefuehrt_ids VALUES (?)",
                                [(str(i),) for i in gefuehrt])
                con.execute(
                    "UPDATE product SET active = 0 WHERE source = ?"
                    " AND external_id NOT IN (SELECT external_id FROM gefuehrt_ids)",
                    (SOURCE,))
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
                ean                  = COALESCE(excluded.ean, product.ean),
                last_seen_at         = excluded.last_seen_at,
                active               = 1
        """, (jetzt,))
        # In DERSELBEN Transaktion wie die Produkte: eine Nährwertzeile ohne
        # ihr Produkt wäre eine Waise, und ein Produkt, dessen Nährwerte aus
        # einem verworfenen Lauf stammen, wäre schlimmer als keine Angabe.
        _naehrwerte_uebernehmen(con, jetzt)
        n = con.execute(
            "SELECT count(*) AS n FROM product WHERE source = ? AND active = 1",
            (SOURCE,)).fetchone()["n"]
    return int(n)


def _naehrwerte_uebernehmen(con, jetzt: str) -> int:
    """`naehrwert_staging` -> `product_naehrwert`, über den Verbund.

    Eigene Funktion, weil es zwei Aufrufer gibt: den Lauf, der auch Produkte
    schreibt, und den Nachtrag, der NUR Nährwerte holt
    (`/api/v1/products/composition` für Produkte, die längst im Katalog
    stehen). Beide schreiben dieselbe Tabelle auf demselben Weg — der Verbund
    über `(source, external_id)` sorgt dafür, dass eine Nährwertzeile ohne ihr
    Produkt gar nicht erst entsteht.
    """
    cur = con.execute(f"""
            INSERT INTO product_naehrwert (
                product_id, dose, kj, kcal, fett, gesaettigt, kohlenhydrate,
                zucker, protein, salz, ballaststoffe, ohne_zusatzstoffe,
                zusatzstoff_score, gesehen_at)
            SELECT p.id, s.dose, s.kj, s.kcal, s.fett, s.gesaettigt,
                   s.kohlenhydrate, s.zucker, s.protein, s.salz,
                   s.ballaststoffe, s.ohne_zusatzstoffe, s.zusatzstoff_score, ?
              FROM naehrwert_staging s
              JOIN product p ON p.source = s.source
                            AND p.external_id = s.external_id
            WHERE true
            ON CONFLICT (product_id) DO UPDATE SET
                dose = excluded.dose, kj = excluded.kj, kcal = excluded.kcal,
                fett = excluded.fett, gesaettigt = excluded.gesaettigt,
                kohlenhydrate = excluded.kohlenhydrate,
                zucker = excluded.zucker, protein = excluded.protein,
                salz = excluded.salz, ballaststoffe = excluded.ballaststoffe,
                ohne_zusatzstoffe = excluded.ohne_zusatzstoffe,
                zusatzstoff_score = excluded.zusatzstoff_score,
                gesehen_at = excluded.gesehen_at
        """, (jetzt,))
    return cur.rowcount


def crawl(con: sqlite3.Connection, http, begriffe, *,
          image_dir: str | Path | None = None,
          pause_s: float = PAUSE_S,
          schlafen=time.sleep,
          jetzt: str | None = None,
          additiv: bool = False,
          gefuehrt: set[str] | None = None) -> dict:
    """Ein vollständiger Katalog-Lauf.

    `http` muss ein `.get(url)` anbieten, dessen Ergebnis `.json()` und
    `.content` kennt — httpx erfüllt das, Tests reichen einen Doppelgänger
    herein. Gibt eine Zusammenfassung mit `status` zurück:
    `ok` | `rejected` | `error`.

    **`additiv` macht aus dem Lauf einen Nachtrag** (siehe `uebernehmen`): die
    Schwelle `MIN_ANTEIL` entfällt, und nichts wird abgemeldet. Sie muss
    entfallen, weil ein Nachtrag per Definition weniger liefert als der letzte
    Vollcrawl — sonst würde ausgerechnet der Lauf verworfen, der eine Lücke
    schliessen soll. Der Nachtlauf setzt das Flag nie.
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
                _naehrwerte_schreiben(con, parse_naehrwerte(payload))
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
    if not additiv and vorher and n_neu < vorher * MIN_ANTEIL:
        return abschluss(
            "rejected", n_neu,
            f"nur {n_neu} Produkte gegenüber {vorher} im letzten guten Lauf "
            f"(Schwelle {MIN_ANTEIL:.0%}) — Katalog unverändert gelassen")

    n_aktiv = uebernehmen(con, jetzt, additiv=additiv, gefuehrt=gefuehrt)
    return abschluss("ok", n_aktiv)
