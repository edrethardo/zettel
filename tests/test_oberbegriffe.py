"""Tests für die Auffächerung von Oberbegriffen (WB-368).

**Kein Test geht ins Netz und keiner weckt die Box.** Das Modell ist ein Fake
mit fester Antwort; der Katalog ist ein kleiner Aufschnitt-Regal-Nachbau, in
dem die Sorten so ungleich gross sind wie im echten.

Die beiden Zusicherungen, um die es geht, stehen hier als eigene Tests:

* **Die Sorten kommen aus dem Katalog.** „Aufschnitt" fächert ohne einen
  einzigen Modellaufruf auf, und eine Sorte, deren Produkte ausgemustert
  sind, wird nicht angeboten — sie kann in einem `GROUP BY` gar nicht
  auftauchen.
* **Das Modell kann keine Kategorie erfinden.** Nennt Stufe 1 eine, die ihr
  nicht vorgelegt wurde, wird sie verworfen und nicht auf die ähnlichste
  gebogen — dieselbe Regel wie bei den Produkt-IDs in `plan.choose`.
"""
import json

import pytest

from picknick import orders
from picknick.assistant import chat as chatmodul
from picknick.assistant import oberbegriffe, plan, vorschlaege
from picknick.llm import wake
from picknick.llm.client import Antwort


class FakeLLM:
    """Ein Modell mit fest vorgegebenen Antworten, das seine Aufrufe merkt."""

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
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class NieGefragt:
    """Ein Modell, das jeden Aufruf als Testfehler meldet."""

    def modell(self, **_):
        raise AssertionError("Das Modell wurde nach dem Kürzel gefragt.")

    def chat(self, *_, **__):
        raise AssertionError(
            "Das Modell wurde gefragt, obwohl der Katalog schon antwortet.")


class Box:
    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="fake")


# --------------------------------------------------------------------------
# Der Katalog: ein Aufschnittregal mit ungleich grossen Sorten
#
# `aktiv=0` bei der Sülze ist der Fall, um den es in Punkt 2 des Tickets geht:
# das Produkt steht noch in der Tabelle (Spec 5.3 löscht nicht), aber die
# Sorte darf nicht mehr angeboten werden.

KATALOG = [
    # (external_id, name, l1, l2, aktiv)
    ("sal1", "Levoni Salami Milano", "Aufschnitt", "Salami", 1),
    ("sal2", "Simonini Salami Napoli", "Aufschnitt", "Salami", 1),
    ("sal3", "Ferdi Fuchs Mini Salami", "Aufschnitt", "Salami", 1),
    ("koc1", "Gutfried Kochschinken", "Aufschnitt", "Kochschinken", 1),
    ("koc2", "Rügenwalder Kochschinken zart", "Aufschnitt", "Kochschinken", 1),
    ("bru1", "Jagdwurst Aufschnitt", "Aufschnitt", "Brühwurst", 1),
    ("gef1", "Hähnchenbrust Aufschnitt", "Aufschnitt", "Geflügelwurst", 1),
    ("sue1", "Sülze mit Gurken", "Aufschnitt", "Sülze & Wurst in Aspik", 0),
    # Eine zweite Warengruppe, die anders heisst, als man sie tippt: „Nudeln"
    # ist KEINE Kategorie, das steckt hier drunter (wie im echten Katalog).
    ("pas1", "Spaghetti No. 5", "Reis, Pasta & Getreide", "Pasta", 1),
    ("rei1", "Basmatireis", "Reis, Pasta & Getreide", "Reis", 1),
    ("cou1", "Couscous mittel", "Reis, Pasta & Getreide", "Couscous", 1),
    ("meh1", "Weizenmehl Type 405", "Reis, Pasta & Getreide", "Mehl", 1),
    # Und eine Ware, die keine Warengruppe ist: sie muss laufen wie bisher.
    ("tom1", "Mutti Tomatenmark", "Konserven & Eingelegtes", "Tomaten", 1),
    ("tom2", "Oro di Parma Tomatenmark", "Konserven & Eingelegtes", "Tomaten",
     1),
]


def _katalog(con):
    for external_id, name, l1, l2, aktiv in KATALOG:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2, category_l3, active)"
            " VALUES ('knuspr', ?, ?, 199, '100 g', ?, ?, NULL, ?)",
            (external_id, name, l1, l2, aktiv))
    con.commit()


@pytest.fixture
def con(vorlagen):
    c = vorlagen.con("oberbegriffe_katalog", _katalog)
    yield c
    c.close()


