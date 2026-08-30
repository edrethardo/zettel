"""Was der Shop einmal gewählt hat, wählt er nicht noch einmal (WB-411).

Der Nutzer: „Speichere außerdem Suchergebnisse dass sie Instant kommen
können. Melde dann dass sie cached sind und gib die Möglichkeit neu zu
suchen." — und daneben: „Der Agent braucht 20 Sekunden oder so."

Gemessen (Phoenix, Züge vom 2026-08-30, echte Box): `plan.extract` 11,53 s
Median, `plan.choose` 14,71 s, `catalog.search` 0,00 s. Die Suche im eigenen
Katalog kostet nichts; die WAHL kostet — und dieselbe Wahl wird immer wieder
bezahlt. Über sieben Lasagne-Rezepte kannte jedes 36 bis 62 % seiner
Begriffsketten schon aus den anderen.

**Der Zähler dieser Datei ist `client.llm.aufrufe`.** Geprüft wird nicht eine
Zeit — die misst die Maschine, auf der der Test läuft —, sondern dass das
Modell NICHT gefragt wurde.

Kein Test geht ins Netz und keiner an ein echtes Modell.
"""
from __future__ import annotations

import json

from zettel import db
from zettel.assistant import gedaechtnis as ged

from test_alternativrezepte import (HTMX, _choose, _extract, _pid, _shop,
                                    _zug_id)
from test_alternativrezepte import datei  # noqa: F401  (Fixture)

SATZ = "Ingwer und Mie Nudeln"


def _paar(pfad):
    """Ein Zug: Stufe 1 und Stufe 3, für genau diesen Satz."""
    return [_extract((("Ingwer",), 1), (("Mie Nudeln", "Nudeln"), 1)),
            _choose(("Ingwer", _pid(pfad, "Ingwer"), 1),
                    ("Mie Nudeln", _pid(pfad, "Mie Nudeln"), 1))]


def _zeilen(pfad, mid):
    con = db.connect(pfad)
    try:
        return [(r["product_id"], r["free_text"])
                for r in con.execute(
                    "SELECT product_id, free_text FROM chat_suggestion"
                    " WHERE chat_message_id = ? ORDER BY id", (mid,))]
    finally:
        con.close()


def _wahlen(pfad):
    con = db.connect(pfad)
    try:
        return {r["begriff"]: dict(r) for r in con.execute(
            "SELECT begriff, product_id, gewaehlt, benutzt FROM begriff_wahl")}
    finally:
        con.close()


def _text(pfad, mid):
    con = db.connect(pfad)
    try:
        return con.execute("SELECT content FROM chat_message WHERE id = ?",
                           (mid,)).fetchone()["content"]
    finally:
        con.close()


# --------------------------------------------------------------------------
# 1. Dieselbe Wahl wird nicht zweimal bezahlt

def test_der_zweite_satz_fragt_stufe_drei_nicht_noch_einmal(datei, tmp_path):
    """Stufe 1 muss den Satz zerlegen, Stufe 3 nicht noch einmal wählen.

    `antworten` reicht für EINEN vollen Zug plus eine Stufe 1. Fragt der
    zweite Zug auch Stufe 3, bricht der Doppelgänger ab — der Test misst also
    eine Grenze und nicht eine Zahl.
    """
    client, _, _ = _shop(datei, tmp_path,
                         antworten=[*_paar(datei), _paar(datei)[0]])

    client.post("/chat", data={"satz": SATZ}, headers=HTMX)
    erster = _zug_id(datei)
    assert len(client.llm.aufrufe) == 2

    client.post("/chat", data={"satz": SATZ}, headers=HTMX)
    assert len(client.llm.aufrufe) == 3, "Stufe 3 wurde noch einmal gefragt."
    assert _zeilen(datei, _zug_id(datei)) == _zeilen(datei, erster), (
        "Aus dem Gedächtnis kommt eine andere Liste heraus.")


def test_die_wahl_steht_unter_der_ganzen_kette(datei, tmp_path):
    """Nicht unter „Mie Nudeln", sondern unter „Mie Nudeln|Nudeln".

    „Möhren" und „Karotten" führen zu verschiedenen Produkten (WB-340); wer
    nur den genauesten Begriff merkte, gäbe die Wahl einer Kette für die
    einer anderen aus.
    """
    client, _, _ = _shop(datei, tmp_path, antworten=_paar(datei))
    client.post("/chat", data={"satz": SATZ}, headers=HTMX)

    wahlen = _wahlen(datei)
    assert set(wahlen) == {"ingwer", "mie nudeln|nudeln"}, sorted(wahlen)
    assert all(w["gewaehlt"] for w in wahlen.values())


def test_der_zug_sagt_wie_viele_zeilen_aus_dem_gedaechtnis_kamen(datei,
                                                                 tmp_path):
    """„Melde dann dass sie cached sind" — und zwar in der ANTWORT.

    Der Satz steht im Verlauf und übersteht das Neuladen; eine Anzeige, die
    nur die Oberfläche kennt, wäre nach dem ersten Blick weg.
    """
    client, _, _ = _shop(datei, tmp_path,
                         antworten=[*_paar(datei), _paar(datei)[0]])
    client.post("/chat", data={"satz": SATZ}, headers=HTMX)
    erster = _text(datei, _zug_id(datei))
    assert "Gedächtnis" not in erster, erster

    client.post("/chat", data={"satz": SATZ}, headers=HTMX)
    zweiter = _text(datei, _zug_id(datei))
    assert "2 von 2 Zeilen kamen aus dem Gedächtnis" in zweiter, zweiter
    assert "Neu suchen" in zweiter, zweiter


