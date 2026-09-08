"""Tests für den Nachtrag (2026-09-06).

Wie bei `test_knuspr.py` geht kein Test ins Netz: der Nachtrag bekommt rohe
Produktobjekte herein, und woher sie kommen, ist ihm egal — genau das macht
ihn testbar.
"""
import json

import pytest

from zettel import db
from zettel.scrapers import knuspr, nachtrag


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.migrate(c)
    yield c
    c.close()


def _roh(pid, name, kcal=None):
    p = {"productId": pid, "productName": name, "brand": "Marke",
         "price": {"full": 1.19}, "pricePerUnit": {"full": 1.19},
         "textualAmount": "1 kg", "unit": "kg", "inStock": True,
         "imgPath": f"/images/{pid}.jpg",
         "categories": [{"id": 1, "name": "Gemüse", "level": 1}]}
    if kcal is not None:
        p["composition"] = {"nutritionalValues": {"dose": "100 g",
                                                  "energyValueKcal": kcal,
                                                  "proteins": 1.4}}
    return p


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, url):
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


def _seite(zeilen):
    return {"data": {"productList": zeilen, "totalHits": len(zeilen)}}


def test_nachtrag_legt_fehlende_produkte_an(con):
    bericht = nachtrag.nachtragen(con, [_roh(269, "Weißkohl 1 Stk")])
    assert bericht["status"] == "ok" and bericht["nachgetragen"] == 1
    zeile = con.execute(
        "SELECT name, active FROM product WHERE external_id = '269'").fetchone()
    assert zeile["name"] == "Weißkohl 1 Stk" and zeile["active"] == 1


def test_nachtrag_meldet_den_bestand_nicht_ab(con):
    knuspr.crawl(con, FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Butter")])]),
                 ["milch"], pause_s=0)
    nachtrag.nachtragen(con, [_roh(269, "Weißkohl")])
    aktiv = {r["external_id"] for r in con.execute(
        "SELECT external_id FROM product WHERE active = 1")}
    assert aktiv == {"1", "2", "269"}, "der Nachtrag hat den Katalog abgemeldet"


def test_nachtrag_schreibt_die_naehrwerte_mit(con):
    nachtrag.nachtragen(con, [_roh(269, "Weißkohl", kcal=25.0),
                              _roh(270, "Klopapier")])
    zeilen = con.execute("""
        SELECT p.external_id, n.kcal, n.protein
          FROM product p JOIN product_naehrwert n ON n.product_id = p.id
    """).fetchall()
    assert len(zeilen) == 1
    assert zeilen[0]["external_id"] == "269" and zeilen[0]["kcal"] == 25.0


def test_nachtrag_steht_als_eigener_lauf_im_protokoll(con):
    bericht = nachtrag.nachtragen(con, [_roh(269, "Weißkohl")])
    lauf = con.execute("SELECT id, status, n_products FROM scrape_run"
                       " ORDER BY id DESC LIMIT 1").fetchone()
    assert lauf["id"] == bericht["run_id"] and lauf["status"] == "ok"
    assert lauf["n_products"] == 1


def test_jsonl_wird_zeilenweise_gelesen(con, tmp_path):
    datei = tmp_path / "neu.jsonl"
    datei.write_text("\n".join([json.dumps(_roh(269, "Weißkohl", kcal=25.0)),
                                "", "{kaputt",
                                json.dumps(_roh(270, "Wirsing"))]),
                     encoding="utf-8")
    bericht = nachtrag.aus_jsonl(con, datei)
    assert bericht["gelesen"] == 2 and bericht["kaputte_zeilen"] == 1
    assert bericht["nachgetragen"] == 2 and bericht["mit_naehrwert"] == 1
    assert con.execute(
        "SELECT count(*) AS n FROM product").fetchone()["n"] == 2


