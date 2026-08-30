"""Dass die Vorlage-Datenbank je Test wirklich frisch ist (WB-366).

Seit WB-366 baut nicht mehr jeder Test seine Datenbank neu, sondern kopiert
eine Vorlage, die einmal je Testlauf gebaut wird. Das ist 250-mal schneller —
und es ist genau die Sorte Abkürzung, die still schiefgehen kann: teilten sich
zwei Tests versehentlich EINE Datenbank, dann sähe der zweite, was der erste
angerichtet hat, und beide wären ab da wertlos, ohne rot zu werden.

Diese Datei ist der Beleg, dass das nicht passiert. Sie prüft drei Dinge
getrennt:

* die Vorlage wird wirklich nur EINMAL gebaut (sonst wäre nichts gewonnen);
* zwei Kopien derselben Vorlage sind voneinander unabhängig, und die Vorlage
  selbst bleibt unangetastet;
* was ein Test an seiner Kopie ändert, ist im nächsten Test weg — für die
  Datei-Fixture (`db_datei`) und für die Speicher-Fixture (`katalog_con`)
  einzeln, denn es sind zwei verschiedene Kopierwege.

Dazu kommt die Prüfung, dass die Kopie eine VOLLWERTIGE Datenbank ist und
nicht bloss die Produktzeilen: der FTS-Index und seine Trigger müssen den
Kopiervorgang überlebt haben, sonst suchten alle Katalogtests ab jetzt ins
Leere und merkten es nicht.
"""
import sqlite3

import pytest

from zettel import db
from zettel.catalog import search

#: So viele Produkte hat die aufgezeichnete Knuspr-Antwort.
PRODUKTE = 28
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"


def _zahl(con):
    return con.execute("SELECT count(*) AS n FROM product").fetchone()["n"]


# --------------------------------------------------------------------------
# Einmal bauen, danach nur kopieren

def test_die_vorlage_wird_nur_einmal_gebaut(vorlagen):
    """Zweimal gefragt, dieselbe Datei — und sie ist nicht neu geschrieben."""
    erst = vorlagen.vorlage("katalog", vorlagen.katalog)
    stand = erst.stat().st_mtime_ns
    nochmal = vorlagen.vorlage("katalog", vorlagen.katalog)

    assert nochmal == erst
    assert nochmal.stat().st_mtime_ns == stand, (
        "Die Vorlage wurde ein zweites Mal gebaut — dann ist die ganze "
        "Übung umsonst.")


def test_derselbe_name_mit_anderer_fuellung_ist_ein_testfehler(vorlagen):
    """Der Schutz davor, dass ein Test die Datenbank eines anderen bekommt."""
    def _eins(con):
        con.execute("INSERT INTO product (source, external_id, name)"
                    " VALUES ('knuspr', 'p1', 'Eins')")

    def _zwei(con):
        con.execute("INSERT INTO product (source, external_id, name)"
                    " VALUES ('knuspr', 'p2', 'Zwei')")

    vorlagen.vorlage("wb366_probe", _eins)
    with pytest.raises(AssertionError, match="anders gefüllt"):
        vorlagen.vorlage("wb366_probe", _zwei)


# --------------------------------------------------------------------------
# Zwei Kopien, eine Vorlage — in EINEM Test, also ohne Verlass auf die
# Reihenfolge zweier Tests (unter `-n auto` liefen die auf zwei Prozessen)

def test_zwei_kopien_stoeren_einander_nicht(vorlagen, tmp_path):
    eine = db.connect(vorlagen.datei(tmp_path / "eine.db", "katalog",
                                     vorlagen.katalog))
    eine.execute("DELETE FROM product")
    eine.commit()
    assert _zahl(eine) == 0

    andere = db.connect(vorlagen.datei(tmp_path / "andere.db", "katalog",
                                       vorlagen.katalog))
    assert _zahl(andere) == PRODUKTE, (
        "Die zweite Kopie hat gesehen, was an der ersten geändert wurde.")
    eine.close()
    andere.close()


