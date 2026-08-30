"""Welche Zutat steckt hinter einem Suchbegriff? (WB-369)

Die Zuordnung ohne Modell und ohne Datenbank — reine Funktionen, und deshalb
die Stelle, an der die Zusicherung geprüft wird: **wo die Zuordnung nicht
sicher ist, gibt es keine Menge.** Eine falsche Menge im Korb ist schlimmer
als gar keine, weil sie aussieht wie eine gerechnete.

Die Fälle unten sind nicht ausgedacht, sondern die gemessenen aus den drei
echten Chefkoch-Zügen der Datenbank (2026-08-28, Qwen3.8-27B): Pho Bo,
Käse-Lauch-Suppe, Ratatouille.
"""
from __future__ import annotations

from zettel.assistant import herkunft


def _zutat(name, amount=None, unit=None):
    """Eine Zutat, wie `chefkoch.parse_zutaten` sie liefert."""
    from zettel.gerichte import chefkoch

    kette = chefkoch.zutat_kette(name)
    return {"raw_name": name, "name": kette[0] if kette else name,
            "amount": amount, "unit": unit}


def _begriffe(*ketten):
    return [{"suchbegriffe": list(k) if isinstance(k, tuple) else [k],
             "menge": 1} for k in ketten]


def _zuordnen(zutaten, *ketten):
    return herkunft.zuordnen(zutaten, _begriffe(*ketten))


# --------------------------------------------------------------------------
# Was zusammengehört, findet zusammen

def test_die_menge_der_zutat_haengt_am_begriff():
    zeilen = _zuordnen([_zutat("Hackfleisch, gemischtes", 500.0, "g")],
                       ("gemischtes Hackfleisch", "Hackfleisch"))
    assert (zeilen[0]["bedarf"], zeilen[0]["einheit"]) == (500.0, "g")
    assert zeilen[0]["zutat"] == "Hackfleisch, gemischtes"


def test_das_grundwort_eines_kompositums_trifft():
    """„Knoblauchzehe(n)" -> „Knoblauch": der Fall aus dem Ratatouille-Zug."""
    zeilen = _zuordnen([_zutat("Knoblauchzehe(n)", 4.0, None)], "Knoblauch")
    assert zeilen[0]["bedarf"] == 4.0
    assert zeilen[0]["einheit"] == "Stk", "ohne Einheit zählt Chefkoch Stück"


def test_die_mehrzahl_des_modells_trifft_die_einzahl_des_rezepts():
    zeilen = _zuordnen([_zutat("Nelke(n)", 15.0, None)], "Nelken")
    assert zeilen[0]["bedarf"] == 15.0


def test_kilogramm_werden_in_die_grundeinheit_gerechnet():
    zeilen = _zuordnen([_zutat("Markknochen", 1.0, "kg")], "Markknochen")
    assert (zeilen[0]["bedarf"], zeilen[0]["einheit"]) == (1000.0, "g")


def test_dieselbe_zutat_zweimal_wird_zusammengezaehlt():
    """Pho nennt die Zwiebel zweimal — das Modell macht eine Zeile daraus.

    Ohne das Zusammenzählen hinge an dieser Zeile die Menge der ERSTEN
    Nennung, und die halbe Zwiebelmenge des Rezepts wäre still verschwunden.
    """
    zeilen = _zuordnen([_zutat("Zwiebel(n)", 4.0, "große"),
                        _zutat("Zwiebel(n)", 4.0, "große")], "Zwiebel")
    assert (zeilen[0]["bedarf"], zeilen[0]["einheit"]) == (8.0, "Stk")


def test_die_gedrehte_kommaform_trifft_auch():
    """Chefkoch schreibt „Tomaten, passierte", das Modell „passierte Tomaten"."""
    zeilen = _zuordnen([_zutat("Tomaten, passierte", 500.0, "ml")],
                       ("passierte Tomaten", "Tomaten"))
    assert zeilen[0]["bedarf"] == 500.0


# --------------------------------------------------------------------------
# Was nicht zusammengehört, bleibt getrennt

