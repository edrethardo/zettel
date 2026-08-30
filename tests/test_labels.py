"""Tests für das Eval-Label aus dem Produkt (WB-329, Spec 8.1).

**Ohne Phoenix, ohne Netz.** Der Phoenix-Client ist hier ein Doppelgänger, der
nur mitschreibt; eine autouse-Fixture sorgt dafür, dass auch ein vergessener
Test keinen echten bauen kann — auf diesem Rechner läuft Phoenix wirklich auf
localhost:6006, und ein Testlauf, der dorthin annotiert, verdürbe genau die
Zahlen, mit denen `scripts/label_probe.py` die Verifikation belegt.

Geprüft wird der Reihe nach, was an dieser Idee tragen muss:

* die Rechnung — 3 von 4 behalten ist 0.75, und `offen` verschiebt sie nicht;
* das Schweigen — ein Zug, über den nie entschieden wurde, bekommt gar keine
  Zahl, weil eine fehlende ehrlicher ist als eine erfundene;
* der Zeitpunkt — vor dem Abschicken wird nichts geschrieben;
* die Herkunft — `annotator_kind` ist `HUMAN`, sonst wäre der Wert dieser
  Daten dahin;
* die Erklärung — der Suchbegriff steht an der Einzelannotation;
* und die harte Anforderung: **fällt Phoenix aus oder hängt es, geht die
  Bestellung trotzdem durch.**

Was diese Suite NICHT beweist: dass Phoenix gleiche `identifier`
tatsächlich aktualisiert statt danebenzulegen. Hier steht nur, dass dieselbe
Sache immer denselben Schlüssel bekommt — der Rest ist eine Zusage der API und
wird gegen das echte Phoenix in `scripts/label_probe.py` nachgemessen.
"""
import threading
import time

import pytest

from zettel import obs, orders
from zettel.assistant import vorschlaege
from zettel.obs import labels

SPAN = "a1b2c3d4e5f60718"
SPAN_ZWEI = "00112233445566ff"


# --------------------------------------------------------------------------
# Doppelgänger

class FakePhoenix:
    """Ein Phoenix, das nur mitschreibt — oder auf Wunsch versagt.

    Deckt beide Ausfälle ab, die es in echt gibt und die sich unterscheiden:
    „Port zu" (wirft sofort) und „nimmt an, antwortet nie" (`bremse_s`, der
    teure Fall — gemessen bis 30 s, siehe `zettel.obs.labels`).
    """

    def __init__(self, fehler: Exception | None = None, bremse_s: float = 0.0):
        self.fehler = fehler
        self.bremse_s = bremse_s
        self.aufrufe: list[list[dict]] = []
        self._sperre = threading.Lock()

    @property
    def spans(self):
        return self

    def log_span_annotations(self, *, span_annotations, sync=False):
        if self.bremse_s:
            time.sleep(self.bremse_s)
        with self._sperre:
            self.aufrufe.append(list(span_annotations))
        if self.fehler is not None:
            raise self.fehler

    # -- Auswertung im Test ------------------------------------------------

    @property
    def alle(self) -> list[dict]:
        return [a for aufruf in self.aufrufe for a in aufruf]

    def mit_namen(self, name: str) -> list[dict]:
        return [a for a in self.alle if a["name"] == name]


@pytest.fixture(autouse=True)
def kein_echtes_phoenix(monkeypatch):
    """Kein Test baut je einen echten Client. Siehe Modul-Docstring."""
    def verboten():
        raise AssertionError("Ein Test wollte an das echte Phoenix.")

    monkeypatch.setattr(labels, "klient", verboten)


@pytest.fixture
def tracing_an(monkeypatch):
    """Schaltet an, was `conftest.py` für die ganze Suite abschaltet.

    Ohne Tracing gibt es keine Spans, also schreibt `labels.schreiben()` von
    sich aus nichts (eigener Test weiter unten). Wer den Weg über
    `orders.abschicken()` prüfen will, kann keinen Client durchreichen und
    braucht deshalb beides: den Schalter an und einen untergeschobenen
    Client.
    """
    monkeypatch.setenv("ZETTEL_TRACING", "1")


