"""Tests für den Knuspr-Crawler (WB-321).

KEIN Test geht ins Netz. Alle laufen gegen `tests/fixtures/knuspr_milch.json`,
eine echte, einmal von Hand aufgenommene Antwort (`scripts/record_fixture.py`).
"""
import json
from pathlib import Path

import pytest

from zettel import db
from zettel.scrapers import knuspr

FIXTURE = Path(__file__).parent / "fixtures" / "knuspr_milch.json"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.migrate(c)
    yield c
    c.close()


class FakeHTTP:
    """Antwortet auf Suchanfragen aus einer vorgegebenen Seitenliste.

    Bilder liefert er nie — der Crawl muss auch ohne durchlaufen.
    """

    def __init__(self, seiten):
        self.seiten = list(seiten)
        self.aufrufe = []

    def get(self, url):
        self.aufrufe.append(url)
        payload = self.seiten.pop(0) if self.seiten else {"data": {}}
        return _Antwort(payload)


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


def _seite(zeilen, total):
    return {"data": {"productList": zeilen, "totalHits": total}}


def _roh(pid, name, preis=1.19, level3="Frischmilch"):
    return {"productId": pid, "productName": name, "brand": "Marke",
            "price": {"full": preis, "currency": "€"},
            "pricePerUnit": {"full": preis, "currency": "€"},
            "textualAmount": "1 l", "unit": "l", "inStock": True,
            "imgPath": f"/images/{pid}.jpg",
            "categories": [{"id": 547, "name": level3, "level": 3},
                           {"id": 546, "name": "Milch", "level": 2},
                           {"id": 1, "name": "Molkerei", "level": 1}]}


# --------------------------------------------------------------------------
# parse_products gegen die echte Antwort

def test_echte_antwort_wird_geparst(payload):
    zeilen = knuspr.parse_products(payload)
    assert len(zeilen) > 10, "Format geändert? Fixture neu aufnehmen."
    assert knuspr.total_hits(payload) > 0


def test_preis_wird_zu_cent(payload):
    zeilen = {z["external_id"]: z for z in knuspr.parse_products(payload)}
    milch = zeilen.get("95793")
    assert milch is not None, "Referenzprodukt aus Spec-Anhang A fehlt"
    # 1.19 EUR -> 119 ct. Fliesskomma darf hier nichts verlieren.
    assert milch["price_cents"] == 119
    assert milch["name"] == "Miil Frische Landmilch 3,8% Vollmilch"
    assert milch["unit_text"] == "1 l"


def test_kategorien_dreistufig(payload):
    zeilen = {z["external_id"]: z for z in knuspr.parse_products(payload)}
    milch = zeilen["95793"]
    assert milch["category_l3"] == "Frischmilch"
    assert milch["category_l2"] == "Milch"
    assert milch["category_l1"]


def test_zeile_ohne_id_oder_name_wird_uebersprungen():
    kaputt = _seite([{"productName": "ohne id"},
                     {"productId": 7},
                     _roh(1, "gut")], 3)
    assert [z["external_id"] for z in knuspr.parse_products(kaputt)] == ["1"]


def test_fehlender_preis_ist_none_nicht_null():
    ohne = _roh(1, "Milch")
    ohne["price"] = {}
    zeilen = knuspr.parse_products(_seite([ohne], 1))
    # None heisst "unbekannt". 0 hiesse "geschenkt" — und würde im Shop stehen.
    assert zeilen[0]["price_cents"] is None


