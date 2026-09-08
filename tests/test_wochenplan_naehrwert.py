"""kcal und Eiweiss je Tag — gerechnet aus `product_naehrwert`, nie geschätzt.

Kein Modell. Geprüft wird die eine Regel: was sich nicht rechnen lässt
(Freitext, Produkt ohne Nährwertzeile, Bedarf in Stück), wird gezählt und
benannt — und drückt die Zahl nicht auf null.
"""
import pytest
from fastapi.testclient import TestClient

from zettel import db, recipes, wochenplan
from zettel.web import app as webapp
from zettel.wochenplan import naehrwert

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
BUTTER = "MIIL Deutsche Markenbutter"


@pytest.fixture
def con(katalog_con):
    return katalog_con


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _naehrwert(con, name, kcal, protein, dose="100 g"):
    con.execute(
        "INSERT OR REPLACE INTO product_naehrwert (product_id, dose, kcal, protein,"
        " gesehen_at) VALUES (?, ?, ?, ?, 'x')",
        (_pid(con, name), dose, kcal, protein))
    con.commit()


def _plan(con, rid, personen="2"):
    pid = wochenplan.anlegen(con, wochenplan.aus_formular(
        {"tage": "1", "personen": personen, "kcal": "700"}))
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    wochenplan.tag_setzen(con, tag["id"], rid)
    return pid


def test_je_tag_rechnet_aus_gramm_und_zaehlt_den_rest(con):
    _naehrwert(con, MILCH, 64, 3.4, dose="100 ml")
    _naehrwert(con, BUTTER, 740, 0.7, dose="100g")
    rid = recipes.anlegen(con, "Milchreis", servings=4, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 1000, "unit": "ml"},
        {"product_id": _pid(con, BUTTER), "amount": 50, "unit": "g"},
        {"free_text": "Zimt", "amount": 1, "unit": "TL"},
        {"free_text": "Eier", "amount": 2}])
    n = naehrwert.je_tag(con, rid, portionen=2)
    # Für 2 statt 4 Portionen: 500 ml Milch (320 kcal, 17 g) + 25 g Butter
    # (185 kcal, 0,175 g) = 505 kcal, 17,2 g — je Portion 253 / 8,6.
    assert (n["kcal"], n["protein"]) == (505, 17.2)
    assert (n["kcal_je_portion"], n["protein_je_portion"]) == (252, 8.6)
    assert (n["gerechnet"], n["n"]) == (2, 4)
    assert [o["name"] for o in n["offen"]] == ["Zimt", "Eier"]
    assert n["offen"][0]["grund"] == "kein Produkt"


def test_produkt_ohne_naehrwert_und_stueck_bleiben_offen(con):
    _naehrwert(con, MILCH, 64, 3.4)
    # Die Fixture trägt seit dem 06.09. echte Nährwerte — für diesen Fall
    # soll die Butter ausdrücklich keine haben.
    con.execute("DELETE FROM product_naehrwert WHERE product_id = ?",
                (_pid(con, BUTTER),))
    con.commit()
    rid = recipes.anlegen(con, "A", servings=2, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 3},          # Stück
        {"product_id": _pid(con, BUTTER), "amount": 20, "unit": "g"}])  # ohne Zeile
    n = naehrwert.je_tag(con, rid, portionen=2)
    assert n["kcal"] is None and n["gerechnet"] == 0
    assert [o["grund"] for o in n["offen"]] == [
        "Menge in Stk, nicht in g", "keine Nährwertangabe"]


def test_fremder_bezug_wird_nicht_umgerechnet(con):
    _naehrwert(con, MILCH, 64, 3.4, dose="1 Portion")
    rid = recipes.anlegen(con, "A", servings=2, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 200, "unit": "ml"}])
    n = naehrwert.je_tag(con, rid, portionen=2)
    assert n["kcal"] is None
    assert "nicht je 100 g" in n["offen"][0]["grund"]


def test_anreichern_mittelt_nur_tage_mit_zahl(con):
    _naehrwert(con, MILCH, 64, 3.4)
    a = recipes.anlegen(con, "A", servings=2, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 1000, "unit": "ml"}])
    b = recipes.anlegen(con, "B", servings=2, zutaten=[{"free_text": "Reis"}])
    pid = wochenplan.anlegen(con, wochenplan.aus_formular(
        {"tage": "3", "personen": "2", "kcal": "300"}))
    tage = wochenplan.laden(con, pid)["tage_liste"]
    wochenplan.tag_setzen(con, tage[0]["id"], a)
    wochenplan.tag_setzen(con, tage[1]["id"], b)
    plan = wochenplan.naehrwerte(con, wochenplan.laden(con, pid))
    assert plan["kcal_ziel"] == 300
    assert plan["tage_liste"][0]["naehrwert"]["kcal_je_portion"] == 320
    assert plan["tage_liste"][1]["naehrwert"]["kcal"] is None
    assert plan["tage_liste"][2]["naehrwert"] is None
    z = plan["zusammenfassung"]
    assert (z["kcal_je_portion"], z["naehrwert_tage"], z["kcal_abstand"]) == (320, 1, 20)


def test_seite_zeigt_kcal_je_portion(db_datei, tmp_path):
    con = db.connect(db_datei)
    _naehrwert(con, MILCH, 64, 3.4)
    rid = recipes.anlegen(con, "Milchreis", servings=2, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 1000, "unit": "ml"}])
    con.close()
    with TestClient(webapp.create_app(db_path=db_datei,
                                      image_dir=tmp_path / "b")) as client:
        r = client.post("/plan", data={"tage": "1", "personen": "2",
                                       "kcal": "300"}, follow_redirects=False)
        pid = int(r.headers["location"].rsplit("/", 1)[1])
        con = db.connect(db_datei)
        tag = wochenplan.laden(con, pid)["tage_liste"][0]
        con.close()
        r = client.post(f"/plan/{pid}/tag/{tag['id']}", data={"recipe_id": str(rid)})
        assert "≈ 320 kcal · 17 g Eiweiss je Portion" in r.text
        assert "1 von 1 Zutaten gerechnet" in r.text
        assert "20 kcal über dem Ziel" in r.text
        assert "Ziel 300 kcal je Person und Tag" in r.text