@pytest.fixture
def con(katalog_con):
    """Der Katalog aus der Vorlage (conftest.py) — einmal gebaut, hier kopiert."""
    yield katalog_con
    # Wartet auf die Hintergrund-Threads, bevor die Verbindung zugeht: sonst
    # zieht ein langsamer Test dem nächsten den Boden weg. `katalog_con`
    # schliesst danach.
    labels.abwarten(5.0)


# --------------------------------------------------------------------------
# Ein Zug von Hand — so, wie ihn `chat.turn()` hinterlässt

def _zug(con, span_id: str = SPAN, begriffe=("Milch", "Butter", "Zwiebeln",
                                             "Klopapier")) -> int:
    """Legt eine Chatzeile mit Vorschlägen an und gibt ihre id zurück.

    Der erste Vorschlag ist ein echtes Produkt aus dem Katalog (damit
    `product_id` und `rank` in den Metadaten geprüft sind), die übrigen sind
    Freitext — genau die Mischung, die ein Zug in echt erzeugt.
    """
    order_id = orders.warenkorb(con)
    msg = vorschlaege.nachricht(con, order_id, vorschlaege.ROLLE_AGENT,
                                "Vorschläge", span_id)
    pid = con.execute("SELECT id FROM product ORDER BY id LIMIT 1"
                      ).fetchone()["id"]
    for i, begriff in enumerate(begriffe):
        if i == 0:
            vorschlaege.vorschlag(con, msg, product_id=pid,
                                  search_term=begriff, rang=4.01)
        else:
            vorschlaege.vorschlag(con, msg, free_text=f"{begriff} (Freitext)",
                                  search_term=begriff, rang=None)
    return msg


def _entscheiden(con, msg_id: int, entscheidungen) -> None:
    """`entscheidungen` je Vorschlag in der Reihenfolge des Anlegens.

    `None` heisst „gar nichts angetippt" — der Vorschlag bleibt `offen`.
    """
    for v, e in zip(vorschlaege.liste(con, msg_id), entscheidungen):
        if e is not None:
            vorschlaege.entscheiden(con, v["id"], e)


BEHALTEN, VERWORFEN = vorschlaege.BEHALTEN, vorschlaege.VERWORFEN


# --------------------------------------------------------------------------
# Die Rechnung

def test_drei_von_vier_behalten_ergibt_die_quote_075(con):
    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, BEHALTEN, BEHALTEN, VERWORFEN])

    annos = labels.annotationen(con, orders.warenkorb(con))
    quoten = [a for a in annos if a["name"] == labels.NAME_QUOTE]

    assert len(quoten) == 1
    assert quoten[0]["result"]["score"] == 0.75
    assert quoten[0]["span_id"] == SPAN
    assert quoten[0]["metadata"]["kept"] == 3
    assert quoten[0]["metadata"]["removed"] == 1


def test_offene_vorschlaege_veraendern_die_quote_nicht(con):
    """Sonst zählte ein Abbruch als Fehler des Modells — und die Zahl löge."""
    msg = _zug(con, begriffe=("Milch", "Butter", "Zwiebeln", "Klopapier",
                              "Zahnpasta", "Reis"))
    _entscheiden(con, msg,
                 [BEHALTEN, BEHALTEN, BEHALTEN, VERWORFEN, None, None])

    quote = labels.annotationen(con, orders.warenkorb(con))[0]

    assert quote["name"] == labels.NAME_QUOTE
    assert quote["result"]["score"] == 0.75
    assert quote["metadata"]["open"] == 2
    assert "2 offen und nicht gewertet" in quote["result"]["explanation"]


def test_nur_offene_vorschlaege_erzeugen_gar_keine_quote(con):
    """Eine fehlende Zahl ist ehrlicher als eine erfundene 0.0."""
    _zug(con)
    orders.einlegen(con, free_text="Damit der Korb nicht leer ist")

    annos = labels.annotationen(con, orders.warenkorb(con))

    assert annos == []


def test_ein_verworfener_vorschlag_bekommt_label_und_score_null(con):
    msg = _zug(con, begriffe=("Butter",))
    _entscheiden(con, msg, [VERWORFEN])

    einzel = [a for a in labels.annotationen(con, orders.warenkorb(con))
              if a["name"] == labels.NAME_ENTSCHEIDUNG]

    assert len(einzel) == 1
    assert einzel[0]["result"]["label"] == "removed"
    assert einzel[0]["result"]["score"] == 0.0