@pytest.mark.parametrize("text, unit, erwartet", [
    ("0,25 g", "g", "250 g"),        # Byodo Tagliatelle 250 g
    ("0,225 ml", "ml", "225 ml"),    # Develey Sauce
    ("0,27 g", "g", "270 g"),        # Piccolinis 9×30 g
    ("0,5 g", "g", "500 g"),
    ("0,05 g", "g", "50 g"),
    ("0,034 g", "g", "34 g"),
    ("0,2505 g", "g", "0,2505 g"),   # mehr als drei Stellen: bleibt stehen
    ("0,25 g", "kg", "0,25 g"),   # Einheit der Zeile widerspricht dem Text: Finger weg
    ("0,75 l", "l", "0,75 l"),       # richtig — Liter bleiben Liter
    ("0,7 kg", "kg", "0,7 kg"),      # richtig
    ("250 g", "g", "250 g"),         # schon in Ordnung
    ("1 l", "l", "1 l"),
    ("2 x 0,25 g", "g", "2 x 0,25 g"),  # Multipack: nicht angefasst, nie gesehen
    (None, "g", None),
    ("", "g", ""),
])
def test_eine_kilozahl_mit_grammeinheit_wird_zu_gramm(text, unit, erwartet):
    """UI-Review 2026-09-01, Fund 5. Knuspr liefert „0,25 g" für 250 g: die
    Zahl ist in kg, die Einheit blieb klein. 167 aktive Produkte in der Demo-
    Datenbank. Beleg sind die Produktnamen („… 250g", „9×30 g") und dass es
    Bruchteile eines Gramms im Lebensmittelhandel nicht gibt — deshalb ist
    „< 1 und kleine Einheit" das Merkmal. `price_cents /
    price_per_unit_cents` (239/956 = 0,25) ist dazu nur Plausibilität, kein
    Beweis: der Quotient zeigt, dass die Zahl in der Einheit des Grundpreis-
    Nenners steht, nicht welche das ist."""
    assert knuspr.normalisiere_einheit(text, unit) == erwartet


def test_parse_products_normalisiert_die_einheit():
    roh = _roh(7, "Tagliatelle")
    roh["textualAmount"] = "0,25 g"
    roh["unit"] = "g"
    zeile = knuspr.parse_products(_seite([roh], 1))[0]
    assert zeile["unit_text"] == "250 g"


def test_repariere_einheiten_bringt_bestehende_zeilen_in_ordnung(con):
    http = FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Nudeln")], 2)])
    knuspr.crawl(con, http, ["milch"], pause_s=0)
    con.execute("UPDATE product SET unit_text = '0,25 g', unit = 'g'"
                " WHERE name = 'Nudeln'")
    con.commit()

    n = knuspr.repariere_einheiten(con)

    assert n == 1
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Nudeln'"
                       ).fetchone()["unit_text"] == "250 g"
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Milch'"
                       ).fetchone()["unit_text"] == "1 l"
    assert knuspr.repariere_einheiten(con) == 0     # idempotent


# --------------------------------------------------------------------------
# Lauf

def test_lauf_fuellt_katalog(con):
    http = FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")], 2)])
    ergebnis = knuspr.crawl(con, http, ["milch"], pause_s=0)

    assert ergebnis["status"] == "ok"
    assert ergebnis["n_products"] == 2
    namen = [r["name"] for r in con.execute(
        "SELECT name FROM product WHERE active = 1 ORDER BY external_id")]
    assert namen == ["Milch", "Butter"]


def test_paginierung_folgt_total_hits(con):
    http = FakeHTTP([_seite([_roh(i, f"P{i}") for i in range(1, 3)], 4),
                     _seite([_roh(i, f"P{i}") for i in range(3, 5)], 4)])
    knuspr.crawl(con, http, ["milch"], pause_s=0)

    assert con.execute("SELECT count(*) AS n FROM product").fetchone()["n"] == 4
    assert "offset=0" in http.aufrufe[0]
    assert "offset=2" in http.aufrufe[1]


def test_verschwundenes_produkt_wird_inaktiv_nicht_geloescht(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")], 2)]),
                 ["milch"], pause_s=0)
    # Zweiter Lauf, Butter fehlt — aber genug Produkte, um nicht an der
    # Plausibilitätsschwelle zu scheitern.
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch")], 1)]),
                 ["milch"], pause_s=0)

    butter = con.execute(
        "SELECT active FROM product WHERE external_id = '2'").fetchone()
    assert butter is not None, "Produkt wurde gelöscht statt deaktiviert"
    assert butter["active"] == 0
    assert con.execute(
        "SELECT active FROM product WHERE external_id = '1'"
    ).fetchone()["active"] == 1


