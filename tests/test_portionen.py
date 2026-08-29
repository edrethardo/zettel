"""Portionen wählen und die Einkaufsmenge daraus ableiten (WB-362).

**Alles hier läuft über den Produktivweg** — `recipes.in_den_korb()` und
`orders.einlegen()` —, und geprüft wird, was danach in `order_item` steht.
Das ist Absicht und der Kern des Tickets: die Reihenfolge der Rechenschritte
(skalieren, zusammenzählen, DANN aufrunden) ist keine Eigenschaft einer
Hilfsfunktion, sondern eine des Weges. Ein Test, der `mengen.rechne()` direkt
aufruft, kann grün bleiben, während der Shop je Rezept aufrundet — deshalb
liegt die Arithmetik in `test_mengen.py` und der Beweis hier.

Die Produkte sind eigene Zeilen und nicht die aus dem aufgezeichneten
Katalog: es geht um Packungsgrössen (100 g, 1 kg, 500 g), und die müssen im
Test dastehen, damit die Rechnung nachlesbar ist.
"""
import pytest

from picknick import db, orders, recipes


def _produkt(con, name, unit_text):
    """Ein Produkt mit einer bestimmten Packungsgrösse.

    `external_id` aus Name UND Gebinde: der Katalog darf denselben Namen in
    zwei Gebinden führen („Knoblauch 100 g" und „Knoblauch 500 g"), und genau
    dieser Fall wird weiter unten gebraucht.
    """
    cur = con.execute(
        "INSERT INTO product (source, external_id, name, unit_text)"
        " VALUES ('test', ?, ?, ?)", (f"{name}|{unit_text}", name, unit_text))
    con.commit()
    return int(cur.lastrowid)


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def knoblauch(con):
    """Eine 100-g-Packung — der Fall aus dem Nachtrag des Tickets."""
    return _produkt(con, "Knoblauch geschält", "100 g")


@pytest.fixture
def zwiebelnetz(con):
    """Ein Kilonetz. Deckt jede Portionszahl ab, die in einem Topf landet."""
    return _produkt(con, "Zwiebeln im Netz", "1 kg")


@pytest.fixture
def pomito(con):
    return _produkt(con, "Pomito passierte Tomaten", "500 g")


def _korb(con):
    return orders.inhalt(con)


def _zeile(con, name):
    return next(p for p in _korb(con) if p["name"] == name)


# --------------------------------------------------------------------------
# Skalieren: die Zutatenmenge wächst mit den Portionen

def test_ein_rezept_fuer_vier_auf_acht_verdoppelt_die_zutatenmenge(con, pomito):
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    recipes.in_den_korb(con, r, portionen=8)
    zeile = _zeile(con, "Pomito passierte Tomaten")
    assert zeile["need_amount"] == 1000
    assert zeile["need_unit"] == "ml"


def test_und_daraus_werden_zwei_packungen(con, pomito):
    """500 ml -> 1 Packung, 1000 ml -> 2. Die Rechnung aus dem Ticket."""
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    recipes.in_den_korb(con, r, portionen=4)
    assert _zeile(con, "Pomito passierte Tomaten")["qty"] == 1

    con.execute("DELETE FROM order_item")
    con.commit()
    recipes.in_den_korb(con, r, portionen=8)
    assert _zeile(con, "Pomito passierte Tomaten")["qty"] == 2