def _extract(*paare, kategorie=None, gericht=None):
    """Eine Stufe-1-Antwort, wahlweise mit Kategorie (WB-368)."""
    return json.dumps(
        {"gericht": gericht, "kategorie": kategorie,
         "begriffe": [{"suchbegriffe": [b], "menge": m} for b, m in paare]},
        ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _chat(*antworten, **weitere):
    llm = FakeLLM(*antworten)
    return chatmodul.Chat(llm, wecker=Box(), **weitere), llm


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _faechere(con):
    """Der Zug, der „Aufschnitt" auffächert — die Vorbedingung vieler Tests."""
    agent = chatmodul.Chat(NieGefragt(), wecker=Box())
    return agent.turn(con, "Aufschnitt")


# --------------------------------------------------------------------------
# Die Sorten kommen aus dem Katalog

def test_aufschnitt_faechert_auf_und_bietet_die_sorten_an(con):
    """Sechs zufällige Produkte waren die alte Antwort. Jetzt: die Sorten."""
    ergebnis = _faechere(con)

    assert ergebnis.weg == chatmodul.WEG_FAECHER
    assert ergebnis.kategorie == "Aufschnitt"
    assert [s["name"] for s in ergebnis.sorten] == [
        "Salami", "Kochschinken", "Brühwurst", "Geflügelwurst"]
    # Die echten Stückzahlen, nicht geschätzte.
    assert [s["anzahl"] for s in ergebnis.sorten] == [3, 2, 1, 1]
    # Kein Produkt wurde vorgeschlagen: erst die Sorte, dann das Produkt.
    assert ergebnis.vorschlaege == []


def test_die_auffaecherung_kostet_keinen_modellaufruf(con):
    """Das getippte Wort IST eine Kategorie — dafür braucht es kein Modell.

    `NieGefragt` würde jeden Aufruf als Testfehler melden. Dass der Zug
    durchläuft, IST die Prüfung: der Weg funktioniert auch bei schlafender
    Box, wie der Rezeptweg.
    """
    ergebnis = _faechere(con)
    assert ergebnis.sorten_herkunft == oberbegriffe.KATALOG


def test_die_meldung_nennt_die_zahlen_und_den_ausweg(con):
    ergebnis = _faechere(con)
    assert "4 Sorten" in ergebnis.meldung
    assert "7 Produkten" in ergebnis.meldung
    # Kein Oberbegriff ohne Ausweg (Punkt 5 des Tickets).
    assert "überspring" in ergebnis.meldung


def test_eine_sorte_ohne_produkte_wird_nicht_angeboten(con):
    """Die Sülze steht noch in der Tabelle, ist aber ausgemustert.

    Das ist keine Prüfung im Code, sondern die Bauart der Abfrage: gezählt
    werden aktive Produktzeilen, und wo keine steht, entsteht keine Gruppe.
    """
    ergebnis = _faechere(con)
    assert "Sülze & Wurst in Aspik" not in [s["name"] for s in ergebnis.sorten]
    # Und sie steht auch nicht in der Datenbank, aus der die Oberfläche liest.
    gemerkt = oberbegriffe.zu_nachricht(con, ergebnis.chat_message_id)
    assert "Sülze & Wurst in Aspik" not in [s["name"] for s in gemerkt["sorten"]]


def test_zu_wenige_sorten_faechern_nicht_auf(con):
    """„Konserven & Eingelegtes" hat eine Sorte — das ist keine Auswahl."""
    assert oberbegriffe.aus_katalog(con, "Konserven & Eingelegtes") is None


# --------------------------------------------------------------------------
# Kein Oberbegriff -> unverändertes Verhalten

def test_tomatenmark_faechert_nicht_auf(con):
    """Eine Ware ist keine Warengruppe. Der Zug läuft wie vor dem Ticket."""
    agent, llm = _chat(_extract(("Tomatenmark", 1)),
                       _choose(("Tomatenmark", _pid(con, "Mutti Tomatenmark"),
                                1)))
    ergebnis = agent.turn(con, "Tomatenmark")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.kategorie is None
    assert ergebnis.sorten == []
    assert [v["name"] for v in ergebnis.vorschlaege] == ["Mutti Tomatenmark"]
    # Zwei Stufen wie immer — die Auffächerung hat keine dritte hinzugefügt.
    assert len(llm.aufrufe) == 2


def test_ein_langer_satz_bekommt_die_kategorien_gar_nicht_erst_vorgelegt(con):
    """Für „alles für Spaghetti Bolognese" ist der Prompt Wort für Wort derselbe.

    Sonst zahlte jeder Kochsatz die 1.036 Zeichen Kategorienliste mit — und
    das Modell bekäme bei einem Gericht eine Frage gestellt, die dort nichts
    zu suchen hat.
    """
    agent, llm = _chat(_extract(("Spaghetti", 1)),
                       _choose(("Spaghetti", _pid(con, "Spaghetti No. 5"), 1)))
    agent.turn(con, "alles für Spaghetti Bolognese")

    system = llm.aufrufe[0]["nachrichten"][0]["content"]
    assert system == plan.SYSTEM_EXTRACT
    assert "Kategorien:" not in system


def test_ein_kurzer_satz_bekommt_die_kategorien_vorgelegt(con):
    agent, llm = _chat(_extract(("Tomatenmark", 1)),
                       _choose(("Tomatenmark", _pid(con, "Mutti Tomatenmark"),
                                1)))
    agent.turn(con, "Tomatenmark")

    system = llm.aufrufe[0]["nachrichten"][0]["content"]
    assert "Kategorien:" in system
    # Vorgelegt wird, was der Katalog hergibt — und nur das.
    assert "Aufschnitt" in system
    assert "Reis, Pasta & Getreide" in system
    assert "Konserven & Eingelegtes" not in system


# --------------------------------------------------------------------------
# Das Modell ordnet zu — und erfindet nicht

def test_das_modell_ordnet_ein_wort_einer_kategorie_zu(con):
    """„Nudeln" ist keine Kategorie. Das Modell weiss, wo sie stehen."""
    agent, llm = _chat(_extract(("Nudeln", 1),
                                kategorie="Reis, Pasta & Getreide"))
    ergebnis = agent.turn(con, "Nudeln")

    assert ergebnis.weg == chatmodul.WEG_FAECHER
    assert ergebnis.kategorie == "Reis, Pasta & Getreide"
    assert ergebnis.sorten_herkunft == oberbegriffe.MODELL
    assert {s["name"] for s in ergebnis.sorten} == {"Pasta", "Reis",
                                                    "Couscous", "Mehl"}
    # EIN Aufruf, nicht zwei: Stufe 3 entfällt, es ist nichts zu wählen.
    assert len(llm.aufrufe) == 1


def test_das_modell_kann_keine_kategorie_erfinden(con):
    """Eine Kategorie, die nicht vorgelegt wurde, wird VERWORFEN.

    Dieselbe Zusicherung wie bei den Produkt-IDs in `plan.choose` — und aus
    demselben Grund: eine erfundene Kategorie führte in eine Auffächerung
    ohne Sorten, also in eine Auswahl, die aussieht wie eine echte.
    """
    agent, llm = _chat(_extract(("Pasta", 1), kategorie="Teigwarenabteilung"),
                       _choose(("Pasta", _pid(con, "Spaghetti No. 5"), 1)))
    ergebnis = agent.turn(con, "Nudeln")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.kategorie is None
    # Nicht repariert, nicht auf die ähnlichste gebogen — benannt.
    assert ergebnis.sorten_verworfen == "Teigwarenabteilung"
    # Und der Begriff geht trotzdem nicht verloren.
    assert [v["name"] for v in ergebnis.vorschlaege] == ["Spaghetti No. 5"]


def test_eine_kategorie_wird_auch_in_anderer_schreibweise_erkannt(con):
    """Umlaute und Kleinschreibung sind Modelllaune, kein Erfinden."""
    agent, _ = _chat(_extract(("Pasta", 1),
                              kategorie="reis, pasta & getreide"))
    ergebnis = agent.turn(con, "Nudeln")
    assert ergebnis.kategorie == "Reis, Pasta & Getreide"


def test_ein_gericht_schlaegt_den_oberbegriff(con):
    """„Pho" ist kurz — aber ein Rezept ist die bessere Antwort als eine Frage."""
    agent, _ = _chat(_extract(("Reisnudeln", 1), gericht="Pho",
                              kategorie="Reis, Pasta & Getreide"),
                     _choose(("Reisnudeln", _pid(con, "Spaghetti No. 5"), 1)))
    ergebnis = agent.turn(con, "Pho")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.gericht == "Pho"
    assert ergebnis.kategorie is None


# --------------------------------------------------------------------------
# Erst die Sorte, dann das Produkt — der normale Ablauf

def test_eine_gewaehlte_sorte_fuehrt_in_den_normalen_kandidatenablauf(con):
    """Kandidaten, Auswahl, Ja/Nein, Alternativen — kein zweiter Mechanismus."""
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id, ["Salami"])

    agent, llm = _chat(_choose(("Salami", _pid(con, "Levoni Salami Milano"),
                                1)))
    ergebnis = agent.turn(con, "Salami", auffaechern=False,
                          aus_sorten=("Aufschnitt", gewaehlt))

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.gewaehlte_sorten == ["Salami"]
    zeile = ergebnis.vorschlaege[0]
    assert zeile["name"] == "Levoni Salami Milano"
    # Die Herkunft steht an der Zeile: die Sorte ist der Suchbegriff.
    assert zeile["search_term"] == "Salami"
    # Und die übrigen Kandidaten sind aufgehoben (WB-359) — ein „Nein" zeigt
    # sie, ohne ein zweites Mal zu suchen.
    assert zeile["n_alternativen"] == 2
    # Nur Stufe 3 lief; Stufe 1 entfällt, die Begriffe stehen ja fest.
    assert len(llm.aufrufe) == 1


