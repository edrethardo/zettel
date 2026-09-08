"""Der Sammelabruf von knuspr.de — dieselben Produkte, ein anderes Schema.

Der Nachtlauf fragt die Suche (`search-metadata`) und bekommt, wonach die
Begriffsliste fragt. Am 2026-09-06 kam heraus, dass das die Hälfte des
Sortiments ist (`docs/superpowers/specs/2026-09-06-quellen-design.md`). Die
Website selbst holt ihre Produkte anders — über vier Endpunkte, die **je 100
Produkt-IDs auf einmal** beantworten und in ihrem eigenen JS-Bündel stehen
(`PRODUCTS_BATCH_SIZE = 100`, `path:"/api/v1/products/card"`):

    /api/v1/products/card?products=<id>&products=<id>&…&categoryType=normal
    /api/v1/products?products=…
    /api/v1/products/composition?products=…
    /api/v1/products/categories?products=…

**Warum ein zweites Modul und nicht ein zweiter Zweig in `knuspr.py`:** die
vier liefern *dieselben Waren in anderen Feldern* — `name` statt
`productName`, `image.path` statt `imgPath`, `prices.salePrice` statt
`price.full`, `stock.availabilityStatus` statt `inStock`, Kategorien ab
`level 0` statt ab `level 1`. Ein Parser, der beides kann, wäre eine Kette von
`or`-Ausdrücken, in der man nach dem nächsten Formatwechsel nicht mehr sähe,
welche Hälfte kaputt ist. Beide schreiben in dieselben Spalten (`knuspr.FELDER`
und `knuspr.NAEHRWERT_FELDER`) — die Datenbank kennt den Unterschied nicht.

**Was es hier zusätzlich gibt und in der Suche nicht:** `ean`, die Zutatenliste
und die Allergene. Die EAN ist der Schlüssel, dessen Fehlen im Quellen-Design
noch als Grund gegen Open Food Facts stand. Übernommen wird davon vorerst nur
die EAN: sie ist die Identität des Artikels. Zutaten und Allergene bleiben in
der Rohdatei, bis jemand eine Frage hat, die sie beantworten — eine Spalte,
die niemand liest, ist eine Zusage, die niemand pflegt.
"""
from __future__ import annotations

from zettel.scrapers import knuspr

#: Der Präfix, den die Kartenantwort vor den Bildpfad setzt. Er wird
#: abgeschnitten, damit `image_path` dieselbe Form hat wie aus der Suche
#: (`/images/grocery/…`) — sonst stünde in der einen Hälfte des Katalogs eine
#: vollständige CDN-Adresse, und die Bilder würden von dort geladen statt aus
#: `data/images`. Genau das schliesst Spec 5.2 aus.
CDN_PREFIX = knuspr.CDN

#: Knuspr-Name im Sammelabruf -> unsere Spalte. Die Werte stehen dort als
#: `{"amount": 442.0, "unit": "kCal"}`; die Einheit wird nicht übernommen,
#: weil sie je Feld feststeht und eine Spalte mit „g" in jeder Zeile nichts
#: sagt. Weicht sie ab, ist das ein Formatwechsel und kein Datenpunkt.
NAEHRWERT_NAMEN = {
    "energyKJ": "kj",
    "energyKCal": "kcal",
    "fats": "fett",
    "saturatedFats": "gesaettigt",
    "carbohydrates": "kohlenhydrate",
    "sugars": "zucker",
    "protein": "protein",
    "salt": "salz",
    "fiber": "ballaststoffe",
}


def _cents(wert) -> int | None:
    """`1.43` -> 143. Euro als Fliesskomma, gerechnet wird in Cent."""
    if wert in (None, ""):
        return None
    try:
        return int(round(float(wert) * 100))
    except (TypeError, ValueError):
        return None


def _bildpfad(karte: dict) -> str | None:
    bild = (karte or {}).get("image") or {}
    pfad = bild.get("path")
    if not pfad:
        return None
    return pfad[len(CDN_PREFIX):] if pfad.startswith(CDN_PREFIX) else pfad


def _kategorien(eintrag: dict) -> tuple[str | None, str | None, str | None]:
    """Die drei Ebenen. Hier zählt `level` ab 0, in der Suche ab 1.

    Zugeordnet wird über `level` und nicht über die Position — aus demselben
    Grund wie in `knuspr._kategorien`: die Kette ist nicht immer vollständig.
    """
    ebenen: dict[int, str] = {}
    for c in ((eintrag or {}).get("categories") or []):
        if not isinstance(c, dict) or not c.get("name"):
            continue
        try:
            lvl = int(c.get("level"))
        except (TypeError, ValueError):
            continue
        if lvl in (0, 1, 2):
            ebenen.setdefault(lvl + 1, c["name"])
    return ebenen.get(1), ebenen.get(2), ebenen.get(3)


