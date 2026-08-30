"""Tests für die Käufe aus einem Kassenbon (WB-358).

Zwei Fragen tragen diese Datei:

**1. Landet etwas in der Datenbank, was dort nicht hingehört?** Der Test
`test_zahlungsdaten_stehen_nicht_in_der_datenbank` liest die GANZE Datenbank
als SQL-Text zurück und sucht die Kartennummer, die VU-Nummer, die Terminal-ID,
die Trace- und die Belegnummer darin. Er prüft damit nicht eine Funktion,
sondern das Ergebnis — und bleibt deshalb auch dann gültig, wenn jemand den
Weg dahin umbaut. Genau das ist der Zweck: die Zusage soll den nächsten Umbau
überleben.

**2. Gilt eine Zuordnung, die niemand bestätigt hat?** Nein. Ein Kauf wird
erst zur Kaufhistorie und zum echten Preis, wenn ein Mensch „Ja" gesagt hat.

Kein Netz, kein Modell, keine Box: das Modell ist ein Fake mit fester Antwort,
der Weckzustand wird untergeschoben.
"""
import json

import pytest

from zettel import db
from zettel.bons import kaeufe, zerlegen, zuordnung
from zettel.llm import wake
from zettel.llm.client import Antwort

from conftest import ZAHLUNGSDATEN  # noqa: F401  (Pfad via rootdir)


class FakeLLM:
    """Dasselbe Muster wie in `test_web_chat.py`: feste Antworten, kein Netz."""

    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        self.aufrufe.append((list(nachrichten), weitere))
        if not self.antworten:
            raise AssertionError("Mehr Modellaufrufe als vorbereitete Antworten.")
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def __init__(self, zustand=wake.BEDIENT, grund=None):
        self._zustand = zustand
        self._grund = grund

    def zustand(self):
        if self._zustand == wake.BEDIENT:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(self._zustand, seit_s=12.0, grund=self._grund)


PRODUKTE = [
    ("Miil Kräuterquark 40%", "Quark & Frischkäse", 129),
    ("Bergbauern Brotzeit Käse", "Käse", 249),
    ("Choco Schokokekse", "Kekse & Gebäck", 199),
    ("Zwiebelringe tiefgekühlt", "Tiefkühl", 219),
]


def _produkte(con):
    for i, (name, kategorie, preis) in enumerate(PRODUKTE, start=1):
        con.execute(
            "INSERT INTO product (source, external_id, name, brand,"
            "                     price_cents, unit_text, category_l1,"
            "                     category_l2, category_l3)"
            " VALUES ('knuspr', ?, ?, NULL, ?, '250 g', 'Molkerei', ?, ?)",
            (str(100 + i), name, preis, kategorie, kategorie))
    con.commit()


@pytest.fixture
def con(vorlagen, tmp_path):
    """Die vier Produkte von oben — einmal gebaut, hier kopiert (conftest.py)."""
    c = db.connect(vorlagen.datei(tmp_path / "zettel.db", "bon_produkte",
                                  _produkte))
    yield c
    c.close()


@pytest.fixture
def bon(bon_text):
    return zerlegen.zerlege(bon_text)


def _pid(con, praefix):
    return con.execute("SELECT id FROM product WHERE name LIKE ?",
                       (praefix + "%",)).fetchone()["id"]


def _deutung(*zeilen):
    """Eine Modellantwort im Format von `zuordnung.SCHEMA_BON`."""
    return json.dumps({"zeilen": list(zeilen)}, ensure_ascii=False)


# --------------------------------------------------------------------------
# Datenschutz

def test_zahlungsdaten_stehen_nicht_in_der_datenbank(con, bon):
    """Die Zusage des Tickets, am Ergebnis geprüft und nicht am Weg dorthin."""
    kaeufe.anlegen(con, bon, datei="bon.pdf")
    alles = "\n".join(con.iterdump())
    for was, wert in ZAHLUNGSDATEN.items():
        assert wert not in alles, f"{was} ({wert}) steht in der Datenbank"
    assert "Mastercard" not in alles
    # Gegenprobe: die Käufe SIND drin. Ein Test, der nur auf Abwesenheit
    # prüft, wäre auch bei einer leeren Datenbank grün.
    assert "KRAEUTERQUARK" in alles