def test_halbierter_lauf_wird_verworfen_und_katalog_bleibt(con):
    voll = [_roh(i, f"P{i}") for i in range(1, 11)]
    knuspr.crawl(con, FakeHTTP([_seite(voll, 10)]), ["milch"], pause_s=0)
    assert con.execute(
        "SELECT count(*) AS n FROM product WHERE active = 1").fetchone()["n"] == 10

    # 4 von 10 — unter der 50-%-Schwelle.
    ergebnis = knuspr.crawl(con, FakeHTTP([_seite(voll[:4], 4)]),
                            ["milch"], pause_s=0)

    assert ergebnis["status"] == "rejected"
    assert "Katalog unverändert" in ergebnis["error"]
    # Der Katalog muss unangetastet sein — das ist der eigentliche Punkt.
    assert con.execute(
        "SELECT count(*) AS n FROM product WHERE active = 1").fetchone()["n"] == 10
    lauf = con.execute(
        "SELECT status, n_products FROM scrape_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert lauf["status"] == "rejected" and lauf["n_products"] == 4


def test_erster_lauf_wird_nie_verworfen(con):
    # Ohne Vergleichswert gibt es keine Schwelle — sonst kaeme der Katalog
    # nie in Gang.
    ergebnis = knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch")], 1)]),
                            ["milch"], pause_s=0)
    assert ergebnis["status"] == "ok"


def test_abbruch_mittendrin_laesst_katalog_intakt(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch")], 1)]),
                 ["milch"], pause_s=0)

    class Kaputt:
        def get(self, url):
            raise ConnectionError("Netz weg")

    ergebnis = knuspr.crawl(con, Kaputt(), ["milch"], pause_s=0)

    assert ergebnis["status"] == "error"
    assert "ConnectionError" in ergebnis["error"]
    assert con.execute(
        "SELECT count(*) AS n FROM product WHERE active = 1").fetchone()["n"] == 1


def test_jeder_lauf_wird_protokolliert(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch")], 1)]),
                 ["milch"], pause_s=0)
    lauf = con.execute("SELECT * FROM scrape_run ORDER BY id DESC LIMIT 1").fetchone()
    assert lauf["source"] == "knuspr"
    assert lauf["started_at"] and lauf["finished_at"]
    assert lauf["status"] == "ok"


def test_produkt_ist_nach_dem_lauf_ueber_fts_findbar(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Miil Frische Landmilch")], 1)]),
                 ["milch"], pause_s=0)
    n = con.execute(
        "SELECT count(*) AS n FROM product_fts WHERE product_fts MATCH ?",
        ("landmilch",)).fetchone()["n"]
    assert n == 1, "FTS-Trigger greift beim Crawl-Insert nicht"


def test_bilder_werden_lokal_abgelegt(con, tmp_path):
    class MitBild(FakeHTTP):
        def get(self, url):
            if "/images/" in url:
                a = _Antwort({})
                a.content = b"\xff\xd8jpegbytes"
                return a
            return super().get(url)

    http = MitBild([_seite([_roh(1, "Milch")], 1)])
    knuspr.crawl(con, http, ["milch"], pause_s=0, image_dir=tmp_path)

    pfad = con.execute(
        "SELECT image_path FROM product WHERE external_id='1'").fetchone()["image_path"]
    assert pfad and Path(pfad).exists()
    assert Path(pfad).read_bytes().startswith(b"\xff\xd8")
    # Kein Hotlink auf das CDN in der Datenbank.
    assert "cdn.knuspr.de" not in pfad


def test_fehlendes_bild_verhindert_kein_produkt(con, tmp_path):
    class OhneBild(FakeHTTP):
        def get(self, url):
            if "/images/" in url:
                raise ConnectionError("CDN weg")
            return super().get(url)

    knuspr.crawl(con, OhneBild([_seite([_roh(1, "Milch")], 1)]),
                 ["milch"], pause_s=0, image_dir=tmp_path)

    row = con.execute(
        "SELECT name, image_path FROM product WHERE external_id='1'").fetchone()
    assert row["name"] == "Milch"
    assert row["image_path"] is None


