"""Tests für Warenkorb, Bestellung und Pick-Liste (WB-324).

Kein Netz, kein Browser. Die Produkte kommen aus derselben aufgezeichneten
Knuspr-Antwort wie in `test_catalog.py`, durch den echten Crawler eingespielt.
Wo zwei Verbindungen gebraucht werden (der Wettlauf um den einen Warenkorb),
läuft der Test gegen eine Datei in `tmp_path` — `:memory:` gibt jeder
Verbindung ihre eigene, leere Datenbank und würde genau das nicht prüfen.
"""
import sqlite3
import threading

import pytest

from picknick import db, orders, recipes
from picknick.orders import korb as korb_modul

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HAFER = "Alpro Haferdrink Original VEGAN"


@pytest.fixture
def con(katalog_con):
    """Der Katalog aus der Vorlage (conftest.py) — einmal gebaut, hier kopiert."""
    return katalog_con


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _zustand(con, order_id):
    return orders.bestellung(con, order_id)["state"]


# --------------------------------------------------------------------------
# Genau ein Warenkorb, gemeinsam für beide (Spec 4)

def test_warenkorb_legt_einen_an_und_gibt_ihn_wieder(con):
    erste = orders.warenkorb(con)
    assert orders.warenkorb(con) == erste
    assert orders.warenkorb(con) == erste
    assert con.execute("SELECT count(*) n FROM orders WHERE state = 'draft'"
                       ).fetchone()["n"] == 1


def test_datenbank_verbietet_einen_zweiten_draft(con):
    """Der Zwang muss in der Datenbank stehen, nicht nur in der Funktion.

    Sonst reicht ein einziges INSERT irgendwo im Projekt, um zwei Warenkörbe
    zu haben — und niemand merkt es, bis Milch doppelt gekauft wird.
    """
    orders.warenkorb(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO orders (state, created_at)"
                    " VALUES ('draft', '2026-08-28 10:00:00')")


def test_mehrere_offene_bestellungen_bleiben_erlaubt(con):
    """Der Index sperrt `draft`, sonst nichts."""
    for _ in range(3):
        orders.einlegen(con, product_id=_pid(con, MILCH))
        orders.abschicken(con)
    assert con.execute("SELECT count(*) n FROM orders WHERE state = 'offen'"
                       ).fetchone()["n"] == 3