def test_offene_vorschlaege_bekommen_keine_einzelannotation(con):
    """Nie entschieden heisst nie beurteilt."""
    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, None, None, None])

    einzel = [a for a in labels.annotationen(con, orders.warenkorb(con))
              if a["name"] == labels.NAME_ENTSCHEIDUNG]

    assert [a["result"]["label"] for a in einzel] == ["kept"]


def test_zwei_zuege_bekommen_getrennte_quoten(con):
    """Je Zug eine Zahl — sonst verschwindet der schlechte im guten."""
    eins = _zug(con, SPAN, begriffe=("Milch", "Butter"))
    _entscheiden(con, eins, [BEHALTEN, BEHALTEN])
    zwei = _zug(con, SPAN_ZWEI, begriffe=("Zahnpasta", "Reis"))
    _entscheiden(con, zwei, [VERWORFEN, VERWORFEN])

    quoten = {a["span_id"]: a["result"]["score"]
              for a in labels.annotationen(con, orders.warenkorb(con))
              if a["name"] == labels.NAME_QUOTE}

    assert quoten == {SPAN: 1.0, SPAN_ZWEI: 0.0}


# --------------------------------------------------------------------------
# Herkunft und Erklärung

def test_annotator_kind_ist_human(con):
    """Kein Detail: das unterscheidet echtes Feedback von einem LLM-Judge."""
    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, VERWORFEN, None, None])

    annos = labels.annotationen(con, orders.warenkorb(con))

    assert annos
    assert {a["annotator_kind"] for a in annos} == {"HUMAN"}


def test_der_suchbegriff_steht_als_erklaerung_an_der_einzelannotation(con):
    """Damit im Trace zu sehen ist, WELCHER Begriff schlecht gemappt hat."""
    msg = _zug(con, begriffe=("Milch", "Butter"))
    _entscheiden(con, msg, [BEHALTEN, VERWORFEN])

    einzel = [a for a in labels.annotationen(con, orders.warenkorb(con))
              if a["name"] == labels.NAME_ENTSCHEIDUNG]

    assert [(a["result"]["label"], a["result"]["explanation"])
            for a in einzel] == [("kept", "Milch"), ("removed", "Butter")]


def test_die_metadaten_tragen_produkt_und_rang(con):
    """Der Rang neben dem Urteil — die Frage aus WB-328 mit Antwort daneben."""
    msg = _zug(con, begriffe=("Milch", "Butter"))
    _entscheiden(con, msg, [VERWORFEN, VERWORFEN])

    erste = [a for a in labels.annotationen(con, orders.warenkorb(con))
             if a["name"] == labels.NAME_ENTSCHEIDUNG][0]

    assert erste["metadata"]["rank"] == 4.01
    assert erste["metadata"]["search_term"] == "Milch"
    assert erste["metadata"]["product_id"]
    assert erste["metadata"]["free_text"] is False


def test_ein_zug_ohne_span_id_wird_uebersprungen(con):
    """Ohne Span gibt es nichts zu annotieren — und keine Null-ID erfinden."""
    order_id = orders.warenkorb(con)
    msg = vorschlaege.nachricht(con, order_id, vorschlaege.ROLLE_AGENT,
                                "ohne Trace", None)
    vorschlaege.vorschlag(con, msg, free_text="Milch", search_term="Milch")
    _entscheiden(con, msg, [BEHALTEN])

    assert labels.annotationen(con, order_id) == []


# --------------------------------------------------------------------------
# Der Zeitpunkt: erst beim Abschicken

def test_vor_dem_abschicken_wird_nichts_geschrieben(con, tracing_an,
                                                    monkeypatch):
    """Bis dahin kann sie ihre Meinung noch ändern."""
    fake = FakePhoenix()
    monkeypatch.setattr(labels, "klient", lambda: fake)

    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, BEHALTEN, BEHALTEN, VERWORFEN])
    assert fake.aufrufe == []

    orders.abschicken(con)
    labels.abwarten(5.0)

    assert fake.mit_namen(labels.NAME_QUOTE)[0]["result"]["score"] == 0.75