def _verfuegbar(karte: dict) -> int:
    stock = (karte or {}).get("stock") or {}
    return 1 if stock.get("availabilityStatus") == "AVAILABLE" else 0


def parse_produkte(eintraege) -> list[dict]:
    """Sammelabruf-Zeilen -> Zeilen für `product_staging`.

    Eine Zeile ist `{"productId": …, "card": …, "product": …,
    "composition": …, "categories": …}`; jeder Teil darf `None` sein, weil er
    aus einem eigenen Abruf stammt. Ohne `card` und ohne `product` gibt es
    keinen Namen — dann wird die Zeile übersprungen, wie in `parse_products`.
    """
    zeilen = []
    for e in eintraege or []:
        if not isinstance(e, dict):
            continue
        karte = e.get("card") or {}
        produkt = e.get("product") or {}
        pid = e.get("productId") or karte.get("productId") or produkt.get("id")
        name = karte.get("name") or produkt.get("name")
        if pid in (None, "") or not name:
            continue
        preise = karte.get("prices") or {}
        # Der bezahlte Preis ist der Aktionspreis, wenn es einen gibt — genau
        # wie `price.full` in der Suche. `originalPrice` ist der Preis davor
        # und gehört nicht in eine Spalte, die „was kostet es" heisst.
        preis = preise.get("salePrice")
        if preis in (None, ""):
            preis = preise.get("originalPrice")
        l1, l2, l3 = _kategorien(e.get("categories"))
        einheit = karte.get("unit") or produkt.get("unit")
        text = karte.get("textualAmount") or produkt.get("textualAmount")
        preis_cents = _cents(preis)
        grundpreis_cents = _cents(preise.get("unitPrice"))
        zeilen.append({
            "source": knuspr.SOURCE,
            "external_id": str(pid),
            "name": name,
            "brand": produkt.get("brand") or karte.get("brand"),
            "price_cents": preis_cents,
            "price_per_unit_cents": grundpreis_cents,
            "unit_text": knuspr.normalisiere_einheit(text, einheit, preis_cents,
                                                     grundpreis_cents),
            "unit": einheit,
            "image_path": _bildpfad(karte),
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": l3,
            "in_stock": _verfuegbar(karte),
            "ean": ((e.get("composition") or {}).get("ean")) or None,
        })
    return zeilen


def parse_naehrwerte(eintraege) -> list[dict]:
    """Sammelabruf-Zeilen -> Zeilen für `naehrwert_staging`.

    `nutritionalValues` ist hier eine LISTE von Portionsangaben. Beobachtet
    wurde nie mehr als eine (5.868 Produkte, 2026-09-06); genommen wird
    deshalb die erste, und die Bezugsmenge steht als `dose` daneben, statt
    stillschweigend auf 100 g gerechnet zu werden.
    """
    zeilen = []
    for e in eintraege or []:
        if not isinstance(e, dict):
            continue
        comp = e.get("composition") or {}
        pid = e.get("productId") or comp.get("productId")
        werte_liste = comp.get("nutritionalValues") or []
        if pid in (None, "") or not werte_liste:
            continue
        erste = werte_liste[0] or {}
        werte = erste.get("values") or {}
        zeile = {"source": knuspr.SOURCE, "external_id": str(pid),
                 "dose": erste.get("portion")}
        for knuspr_name, spalte in NAEHRWERT_NAMEN.items():
            eintrag = werte.get(knuspr_name) or {}
            betrag = eintrag.get("amount") if isinstance(eintrag, dict) else None
            try:
                zeile[spalte] = None if betrag in (None, "") else float(betrag)
            except (TypeError, ValueError):
                zeile[spalte] = None
        # Die Zusatzstoffbewertung gibt es in diesem Abruf nicht. Sie bleibt
        # leer, statt aus etwas anderem gefolgert zu werden.
        zeile["ohne_zusatzstoffe"] = None
        zeile["zusatzstoff_score"] = None
        if not any(zeile[s] is not None for s in NAEHRWERT_NAMEN.values()):
            continue
        zeilen.append(knuspr.verwirf_unmoegliche_kcal(zeile))
    return zeilen