def test_gleichzeitige_aufrufe_erzeugen_keinen_zweiten_warenkorb(db_datei):
    """Acht Verbindungen greifen gleichzeitig zu — es bleibt bei einem Korb."""
    tor = threading.Barrier(8)
    ergebnisse: list = []
    fehler: list = []

    def hole():
        c = db.connect(db_datei)
        try:
            tor.wait(timeout=10)
            ergebnisse.append(orders.warenkorb(c))
        except Exception as e:                     # pragma: no cover
            fehler.append(e)
        finally:
            c.close()

    faeden = [threading.Thread(target=hole) for _ in range(8)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join(timeout=20)

    assert fehler == []
    assert len(ergebnisse) == 8
    assert len(set(ergebnisse)) == 1, f"mehrere Warenkörbe: {set(ergebnisse)}"
    c = db.connect(db_datei)
    try:
        assert c.execute("SELECT count(*) n FROM orders WHERE state = 'draft'"
                         ).fetchone()["n"] == 1
    finally:
        c.close()


def test_erzwungener_wettlauf_endet_bei_einem_warenkorb(db_datei, monkeypatch):
    """Derselbe Test, aber mit garantiert verlorenem Wettlauf.

    Der Test darüber HOFFT, dass sich acht Fäden ins Gehege kommen; hier wird
    es erzwungen: jeder Faden sieht beim ersten Blick „kein Warenkorb da" und
    versucht anzulegen. Genau das ist der Fall, den eine reine
    Python-Prüfung nicht abfangen kann — sieben INSERTs müssen an der
    Datenbank scheitern und sich auf den einen vorhandenen Korb einigen.
    """
    echt = korb_modul._draft_id
    lokal = threading.local()

    def blind(con):
        if not getattr(lokal, "gesehen", False):
            lokal.gesehen = True
            return None
        return echt(con)

    monkeypatch.setattr(korb_modul, "_draft_id", blind)

    tor = threading.Barrier(8)
    ergebnisse: list = []
    fehler: list = []

    def hole():
        c = db.connect(db_datei)
        try:
            tor.wait(timeout=10)
            ergebnisse.append(korb_modul.warenkorb(c))
        except Exception as e:                     # pragma: no cover
            fehler.append(e)
        finally:
            c.close()

    faeden = [threading.Thread(target=hole) for _ in range(8)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join(timeout=20)

    assert fehler == []
    assert len(set(ergebnisse)) == 1
    c = db.connect(db_datei)
    try:
        assert c.execute("SELECT count(*) n FROM orders").fetchone()["n"] == 1
    finally:
        c.close()


def test_zaehler_legt_keinen_warenkorb_an(con):
    assert orders.korb_anzahl(con) == 0
    assert con.execute("SELECT count(*) n FROM orders").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Posten: Produkt oder Freitext, nie keines von beidem (Spec 4)

def test_posten_ohne_produkt_und_ohne_freitext_wird_abgelehnt(con):
    with pytest.raises(orders.UngueltigerPosten):
        orders.einlegen(con)
    with pytest.raises(orders.UngueltigerPosten):
        orders.einlegen(con, free_text="   ")


def test_posten_mit_beidem_wird_abgelehnt(con):
    with pytest.raises(orders.UngueltigerPosten):
        orders.einlegen(con, product_id=_pid(con, MILCH), free_text="Milch")


def test_datenbank_lehnt_einen_posten_ohne_beides_ebenfalls_ab(con):
    korb = orders.warenkorb(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO order_item (order_id, qty) VALUES (?, 1)",
                    (korb,))


def test_gleiches_produkt_zweimal_erhoeht_die_menge(con):
    pid = _pid(con, MILCH)
    erste = orders.einlegen(con, product_id=pid)
    zweite = orders.einlegen(con, product_id=pid)
    assert erste == zweite
    zeilen = orders.inhalt(con)
    assert len(zeilen) == 1
    assert zeilen[0]["qty"] == 2


def test_freitext_bekommt_einen_namen_wie_ein_produkt(con):
    orders.einlegen(con, free_text="Brötchen vom Bäcker")
    zeile = orders.inhalt(con)[0]
    assert zeile["name"] == "Brötchen vom Bäcker"
    assert zeile["ist_freitext"] is True
    assert zeile["product_id"] is None


# --------------------------------------------------------------------------
# Menge, Laden, Löschen

def test_menge_setzen_und_null_entfernt_die_zeile(con):
    item = orders.einlegen(con, product_id=_pid(con, MILCH))
    assert orders.menge_setzen(con, item, 4) == 4
    assert orders.inhalt(con)[0]["qty"] == 4
    assert orders.menge_setzen(con, item, 0) == 0
    assert orders.inhalt(con) == []


def test_entfernen_nimmt_die_zeile_weg(con):
    item = orders.einlegen(con, free_text="Klopapier")
    orders.entfernen(con, item)
    assert orders.inhalt(con) == []


def test_unbekannter_laden_wird_abgelehnt(con):
    item = orders.einlegen(con, product_id=_pid(con, MILCH))
    with pytest.raises(orders.UngueltigerPosten):
        orders.laden_setzen(con, item, "aldi")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE order_item SET store = 'aldi' WHERE id = ?", (item,))


def test_an_abgeschickter_bestellung_wird_nichts_mehr_geaendert(con):
    item = orders.einlegen(con, product_id=_pid(con, MILCH))
    orders.abschicken(con)
    with pytest.raises(orders.UngueltigerPosten):
        orders.menge_setzen(con, item, 2)
    with pytest.raises(orders.UngueltigerPosten):
        orders.entfernen(con, item)


# --------------------------------------------------------------------------
# Ladenvorbelegung aus der letzten Wahl (Spec 4)

def test_ohne_geschichte_ist_der_laden_egal(con):
    assert orders.vorbelegter_laden(con, product_id=_pid(con, MILCH)) == "egal"
    item = orders.einlegen(con, product_id=_pid(con, MILCH))
    assert orders.inhalt(con)[0]["store"] == "egal"
    assert item


def test_letzte_wahl_wird_uebernommen(con):
    pid = _pid(con, MILCH)
    item = orders.einlegen(con, product_id=pid)
    orders.laden_setzen(con, item, "lidl")
    orders.abschicken(con)

    assert orders.vorbelegter_laden(con, product_id=pid) == "lidl"
    neu = orders.einlegen(con, product_id=pid)
    assert orders.inhalt(con)[0]["store"] == "lidl"
    assert neu


def test_die_juengste_wahl_gewinnt(con):
    pid = _pid(con, MILCH)
    for laden in ("lidl", "rewe"):
        item = orders.einlegen(con, product_id=pid)
        orders.laden_setzen(con, item, laden)
        orders.abschicken(con)
    assert orders.vorbelegter_laden(con, product_id=pid) == "rewe"


def test_vorbelegung_gilt_je_produkt_und_nicht_pauschal(con):
    milch, hafer = _pid(con, MILCH), _pid(con, HAFER)
    item = orders.einlegen(con, product_id=milch)
    orders.laden_setzen(con, item, "rewe")
    orders.abschicken(con)
    assert orders.vorbelegter_laden(con, product_id=hafer) == "egal"


def test_vorbelegung_gilt_auch_fuer_freitext(con):
    item = orders.einlegen(con, free_text="Brötchen")
    orders.laden_setzen(con, item, "lidl")
    orders.abschicken(con)
    assert orders.vorbelegter_laden(con, free_text="Brötchen") == "lidl"
    assert orders.vorbelegter_laden(con, free_text="Brezeln") == "egal"


# --------------------------------------------------------------------------
# Abschicken: draft -> offen

def test_abschicken_wechselt_den_zustand_und_setzt_die_zeit(con):
    korb = orders.warenkorb(con)
    orders.einlegen(con, product_id=_pid(con, MILCH))
    bestellung = orders.abschicken(con)

    assert bestellung["id"] == korb
    assert bestellung["state"] == "offen"
    assert bestellung["submitted_at"]
    assert bestellung["done_at"] is None


def test_abschicken_kopiert_die_posten_nicht(con):
    orders.einlegen(con, product_id=_pid(con, MILCH))
    b = orders.abschicken(con)
    assert con.execute("SELECT count(*) n FROM order_item").fetchone()["n"] == 1
    assert len(orders.posten(con, b["id"])) == 1


def test_nach_dem_abschicken_faengt_ein_neuer_korb_leer_an(con):
    orders.einlegen(con, product_id=_pid(con, MILCH))
    alt = orders.abschicken(con)["id"]
    neu = orders.warenkorb(con)
    assert neu != alt
    assert orders.inhalt(con) == []


def test_leerer_warenkorb_wird_nicht_abgeschickt(con):
    with pytest.raises(orders.LeererWarenkorb):
        orders.abschicken(con)
    orders.warenkorb(con)
    with pytest.raises(orders.LeererWarenkorb):
        orders.abschicken(con)


def test_notiz_wird_mitgeschickt(con):
    orders.einlegen(con, free_text="Blumen")
    assert orders.abschicken(con, note="bis Freitag")["note"] == "bis Freitag"


# --------------------------------------------------------------------------
# Zustandsmaschine — genau drei Zustände (Spec 4)

def test_es_gibt_genau_drei_zustaende():
    assert db.ORDER_STATES == ("draft", "offen", "erledigt")
    assert "unterwegs" not in db.ORDER_STATES
    assert set(orders.UEBERGAENGE) == set(db.ORDER_STATES)


def test_draft_kann_nicht_direkt_erledigt_werden(con):
    korb = orders.warenkorb(con)
    with pytest.raises(orders.FalscherZustand):
        orders.wechsle(con, korb, "erledigt")


def test_die_datenbank_kennt_keinen_vierten_zustand(con):
    korb = orders.warenkorb(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE orders SET state = 'unterwegs' WHERE id = ?", (korb,))


# --------------------------------------------------------------------------
# Pick-Ansicht: nach Laden getrennt, abhaken bis erledigt

@pytest.fixture
def bestellung_mit_drei(con):
    """Eine offene Bestellung: Rewe, Lidl und ein Freitext ohne Laden."""
    milch = orders.einlegen(con, product_id=_pid(con, MILCH))
    orders.laden_setzen(con, milch, "rewe")
    hafer = orders.einlegen(con, product_id=_pid(con, HAFER))
    orders.laden_setzen(con, hafer, "lidl")
    orders.einlegen(con, free_text="Klopapier")
    return orders.abschicken(con)["id"]


def test_pick_ansicht_gruppiert_nach_laden(con, bestellung_mit_drei):
    gruppen = orders.nach_laden(con, bestellung_mit_drei)
    assert [g["store"] for g in gruppen] == ["rewe", "lidl", "egal"]
    assert [g["titel"] for g in gruppen] == ["Rewe", "Lidl", "Egal wo"]
    assert [len(g["posten"]) for g in gruppen] == [1, 1, 1]
    assert gruppen[0]["posten"][0]["name"] == MILCH
    assert gruppen[2]["posten"][0]["name"] == "Klopapier"


def test_leere_ladengruppen_erscheinen_nicht(con):
    orders.einlegen(con, free_text="Klopapier")
    b = orders.abschicken(con)["id"]
    assert [g["store"] for g in orders.nach_laden(con, b)] == ["egal"]


def test_ein_offener_posten_verhindert_erledigt(con, bestellung_mit_drei):
    zeilen = orders.posten(con, bestellung_mit_drei)
    for zeile in zeilen[:-1]:
        stand = orders.abhaken(con, zeile["id"])
        assert stand["state"] == "offen"
        assert stand["done_at"] is None
    assert _zustand(con, bestellung_mit_drei) == "offen"


def test_alle_posten_abgehakt_macht_die_bestellung_erledigt(
        con, bestellung_mit_drei):
    for zeile in orders.posten(con, bestellung_mit_drei):
        stand = orders.abhaken(con, zeile["id"])
    assert stand["state"] == "erledigt"
    assert stand["done_at"]
    assert _zustand(con, bestellung_mit_drei) == "erledigt"


def test_haken_wieder_wegnehmen_oeffnet_die_bestellung_erneut(
        con, bestellung_mit_drei):
    zeilen = orders.posten(con, bestellung_mit_drei)
    for zeile in zeilen:
        orders.abhaken(con, zeile["id"])
    stand = orders.abhaken(con, zeilen[0]["id"], gepickt=False)
    assert stand["state"] == "offen"
    assert stand["done_at"] is None


def test_im_warenkorb_wird_nichts_abgehakt(con):
    item = orders.einlegen(con, product_id=_pid(con, MILCH))
    with pytest.raises(orders.UngueltigerPosten):
        orders.abhaken(con, item)


def test_unbekannter_posten_wird_nicht_abgehakt(con):
    with pytest.raises(orders.UngueltigerPosten):
        orders.abhaken(con, 999999)


# --------------------------------------------------------------------------
# „Gab's nicht": der dritte Stand eines Postens (WB-373)
#
# Vorher wurde eine Bestellung ausschliesslich dadurch fertig, dass JEDER
# Posten abgehakt war. Wer vor einem leeren Regal stand, hatte zwei
# Möglichkeiten: falsch abhaken — dann lügt die Historie — oder die Bestellung
# für immer offen lassen. Der häufigste Fall des Einkaufens war der einzige,
# den die Liste nicht kannte.

def test_ein_nicht_bekommener_posten_schliesst_die_bestellung_ab(
        con, bestellung_mit_drei):
    zeilen = orders.posten(con, bestellung_mit_drei)
    for zeile in zeilen[:-1]:
        orders.abhaken(con, zeile["id"])
    assert _zustand(con, bestellung_mit_drei) == "offen"

    stand = orders.setze_stand(con, zeilen[-1]["id"], "fehlt")
    assert stand["state"] == "erledigt"
    assert stand["done_at"]


def test_gabs_nicht_laesst_sich_zuruecknehmen(con, bestellung_mit_drei):
    """Der Fehlgriff ist im Laden wahrscheinlicher als sonstwo (WB-361)."""
    zeilen = orders.posten(con, bestellung_mit_drei)
    for zeile in zeilen[:-1]:
        orders.abhaken(con, zeile["id"])
    orders.setze_stand(con, zeilen[-1]["id"], "fehlt")

    zurueck = orders.setze_stand(con, zeilen[-1]["id"], "offen")
    assert zurueck["state"] == "offen"
    assert zurueck["done_at"] is None
    assert orders.posten(con, bestellung_mit_drei)[-1]["stand"] == "offen"


def test_ein_posten_ist_nie_abgehakt_und_vermisst_zugleich(
        con, bestellung_mit_drei):
    """Beides zugleich wäre im Laden nicht darstellbar und nicht zählbar."""
    item = orders.posten(con, bestellung_mit_drei)[0]["id"]
    orders.abhaken(con, item)
    orders.setze_stand(con, item, "fehlt")
    zeile = orders.posten(con, bestellung_mit_drei)[0]
    assert zeile["picked_at"] is None and zeile["missing_at"]
    assert zeile["stand"] == "fehlt" and zeile["gepickt"] is False

    orders.abhaken(con, item)
    zeile = orders.posten(con, bestellung_mit_drei)[0]
    assert zeile["missing_at"] is None and zeile["picked_at"]
    assert zeile["stand"] == "gepickt"


def test_ein_erfundener_stand_wird_abgelehnt(con, bestellung_mit_drei):
    item = orders.posten(con, bestellung_mit_drei)[0]["id"]
    with pytest.raises(orders.UngueltigerPosten):
        orders.setze_stand(con, item, "ausverkauft")
    assert orders.posten(con, bestellung_mit_drei)[0]["stand"] == "offen"


def test_im_warenkorb_gibt_es_auch_kein_gabs_nicht(con):
    item = orders.einlegen(con, product_id=_pid(con, MILCH))
    with pytest.raises(orders.UngueltigerPosten):
        orders.setze_stand(con, item, "fehlt")


def test_ein_vermisster_posten_zaehlt_in_seiner_gruppe_nicht_als_offen(
        con, bestellung_mit_drei):
    zeilen = orders.posten(con, bestellung_mit_drei)
    orders.setze_stand(con, zeilen[0]["id"], "fehlt")
    rewe = orders.nach_laden(con, bestellung_mit_drei)[0]
    assert rewe["n_offen"] == 0
    assert rewe["n_fehlt"] == 1 and rewe["n_geholt"] == 0


def test_die_uebersicht_zaehlt_geholt_und_vermisst_getrennt(
        con, bestellung_mit_drei):
    """„9 geholt, 2 nicht bekommen" braucht beide Zahlen.

    `n_geholt` ist NICHT `n_posten - n_offen`: dazwischen liegt genau das,
    was im Laden fehlte. Ohne die eigene Zahl sähe eine Bestellung mit Lücken
    aus wie eine, in der alles im Wagen lag.
    """
    zeilen = orders.posten(con, bestellung_mit_drei)
    orders.abhaken(con, zeilen[0]["id"])
    orders.setze_stand(con, zeilen[1]["id"], "fehlt")

    b = orders.bestellungen(con, "offen")[0]
    assert (b["n_posten"], b["n_offen"], b["n_geholt"], b["n_fehlt"]) \
        == (3, 1, 1, 1)

    orders.abhaken(con, zeilen[2]["id"])
    b = orders.bestellungen(con, "erledigt")[0]
    assert (b["n_posten"], b["n_offen"], b["n_geholt"], b["n_fehlt"]) \
        == (3, 0, 2, 1)


# --------------------------------------------------------------------------
# Bestellübersicht: offene oben, erledigte darunter (Spec 9)

def test_uebersicht_sortiert_offene_vor_erledigte(con):
    orders.einlegen(con, free_text="alt")
    alt = orders.abschicken(con)["id"]
    for zeile in orders.posten(con, alt):
        orders.abhaken(con, zeile["id"])

    orders.einlegen(con, free_text="neu")
    neu = orders.abschicken(con)["id"]

    liste = orders.bestellungen(con)
    assert [b["id"] for b in liste] == [neu, alt]
    assert [b["state"] for b in liste] == ["offen", "erledigt"]
    assert liste[0]["n_posten"] == 1 and liste[0]["n_offen"] == 1
    assert liste[1]["n_offen"] == 0


def test_der_warenkorb_steht_nicht_in_der_bestelluebersicht(con):
    orders.einlegen(con, free_text="Klopapier")
    assert orders.bestellungen(con) == []


def test_naechste_ist_die_aelteste_offene(con):
    ids = []
    for text in ("erste", "zweite"):
        orders.einlegen(con, free_text=text)
        ids.append(orders.abschicken(con)["id"])
    assert orders.naechste(con) == ids[0]


def test_ohne_offene_bestellung_gibt_es_keine_naechste(con):
    assert orders.naechste(con) is None


# --------------------------------------------------------------------------
# Ein Freitext-Posten läuft vollständig durch (Spec 4)

def test_freitext_laeuft_von_einlegen_bis_abhaken_durch(con):
    item = orders.einlegen(con, free_text="Brötchen vom Bäcker", qty=3)
    orders.laden_setzen(con, item, "lidl")
    assert orders.inhalt(con)[0]["qty"] == 3

    b = orders.abschicken(con)
    assert b["state"] == "offen"

    gruppen = orders.nach_laden(con, b["id"])
    assert [g["store"] for g in gruppen] == ["lidl"]
    zeile = gruppen[0]["posten"][0]
    assert zeile["name"] == "Brötchen vom Bäcker"
    assert zeile["ist_freitext"] is True

    stand = orders.abhaken(con, zeile["id"])
    assert stand["state"] == "erledigt"
    assert stand["done_at"]


# --------------------------------------------------------------------------
# Ausgemusterte Produkte reichen bis zum Posten durch (WB-335)
#
# Produkte werden beim Crawl nie gelöscht, sondern auf `active = 0` gesetzt
# (Spec 5.3). Ein Posten, der auf so ein Produkt zeigt, bleibt liegen — aber
# er muss es sagen können, sonst steht jemand im Laden vor einem Regal und
# sucht etwas, das es dort nicht mehr gibt.

def _ausmustern(con, name):
    con.execute("UPDATE product SET active = 0 WHERE name = ?", (name,))
    con.commit()


def test_ein_ausgemustertes_produkt_faellt_am_posten_auf(con):
    orders.einlegen(con, product_id=_pid(con, MILCH))
    _ausmustern(con, MILCH)
    zeile = orders.inhalt(con)[0]
    assert zeile["nicht_im_katalog"] is True
    # Der Posten verschwindet dabei NICHT — das wäre der schlimmste Ausgang.
    assert zeile["name"] == MILCH


def test_ein_aktives_produkt_wird_nicht_markiert(con):
    orders.einlegen(con, product_id=_pid(con, MILCH))
    assert orders.inhalt(con)[0]["nicht_im_katalog"] is False


def test_freitext_ist_nie_nicht_im_katalog(con):
    """Freitext war nie im Katalog und behauptet das auch nicht.

    Ohne diese Unterscheidung bekäme jede Freitext-Zeile eine Warnung, die
    nicht stimmt — und eine Warnung, die an allem steht, liest im Laden
    niemand mehr.
    """
    orders.einlegen(con, free_text="Brötchen vom Bäcker")
    zeile = orders.inhalt(con)[0]
    assert zeile["ist_freitext"] is True
    assert zeile["nicht_im_katalog"] is False


def test_die_markierung_haelt_bis_in_die_pick_ansicht(con):
    orders.einlegen(con, product_id=_pid(con, MILCH))
    orders.einlegen(con, free_text="Klopapier")
    _ausmustern(con, MILCH)
    b = orders.abschicken(con)

    zeilen = [z for g in orders.nach_laden(con, b["id"]) for z in g["posten"]]
    nach_namen = {z["name"]: z["nicht_im_katalog"] for z in zeilen}
    assert nach_namen == {MILCH: True, "Klopapier": False}


def _katalogfelder(eintrag: dict) -> set:
    """Die Felder eines Eintrags, die vom Katalogstand sprechen."""
    return {k for k in eintrag if "katalog" in k}


def test_posten_und_zutat_benutzen_denselben_feldnamen(con):
    """Ein Produkt, zwei Wege, EIN Name für dieselbe Sache.

    Geprüft wird gegen die echten Rückgaben von `recipes.zutaten()` und
    `orders.posten()`, nicht gegen die Zeichenkette „nicht_im_katalog": ein
    Test, der den Namen selbst noch einmal hinschreibt, merkt gerade dann
    nichts, wenn eine der beiden Seiten ihn ändert — und zwei Namen für
    dieselbe Sache laufen irgendwann auseinander.
    """
    pid = _pid(con, MILCH)
    _ausmustern(con, MILCH)
    rid = recipes.anlegen(con, "Milchreis", zutaten=[{"product_id": pid}])
    zutat = recipes.zutaten(con, rid)[0]
    orders.einlegen(con, product_id=pid)
    posten = orders.inhalt(con)[0]

    assert _katalogfelder(zutat), "die Rezeptzutat kennt gar kein solches Feld"
    assert _katalogfelder(zutat) == _katalogfelder(posten)
    for feld in _katalogfelder(zutat):
        assert zutat[feld] is True and posten[feld] is True
