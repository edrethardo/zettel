"""Der Wochenplan, Phase 1: Rahmen, Speicher, Vorlage (ohne Modell).

Kein Netz, kein Modell. Was hier geprüft wird, ist die Hälfte des Planers,
die auch ohne LLM trägt — und die zwei Zusicherungen, die strukturell sind:
der Bestand hängt am Plan (und fällt mit ihm), und zur Wahl steht ein Rezept
je Gericht, nicht sieben Lasagnen.
"""
from datetime import date

import pytest

from zettel import db, recipes, wochenplan
from zettel.wochenplan import rahmen as rahmenmodul
from zettel.wochenplan import speicher, vorlage

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
BUTTER = "MIIL Deutsche Markenbutter"


@pytest.fixture
def con(katalog_con):
    return katalog_con


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _rahmen(**werte):
    return wochenplan.aus_formular(werte)


# --------------------------------------------------------------------------
# Rahmen

def test_formular_liest_zahlen_nachsichtig():
    r = wochenplan.aus_formular({"tage": "3", "personen": "4",
                                 "max_minuten": "30 min", "budget": "40,50"})
    assert (r.tage, r.personen, r.max_minuten, r.budget_cents) == (3, 4, 30, 4050)


def test_formular_faellt_auf_vorgaben_zurueck():
    r = wochenplan.aus_formular({"tage": "", "personen": "abc",
                                 "max_minuten": "", "budget": "0"})
    assert r.tage == rahmenmodul.TAGE_VORGABE
    assert r.personen == rahmenmodul.PERSONEN_VORGABE
    assert r.max_minuten is None and r.budget_cents is None


def test_formular_deckelt_vertipper():
    r = wochenplan.aus_formular({"tage": "50", "personen": "99"})
    assert r.tage == rahmenmodul.MAX_TAGE
    assert r.personen == rahmenmodul.MAX_PERSONEN


def test_bestand_aus_text():
    assert wochenplan.bestand_aus_text("500 g Kartoffeln, 6 Eier, Nudeln") == [
        {"menge": 500.0, "einheit": "g", "name": "Kartoffeln"},
        {"menge": 6.0, "einheit": None, "name": "Eier"},
        {"menge": None, "einheit": None, "name": "Nudeln"},
    ]


def test_bestand_aus_text_kilo_komma_und_leeres():
    zeilen = wochenplan.bestand_aus_text("1,5 kg Mehl;\n\n 500 g ; ; Reis.")
    assert zeilen == [{"menge": 1.5, "einheit": "kg", "name": "Mehl"},
                      {"menge": None, "einheit": None, "name": "Reis"}]
    assert wochenplan.bestand_aus_text(None) == []


def test_unbekanntes_wort_ist_kein_einheit():
    # „2 Becher Sahne": `mengen` kennt „Becher" nicht als Einheit — dann
    # gehört das Wort zum Namen, statt stillschweigend eine Einheit zu werden.
    assert wochenplan.bestand_aus_text("2 Becher Sahne") == [
        {"menge": 2.0, "einheit": None, "name": "Becher Sahne"}]


# --------------------------------------------------------------------------
# Speicher

def test_anlegen_ergibt_tage_mit_datum_und_bestand(con):
    r = _rahmen(tage="3", personen="2", bestand="500 g Kartoffeln, Nudeln")
    pid = wochenplan.anlegen(con, r, von="2026-09-07")
    p = wochenplan.laden(con, pid)
    assert [t["datum"] for t in p["tage_liste"]] == [
        "2026-09-07", "2026-09-08", "2026-09-09"]
    assert p["bis"] == "2026-09-09"
    assert [t["portionen"] for t in p["tage_liste"]] == [2, 2, 2]
    assert p["tage_liste"][0]["wochentag"] == date(2026, 9, 7).weekday()
    assert [(b["name"], b["menge"], b["decision"], b["herkunft"])
            for b in p["bestand"]] == [
        ("Kartoffeln", 500.0, "kept", "erklaert"),
        ("Nudeln", None, "kept", "erklaert")]
    assert p["status"] == wochenplan.ENTWURF
    assert p["zusammenfassung"]["belegt"] == 0