def test_die_kandidaten_einer_sorte_kommen_aus_ihrer_kategorie(con):
    """Und NUR aus ihr: was in einer anderen Sorte steht, liegt nicht vor."""
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id, ["Salami"])
    agent, _ = _chat(_choose(("Salami", _pid(con, "Levoni Salami Milano"), 1)))
    ergebnis = agent.turn(con, "Salami", auffaechern=False,
                          aus_sorten=("Aufschnitt", gewaehlt))

    alternativen = vorschlaege.alternativen(con, ergebnis.vorschlaege[0]["id"])
    namen = {a["name"] for a in alternativen}
    assert namen == {"Simonini Salami Napoli", "Ferdi Fuchs Mini Salami"}


def test_ein_ja_zur_sorte_legt_in_den_korb(con):
    """Der Weg endet, wo jeder Vorschlag endet: an derselben Tür in den Korb."""
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id, ["Salami"])
    agent, _ = _chat(_choose(("Salami", _pid(con, "Levoni Salami Milano"), 1)))
    ergebnis = agent.turn(con, "Salami", auffaechern=False,
                          aus_sorten=("Aufschnitt", gewaehlt))

    vorschlaege.entscheiden(con, ergebnis.vorschlaege[0]["id"],
                            vorschlaege.BEHALTEN)
    assert [p["name"] for p in orders.inhalt(con)] == ["Levoni Salami Milano"]