def test_receipt_hat_keine_spalte_fuer_zahlungsdaten(con):
    """Die strukturelle Hälfte: es gibt keinen Ort, an den so etwas passte."""
    spalten = set()
    for tabelle in ("receipt", "receipt_item"):
        spalten |= {r[1].lower() for r in con.execute(
            f"PRAGMA table_info({tabelle})")}
    for verboten in ("card", "karte", "pan", "terminal", "trace", "vu",
                     "beleg_nr", "payment", "zahlung"):
        assert not [s for s in spalten if verboten in s], verboten


# --------------------------------------------------------------------------
# Anlegen

def test_beleg_traegt_laden_datum_und_summe(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    b = kaeufe.beleg(con, rid)
    assert b["store"] == "rewe"
    assert b["bought_on"] == "2026-03-04"
    assert b["total_cents"] == 1065
    assert b["n_posten"] == 6
    assert b["n_offen"] == 6


def test_posten_tragen_menge_und_preis(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeilen = {z["bon_text"]: z for z in kaeufe.posten(con, rid)}
    kekse = zeilen["SCHOKO-KEKSE"]
    assert (kekse["qty"], kekse["unit_cents"], kekse["total_cents"]) == (2, 100, 200)
    assert all(z["decision"] == kaeufe.OFFEN for z in zeilen.values())
    assert all(z["product_id"] is None for z in zeilen.values())


def test_zweites_einlesen_verdoppelt_nicht(con, bon):
    kaeufe.anlegen(con, bon, datei="bon.pdf")
    with pytest.raises(kaeufe.KaufFehler) as e:
        kaeufe.anlegen(con, bon, datei="bon.pdf")
    assert "schon eingelesen" in str(e.value)
    assert len(kaeufe.belege(con)) == 1


def test_ersetzen_wirft_den_alten_beleg_weg(con, bon):
    alt = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, alt)[0]
    kaeufe.korrigieren(con, zeile["id"], _pid(con, "Miil"))

    neu = kaeufe.anlegen(con, bon, datei="bon.pdf", ersetzen=True)

    assert len(kaeufe.belege(con)) == 1
    # Genau ein Satz Posten, und alle wieder offen: die Entscheidung des
    # vorigen Laufs gehört zu einer Zeile, die es nicht mehr gibt.
    assert con.execute("SELECT count(*) FROM receipt_item").fetchone()[0] == 6
    assert kaeufe.beleg(con, neu)["n_bestaetigt"] == 0


# --------------------------------------------------------------------------
# Bestätigen — nichts gilt ungefragt

def test_ohne_bestaetigung_kein_echter_preis(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, rid)[0]
    kaeufe.zuordnung_setzen(con, zeile["id"], product_id=_pid(con, "Miil"),
                            note="Kräuterquark", search_term="Kräuterquark",
                            rang=9.5)
    # Der Vorschlag steht da …
    assert kaeufe.posten_zeile(con, zeile["id"])["product_id"] is not None
    # … und zählt trotzdem nicht.
    assert kaeufe.echte_preise(con) == []


def test_nach_bestaetigung_steht_der_preis_mit_laden_und_datum(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = next(z for z in kaeufe.posten(con, rid)
                 if z["bon_text"] == "SCHOKO-KEKSE")
    pid = _pid(con, "Choco")
    kaeufe.zuordnung_setzen(con, zeile["id"], product_id=pid)
    kaeufe.entscheiden(con, zeile["id"], kaeufe.BESTAETIGT)

    preise = kaeufe.echte_preise(con, pid)
    assert len(preise) == 1
    p = preise[0]
    assert (p["store"], p["bought_on"]) == ("rewe", "2026-03-04")
    assert (p["qty"], p["total_cents"], p["stueck_cents"]) == (2, 200, 100)
    assert kaeufe.letzter_preis(con, pid)["total_cents"] == 200


def test_verworfene_zuordnung_zaehlt_nicht_als_preis(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, rid)[0]
    pid = _pid(con, "Miil")
    kaeufe.zuordnung_setzen(con, zeile["id"], product_id=pid)
    kaeufe.entscheiden(con, zeile["id"], kaeufe.VERWORFEN)
    assert kaeufe.echte_preise(con, pid) == []
    # Das Produkt bleibt an der Zeile stehen: „das hat das Modell
    # vorgeschlagen, und es war falsch" ist die Messung, um die es geht.
    assert kaeufe.posten_zeile(con, zeile["id"])["product_id"] == pid


def test_ja_zu_einer_zeile_ohne_produkt_geht_nicht(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, rid)[0]
    with pytest.raises(kaeufe.KaufFehler) as e:
        kaeufe.entscheiden(con, zeile["id"], kaeufe.BESTAETIGT)
    assert "keinem Produkt zugeordnet" in str(e.value)


def test_zweimal_ja_bleibt_ein_kauf(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, rid)[0]
    pid = _pid(con, "Miil")
    kaeufe.zuordnung_setzen(con, zeile["id"], product_id=pid)
    kaeufe.entscheiden(con, zeile["id"], kaeufe.BESTAETIGT)
    kaeufe.entscheiden(con, zeile["id"], kaeufe.BESTAETIGT)
    assert len(kaeufe.echte_preise(con, pid)) == 1


def test_korrigieren_setzt_ein_anderes_produkt_und_bestaetigt(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, rid)[0]
    kaeufe.zuordnung_setzen(con, zeile["id"], product_id=_pid(con, "Choco"),
                            search_term="falsch", rang=1.0)
    richtig = _pid(con, "Miil")
    neu = kaeufe.korrigieren(con, zeile["id"], richtig)
    assert neu["product_id"] == richtig
    assert neu["bestaetigt"]
    # Rang und Suchbegriff stammten von der Suche. Nach einer Korrektur von
    # Hand behaupteten sie eine Herkunft, die nicht mehr stimmt.
    assert neu["search_term"] is None and neu["rang"] is None


def test_ein_modelllauf_ueberschreibt_keine_entscheidung(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    zeile = kaeufe.posten(con, rid)[0]
    richtig = _pid(con, "Miil")
    kaeufe.korrigieren(con, zeile["id"], richtig)
    kaeufe.zuordnung_setzen(con, zeile["id"], product_id=_pid(con, "Choco"))
    assert kaeufe.posten_zeile(con, zeile["id"])["product_id"] == richtig


def test_bilanz_zaehlt_nur_entschiedenes(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    assert kaeufe.bilanz(con, rid)["quote"] is None
    zeilen = kaeufe.posten(con, rid)
    kaeufe.korrigieren(con, zeilen[0]["id"], _pid(con, "Miil"))
    kaeufe.zuordnung_setzen(con, zeilen[1]["id"], product_id=_pid(con, "Choco"))
    kaeufe.entscheiden(con, zeilen[1]["id"], kaeufe.VERWORFEN)
    b = kaeufe.bilanz(con, rid)
    assert (b["bestaetigt"], b["verworfen"], b["offen"]) == (1, 1, 4)
    assert b["quote"] == 0.5


# --------------------------------------------------------------------------
# Die Zuordnung über das Modell

def test_zuordner_schlaegt_vor_und_bestaetigt_nichts(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    antwort = _deutung(
        {"bon": "KRAEUTERQUARK", "artikel": True, "klartext": "Kräuterquark",
         "suchbegriffe": ["Kräuterquark", "Quark"]},
        {"bon": "BROTZEIT KAESE", "artikel": True, "klartext": "Brotzeitkäse",
         "suchbegriffe": ["Brotzeit Käse", "Käse"]},
    )
    z = zuordnung.Zuordner(FakeLLM(antwort), wecker=Box())
    ergebnis = z.zuordnen(con, rid)

    assert ergebnis.zugeordnet == 2
    zeilen = {x["bon_text"]: x for x in kaeufe.posten(con, rid)}
    assert zeilen["KRAEUTERQUARK"]["produkt_name"].startswith("Miil Kräuterquark")
    assert zeilen["KRAEUTERQUARK"]["note"] == "Kräuterquark"
    assert zeilen["KRAEUTERQUARK"]["search_term"]
    # Nichts ist entschieden — das ist die ganze Regel.
    assert all(x["offen"] for x in zeilen.values())
    assert kaeufe.echte_preise(con) == []


def test_zuordner_schaltet_das_denken_ab(con, bon):
    """Denk-Token zählen gegen `max_tokens` und schneiden das JSON ab."""
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    llm = FakeLLM(_deutung())
    zuordnung.Zuordner(llm, wecker=Box()).zuordnen(con, rid)
    _, weitere = llm.aufrufe[0]
    assert weitere["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}
    assert "guided_json" in weitere["extra_body"]


def test_nicht_artikel_bekommt_keinen_produktvorschlag(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    antwort = _deutung(
        {"bon": "KRAEUTERQUARK", "artikel": False, "klartext": "Pfand"})
    ergebnis = zuordnung.Zuordner(FakeLLM(antwort), wecker=Box()).zuordnen(
        con, rid)
    assert ergebnis.kein_artikel == 1
    zeile = next(z for z in kaeufe.posten(con, rid)
                 if z["bon_text"] == "KRAEUTERQUARK")
    assert zeile["product_id"] is None
    assert "kein gekaufter Artikel" in zeile["note"]


def test_eine_ausgelassene_zeile_verschiebt_nichts(con, bon):
    """Zugeordnet wird über den TEXT, nie über die Position.

    Lässt das Modell die erste Zeile aus, dürfen nicht alle folgenden
    Zuordnungen um eins verrutschen — jede davon sähe plausibel aus.
    """
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    antwort = _deutung(
        {"bon": "BROTZEIT KAESE", "artikel": True, "klartext": "Brotzeitkäse",
         "suchbegriffe": ["Brotzeit Käse"]})
    ergebnis = zuordnung.Zuordner(FakeLLM(antwort), wecker=Box()).zuordnen(
        con, rid)
    zeilen = {z["bon_text"]: z for z in kaeufe.posten(con, rid)}
    assert zeilen["KRAEUTERQUARK"]["product_id"] is None
    assert zeilen["BROTZEIT KAESE"]["produkt_name"].startswith("Bergbauern")
    assert "KRAEUTERQUARK" in ergebnis.unbeantwortet


def test_erfundene_zeile_wird_ignoriert(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    antwort = _deutung(
        {"bon": "GIBT ES NICHT", "artikel": True, "klartext": "Quark",
         "suchbegriffe": ["Kräuterquark"]})
    zuordnung.Zuordner(FakeLLM(antwort), wecker=Box()).zuordnen(con, rid)
    assert all(z["product_id"] is None for z in kaeufe.posten(con, rid))


def test_ohne_katalogtreffer_bleibt_der_klartext_stehen(con, bon):
    """`OLD AMSTERDAM` gibt es im Katalog nicht. Das ist eine Lücke, kein
    Fehler — und der Klartext ist das, wonach ein zweiter Versuch sucht."""
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    antwort = _deutung(
        {"bon": "APFELSAFT NATUR", "artikel": True,
         "klartext": "Apfelsaft naturtrüb",
         "suchbegriffe": ["Apfelsaft naturtrüb", "Apfelsaft"]})
    ergebnis = zuordnung.Zuordner(FakeLLM(antwort), wecker=Box()).zuordnen(
        con, rid)
    assert ergebnis.ohne_treffer == 1
    zeile = next(z for z in kaeufe.posten(con, rid)
                 if z["bon_text"] == "APFELSAFT NATUR")
    assert zeile["product_id"] is None
    assert zeile["note"] == "Apfelsaft naturtrüb"


def test_schlafende_box_verliert_die_kaeufe_nicht(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    z = zuordnung.Zuordner(FakeLLM(), wecker=Box(wake.WACHT_AUF, grund="lädt"))
    with pytest.raises(zuordnung.ZuordnungNichtVerfuegbar):
        z.zuordnen(con, rid)
    assert kaeufe.beleg(con, rid)["n_posten"] == 6


def test_kaputte_modellantwort_kostet_keine_kaeufe(con, bon):
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    ergebnis = zuordnung.Zuordner(FakeLLM("kein JSON, nur Prosa"),
                                  wecker=Box()).zuordnen(con, rid)
    assert ergebnis.zugeordnet == 0
    assert len(ergebnis.unbeantwortet) == 6
    assert kaeufe.beleg(con, rid)["n_posten"] == 6
