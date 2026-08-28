"""Zutatenmenge, Packungsgrösse, Einkaufsmenge — die Rechnung allein (WB-362).

Hier steht die Arithmetik ohne Datenbank: Einheiten lesen, skalieren,
zusammenzählen, aufrunden. **Der Beweis, dass der Shop das auch wirklich so
tut, steht nicht hier**, sondern in `test_portionen.py` — dort läuft alles
über `recipes.in_den_korb` und `orders.einlegen`, also über den Weg, den ein
Tipp auf „Alles in den Warenkorb" nimmt. Ein grüner Test über eine Funktion,
die im Produktivweg nicht aufgerufen wird, beweist nichts.
"""
import pytest

from picknick import mengen


# --------------------------------------------------------------------------
# Einheiten lesen

@pytest.mark.parametrize("text, erwartet", [
    ("500 g", (500.0, "g")),
    ("100 g", (100.0, "g")),
    ("1 kg", (1000.0, "g")),
    ("0,8 kg", (800.0, "g")),
    ("0,75 l", (750.0, "ml")),
    ("500 ml", (500.0, "ml")),
    ("1 l", (1000.0, "ml")),
    ("330 ml", (330.0, "ml")),
    ("1 Stk", (1.0, "Stk")),
    ("ca. 500 g", (500.0, "g")),
    # Ein Kasten: die Gesamtmenge steht vorn, und die ist gemeint.
    ("10 l 20Stk", (10000.0, "ml")),
    # „2 x 250 g" sind 500 g und nicht zwei Stück — ohne den Sonderfall läse
    # das Muster die 2 als Menge.
    ("2 x 250 g", (500.0, "g")),
])
def test_packungsgroessen_aus_dem_echten_katalog(text, erwartet):
    """Die häufigsten `unit_text`-Formen, gemessen am echten Katalog."""
    assert mengen.packungsgroesse(text) == erwartet


@pytest.mark.parametrize("text", ["", None, "   ", "Packung"])
def test_ohne_zahl_gibt_es_keine_packungsgroesse(text):
    """`None` ist eine Antwort und kein Fehler: dann wird nicht gerechnet."""
    assert mengen.packungsgroesse(text) is None


def test_eine_unbekannte_einheit_bleibt_ihre_eigene():
    """„15 Wäschen" ist lesbar und trotzdem mit Gramm nicht vergleichbar.

    Genau 34 Zeilen des echten Katalogs sehen so aus (Toilettenpapier in
    Metern, Waschmittel in Wäschen, Küchenrolle in Blatt). Sie zu einer
    Grundeinheit zu biegen wäre Raten; sie als unlesbar zu behandeln wäre eine
    zweite Unwahrheit.
    """
    assert mengen.packungsgroesse("15 Wäschen") == (15.0, "waeschen")
    assert mengen.vergleichbar("waeschen", "g") is None


@pytest.mark.parametrize("roh, erwartet", [
    ("Zehe(n)", "zehe"), ("Stange/n", "stange"), ("Pkt.", "pkt"),
    ("Stück", "stueck"), ("EL", "el"), ("", ""),
])
def test_einheiten_werden_gefaltet(roh, erwartet):
    """Einzahl und Mehrzahl sind dieselbe Einheit — sonst zählt nichts zusammen."""
    assert mengen.falte(roh) == erwartet


def test_ohne_einheit_wird_in_stueck_gezaehlt():
    """Chefkoch schreibt „2 Zwiebeln" ohne Einheit. Die 2 zählt trotzdem."""
    assert mengen.in_grundeinheit(2, None) == (2.0, "Stk")
    assert mengen.in_grundeinheit(1, "große") == (1.0, "Stk")


# --------------------------------------------------------------------------
# Skalieren

def test_ein_rezept_fuer_vier_auf_acht_verdoppelt_die_mengen():
    assert mengen.skaliere(500, 4, 8) == 1000
    assert mengen.skaliere(1, 4, 8) == 2


def test_ohne_portionszahl_wird_nicht_skaliert():
    """Ein selbst angelegtes Rezept hat keine `servings` — dann bleibt alles."""
    assert mengen.skaliere(500, None, 8) == 500
    assert mengen.skaliere(500, 4, None) == 500
    assert mengen.skaliere(500, 0, 8) == 500


def test_skalieren_rundet_nicht():
    """1 Zwiebel für 4 sind bei 6 Portionen 1,5 — und nicht 2.

    Gerundet wird die PACKUNGSZAHL und erst nach dem Zusammenzählen. Wer hier
    rundet, rundet zwischendurch, und genau das verbietet der Nachtrag.
    """
    assert mengen.skaliere(1, 4, 6) == 1.5


def test_eine_zutat_ohne_menge_bekommt_durch_skalieren_keine():
    assert mengen.skaliere(None, 4, 8) is None