def test_der_zwiebeltest(con, zwiebelnetz):
    """**Eine Zwiebel aus einem Kilonetz bleibt ein Netz — auch für acht.**

    Der Kerntest des Tickets, und er prüft ausdrücklich BEIDES:

    * dass skaliert wird — aus 1 Zwiebel für 4 werden 2 für 8. Ohne diese
      Zusicherung wäre der Test auch dann grün, wenn gar nichts gerechnet
      würde, und bewiese nichts.
    * dass die Packungszahl trotzdem bei 1 bleibt.

    Sie bleibt bei 1, weil „2 Stück" gegen „1 kg" nicht ausrechenbar ist und
    dann die Menge steht, wie sie ist (Regel 4) — nicht, weil hier irgendwo
    ein Sonderfall für Zwiebeln stünde. Der Bericht sagt genau das.
    """
    r = recipes.anlegen(con, "Zwiebelsuppe", servings=4, zutaten=[
        {"product_id": zwiebelnetz, "amount": 1, "unit": "große"}])
    bericht = recipes.in_den_korb(con, r, portionen=8)

    zeile = _zeile(con, "Zwiebeln im Netz")
    assert (zeile["need_amount"], zeile["need_unit"]) == (2.0, "Stk")
    assert zeile["qty"] == 1
    assert "Nicht ausrechenbar" in bericht["meldung"]
    assert "2 Stk gebraucht" in zeile["bedarf_satz"]


def test_die_portionszahl_am_rezept_ist_die_vorgabe(con, pomito):
    """Ohne Angabe gilt, was am Rezept steht."""
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    bericht = recipes.in_den_korb(con, r)
    assert bericht["portionen"] == 4
    assert _zeile(con, "Pomito passierte Tomaten")["need_amount"] == 500


def test_das_ueberschreiben_aendert_das_rezept_nicht(con, pomito):
    """„Diesmal für acht" ist eine Aussage über den Einkauf, nicht über das
    Rezept — sonst kochte man beim nächsten Mal ungefragt für acht."""
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    recipes.in_den_korb(con, r, portionen=8)
    assert recipes.rezept(con, r)["servings"] == 4
    assert recipes.rezept(con, r)["zutaten"][0]["amount"] == 500