# --------------------------------------------------------------------------
# 2. Der Weg zurück

def test_neu_suchen_fragt_wieder_und_ueberschreibt(datei, tmp_path):
    """„gib die Möglichkeit neu zu suchen".

    Ohne diesen Weg wäre eine einmal danebengegriffene Wahl für immer
    festgeschrieben, und das Gedächtnis wäre ein Käfig statt einer Abkürzung.
    """
    # Beim zweiten Mal nimmt das Modell zu „Ingwer" NICHTS. Das ist der
    # sichtbarste Unterschied, den ein Doppelgänger überhaupt liefern kann:
    # eine andere ID zu nennen ginge nicht, weil `plan.choose` nur zulässt,
    # was vorgelegt wurde — und der Katalog dieser Fixture hat genau einen
    # Ingwer.
    anders = _choose(("Mie Nudeln", _pid(datei, "Mie Nudeln"), 1))
    client, _, _ = _shop(datei, tmp_path,
                         antworten=[*_paar(datei),
                                    _paar(datei)[0], anders])
    client.post("/chat", data={"satz": SATZ}, headers=HTMX)
    mid = _zug_id(datei)
    assert _wahlen(datei)["ingwer"]["product_id"] == _pid(datei, "Ingwer")

    antwort = client.post(f"/chat/{mid}/neusuche", headers=HTMX)
    assert antwort.status_code == 200
    assert len(client.llm.aufrufe) == 4, "Neu suchen hat nicht gefragt."

    nachher = _wahlen(datei)["ingwer"]
    assert not nachher["gewaehlt"], "Die neue Wahl hat die alte nicht abgelöst."
    assert nachher["product_id"] is None
    assert _zug_id(datei) != mid, "Der Zug wurde nicht ersetzt."


def test_neu_suchen_an_einem_zug_ohne_satz_bricht_nichts(datei, tmp_path):
    """Ein Zug, dessen Frage jemand geleert hat — dann gibt es nichts zu tun."""
    client, _, _ = _shop(datei, tmp_path, antworten=_paar(datei))
    client.post("/chat", data={"satz": SATZ}, headers=HTMX)
    mid = _zug_id(datei)
    con = db.connect(datei)
    try:
        con.execute("DELETE FROM chat_message WHERE role = 'user'")
        con.commit()
    finally:
        con.close()

    antwort = client.post(f"/chat/{mid}/neusuche", headers=HTMX)
    assert antwort.status_code == 200
    assert "kein Satz mehr" in antwort.text


# --------------------------------------------------------------------------
# 3. Wann eine Erinnerung NICHT gilt

def test_eine_erinnerung_gilt_nur_bei_vorgelegtem_produkt():
    """Die Zusicherung aus `plan.choose` hätte sonst eine Hintertür.

    Gewählt werden darf nur, was die Suche HEUTE vorlegt. Steht das gemerkte
    Produkt nicht mehr darunter, ist der Begriff wieder offen.
    """
    gemerkt = {"ingwer": {"begriff": "ingwer", "product_id": 7,
                          "gewaehlt": 1, "quelle": ged.MODELL}}
    dabei = {"begriff": "Ingwer", "suchbegriffe": ["Ingwer"],
             "aufgehoben": [{"id": 7, "name": "Ingwer frisch"}]}
    weg = {"begriff": "Ingwer", "suchbegriffe": ["Ingwer"],
           "aufgehoben": [{"id": 9, "name": "Ingwerpulver"}]}

    bekannt, offen = ged.teilen([dabei], gemerkt)
    assert len(bekannt) == 1 and not offen
    bekannt, offen = ged.teilen([weg], gemerkt)
    assert not bekannt and len(offen) == 1


def test_ein_ausdrueckliches_nichts_wird_nicht_neu_gefragt():
    """„Das Modell wollte hier nichts" ist eine Antwort und keine Lücke.

    Sonst kostete jeder Begriff ohne Katalogtreffer für immer einen
    Modellaufruf — und die Antwort wäre jedes Mal dieselbe.
    """
    gemerkt = {"klopapier": {"begriff": "klopapier", "product_id": None,
                             "gewaehlt": 0, "quelle": ged.MODELL}}
    aufgabe = {"begriff": "Klopapier", "suchbegriffe": ["Klopapier"],
               "aufgehoben": [{"id": 3, "name": "Irgendwas"}]}

    bekannt, offen = ged.teilen([aufgabe], gemerkt)
    assert not offen, "Der Begriff wurde noch einmal gefragt."
    assert bekannt == [(aufgabe, None)]
    assert not ged.auswahl_aus(bekannt).gewaehlt


def test_ohne_kandidaten_wird_nichts_gemerkt(datei, tmp_path):
    """Ein leerer Katalogtreffer ist keine Entscheidung des Modells."""
    con = db.connect(datei)
    try:
        n = ged.merken(con, [{"begriff": "Einhorn",
                              "suchbegriffe": ["Einhorn"], "kandidaten": []}],
                       ged.plan.Auswahl())
    finally:
        con.close()
    assert n == 0
    assert not _wahlen(datei)
