"""Tests für den Chat-Agenten (WB-327).

**Kein Test geht ins Netz und keiner weckt die Box.** Das Modell ist hier ein
`FakeLLM` mit fest vorgegebener Antwort; der Weckzustand wird untergeschoben.
Der Katalog kommt wie in den anderen Tests aus der aufgezeichneten
Knuspr-Antwort, ergänzt um die Handvoll Produkte, die eine Bolognese braucht.

Der wichtigste Test des Tickets steht unter „Das Modell erfindet niemals
Produkte": nennt Stufe 3 eine ID, die ihr nicht vorgelegt wurde, wird der
Vorschlag verworfen — nicht repariert, nicht auf das nächstbeste Produkt
gebogen. Es gibt ihn zweimal, denn es sind zwei verschiedene Fehler: eine ID,
die es gar nicht gibt, und eine ID, die es gibt, die aber zu dieser Suche nie
vorgelegt wurde. Die zweite ist die gefährlichere, weil sie in der Datenbank
gültig aussieht.
"""
import json

import pytest

from zettel import orders, recipes
from zettel.assistant import chat as chatmodul
from zettel.assistant import plan, rezeptweg, vorschlaege
from zettel.catalog import search
from zettel.llm import wake
from zettel.llm.client import Antwort, ModellNichtErreichbar

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"


# --------------------------------------------------------------------------
# Doppelgänger

class FakeLLM:
    """Ein Modell mit fest vorgegebenen Antworten.

    Kein Socket, kein Wecken — und es merkt sich, was es gefragt wurde. Beides
    wird gebraucht: der Rezeptweg wird daran geprüft, dass hier NICHTS ankommt.
    """

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


class NieGefragt:
    """Ein Modell, das jeden Aufruf als Testfehler meldet."""

    def modell(self, **_):
        raise AssertionError("Das Modell wurde nach dem Kürzel gefragt.")

    def chat(self, *_, **__):
        raise AssertionError(
            "Das Modell wurde gefragt, obwohl kein Modell nötig war.")


class Box:
    """Der Weckzustand, ohne die echte Box anzufassen."""

    def __init__(self, bedient=True, grund="Die Box bedient gerade nicht."):
        self._bedient = bedient
        self._grund = grund
        self.gefragt = 0

    def zustand(self):
        self.gefragt += 1
        if self._bedient:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(wake.NICHT_ERREICHBAR, grund=self._grund)


# --------------------------------------------------------------------------
# Katalog

ZUSATZ = [
    ("hack1", "Rinderhackfleisch 500 g", "Fleisch", "Rind", "Hackfleisch"),
    ("toma1", "Passierte Tomaten 500 g", "Konserven", "Tomaten", "Passata"),
    ("nude1", "Spaghetti No. 5 500 g", "Nudeln", "Pasta", "Spaghetti"),
    ("klo1", "Toilettenpapier 10 Rollen", "Haushalt", "Papier", "Toilettenpapier"),
    # Acht Butter (WB-359). Der Fall, der es vor diesem Ticket NICHT tat:
    # „Butter" ist einbegriffig, und mit einer Grenze von 5 je Begriff blieben
    # nach Abzug des gewählten Produkts genau vier Alternativen. Die Namen sind
    # dem echten Katalog nachgebildet (siehe OBSERVABILITY.md).
    ("but1", "Weihenstephan Butter", "Molkerei", "Butter & Fette", "Butter"),
    ("but2", "Landliebe Butter rahmig-frisch", "Molkerei", "Butter & Fette",
     "Butter"),
    ("but3", "Kerrygold irische Butter", "Molkerei", "Butter & Fette",
     "Butter"),
    ("but4", "Minus L Butter laktosefrei", "Molkerei", "Butter & Fette",
     "Butter"),
    ("but5", "Isigny Butter AOP", "Molkerei", "Butter & Fette", "Butter"),
    ("but6", "Lindner Butter mit Salz", "Molkerei", "Butter & Fette",
     "Butter"),
    ("but7", "ButterBoyz Chili & Röstzwiebel", "Molkerei", "Butter & Fette",
     "Markenbutter"),
    ("but8", "Neuvic Périgord Trüffel Butter", "Molkerei", "Butter & Fette",
     "Markenbutter"),
]


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _vorgelegt(con, begriff, limit=plan.KANDIDATEN_MODELL):
    """Was die Suche zu diesem Begriff tatsächlich vorlegt.

    Die Tests nehmen ihre ids daher und nicht aus einem festen Namen: welches
    Produkt bei „Milch" oben steht, entscheidet bm25 — und wer das in einem
    Test festschreibt, prüft irgendwann die Rangfolge statt der Zweistufigkeit.
    """
    from zettel.catalog import search
    return search.search(con, begriff, limit=limit)


def _zusatz(con):
    for external_id, name, l1, l2, l3 in ZUSATZ:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 199, '1 Stk', ?, ?, ?)",
            (external_id, name, l1, l2, l3))
    con.commit()


@pytest.fixture
def con(vorlagen):
    """Katalog plus die Produkte von oben — einmal gebaut, hier kopiert.

    Die Vorlage baut `conftest.py`; sie heisst `assistant_katalog`, weil sie
    NICHT dieselbe ist wie der blosse Katalog: `ZUSATZ` gehört dazu, und ein
    Test, der die Butter-Alternativen zählt, hinge sonst davon ab, welche
    Datei zuerst lief.
    """
    c = vorlagen.con("assistant_katalog", vorlagen.katalog, _zusatz)
    yield c
    c.close()