def test_ein_rezept_ohne_portionszahl_skaliert_nicht(con, pomito):
    """Ein selbst angelegtes Rezept hat keine `servings`. Das ist kein Fehler.

    Ohne Bezugsgrösse gibt es keinen Faktor — die Menge bleibt, wie sie
    eingetragen wurde. Sie stattdessen als „für 1 Portion" zu lesen und mal
    acht zu nehmen, wäre geraten.
    """
    r = recipes.anlegen(con, "Handrezept", zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    recipes.in_den_korb(con, r, portionen=8)
    assert _zeile(con, "Pomito passierte Tomaten")["need_amount"] == 500


# --------------------------------------------------------------------------
# Erst zusammenzählen, dann aufrunden (der Nachtrag)

def test_der_knoblauchtest_zwei_mal_vierzig_gramm_sind_eine_packung(
        con, knoblauch):
    """**Der Kerntest des Nachtrags.**

    Zwei Rezepte mit je 40 g, Packung 100 g. Wer je Rezept aufrundet, kommt
    auf zwei Packungen — und kauft 100 g Knoblauch zu viel. Richtig ist:
    zusammenzählen (80 g), dann aufrunden (1).
    """
    for name in ("Aioli", "Pesto"):
        r = recipes.anlegen(con, name, servings=4, zutaten=[
            {"product_id": knoblauch, "amount": 40, "unit": "g"}])
        recipes.in_den_korb(con, r)

    zeile = _zeile(con, "Knoblauch geschält")
    assert zeile["need_amount"] == 80
    assert zeile["qty"] == 1
    assert len(_korb(con)) == 1


def test_zwei_mal_achtzig_gramm_sind_zwei_packungen(con, knoblauch):
    """Die Gegenprobe: 160 g passen nicht in eine 100-g-Packung.

    Ohne sie wäre der Test oben auch dann grün, wenn die Packungszahl schlicht
    nie über 1 stiege.
    """
    for name in ("Aioli", "Pesto"):
        r = recipes.anlegen(con, name, servings=4, zutaten=[
            {"product_id": knoblauch, "amount": 80, "unit": "g"}])
        recipes.in_den_korb(con, r)

    zeile = _zeile(con, "Knoblauch geschält")
    assert zeile["need_amount"] == 160
    assert zeile["qty"] == 2


def test_die_portionen_wirken_vor_dem_zusammenzaehlen(con, knoblauch):
    """40 g für vier, aber für acht gekocht — zusammen mit 40 g aus einem
    zweiten Rezept sind das 120 g und damit zwei Packungen."""
    a = recipes.anlegen(con, "Aioli", servings=4, zutaten=[
        {"product_id": knoblauch, "amount": 40, "unit": "g"}])
    b = recipes.anlegen(con, "Pesto", servings=4, zutaten=[
        {"product_id": knoblauch, "amount": 40, "unit": "g"}])
    recipes.in_den_korb(con, a, portionen=8)
    recipes.in_den_korb(con, b, portionen=4)

    zeile = _zeile(con, "Knoblauch geschält")
    assert zeile["need_amount"] == 120
    assert zeile["qty"] == 2


def test_zusammengezaehlt_wird_ueber_die_produkt_id(con, knoblauch):
    """Dieselbe Produkt-ID, zwei Rezepte, die sie verschieden gefunden haben.

    Der Fall aus dem Nachtrag: „Knoblauch" im einen Rezept,
    „Knoblauchzehen" im anderen — beide landen auf derselben `product_id`.
    Zusammengezählt wird über sie, also liegt eine Zeile im Korb.
    """
    for name in ("Aioli mit Knoblauch", "Pesto mit Knoblauchzehen"):
        r = recipes.anlegen(con, name, servings=4, zutaten=[
            {"product_id": knoblauch, "amount": 40, "unit": "g"}])
        recipes.in_den_korb(con, r)

    assert len(_korb(con)) == 1
    assert _zeile(con, "Knoblauch geschält")["qty"] == 1


def test_und_ausdruecklich_nicht_ueber_den_namen(con):
    """Zwei Produkte, derselbe Name, verschiedene Packungen — zwei Zeilen.

    Die Gegenprobe zum Test darüber, und sie ist die schärfere: ginge die
    Zusammenfassung über den Namen, würden hier 200 g und 200 g zu 400 g
    verrechnet — über zwei Produkte hinweg, deren Packungen 100 g und 500 g
    gross sind. Die Summe wäre gegen die falsche Packung gerechnet, und im
    Laden läge das falsche Glas im Wagen.
    """
    klein = _produkt(con, "Knoblauch", "100 g")
    gross = _produkt(con, "Knoblauch", "500 g")
    for pid in (klein, gross):
        r = recipes.anlegen(con, f"Rezept {pid}", servings=4, zutaten=[
            {"product_id": pid, "amount": 200, "unit": "g"}])
        recipes.in_den_korb(con, r)

    zeilen = _korb(con)
    assert len(zeilen) == 2
    assert [(z["product_id"], z["need_amount"], z["qty"]) for z in zeilen] == [
        (klein, 200.0, 2), (gross, 200.0, 1)]


def test_ein_von_hand_eingelegter_posten_zaehlt_mit(con, knoblauch):
    """Wer schon eine Packung im Korb hat, bekommt keine zweite.

    Der Handposten hat KEINE benötigte Menge — er sagt „eine Packung". Genau
    deshalb steht er als Untergrenze da und nicht als 0 Gramm: verrechnet
    würde er sonst weg.
    """
    orders.einlegen(con, product_id=knoblauch, qty=1)
    r = recipes.anlegen(con, "Aioli", servings=4, zutaten=[
        {"product_id": knoblauch, "amount": 40, "unit": "g"}])
    recipes.in_den_korb(con, r)

    zeile = _zeile(con, "Knoblauch geschält")
    assert zeile["qty"] == 1
    assert zeile["hand_qty"] == 1
    assert zeile["need_amount"] == 40


def test_ein_handposten_verschwindet_nicht_hinter_einer_kleinen_menge(
        con, knoblauch):
    """Zwei Packungen von Hand plus 40 g aus einem Rezept bleiben zwei.

    Die Rechnung sagt „eine Packung reicht". Sie darf trotzdem nicht die von
    Hand verlangten zwei überschreiben: jemand hat sie ausdrücklich gewollt,
    und ein Posten, der beim Einlegen eines Rezepts schrumpft, ist ein
    verlorener Posten.
    """
    orders.einlegen(con, product_id=knoblauch, qty=2)
    r = recipes.anlegen(con, "Aioli", servings=4, zutaten=[
        {"product_id": knoblauch, "amount": 40, "unit": "g"}])
    recipes.in_den_korb(con, r)
    assert _zeile(con, "Knoblauch geschält")["qty"] == 2


def test_freitextposten_werden_nicht_zusammengezaehlt(con):
    """Freitext hat kein Produkt und damit keine Packungsgrösse.

    Er bleibt eine Zeile mit einer Stückzahl — und das ist richtig so: zwei
    Freitexte lassen sich nicht gegeneinander rechnen, und einer gegen ein
    Produkt schon gar nicht.
    """
    orders.einlegen(con, free_text="Hefe vom Bäcker", qty=1)
    orders.einlegen(con, free_text="Hefe vom Bäcker", qty=1)
    orders.einlegen(con, free_text="frische Minze", qty=1)

    zeilen = _korb(con)
    assert len(zeilen) == 2
    hefe = _zeile(con, "Hefe vom Bäcker")
    assert hefe["qty"] == 2
    assert hefe["need_amount"] is None
    assert hefe["bedarf_satz"] is None


def test_ein_griff_ins_regal_rechnet_nichts_aus(con, knoblauch):
    """Das „+" an der Kachel heisst „eine Packung" und nicht „100 Gramm"."""
    orders.einlegen(con, product_id=knoblauch, qty=1)
    orders.einlegen(con, product_id=knoblauch, qty=1)
    zeile = _zeile(con, "Knoblauch geschält")
    assert zeile["qty"] == 2
    assert zeile["need_amount"] is None
    assert zeile["bedarf_satz"] is None


# --------------------------------------------------------------------------
# Ändern und herausnehmen

def test_die_menge_von_hand_zu_setzen_schlaegt_die_rechnung(con, pomito):
    """Der Minus-Knopf muss wirken, auch gegen eine Rechnung.

    Sonst tippt jemand auf Minus, es passiert nichts, und er hält den Shop
    für kaputt. Die benötigte Menge bleibt trotzdem stehen — sie ist eine
    Tatsache über das Rezept —, und die Zeile sagt beides.
    """
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 1000, "unit": "ml"}])
    recipes.in_den_korb(con, r)
    zeile = _zeile(con, "Pomito passierte Tomaten")
    assert zeile["qty"] == 2

    orders.menge_setzen(con, zeile["id"], 1)
    danach = _zeile(con, "Pomito passierte Tomaten")
    assert danach["qty"] == 1
    assert danach["need_amount"] == 1000
    assert "heruntergesetzt" in danach["bedarf_satz"]


