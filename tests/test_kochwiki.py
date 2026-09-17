"""Wikitext wird ein Rezept — und zwar dasselbe, das Chefkoch liefert (WB-606).

**Kein Test geht ins Netz** (Spec 13). Gespielt wird gegen echten Wikitext
aus dem Koch-Wiki, abgerufen am 2026-09-17 und eingecheckt: sieben einzelne
Rezepte unter `tests/fixtures/kochwiki/` und die Stichprobe aus 120 Rezepten
unter `tests/fixtures/kochwiki_stichprobe.json`. Anders als bei Chefkoch darf
das hier stehen: **CC BY-SA 3.0** erlaubt die Weitergabe ausdrücklich, mit
Namensnennung und unter gleichen Bedingungen. Jede Fixture nennt im Kopf
Titel, Artikel-URL, Abrufdatum und Lizenz.

Die vier Fallen des Tickets haben je einen Test, der NAMENTLICH auf sie
zeigt. Sie sind so geschrieben, dass sie mit der naiven Fassung fehlschlagen
— ein Test, der auch ohne den Fix grün ist, prüft die Falle nicht:

    Falle 1  `^==` matcht auch `===`   -> test_ein_abschnitt_endet_nicht_…
    Falle 2  `(Minute)\\b` ohne Plural  -> test_minuten_im_plural_…
    Falle 3  `=== Variante (n) ===`    -> test_varianten_sind_alternative_…
    Falle 4  mehrteilige Zeitangabe    -> test_eine_mehrteilige_zeitangabe_…
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from zettel.gerichte import chefkoch, kochwiki

FIXTURES = Path(__file__).parent / "fixtures" / "kochwiki"
STICHPROBE = Path(__file__).parent / "fixtures" / "kochwiki_stichprobe.json"


def rezept(name: str) -> tuple[str, str]:
    """Eine Fixture -> `(titel, wikitext)`."""
    roh = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return roh["titel"], roh["wikitext"]


def geparst(name: str) -> dict:
    return kochwiki.parse_rezept(*rezept(name))


@pytest.fixture(scope="module")
def stichprobe() -> dict:
    roh = json.loads(STICHPROBE.read_text(encoding="utf-8"))
    return roh["rezepte"]


# --------------------------------------------------------------------------
# Falle 1: ein Abschnitt endet am nächsten `==`, dem kein `=` folgt

def test_ein_abschnitt_endet_nicht_an_einer_untergruppe():
    """Die teuerste Klammer des Tickets: `(?=^==[^=]|\\Z)`.

    Mit dem naiven `(?=^==|\\Z)` bricht jeder Abschnitt an seiner ersten
    Untergruppe ab. Beide Hälften stehen hier, weil beide gemessen sind:
    die Zutatenliste verliert ein Drittel ihrer Zeilen, und die Zubereitung
    verliert ALLES, wenn sie mit einer Untergruppe beginnt.
    """
    _, wikitext = rezept("butter-chicken")
    naiv = re.compile(r"^==\s*Zutaten\s*==\s*$(.*?)(?=^==|\Z)", re.M | re.S)
    naive_zeilen = [z for z in naiv.search(wikitext).group(1).splitlines()
                    if z.strip().startswith(("*", "#"))]
    assert naive_zeilen == [], "die naive Fassung findet plötzlich etwas"
    assert len(kochwiki.parse_zutaten(wikitext)) == 27

    # Und die Zubereitung des Krabbencocktails beginnt mit `==== Sauce ====`:
    # naiv gelesen hat sie null Schritte.
    _, krabben = rezept("nordseekrabbencocktail")
    naiv_zub = re.compile(r"^==\s*Zubereitung\s*==\s*$(.*?)(?=^==|\Z)",
                          re.M | re.S)
    assert not [z for z in naiv_zub.search(krabben).group(1).splitlines()
                if z.strip().startswith("*")]
    assert len(kochwiki.schritte(krabben)) == 8


# --------------------------------------------------------------------------
# Falle 2: `Minuten` ist Plural

def test_minuten_im_plural_werden_erkannt():
    """`(Minute|Std)\\b` findet „Minuten" nicht — die Wortgrenze scheitert am
    Plural-n, und die Zeiterkennung lag damit bei 15,8 % statt 96,7 %."""
    naiv = re.compile(r"(\d+)\s*(Minute|Std)\b")
    assert not naiv.search("45 Minuten")

    assert kochwiki.zeit("45 Minuten")["prep"] == 45
    assert kochwiki.zeit("40 Min.")["prep"] == 40
    assert kochwiki.zeit("1 Stunde")["prep"] == 60
    assert kochwiki.zeit("etwa 1½ Std.")["prep"] == 90


# --------------------------------------------------------------------------
# Falle 3: `Variante (n)` ist ein zweites Rezept, keine Untergruppe

def test_varianten_sind_alternative_rezepte_und_keine_gruppen():
    """Fünf Bratapfel-Varianten sind nicht eine Liste mit fünffachem Zucker.

    Genommen wird die erste jedes Laufs. „Jedes Laufs" und nicht „die erste
    überhaupt": derselbe Artikel fächert unter „Vanillesauce" noch einmal
    auf, und das ist ein anderer Teil des Gerichts.
    """
    r = geparst("bratapfel-mit-parfuemierter-vanillesauce")
    namen = [z["name"] for z in r["zutaten"]]

    assert namen.count("Boskoop-Äpfel") == 1, "eine Variante zu viel gelesen"
    # Calvados steht NUR in Variante (2), Rosinen nur in (4), Mandeln nur in
    # (5) — keine davon darf auf dem Zettel landen.
    assert not {"Calvados", "Rosinen", "Mandeln"} & set(namen)
    # Die Vanillesauce dagegen ist ein eigener Teil und bleibt — mit ihrer
    # ERSTEN Variante (Vanilleschote), nicht mit beiden.
    assert "Vanilleschote" in namen and "Vanillezucker" not in namen
    sauce = [z for z in r["zutaten"] if z["name"] == "Vanilleschote"]
    assert {z["gruppe"] for z in sauce} == {"Vanillesauce"}
    # Die erste Variante IST das Rezept und wird nicht als Gruppe ausgegeben.
    assert r["zutaten"][0]["gruppe"] is None


def test_ein_fetter_gruppenkopf_ist_keine_zutat():
    """`* '''Marinade'''` ist eine Überschrift — sonst stünde „Marinade" als
    Posten auf dem Einkaufszettel."""
    r = geparst("butter-chicken")
    assert "Marinade" not in [z["name"] for z in r["zutaten"]]
    marinade = [z["name"] for z in r["zutaten"] if z["gruppe"] == "Marinade"]
    assert "Joghurt" in marinade and "Kurkuma" in marinade


# --------------------------------------------------------------------------
# Falle 4: eine mehrteilige Zeitangabe ist keine Zahl

def test_eine_mehrteilige_zeitangabe_wird_nicht_zur_arbeitszeit():
    """„Einsalzzeit 3 Stunden; Zubereitung 30 Minuten; Einlegezeit 2 Monate".

    Die erste Zahl zu nehmen ergäbe 180 Minuten Arbeit, die Summe 86.610.
    Gearbeitet wird eine halbe Stunde; der Rest ist Warten und steht in
    `rest_minutes`, wo er die Wochenplanung nicht verfälscht.
    """
    r = geparst("amsterdamer-zwiebeln")
    assert r["prep_minutes"] == 30
    assert r["cook_minutes"] is None
    assert r["rest_minutes"] == 3 * 60 + 2 * 43200

    # Benannte Teile gehen in das Feld, das ihr Name nennt.
    assert geparst("gelber-reis")["prep_minutes"] == 15
    assert geparst("gelber-reis")["cook_minutes"] == 30

    # Ein unbenannter Teil in einer mehrteiligen Angabe wird weggelassen:
    # ob dort gearbeitet oder gewartet wird, ist nicht zu entscheiden.
    assert kochwiki.zeit("Sauce: 20 Minuten + Abkühlzeit: 2 Stunden + "
                         "Zubereitung: 5 Minuten + Backzeit: 20 Minuten") == {
        "prep": 5, "cook": 20, "rest": 120}
    # Allein stehend ist dieselbe Angabe die Arbeitszeit.
    assert kochwiki.zeit("20 Minuten")["prep"] == 20


def test_eine_zeitangabe_ohne_zahl_wirft_nicht():
    """Es gibt sie wirklich: „ca. Minuten", „bis Minuten", „keine Angaben"."""
    assert kochwiki.zeit(
        "Zubereitung: ca. Minuten + Backzeit: ca. 20 Minuten") == {
            "prep": None, "cook": 20, "rest": None}
    assert kochwiki.zeit("keine Angaben") == {"prep": None, "cook": None,
                                              "rest": None}
    assert kochwiki.zeit(None) == {"prep": None, "cook": None, "rest": None}


