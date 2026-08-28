"""Zutaten aus Chefkoch statt aus dem Gedächtnis des Modells (WB-338).

**Kein Test geht ins Netz.** Gespielt wird gegen zwei aufgezeichnete
Chefkoch-Antworten unter `tests/fixtures/` — die Suche nach „pho" und das
Detail des Rezepts, das dabei gewinnt. Aufgenommen hat sie
`scripts/record_chefkoch.py`, und das ist das Einzige im Projekt, was
chefkoch.de anfasst. Bricht ein Test hier, nachdem jemand die Fixture erneuert
hat, ist das ein echter Fund: die Quelle hat ihr Format geändert.

Der `FakeHTTP` unten ist deshalb streng: eine URL, die nicht vorgesehen war,
ist ein Testfehler und keine leere Antwort. Genau daran hängt die Zusage
„der zweite Abruf desselben Gerichts geht nicht ins Netz" — sie ist nur
geprüft, wenn ein zweiter Abruf auffallen WÜRDE.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from picknick import db
from picknick.assistant import chat as chatmodul
from picknick.assistant import plan
from picknick.gerichte import chefkoch, lauf, quelle, speicher
from picknick.llm import wake
from picknick.llm.client import Antwort
from picknick.scrapers import knuspr

FIXTURES = Path(__file__).parent / "fixtures"
SUCHE = json.loads((FIXTURES / "chefkoch_pho_suche.json")
                   .read_text(encoding="utf-8"))
REZEPT = json.loads((FIXTURES / "chefkoch_pho_rezept.json")
                    .read_text(encoding="utf-8"))
KNUSPR = FIXTURES / "knuspr_milch.json"

#: Das Rezept, das die Gewichtung aus der aufgezeichneten Suche wählt.
PHO_BO = "3595991540759513"


# --------------------------------------------------------------------------
# Doppelgänger

class FakeHTTP:
    """Chefkoch, aufgezeichnet. Kennt genau die URLs, die es kennen soll."""

    def __init__(self, seiten=None, fehler=None):
        self.seiten = dict(seiten or {})
        self.fehler = fehler
        self.geholt: list[str] = []

    def get(self, url):
        self.geholt.append(url)
        if self.fehler is not None:
            raise self.fehler
        for teil, payload in self.seiten.items():
            if teil in url:
                return _Antwort(payload)
        raise AssertionError(f"Unerwartete URL: {url}")


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


class KnusprHTTP:
    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, url):
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


class FakeLLM:
    """Ein Modell mit fest vorgegebenen Antworten. Merkt sich die Prompts."""

    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        self.aufrufe.append({"nachrichten": list(nachrichten), **weitere})
        if not self.antworten:
            raise AssertionError(
                f"Das Modell wurde {len(self.aufrufe)}-mal gefragt, es liegen "
                "aber nicht so viele Antworten bereit.")
        naechste = self.antworten.pop(0)
        if isinstance(naechste, Exception):
            raise naechste
        return Antwort(content=naechste, reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="fake")


def echtes_chefkoch():
    return FakeHTTP({"/v2/recipes?": SUCHE, f"/v2/recipes/{PHO_BO}": REZEPT})


ZUSATZ = [
    ("rifi", "Rinderfilet 400 g", "Fleisch", "Rind", "Filet"),
    ("ingw", "Ingwer frisch", "Obst & Gemüse", "Gemüse", "Ingwer"),
    ("zwie", "Zwiebeln Gelb, Netz", "Obst & Gemüse", "Gemüse", "Zwiebeln"),
    ("mien", "Mie Nudeln 250 g", "Nudeln", "Asia", "Mie"),
    ("klo1", "Toilettenpapier 10 Rollen", "Haushalt", "Papier",
     "Toilettenpapier"),
]


@pytest.fixture
def con(tmp_path):
    # Eine DATEI und nicht `:memory:`: der Abruf läuft in einem eigenen
    # Prozess und braucht einen Pfad. Eine Verbindung ohne Datei ist ein
    # eigener Testfall (siehe ganz unten).
    c = db.connect(tmp_path / "picknick.db")
    db.migrate(c)
    knuspr.crawl(c, KnusprHTTP([json.loads(KNUSPR.read_text(encoding="utf-8"))]),
                 ["milch"], pause_s=0)
    for external_id, name, l1, l2, l3 in ZUSATZ:
        c.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 199, '1 Stk', ?, ?, ?)",
            (external_id, name, l1, l2, l3))
    c.commit()
    yield c
    c.close()


def _extract(*paare, gericht=None):
    return json.dumps(
        {"gericht": gericht,
         "begriffe": [{"suchbegriffe": list(b) if isinstance(b, tuple) else [b],
                       "menge": m} for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _pid(con, name_teil):
    return con.execute("SELECT id FROM product WHERE name LIKE ?",
                       (f"%{name_teil}%",)).fetchone()["id"]


# --------------------------------------------------------------------------
# Aus der Fixture entstehen Zutaten mit Menge und Einheit

def test_aus_der_fixture_entstehen_zutaten_mit_menge_und_einheit():
    zutaten = chefkoch.parse_zutaten(REZEPT)
    assert len(zutaten) == 23

    nach_name = {z["raw_name"]: z for z in zutaten}
    wasser = nach_name["Wasser"]
    assert (wasser["amount"], wasser["unit"]) == (3.0, "Liter")
    knochen = nach_name["Markknochen"]
    assert (knochen["amount"], knochen["unit"]) == (1.0, "kg")

    # Die Gruppe wandert an jede Zutat mit — sie ist beim Kochen die halbe
    # Ordnung des Rezepts.
    assert wasser["gruppe"] == "Für die Brühe"
    # Und der Zusatz der Rezeptseite bleibt erhalten, ohne in den Namen zu
    # rutschen: „Zwiebel(n), süß, nicht rot" ist eine Zwiebel.
    zwiebel = nach_name["Zwiebel(n)"]
    assert zwiebel["name"] == "Zwiebel"
    assert "süß" in zwiebel["usage_info"]


def test_das_rezept_bringt_zubereitung_zeiten_und_herkunft_mit():
    rezept = chefkoch.parse_rezept(REZEPT)
    assert rezept["servings"] == 6
    assert (rezept["prep_minutes"], rezept["cook_minutes"]) == (90, 480)
    assert rezept["difficulty"] == 2
    assert rezept["site_url"].startswith("https://www.chefkoch.de/rezepte/")
    # Die Zubereitung wird NICHT zu einer Wand zusammengezogen: die Schritte
    # stehen einzeln da, so wie die Quelle sie getrennt hat.
    assert len(rezept["schritte"]) > 5
    assert all(s == s.strip() and s for s in rezept["schritte"])
    assert rezept["schritte"][0].startswith("Die Rindermarkknochen")


# --------------------------------------------------------------------------
# Normalisierung: die Form gehört zur Zutat

def test_zwiebel_klammer_wird_zwiebel():
    assert chefkoch.zutat_kette("Zwiebel(n)") == ["Zwiebel"]
    assert chefkoch.zutat_kette("Ei(er)") == ["Ei"]


def test_die_komma_form_behaelt_die_form_im_ersten_kettenglied():
    """„Tomaten, passierte" darf nicht einfach zu „Tomaten" werden.

    Der teuerste Befund der Vorprobe: gekürzt legte die Suche frische Tomaten
    und Ketchup vor, während das Rezept 500 ml passierte Tomaten will. Wo die
    Form zur Zutat gehört, steht sie im ersten Kettenglied — und das
    allgemeine Wort daneben, damit die Kette bei einer Katalog-Lücke noch
    etwas findet.
    """
    assert chefkoch.zutat_kette("Tomaten, passierte") == ["passierte Tomaten",
                                                          "Tomaten"]
    assert chefkoch.zutat_kette("Käse, geriebener") == ["geriebener Käse",
                                                        "Käse"]
    # Kein Komma, keine Umstellung.
    assert chefkoch.zutat_kette("Rinderfilet") == ["Rinderfilet"]
    # Und nichts Zusammengesetztes wird abgeschnitten: „Staud" fände
    # Staud's Apfelmus.
    assert chefkoch.zutat_kette("Staudensellerie") == ["Staudensellerie"]


# --------------------------------------------------------------------------
# Das bestbewertete Rezept, nicht das erste

def test_das_bestbewertete_rezept_wird_gewaehlt_und_die_stimmen_wiegen_mit():
    treffer = chefkoch.parse_treffer(SUCHE)
    assert len(treffer) == 12

    wahl = chefkoch.bestes(treffer)
    assert wahl["rezept_id"] == PHO_BO
    assert wahl["titel"].startswith("Pho Bo")

    # Eine 5,00 aus zwei Stimmen ist die höchste rohe Note der Liste — und
    # verliert trotzdem. Genau dafür ist die Gewichtung da.
    beste_rohe_note = max(treffer, key=lambda t: t["rating"])
    assert beste_rohe_note["rating"] == 5.0
    assert beste_rohe_note["rezept_id"] != wahl["rezept_id"]
    assert chefkoch.gewicht(beste_rohe_note) < chefkoch.gewicht(wahl)


def test_plus_rezepte_mit_platzhalter_stimmen_gewinnen_nicht():
    """255 Stimmen sind bei Chefkoch-Plus kein Abstimmungsergebnis.

    Gemessen: BEIDE `isPlus`-Treffer tragen exakt `numVotes: 255` (der
    grösste Wert eines Bytes), und der Detail-Endpunkt liefert zu beiden
    `rating: null`. Mit dieser Zahl gewänne „Wildenten-Pho" gegen „Pho Bo" —
    auf Grundlage von etwas, das nichts bedeutet.
    """
    treffer = chefkoch.parse_treffer(SUCHE)
    plus = [t for t in treffer if t["plus"]]
    assert plus and all(t["votes"] == 255 for t in plus)
    # Ohne die Regel gewänne eines davon.
    assert max(treffer, key=chefkoch.gewicht)["plus"] is True
    assert chefkoch.bestes(treffer)["plus"] is False

    # Kennt die Quelle NUR Plus-Rezepte, wird eines davon genommen: ein
    # Rezept mit fragwürdiger Note ist besser als gar keines.
    assert chefkoch.bestes(plus)["rezept_id"] == plus[0]["rezept_id"]


def test_der_abruf_holt_suche_und_detail_und_sonst_nichts():
    http = echtes_chefkoch()
    rezept = chefkoch.hole(http, "Pho", pause_s=0)
    assert rezept["titel"].startswith("Pho Bo")
    assert len(rezept["zutaten"]) == 23
    assert [u.split("/v2")[1] for u in http.geholt] == [
        "/recipes?query=Pho&limit=12", f"/recipes/{PHO_BO}"]


# --------------------------------------------------------------------------
# Der Zwischenspeicher

def test_der_zweite_abruf_desselben_gerichts_geht_nicht_ins_netz(con):
    zaehler = []
    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=zaehler.append)
    assert speicher.gericht(con, "Pho")["rezept"]["source_id"] == PHO_BO

    # Ab hier ist das Netz verboten. Der Speicher muss allein tragen.
    q = quelle.Quelle(starter=quelle.nicht_holen)
    gefunden = q.gericht(con, "pho")
    assert gefunden is not None
    assert len(gefunden["zutaten"]) == 23
    assert [g["query"] for g in q.bereit(con)] == ["Pho"]
    # `anfordern` sieht den frischen Eintrag und startet gar nichts.
    gestartet = []
    q2 = quelle.Quelle(starter=gestartet.append)
    assert q2.anfordern(con, "Pho") is False
    assert gestartet == []


def test_grossschreibung_und_umlaute_treffen_denselben_eintrag(con):
    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    assert speicher.gericht(con, "PHO") is not None
    assert speicher.schluessel("Gemüselasagne") == speicher.schluessel(
        "GEMUESELASAGNE")


def test_ein_erneuter_abruf_legt_das_rezept_nicht_zweimal_an(con):
    for _ in range(2):
        lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                        schreib=lambda _: None)
    assert con.execute("SELECT count(*) AS n FROM recipe").fetchone()["n"] == 1
    assert con.execute(
        "SELECT count(*) AS n FROM recipe_ingredient").fetchone()["n"] == 23


def test_ein_alter_eintrag_gilt_nicht_mehr(con):
    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    import time
    spaeter = time.time() + speicher.ALTER_OK_S + 60
    assert speicher.gericht(con, "Pho", uhr=lambda: spaeter) is None
    assert speicher.bereit(con, uhr=lambda: spaeter) == []


# --------------------------------------------------------------------------
# Ausfall der Quelle

def test_ein_gericht_ohne_treffer_wird_als_leer_vermerkt(con):
    """„Kennt Chefkoch nicht" ist kein Fehler — aber es wird gemerkt.

    Ohne diese Zeile kostete jedes unbekannte Wort bei JEDEM Chat-Zug zwei
    Anfragen an eine fremde Seite.
    """
    leer = FakeHTTP({"/v2/recipes?": {"count": 0, "results": []}})
    assert lauf.hole_eines(con, leer, "Kartoffelraumschiff", pause_s=0,
                           schreib=lambda _: None) == speicher.LEER
    assert speicher.gericht(con, "Kartoffelraumschiff") is None
    assert speicher.bereit(con) == []

    gestartet = []
    q = quelle.Quelle(starter=gestartet.append)
    assert q.anfordern(con, "Kartoffelraumschiff") is False
    assert gestartet == []


def test_chefkoch_nicht_erreichbar_wird_vermerkt_und_wirft_nicht(con):
    kaputt = FakeHTTP(fehler=OSError("Name or service not known"))
    assert lauf.hole_eines(con, kaputt, "Pho", pause_s=0,
                           schreib=lambda _: None) == speicher.FEHLER
    zeile = speicher.zeile(con, "Pho")
    assert zeile["status"] == speicher.FEHLER
    assert "Name or service not known" in zeile["error"]
    assert speicher.gericht(con, "Pho") is None


def test_ein_rezept_ohne_zutaten_gilt_als_fehler_und_nicht_als_leer(con):
    """Der Unterschied ist wichtig: `leer` hält eine Woche, `fehler` eine
    Stunde. Ein Formatwechsel darf sich nicht als „gibt es nicht" tarnen.
    """
    ohne = FakeHTTP({"/v2/recipes?": SUCHE,
                     f"/v2/recipes/{PHO_BO}": {"id": PHO_BO, "title": "Pho"}})
    assert lauf.hole_eines(con, ohne, "Pho", pause_s=0,
                           schreib=lambda _: None) == speicher.FEHLER


# --------------------------------------------------------------------------
# Der Chat-Zug

def test_der_zug_nimmt_die_zutaten_aus_der_quelle(con):
    """Der Kern des Tickets: die Zutaten kommen aus dem Rezept.

    Und zwar sichtbar — `weg` steht auf `chefkoch`, damit sich später messen
    lässt, ob die Quelle wirklich besser ist als das Raten.
    """
    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)

    llm = FakeLLM(
        # Stufe 1b: das Modell übersetzt Chefkochs Zutatenliste. Es sieht
        # keinen Katalog und nennt keine Produkte.
        _extract((("Rinderfilet", "Rindfleisch"), 1), (("Ingwer",), 1),
                 (("Mie Nudeln", "Nudeln"), 1)),
        _choose(("Rinderfilet", _pid(con, "Rinderfilet"), 1),
                ("Ingwer", _pid(con, "Ingwer"), 1),
                ("Mie Nudeln", _pid(con, "Mie Nudeln"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(starter=quelle.nicht_holen))
    ergebnis = agent.turn(con, "alles für Pho")

    assert ergebnis.weg == chatmodul.WEG_QUELLE == "chefkoch"
    assert ergebnis.n_produkte == 3
    assert ergebnis.quelle_name.startswith("Pho Bo")
    assert ergebnis.quelle_url.startswith("https://www.chefkoch.de/rezepte/")
    assert ergebnis.quelle_recipe_id

    # Und der Prompt von Stufe 1 trug die ZUTATEN DES REZEPTS, nicht den
    # Satz der Nutzerin. Das ist der ganze Unterschied zum Modellweg.
    erster_prompt = llm.aufrufe[0]["nachrichten"][-1]["content"]
    assert "Markknochen" in erster_prompt and "Zwiebel(n)" in erster_prompt
    assert "Sternanis" in erster_prompt


def test_was_neben_dem_gericht_stand_geht_nicht_verloren(con):
    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    llm = FakeLLM(_extract((("Rinderfilet",), 1), (("Toilettenpapier",), 1)),
                  _choose(("Rinderfilet", _pid(con, "Rinderfilet"), 1),
                          ("Toilettenpapier", _pid(con, "Toilettenpapier"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(starter=quelle.nicht_holen))
    ergebnis = agent.turn(con, "alles für Pho und Klopapier")

    assert ergebnis.weg == chatmodul.WEG_QUELLE
    prompt = llm.aufrufe[0]["nachrichten"][-1]["content"]
    assert "Klopapier" in prompt
    assert any("Toilettenpapier" in v["name"] for v in ergebnis.vorschlaege)


def test_der_erste_zug_fordert_an_und_geht_solange_den_modellweg(con):
    """Ein Gericht, das noch niemand geholt hat, bricht den Chat nicht.

    Der Zug läuft mit den geratenen Begriffen zu Ende und stösst den Abruf in
    einem EIGENEN PROZESS an — der Request wartet nie auf eine fremde Seite
    (Spec 3).
    """
    gestartet = []
    agent = chatmodul.Chat(
        FakeLLM(_extract((("Rinderfilet",), 1), gericht="Pho"),
                _choose(("Rinderfilet", _pid(con, "Rinderfilet"), 1))),
        wecker=Box(), quelle=quelle.Quelle(starter=gestartet.append))
    ergebnis = agent.turn(con, "alles für Pho")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.gericht == "Pho"
    assert ergebnis.angefordert is True
    assert ergebnis.n_produkte == 1          # der Zug lief zu Ende
    assert "Chefkoch" in ergebnis.meldung
    # Genau ein eigener Prozess, mit dem Gericht als Argument.
    assert len(gestartet) == 1
    assert gestartet[0][1:3] == ["-m", "picknick.gerichte.lauf"]
    assert gestartet[0][-2:] == ["--gericht", "Pho"]
    # Und der Wunsch steht in der Datenbank, damit ein Lauf ohne Argument ihn
    # nachholen kann.
    assert [w["query"] for w in speicher.offene(con)] == ["Pho"]


def test_ein_kaputter_prozessstart_bricht_den_chat_nicht(con):
    def explodiert(argv):
        raise OSError("kein Python gefunden")

    agent = chatmodul.Chat(
        FakeLLM(_extract((("Rinderfilet",), 1), gericht="Pho"),
                _choose(("Rinderfilet", _pid(con, "Rinderfilet"), 1))),
        wecker=Box(), quelle=quelle.Quelle(starter=explodiert))
    ergebnis = agent.turn(con, "alles für Pho")
    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.angefordert is False
    assert ergebnis.n_produkte == 1


def test_ohne_gericht_im_satz_wird_nichts_angefordert(con):
    gestartet = []
    agent = chatmodul.Chat(
        FakeLLM(_extract((("Ingwer",), 1)),
                _choose(("Ingwer", _pid(con, "Ingwer"), 1))),
        wecker=Box(), quelle=quelle.Quelle(starter=gestartet.append))
    ergebnis = agent.turn(con, "Ingwer bitte")
    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.gericht is None
    assert gestartet == []


def test_ein_gescheitertes_stufe_1_faellt_auf_die_rohe_zutatenliste_zurueck(con):
    """Wenn das Modell die Zutatenliste nicht zerlegt, ist sie trotzdem da.

    Das ist der Unterschied zum Modellweg: dort gibt es ohne Stufe 1 nichts.
    Hier liegt die Liste vor, und aus ihr lässt sich ohne Modell eine
    Begriffskette bauen — schlechter, aber vorhanden.
    """
    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    llm = FakeLLM("kein JSON, sondern Prosa",
                  _choose(("Rinderfilet", _pid(con, "Rinderfilet"), 1)))
    agent = chatmodul.Chat(llm, wecker=Box(),
                           quelle=quelle.Quelle(starter=quelle.nicht_holen))
    ergebnis = agent.turn(con, "alles für Pho")

    assert ergebnis.weg == chatmodul.WEG_QUELLE
    assert ergebnis.n_produkte == 1
    assert "roh aus dem Rezept" in ergebnis.meldung
    # Wasser und Salz stehen im Rezept und gehören nicht auf den Zettel.
    begriffe = {b["suchbegriffe"][0] for b in ergebnis.begriffe}
    assert "Wasser" not in begriffe and "Rinderfilet" in begriffe


def test_ein_gespeichertes_rezept_mit_produkten_schlaegt_die_quelle(con):
    """Die Rangfolge der drei Wege.

    Ein Rezept, dem jemand von Hand Produkte zugeordnet hat, enthält genau
    die Produkte, die die beiden selbst ausgesucht haben. Das kann keine
    fremde Seite besser wissen.
    """
    from picknick import recipes

    recipe_id = lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                                schreib=lambda _: None)
    assert recipe_id == speicher.OK
    geholt = speicher.gericht(con, "Pho")["rezept"]["id"]
    recipes.zutat_hinzufuegen(con, geholt,
                              product_id=_pid(con, "Rinderfilet"))

    agent = chatmodul.Chat(FakeLLM(), wecker=Box(),
                           quelle=quelle.Quelle(starter=quelle.nicht_holen))
    # Der Rezeptname ist der Titel von Chefkoch — der steht im Satz.
    ergebnis = agent.turn(con, "alles für Pho Bo - Vietnamesische "
                               "Rindfleischsuppe")
    assert ergebnis.weg == chatmodul.WEG_REZEPT


def test_ein_geholtes_rezept_ohne_produkte_faengt_den_zug_nicht_ab(con):
    """Die Kehrseite: solange `recipe_item` leer ist, greift die Quelle.

    Sonst schnitte das geholte Rezept den Weg ab und hätte nichts
    vorzuschlagen — 23 Zutaten, null Produkte.
    """
    from picknick.assistant import rezeptweg

    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    assert not rezeptweg.erkenne(con, "alles für Pho Bo - Vietnamesische "
                                      "Rindfleischsuppe")


# --------------------------------------------------------------------------
# Was im Rezept landet — zum Kochen, nicht zum Einkaufen

def test_das_geholte_rezept_steht_mit_zubereitung_in_der_sammlung(con):
    from picknick import recipes

    lauf.hole_eines(con, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    recipe_id = speicher.gericht(con, "Pho")["rezept"]["id"]
    r = recipes.rezept(con, recipe_id)

    assert r["name"].startswith("Pho Bo")
    assert r["servings"] == 6
    assert (r["prep_minutes"], r["cook_minutes"]) == (90, 480)
    assert r["difficulty"] == 2
    assert r["source"] == "chefkoch"
    assert r["source_url"].startswith("https://www.chefkoch.de/rezepte/")
    assert r["source_rating"] == 4.84 and r["source_votes"] == 62
    # Die Zubereitung als Schritte, nicht als Wand.
    assert len(r["zubereitung"]) > 5
    # Die Zutaten laut Rezept — mit Menge und Einheit, getrennt von den
    # Produkten, die dafür gekauft werden.
    assert len(r["rezeptzutaten"]) == 23
    assert r["zutaten"] == []


def test_ein_selbst_angelegtes_rezept_hat_keine_zubereitung(con):
    from picknick import recipes

    recipe_id = recipes.anlegen(con, "Nudeln mit Butter", servings=2)
    r = recipes.rezept(con, recipe_id)
    assert r["instructions"] is None
    assert r["zubereitung"] == []
    assert r["rezeptzutaten"] == []
    assert r["source"] is None


def test_die_rezeptseite_zeigt_zubereitung_und_herkunft(con, tmp_path):
    """Sichtbar, nicht nur gespeichert: das ist fremde Arbeit."""
    from fastapi.testclient import TestClient

    from picknick.web import app as webapp

    datei = tmp_path / "picknick.db"
    c = db.connect(datei)
    db.migrate(c)
    lauf.hole_eines(c, echtes_chefkoch(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    recipe_id = speicher.gericht(c, "Pho")["rezept"]["id"]
    c.close()

    client = TestClient(webapp.create_app(db_path=datei, image_dir=tmp_path))
    seite = client.get(f"/rezepte/{recipe_id}").text
    assert "Zubereitung" in seite
    assert "Die Rindermarkknochen" in seite
    assert "480 Min. Kochzeit" in seite
    assert "https://www.chefkoch.de/rezepte/" in seite
    # Die Zutaten laut Rezept, mit Menge und Einheit.
    assert "Markknochen" in seite and "Liter" in seite
    # Und die Seite behauptet NICHT, das Rezept habe keine Zutaten, bloss
    # weil noch kein Produkt verknüpft ist. Es sind 23.
    assert "Noch keine Zutat." not in seite
    assert "Noch kein Produkt verknüpft" in seite


# --------------------------------------------------------------------------
# Stufe 1 erkennt das Gericht

def test_stufe_1_gibt_den_gerichtsnamen_mit_zurueck():
    llm = FakeLLM(_extract((("Rinderhackfleisch",), 1),
                           gericht="Spaghetti Bolognese"))
    ergebnis = plan.extract_plan(llm, "alles für Spaghetti Bolognese")
    assert ergebnis.gericht == "Spaghetti Bolognese"
    assert ergebnis.zutaten[0]["suchbegriffe"] == ["Rinderhackfleisch"]


def test_ohne_gericht_bleibt_das_feld_leer():
    llm = FakeLLM(_extract((("Milch",), 1)))
    assert plan.extract_plan(llm, "Milch").gericht is None
    llm2 = FakeLLM(json.dumps({"begriffe": [{"suchbegriffe": ["Milch"],
                                             "menge": 1}]}))
    assert plan.extract_plan(llm2, "Milch").gericht is None


def test_ein_unsinniger_gerichtsname_wird_nicht_uebernommen():
    """Zwei Zeichen sind kein Gericht, ein Satz auch nicht.

    Beides wäre eine sinnlose Anfrage an eine fremde Seite.
    """
    kurz = FakeLLM(_extract((("Milch",), 1), gericht="ei"))
    assert plan.extract_plan(kurz, "Milch").gericht is None
    lang = FakeLLM(_extract((("Milch",), 1), gericht="x" * 200))
    assert plan.extract_plan(lang, "Milch").gericht is None


def test_extract_bleibt_die_kurzform_und_gibt_weiter_eine_liste():
    llm = FakeLLM(_extract((("Milch",), 2), gericht="Milchreis"))
    assert plan.extract(llm, "Milchreis") == [{"suchbegriffe": ["Milch"],
                                               "menge": 2}]


# --------------------------------------------------------------------------
# Der Web-Prozess ruft nie eine fremde Seite auf (Spec 3)

def test_die_quelle_kennt_keine_url_und_kein_httpx():
    """Strukturell und nicht als Vorsatz: `quelle` importiert kein httpx.

    Was der Web-Prozess anfasst, kann gar nicht ins Netz — der Abruf steckt
    in `lauf`/`chefkoch`, und die laufen in einem eigenen Prozess.
    """
    text = Path(quelle.__file__).read_text(encoding="utf-8")
    quelltext = text.split('"""', 2)[2]
    assert "import httpx" not in quelltext and "from httpx" not in quelltext
    assert "://" not in quelltext