def test_eine_von_hand_gesetzte_menge_bleibt_die_untergrenze(con, knoblauch):
    """Wer 3 tippt, bekommt später nicht weniger — aber mehr, wenn nötig."""
    item = orders.einlegen(con, product_id=knoblauch, qty=1)
    orders.menge_setzen(con, item, 3)
    r = recipes.anlegen(con, "Aioli", servings=4, zutaten=[
        {"product_id": knoblauch, "amount": 40, "unit": "g"}])
    recipes.in_den_korb(con, r)
    assert _zeile(con, "Knoblauch geschält")["qty"] == 3

    gross = recipes.anlegen(con, "Grossaioli", servings=4, zutaten=[
        {"product_id": knoblauch, "amount": 400, "unit": "g"}])
    recipes.in_den_korb(con, gross)
    assert _zeile(con, "Knoblauch geschält")["qty"] == 5


def test_der_posten_verschwindet_ganz_und_nicht_halb(con, pomito):
    """Herausnehmen nimmt die Menge mit — sonst bliebe ein Bedarf ohne Zeile."""
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    recipes.in_den_korb(con, r)
    orders.entfernen(con, _zeile(con, "Pomito passierte Tomaten")["id"])
    assert _korb(con) == []

    # Und danach beginnt die Summe wieder bei null statt beim alten Bedarf.
    recipes.in_den_korb(con, r)
    assert _zeile(con, "Pomito passierte Tomaten")["need_amount"] == 500