# --------------------------------------------------------------------------
# Was diese Quelle besser kann als Chefkoch: der kanonische Name

def test_der_kanonische_name_kommt_aus_dem_linkziel():
    """`[[Zutat:Karotte|Karotten]]` trägt beides — und beide werden gebraucht.

    Die Anzeigeform steht auf der Karte, das Linkziel ist über alle 9.210
    Rezepte derselbe Name und damit der Schlüssel für den Katalog.
    """
    r = geparst("butter-chicken")
    fleisch = r["zutaten"][0]
    assert fleisch["name"] == "Hähnchenfleisch"
    assert fleisch["kanonisch"] == "Hähnchenschenkel"
    assert fleisch["amount"] == 600.0 and fleisch["unit"] == "g"
    # Das Beiwerk steht neben dem Namen, nicht darin.
    assert "Pollo Fino" in fleisch["usage_info"]


def test_was_hinter_oder_steht_bleibt_erhalten_ohne_zweite_zeile_zu_werden():
    """12,3 % der Zeilen bieten eine Alternative an. Gekauft wird die erste."""
    r = geparst("knoblauchessig")
    essig = r["zutaten"][0]
    assert essig["name"] == "Weißweinessig"
    assert [a["kanonisch"] for a in essig["alternativen"]] == ["Rotweinessig"]
    assert len([z for z in r["zutaten"] if "essig" in z["name"].lower()]) == 1