def _extract(*paare):
    """Eine Stufe-1-Antwort. Ein Begriff oder ein Tupel als ganze Kette.

    Seit WB-340 liefert das Modell je Zutat mehrere Suchbegriffe; `("a", "b")`
    schreibt die Kette, `"a"` die Kette der Länge eins.
    """
    return json.dumps(
        {"begriffe": [{"suchbegriffe": list(b) if isinstance(b, tuple) else [b],
                       "menge": m} for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _chat(con, *antworten, bedient=True, **weitere):
    llm = FakeLLM(*antworten)
    return chatmodul.Chat(llm, wecker=Box(bedient), **weitere), llm


# --------------------------------------------------------------------------
# Das Modell erfindet niemals Produkte — der Kern des Tickets

def test_erfundene_produkt_id_wird_verworfen(con):
    """Stufe 3 nennt eine ID, die es nicht gibt. Sie wird NICHT eingesetzt."""
    agent, _ = _chat(con, _extract(("Milch", 1)),
                     _choose(("Milch", 999999, 1)))
    ergebnis = agent.turn(con, "Milch")

    assert [v["product_id"] for v in ergebnis.vorschlaege] == [None]
    assert ergebnis.verworfen and ergebnis.verworfen[0]["produkt_id"] == 999999
    # Nicht repariert: es steht kein Produkt an der Zeile, obwohl die Suche
    # welche vorgelegt hatte.
    assert con.execute(
        "SELECT count(*) AS n FROM chat_suggestion"
        " WHERE product_id IS NOT NULL").fetchone()["n"] == 0


def test_nicht_vorgelegte_aber_echte_id_wird_verworfen(con):
    """Die gefährlichere Sorte: die ID existiert, war aber nicht im Angebot.

    Sie würde durch jeden Fremdschlüssel kommen und in der Datenbank völlig
    unauffällig aussehen. Verworfen wird sie trotzdem — vorgelegt war sie
    nicht.
    """
    klopapier = _pid(con, "Toilettenpapier 10 Rollen")
    agent, _ = _chat(con, _extract(("Milch", 1)),
                     _choose(("Milch", klopapier, 1)))
    ergebnis = agent.turn(con, "Milch")

    assert [v["free_text"] for v in ergebnis.vorschlaege] == ["Milch"]
    assert ergebnis.verworfen[0]["produkt_id"] == klopapier
    assert ergebnis.verworfen[0]["grund"] == "nicht vorgelegt"


def test_der_verworfene_begriff_geht_nicht_verloren(con):
    """Nach dem Verwerfen bleibt der Begriff als Freitext stehen.

    Sonst wäre die Halluzination doppelt teuer: falsches Produkt weg UND die
    Zutat weg, ohne dass es jemand merkt.
    """
    agent, _ = _chat(con, _extract(("Milch", 2)), _choose(("Milch", 4242, 2)))
    ergebnis = agent.turn(con, "Milch")
    zeile = ergebnis.vorschlaege[0]
    assert zeile["ist_freitext"] and zeile["name"] == "Milch"
    assert zeile["qty"] == 2
    assert zeile["search_term"] == "Milch"


def test_stufe_eins_sieht_keinen_katalog(con):
    """`plan.extract` bekommt den Satz und sonst nichts.

    Diese Zusicherung ist der Grund, warum Stufe 1 keine ID nennen KANN. Sie
    wird hier am tatsächlich verschickten Prompt geprüft und nicht an der
    Absicht.
    """
    agent, llm = _chat(con, _extract(("Milch", 1)), _choose())
    agent.turn(con, "Milch")
    erster = " ".join(n["content"] for n in llm.aufrufe[0]["nachrichten"])
    assert MILCH not in erster
    assert str(_pid(con, MILCH)) not in erster


def test_stufe_drei_bekommt_nur_die_kandidaten_der_suche(con):
    """Und zwar so viele, wie `kandidaten` sagt (Spec 8.3: 5 gegen 20)."""
    agent, llm = _chat(con, _extract(("Milch", 1)), _choose(), kandidaten=3)
    agent.turn(con, "Milch")
    zweiter = llm.aufrufe[1]["nachrichten"][-1]["content"]
    vorgelegt = json.loads(zweiter[zweiter.index("["):])
    assert len(vorgelegt[0]["kandidaten"]) == 3


# --------------------------------------------------------------------------
# Rezepte kürzen Stufe 1 ab (Spec 6)

def test_rezept_kuerzt_ab_und_fragt_kein_modell(con):
    rid = recipes.anlegen(con, "Spaghetti Bolognese", zutaten=[
        {"product_id": _pid(con, "Rinderhackfleisch 500 g")},
        {"product_id": _pid(con, "Passierte Tomaten 500 g"), "qty": 2}])
    agent = chatmodul.Chat(NieGefragt(), wecker=Box(bedient=True))
    ergebnis = agent.turn(con, "mach mir Spaghetti Bolognese")

    assert ergebnis.weg == chatmodul.WEG_REZEPT
    assert [v["qty"] for v in ergebnis.vorschlaege] == [1, 2]
    assert all(not v["ist_freitext"] for v in ergebnis.vorschlaege)
    assert ergebnis.rezepte == ["Spaghetti Bolognese"]
    assert recipes.rezept(con, rid)["n_zutaten"] == 2


def test_rezept_geht_auch_wenn_die_box_schlaeft(con):
    """Der Rezeptweg braucht kein Modell — also auch keine wache Box."""
    recipes.anlegen(con, "Bolognese", zutaten=[
        {"product_id": _pid(con, "Rinderhackfleisch 500 g")}])
    box = Box(bedient=False)
    agent = chatmodul.Chat(NieGefragt(), wecker=box)
    ergebnis = agent.turn(con, "Bolognese bitte")
    assert ergebnis.weg == chatmodul.WEG_REZEPT
    assert box.gefragt == 0, "Der Rezeptweg hat die Box angefasst."


def test_was_neben_dem_rezept_steht_wird_freitext(con):
    """„… und Klopapier" darf nicht mit dem Rezepttreffer verschwinden."""
    recipes.anlegen(con, "Spaghetti Bolognese", zutaten=[
        {"product_id": _pid(con, "Rinderhackfleisch 500 g")}])
    agent = chatmodul.Chat(NieGefragt(), wecker=Box())
    ergebnis = agent.turn(con, "alles für Spaghetti Bolognese, und Klopapier")
    freitexte = [v["name"] for v in ergebnis.vorschlaege if v["ist_freitext"]]
    assert freitexte == ["Klopapier"]


def test_rezept_ohne_zutaten_nimmt_den_modellweg(con):
    """Ein leeres Rezept darf den Modellweg nicht abschneiden."""
    recipes.anlegen(con, "Bolognese")
    agent, llm = _chat(con, _extract(("Hackfleisch", 1)), _choose())
    ergebnis = agent.turn(con, "Bolognese")
    assert ergebnis.weg == chatmodul.WEG_LLM
    assert llm.aufrufe


def test_rezeptname_trifft_nicht_mitten_im_wort(con):
    recipes.anlegen(con, "Ei", zutaten=[{"free_text": "Eier"}])
    assert not rezeptweg.erkenne(con, "Eiscreme und Einkaufsliste")
    assert rezeptweg.erkenne(con, "ein Ei bitte")


# --------------------------------------------------------------------------
# Kein Begriff verschwindet still

def test_begriff_ohne_katalogtreffer_wird_freitext(con):
    """Die Suche kennt nur Wortanfänge (WB-322) — sie wird Lücken haben.

    Ein Begriff, zu dem nichts gefunden wird, muss als Freitext sichtbar
    bleiben. Sonst fehlt im Laden etwas, ohne dass es jemand gemerkt hat.
    """
    agent, _ = _chat(con, _extract(("Zahnstocher", 1), ("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    ergebnis = agent.turn(con, "Zahnstocher und Landmilch")

    nach_begriff = {v["search_term"]: v for v in ergebnis.vorschlaege}
    assert nach_begriff["Zahnstocher"]["ist_freitext"]
    assert nach_begriff["Zahnstocher"]["free_text"] == "Zahnstocher"
    assert nach_begriff["Landmilch"]["product_id"] == _pid(con, MILCH)


def test_begriff_ohne_wahl_wird_freitext(con):
    """Das Modell lässt einen Begriff aus, obwohl es Kandidaten gab."""
    agent, _ = _chat(con, _extract(("Milch", 1)), _choose())
    ergebnis = agent.turn(con, "Milch")
    assert ergebnis.vorschlaege[0]["ist_freitext"]


def test_search_term_und_rang_stehen_an_der_zeile(con):
    """Beides gehört an die Zeile, nicht nur in den Trace (Spec 8.1)."""
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    ergebnis = agent.turn(con, "Landmilch")
    row = con.execute("SELECT search_term, rank, decision FROM chat_suggestion"
                      ).fetchone()
    assert row["search_term"] == "Landmilch"
    assert row["rank"] is not None and row["rank"] > 0
    assert row["decision"] == "offen"
    assert ergebnis.vorschlaege[0]["rang"] == pytest.approx(row["rank"])


def test_rang_kommt_aus_der_suche_und_nicht_vom_modell(con):
    erwartet = {t["id"]: t["rang"] for t in _vorgelegt(con, "Landmilch")}
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    zeile = agent.turn(con, "Landmilch").vorschlaege[0]
    assert zeile["rang"] == pytest.approx(erwartet[zeile["product_id"]])


# --------------------------------------------------------------------------
# Kaputte Modellantworten brechen den Request nicht

@pytest.mark.parametrize("antwort", [
    "das ist gar kein JSON",
    "",
    json.dumps({"begriffe": []}),
    json.dumps({"begriffe": "Milch"}),
    json.dumps([1, 2, 3]),
    "```json\n{\"begriffe\": [{\"begriff\": null}]}\n```",
])
def test_kaputte_stufe_eins_bricht_nichts(con, antwort):
    agent, _ = _chat(con, antwort)
    ergebnis = agent.turn(con, "irgendwas")
    assert ergebnis.vorschlaege == []
    assert "Suchbegriffe" in ergebnis.meldung
    # Der Zug ist trotzdem im Verlauf: die Nutzerin sieht ihre Frage und die
    # Begründung, statt auf eine Fehlerseite zu schauen.
    assert len(vorschlaege.verlauf(con, ergebnis.order_id)) == 2


@pytest.mark.parametrize("antwort", [
    "kein JSON weit und breit",
    json.dumps({"auswahl": {"produkt_id": 1}}),
])
def test_kaputte_stufe_drei_macht_alles_zu_freitext(con, antwort):
    agent, _ = _chat(con, _extract(("Milch", 1), ("Zahnstocher", 1)), antwort)
    ergebnis = agent.turn(con, "Milch und Zahnstocher")
    assert [v["ist_freitext"] for v in ergebnis.vorschlaege] == [True, True]
    assert "Freitext" in ergebnis.meldung


def test_leere_auswahl_ist_kein_fehler(con):
    """`{"auswahl": []}` heisst „nichts davon passt" und ist eine Aussage."""
    agent, _ = _chat(con, _extract(("Milch", 1)), json.dumps({"auswahl": []}))
    ergebnis = agent.turn(con, "Milch")
    assert ergebnis.vorschlaege[0]["ist_freitext"]


def test_codefence_und_vorrede_werden_ausgepackt(con):
    agent, _ = _chat(
        con,
        "Hier ist die Liste:\n```json\n" + _extract(("Landmilch", 1)) + "\n```",
        "```\n" + _choose(("Landmilch", _pid(con, MILCH), 1)) + "\n```")
    ergebnis = agent.turn(con, "Landmilch")
    assert ergebnis.vorschlaege[0]["product_id"] == _pid(con, MILCH)


def test_leerer_satz_wird_abgelehnt(con):
    agent, _ = _chat(con)
    with pytest.raises(chatmodul.ChatFehler):
        agent.turn(con, "   ")


# --------------------------------------------------------------------------
# Fällt das Modell aus, ist nur der Chat betroffen (Spec 11)

def test_schlafende_box_wirft_und_schreibt_nichts(con):
    agent, _ = _chat(con, bedient=False)
    with pytest.raises(chatmodul.ChatNichtVerfuegbar) as e:
        agent.turn(con, "Milch")
    assert e.value.zustand.zustand == wake.NICHT_ERREICHBAR
    # Kein halber Zug in der Datenbank.
    assert con.execute("SELECT count(*) AS n FROM chat_message"
                       ).fetchone()["n"] == 0


def test_abbruch_mitten_im_zug_wird_zu_chatfehler(con):
    """Bricht die Verbindung zwischen Stufe 1 und 3 weg, ist das kein 500er."""
    agent, _ = _chat(con, _extract(("Milch", 1)),
                     ModellNichtErreichbar("weg"))
    with pytest.raises(chatmodul.ChatNichtVerfuegbar):
        agent.turn(con, "Milch")


# --------------------------------------------------------------------------
# Nichts landet ungefragt im Warenkorb — und `decision` ist das Label

def test_vorschlag_liegt_nicht_im_korb(con):
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    agent.turn(con, "Landmilch")
    assert orders.inhalt(con) == []


def test_bestaetigen_setzt_kept_und_legt_ein(con):
    agent, _ = _chat(con, _extract(("Landmilch", 2)),
                     _choose(("Landmilch", _pid(con, MILCH), 2)))
    ergebnis = agent.turn(con, "Landmilch")
    v = vorschlaege.entscheiden(con, ergebnis.vorschlaege[0]["id"], "kept")

    assert v["decision"] == "kept" and v["decided_at"]
    zeilen = orders.inhalt(con)
    assert [(z["product_id"], z["qty"]) for z in zeilen] == [(_pid(con, MILCH), 2)]


def test_verwerfen_setzt_removed_und_legt_nichts_ein(con):
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    ergebnis = agent.turn(con, "Landmilch")
    v = vorschlaege.entscheiden(con, ergebnis.vorschlaege[0]["id"], "removed")
    assert v["decision"] == "removed" and v["decided_at"]
    assert orders.inhalt(con) == []


def test_zweimal_ja_legt_nur_einmal_ein(con):
    """Ein doppelter Tipp auf dem Handy darf die Menge nicht verdoppeln."""
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    sid = agent.turn(con, "Landmilch").vorschlaege[0]["id"]
    vorschlaege.entscheiden(con, sid, "kept")
    vorschlaege.entscheiden(con, sid, "kept")
    assert [z["qty"] for z in orders.inhalt(con)] == [1]


def test_freitext_vorschlag_wird_zum_freitext_posten(con):
    agent, _ = _chat(con, _extract(("Zahnstocher", 1)), _choose())
    sid = agent.turn(con, "Zahnstocher").vorschlaege[0]["id"]
    vorschlaege.entscheiden(con, sid, "kept")
    zeile = orders.inhalt(con)[0]
    assert zeile["ist_freitext"] and zeile["name"] == "Zahnstocher"


def test_alle_entscheiden_laesst_bereits_entschiedene_stehen(con):
    agent, _ = _chat(con, _extract(("Landmilch", 1), ("Zahnstocher", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    ergebnis = agent.turn(con, "Landmilch und Zahnstocher")
    vorschlaege.entscheiden(con, ergebnis.vorschlaege[0]["id"], "removed")
    danach = vorschlaege.alle_entscheiden(con, ergebnis.chat_message_id, "kept")
    assert [v["decision"] for v in danach] == ["removed", "kept"]


def test_quote_zaehlt_offene_nicht_mit(con):
    """Spec 8.1: was nie entschieden wurde, ist kein Fehler des Modells."""
    agent, _ = _chat(con, _extract(("Landmilch", 1), ("Zahnstocher", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    ergebnis = agent.turn(con, "Landmilch und Zahnstocher")
    assert vorschlaege.quote(con, ergebnis.chat_message_id)["quote"] is None
    vorschlaege.entscheiden(con, ergebnis.vorschlaege[0]["id"], "kept")
    q = vorschlaege.quote(con, ergebnis.chat_message_id)
    assert (q["quote"], q["offen"]) == (1.0, 1)


def test_unbekannte_entscheidung_wird_abgelehnt(con):
    agent, _ = _chat(con, _extract(("Milch", 1)), _choose())
    sid = agent.turn(con, "Milch").vorschlaege[0]["id"]
    with pytest.raises(vorschlaege.VorschlagFehler):
        vorschlaege.entscheiden(con, sid, "vielleicht")


def test_chat_haengt_am_warenkorb(con):
    """Der Chat gehört zur Bestellung und wandert beim Abschicken mit.

    Das bleibt auch, seit der Chat einen eigenen ORT hat (WB-382): „eigener
    Ort" heisst eine eigene Ansicht, nicht ein eigener Besitzer. Löste man
    die Bindung, blieben die Entscheidungen nicht mehr bei dem Einkauf, zu
    dem sie gehören — und daran hängen die Eval-Labels aus WB-329.
    """
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    ergebnis = agent.turn(con, "Landmilch")
    vorschlaege.entscheiden(con, ergebnis.vorschlaege[0]["id"], "kept")
    bestellung = orders.abschicken(con)
    assert bestellung["id"] == ergebnis.order_id
    assert len(vorschlaege.verlauf(con, bestellung["id"])) == 2


# --------------------------------------------------------------------------
# Kleinigkeiten in `plan`, die stillschweigend schiefgehen könnten

def test_doppelte_begriffe_werden_einmal_gesucht(con):
    agent, _ = _chat(con, _extract(("Milch", 1), ("milch", 3)), _choose())
    ergebnis = agent.turn(con, "Milch und milch")
    assert len(ergebnis.begriffe) == 1


def test_menge_wird_gedeckelt_und_nie_null(con):
    llm = FakeLLM(json.dumps({"begriffe": [
        {"begriff": "Milch", "menge": 0},
        {"begriff": "Tomaten", "menge": 10 ** 6},
        {"begriff": "Nudeln", "menge": "zwei"}]}))
    assert [b["menge"] for b in plan.extract(llm, "egal")] == [
        1, plan.MAX_MENGE, 1]


def test_boolesches_true_ist_keine_produkt_id():
    """`int(True)` ist 1 — und 1 ist irgendwo eine gültige Produkt-id."""
    assert plan._id({"produkt_id": True}, ("produkt_id",)) is None


def test_zweites_produkt_fuer_denselben_begriff_faellt_weg(con):
    erste, zweite = [t["id"] for t in _vorgelegt(con, "Milch")][:2]
    agent, _ = _chat(con, _extract(("Milch", 1)),
                     _choose(("Milch", erste, 1), ("Milch", zweite, 1)))
    ergebnis = agent.turn(con, "Milch")
    assert len(ergebnis.vorschlaege) == 1
    assert ergebnis.verworfen[0]["grund"].startswith("zweites Produkt")


def test_ohne_kandidaten_wird_stufe_drei_gar_nicht_gefragt(con):
    """Ein Modell, dem nichts vorgelegt wird, kann nur erfinden."""
    llm = FakeLLM(_extract(("Zahnstocher", 1)))
    agent = chatmodul.Chat(llm, wecker=Box())
    agent.turn(con, "Zahnstocher")
    assert len(llm.aufrufe) == 1


def test_guided_json_geht_als_extra_body_mit(con):
    """Guided Decoding erzwingt die FORM. Die Prüfung bleibt trotzdem im Code."""
    agent, llm = _chat(con, _extract(("Milch", 1)), _choose())
    agent.turn(con, "Milch")
    assert llm.aufrufe[0]["extra_body"]["guided_json"] == plan.SCHEMA_EXTRACT
    assert llm.aufrufe[1]["extra_body"]["guided_json"] == plan.SCHEMA_CHOOSE


def test_ohne_guided_geht_kein_guided_json_mit(con):
    """Ein anderer Server kennt `guided_json` womöglich nicht (Spec 8.3)."""
    agent, llm = _chat(con, _extract(("Milch", 1)), _choose(), guided=False)
    agent.turn(con, "Milch")
    assert "guided_json" not in llm.aufrufe[0].get("extra_body", {})


def test_denken_ist_aus_und_zwar_je_anfrage(con):
    """Denk-Token zählen gegen `max_tokens` und schneiden das JSON ab.

    Gemessen am 2026-08-28 gegen die echte Box: mit Denken kam
    `'{"begriffe": [{"begriff": "Spaghetti", "menge": 1'` zurück — gültiges
    Format, halbe Antwort. Abgeschaltet wird es je ANFRAGE; der Server bleibt
    unverändert, alles andere auf der Box denkt weiter.
    """
    agent, llm = _chat(con, _extract(("Milch", 1)), _choose())
    agent.turn(con, "Milch")
    for aufruf in llm.aufrufe:
        assert aufruf["extra_body"]["chat_template_kwargs"] == {
            "enable_thinking": False}


def test_denken_laesst_sich_wieder_einschalten(con):
    agent, llm = _chat(con, _extract(("Milch", 1)), _choose(), denken=True)
    agent.turn(con, "Milch")
    assert "chat_template_kwargs" not in llm.aufrufe[0]["extra_body"]


def test_abgeschnittene_antwort_wird_als_solche_gemeldet(con):
    """`finish_reason=length` ist kein Formatfehler, sondern ein Budgetfehler."""
    class Abgeschnitten:
        def modell(self, **_):
            return "fake"

        def chat(self, nachrichten, **weitere):
            return Antwort(content='{"begriffe": [{"begriff": "Spa',
                           reasoning_content="ich denke nach …",
                           modell="fake", finish_reason="length")

    with pytest.raises(plan.PlanFehler) as e:
        plan.extract(Abgeschnitten(), "Spaghetti")
    assert "abgeschnitten" in str(e.value)


# --------------------------------------------------------------------------
# Der Haken für WB-328: die Span-ID am Zug

def test_span_id_landet_an_beiden_zeilen(con):
    """Beide Zeilen des Zuges tragen denselben Span (Spec 7.1)."""
    agent, _ = _chat(con, _extract(("Landmilch", 1)), _choose())
    ergebnis = agent.turn(con, "Landmilch", span_id="abc123")
    spans = [m["span_id"] for m in vorschlaege.verlauf(con, ergebnis.order_id)]
    assert spans == ["abc123", "abc123"]


def test_span_id_laesst_sich_nachtragen(con):
    """Der Span endet erst, wenn der Zug fertig ist — also wird er nachgetragen."""
    agent, _ = _chat(con, _extract(("Landmilch", 1)), _choose())
    ergebnis = agent.turn(con, "Landmilch")
    vorschlaege.span_setzen(con, ergebnis.chat_message_id, "spaeter")
    assert con.execute("SELECT span_id FROM chat_message WHERE id = ?",
                       (ergebnis.chat_message_id,)).fetchone()["span_id"] == "spaeter"


# --------------------------------------------------------------------------
# Mehrere Suchbegriffe je Zutat, vereinigt statt „erster gewinnt" (WB-340)

#: Der gemessene Fall aus WB-340, in klein: „Auberginen" findet NUR das
#: Fertiggericht (der Plural steckt in dessen Namen), „Aubergine" findet die
#: echte Aubergine — und über die Präfixsuche auch das Fertiggericht wieder.
#: Wer nach dem ersten Begriff aufhört, der etwas findet, legt Stufe 3 genau
#: ein Produkt vor: das Fertiggericht.
AUBERGINEN = [
    ("aub1", "Gemüse-Auberginen-Masala mit Jasminreis", "Fertiggerichte",
     "Indisch", "Masala"),
    ("aub2", "Aubergine, 1 Stk.", "Obst & Gemüse", "Gemüse", "Fruchtgemüse"),
    ("aub3", "BIO Aubergine, 1 Stk.", "Obst & Gemüse", "Gemüse",
     "Fruchtgemüse"),
]


def _auberginen(con):
    for external_id, name, l1, l2, l3 in AUBERGINEN:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, 249, '1 Stk', ?, ?, ?)",
            (external_id, name, l1, l2, l3))
    con.commit()
    return {name: _pid(con, name) for _, name, *_ in AUBERGINEN}


def _vorgelegte_namen(llm):
    """Die Kandidaten, die Stufe 3 tatsächlich zu sehen bekam."""
    prompt = llm.aufrufe[1]["nachrichten"][-1]["content"]
    vorgelegt = json.loads(prompt[prompt.index("["):])
    return [[k["name"] for k in a["kandidaten"]] for a in vorgelegt]


def test_alle_begriffe_der_kette_werden_gesucht(con):
    """Nicht nur der erste — auch dann nicht, wenn er schon etwas findet."""
    ids = _auberginen(con)
    agent, llm = _chat(con, _extract((("Auberginen", "Aubergine"), 1)),
                       _choose())
    agent.turn(con, "Auberginen")

    namen = _vorgelegte_namen(llm)[0]
    assert "Gemüse-Auberginen-Masala mit Jasminreis" in namen
    assert "Aubergine, 1 Stk." in namen
    assert len(namen) == len(ids)


def test_der_zweite_begriff_bringt_das_produkt_das_gemeint_ist(con):
    """**Der Kern des Tickets** — der Aubergine-Fall.

    „Auberginen" findet zuerst ein Fertiggericht, und zwar mit dem HÖHEREN
    bm25-Rang. Wer dort aufhört, zurrt es fest. Die Vereinigung legt beides
    vor, und Stufe 3 kann die echte Aubergine wählen — die ohne den zweiten
    Begriff nie zur Wahl gestanden hätte.
    """
    ids = _auberginen(con)
    agent, llm = _chat(
        con, _extract((("Auberginen", "Aubergine"), 1)),
        _choose(("Auberginen", ids["Aubergine, 1 Stk."], 1)))
    ergebnis = agent.turn(con, "Auberginen")

    # Nur der zweite Begriff findet sie …
    from zettel.catalog import search
    assert [t["name"] for t in search.search(con, "Auberginen")] == [
        "Gemüse-Auberginen-Masala mit Jasminreis"]
    # … und trotzdem steht sie in der Vorschlagsliste.
    zeile = ergebnis.vorschlaege[0]
    assert zeile["product_id"] == ids["Aubergine, 1 Stk."]
    assert not zeile["ist_freitext"]


def test_search_term_nennt_den_begriff_der_den_treffer_brachte(con):
    """Die Erklärung an der Eval-Annotation (WB-329) darf nicht raten.

    Gewählt wurde ein Produkt, das der ZWEITE Begriff gebracht hat. An der
    Zeile steht deshalb „Aubergine" und nicht die Zutat „Auberginen".
    """
    ids = _auberginen(con)
    agent, _ = _chat(con, _extract((("Auberginen", "Aubergine"), 1)),
                     _choose(("Auberginen", ids["Aubergine, 1 Stk."], 1)))
    ergebnis = agent.turn(con, "Auberginen")

    assert ergebnis.vorschlaege[0]["search_term"] == "Aubergine"
    assert con.execute("SELECT search_term FROM chat_suggestion"
                       ).fetchone()["search_term"] == "Aubergine"


def test_die_kandidaten_sind_nach_produkt_id_entdoppelt(con):
    """Beide Begriffe finden dasselbe Fertiggericht. Es steht einmal da.

    Und zwar mit der Herkunft des GENAUESTEN Begriffs, der es gefunden hat —
    er beschreibt die Zutat besser als der allgemeinere danach.
    """
    _auberginen(con)
    agent, llm = _chat(con, _extract((("Aubergine", "Auberginen"), 1)),
                       _choose())
    agent.turn(con, "Auberginen")

    namen = _vorgelegte_namen(llm)[0]
    assert len(namen) == len(set(namen)) == 3


def test_ohne_treffer_in_der_ganzen_kette_bleibt_freitext(con):
    """Eine Katalog-Lücke verschwindet nicht dadurch, dass man anders sucht.

    „Sellerie" bleibt auch mit drei Begriffen ohne Treffer — dann wird die
    Zutat wie bisher zum Freitext-Vorschlag, unter ihrem genauesten Begriff.
    """
    agent, llm = _chat(
        con, _extract((("Staudensellerie", "Sellerie", "Knollensellerie"), 2)))
    ergebnis = agent.turn(con, "Staudensellerie")

    zeile = ergebnis.vorschlaege[0]
    assert zeile["ist_freitext"] and zeile["free_text"] == "Staudensellerie"
    assert zeile["search_term"] == "Staudensellerie" and zeile["qty"] == 2
    # Ohne einen einzigen Kandidaten wird Stufe 3 gar nicht erst gefragt.
    assert len(llm.aufrufe) == 1


def test_die_obergrenze_je_zutat_greift(con):
    """Drei Begriffe à fünf Treffer wären 15 Kandidaten für EINE Zutat.

    Bei acht Zutaten sprengt das den Prompt von Stufe 3. Gekürzt wird am
    allgemeinen Ende der Kette: der genaueste Begriff behält seine Treffer,
    der letzte bekommt, was übrig ist — und der letzte ist der, bei dem das
    Modell entgleist.
    """
    _auberginen(con)
    agent, llm = _chat(con, _extract((("Aubergine", "Milch"), 1)), _choose(),
                       kandidaten=5, obergrenze=4)
    agent.turn(con, "Auberginen und Milch")

    namen = _vorgelegte_namen(llm)[0]
    assert len(namen) == 4
    # Die drei Auberginen zuerst, dann eine Milch — nicht umgekehrt.
    assert sum(1 for n in namen[:3] if "ubergine" in n) == 3


def test_die_obergrenze_nimmt_den_ersten_beiden_begriffen_nichts_weg(con):
    """Die Vorgabe ist so gewählt, dass die Treffer der beiden genauesten
    Begriffe immer vollständig hineinpassen: die Obergrenze kürzt nur den
    Zugewinn, und zwar am allgemeinen Ende."""
    assert plan.MAX_KANDIDATEN_MODELL >= plan.KANDIDATEN_MODELL * 2


# --------------------------------------------------------------------------
# Was `plan.extract` aus der Antwort des Modells macht (WB-340)

def test_extract_liefert_die_kette_in_der_reihenfolge_des_modells():
    llm = FakeLLM(json.dumps({"begriffe": [
        {"suchbegriffe": ["Knoblauchzehen", "Knoblauch"], "menge": 1}]}))
    assert plan.extract(llm, "Knoblauch")[0]["suchbegriffe"] == [
        "Knoblauchzehen", "Knoblauch"]


def test_extract_nimmt_auch_einen_einzelnen_begriff_an():
    """Nachsichtig gegenüber der Verpackung: ein Begriff ist eine Kette der
    Länge eins und kein Fehlerfall."""
    llm = FakeLLM(json.dumps({"begriffe": [{"begriff": "Milch", "menge": 1}]}))
    assert plan.extract(llm, "Milch") == [
        {"suchbegriffe": ["Milch"], "menge": 1}]


def test_extract_entdoppelt_die_kette_und_deckelt_sie():
    """Derselbe Begriff zweimal wäre dieselbe Abfrage zweimal — und in der
    Vereinigung keine einzige zusätzliche Zeile."""
    llm = FakeLLM(json.dumps({"begriffe": [
        {"suchbegriffe": ["Möhren", "möhren", "Karotten", "Wurzeln",
                          "Rüben", "Gelbe Rüben"], "menge": 1}]}))
    kette = plan.extract(llm, "Möhren")[0]["suchbegriffe"]
    assert kette[:3] == ["Möhren", "Karotten", "Wurzeln"]
    assert len(kette) == plan.MAX_KETTE


def test_extract_wirft_leere_ketten_weg():
    llm = FakeLLM(json.dumps({"begriffe": [
        {"suchbegriffe": [], "menge": 1},
        {"suchbegriffe": ["  ", None, "Milch"], "menge": 1}]}))
    assert plan.extract(llm, "Milch") == [
        {"suchbegriffe": ["Milch"], "menge": 1}]


def test_das_guided_schema_verlangt_die_kette(con):
    schema = plan.SCHEMA_EXTRACT["properties"]["begriffe"]["items"]
    assert schema["required"] == ["suchbegriffe", "menge"]
    assert schema["properties"]["suchbegriffe"]["type"] == "array"
    assert schema["properties"]["suchbegriffe"]["maxItems"] == plan.MAX_KETTE


def test_extract_wirft_bruchstuecke_weg():
    """Ein zweibuchstabiger „Begriff" ist keiner.

    Die Suche sucht über Wortanfänge: „Ka" fände einen guten Teil des
    Katalogs, und in einer Vereinigung wäre nicht mehr zu erkennen, woher der
    Unsinn kam.
    """
    llm = FakeLLM(json.dumps({"begriffe": [
        {"suchbegriffe": ["Karotten", "Ka"], "menge": 1}]}))
    assert plan.extract(llm, "Karotten")[0]["suchbegriffe"] == ["Karotten"]


def test_die_kette_bleibt_kurz(con):
    """Der letzte Begriff einer langen Kette entgleist — gemessen.

    „Körnig", „Papikra", „Konzenzrat": Begriffe, die irgendetwas finden und
    die Vereinigung vergiften. Drei ist die Grenze, und die Reihenfolge sorgt
    dafür, dass der letzte hinten steht und zuerst wegfällt.
    """
    assert plan.MAX_KETTE == 3
    assert plan.SCHEMA_EXTRACT["properties"]["begriffe"]["items"][
        "properties"]["suchbegriffe"]["maxItems"] == 3


# --------------------------------------------------------------------------
# Die Kandidaten werden aufgehoben, „Nein" zeigt sie, ein Tipp übernimmt eine
# davon (WB-359)
#
# Der Kern dieses Abschnitts ist eine Zusicherung, keine Bequemlichkeit:
# gezeigt wird, was Stufe 3 vorlag — **ohne ein zweites Mal zu suchen**. Eine
# zweite Suche liefe gegen einen inzwischen veränderten Katalog und zeigte im
# Zweifel etwas anderes, als das Modell vor sich hatte.

def _butter_zug(con, gewaehlt: str = "Isigny Butter AOP"):
    """Ein Zug über die EINBEGRIFFIGE Zutat „Butter" — der schwierige Fall.

    Das Modell greift zur teuren Spezialbutter; die Weihenstephan lag mit in
    der Vorlage. Genau der Fall aus OBSERVABILITY.md, nur klein.
    """
    pid = _pid(con, gewaehlt)
    agent, llm = _chat(con, _extract(("Butter", 1)),
                       _choose(("Butter", pid, 1)))
    return agent.turn(con, "Butter"), pid


def test_die_kandidaten_ueberleben_den_chat_zug(con):
    """Sie liegen nach dem Zug in `chat_kandidat` und sind abrufbar."""
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]

    gemerkt = con.execute(
        "SELECT product_id, pos, search_term FROM chat_kandidat"
        " WHERE suggestion_id = ? ORDER BY pos", (sid,)).fetchall()
    assert gemerkt, "Die Kandidaten wurden weggeworfen statt aufgehoben."
    # Genau das, was die Suche mit der ANZEIGEGRENZE vorlegt — und in ihrer
    # Reihenfolge (Kette, dann Wortstufe/Rang), nicht nach roher id.
    erwartet = search.suche_kette(con, ["Butter"],
                                  limit=plan.KANDIDATEN_ANZEIGE,
                                  obergrenze=plan.MAX_KANDIDATEN_ANZEIGE)
    assert [r["product_id"] for r in gemerkt] == [p["id"] for p in erwartet]
    assert [r["search_term"] for r in gemerkt] == ["Butter"] * len(erwartet)


def test_nein_zeigt_die_aufgehobenen_kandidaten_ohne_neue_suche(con,
                                                                monkeypatch):
    """Keine zweite Suche — die Liste kommt aus der Datenbank.

    Die Suche wird nach dem Zug scharf geschaltet: ruft sie noch jemand,
    fliegt der Test. Das ist der einzige Weg, „keine neue Suche" zu prüfen,
    ohne die Absicht zu glauben.
    """
    ergebnis, gewaehlt = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]

    def verboten(*a, **k):
        raise AssertionError("Es wurde ein zweites Mal gesucht.")

    monkeypatch.setattr(search, "search", verboten)
    monkeypatch.setattr(search, "suche_kette", verboten)

    alternativen = vorschlaege.alternativen(con, sid)
    assert alternativen, "Kein einziger Kandidat aufgehoben."
    # Der vorgeschlagene selbst steht nicht als Alternative zu sich da.
    assert gewaehlt not in [a["id"] for a in alternativen]
    # Bild, Name, Menge, Preis — was die Katalogkachel auch zeigt.
    for a in alternativen:
        assert a["name"] and "price_cents" in a and "unit_text" in a
        assert "image_path" in a


def test_butter_hat_mindestens_fuenf_alternativen(con):
    """Der Fall, der es vor WB-359 nicht tat.

    „Butter" ist einbegriffig; mit der Modellgrenze von 5 je Begriff blieben
    nach Abzug des gewählten Produkts genau vier Alternativen. Getrennte
    Grenzen machen daraus so viele, wie der Katalog hergibt.
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    assert len(vorschlaege.alternativen(con, sid)) >= 5


def test_die_modellgrenze_bleibt_klein(con):
    """Die zweite Grenze wächst, die erste nicht: Stufe 3 sieht weiter fünf.

    Das ist die Hälfte des Tickets, die man leicht mitwachsen lässt — und
    jeder Kandidat mehr kostet dort Token (gemessen in WB-340: rund 130
    Zeichen je Kandidat).
    """
    pid = _pid(con, "Isigny Butter AOP")
    agent, llm = _chat(con, _extract(("Butter", 1)),
                       _choose(("Butter", pid, 1)))
    agent.turn(con, "Butter")

    zweiter = llm.aufrufe[1]["nachrichten"][-1]["content"]
    vorgelegt = json.loads(zweiter[zweiter.index("["):])
    assert len(vorgelegt[0]["kandidaten"]) == plan.KANDIDATEN_MODELL == 5


def test_die_zwei_grenzen_sind_getrennt_und_die_anzeige_ist_groesser():
    """Zwei Zahlen, zwei Zwecke — und die Anzeigegrenze ist die grössere.

    Token gegen Datenbankzeilen: Stufe 3 bezahlt jeden Kandidaten im Prompt,
    die aufgehobene Liste bezahlt ihn mit einer Zeile in `chat_kandidat`.
    """
    assert plan.KANDIDATEN_ANZEIGE > plan.KANDIDATEN_MODELL
    assert plan.MAX_KANDIDATEN_ANZEIGE == 2 * plan.KANDIDATEN_ANZEIGE
    assert plan.MAX_KANDIDATEN_MODELL == 2 * plan.KANDIDATEN_MODELL


def test_die_modellvorlage_ist_eine_teilmenge_des_aufgehobenen(con):
    """Sonst zeigte „Nein" nicht die Liste, aus der gewählt wurde."""
    pid = _pid(con, "Isigny Butter AOP")
    agent, llm = _chat(con, _extract((("Buttermilch", "Butter"), 1)),
                       _choose(("Buttermilch", pid, 1)))
    ergebnis = agent.turn(con, "Butter")

    zweiter = llm.aufrufe[1]["nachrichten"][-1]["content"]
    vorgelegt = json.loads(zweiter[zweiter.index("["):])
    modell = {k["id"] for k in vorgelegt[0]["kandidaten"]}
    sid = ergebnis.vorschlaege[0]["id"]
    aufgehoben = {r["product_id"] for r in con.execute(
        "SELECT product_id FROM chat_kandidat WHERE suggestion_id = ?",
        (sid,))}
    assert modell <= aufgehoben


def test_die_wahl_einer_alternative_legt_sie_ein_und_den_vorschlag_nicht(con):
    ergebnis, gewaehlt = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    weihenstephan = _pid(con, "Weihenstephan Butter")

    korrektur = vorschlaege.korrigieren(con, sid, weihenstephan)

    korb = [(z["product_id"], z["qty"]) for z in orders.inhalt(con)]
    assert korb == [(weihenstephan, 1)]
    assert gewaehlt not in [p for p, _ in korb]
    assert korrektur["behalten"] and korrektur["product_id"] == weihenstephan
    # Der ursprüngliche Vorschlag bleibt stehen — er IST das Label „so nicht".
    assert vorschlaege.eine(con, sid)["decision"] == vorschlaege.VERWORFEN


def test_die_korrektur_ist_als_bezug_erkennbar(con):
    """Nicht zwei lose Entscheidungen, sondern eine Korrektur.

    Ohne den Verweis wäre hinterher nicht zu unterscheiden, ob sie den
    Fehlgriff geradegezogen oder einfach etwas dazugelegt hat — und genau
    dieser Unterschied ist der Eval-Wert des Ganzen.
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    vorschlaege.korrigieren(con, sid, _pid(con, "Weihenstephan Butter"))

    zeilen = vorschlaege.liste(con, ergebnis.chat_message_id)
    neu = [z for z in zeilen if z["ist_korrektur"]]
    assert len(neu) == 1
    assert neu[0]["corrected_from"] == sid
    assert neu[0]["statt_name"] == "Isigny Butter AOP"
    # Und von der anderen Seite: die verworfene Zeile kennt ihre Korrektur.
    quelle = [z for z in zeilen if z["id"] == sid][0]
    assert quelle["korrektur"]["name"] == "Weihenstephan Butter"


def test_die_korrektur_schoent_die_trefferquote_nicht(con):
    """Sie ist kein Vorschlag des Modells, sondern die Handbewegung danach.

    Mitgezählt hübe ausgerechnet ein Fehlgriff die Quote, sobald ihn jemand
    korrigiert.
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    vorschlaege.korrigieren(con, sid, _pid(con, "Weihenstephan Butter"))

    q = vorschlaege.quote(con, ergebnis.chat_message_id)
    assert q == {"vorgeschlagen": 1, "behalten": 0, "verworfen": 1,
                 "offen": 0, "quote": 0.0}


def test_zweimal_dieselbe_korrektur_legt_nicht_zweimal_ein(con):
    """Auf dem Handy ist ein Doppeltipp schnell passiert."""
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    weihenstephan = _pid(con, "Weihenstephan Butter")

    erste = vorschlaege.korrigieren(con, sid, weihenstephan)
    zweite = vorschlaege.korrigieren(con, sid, weihenstephan)

    assert erste["id"] == zweite["id"]
    assert [(z["product_id"], z["qty"]) for z in orders.inhalt(con)] == [
        (weihenstephan, 1)]


def test_eine_zweite_andere_korrektur_wird_abgewiesen(con):
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    vorschlaege.korrigieren(con, sid, _pid(con, "Weihenstephan Butter"))

    with pytest.raises(vorschlaege.VorschlagFehler):
        vorschlaege.korrigieren(con, sid, _pid(con, "Kerrygold irische Butter"))


def test_nur_was_vorgelegt_war_kann_gewaehlt_werden(con):
    """Dieselbe Regel wie in `plan.choose` — hier für die Oberfläche.

    Eine ID von aussen ist keine Alternative aus der Vorlage; sonst hiesse die
    Korrektur später fälschlich „aus derselben Liste hätte das Modell das
    Richtige nehmen können".
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]

    with pytest.raises(vorschlaege.VorschlagFehler):
        vorschlaege.korrigieren(con, sid, _pid(con, "Toilettenpapier 10 Rollen"))
    assert orders.inhalt(con) == []


def test_nichts_davon_fuehrt_zu_einer_freitextzeile(con):
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]

    neu = vorschlaege.stattdessen_freitext(con, sid, "gute Butter vom Hof")

    assert neu["ist_freitext"] and neu["free_text"] == "gute Butter vom Hof"
    assert neu["corrected_from"] == sid and neu["behalten"]
    assert [z["free_text"] for z in orders.inhalt(con)] == [
        "gute Butter vom Hof"]


def test_nichts_davon_nimmt_ohne_text_den_suchbegriff(con):
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    assert vorschlaege.stattdessen_freitext(con, sid)["free_text"] == "Butter"


def test_ohne_alternativen_bricht_nichts(con):
    """Die echte Katalog-Lücke: die Suche hat nichts vorgelegt.

    Der Vorschlag steht dann ohnehin als Freitext da — und der Weg zum
    selbstgeschriebenen Text bleibt trotzdem offen, statt in einer leeren
    Liste zu enden.
    """
    agent, _ = _chat(con, _extract(("Staudensellerie", 1)), _choose())
    ergebnis = agent.turn(con, "Staudensellerie")
    sid = ergebnis.vorschlaege[0]["id"]

    assert vorschlaege.alternativen(con, sid) == []
    assert vorschlaege.liste(con, ergebnis.chat_message_id)[0][
        "n_alternativen"] == 0
    neu = vorschlaege.stattdessen_freitext(con, sid, "Staudensellerie")
    assert neu["free_text"] == "Staudensellerie"


def test_eine_ausgemusterte_alternative_wird_nicht_angeboten(con):
    """Was es nicht mehr gibt, soll man nicht neu wählen können.

    Anders als bei einer Zeile, die schon im Korb liegt (WB-335, dort wird sie
    markiert statt verschwiegen): hier wird GEWÄHLT, und was hier gewählt
    wird, soll es im Laden geben.
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    weg = _pid(con, "Weihenstephan Butter")
    vorher = len(vorschlaege.alternativen(con, sid))
    con.execute("UPDATE product SET active = 0 WHERE id = ?", (weg,))
    con.commit()

    ids = [a["id"] for a in vorschlaege.alternativen(con, sid)]
    assert weg not in ids and len(ids) == vorher - 1
    with pytest.raises(vorschlaege.VorschlagFehler):
        vorschlaege.korrigieren(con, sid, weg)


def test_nur_ueber_den_allgemeinen_begriff_gefunden_wird_benannt(con):
    """Der Querschnittsbefund aus WB-358.

    Findet kein genauer Begriff etwas, greift der allgemeinste — und der
    findet immer irgendetwas („OLD AMSTERDAM" -> „Bier" -> Singha Bier). Die
    Zeile sagt es, statt einen unsicheren Treffer als sicheren auszugeben.
    """
    pid = _pid(con, "Weihenstephan Butter")
    agent, _ = _chat(con, _extract((("Gouda am Stück", "Butter"), 1)),
                     _choose(("Gouda am Stück", pid, 1)))
    ergebnis = agent.turn(con, "Gouda")

    zeile = ergebnis.vorschlaege[0]
    assert zeile["fallback_term"] == "Butter"


def test_ein_treffer_ueber_den_genauen_begriff_gilt_als_sicher(con):
    """Die Gegenprobe: keine Marke, wo nichts ausgewichen wurde."""
    ergebnis, _ = _butter_zug(con)
    assert ergebnis.vorschlaege[0]["fallback_term"] is None


# --------------------------------------------------------------------------
# Abgeschnittene Antworten (WB-363)
#
# „alles für Pho" hat 20 Zutaten und brach an MAX_TOKENS ab — die Grenze stammte
# aus der Zeit vor den Begriffsketten (WB-340), als je Zutat EIN Begriff
# ausgegeben wurde. Ein Abbruch nach 18 von 20 Zutaten ist ein weiches Problem;
# alles wegzuwerfen war eine harte Reaktion darauf.

def test_gerettet_wird_was_vollstaendig_dasteht():
    kaputt = ('{"begriffe": ['
              '{"suchbegriffe": ["Reisnudeln", "Nudeln"], "menge": 1},'
              '{"suchbegriffe": ["Rinderbrühe", "Brühe"], "menge": 1},'
              '{"suchbegriffe": ["Sternan')
    gerettet = plan._vollstaendige_eintraege(kaputt)
    assert len(gerettet) == 2
    assert gerettet[0]["suchbegriffe"] == ["Reisnudeln", "Nudeln"]


def test_rettung_stolpert_nicht_ueber_klammern_im_produktnamen():
    """Ein Produktname darf geschweifte Klammern und maskierte Anführungs-
    zeichen enthalten. Deshalb wird über die Klammertiefe gelaufen und nicht
    mit einem regulären Ausdruck gesucht."""
    kaputt = r'[{"suchbegriffe": ["Käse \"alt\" {gereift}"], "menge": 1}, {"such'
    gerettet = plan._vollstaendige_eintraege(kaputt)
    assert len(gerettet) == 1
    assert gerettet[0]["suchbegriffe"] == ['Käse "alt" {gereift}']


def test_ohne_einen_ganzen_eintrag_bleibt_es_ein_fehler():
    """Wo nichts zu retten ist, wird nichts erfunden."""
    assert plan._vollstaendige_eintraege('{"begriffe": [{"suchbeg') == []


def test_das_budget_ist_an_der_gemessenen_groesse_bemessen():
    """34 Token je Zutat, gemessen am 2026-08-28. Wer das Antwortformat
    ändert, muss diese Zahl mitziehen — sie ist einmal stillschweigend zu
    klein geworden (WB-340 verdreifachte die Ausgabe, WB-363 fand es)."""
    assert plan.MAX_TOKENS >= 34 * 30, "trägt keine 30 Zutaten mehr"


def test_abgeschnitten_wird_nicht_stillschweigend_weniger(monkeypatch):
    """Der gerettete Text sagt, dass er gerettet wurde. Ohne das bekäme die
    Nutzerin eine kürzere Zutatenliste und keinen Hinweis darauf."""
    text = plan._AbgeschnittenerText('[{"a": 1}]', 1, 800)
    assert text == '[{"a": 1}]'          # bleibt ein str, Aufrufer merken nichts
    assert text.abgeschnitten is True
    assert text.gerettet == 1 and text.budget == 800


# --------------------------------------------------------------------------
# Entscheidungen zurücknehmen (WB-361)
#
# Ein Fehltipp darf nicht endgültig sein — auf dem Handy sitzen „Ja" und
# „Nein" nebeneinander. Die Falle dabei ist nicht der Knopf, sondern der
# Schutz gegen den Doppeltipp: er verglich die letzte Entscheidung, und genau
# das trägt nicht mehr, sobald es einen Rückweg gibt.

def _milchzeile(con):
    """Ein Zug über „Landmilch" — gibt die id des einen Vorschlags zurück."""
    agent, _ = _chat(con, _extract(("Landmilch", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1)))
    return agent.turn(con, "Landmilch").vorschlaege[0]["id"]


def test_ja_ruecknahme_ja_legt_nur_einmal_ein(con):
    """Die Falle des ganzen Tickets.

    Der alte Schutz gegen den Doppeltipp verglich nur die letzte Entscheidung
    (`decision == entscheidung`). Über den Umweg `offen` wäre er wirkungslos:
    zweimal `kept`, zweimal `orders.einlegen()`, Menge 2 im Korb. Der Schutz
    hängt deshalb an `eingelegt_at` — „war diese Zeile schon einmal im Korb".
    """
    sid = _milchzeile(con)
    vorschlaege.entscheiden(con, sid, "kept")
    vorschlaege.entscheiden(con, sid, "offen")
    vorschlaege.entscheiden(con, sid, "kept")

    assert [(z["product_id"], z["qty"]) for z in orders.inhalt(con)] == [
        (_pid(con, MILCH), 1)]


def test_zehnmal_hin_und_her_bleibt_eine_zeile_im_korb(con):
    """Dasselbe, nur ausdauernder — der Zähler darf nicht mitwachsen."""
    sid = _milchzeile(con)
    for _ in range(5):
        vorschlaege.entscheiden(con, sid, "kept")
        vorschlaege.entscheiden(con, sid, "offen")
    vorschlaege.entscheiden(con, sid, "kept")

    assert [z["qty"] for z in orders.inhalt(con)] == [1]


def test_ein_verworfener_vorschlag_ist_danach_wieder_entscheidbar(con):
    """„Nein" ist keine Sackgasse mehr — der Rückweg führt auf `offen`.

    Nicht auf `kept`: das wäre eine neue Behauptung statt der Rücknahme einer
    alten. Was vor dem Fehltipp galt, war „noch nicht entschieden".
    """
    sid = _milchzeile(con)
    vorschlaege.entscheiden(con, sid, "removed")

    zurueck = vorschlaege.entscheiden(con, sid, "offen")

    assert zurueck["decision"] == "offen" and zurueck["offen"]
    assert zurueck["decided_at"] is None
    assert orders.inhalt(con) == []
    # Und danach geht beides wieder.
    assert vorschlaege.entscheiden(con, sid, "kept")["behalten"]


def test_ein_zurueckgenommenes_ja_laesst_den_korb_stehen(con):
    """Bewusst so (siehe `entscheiden()`): `orders.einlegen()` fasst gleiche
    Zeilen zusammen, die Korbzeile kann also längst eine sein, die sie selbst
    aufgestockt hat. Sie hier herauszunehmen hiesse, fremde Mengen zu löschen.
    """
    sid = _milchzeile(con)
    vorschlaege.entscheiden(con, sid, "kept")
    vorher = [(z["product_id"], z["qty"]) for z in orders.inhalt(con)]

    zurueck = vorschlaege.entscheiden(con, sid, "offen")

    assert [(z["product_id"], z["qty"]) for z in orders.inhalt(con)] == vorher
    # Und die Zeile weiss es, damit die Oberfläche es sagen kann statt es zu
    # verschweigen.
    assert zurueck["im_korb"] is True and zurueck["offen"]


def test_die_ruecknahme_wird_gezaehlt(con):
    """Die einzige Spur, die ein Fehltipp hinterlässt — ein Label bekommt er
    keines (`test_labels.py`)."""
    sid = _milchzeile(con)
    assert vorschlaege.eine(con, sid)["zurueckgenommen"] == 0
    vorschlaege.entscheiden(con, sid, "kept")
    vorschlaege.entscheiden(con, sid, "offen")
    vorschlaege.entscheiden(con, sid, "removed")
    vorschlaege.entscheiden(con, sid, "offen")

    assert vorschlaege.eine(con, sid)["zurueckgenommen"] == 2


def test_eine_korrektur_laesst_sich_zuruecknehmen(con):
    """Sonst wäre eine falsche Korrektur schlimmer als der Fehlgriff selbst:
    sie behauptet zusätzlich, was richtig gewesen wäre."""
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    weihenstephan = _pid(con, "Weihenstephan Butter")
    neu = vorschlaege.korrigieren(con, sid, weihenstephan)

    zurueck = vorschlaege.entscheiden(con, neu["id"], "offen")

    assert zurueck["offen"] and zurueck["ist_korrektur"]
    # Der Korb bleibt auch hier unangetastet — dieselbe Begründung.
    assert [z["product_id"] for z in orders.inhalt(con)] == [weihenstephan]
    # Und die Zeile ist wieder frei: die Alternativen dürfen erneut aufgehen.
    assert vorschlaege.korrektur(con, sid) is None
    assert vorschlaege.eine(con, sid)["decision"] == vorschlaege.VERWORFEN


def test_dieselbe_korrektur_nach_der_ruecknahme_legt_nicht_zweimal_ein(con):
    """Die Falle von oben, eine Ebene höher: „Das", rückgängig, „Das".

    Wiederverwendet wird die vorhandene Korrekturzeile; eine zweite auf
    dasselbe Produkt wäre zweimal dasselbe „das wäre richtig gewesen" — und
    zweimal im Korb.
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    weihenstephan = _pid(con, "Weihenstephan Butter")
    erste = vorschlaege.korrigieren(con, sid, weihenstephan)
    vorschlaege.entscheiden(con, erste["id"], "offen")

    zweite = vorschlaege.korrigieren(con, sid, weihenstephan)

    assert zweite["id"] == erste["id"] and zweite["behalten"]
    assert [(z["product_id"], z["qty"]) for z in orders.inhalt(con)] == [
        (weihenstephan, 1)]
    assert len([z for z in vorschlaege.liste(con, ergebnis.chat_message_id)
                if z["ist_korrektur"]]) == 1


def test_nach_der_ruecknahme_darf_eine_andere_alternative_gewaehlt_werden(con):
    """Vorher stand hier eine Absage („wurde schon korrigiert"). Genau dafür
    ist der Rückweg da — die zurückgenommene Korrektur blockiert nicht mehr.
    """
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    erste = vorschlaege.korrigieren(con, sid, _pid(con, "Weihenstephan Butter"))
    vorschlaege.entscheiden(con, erste["id"], "offen")

    andere = vorschlaege.korrigieren(con, sid,
                                     _pid(con, "Kerrygold irische Butter"))

    assert andere["id"] != erste["id"] and andere["behalten"]
    assert vorschlaege.korrektur(con, sid)["id"] == andere["id"]
    # Der Korb behält beide Zeilen — er wird beim Zurücknehmen nicht
    # angerührt, und das ist die bewusste Entscheidung, keine Nachlässigkeit.
    assert len(orders.inhalt(con)) == 2


def test_eine_stehende_korrektur_blockiert_weiterhin(con):
    """Die Zusicherung aus WB-359 bleibt: solange die Korrektur gilt, wird
    keine zweite danebengelegt."""
    ergebnis, _ = _butter_zug(con)
    sid = ergebnis.vorschlaege[0]["id"]
    vorschlaege.korrigieren(con, sid, _pid(con, "Weihenstephan Butter"))

    with pytest.raises(vorschlaege.VorschlagFehler):
        vorschlaege.korrigieren(con, sid, _pid(con, "Kerrygold irische Butter"))


# --------------------------------------------------------------------------
# „Doch nicht alles" — der Rückweg für den Sammelknopf (WB-397)
#
# WB-361 hat jeder einzelnen Zeile einen Rückweg gegeben; der Sammelknopf war
# danach die einzige Entscheidung im Shop ohne einen. Die Falle liegt nicht im
# Knopf, sondern in der Auswahl: der Sammelknopf rührt AUSDRÜCKLICH nur die
# offenen Zeilen an, damit ein Tipp nicht die Labels umkippt, die die
# Nutzerin einzeln gesetzt hat — und rückwärts muss dasselbe gelten. Ein
# Rückweg, der die einzeln gesetzte Butter mitnähme, wäre derselbe Fehler in
# die andere Richtung.

def _vier_zeilen(con):
    """Ein Zug mit vier Vorschlägen: zwei Produkte, zwei Freitexte.

    Vier, weil der Kerntest zwei einzeln entschiedene Zeilen NEBEN den
    gesammelten braucht — mit zweien liesse sich „nur die des Vorgangs" nicht
    von „alle" unterscheiden.
    """
    agent, _ = _chat(con, _extract(("Landmilch", 1), ("Butter", 1),
                                   ("Zahnstocher", 1), ("Alufolie", 1)),
                     _choose(("Landmilch", _pid(con, MILCH), 1),
                             ("Butter", _pid(con, "Weihenstephan Butter"), 1)))
    ergebnis = agent.turn(con, "Landmilch, Butter, Zahnstocher, Alufolie")
    return ergebnis.chat_message_id, [v["id"] for v in ergebnis.vorschlaege]


def test_der_sammelrueckweg_laesst_einzeln_entschiedenes_stehen(con):
    """**Der Kerntest des Tickets.**

    Erst zwei Sachen einzeln, dann alles auf einmal, dann zurück zu den
    zweien — genau die Geste, die im Video zu sehen sein soll.
    """
    mid, sids = _vier_zeilen(con)
    vorschlaege.entscheiden(con, sids[0], "kept")
    vorschlaege.entscheiden(con, sids[1], "kept")

    vorschlaege.alle_entscheiden(con, mid, "kept")
    assert [v["decision"] for v in vorschlaege.liste(con, mid)] == ["kept"] * 4

    danach = vorschlaege.alle_entscheiden(con, mid, "offen")

    assert [v["decision"] for v in danach] == ["kept", "kept", "offen", "offen"]


def test_der_sammelrueckweg_gilt_auch_fuer_alles_verwerfen(con):
    """Beide Richtungen, dieselbe Geste — sonst wäre „Alles verwerfen" die
    neue Sackgasse."""
    mid, sids = _vier_zeilen(con)
    vorschlaege.entscheiden(con, sids[0], "removed")
    vorschlaege.entscheiden(con, sids[1], "kept")

    vorschlaege.alle_entscheiden(con, mid, "removed")
    danach = vorschlaege.alle_entscheiden(con, mid, "offen")

    assert [v["decision"] for v in danach] == ["removed", "kept",
                                               "offen", "offen"]


def test_sammeln_zuruecknehmen_sammeln_legt_jede_sache_genau_einmal_ein(con):
    """Der Schutz aus WB-361 muss auf dem Sammelweg genauso tragen.

    Er hängt an `eingelegt_at` und nicht an der letzten Entscheidung; der
    Rückweg führt deshalb Zeile für Zeile durch `entscheiden()` und nicht über
    ein `UPDATE … WHERE sammel_nr = ?`, das die Spalte überginge.
    """
    mid, _ = _vier_zeilen(con)
    vorschlaege.alle_entscheiden(con, mid, "kept")
    vorher = [(z["product_id"], z["free_text"], z["qty"])
              for z in orders.inhalt(con)]
    assert len(vorher) == 4

    vorschlaege.alle_entscheiden(con, mid, "offen")
    vorschlaege.alle_entscheiden(con, mid, "kept")

    assert [(z["product_id"], z["free_text"], z["qty"])
            for z in orders.inhalt(con)] == vorher


def test_der_sammelrueckweg_laesst_den_korb_stehen(con):
    """Wie überall seit WB-361: `orders.einlegen()` fasst gleiche Zeilen
    zusammen, die Korbzeile kann also längst eine sein, die sie selbst
    aufgestockt hat."""
    mid, _ = _vier_zeilen(con)
    vorschlaege.alle_entscheiden(con, mid, "kept")
    vorher = [(z["product_id"], z["qty"]) for z in orders.inhalt(con)]

    danach = vorschlaege.alle_entscheiden(con, mid, "offen")

    assert [(z["product_id"], z["qty"]) for z in orders.inhalt(con)] == vorher
    # Und die Zeilen wissen es, damit die Oberfläche es sagen kann.
    assert all(v["offen"] and v["im_korb"] for v in danach)


def test_eine_einzeln_bestaetigte_zeile_verlaesst_den_sammelvorgang(con):
    """Wer eine Zeile einzeln antippt, hat sie entschieden — auch wenn der
    Tipp am Zustand nichts ändert.

    Sonst nähme „Doch nicht alles" eine Entscheidung mit zurück, die sie
    danach ausdrücklich selbst bestätigt hat.
    """
    mid, sids = _vier_zeilen(con)
    vorschlaege.alle_entscheiden(con, mid, "kept")
    vorschlaege.entscheiden(con, sids[0], "kept")      # ändert nichts — fast

    danach = vorschlaege.alle_entscheiden(con, mid, "offen")

    assert [v["decision"] for v in danach] == ["kept", "offen", "offen",
                                               "offen"]


def test_ohne_sammelvorgang_nimmt_der_rueckweg_nichts_zurueck(con):
    """Ein Doppeltipp auf dem Handy darf keine Fehlermeldung ergeben — und
    schon gar nicht die einzeln gesetzten Labels umkippen."""
    mid, sids = _vier_zeilen(con)
    vorschlaege.entscheiden(con, sids[0], "kept")
    vorschlaege.entscheiden(con, sids[1], "removed")

    for _ in range(3):
        danach = vorschlaege.alle_entscheiden(con, mid, "offen")

    assert [v["decision"] for v in danach] == ["kept", "removed", "offen",
                                               "offen"]
    assert [v["zurueckgenommen"] for v in danach] == [0, 0, 0, 0]


def test_zwei_sammelvorgaenge_gehen_einzeln_zurueck(con):
    """Vorgang für Vorgang rückwärts, so wie es kam — und nicht alles auf
    einmal. Jeder Tipp nimmt genau einen Sammeltipp zurück."""
    mid, sids = _vier_zeilen(con)
    vorschlaege.alle_entscheiden(con, mid, "kept")        # alle vier
    vorschlaege.entscheiden(con, sids[3], "offen")        # eine einzeln zurück
    vorschlaege.alle_entscheiden(con, mid, "removed")     # nur noch die eine

    erste = vorschlaege.alle_entscheiden(con, mid, "offen")
    assert [v["decision"] for v in erste] == ["kept", "kept", "kept", "offen"]

    zweite = vorschlaege.alle_entscheiden(con, mid, "offen")
    assert [v["decision"] for v in zweite] == ["offen"] * 4


def test_der_sammelknopf_ueberschreibt_weiterhin_nichts(con):
    """Die Zusicherung aus der Zeit vor dem Rückweg — sie ist die Hälfte, an
    der der Rückweg hängt."""
    mid, sids = _vier_zeilen(con)
    vorschlaege.entscheiden(con, sids[0], "removed")

    danach = vorschlaege.alle_entscheiden(con, mid, "kept")

    assert [v["decision"] for v in danach] == ["removed", "kept", "kept",
                                               "kept"]


def test_eine_korrektur_nach_dem_sammeltipp_bleibt_stehen(con):
    """Wer eine gesammelt verworfene Zeile korrigiert, hat sie entschieden.

    Der Fehlgriff geht dabei durch `entscheiden()` auf `removed` — dieselbe
    Entscheidung, die er schon trug, und trotzdem verlässt er den
    Sammelvorgang. Sonst zöge „Doch nicht alles" ihn auf `offen`, während
    daneben ein „stattdessen X" im Korb liegt: ein Zustand, den niemand
    gemeint hat.
    """
    ergebnis, _ = _butter_zug(con)
    mid = ergebnis.chat_message_id
    sid = ergebnis.vorschlaege[0]["id"]
    vorschlaege.alle_entscheiden(con, mid, "removed")
    neu = vorschlaege.korrigieren(con, sid, _pid(con, "Weihenstephan Butter"))

    vorschlaege.alle_entscheiden(con, mid, "offen")

    assert vorschlaege.eine(con, sid)["decision"] == "removed"
    assert vorschlaege.eine(con, neu["id"])["behalten"]
    assert vorschlaege.korrektur(con, sid)["id"] == neu["id"]


def test_eine_unbekannte_sammelentscheidung_wird_abgelehnt(con):
    """Auch wenn nichts offen ist — vorher lief ein Tippfehler stumm ins
    Leere."""
    mid, _ = _vier_zeilen(con)
    vorschlaege.alle_entscheiden(con, mid, "kept")

    with pytest.raises(vorschlaege.VorschlagFehler):
        vorschlaege.alle_entscheiden(con, mid, "vielleicht")
