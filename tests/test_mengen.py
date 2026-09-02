"""Zutatenmenge, Packungsgrösse, Einkaufsmenge — die Rechnung allein (WB-362).

Hier steht die Arithmetik ohne Datenbank: Einheiten lesen, skalieren,
zusammenzählen, aufrunden. **Der Beweis, dass der Shop das auch wirklich so
tut, steht nicht hier**, sondern in `test_portionen.py` — dort läuft alles
über `recipes.in_den_korb` und `orders.einlegen`, also über den Weg, den ein
Tipp auf „Alles in den Warenkorb" nimmt. Ein grüner Test über eine Funktion,
die im Produktivweg nicht aufgerufen wird, beweist nichts.
"""
import pytest

from zettel import mengen


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


def test_ein_freitext_hat_keine_packung_und_das_ist_kein_mangel():
    """`freitext=True` ändert nichts an der Rechnung, aber alles am Grund.

    Ohne Packungsgrösse gibt es so oder so keine Packungszahl — nur heisst
    „die Packungsgrösse steht nicht lesbar am Produkt" an einer Zeile ohne
    Produkt, dass dort etwas fehlt, das nie hingehörte (WB-385). Der Grund
    wird zitiert: im Trace (`zettel.reason`) und in jedem Satz darunter.
    """
    r = mengen.rechne(3, "Stk", None, freitext=True)
    assert r.bedarf == 3 and r.packungen is None
    assert r.grund == mengen.FREITEXT_GRUND
    assert "Produkt" not in r.grund
    # Die Gegenprobe: dasselbe an einem Produkt bleibt, wie es war.
    assert "am Produkt" in mengen.rechne(3, "Stk", None).grund


def test_der_satz_am_freitext_bleibt_die_menge():
    """„3 Stk gebraucht." — und kein Wort über eine Rechnung, die nie lief.

    „Die Menge bleibt, wie sie ist" antwortet auf eine Packungsrechnung, die
    nicht aufging. Beim Freitext war keine im Gang, und der Zusatz liest sich
    dort wie ein Vorwurf an eine Zeile, die nichts falsch gemacht hat.
    """
    r = mengen.rechne(3, "Stk", None, freitext=True)
    assert mengen.satz(r, produkt="Sternanis") == "3 Stk gebraucht."
    assert mengen.gebinde_text(r, unit_text=None, qty=1) is None
    assert mengen.nachsatz(r, qty=1) is None


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


def test_der_satz_sagt_kurz_warum_nichts_gerechnet_wurde():
    """„2 Stk gebraucht — 2 Stk lässt sich nicht gegen die Packung („1 kg")
    rechnen, die Menge bleibt, wie sie ist." stand an fast jeder Korbzeile
    (UI-Review 2026-09-01, Fund 8): die Menge zweimal, die Entschuldigung
    einmal. Der Grund im Trace bleibt lang; der Satz für den Leser sagt es
    in einer Zeile."""
    r = mengen.rechne(2, None, "1 kg")
    satz = mengen.satz(r, produkt="Zwiebeln", unit_text="1 kg", qty=1)
    assert satz == "2 Stk gebraucht — nicht gegen die Packung („1 kg“) zu rechnen."
    assert "bleibt, wie sie ist" in r.grund or "rechnen" in r.grund


def test_der_satz_verschweigt_nicht_was_von_hand_dazukam():
    r = mengen.rechne(80, "g", "100 g")
    assert "Im Korb liegen 3" in mengen.satz(r, unit_text="100 g", qty=3)
    assert "heruntergesetzt" in mengen.satz(r, unit_text="100 g", qty=0)


# --------------------------------------------------------------------------
# Dieselben Angaben getrennt — die Einkaufszeile (WB-381)

def test_die_gebrauchte_menge_steht_fuer_sich_allein():
    """Sie ist die Hauptangabe der Einkaufszeile und darf nicht erst aus
    einem Satz herausgelesen werden müssen."""
    r = mengen.rechne(1000, "ml", "500 g")
    assert mengen.bedarf_text(r) == "1000 ml gebraucht"


def test_ohne_gebrauchte_menge_gibt_es_keine_hauptangabe():
    """`None` heisst „hier wurde nie eine Menge ausgerechnet" — und nichts
    anderes rückt an ihre Stelle."""
    assert mengen.bedarf_text(mengen.rechne(None, None, "1 kg")) is None


def test_die_packungszahl_multipliziert_die_packungsgroesse():
    """„2 × 500 g" kann nicht als „zwei Kilo" gelesen werden, „2× Pomito"
    daneben schon."""
    r = mengen.rechne(1000, "ml", "500 g")
    assert mengen.gebinde_text(r, unit_text="500 g", qty=2) == "dafür 2 × 500 g"


def test_dafuer_steht_nur_da_wo_es_wirklich_gerechnet_wurde():
    """Liegt etwas anderes im Korb, als die Rechnung verlangt, wäre „dafür"
    eine Behauptung über eine Zahl, die von Hand kommt."""
    r = mengen.rechne(1000, "ml", "500 g")
    assert mengen.gebinde_text(r, unit_text="500 g", qty=3) == "3 × 500 g"