def test_oder_zwischen_zwei_eigenschaften_trennt_keine_zutaten():
    """„frisch gemahlener, weißer oder schwarzer Pfeffer" ist EIN Pfeffer.

    Ohne diese Unterscheidung entstand eine Zutat namens „frisch gemahlener,
    weißer", und der Pfeffer rutschte in die Alternativen.
    """
    zeile = ("frisch gemahlener, weißer oder schwarzer "
             "[[Zutat:Pfeffer|Pfeffer]] aus der Mühle")
    z = kochwiki._zutat(zeile)
    assert z["name"] == "Pfeffer" and z["kanonisch"] == "Pfeffer"
    assert z["alternativen"] == []
    assert "weißer" in z["usage_info"]


def test_eine_warengruppe_ist_kein_produkt():
    """`[[:Kategorie:Reissorten|Langkornreis]]` — eine Warengruppe.

    Sie hat keinen kanonischen Namen, und sie darf auch nicht so tun: 4,1 %
    der Zeilen zeigen auf eine Kategorie statt auf eine Zutat.
    """
    reis = geparst("gelber-reis")["zutaten"][0]
    assert reis["name"] == "Langkornreis"
    assert reis["kanonisch"] is None
    assert reis["warengruppe"] == "Reissorten"


def test_brueche_und_bereiche_werden_zu_zahlen():
    """`{{B|1|2}}` ist ½, und ein Bereich wird zu seiner UNTEREN Grenze.

    Wer „2–3 Zwiebeln" braucht, kauft zwei und hat nicht zu wenig. Die
    Obergrenze geht nicht verloren — sie steht als Beiwerk daneben.
    """
    zwiebel_rezept = geparst("amsterdamer-zwiebeln")
    nach_name = {z["name"]: z for z in zwiebel_rezept["zutaten"]}
    zwiebeln = nach_name["Silberzwiebeln"]
    assert zwiebeln["amount"] == 2.0 and zwiebeln["unit"] == "kg"
    assert "2–3" in zwiebeln["usage_info"]
    assert nach_name["Salz"]["amount"] == 1.5          # 1{{B|1|2}} Tassen

    muffins = {z["name"]: z for z in geparst("kaesekuchen-muffins")["zutaten"]}
    assert muffins["Speisestärke"]["amount"] == 1.5    # 1 {{B|1|2}} EL
    assert muffins["Speisestärke"]["unit"] == "EL"

    thymian = {z["name"]: z for z in geparst("knoblauchessig")["zutaten"]}
    assert thymian["Thymian"]["amount"] == 0.5         # {{B|1|2}} Zweig
    assert thymian["Thymian"]["unit"] == "Zweig"


def test_ein_adjektiv_ist_keine_einheit():
    """„1 große Zwiebel" — „große" beschreibt die Zwiebel, sie misst sie nicht.

    In der Stichprobe steht 72-mal ein Adjektiv an der Stelle, an der eine
    Heuristik „das Wort nach der Zahl" die Einheit vermutet hätte.
    """
    z = kochwiki._zutat("1 große [[Zutat:Zwiebel|Zwiebel]]")
    assert (z["amount"], z["unit"], z["name"]) == (1.0, None, "Zwiebel")
    assert z["usage_info"] == "große"


