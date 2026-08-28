"""Tests für Katalogsuche und Kategoriebaum (WB-322).

KEIN Test geht ins Netz. Die Produkte kommen aus derselben aufgezeichneten
Knuspr-Antwort wie in `test_knuspr.py` und werden durch den echten Crawler in
eine `:memory:`-Datenbank geschrieben — so ist mitgeprüft, dass die
FTS-Trigger auf dem Weg greifen, den der Katalog im Betrieb wirklich nimmt.
Alles, was die Fixture nicht hergibt (Spülmittel, ein inaktives Produkt),
wird daneben von Hand eingefügt.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from picknick import db
from picknick.catalog import categories, search
from picknick.scrapers import knuspr

FIXTURE = Path(__file__).parent / "fixtures" / "knuspr_milch.json"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"


class FakeHTTP:
    """Liefert die Fixture als erste Seite und danach nichts mehr."""

    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, url):
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


def _insert(con, external_id, name, brand=None,
            l1=None, l2=None, l3=None, active=1):
    con.execute(
        "INSERT INTO product (source, external_id, name, brand,"
        " category_l1, category_l2, category_l3, active)"
        " VALUES ('knuspr', ?, ?, ?, ?, ?, ?, ?)",
        (external_id, name, brand, l1, l2, l3, active))
    con.commit()


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.migrate(c)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # totalHits der Fixture ist grösser als die eine aufgezeichnete Seite; der
    # Crawler fragt danach eine leere zweite Seite ab und bricht ab.
    knuspr.crawl(c, FakeHTTP([payload]), ["milch"], pause_s=0)
    yield c
    c.close()


# --------------------------------------------------------------------------
# Suche

def test_milch_findet_die_landmilch(con):
    namen = [t["name"] for t in search.search(con, "milch", limit=50)]
    assert MILCH in namen


def test_treffer_bringt_die_produktdaten_mit(con):
    treffer = [t for t in search.search(con, "landmilch") if t["name"] == MILCH]
    assert treffer, "Referenzprodukt aus Spec-Anhang A nicht gefunden"
    milch = treffer[0]
    assert milch["price_cents"] == 119
    assert milch["external_id"] == "95793"
    assert milch["category_l2"] == "Milch"


@pytest.mark.parametrize("begriff", ["spuelmittel", "Spülmittel",
                                     "SPUELMITTEL", "spülmittel"])
def test_umlaut_und_ue_schreibweise_finden_dasselbe(con, begriff):
    _insert(con, "9001", "Frosch Spülmittel Zitrone", "Frosch",
            "Haushalt", "Reinigen", "Spülmittel")
    treffer = [t["name"] for t in search.search(con, begriff)]
    assert treffer == ["Frosch Spülmittel Zitrone"]


@pytest.mark.parametrize("begriff", ["fuenf", "Fünf", "FUENF"])
def test_ue_auch_am_wortanfang(con, begriff):
    _insert(con, "9002", "Fünf Korn Knäckebrot", "Wasa")
    assert [t["name"] for t in search.search(con, begriff)] \
        == ["Fünf Korn Knäckebrot"]


@pytest.mark.parametrize("begriff", ["strasse", "Straße", "STRASSE"])
def test_scharfes_s_wird_zu_ss(con, begriff):
    _insert(con, "9003", "Straßenbäcker Landbrot", "Straßenbäcker")
    assert [t["name"] for t in search.search(con, begriff)] \
        == ["Straßenbäcker Landbrot"]


def test_inaktives_produkt_taucht_nicht_auf(con):
    _insert(con, "9004", "Spülmittel Sensitiv", active=0)
    _insert(con, "9005", "Spülmittel Zitrone", active=1)
    # Ein deaktiviertes Produkt bleibt in `product` und im FTS-Index stehen
    # (Spec 5.3, kein Löschen) — die Suche muss es trotzdem verschweigen.
    assert [t["name"] for t in search.search(con, "spuelmittel")] \
        == ["Spülmittel Zitrone"]


def test_rang_ist_dabei_und_faellt_monoton(con):
    treffer = search.search(con, "milch", limit=50)
    assert len(treffer) > 1
    raenge = [t["rang"] for t in treffer]
    assert all(isinstance(r, float) for r in raenge)
    assert raenge == sorted(raenge, reverse=True), "nicht nach Relevanz sortiert"


def test_namenstreffer_schlaegt_kategorietreffer(con):
    # Beide liegen in der Kategorie „Spülmittel"; nur eines heisst so. Ohne
    # Spaltengewichtung entschiede die Textlänge — und der Rang wäre als Score
    # im RETRIEVER-Span (Spec 7.1) nichts wert.
    _insert(con, "9006", "Frosch Spülmittel Zitrone", "Frosch",
            "Haushalt", "Reinigen", "Spülmittel")
    _insert(con, "9007", "Schwammtuch", "Vileda",
            "Haushalt", "Reinigen", "Spülmittel")
    treffer = search.search(con, "spuelmittel")
    assert [t["name"] for t in treffer] == ["Frosch Spülmittel Zitrone",
                                            "Schwammtuch"]
    assert treffer[0]["rang"] > treffer[1]["rang"]


@pytest.mark.parametrize("eingabe", [
    '"', 'milch"', '"milch', "milch*", "milch AND OR NOT", "NEAR(a b",
    "(", "^milch", "milch:brand", "'; DROP TABLE product; --",
    "milch -butter", "{norm_name}", "  ", "", "***", "😀",
])
def test_sonderzeichen_sprengen_die_abfrage_nicht(con, eingabe):
    # Ein Anführungszeichen im Suchfeld darf keinen OperationalError auslösen.
    ergebnis = search.search(con, eingabe)
    assert isinstance(ergebnis, list)


def test_eingabe_ohne_wortzeichen_liefert_nichts(con):
    assert search.search(con, "!!!") == []
    assert search.search(con, "") == []


def test_mehrere_woerter_werden_und_verknuepft(con):
    _insert(con, "9008", "Frosch Spülmittel Zitrone", "Frosch",
            "Haushalt", "Reinigen", "Spülmittel")
    assert [t["name"] for t in search.search(con, "frosch spuelmittel")] \
        == ["Frosch Spülmittel Zitrone"]
    # „zitrone klopapier" hat keinen gemeinsamen Treffer.
    assert search.search(con, "zitrone klopapier") == []


def test_limit_wird_eingehalten(con):
    assert len(search.search(con, "milch", limit=3)) == 3


# --------------------------------------------------------------------------
# Die Begriffskette einer Zutat (WB-340)

def _kette_katalog(con):
    """Der gemessene Fall aus WB-340, in klein.

    „Auberginen" (Plural) findet nur das Fertiggericht — der Plural steckt in
    dessen Namen. Die echten Auberginen heissen im Katalog Einzahl und tragen
    einen höheren Preis als Rang: sie kommen erst über den zweiten Begriff,
    und mit schlechterem bm25.
    """
    _insert(con, "aub1", "Gemüse-Auberginen-Masala mit Jasminreis", None,
            "Fertiggerichte", "Indisch", "Masala")
    _insert(con, "aub2", "Aubergine, 1 Stk.", None,
            "Obst & Gemüse", "Gemüse", "Fruchtgemüse")
    _insert(con, "aub3", "BIO Aubergine, 1 Stk.", None,
            "Obst & Gemüse", "Gemüse", "Fruchtgemüse")


def test_kette_vereinigt_statt_beim_ersten_treffer_aufzuhoeren(con):
    """Der Kern von WB-340: „erster Begriff, der etwas findet" ist schlechter.

    Der erste Begriff findet etwas — und zwar das Falsche. Die Vereinigung
    legt beides vor.
    """
    _kette_katalog(con)
    allein = [t["name"] for t in search.search(con, "Auberginen")]
    assert allein == ["Gemüse-Auberginen-Masala mit Jasminreis"]

    treffer = search.suche_kette(con, ["Auberginen", "Aubergine"])
    assert sorted(t["name"] for t in treffer) == [
        "Aubergine, 1 Stk.", "BIO Aubergine, 1 Stk.",
        "Gemüse-Auberginen-Masala mit Jasminreis"]


def test_kette_entdoppelt_nach_produkt_id_und_merkt_sich_die_herkunft(con):
    """Beide Begriffe finden dasselbe Produkt. Es steht einmal da — mit dem
    genaueren Begriff als Herkunft."""
    _kette_katalog(con)
    treffer = search.suche_kette(con, ["Aubergine", "Auberginen"])
    ids = [t["id"] for t in treffer]
    assert len(ids) == len(set(ids)) == 3
    via = {t["name"]: t["via"] for t in treffer}
    assert set(via.values()) == {"Aubergine"}, (
        "Der Plural findet nur, was der Singular schon hatte.")

    andersrum = {t["name"]: t["via"] for t in
                 search.suche_kette(con, ["Auberginen", "Aubergine"])}
    assert andersrum["Gemüse-Auberginen-Masala mit Jasminreis"] == "Auberginen"
    assert andersrum["Aubergine, 1 Stk."] == "Aubergine"


def test_die_kette_bestimmt_die_reihenfolge_und_nicht_der_rang(con):
    """Global nach `rang` zu sortieren wäre falsch — und zwar gemessen.

    bm25 ist über Abfragen hinweg nicht geeicht: ein seltenes Wort bekommt
    strukturell einen höheren Rang als ein häufiges. In der Kette
    [Mais, Körnig] steht der „Körnige Frischkäse" mit 14,01 über der
    „Maishähnchenkeule" mit 9,70 — nicht weil er besser passt, sondern weil
    „Körnig" seltener ist. Nach Rang sortiert bekäme Stufe 3 den Frischkäse
    zuerst vorgelegt.

    Die Kettenreihenfolge ist die einzige Rangfolge, die hier etwas bedeutet:
    sie kommt vom Modell und ist nach Genauigkeit geordnet.
    """
    # „Mais" steckt in vielem und ist deshalb ein häufiges Wort; „Körnig"
    # kommt einmal vor. Genau daraus baut bm25 seinen Rang — und genau
    # deshalb sind Ränge aus zwei Abfragen nicht vergleichbar.
    for i, name in enumerate(["Prignitzer Maishähnchenkeule",
                              "Fackelmann Maiskolbenhalter",
                              "Seeberger Popcorn Mais",
                              "Maiskörner in der Dose",
                              "Maisstärke"]):
        _insert(con, f"mais{i}", name, None, "Diverses", "Mais", None)
    _insert(con, "kf1", "MIIL Körniger Frischkäse", None,
            "Milch, Molkerei & Butter", "Frischkäse", None)

    treffer = search.suche_kette(con, ["Mais", "Körnig"])
    assert [t["via"] for t in treffer][-1] == "Körnig"
    assert treffer[-1]["name"] == "MIIL Körniger Frischkäse"
    # Der spätere Begriff hat den HÖHEREN Rang und steht trotzdem hinten.
    assert treffer[-1]["rang"] > treffer[0]["rang"]


def test_innerhalb_eines_begriffs_gilt_der_rang(con):
    _kette_katalog(con)
    raenge = [t["rang"] for t in search.suche_kette(con, ["Aubergine"])]
    assert raenge == sorted(raenge, reverse=True)


def test_die_obergrenze_kuerzt_am_allgemeinen_ende_der_kette(con):
    """Das Budget wird vom genauen Ende der Kette her ausgegeben.

    Der letzte Begriff ist der, bei dem das Modell entgleist („Körnig",
    „Papikra"). Was er findet, soll als Letztes stehen und als Erstes
    wegfallen — und der genaueste Begriff darf nie darunter leiden.
    """
    _kette_katalog(con)
    treffer = search.suche_kette(con, ["Aubergine", "milch"], limit=5,
                                 obergrenze=4)
    assert len(treffer) == 4
    assert [t["via"] for t in treffer] == ["Aubergine"] * 3 + ["milch"]


def test_die_obergrenze_laesst_den_ersten_beiden_begriffen_alles(con):
    """Die Vorgabe nimmt nie weg, was der Agent vor WB-340 gesehen hätte:
    2 × `KANDIDATEN` fasst die vollen Treffer der ersten beiden Begriffe."""
    from picknick.assistant import plan

    _kette_katalog(con)
    treffer = search.suche_kette(con, ["milch", "Aubergine", "Gemüse"],
                                 limit=plan.KANDIDATEN,
                                 obergrenze=plan.MAX_KANDIDATEN)
    via = [t["via"] for t in treffer]
    assert via.count("milch") == plan.KANDIDATEN
    assert via.count("Aubergine") == 3
    assert len(treffer) <= plan.MAX_KANDIDATEN


def test_ohne_obergrenze_kommt_alles_mit(con):
    _kette_katalog(con)
    treffer = search.suche_kette(con, ["milch", "Aubergine"], limit=5)
    assert len(treffer) == 5 + 3


def test_kette_ohne_treffer_ist_leer_und_nicht_kaputt(con):
    assert search.suche_kette(con, ["Zahnstocher", "Zahnstocherchen"]) == []
    assert search.suche_kette(con, []) == []


def test_alte_datenbank_wird_nachgezogen(tmp_path):
    """Eine vor WB-322 angelegte Datei muss nach `migrate()` umlautfest sein."""
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.execute("""CREATE TABLE product (
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
        name TEXT NOT NULL, brand TEXT, price_cents INTEGER,
        price_per_unit_cents INTEGER, unit_text TEXT, unit TEXT,
        image_path TEXT, category_l1 TEXT, category_l2 TEXT, category_l3 TEXT,
        in_stock INTEGER NOT NULL DEFAULT 1, last_seen_at TEXT,
        active INTEGER NOT NULL DEFAULT 1, UNIQUE (source, external_id))""")
    alt.execute("""CREATE VIRTUAL TABLE product_fts USING fts5(
        name, brand, category_l1, category_l2, category_l3,
        content='product', content_rowid='id')""")
    alt.execute("INSERT INTO product (source, external_id, name)"
                " VALUES ('knuspr', '1', 'Frosch Spülmittel')")
    alt.commit()
    alt.close()

    neu = db.connect(pfad)
    db.migrate(neu)
    db.migrate(neu)          # zweimal: die Migration muss idempotent bleiben
    assert [t["name"] for t in search.search(neu, "spuelmittel")] \
        == ["Frosch Spülmittel"]
    neu.close()


# --------------------------------------------------------------------------
# Kategoriebaum

def test_baum_ist_dreistufig_verschachtelt(con):
    baum = categories.tree(con)
    l1 = {k["name"]: k for k in baum}
    assert "Milch, Molkerei & Butter" in l1

    l2 = {k["name"]: k for k in l1["Milch, Molkerei & Butter"]["kinder"]}
    assert {"Milch", "Butter & Fette"} <= set(l2)

    l3 = {k["name"] for k in l2["Milch"]["kinder"]}
    assert "Frischmilch" in l3
    # Die dritte Ebene ist die letzte.
    assert all(k["kinder"] == [] for k in l2["Milch"]["kinder"])


def test_baum_zaehlt_den_ganzen_teilbaum(con):
    baum = {k["name"]: k for k in categories.tree(con)}
    molkerei = baum["Milch, Molkerei & Butter"]
    assert molkerei["anzahl"] == sum(k["anzahl"] for k in molkerei["kinder"])
    assert molkerei["anzahl"] == categories.count_by_category(
        con, "Milch, Molkerei & Butter")


def test_baum_ist_alphabetisch_sortiert(con):
    baum = categories.tree(con)
    assert [k["name"] for k in baum] == sorted(k["name"] for k in baum)


def test_baum_zeigt_nur_aktive_produkte(con):
    _insert(con, "9009", "Klopapier", "Zewa", "Haushalt", "Papier", "WC-Papier")
    assert "Haushalt" in {k["name"] for k in categories.tree(con)}

    con.execute("UPDATE product SET active = 0 WHERE external_id = '9009'")
    con.commit()
    assert "Haushalt" not in {k["name"] for k in categories.tree(con)}


def test_baum_erfindet_keine_kategorie_fuer_produkte_ohne(con):
    _insert(con, "9010", "Etwas ohne Kategorie")
    namen = {k["name"] for k in categories.tree(con)}
    assert None not in namen and "" not in namen


# --------------------------------------------------------------------------
# Blättern

def test_by_category_filtert_auf_die_ebene(con):
    produkte = categories.by_category(con, "Milch, Molkerei & Butter", "Butter & Fette")
    assert produkte
    assert all(p["category_l2"] == "Butter & Fette" for p in produkte)


def test_by_category_blaettert_ohne_luecke_und_ohne_dopplung(con):
    ganz = categories.by_category(con, "Milch, Molkerei & Butter", limit=1000)
    assert len(ganz) > 4

    seiten = []
    for offset in range(0, len(ganz), 3):
        seiten += categories.by_category(
            con, "Milch, Molkerei & Butter", limit=3, offset=offset)
    assert [p["id"] for p in seiten] == [p["id"] for p in ganz]
    # Hinter dem Ende kommt nichts nach.
    assert categories.by_category(
        con, "Milch, Molkerei & Butter", limit=3, offset=len(ganz)) == []


def test_by_category_ohne_angabe_ist_der_ganze_katalog(con):
    alle = categories.by_category(con, limit=1000)
    n = con.execute(
        "SELECT count(*) AS n FROM product WHERE active = 1").fetchone()["n"]
    assert len(alle) == n


def test_by_category_zeigt_keine_inaktiven(con):
    _insert(con, "9011", "Klopapier", "Zewa", "Haushalt", "Papier", "WC-Papier",
            active=0)
    assert categories.by_category(con, "Haushalt") == []
    assert categories.count_by_category(con, "Haushalt") == 0
