"""Tests für die Rezeptsammlung (WB-325).

Kein Netz, kein Browser. Die Produkte kommen aus derselben aufgezeichneten
Knuspr-Antwort wie in `test_orders.py`, durch den echten Crawler eingespielt —
so trägt der Test dieselben Daten wie der Shop und nicht eine handgemachte
Zeile, die dem Schema zufällig genügt.

Der Test, um den es in diesem Ticket wirklich geht, steht unter „Ein Rezept
überlebt den Katalog": ein Produkt, das der Crawler auf `active = 0` gesetzt
hat, muss sich weiter einlegen lassen und muss sichtbar bleiben.
"""
import pytest

from zettel import orders, recipes

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HAFER = "Alpro Haferdrink Original VEGAN"


@pytest.fixture
def con(katalog_con):
    """Der Katalog aus der Vorlage (conftest.py) — einmal gebaut, hier kopiert."""
    return katalog_con


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _ausmustern(con, name):
    """Genau das, was ein Crawl mit einem verschwundenen Produkt macht."""
    con.execute("UPDATE product SET active = 0 WHERE name = ?", (name,))
    con.commit()


def _namen(zeilen):
    return [z["name"] for z in zeilen]


# --------------------------------------------------------------------------
# Anlegen, gemischt aus Katalogprodukt und Freitext (Spec 4)

def test_rezept_mit_gemischten_zutaten(con):
    """Katalogprodukt und Freitext nebeneinander — der Normalfall, kein Sonderfall."""
    pid = _pid(con, MILCH)
    r = recipes.anlegen(con, "Milchreis", servings=2, note="mit Zimt",
                        zutaten=[{"product_id": pid, "qty": 2},
                                 {"free_text": "Zimt"},
                                 {"free_text": "Rundkornreis", "qty": 1}])

    rezept = recipes.rezept(con, r)
    assert rezept["name"] == "Milchreis"
    assert rezept["servings"] == 2
    assert rezept["note"] == "mit Zimt"
    assert _namen(rezept["zutaten"]) == [MILCH, "Zimt", "Rundkornreis"]

    katalog, zimt, _ = rezept["zutaten"]
    assert katalog["product_id"] == pid and katalog["qty"] == 2
    assert not katalog["ist_freitext"]
    assert katalog["unit_text"]                     # Produktdaten stehen daneben
    assert zimt["product_id"] is None and zimt["ist_freitext"]
    assert rezept["n_zutaten"] == 3


def test_zutat_ohne_alles_und_mit_beidem_wird_abgelehnt(con):
    r = recipes.anlegen(con, "Leer")
    with pytest.raises(orders.UngueltigerPosten):
        recipes.zutat_hinzufuegen(con, r)
    with pytest.raises(orders.UngueltigerPosten):
        recipes.zutat_hinzufuegen(con, r, product_id=_pid(con, MILCH),
                                  free_text="Milch")
    with pytest.raises(orders.UngueltigerPosten):
        recipes.zutat_hinzufuegen(con, r, free_text="   ")
    assert recipes.zutaten(con, r) == []


def test_rezept_ohne_namen_gibt_es_nicht(con):
    with pytest.raises(recipes.RezeptFehler):
        recipes.anlegen(con, "   ")
    assert recipes.rezepte(con) == []


def test_gescheiterte_zutat_laesst_kein_halbes_rezept_zurueck(con):
    """Alles oder nichts: ein halbes Rezept sieht vollständig aus."""
    with pytest.raises(orders.UngueltigerPosten):
        recipes.anlegen(con, "Halb", zutaten=[{"free_text": "Salz"},
                                              {"free_text": ""}])
    assert recipes.rezepte(con) == []


def test_dieselbe_zutat_zweimal_erhoeht_die_menge(con):
    r = recipes.anlegen(con, "Doppelt")
    recipes.zutat_hinzufuegen(con, r, free_text="Salz")
    recipes.zutat_hinzufuegen(con, r, free_text="Salz", qty=2)
    assert [(z["name"], z["qty"]) for z in recipes.zutaten(con, r)] == [("Salz", 3)]


# --------------------------------------------------------------------------
# Bearbeiten und Löschen

def test_bearbeiten_aendert_nur_was_mitkommt(con):
    r = recipes.anlegen(con, "Alt", servings=2, note="alte Notiz")
    recipes.aendern(con, r, name="Neu")
    rezept = recipes.rezept(con, r)
    assert (rezept["name"], rezept["servings"], rezept["note"]) == \
        ("Neu", 2, "alte Notiz")

    recipes.aendern(con, r, servings="", note="andere Notiz")
    rezept = recipes.rezept(con, r)
    assert rezept["servings"] is None
    assert rezept["note"] == "andere Notiz"
    assert rezept["name"] == "Neu"