def test_mehrere_sorten_lassen_sich_waehlen(con):
    """„Aufschnitt" heisst oft Salami UND Kochschinken, nicht entweder-oder."""
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id,
                                      ["Salami", "Kochschinken"])
    assert gewaehlt == ["Salami", "Kochschinken"]

    agent, _ = _chat(_choose(
        ("Salami", _pid(con, "Levoni Salami Milano"), 1),
        ("Kochschinken", _pid(con, "Gutfried Kochschinken"), 2)))
    ergebnis = agent.turn(con, "Salami, Kochschinken", auffaechern=False,
                          aus_sorten=("Aufschnitt", gewaehlt))

    assert [v["name"] for v in ergebnis.vorschlaege] == [
        "Levoni Salami Milano", "Gutfried Kochschinken"]
    assert [v["qty"] for v in ergebnis.vorschlaege] == [1, 2]


def test_eine_nicht_angebotene_sorte_wird_verworfen(con):
    """Dieselbe Regel wie bei den Produkt-IDs, nur für das Formular."""
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(
        con, erst.chat_message_id, ["Salami", "Sülze & Wurst in Aspik",
                                    "Kaviar"])
    assert gewaehlt == ["Salami"]


def test_ein_oberbegriff_darf_uebersprungen_werden(con):
    """Wer nichts auswählt, steht nicht in einer Sackgasse.

    `auffaechern=False` ist der Weg zurück in den Freitext: derselbe Satz
    läuft durch die normalen Stufen und liefert die Produkte, die das Wort im
    Namen tragen — genau das, was der Shop vor diesem Ticket lieferte.
    """
    agent, llm = _chat(_extract(("Aufschnitt", 1)),
                       _choose(("Aufschnitt", _pid(con, "Jagdwurst Aufschnitt"),
                                1)))
    ergebnis = agent.turn(con, "Aufschnitt", auffaechern=False)

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert [v["name"] for v in ergebnis.vorschlaege] == ["Jagdwurst Aufschnitt"]
    # Auch der Prompt bleibt der alte: ohne Auffächerung keine Kategorienliste.
    assert "Kategorien:" not in llm.aufrufe[0]["nachrichten"][0]["content"]