def test_tag_setzen_und_entscheiden(con):
    rid = recipes.anlegen(con, "Milchreis", servings=4,
                          zutaten=[{"product_id": _pid(con, MILCH)}])
    con.execute("UPDATE recipe SET prep_minutes = 10, cook_minutes = 25"
                " WHERE id = ?", (rid,))
    pid = wochenplan.anlegen(con, _rahmen(tage="2"))
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    t = wochenplan.tag_setzen(con, tag["id"], rid, portionen="3")
    assert (t["recipe_id"], t["portionen"], t["decision"]) == (rid, 3, "offen")

    t = wochenplan.tag_entscheiden(con, tag["id"], "kept")
    assert t["decision"] == "kept" and t["decided_at"]
    t = wochenplan.tag_entscheiden(con, tag["id"], "offen")
    assert t["decision"] == "offen" and t["decided_at"] is None

    # Ein anderes Rezept ist ein neuer Vorschlag: die Entscheidung geht zurück.
    wochenplan.tag_entscheiden(con, tag["id"], "kept")
    t = wochenplan.tag_setzen(con, tag["id"], rid)
    assert t["decision"] == "offen"

    p = wochenplan.laden(con, pid)
    assert p["tage_liste"][0]["rezept"]["zeitsatz"] == "35 Minuten"
    assert p["zusammenfassung"]["kochzeit"] == 35
    assert p["zusammenfassung"]["belegt"] == 1


def test_auswaerts_ist_eine_festlegung(con):
    pid = wochenplan.anlegen(con, _rahmen(tage="1"))
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    t = wochenplan.auswaerts_setzen(con, tag["id"])
    assert (t["auswaerts"], t["recipe_id"], t["decision"]) == (1, None, "kept")
    assert wochenplan.laden(con, pid)["zusammenfassung"]["auswaerts"] == 1
    assert not wochenplan.laden(con, pid)["tage_liste"][0]["zaehlt"]


def test_unsinn_wird_abgelehnt(con):
    pid = wochenplan.anlegen(con, _rahmen(tage="1"))
    tag = wochenplan.laden(con, pid)["tage_liste"][0]
    with pytest.raises(wochenplan.WochenplanFehler):
        wochenplan.tag_setzen(con, tag["id"], 999_999)
    with pytest.raises(wochenplan.WochenplanFehler):
        wochenplan.tag_entscheiden(con, tag["id"], "vielleicht")
    with pytest.raises(wochenplan.WochenplanFehler):
        wochenplan.laden(con, 999_999)
    with pytest.raises(wochenplan.WochenplanFehler):
        wochenplan.bestand_hinzufuegen(con, pid, "   ")


def test_bestand_am_plan_faellt_mit_ihm(con):
    pid = wochenplan.anlegen(con, _rahmen(tage="2", bestand="6 Eier"))
    bid = wochenplan.bestand_hinzufuegen(con, pid, "Reis", 200, "g")
    b = wochenplan.bestand_entscheiden(con, bid, "removed")
    assert b["decision"] == "removed"
    wochenplan.loeschen(con, pid)
    assert con.execute("SELECT count(*) FROM plan_tag").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM plan_bestand").fetchone()[0] == 0


def test_vorgeschlagener_bestand_ist_offen(con):
    pid = wochenplan.anlegen(con, _rahmen(tage="1"))
    bid = wochenplan.bestand_hinzufuegen(con, pid, "Kartoffeln", 1, "kg",
                                         herkunft=wochenplan.AUS_BON)
    b = wochenplan.laden(con, pid)["bestand"][0]
    assert (b["id"], b["decision"], b["offen"]) == (bid, "offen", True)


def test_aktuell_und_alle(con):
    assert wochenplan.aktuell(con) is None
    a = wochenplan.anlegen(con, _rahmen(tage="1"))
    b = wochenplan.anlegen(con, _rahmen(tage="2"))
    assert wochenplan.aktuell(con)["id"] == b
    assert [p["id"] for p in wochenplan.alle(con)] == [b, a]