def test_zutaten_aendern_und_entfernen(con):
    r = recipes.anlegen(con, "Wandelbar", zutaten=[{"free_text": "Salz"},
                                                   {"free_text": "Pfeffer"}])
    salz, pfeffer = recipes.zutaten(con, r)

    assert recipes.zutat_menge(con, salz["id"], 4) == 4
    assert recipes.zutaten(con, r)[0]["qty"] == 4

    # Menge unter 1 entfernt die Zeile — wie im Warenkorb.
    assert recipes.zutat_menge(con, salz["id"], 0) == 0
    assert _namen(recipes.zutaten(con, r)) == ["Pfeffer"]

    recipes.zutat_entfernen(con, pfeffer["id"])
    assert recipes.zutaten(con, r) == []


def test_loeschen_nimmt_die_zutaten_mit(con):
    r = recipes.anlegen(con, "Weg", zutaten=[{"free_text": "Salz"}])
    recipes.loeschen(con, r)
    assert recipes.rezepte(con) == []
    assert con.execute("SELECT count(*) n FROM recipe_item"
                       ).fetchone()["n"] == 0
    with pytest.raises(recipes.RezeptFehler):
        recipes.rezept(con, r)


def test_geloeschtes_rezept_laesst_bestellungen_in_ruhe(con):
    """Was eingekauft wurde, ändert sich nicht, weil jemand ein Rezept wegwirft."""
    r = recipes.anlegen(con, "Kurz", zutaten=[{"product_id": _pid(con, MILCH)}])
    recipes.in_den_korb(con, r)
    recipes.loeschen(con, r)
    assert _namen(orders.inhalt(con)) == [MILCH]


# --------------------------------------------------------------------------
# Alles in den Warenkorb

def test_alles_in_den_warenkorb_legt_genau_die_zutaten_ein(con):
    pid = _pid(con, MILCH)
    r = recipes.anlegen(con, "Milchreis",
                        zutaten=[{"product_id": pid, "qty": 2},
                                 {"free_text": "Zimt", "qty": 3}])

    bericht = recipes.in_den_korb(con, r)
    assert len(bericht["eingelegt"]) == 2
    assert bericht["ausgemustert"] == [] and bericht["gescheitert"] == []

    zeilen = orders.inhalt(con)
    assert [(z["name"], z["qty"]) for z in zeilen] == [(MILCH, 2), ("Zimt", 3)]
    assert zeilen[0]["product_id"] == pid
    assert zeilen[1]["free_text"] == "Zimt"
    # Alles in DEN einen Warenkorb, nicht in eine neue Bestellung (Spec 4).
    assert orders.bestellung(con, zeilen[0]["order_id"])["state"] == "draft"
    assert len({z["order_id"] for z in zeilen}) == 1


def test_zweimal_einlegen_zaehlt_hoch_statt_zeilen_zu_doppeln(con):
    """`korb.einlegen()` fasst zusammen — das Rezept baut daran nichts nach."""
    r = recipes.anlegen(con, "Zweimal",
                        zutaten=[{"product_id": _pid(con, MILCH), "qty": 2}])
    recipes.in_den_korb(con, r)
    recipes.in_den_korb(con, r)
    assert [(z["name"], z["qty"]) for z in orders.inhalt(con)] == [(MILCH, 4)]


def test_leeres_rezept_legt_nichts_ein_und_sagt_es(con):
    """Ein Rezept ohne Zutaten wird laut abgelehnt statt geräuschlos zu wirken.

    Wäre es still, drückte jemand den Knopf, sähe nichts geschehen und hielte
    den Shop für kaputt — und in der Datenbank stünde danach ein leerer
    `draft`, den niemand angelegt haben wollte.
    """
    r = recipes.anlegen(con, "Nur ein Name")
    with pytest.raises(recipes.LeeresRezept) as e:
        recipes.in_den_korb(con, r)
    assert "keine Zutaten" in str(e.value)
    assert orders.inhalt(con) == []
    assert con.execute("SELECT count(*) n FROM orders").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Ein Rezept überlebt den Katalog (Spec 5.3) — der Kern dieses Tickets

def test_ausgemusterte_zutat_bleibt_sichtbar_und_markiert(con):
    r = recipes.anlegen(con, "Milchreis",
                        zutaten=[{"product_id": _pid(con, MILCH), "qty": 2},
                                 {"free_text": "Zimt"}])
    _ausmustern(con, MILCH)

    rezept = recipes.rezept(con, r)
    # Nicht stillschweigend weggelassen — das ist der schlimmste Ausgang.
    assert _namen(rezept["zutaten"]) == [MILCH, "Zimt"]
    milch, zimt = rezept["zutaten"]
    assert milch["nicht_im_katalog"] is True
    assert zimt["nicht_im_katalog"] is False      # Freitext war nie im Katalog
    assert rezept["n_ausgemustert"] == 1
    # Und schon in der Übersicht, nicht erst in der Einzelansicht.
    assert recipes.rezepte(con)[0]["n_ausgemustert"] == 1