def test_die_einheiten_falten_auf_das_was_mengen_kennt():
    """„Pr." und „Bd" wären sonst eigene Einheiten neben „Prise" und „Bund"
    und liessen sich mit ihnen nicht zusammenzählen."""
    from zettel import mengen

    assert kochwiki._einheit_von("Pr.") == "Prise"
    assert kochwiki._einheit_von("Bd") == "Bund"
    assert kochwiki._einheit_von("EL") == "EL"
    assert kochwiki._einheit_von("große") is None
    # Jede geschriebene Einheit faltet auf sich selbst zurück — sonst wäre
    # „Prise" aus diesem Rezept und „Prise" aus dem nächsten zweierlei.
    for geschrieben in set(kochwiki.EINHEITEN.values()):
        gefaltet = mengen.falte(geschrieben)
        assert kochwiki.EINHEITEN.get(gefaltet) == geschrieben, geschrieben
    # Und die fünf, die `mengen.SCHREIBWEISE` kennt, kommen genau so zurück.
    for geschrieben in ("EL", "TL", "Msp.", "Pck."):
        assert mengen.einheit_text(mengen.falte(geschrieben)) == geschrieben


# --------------------------------------------------------------------------
# Dieselbe Form wie Chefkoch — sonst ist es keine zweite Quelle,
# sondern ein zweites Format

def test_das_rezept_hat_genau_die_schluessel_von_chefkoch():
    r = geparst("gelber-reis")
    von_chefkoch = set(chefkoch.parse_rezept({}))
    assert von_chefkoch <= set(r), von_chefkoch - set(r)
    # Bewertungen gibt es im Koch-Wiki nicht. Sie werden nicht erfunden.
    assert r["rating"] is None and r["votes"] == 0
    # Und die Zutaten tragen dieselben sechs Felder wie dort.
    von_chefkoch_zutat = {"gruppe", "raw_name", "name", "amount", "unit",
                          "usage_info"}
    assert von_chefkoch_zutat <= set(r["zutaten"][0])


def test_die_lizenz_wandert_mit_jedem_rezept():
    """Share-Alike ist der Preis dieser Quelle. Ein Rezept ohne Herkunft
    verletzt die Lizenz, unter der es überhaupt hier ist."""
    r = geparst("butter-chicken")
    assert r["quelle"] == "kochwiki"
    assert r["lizenz"] == "CC BY-SA 3.0"
    assert r["lizenz_url"].startswith("https://creativecommons.org/")
    assert r["site_url"] == "https://www.kochwiki.org/wiki/Butter_Chicken"
    assert r["rezept_id"] == "Butter Chicken"


def test_jede_fixture_nennt_titel_abrufdatum_und_lizenz():
    """Namensnennung ist keine Fussnote, sondern die Bedingung der Lizenz."""
    dateien = sorted(FIXTURES.glob("*.json"))
    assert len(dateien) >= 6, "zu wenige Fixtures für die vier Fallen"
    for datei in dateien:
        kopf = json.loads(datei.read_text(encoding="utf-8"))
        assert kopf["_titel"]
        assert kopf["_url"].startswith("https://www.kochwiki.org/")
        assert kopf["_abgerufen"] == "2026-09-17"
        assert kopf["_lizenz"] == "CC BY-SA 3.0"


def test_die_adressen_zeigen_auf_die_freie_api():
    """robots.txt sperrt `Spezial:`, `Special:` und `index.php?` — sonst
    nichts (gelesen am 2026-09-17). `/w/api.php` ist frei."""
    assert "index.php" not in kochwiki.such_url("Lasagne")
    assert "srlimit=12" in kochwiki.such_url("Lasagne")
    assert "Lasagne" in kochwiki.such_url("Lasagne")
    detail = kochwiki.detail_url("Gelber Reis")
    assert "rvslots=main" in detail and "prop=revisions" in detail
    assert kochwiki.artikel_url("Gelber Reis").endswith("/wiki/Gelber_Reis")


def test_eine_fehlende_seite_ist_kein_fehler_sondern_ein_none():
    antwort = {"query": {"pages": [{"title": "Gibtsnicht", "missing": True}]}}
    assert kochwiki.parse_detail(antwort) is None
    text = "== Zutaten ==\n* [[Zutat:Salz|Salz]]"
    da = {"query": {"pages": [{"title": "Da", "revisions": [
        {"slots": {"main": {"content": text}}}]}]}}
    assert kochwiki.parse_detail(da) == ("Da", text)