def test_die_fruehlingszwiebel_ist_keine_zwiebel():
    """Nur der WORTANFANG zählt, nicht das Wortende.

    Sonst bekäme „Zwiebel" die Menge der Frühlingszwiebeln — ein Fehler, den
    hinterher niemand mehr sieht.
    """
    zeilen = _zuordnen([_zutat("Frühlingszwiebel(n)", 1.0, "Bund")], "Zwiebel")
    assert zeilen[0]["bedarf"] is None
    assert zeilen[0]["grund"] == "keine Zutat des Rezepts zuzuordnen"


def test_jedes_wort_des_begriffs_muss_vorkommen():
    """„gelbe Paprika" holt nicht die rote — der gemessene Ratatouille-Fall."""
    zutaten = [_zutat("Paprikaschote(n), rote", 2.0, "große"),
               _zutat("Paprikaschote(n), gelbe", 3.0, "große")]
    zeilen = herkunft.zuordnen(zutaten, _begriffe("Paprika", "gelbe Paprika"))
    assert zeilen[0]["bedarf"] == 2.0, '„Paprika“ bekommt die rote'
    assert zeilen[1]["bedarf"] == 3.0, '„gelbe Paprika“ die gelbe'


def test_ein_gleichstand_ergibt_keine_menge():
    """Zwei Begriffe passen gleich gut — dann lieber keine Menge als die falsche.

    Geraten würde hier eine Menge an eine von zwei Zeilen gehängt, und sie
    sähe dort aus wie eine gerechnete.
    """
    zeilen = _zuordnen([_zutat("Käse", 200.0, "g")], "Käse", "Käse")
    assert [z["bedarf"] for z in zeilen] == [None, None]


def test_ein_synonym_des_modells_bekommt_keine_menge():
    """„Möhren" -> „Karotten": ehrlich verloren, nicht falsch zugeordnet.

    Das ist der Preis der Regel, und er ist der richtige: die Zeile
    verhält sich dann wie vor WB-369 und trägt die geratene Packungszahl.
    """
    zeilen = _zuordnen([_zutat("Möhren", 3.0, None)], "Karotten")
    assert zeilen[0]["bedarf"] is None
    assert zeilen[0]["menge"] == 1, "die Zahl des Modells bleibt unangetastet"


def test_ein_begriff_ohne_rezept_hat_keine_menge():
    """Der Rest des Satzes („und Klopapier") stammt aus keiner Zutat."""
    zeilen = _zuordnen([_zutat("Hackfleisch", 500.0, "g")],
                       "Hackfleisch", "Klopapier")
    assert zeilen[1]["bedarf"] is None
    assert zeilen[1]["zutat"] is None


# --------------------------------------------------------------------------
# Mengen, die keine sind

def test_chefkochs_null_ist_keine_menge():
    """`amount: 0.0` heisst „nach Belieben" und nicht „null Gramm".

    Durchgereicht ergäbe es einen Bedarf von null — und `mengen.rechne`
    machte daraus aufgerundet eine Packung, gekauft, weil nichts gebraucht
    wird.
    """
    zeilen = _zuordnen([_zutat("Fischsauce", 0.0, None)], "Fischsauce")
    assert zeilen[0]["bedarf"] is None
    assert zeilen[0]["zutat"] == "Fischsauce", "zugeordnet ist sie trotzdem"
    assert zeilen[0]["grund"] == "das Rezept nennt dazu keine Menge"


def test_zwei_unvereinbare_mengen_derselben_zutat_ergeben_keine():
    """„1 Stange Lauch" und „200 g Lauch" sind keine Summe."""
    zeilen = _zuordnen([_zutat("Lauch", 1.0, "Stange/n"),
                        _zutat("Lauch", 200.0, "g")], "Lauch")
    assert zeilen[0]["bedarf"] is None
    assert "zusammenzählen" in zeilen[0]["grund"]


def test_ohne_begriffe_gibt_es_nichts_zuzuordnen():
    assert herkunft.zuordnen([_zutat("Salz", 1.0, "TL")], []) == []


# --------------------------------------------------------------------------
# Klammern und Akzente (WB-371)
#
# Chefkoch schreibt die Mehrzahl in Klammern — „Ei(er)", „Limette(n)",
# „Zwiebel(n)". Bis WB-371 stand das als EIN Wort „ei(er)" im Vergleich und
# traf den Begriff „Eier" nicht: die Zeile verlor ihre Menge, fiel aus dem
# Zusammenzählen und fehlte im Rezeptentwurf. Gemessen an 35 Chefkoch-Zügen
# gingen 6 der 13 Begriffe ohne Herkunftszutat allein auf „Eier" zurück.