def test_status_setzen(con):
    pid = wochenplan.anlegen(con, _rahmen(tage="1"))
    wochenplan.status_setzen(con, pid, wochenplan.IM_KORB, order_id=None,
                             span_id="abc")
    p = wochenplan.laden(con, pid)
    assert (p["status"], p["span_id"]) == (wochenplan.IM_KORB, "abc")
    with pytest.raises(wochenplan.WochenplanFehler):
        wochenplan.status_setzen(con, pid, "bestellt")


# --------------------------------------------------------------------------
# Vorlage

def _quellrezept(con, name, minuten, zutaten, *, gericht=None, servings=4):
    """Ein Rezept, wie der Chefkoch-Abruf es hinterlässt — mit Zutatenliste
    und, wenn `gericht` gesetzt ist, einem `dish`, das darauf zeigt."""
    cur = con.execute(
        "INSERT INTO recipe (name, servings, source, prep_minutes)"
        " VALUES (?, ?, 'chefkoch', ?)", (name, servings, minuten))
    rid = int(cur.lastrowid)
    for pos, z in enumerate(zutaten):
        con.execute(
            "INSERT INTO recipe_ingredient (recipe_id, pos, raw_name, name,"
            " amount, unit) VALUES (?, ?, ?, ?, ?, ?)",
            (rid, pos, z[0], z[0], z[1] if len(z) > 1 else None,
             z[2] if len(z) > 2 else None))
    if gericht:
        con.execute(
            "INSERT INTO dish (name, query, status, recipe_id, requested_at,"
            " fetched_at) VALUES (?, ?, 'ok', ?, 'x', 'x')",
            (gericht, gericht, rid))
    con.commit()
    return rid


def test_vorlage_ein_rezept_je_gericht(con):
    a = _quellrezept(con, "Lasagne", 60, [("Hackfleisch", 500, "g")],
                     gericht="lasagne")
    _quellrezept(con, "Lasagne Bolognese", 90, [("Hackfleisch", 400, "g")])
    _quellrezept(con, "Lasagne alla Nonna", 120, [("Hackfleisch", 400, "g")])
    eigenes = recipes.anlegen(con, "Milchreis", servings=2,
                              zutaten=[{"product_id": _pid(con, MILCH)}])
    leer = recipes.anlegen(con, "Leeres Rezept")
    ids = [g["id"] for g in vorlage.gerichte(con)]
    assert a in ids and eigenes in ids
    assert leer not in ids
    assert len(ids) == 2
    lasagne = next(g for g in vorlage.gerichte(con) if g["id"] == a)
    assert lasagne["zutaten"] == ["Hackfleisch"]
    assert lasagne["zeitsatz"] == "1 Stunde"
    milchreis = next(g for g in vorlage.gerichte(con) if g["id"] == eigenes)
    assert milchreis["zutaten"] == [MILCH]
    assert milchreis["passt_zeit"] is None


def test_vorlage_zeitgrenze_laesst_unbekannte_stehen(con):
    schnell = _quellrezept(con, "Omelett", 15, [("Eier", 3)], gericht="omelett")
    _quellrezept(con, "Schmorbraten", 180, [("Rind", 1, "kg")], gericht="braten")
    eigenes = recipes.anlegen(con, "Milchreis", servings=2,
                              zutaten=[{"product_id": _pid(con, MILCH)}])
    liste = vorlage.gerichte(con, _rahmen(max_minuten="30"))
    assert {g["id"] for g in liste} == {schnell, eigenes}
    assert next(g for g in liste if g["id"] == schnell)["passt_zeit"] is True
    assert next(g for g in liste if g["id"] == eigenes)["passt_zeit"] is None


def test_schema_hat_die_drei_tabellen(con):
    assert {"plan", "plan_tag", "plan_bestand"} <= db._tabellen(con)
    assert "plan_bestand" in db.TABLES