def test_eine_meinungsaenderung_vor_dem_abschicken_zaehlt(con, tracing_an,
                                                          monkeypatch):
    """Aus „Ja, doch nicht" wird ein `removed` — und nur das geht raus."""
    fake = FakePhoenix()
    monkeypatch.setattr(labels, "klient", lambda: fake)

    msg = _zug(con, begriffe=("Milch", "Butter"))
    _entscheiden(con, msg, [BEHALTEN, BEHALTEN])
    _entscheiden(con, msg, [None, VERWORFEN])

    orders.abschicken(con)
    labels.abwarten(5.0)

    assert fake.mit_namen(labels.NAME_QUOTE)[0]["result"]["score"] == 0.5
    assert sorted(a["result"]["label"]
                  for a in fake.mit_namen(labels.NAME_ENTSCHEIDUNG)) == [
        "kept", "removed"]


def test_tracing_aus_schreibt_nichts(con):
    """`ZETTEL_TRACING=0` (die Vorgabe der Suite): keine Spans, keine Labels.

    Ohne diese Bremse redete jeder Testlauf mit dem Phoenix, das auf diesem
    Rechner läuft — die autouse-Fixture würde das als Fehler melden, aber im
    Betrieb wäre es ein stiller Schreibzugriff auf ein Projekt, in dem es
    keine passenden Spans gibt.
    """
    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, VERWORFEN, None, None])

    assert labels.schreiben(con, orders.warenkorb(con)) == []


# --------------------------------------------------------------------------
# Phoenix darf den Nutzerweg nicht aufhalten

def test_nicht_erreichbares_phoenix_bricht_das_abschicken_nicht_ab(
        con, tracing_an, monkeypatch):
    import httpx

    fake = FakePhoenix(fehler=httpx.ConnectError("Connection refused"))
    monkeypatch.setattr(labels, "klient", lambda: fake)

    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, VERWORFEN, None, None])
    bestellung = orders.abschicken(con)
    labels.abwarten(5.0)

    assert bestellung["state"] == "offen"
    assert orders.warenkorb_id(con) is None
    # Der Versuch wurde unternommen und ist gescheitert — nicht übersprungen.
    assert fake.aufrufe


def test_haengendes_phoenix_haelt_die_bestellung_nicht_auf(con, tracing_an,
                                                           monkeypatch):
    """Der teure Ausfall: Phoenix nimmt an und antwortet nie.

    Gemessen sind dafür 30 s (read-Timeout des Clients). Der Fake bremst hier
    nur 2 s — würde `abschicken()` darauf warten, fiele der Test trotzdem um,
    und zwar aus demselben Grund.
    """
    fake = FakePhoenix(bremse_s=2.0)
    monkeypatch.setattr(labels, "klient", lambda: fake)

    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, VERWORFEN, None, None])

    t0 = time.monotonic()
    bestellung = orders.abschicken(con)
    dauer = time.monotonic() - t0

    assert bestellung["state"] == "offen"
    assert dauer < 0.5, f"Abschicken hat {dauer:.2f} s auf Phoenix gewartet"
    assert labels.abwarten(10.0)
    assert fake.aufrufe


def test_ein_kaputter_client_bricht_das_abschicken_nicht_ab(con, tracing_an,
                                                            monkeypatch):
    """Auch ein Client, der beim BAUEN scheitert, kostet keine Bestellung."""
    def kaputt():
        raise RuntimeError("kein Client")

    monkeypatch.setattr(labels, "klient", kaputt)

    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, VERWORFEN, None, None])

    assert orders.abschicken(con)["state"] == "offen"
    assert labels.abwarten(5.0)


# --------------------------------------------------------------------------
# Keine Dubletten

def test_zweimal_schreiben_erzeugt_dieselben_identifier(con):
    """`identifier` ist der Schlüssel, unter dem Phoenix aktualisiert.

    Der Test kann nur die eine Hälfte zeigen — dass dieselbe Sache immer
    denselben Schlüssel bekommt. Dass Phoenix daraufhin aktualisiert statt
    danebenzulegen, misst `scripts/label_probe.py` gegen das echte Phoenix.
    """
    fake = FakePhoenix()
    msg = _zug(con)
    _entscheiden(con, msg, [BEHALTEN, BEHALTEN, BEHALTEN, VERWORFEN])
    korb = orders.warenkorb(con)

    labels.schreiben(con, korb, client=fake)
    labels.abwarten(5.0)
    labels.schreiben(con, korb, client=fake)
    labels.abwarten(5.0)

    erst, zweit = fake.aufrufe
    schluessel = [(a["name"], a["span_id"], a["identifier"]) for a in erst]
    assert schluessel == [(a["name"], a["span_id"], a["identifier"])
                          for a in zweit]
    assert len(set(schluessel)) == len(schluessel) == 5


