"""Die Einkaufsliste des Wochenplans — gerechnet, nicht generiert.

Kein Modell. Die Rangfolge der Quellen (verknüpfte Produkte, gemerkte
Zuordnung, Zutatenliste der Quelle), das Zusammenzählen über die Tage, das
Abziehen des Bestands und der Weg in den bestehenden Korb.
"""
import json

import pytest

from zettel import orders, recipes, wochenplan
from zettel.wochenplan import liste

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
BUTTER = "MIIL Deutsche Markenbutter"


@pytest.fixture
def con(katalog_con):
    return katalog_con


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _plan(con, *rezepte, bestand="", tage=None, personen="4"):
    """Ein Plan mit je einem Tag je Rezept — alle offen."""
    r = wochenplan.aus_formular({"tage": str(tage or len(rezepte)),
                                 "personen": personen, "bestand": bestand})
    pid = wochenplan.anlegen(con, r)
    for t, rid in zip(wochenplan.laden(con, pid)["tage_liste"], rezepte):
        if rid is not None:
            wochenplan.tag_setzen(con, t["id"], rid)
    return pid


def _quellrezept(con, name, zutaten, *, servings=4, zuordnung=None):
    cur = con.execute(
        "INSERT INTO recipe (name, servings, source) VALUES (?, ?, 'chefkoch')",
        (name, servings))
    rid = int(cur.lastrowid)
    for pos, (raw, amount, unit) in enumerate(zutaten):
        con.execute(
            "INSERT INTO recipe_ingredient (recipe_id, pos, raw_name, name,"
            " amount, unit) VALUES (?, ?, ?, ?, ?, ?)",
            (rid, pos, raw, raw, amount, unit))
    for pos, (kette, product_id, menge) in enumerate(zuordnung or []):
        con.execute(
            "INSERT INTO recipe_zuordnung (recipe_id, pos, suchbegriffe, menge,"
            " product_id, wahl_menge, gewaehlt, erstellt_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'x')",
            (rid, pos, json.dumps(kette), menge, product_id, menge,
             1 if product_id else 0))
    con.commit()
    return rid


# --------------------------------------------------------------------------
# Zeilen je Rezept — die Rangfolge

def test_verknuepfte_produkte_gewinnen(con):
    rid = recipes.anlegen(con, "Milchreis", servings=4, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 500, "unit": "ml"},
        {"free_text": "Zimt"}])
    zeilen = liste.zeilen_je_rezept(con, rid, portionen=8)
    assert [(z["woher"], z["name"], z["bedarf"], z["einheit"]) for z in zeilen] == [
        (liste.AUS_VERKNUEPFUNG, MILCH, 1000.0, "ml"),
        (liste.AUS_VERKNUEPFUNG, "Zimt", None, None)]
    assert zeilen[0]["price_cents"] == 119


def test_gemerkte_zuordnung_mit_mengen_der_quelle(con):
    rid = _quellrezept(con, "Béchamel", [("Milch", 500, "ml"),
                                         ("Butter", 50, "g"),
                                         ("Salz", None, None)],
                       zuordnung=[(["Milch"], _pid(con, MILCH), 1),
                                  (["Butter"], _pid(con, BUTTER), 1),
                                  (["Salz"], None, 1)])
    zeilen = liste.zeilen_je_rezept(con, rid, portionen=2)
    assert [(z["woher"], z["product_id"] is not None, z["bedarf"], z["einheit"])
            for z in zeilen] == [
        (liste.AUS_ZUORDNUNG, True, 250.0, "ml"),
        (liste.AUS_ZUORDNUNG, True, 25.0, "g"),
        (liste.AUS_ZUORDNUNG, False, None, None)]
    assert zeilen[0]["zutat"] == "Milch"
    assert zeilen[2]["free_text"] == "Salz"