# --------------------------------------------------------------------------
# „1,5 ml" für eine 1,5-Liter-Flasche (2026-09-04)

@pytest.mark.parametrize("text, unit, preis, grundpreis, erwartet", [
    ("1,5 ml", "ml", 319, 213, "1,5 l"),    # Sagrotan „Frischetraum 1,5 L": 3,19 € / 1,5 = 2,13 €/l
    ("1 ml", "ml", 269, 269, "1 l"),        # Saft „1L": Grundpreis gleich Preis -> ein Liter
    ("1 g", "g", 2299, 2299, "1 kg"),       # Kaffeebohnen „1 kg"
    ("2,5 g", "g", 500, 200, "2,5 kg"),     # 5,00 € / 2,5 = 2,00 €/kg
    ("5 g", "g", 99, 19800, "5 g"),         # echte 5 g: 0,99 € * 1000 / 5 = 198 €/kg — bleibt
    ("1,5 ml", "ml", 319, None, "1,5 ml"),  # ohne Grundpreis kein Beleg: Finger weg
    ("1,5 ml", "ml", 319, 400, "1,5 ml"),   # Grundpreis passt zu keiner Lesart: Finger weg
    ("12 ml", "ml", 100, 8, "12 ml"),       # ab 10 greift die Regel nicht — 12 ml gibt es
    ("1,5 l", "l", 319, 213, "1,5 l"),      # schon richtig
])
def test_eine_kleine_zahl_mit_kleiner_einheit_ist_liter_oder_kilo_wenn_der_grundpreis_es_belegt(
        text, unit, preis, grundpreis, erwartet):
    """Das erste Produkt der Katalogseite hiess „Sagrotan … 1,5 L" und stand
    mit „1,5 ml" da. Knuspr liefert die Zahl in Litern und die Einheit in
    Millilitern — wie bei „0,25 g", nur ohne führende Null, deshalb griff
    die Regel oben nicht. Diesmal beweist der Grundpreis die Lesart: er ist
    je Kilo bzw. Liter angegeben, und `preis / zahl` trifft ihn genau dann,
    wenn die Zahl in Kilo bzw. Litern steht. Gemessen am 2026-09-04 an der
    Demo-Datenbank: 12 aktive Zeilen mit g/ml und Zahl < 10, alle 12 so
    belegt — und keine einzige echte Kleinstmenge, deren Grundpreis zur
    Gramm-Lesart (× 1000) passte."""
    assert knuspr.normalisiere_einheit(text, unit, preis, grundpreis) == erwartet


def test_repariere_einheiten_belegt_die_grosse_einheit_mit_dem_grundpreis(con):
    http = FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Reiniger")], 2)])
    knuspr.crawl(con, http, ["milch"], pause_s=0)
    con.execute("UPDATE product SET unit_text = '1,5 ml', unit = 'ml',"
                " price_cents = 319, price_per_unit_cents = 213"
                " WHERE name = 'Reiniger'")
    con.commit()

    assert knuspr.repariere_einheiten(con) == 1
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Reiniger'"
                       ).fetchone()["unit_text"] == "1,5 l"
    assert knuspr.repariere_einheiten(con) == 0     # idempotent


# --------------------------------------------------------------------------
# Nährwerte (2026-09-06): sie lagen immer schon in der Nutzlast

def _mit_naehrwert(pid, name, **werte):
    roh = _roh(pid, name)
    roh["composition"] = {
        "additiveScoreMax": 6, "withoutAdditives": True,
        "nutritionalValues": {"dose": "100 g", "energyValueKJ": 192.0,
                              "energyValueKcal": 46.0, "fats": 1.5,
                              "saturatedFattyAcids": 0.2,
                              "carbohydrates": 6.6, "sugars": 3.3,
                              "proteins": 0.8, "salt": 0.08, "fiber": 1.4,
                              **werte}}
    return roh