# --------------------------------------------------------------------------
# Die Deckung über die Stichprobe — die Messlatte des Tickets

#: Die gemessenen Zahlen vom 2026-09-17 als MINDESTwerte. Sinkt eine, bricht
#: der Test und nennt sie. Sie stehen mit ihrer Herleitung in `EVALS.md`.
MINDESTENS = {
    "difficulty": 1.00,      # gemessen 100,0 %
    "servings": 0.90,        # gemessen  93,3 %
    "zutaten": 1.00,         # gemessen 100,0 %
    "schritte": 0.99,        # gemessen 100,0 %
    "zutat_link": 0.94,      # gemessen  94,4 % (Zeilen mit [[Zutat:…]])
    "kanonisch": 0.93,       # gemessen  93,7 % (Zeilen MIT kanonischem Namen)
}
#: Unter dieser Zahl ist der Abschnittsleser kaputt: mit dem naiven `^==`
#: bleiben von 1.254 Zutatenzeilen 837 übrig.
MINDESTZEILEN = 1250


def test_die_deckung_der_stichprobe_haelt_die_gemessenen_zahlen(stichprobe):
    """120 Rezepte, kein Netz, und jede Zahl mit Namen.

    Der Unterschied zwischen `zutat_link` und `kanonisch` ist gewollt und
    gemessen: 16 Zeilen enthalten zwar ein `[[Zutat:…]]`, aber nicht als
    Zutat — „Mehlbutter ''(je 1 EL [[Zutat:Butter|Butter]] …)''" ist
    Mehlbutter und nicht Butter. Der Parser zählt die Zeile deshalb nicht
    mit; die Quelle zählt sie.
    """
    zahl = len(stichprobe)
    assert zahl == 120, "die Stichprobe ist nicht mehr die gemessene"

    treffer = dict.fromkeys(MINDESTENS, 0)
    zeilen = roh_mit_link = schmutzig = 0
    for titel, wikitext in stichprobe.items():
        r = kochwiki.parse_rezept(titel, wikitext)
        treffer["difficulty"] += r["difficulty"] is not None
        treffer["servings"] += r["servings"] is not None
        treffer["zutaten"] += len(r["zutaten"]) > 0
        treffer["schritte"] += len(r["schritte"]) > 0
        for z in r["zutaten"]:
            zeilen += 1
            alt = any(a["kanonisch"] for a in z["alternativen"])
            treffer["kanonisch"] += bool(z["kanonisch"] or alt)
            if re.search(r"[\[\]{}']", z["name"]) or re.match(r"\d",
                                                               z["name"]):
                schmutzig += 1
        for zeile in kochwiki.abschnitt(wikitext, "Zutaten").splitlines():
            if zeile.strip().startswith(("*", "#")):
                treffer["zutat_link"] += "[[Zutat:" in zeile
                roh_mit_link += 1

    assert zeilen >= MINDESTZEILEN, (
        f"nur {zeilen} Zutatenzeilen statt {MINDESTZEILEN} — liest der "
        "Abschnittsleser noch über Untergruppen hinweg?")
    assert schmutzig == 0, f"{schmutzig} Namen mit Auszeichnung oder Ziffer"

    gemessen = {feld: treffer[feld] / (roh_mit_link if feld == "zutat_link"
                                       else zeilen if feld == "kanonisch"
                                       else zahl)
                for feld in MINDESTENS}
    zu_wenig = {f: f"{gemessen[f]:.3f} < {MINDESTENS[f]:.2f}"
                for f in MINDESTENS if gemessen[f] < MINDESTENS[f]}
    assert not zu_wenig, zu_wenig


def test_kein_name_traegt_auszeichnung_oder_eine_fuehrende_ziffer(stichprobe):
    """Die Latte des Tickets: 0 von 1.254 Zeilen. `[`, `]`, `{`, `}`, `'`
    und eine führende Ziffer sind Wikitext, der durchgerutscht ist."""
    schlecht = []
    for titel, wikitext in stichprobe.items():
        for z in kochwiki.parse_zutaten(wikitext):
            name = z["name"]
            if re.search(r"[\[\]{}']", name) or re.match(r"\d", name):
                schlecht.append((titel, name))
    assert schlecht == []