def test_die_identifier_kommen_aus_den_datenbank_ids(con):
    """Damit sie über Prozesse und Neustarts hinweg dieselben bleiben."""
    msg = _zug(con, begriffe=("Milch",))
    _entscheiden(con, msg, [BEHALTEN])
    vid = vorschlaege.liste(con, msg)[0]["id"]

    annos = labels.annotationen(con, orders.warenkorb(con))

    assert {a["identifier"] for a in annos} == {
        f"zettel-turn-{msg}", f"zettel-suggestion-{vid}"}


# --------------------------------------------------------------------------
# Die Adresse

def test_die_basis_url_kommt_vom_selben_endpunkt_wie_die_spans(monkeypatch):
    """Sonst gingen Spans und Annotationen an verschiedene Phoenixe."""
    monkeypatch.setenv(obs.otel.ENV_ENDPUNKT,
                       "http://dose:6006/v1/traces")

    assert labels.basis_url() == "http://dose:6006"


def test_ohne_umgebung_gilt_der_endpunkt_aus_der_spec(monkeypatch):
    monkeypatch.delenv(obs.otel.ENV_ENDPUNKT, raising=False)

    assert labels.basis_url() == "http://localhost:6006"


# --------------------------------------------------------------------------
# Die Korrektur in Phoenix (WB-359)
#
# Hier wird aus einem negativen Label ein positives. `suggestion=removed` sagt
# „das war falsch"; die `correction`-Annotation sagt, WAS statt dessen richtig
# war — und zwar mit einer Produkt-ID, die in derselben Vorlage stand. Für
# eine Eval ist das der Unterschied zwischen einer Fehlerquote und einer
# Fehleranalyse.

def _zug_mit_korrektur(con, span_id: str = SPAN):
    """Ein Vorschlag, verworfen, und eine Alternative statt seiner."""
    order_id = orders.warenkorb(con)
    msg = vorschlaege.nachricht(con, order_id, vorschlaege.ROLLE_AGENT,
                                "Vorschläge", span_id)
    falsch, richtig = [r["id"] for r in con.execute(
        "SELECT id FROM product ORDER BY id LIMIT 2")]
    sid = vorschlaege.vorschlag(con, msg, product_id=falsch,
                                search_term="Butter", rang=4.01)
    vorschlaege.kandidaten_merken(con, sid, [
        {"id": falsch, "via": "Butter", "rang": 4.01},
        {"id": richtig, "via": "Butter", "rang": 3.9}])
    return msg, sid, falsch, richtig


def test_die_korrektur_wird_als_eigene_annotation_geschrieben(con):
    msg, sid, falsch, richtig = _zug_mit_korrektur(con)
    vorschlaege.korrigieren(con, sid, richtig)

    annos = labels.annotationen(con, orders.warenkorb(con))
    k = [a for a in annos if a["name"] == labels.NAME_KORREKTUR]
    assert len(k) == 1
    assert k[0]["result"]["label"] == labels.LABEL_KORRIGIERT
    # Beide Seiten stehen dran: was verworfen wurde und was statt dessen kam.
    m = k[0]["metadata"]
    assert m["product_id"] == richtig
    assert m["corrected_suggestion_id"] == sid
    assert m["search_term"] == "Butter"
    assert m["rejected_product"] == _name(con, falsch)
    assert _name(con, richtig) in k[0]["result"]["explanation"]
    assert k[0]["annotator_kind"] == labels.MENSCH