def test_ohne_zuordnung_bleibt_die_zutat_als_freitext(con):
    rid = _quellrezept(con, "Pfannkuchen", [("Mehl", 200, "g"), ("Eier", 3, None)])
    zeilen = liste.zeilen_je_rezept(con, rid)
    assert [(z["woher"], z["free_text"], z["bedarf"], z["einheit"])
            for z in zeilen] == [
        (liste.AUS_QUELLE, "Mehl", 200.0, "g"),
        (liste.AUS_QUELLE, "Eier", 3.0, None)]


# --------------------------------------------------------------------------
# Zusammenlegen und Bestand

def test_zwei_rezepte_ergeben_eine_zeile_mit_summe(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[
        {"product_id": _pid(con, BUTTER), "amount": 40, "unit": "g"}])
    b = recipes.anlegen(con, "B", servings=4, zutaten=[
        {"product_id": _pid(con, BUTTER), "amount": 40, "unit": "g"},
        {"free_text": "Zimt"}])
    pid = _plan(con, a, b)
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    butter = next(z for z in e["zeilen"] if z["product_id"] == _pid(con, BUTTER))
    assert (butter["bedarf"], butter["einheit"]) == (80.0, "g")
    assert butter["tage"] == [0, 1] and butter["rezepte"] == ["A", "B"]
    assert butter["packungen"] == 1 and butter["preis_cents"] == 105
    zimt = next(z for z in e["zeilen"] if z["free_text"] == "Zimt")
    assert zimt["tage"] == [1]
    # Rest: nur der Zimt kommt an genau einem Tag vor.
    assert [z["name"] for z in e["rest"]] == ["Zimt"]
    assert e["n_rest"] == 1 and e["zu_kaufen"] == 2 and e["ohne_produkt"] == 1


def test_unvereinbare_mengen_werden_nicht_verruehrt(con):
    a = _quellrezept(con, "A", [("Lauch", 2, "Stange")])
    b = _quellrezept(con, "B", [("Lauch", 200, "g")])
    pid = _plan(con, a, b)
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    lauch = next(z for z in e["zeilen"] if z["name"] == "Lauch")
    assert lauch["bedarf"] is None and lauch["grund"]
    assert lauch["tage"] == [0, 1]


def test_bestand_zieht_ab_wo_es_rechnen_kann(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 800, "unit": "ml"},
        {"free_text": "Kartoffeln", "amount": 1, "unit": "kg"},
        {"free_text": "Eier", "amount": 4},
        {"free_text": "Nudeln", "amount": 500, "unit": "g"}])
    pid = _plan(con, a, bestand="500 ml Milch, 2 kg Kartoffeln, 6 Stk Eier, Nudeln")
    # „Milch" trifft das Produkt über den Namen — das Produkt heisst „…
    # Landmilch …", also über den Wortanfang nicht: der Bestand nennt daher
    # das Produkt.
    wochenplan.bestand_hinzufuegen(con, pid, MILCH, 500, "ml",
                                   product_id=_pid(con, MILCH))
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    nach_name = {z["name"]: z for z in e["zeilen"]}
    milch = nach_name[MILCH]
    assert (milch["bedarf"], milch["gedeckt"]) == (300.0, False)
    assert "noch 300 ml" in milch["bestand_hinweis"]
    assert nach_name["Kartoffeln"]["gedeckt"] is True
    assert nach_name["Eier"]["gedeckt"] is True
    nudeln = nach_name["Nudeln"]
    assert nudeln["gedeckt"] is False and nudeln["bedarf"] == 500.0
    assert "ohne Menge" in nudeln["bestand_hinweis"]
    assert e["gedeckt"] == 2 and e["zu_kaufen"] == 2


def test_bestand_in_fremder_einheit_zieht_nichts_ab(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[
        {"free_text": "Lauch", "amount": 500, "unit": "g"}])
    pid = _plan(con, a, bestand="2 Stk Lauch")
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    lauch = e["zeilen"][0]
    assert lauch["bedarf"] == 500.0 and not lauch["gedeckt"]
    assert "nicht gegen" in lauch["bestand_hinweis"]