def test_die_klammermehrzahl_trifft_die_mehrzahl_des_modells():
    """Der Fall, der das Ticket ausgelöst hat: „Eier" gegen „Ei(er)"."""
    zeilen = _zuordnen([_zutat("Ei(er)", 4.0, None)], "Eier")
    assert zeilen[0]["bedarf"] == 4.0
    assert zeilen[0]["einheit"] == "Stk"
    assert zeilen[0]["zutat"] == "Ei(er)"


def test_zwei_klammerzeilen_derselben_zutat_zaehlen_zusammen():
    """Quiche Lorraine nennt 1 Ei und 3 Eier — auf dem Zettel stehen 4.

    Der gemessene Fall vom 2026-08-29. Ohne die Klammerfaltung bekam die
    Zeile gar keine Menge und der Korb eine geratene Packungszahl.
    """
    zeilen = _zuordnen([_zutat("Ei(er)", 1.0, None),
                        _zutat("Ei(er)", 3.0, None)], "Eier")
    assert (zeilen[0]["bedarf"], zeilen[0]["einheit"]) == (4.0, "Stk")


def test_die_klammermehrzahl_trifft_auch_bei_limetten():
    """Hier hing die Zuordnung schon vorher am zweiten Namen der Zutat.

    `chefkoch.zutat_kette` legt „Limette" ohne Klammerteil daneben, und
    „Limetten" traf das über den Wortanfang. Die Rohform traf es nicht — und
    ein Begriff, der nur über den Umweg ankommt, ist eine Zuordnung weniger,
    sobald ein anderer Name danebensteht.
    """
    assert herkunft.punkte("Limetten", "Limette(n)") == 1.0
    zeilen = _zuordnen([_zutat("Limette(n)", 2.0, None)], "Limetten")
    assert zeilen[0]["bedarf"] == 2.0


def test_der_akzent_des_rezepts_faellt_wie_der_umlaut():
    """„Porrée" gegen „Porree" — Borschtsch schreibt es mit Akzent."""
    zeilen = _zuordnen([_zutat("Porrée", 200.0, "g")], "Porree")
    assert zeilen[0]["bedarf"] == 200.0
    assert herkunft.punkte("Porree", "Porrée") == 1.0


def test_das_komma_klebt_nicht_mehr_am_wort():
    """„Hackfleisch, gemischt" war ein Wort „hackfleisch," und traf nur zu 0.6."""
    assert herkunft.punkte("Hackfleisch", "Hackfleisch, gemischt") == 1.0


# --------------------------------------------------------------------------
# ... und was dabei NICHT passieren darf

def test_eier_holen_nicht_das_eigelb():
    """Der Kaiserschmarrn nennt Eigelb und Eiweiß, aber keine Eier.

    Mehr Treffer heisst auch mehr Gelegenheit, das Falsche zu treffen — und
    die halbe Menge Eigelb an der Zeile „Eier" sähe aus wie gerechnet.
    """
    zeilen = _zuordnen([_zutat("Eigelb", 4.0, None),
                        _zutat("Eiweiß", 4.0, None)], "Eier")
    assert zeilen[0]["bedarf"] is None
    assert zeilen[0]["zutat"] is None


def test_klopapier_findet_auch_neben_klammerzutaten_nichts():
    zeilen = _zuordnen([_zutat("Ei(er)", 4.0, None),
                        _zutat("Zwiebel(n)", 2.0, None)],
                       "Eier", "Klopapier")
    assert zeilen[0]["bedarf"] == 4.0
    assert zeilen[1]["bedarf"] is None
    assert zeilen[1]["zutat"] is None


def test_die_klammer_macht_aus_der_fruehlingszwiebel_keine_zwiebel():
    """Die Zusicherung aus WB-369 hält auch nach der Faltung.

    „Frühlingszwiebel(n)" wird zu „fruehlingszwiebeln" — der Wortanfang-
    Vergleich greift weiterhin nur von vorn.
    """
    zeilen = _zuordnen([_zutat("Frühlingszwiebel(n)", 1.0, "Bund")], "Zwiebeln")
    assert zeilen[0]["bedarf"] is None