def test_die_packungsangabe_ohne_bedarf_bleibt_eine_packungsangabe():
    """Ein von Hand eingelegter Posten hat keine gebrauchte Menge. Die
    Packungsgrösse ist dann alles, was es gibt — und muss als das erkennbar
    sein, statt wie eine Bedarfsmenge auszusehen."""
    r = mengen.rechne(None, None, "1 kg")
    assert mengen.gebinde_text(r, unit_text="1 kg", qty=2) == "2 × 1 kg"


def test_eine_nicht_ausrechenbare_packungszahl_sagt_das_selbst():
    """„6 Stange gebraucht" gegen „1 Stk": die 1 vor der Packung stammt nicht
    aus den 6 Stangen, und ohne diesen Zusatz sähe sie wie ein Rechenfehler
    aus (Regel 5)."""
    r = mengen.rechne(6, "Stange/n", "1 Stk")
    assert mengen.gebinde_text(r, unit_text="1 Stk", qty=1) == \
        "1 × 1 Stk — nicht ausrechenbar"


def test_der_nachsatz_wiederholt_nicht_was_die_zeile_schon_zeigt():
    """Wo Bedarf und Packungsangabe getrennt dastehen, bleibt im
    Kleingedruckten nichts übrig — und eine Zeile, die nichts zu sagen hat,
    schweigt."""
    r = mengen.rechne(500, "ml", "500 ml")
    assert mengen.nachsatz(r, unit_text="500 ml", qty=1) is None


def test_der_nachsatz_behaelt_annahme_und_handmenge():
    r = mengen.rechne(1000, "ml", "500 g")
    nach = mengen.nachsatz(r, unit_text="500 g", qty=3)
    assert "Im Korb liegen 3" in nach
    assert mengen.DICHTE_ANNAHME in nach


def test_der_nachsatz_nennt_den_grund_nur_wenn_ihn_sonst_niemand_nennt():
    """Steht eine Packungsangabe an der Zeile, trägt sie das „nicht
    ausrechenbar" selbst. Fehlt sie ganz, muss der Nachsatz sagen, warum
    nichts gerechnet wurde. Der Freitext ist der dritte Fall und seit WB-385
    kein Beispiel mehr für den zweiten: er trägt „Freitext" an der Stelle der
    Packungsangabe und bekommt deshalb gar keinen Grund
    (`test_ein_freitext_hat_keine_packung_und_das_ist_kein_mangel`)."""
    mit = mengen.rechne(6, "Stange/n", "1 Stk")
    assert mengen.nachsatz(mit, unit_text="1 Stk", qty=1) is None
    ohne = mengen.rechne(200, "g", None)
    assert mengen.nachsatz(ohne, qty=1) == "Keine lesbare Packungsgrösse."


def test_der_kurzgrund_kennt_alle_faelle():
    assert mengen.kurzgrund(mengen.rechne(200, "g", None)) == \
        "keine lesbare Packungsgrösse"
    assert mengen.kurzgrund(mengen.rechne(200, "g", "Beutel"), "Beutel") == \
        "keine lesbare Packungsgrösse („Beutel“)"
    assert mengen.kurzgrund(mengen.rechne(2, None, "1 kg"), "1 kg") == \
        "nicht gegen die Packung („1 kg“) zu rechnen"
    assert mengen.kurzgrund(mengen.rechne(200, "g", "0 g"), "0 g") == \
        "Packungsgrösse null"
    assert mengen.kurzgrund(mengen.rechne(3, None, None, freitext=True)) == ""


def test_die_einheit_wird_geschrieben_wie_im_kochbuch():
    """Gefaltet ist „el"; gelesen wird „EL" (UI-Review 2026-09-01, Fund 16).
    `schreibe` gab Hauptwörtern den grossen Anfangsbuchstaben zurück —
    „Paket" — und machte aus der Abkürzung „El"."""
    assert mengen.einheit_text("el") == "EL"
    assert mengen.einheit_text("tl") == "TL"
    assert mengen.einheit_text("pck") == "Pck."
    # „Pkt." ist die Form, die wirklich aus den Rezepten kommt
    # (`chefkoch_pho_rezept.json`), „Pck." dagegen keine, die dort je stand.
    # Ohne eigenen Eintrag stünde im Feld „Pkt" ohne Punkt — die Rückfaltung
    # unten fiele darauf nicht herein, sie kürzt den Punkt ohnehin weg.
    assert mengen.einheit_text("pkt") == "Pkt."
    assert mengen.falte(mengen.einheit_text("pkt")) == "pkt"
    assert mengen.einheit_text("paket") == "Paket"
    assert mengen.einheit_text("g") == "g"
    assert mengen.einheit_text(None) == ""
    assert mengen.schreibe(2, "el") == "2 EL"
    # Die Schreibweise faltet auf sich selbst zurück — ein Feld, das „EL"
    # anzeigt und „EL" zurückschickt, speichert wieder „el".
    for gefaltet in mengen.SCHREIBWEISE:
        assert mengen.falte(mengen.SCHREIBWEISE[gefaltet]) == gefaltet