def test_die_fixture_von_anfang_an_traegt_naehrwerte(payload):
    # Der Punkt dieses Tests: die Daten sind nicht neu, nur der Parser ist es.
    zeilen = knuspr.parse_naehrwerte(payload)
    assert len(zeilen) > 10
    eine = zeilen[0]
    assert eine["kcal"] is not None and eine["protein"] is not None
    assert eine["dose"] == "100 g"


def test_produkt_ohne_composition_bekommt_keine_zeile():
    # Keine Angabe ist etwas anderes als eine Angabe voller NULL — Klopapier
    # hat keine Kalorien, und eine leere Zeile behauptete, es hätte welche.
    assert knuspr.parse_naehrwerte(_seite([_roh(1, "Klopapier")], 1)) == []


def test_leerer_naehrwertblock_ist_keine_angabe():
    roh = _roh(1, "Etwas")
    roh["composition"] = {"nutritionalValues": {"dose": "100 g"}}
    assert knuspr.parse_naehrwerte(_seite([roh], 1)) == []


def test_null_kalorien_sind_eine_angabe():
    # Mineralwasser hat 0 kcal. Das ist ein Wert und kein fehlender Wert.
    roh = _roh(1, "Mineralwasser")
    roh["composition"] = {"nutritionalValues": {"dose": "100 ml",
                                                "energyValueKcal": 0.0}}
    zeilen = knuspr.parse_naehrwerte(_seite([roh], 1))
    assert len(zeilen) == 1 and zeilen[0]["kcal"] == 0.0


def test_naehrwerte_landen_nach_dem_lauf_am_produkt(con):
    knuspr.crawl(con, FakeHTTP([_seite([_mit_naehrwert(1, "Milch"),
                                        _roh(2, "Klopapier")], 2)]),
                 ["milch"], pause_s=0)
    zeilen = con.execute("""
        SELECT p.name, n.kcal, n.protein, n.dose
          FROM product p JOIN product_naehrwert n ON n.product_id = p.id
    """).fetchall()
    assert len(zeilen) == 1, "Klopapier hat eine Nährwertzeile bekommen"
    assert zeilen[0]["name"] == "Milch"
    assert zeilen[0]["kcal"] == 46.0 and zeilen[0]["protein"] == 0.8


def test_ein_verworfener_lauf_hinterlaesst_keine_naehrwerte(con):
    voll = [_mit_naehrwert(i, f"P{i}") for i in range(1, 11)]
    knuspr.crawl(con, FakeHTTP([_seite(voll, 10)]), ["milch"], pause_s=0)
    # Ein zweiter Lauf mit geänderten Werten, der an der Schwelle scheitert.
    kaputt = [_mit_naehrwert(i, f"P{i}", energyValueKcal=999.0) for i in range(1, 5)]
    ergebnis = knuspr.crawl(con, FakeHTTP([_seite(kaputt, 4)]), ["milch"], pause_s=0)
    assert ergebnis["status"] == "rejected"
    assert con.execute(
        "SELECT count(*) AS n FROM product_naehrwert WHERE kcal = 999.0"
    ).fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Der additive Lauf: ein Nachtrag darf nichts abmelden

def test_additiver_lauf_meldet_nichts_ab(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")], 2)]),
                 ["milch"], pause_s=0)
    knuspr.crawl(con, FakeHTTP([_seite([_roh(3, "Weisskohl")], 1)]),
                 ["weisskohl"], pause_s=0, additiv=True)

    aktiv = {r["external_id"] for r in con.execute(
        "SELECT external_id FROM product WHERE active = 1")}
    assert aktiv == {"1", "2", "3"}