def test_die_vorlage_selbst_bleibt_unberuehrt(vorlagen, tmp_path):
    kopie = db.connect(vorlagen.datei(tmp_path / "kopie.db", "katalog",
                                      vorlagen.katalog))
    kopie.execute("DELETE FROM product")
    kopie.commit()
    kopie.close()

    vorlage = sqlite3.connect(str(vorlagen.vorlage("katalog",
                                                   vorlagen.katalog)))
    vorlage.row_factory = sqlite3.Row
    try:
        assert _zahl(vorlage) == PRODUKTE
    finally:
        vorlage.close()


def test_zwei_speicherkopien_stoeren_einander_nicht(vorlagen):
    eine = vorlagen.con("katalog", vorlagen.katalog)
    eine.execute("DELETE FROM product")
    eine.commit()

    andere = vorlagen.con("katalog", vorlagen.katalog)
    assert _zahl(andere) == PRODUKTE
    eine.close()
    andere.close()


# --------------------------------------------------------------------------
# Und über Testgrenzen hinweg: jeder Durchgang findet die Datenbank unberührt
# vor und lässt sie verwüstet zurück. Läuft ein zweiter Durchgang im selben
# Prozess, fällt eine geteilte Datenbank hier auf; unter `-n auto` können die
# Durchgänge auf verschiedene Worker fallen — deshalb stehen die beiden Tests
# oben daneben, die ohne Reihenfolge auskommen.

@pytest.mark.parametrize("durchgang", [1, 2, 3])
def test_db_datei_ist_in_jedem_durchgang_frisch(db_datei, durchgang):
    con = db.connect(db_datei)
    try:
        assert _zahl(con) == PRODUKTE
        assert con.execute(
            "SELECT count(*) AS n FROM sqlite_master WHERE name = 'wb366'"
        ).fetchone()["n"] == 0

        con.execute("DELETE FROM product")
        con.execute("CREATE TABLE wb366 (spur TEXT)")
        con.commit()
    finally:
        con.close()


@pytest.mark.parametrize("durchgang", [1, 2, 3])
def test_katalog_con_ist_in_jedem_durchgang_frisch(katalog_con, durchgang):
    assert _zahl(katalog_con) == PRODUKTE
    assert katalog_con.execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE name = 'wb366'"
    ).fetchone()["n"] == 0

    katalog_con.execute("DELETE FROM product")
    katalog_con.execute("CREATE TABLE wb366 (spur TEXT)")
    katalog_con.commit()


# --------------------------------------------------------------------------
# Die Kopie ist eine vollwertige Datenbank

def test_die_kopie_kann_suchen(katalog_con):
    """FTS-Index und Trigger haben den Kopiervorgang überlebt.

    Ohne diesen Test wäre eine kaputte Kopie unauffällig: die Katalogtests
    prüfen Treffer, und eine Kopie ohne Index liefert einfach keine — das
    fiele erst als Kette von Fehlern in `test_catalog.py` auf, ohne dass
    jemand die Ursache sähe.
    """
    assert MILCH in [t["name"] for t in search.search(katalog_con, "milch",
                                                      limit=50)]


def test_der_index_der_kopie_zieht_bei_neuen_produkten_nach(katalog_con):
    """Ein Trigger, der nur mitkopiert, aber nicht mehr feuert, wäre still."""
    katalog_con.execute(
        "INSERT INTO product (source, external_id, name, category_l1)"
        " VALUES ('knuspr', 'wb366', 'Testwurst Landjäger', 'Fleisch')")
    katalog_con.commit()

    assert "Testwurst Landjäger" in [
        t["name"] for t in search.search(katalog_con, "landjaeger", limit=50)]


def test_leere_db_datei_ist_migriert_und_leer(leere_db_datei):
    con = db.connect(leere_db_datei)
    try:
        assert _zahl(con) == 0
        # Migriert heisst: die Tabellen sind da, nicht nur die Datei.
        assert con.execute(
            "SELECT count(*) AS n FROM sqlite_master WHERE type = 'table'"
            " AND name IN ('product', 'orders', 'recipe')"
        ).fetchone()["n"] == 3
    finally:
        con.close()