def test_ohne_dateipfad_wird_nichts_gestartet():
    """Eine Datenbank im Speicher kann ein eigener Prozess nicht lesen.

    Dann bleibt es beim Modellweg — und es wird auch kein Wunsch
    eingetragen, den niemand je einlösen könnte.
    """
    c = db.connect(":memory:")
    db.migrate(c)
    try:
        gestartet = []
        q = quelle.Quelle(starter=gestartet.append)
        assert q.anfordern(c, "Pho") is False
        assert gestartet == []
        assert speicher.offene(c) == []
    finally:
        c.close()


def test_der_lauf_migriert_selbst(tmp_path):
    """Ein eigener Prozess an einer alten Datei muss sich selbst einrichten.

    Gemessen beim ersten Lauf gegen die echte Datenbank: ohne `migrate()`
    fiel er mit „no such column: source" um. Der Web-Prozess migriert beim
    Start — dieser Prozess wird aber auch von Hand gestartet, und dann ist er
    der Erste.
    """
    import sqlite3

    datei = tmp_path / "alt.db"
    roh = sqlite3.connect(datei)
    roh.execute("CREATE TABLE recipe (id INTEGER PRIMARY KEY, name TEXT"
                " NOT NULL, servings INTEGER, note TEXT)")
    roh.commit()
    roh.close()

    zaehler = lauf.lauf(str(datei), ["Pho"], http=echtes_chefkoch(), pause_s=0,
                        schreib=lambda _: None)
    assert zaehler["ok"] == 1

    c = db.connect(datei)
    try:
        assert speicher.gericht(c, "Pho")["rezept"]["source_id"] == PHO_BO
    finally:
        c.close()