def test_die_korrektur_faellt_nicht_in_die_mapping_praezision(con):
    """Sonst hübe ausgerechnet ein Fehlgriff die Quote, sobald er korrigiert
    wird — und die Zahl, um die es im Projekt geht, wäre nach oben verbogen."""
    msg, sid, _, richtig = _zug_mit_korrektur(con)
    vorschlaege.korrigieren(con, sid, richtig)

    annos = labels.annotationen(con, orders.warenkorb(con))
    quote = [a for a in annos if a["name"] == labels.NAME_QUOTE][0]
    assert quote["result"]["score"] == 0.0
    entscheidungen = [a for a in annos
                      if a["name"] == labels.NAME_ENTSCHEIDUNG]
    assert [a["result"]["label"] for a in entscheidungen] == ["removed"]


def test_das_negative_label_traegt_die_richtige_antwort_bei_sich(con):
    """Wer nach `removed` filtert, sieht direkt, was richtig gewesen wäre."""
    msg, sid, _, richtig = _zug_mit_korrektur(con)
    vorschlaege.korrigieren(con, sid, richtig)

    anno = [a for a in labels.annotationen(con, orders.warenkorb(con))
            if a["name"] == labels.NAME_ENTSCHEIDUNG][0]
    assert anno["metadata"]["corrected_to_product_id"] == richtig
    assert anno["metadata"]["corrected_to"] == _name(con, richtig)
    assert "stattdessen" in anno["result"]["explanation"]


def test_ein_freitext_statt_der_vorlage_heisst_katalog_luecke(con):
    """Ein anderes Label als eine Korrektur — und das ist der Punkt.

    „Aus der Vorlage hätte das Modell das Richtige nehmen können" ist ein
    Modellfehler. „In der Vorlage stand es gar nicht" ist eine Katalog-Lücke
    und keinem Modell anzulasten. Unter einem Label wären die beiden nicht
    mehr zu trennen.
    """
    msg, sid, _, _ = _zug_mit_korrektur(con)
    vorschlaege.stattdessen_freitext(con, sid, "Staudensellerie")

    k = [a for a in labels.annotationen(con, orders.warenkorb(con))
         if a["name"] == labels.NAME_KORREKTUR][0]
    assert k["result"]["label"] == labels.LABEL_FREITEXT
    assert k["metadata"]["free_text"] == "Staudensellerie"
    assert "Staudensellerie" in k["result"]["explanation"]


def test_eine_zurueckgenommene_korrektur_behauptet_nichts(con):
    """Sie hat auch bei der Alternative „Nein" gesagt — dann weiss niemand,
    was richtig gewesen wäre, und es wird auch nichts geschrieben."""
    msg, sid, _, richtig = _zug_mit_korrektur(con)
    neu = vorschlaege.korrigieren(con, sid, richtig)
    vorschlaege.entscheiden(con, neu["id"], VERWORFEN)

    annos = labels.annotationen(con, orders.warenkorb(con))
    assert [a for a in annos if a["name"] == labels.NAME_KORREKTUR] == []


# --------------------------------------------------------------------------
# Der zurückgenommene Fehltipp (WB-361)
#
# Er darf KEIN Label hinterlassen — das ist der ganze Grund, warum ein
# Rückweg vor dem Abschicken nichts kostet. Sichtbar sein muss er trotzdem,
# sonst sähe später niemand, wie oft danebengetippt wird; dafür steht
# `withdrawn` in den Metadaten.

def test_ein_zurueckgenommener_fehltipp_hinterlaesst_kein_label(con):
    """Er steht beim Abschicken auf `offen`, und `offen` bekommt nichts."""
    msg = _zug(con, begriffe=("Butter",))
    sid = vorschlaege.liste(con, msg)[0]["id"]
    vorschlaege.entscheiden(con, sid, VERWORFEN)
    vorschlaege.entscheiden(con, sid, vorschlaege.OFFEN)

    annos = labels.annotationen(con, orders.warenkorb(con))

    assert [a for a in annos if a["name"] == labels.NAME_ENTSCHEIDUNG] == []
    # Und auch keine Quote: entschieden wurde am Ende gar nichts.
    assert annos == []


