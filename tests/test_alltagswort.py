"""Alltagswörter, die der Katalog anders schreibt (Klopapier -> Toilettenpapier).

Der Fall kam aus dem Demo-Video: „alles für Lasagne, und Klopapier" liess das
Klopapier als Freitext liegen, obwohl elf Packungen im Katalog stehen — alle
als „Toilettenpapier" geschrieben. Keine Sortimentslücke, eine Vokabellücke.
"""
import pytest

from zettel.catalog import search

#: Produkte, die es in der Knuspr-Vorlage nicht gibt. Bewusst zwei Marken:
#: eine Übersetzung, die nur ein Produkt findet, sähe wie ein Zufallstreffer
#: aus.
ZUSATZ = (
    ("tp-1", "Moddia Toilettenpapier 3-lagig"),
    ("tp-2", "Danke Toilettenpapier 4-lagig"),
    ("sp-1", "Pril Spülmittel Original"),
)


def _zusatz(con):
    for external_id, name in ZUSATZ:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 199, '1 Stk', 'Haushalt', 'Papier', '')",
            (external_id, name))
    con.commit()


@pytest.fixture
def con(vorlagen):
    c = vorlagen.con("alltagswort_katalog", vorlagen.katalog, _zusatz)
    yield c
    c.close()


def test_ladenwort_findet_das_wort_des_ladens():
    assert search.ladenwort("Klopapier") == "Toilettenpapier"


def test_ladenwort_ist_gleichgueltig_gegen_schreibung_und_leerraum():
    assert search.ladenwort("  KLOPAPIER ") == "Toilettenpapier"


def test_ladenwort_schweigt_zu_allem_anderen():
    assert search.ladenwort("Mehl") is None


def test_das_getippte_wort_findet_von_sich_aus_nichts(con):
    """Die Voraussetzung des ganzen Mechanismus — sonst braucht es ihn nicht."""
    assert search.search(con, "Klopapier", limit=5) == []


def test_leere_kette_wird_uebersetzt(con):
    treffer = search.suche_kette(con, ["Klopapier"])
    assert treffer, "Klopapier muss über das Ladenwort Produkte finden"
    # `via` nennt das LADENWORT, nicht das getippte: „Klopapier" stünde sonst
    # an einem Produkt, in dem es gar nicht vorkommt.
    assert all(t["via"] == "Toilettenpapier" for t in treffer)
    assert all("Toilettenpapier" in t["name"] for t in treffer)


def test_kette_die_traegt_bleibt_unberuehrt(con):
    """Solange irgendein Begriff Treffer bringt, wird NICHT übersetzt.

    Sonst zöge das Ladenwort breitere Kandidaten in eine Kette, die schon eine
    genauere Auskunft hatte.
    """
    treffer = search.suche_kette(con, ["Toilettenpapier", "Klopapier"])
    assert treffer
    assert {t["via"] for t in treffer} == {"Toilettenpapier"}


def test_uebersetzung_achtet_die_obergrenze(con):
    assert len(search.suche_kette(con, ["Klopapier"], obergrenze=1)) == 1


def test_ohne_passendes_ladenwort_bleibt_es_leer(con):
    assert search.suche_kette(con, ["Schrumpelfrikandel"]) == []