def test_die_auffaecherung_laesst_sich_ganz_abschalten(con):
    """Die Stellschraube aus Spec 8.3 — für Experimente und Evals."""
    agent = chatmodul.Chat(
        FakeLLM(_extract(("Aufschnitt", 1)),
                _choose(("Aufschnitt", _pid(con, "Jagdwurst Aufschnitt"), 1))),
        wecker=Box(), auffaechern=False)
    assert agent.turn(con, "Aufschnitt").weg == chatmodul.WEG_LLM


# --------------------------------------------------------------------------
# Im Trace sichtbar

def test_die_auffaecherung_steht_am_ergebnis_und_verbindet_beide_haelften(con):
    """Ohne die Kategorie auf BEIDEN Zügen ist der Umweg nicht messbar."""
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id, ["Salami"])
    agent, _ = _chat(_choose(("Salami", _pid(con, "Levoni Salami Milano"), 1)))
    zweit = agent.turn(con, "Salami", auffaechern=False,
                       aus_sorten=("Aufschnitt", gewaehlt))

    assert (erst.kategorie, len(erst.sorten)) == ("Aufschnitt", 4)
    assert (zweit.kategorie, zweit.gewaehlte_sorten) == ("Aufschnitt",
                                                         ["Salami"])


def test_eine_erfundene_kategorie_ohne_begriffe_wird_zur_suche(con):
    """Der gemessene Fall „Getränke": nur eine Kategorie, und die gibt es nicht.

    Das Modell antwortete auf ein Wort mit `kategorie: "Alkoholfreie
    Alternatives"` und einer LEEREN Begriffsliste. Eine Fehlermeldung wäre
    hier das schlechtere Ende: gesucht wird dann nach dem, was die Nutzerin
    getippt hat — ihr eigenes Wort, nicht eines aus dem Modell.
    """
    agent, _ = _chat(_extract(kategorie="Wurstabteilung"),
                     _choose(("Salami", _pid(con, "Levoni Salami Milano"), 1)))
    ergebnis = agent.turn(con, "Salami")

    assert ergebnis.weg == chatmodul.WEG_LLM
    assert ergebnis.sorten_verworfen == "Wurstabteilung"
    assert [v["name"] for v in ergebnis.vorschlaege] == ["Levoni Salami Milano"]


def test_eine_gewaehlte_sorte_bekommt_ihren_eigenen_prompt(con):
    """Der Begriff ist ein REGALNAME, und Stufe 3 muss das wissen.

    Gemessen (2026-08-28, echte Box): mit dem gewöhnlichen Prompt wählte das
    Modell zu „Rohschinken & Bacon" dreimal gar nichts — kein Produkt heisst
    so. Mit `SYSTEM_CHOOSE_SORTE` wählte es in allen fünf Sorten eines.
    """
    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id, ["Salami"])
    agent, llm = _chat(_choose(("Salami", _pid(con, "Levoni Salami Milano"),
                                1)))
    agent.turn(con, "Salami", auffaechern=False,
               aus_sorten=("Aufschnitt", gewaehlt))

    system = llm.aufrufe[0]["nachrichten"][0]["content"]
    assert system == plan.SYSTEM_CHOOSE_SORTE
    assert "NAME EINER SORTE" in system


def test_die_auffaecherung_steht_im_span(con):
    """Sonst ist später nicht messbar, ob der Umweg hilft (Punkt 7).

    Kein Phoenix und kein Netz: die Spans landen in einem
    `InMemorySpanExporter`, genau wie in `test_obs.py`.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter)

    from picknick import obs

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs.setze_provider(provider)

    erst = _faechere(con)
    gewaehlt = oberbegriffe.gewaehlte(con, erst.chat_message_id, ["Salami"])
    agent, _ = _chat(_choose(("Salami", _pid(con, "Levoni Salami Milano"), 1)))
    agent.turn(con, "Salami", auffaechern=False,
               aus_sorten=("Aufschnitt", gewaehlt))

    zuege = [s for s in exporter.get_finished_spans() if s.name == "chat.turn"]
    assert len(zuege) == 2
    auf, wahl = (dict(s.attributes) for s in zuege)

    assert auf["picknick.path"] == chatmodul.WEG_FAECHER
    assert auf["picknick.fanout_category"] == "Aufschnitt"
    assert auf["picknick.fanout_varieties"] == 4
    assert auf["picknick.fanout_source"] == oberbegriffe.KATALOG
    # Beide Hälften desselben Umwegs finden über die Kategorie zusammen.
    assert wahl["picknick.fanout_category"] == "Aufschnitt"
    assert wahl["picknick.varieties_chosen"] == "Salami"
    assert wahl["picknick.products"] == 1