def test_additiver_lauf_wird_nicht_an_der_schwelle_verworfen(con):
    voll = [_roh(i, f"P{i}") for i in range(1, 11)]
    knuspr.crawl(con, FakeHTTP([_seite(voll, 10)]), ["milch"], pause_s=0)
    # Ein Nachtrag liefert IMMER weniger als der Vollcrawl. Genau deshalb
    # gilt die Schwelle für ihn nicht.
    ergebnis = knuspr.crawl(con, FakeHTTP([_seite([_roh(99, "Safran")], 1)]),
                            ["safran"], pause_s=0, additiv=True)
    assert ergebnis["status"] == "ok"
    assert con.execute(
        "SELECT count(*) AS n FROM product WHERE active = 1").fetchone()["n"] == 11


def test_vollcrawl_meldet_weiterhin_ab(con):
    # Die Gegenprobe zum additiven Lauf: das alte Verhalten bleibt.
    knuspr.crawl(con, FakeHTTP([_seite([_roh(i, f"P{i}") for i in range(1, 11)], 10)]),
                 ["milch"], pause_s=0)
    knuspr.crawl(con, FakeHTTP([_seite([_roh(i, f"P{i}") for i in range(1, 9)], 8)]),
                 ["milch"], pause_s=0)
    assert con.execute(
        "SELECT active FROM product WHERE external_id = '10'").fetchone()["active"] == 0


# --------------------------------------------------------------------------
# Die Produkt-Sitemap sagt, was FEHLT

SITEMAP = """<?xml version="1.0"?><urlset>
 <url><loc>https://www.knuspr.de/269-weisskohl-1-stk</loc></url>
 <url><loc>https://www.knuspr.de/2197-weihenstephan-butter</loc></url>
 <url><loc>https://www.knuspr.de/c533-milch-molkerei-butter</loc></url>
</urlset>"""


def test_sitemap_liefert_id_und_slug():
    assert knuspr.parse_sitemap(SITEMAP) == {
        "269": "weisskohl-1-stk", "2197": "weihenstephan-butter"}


def test_sitemap_ueberspringt_was_kein_produkt_ist():
    # `/c533-…` ist eine Kategorie und hat in der Produktliste nichts zu suchen.
    assert "c533" not in "".join(knuspr.parse_sitemap(SITEMAP))


def test_fehlende_ids_sind_die_differenz(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(2197, "Butter")], 1)]),
                 ["butter"], pause_s=0)
    assert knuspr.fehlende_ids(con, knuspr.parse_sitemap(SITEMAP)) == ["269"]


def test_ausgelistetes_produkt_gilt_nicht_als_luecke(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(269, "Weisskohl"),
                                        _roh(2197, "Butter")], 2)]),
                 ["kohl"], pause_s=0)
    knuspr.crawl(con, FakeHTTP([_seite([_roh(2197, "Butter")], 1)]),
                 ["butter"], pause_s=0)
    assert con.execute(
        "SELECT active FROM product WHERE external_id = '269'").fetchone()["active"] == 0
    # Bekannt und ausgelistet ist nicht dasselbe wie unbekannt: sonst holte
    # jeder Nachtrag dieselben verschwundenen Produkte wieder herein.
    assert knuspr.fehlende_ids(con, knuspr.parse_sitemap(SITEMAP)) == []


def test_zusatzstoffbewertung_allein_ist_keine_naehrwertangabe():
    # Sonst hiesse eine Zeile in `product_naehrwert` mal das eine und mal das
    # andere — und „wie viele Produkte haben Nährwerte" wäre nicht mehr
    # beantwortbar.
    roh = _roh(1, "Etwas")
    roh["composition"] = {"withoutAdditives": True, "additiveScoreMax": 6}
    assert knuspr.parse_naehrwerte(_seite([roh], 1)) == []


# --------------------------------------------------------------------------
# Was der Händler laut Sitemap noch führt, überlebt einen Vollcrawl

def test_vollcrawl_meldet_nicht_ab_was_die_sitemap_noch_fuehrt(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")], 2)]),
                 ["milch"], pause_s=0)
    # Ein Nachtrag holt ein Produkt, nach dem kein Begriff fragt.
    knuspr.crawl(con, FakeHTTP([_seite([_roh(269, "Weisskohl")], 1)]),
                 ["weisskohl"], pause_s=0, additiv=True)
    # Der nächste Vollcrawl findet es nicht — die Sitemap kennt es aber.
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")], 2)]),
                 ["milch"], pause_s=0, gefuehrt={"1", "2", "269"})
    assert con.execute(
        "SELECT active FROM product WHERE external_id = '269'"
    ).fetchone()["active"] == 1, "der Nachtrag wäre eine Nacht später weg"