def test_ausgemusterte_zutat_laesst_sich_weiter_einlegen(con):
    pid = _pid(con, MILCH)
    r = recipes.anlegen(con, "Milchreis", zutaten=[{"product_id": pid, "qty": 2},
                                                   {"free_text": "Zimt"}])
    _ausmustern(con, MILCH)

    bericht = recipes.in_den_korb(con, r)

    zeilen = orders.inhalt(con)
    assert [(z["name"], z["qty"]) for z in zeilen] == [(MILCH, 2), ("Zimt", 1)]
    assert zeilen[0]["product_id"] == pid
    # Sie liegt im Korb UND wird genannt: beides zusammen, nicht eines davon.
    assert _namen(bericht["eingelegt"]) == [MILCH, "Zimt"]
    assert _namen(bericht["ausgemustert"]) == [MILCH]
    assert bericht["gescheitert"] == []
    assert "Nicht mehr im Katalog" in bericht["meldung"] and MILCH in bericht["meldung"]


def test_ausgemustertes_produkt_ist_aus_der_suche_raus_aber_nicht_aus_dem_rezept(con):
    """Beides gilt gleichzeitig — sonst wäre der Test oben eine Tautologie."""
    from zettel.catalog import search
    r = recipes.anlegen(con, "Milchreis",
                        zutaten=[{"product_id": _pid(con, MILCH)}])
    _ausmustern(con, MILCH)
    assert MILCH not in [p["name"] for p in search.search(con, "Landmilch")]
    assert _namen(recipes.zutaten(con, r)) == [MILCH]


# --------------------------------------------------------------------------
# Daraus ein Rezept machen (Spec 6)

def _erledigte_bestellung(con):
    """Milch und Klopapier einkaufen und abhaken."""
    orders.einlegen(con, product_id=_pid(con, MILCH), qty=2)
    orders.einlegen(con, free_text="Klopapier", qty=3)
    b = orders.abschicken(con)
    for p in orders.posten(con, b["id"]):
        orders.abhaken(con, p["id"])
    return b["id"]


def test_aus_erledigter_bestellung_wird_ein_rezept_mit_deren_posten(con):
    order_id = _erledigte_bestellung(con)
    assert orders.bestellung(con, order_id)["state"] == "erledigt"

    r = recipes.aus_bestellung(con, order_id, name="Sonntag")
    rezept = recipes.rezept(con, r)
    assert rezept["name"] == "Sonntag"
    assert [(z["name"], z["qty"]) for z in rezept["zutaten"]] == \
        [(MILCH, 2), ("Klopapier", 3)]
    # Die Produktbindung wird übernommen, nicht nur der Name — sonst könnte
    # das Rezept später Stufe 1 des Agenten nicht abkürzen (Spec 6).
    assert rezept["zutaten"][0]["product_id"] == _pid(con, MILCH)
    assert rezept["zutaten"][1]["product_id"] is None
    # Die Bestellung bleibt, wie sie war.
    assert orders.bestellung(con, order_id)["state"] == "erledigt"
    assert len(orders.posten(con, order_id)) == 2


def test_rezept_aus_bestellung_ohne_namen_bekommt_einen_vorschlag(con):
    order_id = _erledigte_bestellung(con)
    r = recipes.aus_bestellung(con, order_id)
    assert recipes.rezept(con, r)["name"].startswith("Einkauf vom ")


def test_aus_dem_warenkorb_wird_seit_wb_337_auch_ein_rezept(con):
    """Die umgedrehte Entscheidung (WB-337).

    Bis dahin stand hier das Gegenteil, mit der Begründung, aus einer noch
    nicht eingekauften Absicht ein Rezept zu machen hiesse eine Mahlzeit zu
    behaupten, die es nicht gab. Ein Rezept ist aber keine Chronik, sondern
    eine Einkaufsvorlage — die Begründung steht ersetzt (nicht gelöscht) im
    Docstring von `aus_bestellung`.
    """
    orders.einlegen(con, free_text="Klopapier")
    korb = orders.inhalt(con)[0]["order_id"]
    assert orders.bestellung(con, korb)["state"] == "draft"

    r = recipes.aus_bestellung(con, korb, name="Vorrat")
    assert recipes.rezept(con, r)["name"] == "Vorrat"
    assert [z["name"] for z in recipes.rezept(con, r)["zutaten"]] == \
        ["Klopapier"]
    # Der Korb bleibt der Korb: ein Rezept daraus zu machen schickt nichts ab.
    assert orders.bestellung(con, korb)["state"] == "draft"


def test_rezept_aus_einer_bestellung_die_es_nicht_gibt(con):
    with pytest.raises(recipes.RezeptFehler):
        recipes.aus_bestellung(con, 999999)


def test_der_ganze_kreis_bestellung_rezept_warenkorb(con):
    """Einkaufen, Rezept daraus, wieder in den Korb — mit denselben Mengen."""
    order_id = _erledigte_bestellung(con)
    r = recipes.aus_bestellung(con, order_id, name="Sonntag")
    recipes.in_den_korb(con, r)
    assert [(z["name"], z["qty"]) for z in orders.inhalt(con)] == \
        [(MILCH, 2), ("Klopapier", 3)]
