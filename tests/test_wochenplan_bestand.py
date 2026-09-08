"""Der Bon schlägt vor, der Mensch bestätigt (Phase 3).

Kein Modell, kein Netz. Ein bestätigter Kauf der letzten Tage, dessen
Produkt eine Zeile der Einkaufsliste trifft, wird als OFFENE Bestandszeile
vorgelegt — und erst ein Ja lässt ihn abziehen.
"""
from datetime import date

import pytest

from zettel import recipes, wochenplan
from zettel.bons import kaeufe, zerlegen
from zettel.wochenplan import bestand

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
BUTTER = "MIIL Deutsche Markenbutter"
HEUTE = date(2026, 9, 7)


@pytest.fixture
def con(katalog_con):
    return katalog_con


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _kauf(con, product_name, *, datum="2026-09-05", qty=1,
          entscheidung="kept", datei="bon.pdf"):
    """Ein Bon mit einer Zeile, zugeordnet und entschieden."""
    bon = zerlegen.Bon(laden="rewe", datum=datum, posten=[
        zerlegen.Posten(text=product_name[:12].upper(), gesamt_cents=199,
                        menge=qty, zeile=1)])
    rid = kaeufe.anlegen(con, bon, datei=datei)
    item = kaeufe.posten(con, rid)[0]
    kaeufe.zuordnung_setzen(con, item["id"], product_id=_pid(con, product_name))
    if entscheidung != "offen":
        kaeufe.entscheiden(con, item["id"], entscheidung)
    return item["id"]


def _plan_mit(con, *zutaten):
    rid = recipes.anlegen(con, "A", servings=4, zutaten=list(zutaten))
    pid = wochenplan.anlegen(con, wochenplan.aus_formular(
        {"tage": "1", "personen": "4"}), von=HEUTE)
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    wochenplan.tag_setzen(con, tag["id"], rid)
    return pid


def test_gekaufte_menge_aus_packung():
    assert bestand.gekaufte_menge({"unit_text": "1 kg", "qty": 2}) == (2000.0, "g")
    assert bestand.gekaufte_menge({"unit_text": "500 ml", "qty": 1}) == (500.0, "ml")
    assert bestand.gekaufte_menge({"unit_text": None, "qty": 3}) == (3.0, "Stk")


def test_passender_kauf_wird_vorgeschlagen(con):
    item = _kauf(con, BUTTER, datum="2026-09-05")
    pid = _plan_mit(con, {"product_id": _pid(con, BUTTER), "amount": 80, "unit": "g"})
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 1
    b = wochenplan.laden(con, pid)["bestand"]
    assert len(b) == 1
    z = b[0]
    assert (z["herkunft"], z["decision"], z["receipt_item_id"]) == (
        "aus_bon", "offen", item)
    assert (z["name"], z["menge"], z["einheit"]) == (BUTTER, 250.0, "g")
    assert z["gekauft_am"] == "2026-09-05"
    # Offen zieht nichts ab …
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["gedeckt"] is False
    # … erst das Ja.
    wochenplan.bestand_entscheiden(con, z["id"], "kept")
    e = wochenplan.einkaufsliste(con, wochenplan.laden(con, pid))
    assert e["zeilen"][0]["gedeckt"] is True


def test_kauf_ohne_bezug_und_ohne_ja_wird_nicht_vorgeschlagen(con):
    _kauf(con, MILCH, datei="a.pdf")                       # nicht auf der Liste
    _kauf(con, BUTTER, entscheidung="offen", datei="b.pdf")  # nie bestätigt
    _kauf(con, BUTTER, entscheidung="removed", datei="c.pdf")
    pid = _plan_mit(con, {"product_id": _pid(con, BUTTER), "amount": 80, "unit": "g"})
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 0


def test_alter_kauf_ist_kein_hinweis_mehr(con):
    _kauf(con, BUTTER, datum="2026-08-20")
    pid = _plan_mit(con, {"product_id": _pid(con, BUTTER), "amount": 80, "unit": "g"})
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 0


def test_treffer_ueber_den_namen_bei_freitext(con):
    _kauf(con, BUTTER)
    pid = _plan_mit(con, {"free_text": "Markenbutter", "amount": 50, "unit": "g"})
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 1


def test_zweimal_vorschlagen_ergibt_keine_dublette(con):
    _kauf(con, BUTTER)
    pid = _plan_mit(con, {"product_id": _pid(con, BUTTER), "amount": 80, "unit": "g"})
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 1
    b = wochenplan.laden(con, pid)["bestand"][0]
    wochenplan.bestand_entscheiden(con, b["id"], "removed")
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 0
    assert len(wochenplan.laden(con, pid)["bestand"]) == 1


def test_ohne_liste_nichts(con):
    _kauf(con, BUTTER)
    pid = wochenplan.anlegen(con, wochenplan.aus_formular({"tage": "1"}), von=HEUTE)
    assert bestand.vorschlagen(con, pid, heute=HEUTE) == 0