def test_verworfener_bestand_zaehlt_nicht(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[
        {"free_text": "Eier", "amount": 4}])
    pid = _plan(con, a, bestand="6 Eier")
    bid = wochenplan.laden(con, pid)["bestand"][0]["id"]
    wochenplan.bestand_entscheiden(con, bid, "removed")
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["gedeckt"] is False


def test_verworfene_und_auswaertige_tage_zaehlen_nicht(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[{"free_text": "Reis"}])
    b = recipes.anlegen(con, "B", servings=4, zutaten=[{"free_text": "Mais"}])
    pid = _plan(con, a, b, None, tage=3)
    p = wochenplan.laden(con, pid)
    wochenplan.tag_entscheiden(con, p["tage_liste"][1]["id"], "removed")
    wochenplan.auswaerts_setzen(con, p["tage_liste"][2]["id"])
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert [z["name"] for z in e["zeilen"]] == ["Reis"]


def test_portionen_skalieren_je_tag(con):
    a = recipes.anlegen(con, "A", servings=2, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 250, "unit": "ml"}])
    pid = _plan(con, a, personen="6")
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["bedarf"] == 750.0


def test_budget_vergleich(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[
        {"product_id": _pid(con, MILCH), "amount": 3000, "unit": "ml"}])
    r = wochenplan.aus_formular({"tage": "1", "personen": "4", "budget": "2"})
    pid = wochenplan.anlegen(con, r)
    wochenplan.tag_setzen(con, wochenplan.laden(con, pid)["tage_liste"][0]["id"], a)
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["packungen"] == 3
    assert e["preis_cents"] == 3 * 119
    assert e["budget_ueber"] == 3 * 119 - 200


# --------------------------------------------------------------------------
# In den Korb

def test_in_den_korb_legt_mengen_ein_und_ueberspringt_gedecktes(con):
    a = recipes.anlegen(con, "A", servings=4, zutaten=[
        {"product_id": _pid(con, BUTTER), "amount": 40, "unit": "g"},
        {"free_text": "Eier", "amount": 4}])
    b = recipes.anlegen(con, "B", servings=4, zutaten=[
        {"product_id": _pid(con, BUTTER), "amount": 40, "unit": "g"}])
    pid = _plan(con, a, b, bestand="12 Eier")
    bericht = wochenplan.in_den_korb(con, pid)
    assert bericht["n_eingelegt"] == 1 and bericht["gedeckt"] == 1
    posten = orders.inhalt(con)
    assert len(posten) == 1
    assert posten[0]["product_id"] == _pid(con, BUTTER)
    assert posten[0]["need_amount"] == 80.0 and posten[0]["need_unit"] == "g"
    p = wochenplan.laden(con, pid)
    assert p["status"] == wochenplan.IM_KORB
    assert p["order_id"] == orders.warenkorb_id(con)


def test_leerer_plan_wird_abgelehnt(con):
    pid = _plan(con, None, tage=2)
    with pytest.raises(wochenplan.WochenplanFehler):
        wochenplan.in_den_korb(con, pid)
    assert orders.warenkorb_id(con) is None


def test_bestand_trifft_ueber_die_zutat_auch_bei_anderem_produkt(con):
    """Der Bon nennt ein anderes Eier-Produkt als das Rezept — Eier sind
    trotzdem Eier (Trockenlauf 06.09.)."""
    a = _quellrezept(con, "Carbonara", [("Ei(er)", 6, None)],
                     zuordnung=[(["Eier"], _pid(con, MILCH), 1)])   # irgendein Produkt
    pid = _plan(con, a)
    wochenplan.bestand_hinzufuegen(con, pid, "Berliner Eisbären Eier Bodenhaltung 10er",
                                   10, "Stk", product_id=_pid(con, BUTTER))
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["gedeckt"] is True