def test_ein_zweiter_nachtrag_derselben_datei_aendert_nichts(con, tmp_path):
    datei = tmp_path / "neu.jsonl"
    datei.write_text(json.dumps(_roh(269, "Weißkohl", kcal=25.0)), encoding="utf-8")
    nachtrag.aus_jsonl(con, datei)
    nachtrag.aus_jsonl(con, datei)
    assert con.execute("SELECT count(*) AS n FROM product").fetchone()["n"] == 1
    assert con.execute(
        "SELECT count(*) AS n FROM product_naehrwert").fetchone()["n"] == 1


# --------------------------------------------------------------------------
# Der Sammelabruf: dieselben Waren, anderes Schema (2026-09-06)

def _sammel(pid, name, kcal=None, ean=None):
    zeile = {"productId": pid,
             "card": {"productId": pid, "name": name,
                      "image": {"path": f"https://cdn.knuspr.de/images/{pid}.jpg"},
                      "unit": "kg", "textualAmount": "100 g",
                      "prices": {"originalPrice": 1.59, "salePrice": 1.43,
                                 "unitPrice": 14.3},
                      "stock": {"availabilityStatus": "AVAILABLE"}},
             "product": {"id": pid, "name": name, "brand": "Alnatura"},
             "categories": {"categories": [
                 {"id": 1, "name": "Süßes", "level": 0},
                 {"id": 2, "name": "Gebäck", "level": 1},
                 {"id": 3, "name": "Cracker", "level": 2}]},
             "composition": None}
    if kcal is not None or ean is not None:
        zeile["composition"] = {"productId": pid, "ean": ean,
                                "nutritionalValues": ([{
                                    "portion": "100 g",
                                    "values": {"energyKCal": {"amount": kcal},
                                               "protein": {"amount": 8.9}}}]
                                    if kcal is not None else [])}
    return zeile


def test_sammelabruf_wird_am_aufbau_erkannt(con):
    bericht = nachtrag.nachtragen(con, [_sammel(10002, "Knabber Eulen",
                                                kcal=442.0, ean="4104420256873")])
    assert bericht["nachgetragen"] == 1 and bericht["mit_naehrwert"] == 1
    z = con.execute("SELECT * FROM product WHERE external_id = '10002'").fetchone()
    assert z["name"] == "Knabber Eulen" and z["brand"] == "Alnatura"
    assert z["price_cents"] == 143, "der Aktionspreis ist der bezahlte Preis"
    assert z["price_per_unit_cents"] == 1430
    assert z["category_l1"] == "Süßes" and z["category_l3"] == "Cracker"
    assert z["ean"] == "4104420256873"


def test_bildpfad_wird_vom_cdn_befreit(con):
    # Sonst stünde in der einen Hälfte des Katalogs eine CDN-Adresse und die
    # Bilder kämen von dort statt aus data/images (Spec 5.2).
    nachtrag.nachtragen(con, [_sammel(1, "Etwas")])
    pfad = con.execute(
        "SELECT image_path FROM product WHERE external_id = '1'").fetchone()[0]
    assert pfad == "/images/1.jpg"


def test_beide_formate_in_einer_liste(con):
    bericht = nachtrag.nachtragen(con, [_roh(269, "Weißkohl", kcal=25.0),
                                        _sammel(10002, "Eulen", kcal=442.0)])
    assert bericht["nachgetragen"] == 2 and bericht["mit_naehrwert"] == 2


def test_ein_lauf_ohne_ean_loescht_keine_bekannte(con):
    nachtrag.nachtragen(con, [_sammel(1, "Etwas", ean="4104420256873")])
    # Der Nachtlauf über die Suche kennt keine EAN — er darf sie nicht leeren.
    nachtrag.nachtragen(con, [_roh(1, "Etwas")])
    assert con.execute(
        "SELECT ean FROM product WHERE external_id = '1'").fetchone()[0] \
        == "4104420256873"


# --------------------------------------------------------------------------
# Der Ergänzungsweg: nur `composition`, für Produkte, die es schon gibt