# --------------------------------------------------------------------------
# Was der Bericht sagt (Regel 6)

def test_der_bericht_nennt_portionen_menge_und_packungszahl(con, pomito):
    """„für 8 statt 4 Portionen: 1000 ml, das sind 2 × Pomito 500 g."

    Der Satz aus dem Ticket, wörtlich geprüft — eine stumme 2 im Mengenfeld
    ist das, was dieses Ticket verhindern soll.
    """
    r = recipes.anlegen(con, "Sugo", servings=4, zutaten=[
        {"product_id": pomito, "amount": 500, "unit": "ml"}])
    meldung = recipes.in_den_korb(con, r, portionen=8)["meldung"]
    assert "für 8 statt 4 Portionen" in meldung
    assert "1000 ml" in meldung
    assert "2 × 500 g" in meldung


def test_was_nicht_ausrechenbar_war_steht_mit_grund_im_bericht(
        con, zwiebelnetz):
    r = recipes.anlegen(con, "Suppe", servings=4, zutaten=[
        {"product_id": zwiebelnetz, "amount": 2, "unit": "Stange/n"}])
    meldung = recipes.in_den_korb(con, r)["meldung"]
    assert "Nicht ausrechenbar" in meldung
    assert "1 kg" in meldung


# --------------------------------------------------------------------------
# Die Zutat und ihre Menge am Rezept

def test_eine_zutat_traegt_menge_und_einheit(con, pomito):
    r = recipes.anlegen(con, "Sugo", servings=4)
    recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount="0,5",
                              unit="l")
    z = recipes.zutaten(con, r)[0]
    assert (z["amount"], z["unit"]) == (500.0, "ml")


def test_dieselbe_zutat_zweimal_addiert_ihre_mengen(con, pomito):
    r = recipes.anlegen(con, "Sugo", servings=4)
    recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount=200, unit="g")
    recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount=300, unit="g")
    z = recipes.zutaten(con, r)[0]
    assert (z["amount"], z["qty"]) == (500.0, 2)


def test_eine_menge_laesst_sich_wieder_loeschen(con, pomito):
    """Rücknehmbar, sonst traut sich niemand, eine Menge einzutragen."""
    r = recipes.anlegen(con, "Sugo", servings=4)
    item = recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount=200,
                                     unit="g")
    recipes.zutat_menge_setzen(con, item, amount="")
    assert recipes.zutaten(con, r)[0]["amount"] is None


def test_eine_unsinnige_menge_wird_nicht_zu_null(con, pomito):
    """„null Gramm Butter" wäre eine Behauptung, die niemand aufgestellt hat
    — und sie ginge beim Zusammenzählen mit ein."""
    r = recipes.anlegen(con, "Sugo", servings=4)
    recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount="viel",
                              unit="g")
    assert recipes.zutaten(con, r)[0]["amount"] is None