# --------------------------------------------------------------------------
# Aufrunden — der letzte Schritt

def test_fuenfhundert_milliliter_in_einer_fuenfhundert_gramm_packung():
    """Das Beispiel des Tickets. ml gegen g gilt als 1:1 — und sagt es."""
    r = mengen.rechne(500, "ml", "500 g")
    assert r.packungen == 1
    assert r.annahme == mengen.DICHTE_ANNAHME


def test_tausend_milliliter_sind_zwei_packungen():
    assert mengen.rechne(1000, "ml", "500 g").packungen == 2


def test_ein_hauch_mehr_als_eine_packung_kostet_die_zweite():
    """Aufgerundet, nicht kaufmännisch gerundet: 510 g sind zwei Packungen."""
    assert mengen.rechne(510, "g", "500 g").packungen == 2


def test_weniger_als_eine_packung_bleibt_eine():
    """Wer 10 g Butter braucht, kauft kein Zehntel Päckchen."""
    assert mengen.rechne(10, "g", "250 g").packungen == 1


def test_zwei_zwiebeln_gegen_ein_kilonetz_sind_nicht_ausrechenbar():
    """Stück gegen Gewicht: nicht ausrechenbar, und der Grund steht da.

    Das ist Regel 4. Ein geratener Faktor („eine Zwiebel wiegt 80 g") wäre
    hier bequem und irgendwann falsch — und niemand könnte sagen, woher die
    Zahl kam.
    """
    r = mengen.rechne(2, None, "1 kg")
    assert r.packungen is None
    assert not r.ausrechenbar
    assert "1 kg" in r.grund


def test_eine_stange_gegen_ein_gewicht_ist_nicht_ausrechenbar():
    """Der Fall aus dem Ticket, wörtlich."""
    r = mengen.rechne(1, "Stange/n", "ca. 500 g")
    assert not r.ausrechenbar
    assert "500 g" in r.grund


def test_ohne_lesbare_packungsgroesse_wird_nicht_gerechnet():
    r = mengen.rechne(200, "g", None)
    assert not r.ausrechenbar
    assert r.bedarf == 200


def test_ohne_bedarf_gibt_es_gar_keine_rechnung():
    """Ein von Hand eingelegter Posten hat keine benötigte Menge."""
    r = mengen.rechne(None, None, "500 g")
    assert r.bedarf is None and r.packungen is None and r.grund is None


def test_rundungsfehler_erzeugen_keine_zweite_flasche():
    """3 × 0,1 l sind in Fliesskomma 0,30000000000000004 — und trotzdem 1×0,3 l."""
    summe = None, None
    for _ in range(3):
        summe = mengen.summiere(summe[0], summe[1], 0.1, "l")
    assert mengen.rechne(summe[0], summe[1], "0,3 l").packungen == 1


# --------------------------------------------------------------------------
# Zusammenzählen

def test_zwei_mengen_derselben_einheit_addieren_sich():
    assert mengen.summiere(40, "g", 40, "g") == (80.0, "g")


def test_unterschiedliche_schreibweisen_derselben_groesse_addieren_sich():
    assert mengen.summiere(500, "g", 1, "kg") == (1500.0, "g")


def test_der_erste_bedarf_ist_auch_eine_summe():
    assert mengen.summiere(None, None, 40, "g") == (40.0, "g")


def test_was_nicht_zusammenpasst_wird_nicht_addiert():
    """4 Stangen und 200 g sind keine Summe — und werden auch keine.

    `None` heisst „lässt sich nicht"; der Aufrufer entscheidet dann, was er
    behält. Stillschweigend 204 daraus zu machen wäre die schlimmste Antwort.
    """
    assert mengen.summiere(4, "Stange/n", 200, "g") is None


# --------------------------------------------------------------------------
# Der Satz, den die Nutzerin liest (Regel 6)

def test_der_satz_nennt_menge_packungszahl_und_gebinde():
    r = mengen.rechne(1000, "ml", "500 g")
    satz = mengen.satz(r, produkt="Pomito", unit_text="500 g", qty=2)
    assert "1000 ml gebraucht" in satz
    assert "2 × Pomito 500 g" in satz
    assert mengen.DICHTE_ANNAHME in satz


def test_der_satz_sagt_auch_warum_nichts_gerechnet_wurde():
    r = mengen.rechne(2, None, "1 kg")
    satz = mengen.satz(r, produkt="Zwiebeln", unit_text="1 kg", qty=1)
    assert "2 Stk gebraucht" in satz
    assert "bleibt, wie sie ist" in satz


def test_der_satz_verschweigt_nicht_was_von_hand_dazukam():
    r = mengen.rechne(80, "g", "100 g")
    assert "Im Korb liegen 3" in mengen.satz(r, unit_text="100 g", qty=3)
    assert "heruntergesetzt" in mengen.satz(r, unit_text="100 g", qty=0)