def _nur_comp(pid, kcal=None, ean=None):
    return {"productId": pid,
            "composition": {"productId": pid, "ean": ean,
                            "nutritionalValues": ([{
                                "portion": "100 g",
                                "values": {"energyKCal": {"amount": kcal}}}]
                                if kcal is not None else []),
                            "plainIngredients": "WEIZENMEHL"}}


def test_ergaenzung_legt_kein_produkt_an(con):
    bericht = nachtrag.nachtragen(con, [_nur_comp(999, kcal=42.0, ean="123")])
    assert bericht["status"] == "ok"
    assert con.execute("SELECT count(*) AS n FROM product").fetchone()["n"] == 0
    # Ohne Produkt auch keine Nährwertzeile — sonst entstünde eine Waise.
    assert con.execute(
        "SELECT count(*) AS n FROM product_naehrwert").fetchone()["n"] == 0


def test_ergaenzung_traegt_naehrwerte_und_ean_nach(con):
    nachtrag.nachtragen(con, [_roh(269, "Weißkohl")])
    assert con.execute(
        "SELECT count(*) AS n FROM product_naehrwert").fetchone()["n"] == 0

    bericht = nachtrag.nachtragen(con, [_nur_comp(269, kcal=25.0, ean="4104420256873")])

    assert bericht["ean_nachgetragen"] == 1
    z = con.execute("""SELECT p.name, p.ean, n.kcal FROM product p
                       JOIN product_naehrwert n ON n.product_id = p.id""").fetchone()
    assert z["name"] == "Weißkohl" and z["ean"] == "4104420256873"
    assert z["kcal"] == 25.0


def test_ergaenzung_aendert_den_namen_nicht(con):
    nachtrag.nachtragen(con, [_roh(269, "Weißkohl 1 Stk")])
    nachtrag.nachtragen(con, [_nur_comp(269, kcal=25.0)])
    assert con.execute(
        "SELECT name FROM product WHERE external_id='269'").fetchone()[0] \
        == "Weißkohl 1 Stk"


def test_eine_vorhandene_ean_wird_nicht_ueberschrieben(con):
    nachtrag.nachtragen(con, [_sammel(1, "Etwas", ean="1111111111111")])
    nachtrag.nachtragen(con, [_nur_comp(1, ean="9999999999999")])
    assert con.execute(
        "SELECT ean FROM product WHERE external_id='1'").fetchone()[0] \
        == "1111111111111"


def test_ein_suchprodukt_ist_keine_ergaenzung(con):
    # Eine Suchzeile trägt Produkt UND Nährwerte im selben Objekt. Würde sie
    # als Ergänzung gelesen, legte der Nachtrag kein einziges Produkt mehr an.
    assert nachtrag._nur_composition(_roh(1, "Milch", kcal=46.0)) is False
    assert nachtrag._nur_composition(_nur_comp(1, kcal=46.0)) is True


# --------------------------------------------------------------------------
# Gegen die ECHTE Antwort des Sammelabrufs, einmal aufgenommen (2026-09-06)

SAMMEL_FIXTURE = (__import__("pathlib").Path(__file__).parent / "fixtures"
                  / "knuspr_sammelabruf.json")


def test_echte_sammelantwort_wird_geparst(con):
    eintraege = json.loads(SAMMEL_FIXTURE.read_text(encoding="utf-8"))
    bericht = nachtrag.nachtragen(con, eintraege)
    assert bericht["status"] == "ok"
    assert bericht["nachgetragen"] == len(eintraege), \
        "Format geändert? Fixture neu aufnehmen."
    zeilen = con.execute("SELECT name, price_cents, unit_text, category_l1,"
                         " image_path, ean FROM product").fetchall()
    assert all(z["name"] and z["price_cents"] for z in zeilen)
    assert all(z["image_path"].startswith("/images/") for z in zeilen
               if z["image_path"])
    assert any(z["ean"] for z in zeilen)
    assert con.execute(
        "SELECT count(*) AS n FROM product_naehrwert").fetchone()["n"] >= 1