def test_was_die_sitemap_nicht_mehr_fuehrt_wird_abgemeldet(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")], 2)]),
                 ["milch"], pause_s=0)
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch")], 1)]),
                 ["milch"], pause_s=0, gefuehrt={"1"})
    assert con.execute(
        "SELECT active FROM product WHERE external_id = '2'").fetchone()["active"] == 0


def test_eine_leere_sitemap_wird_nicht_geglaubt(con):
    # Der gefährlichste Fall des ganzen Moduls: eine leere Menge „geführter"
    # Produkte meldet in `uebernehmen` den KOMPLETTEN Katalog ab.
    knuspr.crawl(con, FakeHTTP([_seite([_roh(i, f"P{i}") for i in range(1, 11)], 10)]),
                 ["milch"], pause_s=0)
    assert knuspr.sitemap_glaubwuerdig(con, set()) is False
    assert knuspr.sitemap_glaubwuerdig(con, {"1", "2"}) is False
    assert knuspr.sitemap_glaubwuerdig(con, {str(i) for i in range(1, 11)}) is True


def test_erste_sitemap_ohne_katalog_gilt_wenn_etwas_drinsteht(con):
    assert knuspr.sitemap_glaubwuerdig(con, {"1"}) is True
    assert knuspr.sitemap_glaubwuerdig(con, set()) is False


def test_alte_staging_tabelle_haelt_einen_lauf_nicht_auf(con):
    # Genau der Abbruch vom 06.09.: die Zwischenablage stammte aus einem Lauf
    # vor der Spalte `ean` und der nächste Lauf brach mit „has no column" ab.
    con.execute("CREATE TABLE product_staging (source TEXT, external_id TEXT)")
    ergebnis = knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch")], 1)]),
                            ["milch"], pause_s=0)
    assert ergebnis["status"] == "ok"
    assert con.execute(
        "SELECT count(*) AS n FROM product").fetchone()["n"] == 1


def test_unmoegliche_kcal_werden_verworfen():
    # 1.935 kcal je 100 g gibt es nicht — dort stehen die Kilojoule.
    zeile = {"dose": "100 g", "kcal": 1935.0, "kj": 462.0}
    assert knuspr.verwirf_unmoegliche_kcal(zeile)["kcal"] is None


def test_die_kilojoule_bleiben_stehen():
    # Verworfen wird nur der unmögliche Wert, nicht die ganze Zeile: der
    # kJ-Wert ist in allen gemessenen Fällen plausibel.
    zeile = knuspr.verwirf_unmoegliche_kcal({"dose": "100 g", "kcal": 1935.0,
                                             "kj": 462.0})
    assert zeile["kj"] == 462.0


def test_vertauschte_werte_werden_nicht_repariert():
    # Das Tauschen sähe richtig aus und wäre doch eine Vermutung über den
    # Fehler des Händlers. Verworfen und gezählt, nicht geraten.
    zeile = knuspr.verwirf_unmoegliche_kcal({"dose": "100 g", "kcal": 1935.0,
                                             "kj": 462.0})
    assert zeile["kcal"] != 462.0


def test_hohe_kcal_bei_anderer_bezugsmenge_bleiben():
    # 1.200 kcal je Portion sind möglich; die Schranke gilt nur je 100 g.
    zeile = {"dose": "1 Portion", "kcal": 1200.0}
    assert knuspr.verwirf_unmoegliche_kcal(zeile)["kcal"] == 1200.0


def test_butter_bleibt_unangetastet():
    # 747 kcal je 100 g sind Butter und kein Fehler.
    assert knuspr.verwirf_unmoegliche_kcal(
        {"dose": "100 g", "kcal": 747.0})["kcal"] == 747.0