def test_ein_zurueckgenommener_sammelvorgang_hinterlaesst_kein_label(con):
    """Dasselbe für „Alles übernehmen" (WB-397).

    Der Sammelknopf entscheidet eine ganze Liste auf einmal — ein Fehlgriff
    dort verdürbe die Trefferquote in einem Zug. Nach der Rücknahme stehen
    seine Zeilen auf `offen`, und `offen` zählt nirgends (Spec 8.1); die
    einzeln getroffenen Entscheidungen bekommen ihr Label trotzdem.
    """
    msg = _zug(con, begriffe=("Butter", "Milch", "Zwiebeln", "Klopapier"))
    sids = [v["id"] for v in vorschlaege.liste(con, msg)]
    vorschlaege.entscheiden(con, sids[0], BEHALTEN)
    vorschlaege.alle_entscheiden(con, msg, BEHALTEN)

    vorschlaege.alle_entscheiden(con, msg, vorschlaege.OFFEN)
    annos = labels.annotationen(con, orders.warenkorb(con))

    einzel = [a for a in annos if a["name"] == labels.NAME_ENTSCHEIDUNG]
    # Genau eines: das der Zeile, die sie selbst angetippt hat.
    assert [a["result"]["label"] for a in einzel] == ["kept"]
    # Und die Quote steht auf dieser einen Entscheidung, nicht auf vieren.
    quote = [a for a in annos if a["name"] == labels.NAME_QUOTE][0]
    assert quote["result"]["score"] == 1.0
    assert (quote["metadata"]["kept"], quote["metadata"]["open"]) == (1, 3)


def test_die_ruecknahme_steht_als_metadatum_an_der_annotation(con):
    """Ein `kept`, bei dem vorher danebengetippt wurde, ist ein anderer
    Datenpunkt als ein `kept` beim ersten Hinsehen."""
    msg = _zug(con, begriffe=("Butter", "Milch"))
    erste, zweite = [v["id"] for v in vorschlaege.liste(con, msg)]
    vorschlaege.entscheiden(con, erste, VERWORFEN)
    vorschlaege.entscheiden(con, erste, vorschlaege.OFFEN)
    vorschlaege.entscheiden(con, erste, BEHALTEN)
    vorschlaege.entscheiden(con, zweite, BEHALTEN)

    annos = labels.annotationen(con, orders.warenkorb(con))
    einzel = [a for a in annos if a["name"] == labels.NAME_ENTSCHEIDUNG]
    quote = [a for a in annos if a["name"] == labels.NAME_QUOTE][0]

    assert [a["metadata"]["withdrawn"] for a in einzel] == [1, 0]
    # An der Zugannotation die Summe — und in der Erklärung, damit man sie in
    # einer Liste von Zügen sieht, ohne etwas aufzuklappen.
    assert quote["metadata"]["withdrawn"] == 1
    assert "1 Entscheidung zurückgenommen" in quote["result"]["explanation"]


def test_ohne_ruecknahme_steht_dort_eine_null_und_kein_satz(con):
    """Die Gegenprobe: der Normalfall bleibt unverändert lesbar."""
    msg = _zug(con, begriffe=("Butter",))
    _entscheiden(con, msg, [BEHALTEN])

    annos = labels.annotationen(con, orders.warenkorb(con))
    quote = [a for a in annos if a["name"] == labels.NAME_QUOTE][0]

    assert quote["metadata"]["withdrawn"] == 0
    assert "zurückgenommen" not in quote["result"]["explanation"]


def test_nur_ueber_den_allgemeinen_begriff_steht_an_der_annotation(con):
    """Der Befund aus WB-358: ein Treffer, der nur über den allgemeinsten
    Kettenbegriff kam, ist kein sicherer — und die Eval soll das sehen."""
    order_id = orders.warenkorb(con)
    msg = vorschlaege.nachricht(con, order_id, vorschlaege.ROLLE_AGENT,
                                "Vorschläge", SPAN)
    pid = con.execute("SELECT id FROM product ORDER BY id LIMIT 1"
                      ).fetchone()["id"]
    sid = vorschlaege.vorschlag(con, msg, product_id=pid,
                                search_term="Old Amsterdam", rang=2.0,
                                fallback_term="Bier")
    vorschlaege.entscheiden(con, sid, VERWORFEN)

    anno = [a for a in labels.annotationen(con, order_id)
            if a["name"] == labels.NAME_ENTSCHEIDUNG][0]
    assert anno["metadata"]["fallback_term"] == "Bier"
    assert "allgemeinen Begriff" in anno["result"]["explanation"]


def _name(con, product_id: int) -> str:
    return con.execute("SELECT name FROM product WHERE id = ?",
                       (product_id,)).fetchone()["name"]