def test_die_menge_zu_loeschen_loescht_die_einheit_nicht_mit(con, pomito):
    """WB-375: der Kern der stillen Verfälschung.

    Vorher liefen Menge und Einheit gemeinsam durch `in_grundeinheit()`, das
    bei leerer Menge `None` liefert — die Einheit fiel mit. Weil sie nur
    versteckt im Formular stand, kam sie nie zurück, und aus „500 g" wurde
    beim Neutippen „500 Stk".
    """
    r = recipes.anlegen(con, "Sugo", servings=4)
    item = recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount=500,
                                     unit="g")
    recipes.zutat_menge_setzen(con, item, amount="")
    z = recipes.zutaten(con, r)[0]
    assert (z["amount"], z["unit"]) == (None, "g")

    recipes.zutat_menge_setzen(con, item, amount="500", unit="g")
    assert recipes.zutaten(con, r)[0]["unit"] == "g", "nicht mehr Gramm"


def test_eine_leere_einheit_nimmt_sie_ausdruecklich_weg(con, pomito):
    """Fehlend heisst „lass stehen", leer heisst „weg". Ohne den Unterschied
    liesse sich eine falsch geratene Einheit nicht mehr loswerden."""
    r = recipes.anlegen(con, "Sugo", servings=4)
    item = recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount=2,
                                     unit="Bund")
    recipes.zutat_menge_setzen(con, item, amount="2", unit="")
    assert recipes.zutaten(con, r)[0]["unit"] is None


def test_die_einheit_wird_beim_setzen_umgerechnet(con, pomito):
    r = recipes.anlegen(con, "Sugo", servings=4)
    item = recipes.zutat_hinzufuegen(con, r, product_id=pomito, amount=500,
                                     unit="g")
    recipes.zutat_menge_setzen(con, item, amount="1,5", unit="kg")
    z = recipes.zutaten(con, r)[0]
    assert (z["amount"], z["unit"]) == (1500.0, "g")


def test_das_einheitenfeld_behauptet_nichts_wo_nichts_steht(con, pomito):
    """`einheit_text` schreibt „Stk", auch wo NULL steht — als Vorbelegung
    eines Feldes wäre das eine Behauptung, die sich selbst wahr macht."""
    r = recipes.anlegen(con, "Sugo", servings=4)
    recipes.zutat_hinzufuegen(con, r, product_id=pomito)
    z = recipes.zutaten(con, r)[0]
    assert z["einheit_text"] == "Stk"
    assert z["einheit_feld"] == ""


# --------------------------------------------------------------------------
# Die Rezeptliste zählt beides (WB-375)

def _rezeptzutaten(con, rid, namen):
    for pos, name in enumerate(namen):
        con.execute("INSERT INTO recipe_ingredient (recipe_id, pos, raw_name,"
                    " name) VALUES (?, ?, ?, ?)", (rid, pos, name, name))
    con.commit()


def test_die_liste_zaehlt_zutaten_und_verknuepfte_getrennt(con, pomito):
    """Ein geholtes Rezept hat eine Zutatenliste und noch keine verknüpften
    Produkte — die alte Liste meldete davon „0 Zutaten"."""
    r = recipes.anlegen(con, "Pho Bo", servings=4)
    _rezeptzutaten(con, r, ["Rinderbrühe", "Reisnudeln", "Ingwer"])
    zeile = recipes.rezepte(con)[0]
    assert (zeile["n_rezeptzutaten"], zeile["n_zutaten"]) == (3, 0)

    recipes.zutat_hinzufuegen(con, r, product_id=pomito)
    zeile = recipes.rezepte(con)[0]
    assert (zeile["n_rezeptzutaten"], zeile["n_zutaten"]) == (3, 1)


def test_die_zutatenliste_multipliziert_die_verknuepften_nicht(con, pomito,
                                                               knoblauch):
    """Zwei LEFT JOINs auf dasselbe Rezept ergäben 3 × 2 = 6 verknüpfte."""
    r = recipes.anlegen(con, "Pho Bo", servings=4)
    _rezeptzutaten(con, r, ["Rinderbrühe", "Reisnudeln", "Ingwer"])
    recipes.zutat_hinzufuegen(con, r, product_id=pomito)
    recipes.zutat_hinzufuegen(con, r, product_id=knoblauch)
    assert recipes.rezepte(con)[0]["n_zutaten"] == 2
