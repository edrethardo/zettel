"""Tests für den Chat im Warenkorb (WB-327, Spec 9).

Kein Browser, kein Netz, keine geweckte Box — dieselbe Bauart wie
`test_web_orders.py`: die Oberfläche wird über `fastapi.testclient` geprüft,
das Modell ist ein Fake mit fester Antwort, der Weckzustand wird
untergeschoben.

Zwei Eigenschaften dieser Ansicht sind leicht zu übersehen und werden deshalb
ausdrücklich geprüft:

* **Ein Blick in den Warenkorb fragt die vLLM-Box nicht.** Der Zustand wird
  nachgeladen. Ohne das hinge jede Korbansicht am health-Timeout — und jeder
  Testlauf ginge ins Netz.
* **Ein Vorschlag ist noch kein Posten.** Erst „Ja" legt ein; die Antwort
  darauf trägt Chat UND Korb, sonst sieht die Nutzerin ihre Zeile nicht.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from picknick import db, orders, recipes
from picknick.assistant import chat as chatmodul
from picknick.assistant import vorschlaege as vorschlagsliste
from picknick.catalog import search
from picknick.llm import wake
from picknick.llm.client import Antwort
from picknick.web import app as webapp

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
HTMX = {"HX-Request": "true"}


class FakeLLM:
    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        self.aufrufe.append(list(nachrichten))
        if not self.antworten:
            raise AssertionError("Mehr Modellaufrufe als vorbereitete Antworten.")
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class Box:
    def __init__(self, zustand=wake.BEDIENT, grund=None):
        self._zustand = zustand
        self._grund = grund
        self.gefragt = 0

    def zustand(self):
        self.gefragt += 1
        if self._zustand == wake.BEDIENT:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(self._zustand, seit_s=12.0, grund=self._grund)


def _pid(pfad, name):
    con = db.connect(pfad)
    try:
        return con.execute("SELECT id FROM product WHERE name = ?",
                           (name,)).fetchone()["id"]
    finally:
        con.close()


def _extract(*paare):
    return json.dumps({"begriffe": [{"begriff": b, "menge": m}
                                    for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel):
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]}, ensure_ascii=False)


def _client(db_datei, tmp_path, *antworten, box=None):
    box = box or Box()
    agent = chatmodul.Chat(FakeLLM(*antworten), wecker=box)
    app = webapp.create_app(db_path=db_datei, image_dir=tmp_path / "bilder",
                            chat=agent)
    return TestClient(app), box


def _inhalt(pfad):
    con = db.connect(pfad)
    try:
        return orders.inhalt(con)
    finally:
        con.close()


# --------------------------------------------------------------------------

def test_warenkorb_zeigt_das_chatfeld(db_datei, tmp_path):
    client, box = _client(db_datei, tmp_path)
    seite = client.get("/warenkorb").text
    assert 'name="satz"' in seite
    assert 'hx-post="/warenkorb/chat"' in seite


def test_warenkorb_fragt_die_box_nicht(db_datei, tmp_path):
    """Der Zustand wird nachgeladen — sonst wartet jeder Blick am Timeout."""
    client, box = _client(db_datei, tmp_path)
    client.get("/warenkorb")
    assert box.gefragt == 0
    assert 'hx-get="/warenkorb/chat/zustand"' in client.get("/warenkorb").text


def test_warenkorb_ansehen_legt_keine_bestellung_an(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path)
    client.get("/warenkorb")
    con = db.connect(db_datei)
    try:
        assert con.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0
    finally:
        con.close()


def test_zustand_zeigt_den_zaehler_beim_aufwachen(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.WACHT_AUF, grund="Weckruf läuft."))
    stueck = client.get("/warenkorb/chat/zustand").text
    assert "wacht auf" in stueck
    assert "Weckruf läuft." in stueck
    # Es fragt sich selbst wieder — sonst bliebe der Zähler stehen.
    assert 'hx-get="/warenkorb/chat/zustand"' in stueck


def test_zustand_schweigt_wenn_die_box_bedient(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path)
    stueck = client.get("/warenkorb/chat/zustand").text
    assert "wacht auf" not in stueck
    assert "hx-trigger" not in stueck


def test_chat_zug_legt_vorschlaege_vor_und_nichts_in_den_korb(db_datei, tmp_path):
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 2)),
                        _choose(("Landmilch", milch, 2)))
    stueck = client.post("/warenkorb/chat", data={"satz": "Landmilch"},
                         headers=HTMX).text

    assert MILCH in stueck
    assert "Landmilch" in stueck          # der Suchbegriff steht an der Zeile
    assert "Rang" in stueck
    assert _inhalt(db_datei) == []        # nichts landet ungefragt im Korb


def test_ja_legt_ein_und_zeigt_den_korb_gleich_mit(db_datei, tmp_path):
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 2)),
                        _choose(("Landmilch", milch, 2)))
    client.post("/warenkorb/chat", data={"satz": "Landmilch"}, headers=HTMX)
    con = db.connect(db_datei)
    sid = con.execute("SELECT id FROM chat_suggestion").fetchone()["id"]
    con.close()

    antwort = client.post(f"/warenkorb/vorschlag/{sid}/entscheiden?decision=kept",
                          headers=HTMX)
    assert antwort.status_code == 200
    # Der Korb kommt als out-of-band-Tausch mit, sonst sieht sie ihn nicht.
    assert 'id="korb" hx-swap-oob="true"' in antwort.text
    assert "im Korb" in antwort.text
    zeilen = _inhalt(db_datei)
    assert [(z["product_id"], z["qty"]) for z in zeilen] == [(milch, 2)]


def test_nein_setzt_removed_und_legt_nichts_ein(db_datei, tmp_path):
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 1)),
                        _choose(("Landmilch", milch, 1)))
    client.post("/warenkorb/chat", data={"satz": "Landmilch"}, headers=HTMX)
    con = db.connect(db_datei)
    sid = con.execute("SELECT id FROM chat_suggestion").fetchone()["id"]
    con.close()

    antwort = client.post(
        f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed", headers=HTMX)
    assert "verworfen" in antwort.text
    assert _inhalt(db_datei) == []
    con = db.connect(db_datei)
    try:
        assert con.execute("SELECT decision FROM chat_suggestion"
                           ).fetchone()["decision"] == "removed"
    finally:
        con.close()


def test_erfundene_id_kommt_auch_ueber_die_oberflaeche_nicht_durch(db_datei,
                                                                  tmp_path):
    """Derselbe Kern wie in `test_assistant.py`, hier am HTTP-Rand."""
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 1)),
                        _choose(("Landmilch", 987654, 1)))
    stueck = client.post("/warenkorb/chat", data={"satz": "Landmilch"},
                         headers=HTMX).text
    assert "Freitext" in stueck
    con = db.connect(db_datei)
    try:
        assert con.execute("SELECT count(*) AS n FROM chat_suggestion"
                           " WHERE product_id IS NOT NULL").fetchone()["n"] == 0
    finally:
        con.close()


def test_schlafende_box_graut_nur_den_chat_aus(db_datei, tmp_path):
    """Spec 11: alles ausser dem Chat bleibt benutzbar."""
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.NICHT_ERREICHBAR, grund="Box antwortet nicht."))
    antwort = client.post("/warenkorb/chat", data={"satz": "Landmilch"},
                          headers=HTMX)
    assert antwort.status_code == 200
    assert "Box antwortet nicht." in antwort.text
    # Der Satz steht noch im Feld — niemand tippt gern zweimal.
    assert 'value="Landmilch"' in antwort.text
    # Und der Katalog ist unbeeindruckt.
    assert client.get("/katalog").status_code == 200
    assert client.post("/katalog/einlegen",
                       data={"product_id": _pid(db_datei, MILCH)},
                       headers=HTMX).status_code == 200


def test_rezeptweg_geht_ohne_modell_auch_ueber_die_oberflaeche(db_datei,
                                                              tmp_path):
    con = db.connect(db_datei)
    recipes.anlegen(con, "Milchreis",
                    zutaten=[{"product_id": _pid(db_datei, MILCH)}])
    con.close()
    # Keine einzige vorbereitete Modellantwort: würde das Modell gefragt,
    # scheiterte der Test.
    client, box = _client(db_datei, tmp_path,
                          box=Box(wake.NICHT_ERREICHBAR, grund="schläft"))
    stueck = client.post("/warenkorb/chat", data={"satz": "Milchreis bitte"},
                         headers=HTMX).text
    assert MILCH in stueck
    assert box.gefragt == 0


def test_ohne_javascript_kommt_die_ganze_seite(db_datei, tmp_path):
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 1)),
                        _choose(("Landmilch", milch, 1)))
    antwort = client.post("/warenkorb/chat", data={"satz": "Landmilch"})
    assert antwort.status_code == 200
    assert "<html" in antwort.text
    assert MILCH in antwort.text


def test_leerer_satz_wird_erklaert_und_bricht_nichts(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path)
    antwort = client.post("/warenkorb/chat", data={"satz": "  "}, headers=HTMX)
    assert antwort.status_code == 200
    assert "leer geht nicht" in antwort.text


def test_alles_uebernehmen_legt_alle_offenen_ein(db_datei, tmp_path):
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path,
                        _extract(("Landmilch", 1), ("Zahnstocher", 1)),
                        _choose(("Landmilch", milch, 1)))
    client.post("/warenkorb/chat", data={"satz": "Landmilch und Zahnstocher"},
                headers=HTMX)
    con = db.connect(db_datei)
    mid = con.execute("SELECT id FROM chat_message WHERE role = 'assistant'"
                      ).fetchone()["id"]
    con.close()

    client.post(f"/warenkorb/chat/{mid}/alle?decision=kept", headers=HTMX)
    namen = sorted(z["name"] for z in _inhalt(db_datei))
    assert namen == sorted([MILCH, "Zahnstocher"])


# --------------------------------------------------------------------------
# „Nein" klappt die Alternativen auf (WB-359)
#
# Der Katalog dieses Moduls ist die Milch-Fixture: „Milch" legt ein gutes
# Dutzend Kandidaten vor, und genau die sollen beim „Nein" erscheinen — ohne
# eine zweite Suche und ohne einen Umweg über den Katalog.

def _milch_zug(db_datei, tmp_path):
    """Ein Zug über „Milch", bei dem das Modell den ERSTEN Kandidaten nimmt.

    Die id kommt aus der Suche und nicht aus einem festen Namen: welches
    Produkt bei „Milch" oben steht, entscheidet die Wortstufe und bm25 — wer
    das festschreibt, prüft irgendwann die Rangfolge statt der Korrektur.
    """
    con = db.connect(db_datei)
    pid = search.search(con, "Milch", limit=1)[0]["id"]
    con.close()
    client, _ = _client(db_datei, tmp_path, _extract(("Milch", 1)),
                        _choose(("Milch", pid, 1)))
    client.post("/warenkorb/chat", data={"satz": "Milch"}, headers=HTMX)
    con = db.connect(db_datei)
    sid = con.execute("SELECT id FROM chat_suggestion ORDER BY id"
                      ).fetchone()["id"]
    con.close()
    return client, sid, pid


def _alternativen(db_datei, sid):
    con = db.connect(db_datei)
    try:
        return vorschlagsliste.alternativen(con, sid)
    finally:
        con.close()


def test_nein_klappt_die_alternativen_auf(db_datei, tmp_path):
    """Und zwar aufgeklappt: die Zeile, die gerade verworfen wurde."""
    client, sid, _ = _milch_zug(db_datei, tmp_path)

    stueck = client.post(
        f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed",
        headers=HTMX).text

    assert "<details class=\"alternativen\" open>" in stueck
    namen = [a["name"] for a in _alternativen(db_datei, sid)]
    assert len(namen) >= 5
    for name in namen:
        assert name in stueck
    # Bild, Menge, Preis — wie im Katalog. Und ein Tipp je Alternative.
    assert f'/warenkorb/vorschlag/{sid}/statt?produkt_id=' in stueck
    assert "Nichts davon" in stueck


def test_die_alternativen_kosten_keine_zweite_suche(db_datei, tmp_path,
                                                   monkeypatch):
    """Sie kommen aus `chat_kandidat`, nicht aus dem Katalog.

    Die Suche wird nach dem Zug scharf geschaltet: rührt das Aufklappen sie
    an, fliegt der Test.
    """
    client, sid, _ = _milch_zug(db_datei, tmp_path)

    def verboten(*a, **k):
        raise AssertionError("Es wurde ein zweites Mal gesucht.")

    monkeypatch.setattr(webapp.search, "search", verboten)
    monkeypatch.setattr(webapp.search, "suche_kette", verboten)
    antwort = client.post(
        f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed",
        headers=HTMX)
    assert antwort.status_code == 200
    assert "alternativen" in antwort.text


def test_ein_tipp_auf_die_alternative_legt_sie_statt_des_vorschlags_ein(
        db_datei, tmp_path):
    client, sid, vorgeschlagen = _milch_zug(db_datei, tmp_path)
    client.post(f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed",
                headers=HTMX)
    andere = _alternativen(db_datei, sid)[0]

    antwort = client.post(
        f"/warenkorb/vorschlag/{sid}/statt?produkt_id={andere['id']}",
        headers=HTMX)

    assert antwort.status_code == 200
    # Der Korb kommt out-of-band mit, sonst sieht sie ihre Zeile nicht.
    assert 'id="korb" hx-swap-oob="true"' in antwort.text
    assert [(z["product_id"], z["qty"]) for z in _inhalt(db_datei)] == [
        (andere["id"], 1)]
    # Und die Korrektur steht als Korrektur da, nicht als zweiter Vorschlag.
    assert "statt" in antwort.text
    con = db.connect(db_datei)
    try:
        zeilen = con.execute(
            "SELECT product_id, decision, corrected_from FROM chat_suggestion"
            " ORDER BY id").fetchall()
    finally:
        con.close()
    assert [tuple(z) for z in zeilen] == [
        (vorgeschlagen, "removed", None), (andere["id"], "kept", sid)]


def test_ein_produkt_ausserhalb_der_vorlage_kommt_nicht_durch(db_datei,
                                                              tmp_path):
    """Dieselbe Regel wie gegen erfundene IDs des Modells, hier am HTTP-Rand."""
    client, sid, _ = _milch_zug(db_datei, tmp_path)
    client.post(f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed",
                headers=HTMX)

    antwort = client.post(
        f"/warenkorb/vorschlag/{sid}/statt?produkt_id=987654", headers=HTMX)

    assert antwort.status_code == 200
    assert "stand nicht in der Vorlage" in antwort.text
    assert _inhalt(db_datei) == []


def test_nichts_davon_fuehrt_zu_einer_freitextzeile(db_datei, tmp_path):
    """Der Ausgang bei einer Katalog-Lücke — im echten Katalog „Sellerie"."""
    client, sid, _ = _milch_zug(db_datei, tmp_path)
    client.post(f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed",
                headers=HTMX)

    antwort = client.post(f"/warenkorb/vorschlag/{sid}/freitext",
                          data={"text": "Rohmilch vom Hof"}, headers=HTMX)

    assert antwort.status_code == 200
    assert "Rohmilch vom Hof" in antwort.text
    assert [z["free_text"] for z in _inhalt(db_datei)] == ["Rohmilch vom Hof"]


def test_ohne_alternativen_bricht_die_ansicht_nicht(db_datei, tmp_path):
    """Ein Begriff ohne einen einzigen Treffer — der Weg bleibt trotzdem offen."""
    client, _ = _client(db_datei, tmp_path, _extract(("Zahnstocher", 1)),
                        _choose())
    client.post("/warenkorb/chat", data={"satz": "Zahnstocher"}, headers=HTMX)
    con = db.connect(db_datei)
    sid = con.execute("SELECT id FROM chat_suggestion").fetchone()["id"]
    con.close()

    stueck = client.post(
        f"/warenkorb/vorschlag/{sid}/entscheiden?decision=removed",
        headers=HTMX).text
    assert "eine Lücke im Katalog" in stueck
    assert "Nichts davon" in stueck


def test_die_tap_ziele_der_alternativen_sind_gross_genug(db_datei, tmp_path):
    """44 px, und die Liste scrollt in sich, statt die Seite zu sprengen."""
    stil = (Path(webapp.__file__).parent / "static" / "stil.css").read_text(
        encoding="utf-8")
    assert "--tap: 44px" in stil
    for regel in (".alternativen > summary", ".selbst input"):
        block = stil.split(regel)[1].split("}")[0]
        assert "min-height: var(--tap)" in block
    assert "max-height: 60vh" in stil.split(".altliste")[1].split("}")[0]


# --------------------------------------------------------------------------
# Der Rückweg (WB-361)
#
# Über HTTP, weil hier der Unterschied sitzt: die Logik konnte `offen` immer
# schon, es fragte nur niemand danach.

def _sid(db_datei):
    con = db.connect(db_datei)
    try:
        return con.execute("SELECT id FROM chat_suggestion ORDER BY id"
                           ).fetchone()["id"]
    finally:
        con.close()


def _entscheiden(client, sid, decision):
    return client.post(
        f"/warenkorb/vorschlag/{sid}/entscheiden?decision={decision}",
        headers=HTMX)


def test_die_entschiedene_zeile_bietet_den_rueckweg_an(db_datei, tmp_path):
    """Beide Seiten, dieselbe Geste, derselbe Ort."""
    milch = _pid(db_datei, MILCH)
    for decision, marke in (("kept", "im Korb"), ("removed", "verworfen")):
        client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 1)),
                            _choose(("Landmilch", milch, 1)))
        client.post("/warenkorb/chat", data={"satz": "Landmilch"},
                    headers=HTMX)
        con = db.connect(db_datei)
        sid = con.execute("SELECT id FROM chat_suggestion ORDER BY id DESC"
                          ).fetchone()["id"]
        con.close()

        stueck = _entscheiden(client, sid, decision).text

        assert marke in stueck
        assert "rückgängig" in stueck
        assert (f'hx-post="/warenkorb/vorschlag/{sid}/entscheiden'
                '?decision=offen"') in stueck


def test_ja_ruecknahme_ja_legt_auch_ueber_die_oberflaeche_nur_einmal_ein(
        db_datei, tmp_path):
    """Die Falle des Tickets, am HTTP-Rand: der Schutz gegen den Doppeltipp
    darf nicht am Vergleich der letzten Entscheidung hängen."""
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 2)),
                        _choose(("Landmilch", milch, 2)))
    client.post("/warenkorb/chat", data={"satz": "Landmilch"}, headers=HTMX)
    sid = _sid(db_datei)

    _entscheiden(client, sid, "kept")
    _entscheiden(client, sid, "offen")
    _entscheiden(client, sid, "kept")

    assert [(z["product_id"], z["qty"]) for z in _inhalt(db_datei)] == [
        (milch, 2)]


def test_die_ruecknahme_sagt_dass_die_zeile_im_korb_bleibt(db_datei, tmp_path):
    """Der Korb wird bewusst nicht angerührt — verschweigen darf die
    Oberfläche das nicht, sonst sucht sie die Zeile dort vergeblich."""
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Landmilch", 1)),
                        _choose(("Landmilch", milch, 1)))
    client.post("/warenkorb/chat", data={"satz": "Landmilch"}, headers=HTMX)
    sid = _sid(db_datei)
    _entscheiden(client, sid, "kept")

    stueck = _entscheiden(client, sid, "offen").text

    assert "die Zeile bleibt im Korb" in stueck
    assert "Löschknopf" in stueck
    assert [(z["product_id"], z["qty"]) for z in _inhalt(db_datei)] == [
        (milch, 1)]
    # Und die Zeile ist wieder entscheidbar: beide Knöpfe stehen da.
    assert (f'/warenkorb/vorschlag/{sid}/entscheiden?decision=kept'
            in stueck)
    assert (f'/warenkorb/vorschlag/{sid}/entscheiden?decision=removed'
            in stueck)


def test_ein_zurueckgenommenes_nein_zeigt_die_alternativen_nicht_mehr(
        db_datei, tmp_path):
    """Die Zeile ist wieder unentschieden — dann gibt es auch nichts zu
    korrigieren. Der Aufklapper gehört zum „Nein", nicht zur Zeile."""
    client, sid, _ = _milch_zug(db_datei, tmp_path)
    _entscheiden(client, sid, "removed")

    stueck = _entscheiden(client, sid, "offen").text

    assert "Nichts davon" not in stueck
    assert "rückgängig" not in stueck        # offen ist nichts zurückzunehmen
    assert _inhalt(db_datei) == []


def test_eine_korrektur_laesst_sich_ueber_die_oberflaeche_zuruecknehmen(
        db_datei, tmp_path):
    """Und danach steht die Alternativenliste wieder offen."""
    client, sid, _ = _milch_zug(db_datei, tmp_path)
    _entscheiden(client, sid, "removed")
    andere = _alternativen(db_datei, sid)[0]
    client.post(f"/warenkorb/vorschlag/{sid}/statt?produkt_id={andere['id']}",
                headers=HTMX)
    con = db.connect(db_datei)
    korrektur = con.execute(
        "SELECT id FROM chat_suggestion WHERE corrected_from = ?",
        (sid,)).fetchone()["id"]
    con.close()

    stueck = _entscheiden(client, korrektur, "offen").text

    # Der Korb behält die Zeile (dieselbe Begründung wie beim „Ja"), …
    assert [z["product_id"] for z in _inhalt(db_datei)] == [andere["id"]]
    # … und die Wahl steht wieder offen.
    assert f'/warenkorb/vorschlag/{sid}/statt?produkt_id=' in stueck
    assert "Nichts davon" in stueck


def test_das_tap_ziel_des_rueckwegs_ist_gross_genug(db_datei, tmp_path):
    """Kleiner als die Hauptentscheidung — aber nicht kleiner als der Daumen.

    Die Grösse kommt von `.mini` (44 px); `.mini.zurueck` nimmt nur Gewicht
    und Farbe zurück und darf sie nicht überschreiben.
    """
    stil = (Path(webapp.__file__).parent / "static" / "stil.css").read_text(
        encoding="utf-8")
    block = stil.split(".mini.zurueck")[1].split("}")[0]
    assert "min-height" not in block and "min-width" not in block
    assert "font-size: 14px" in block
    grund = stil.split(".mini {")[1].split("}")[0]
    assert "min-height: var(--tap)" in grund


# --------------------------------------------------------------------------
# Oberbegriffe auffächern (WB-368)
#
# Ein eigener Katalog: die Milch-Vorlage der übrigen Tests hat keine
# L1-Kategorie mit genug Sorten, und eine Auffächerung braucht genau das.

AUFSCHNITT = [
    ("sal1", "Levoni Salami Milano", "Aufschnitt", "Salami"),
    ("sal2", "Simonini Salami Napoli", "Aufschnitt", "Salami"),
    ("koc1", "Gutfried Kochschinken", "Aufschnitt", "Kochschinken"),
    ("bru1", "Jagdwurst Aufschnitt", "Aufschnitt", "Brühwurst"),
    ("gef1", "Hähnchenbrust Aufschnitt", "Aufschnitt", "Geflügelwurst"),
]


def _aufschnitt(con):
    for external_id, name, l1, l2 in AUFSCHNITT:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1, category_l2)"
            " VALUES ('knuspr', ?, ?, 249, '100 g', ?, ?)",
            (external_id, name, l1, l2))
    con.commit()


@pytest.fixture
def aufschnitt_db(vorlagen, tmp_path):
    return vorlagen.datei(tmp_path / "aufschnitt.db", "web_aufschnitt",
                          _aufschnitt)


def _sorten_zug(aufschnitt_db, tmp_path, *antworten):
    """Der Zug, der „Aufschnitt" auffächert — ohne einen Modellaufruf."""
    client, _ = _client(aufschnitt_db, tmp_path, *antworten)
    seite = client.post("/warenkorb/chat", data={"satz": "Aufschnitt"},
                        headers=HTMX).text
    con = db.connect(aufschnitt_db)
    mid = con.execute("SELECT max(id) AS id FROM chat_message"
                      " WHERE role = 'assistant'").fetchone()["id"]
    con.close()
    return client, mid, seite


def test_aufschnitt_zeigt_die_sorten_als_kaestchen(aufschnitt_db, tmp_path):
    """Vier Sorten mit echter Stückzahl — und ein Weg daran vorbei."""
    _, mid, seite = _sorten_zug(aufschnitt_db, tmp_path)

    assert "ist ein Oberbegriff" in seite
    for sorte in ("Salami", "Kochschinken", "Brühwurst", "Geflügelwurst"):
        assert f'name="sorte" value="{sorte}"' in seite
    assert f'hx-post="/warenkorb/chat/{mid}/sorten"' in seite
    # Punkt 5 des Tickets: keine Sackgasse.
    assert "Überspringen" in seite


def test_mehrere_sorten_kommen_als_mehrere_werte_an(aufschnitt_db, tmp_path):
    """Zwei Kästchen, zwei Vorschläge. Ein Wörterbuch behielte nur eines."""
    client, mid, _ = _sorten_zug(aufschnitt_db, tmp_path)
    con = db.connect(aufschnitt_db)
    salami = con.execute("SELECT id FROM product WHERE external_id = 'sal1'"
                         ).fetchone()["id"]
    schinken = con.execute("SELECT id FROM product WHERE external_id = 'koc1'"
                           ).fetchone()["id"]
    con.close()
    client.app.state.chat = chatmodul.Chat(
        FakeLLM(_choose(("Salami", salami, 1), ("Kochschinken", schinken, 1))),
        wecker=Box())

    seite = client.post(f"/warenkorb/chat/{mid}/sorten",
                        data={"sorte": ["Salami", "Kochschinken"]},
                        headers=HTMX).text

    assert "Levoni Salami Milano" in seite
    assert "Gutfried Kochschinken" in seite
    assert "2 Sorten aus „Aufschnitt“" in seite


def test_ohne_kreuz_wird_direkt_gesucht(aufschnitt_db, tmp_path):
    """Überspringen heisst: die Suche nach dem Wort, wie vor dem Ticket."""
    client, mid, _ = _sorten_zug(aufschnitt_db, tmp_path)
    con = db.connect(aufschnitt_db)
    jagdwurst = con.execute("SELECT id FROM product WHERE external_id = 'bru1'"
                            ).fetchone()["id"]
    con.close()
    client.app.state.chat = chatmodul.Chat(
        FakeLLM(json.dumps({"gericht": None,
                            "begriffe": [{"suchbegriffe": ["Aufschnitt"],
                                          "menge": 1}]}),
                _choose(("Aufschnitt", jagdwurst, 1))),
        wecker=Box())

    seite = client.post(f"/warenkorb/chat/{mid}/sorten", headers=HTMX).text

    assert "Jagdwurst Aufschnitt" in seite
    # Und nicht noch einmal dieselbe Frage.
    assert seite.count("ist ein Oberbegriff") == 1


def test_eine_nicht_angebotene_sorte_kommt_nicht_durch(aufschnitt_db, tmp_path):
    """Von Hand gebaute Formularwerte werden verworfen, nicht abgefragt."""
    client, mid, _ = _sorten_zug(aufschnitt_db, tmp_path)
    con = db.connect(aufschnitt_db)
    jagdwurst = con.execute("SELECT id FROM product WHERE external_id = 'bru1'"
                            ).fetchone()["id"]
    con.close()
    client.app.state.chat = chatmodul.Chat(
        FakeLLM(json.dumps({"gericht": None,
                            "begriffe": [{"suchbegriffe": ["Aufschnitt"],
                                          "menge": 1}]}),
                _choose(("Aufschnitt", jagdwurst, 1))),
        wecker=Box())

    seite = client.post(f"/warenkorb/chat/{mid}/sorten",
                        data={"sorte": "Kaviar"}, headers=HTMX).text

    # Kein „1 Sorte aus …": die erfundene Sorte fiel weg, und übrig blieb der
    # Weg für „nichts ausgewählt" — die direkte Suche.
    assert "Kaviar" not in seite
    assert "Jagdwurst Aufschnitt" in seite


def test_die_tap_ziele_der_sortenliste_sind_gross_genug(db_datei, tmp_path):
    """Die Kästchen werden mit dem Daumen getroffen, nicht mit der Maus."""
    stil = (Path(webapp.__file__).parent / "static" / "stil.css").read_text(
        encoding="utf-8")
    block = stil.split(".sortenliste label")[1].split("}")[0]
    assert "min-height: var(--tap)" in block


# --------------------------------------------------------------------------
# WB-372: was ein Tipp kostet, was eingeklappt ist, und der Weg heraus
#
# Der gemessene Anlass: 34 Chatzeilen, 157 Vorschläge, und JEDER Tipp auf „Ja"
# übertrug den kompletten Verlauf — 258.890 Bytes gegen den laufenden Shop.
# Genau diese Tipps macht man in Serie durch eine Liste von 157 Zeilen, am
# Telefon über Mobilfunk.
#
# Der Verlauf wird hier direkt geschrieben statt über das Modell erfragt: für
# zehn Züge bräuchte es zwanzig vorbereitete Antworten, und geprüft würde
# damit der Fake und nicht die Ansicht.


def _langer_verlauf(db_datei, zuege=6, je_zug=3):
    """Schreibt `zuege` Züge mit je `je_zug` Vorschlägen. Gibt deren ids."""
    con = db.connect(db_datei)
    try:
        produkte = [r["id"] for r in
                    con.execute("SELECT id FROM product ORDER BY id LIMIT 8")]
        korb = orders.warenkorb(con)
        ids = []
        for zug in range(zuege):
            vorschlagsliste.nachricht(con, korb, vorschlagsliste.ROLLE_NUTZERIN,
                                      f"Frage aus Zug {zug}")
            mid = vorschlagsliste.nachricht(
                con, korb, vorschlagsliste.ROLLE_AGENT, f"Antwort zu Zug {zug}")
            ids.append([vorschlagsliste.vorschlag(
                con, mid, product_id=produkte[(zug + i) % len(produkte)],
                search_term=f"Begriff {zug}-{i}") for i in range(je_zug)])
        return ids
    finally:
        con.close()


def _chatteil(text):
    """Der `#chat`-Block einer Vollseite — das, was vorher je Tipp neu ging."""
    return text[text.find('<section class="chat"'):]


def test_ein_ja_uebertraegt_nicht_mehr_den_ganzen_verlauf(db_datei, tmp_path):
    """Der Grössenvergleich, um den es im Ticket geht."""
    ids = _langer_verlauf(db_datei)
    client, _ = _client(db_datei, tmp_path)
    seite = _chatteil(client.get("/warenkorb?verlauf=alles").text)

    antwort = _entscheiden(client, ids[-1][0], "kept").text

    # Eine Zeile plus Nachträge statt des Verlaufs. Der Faktor ist grosszügig
    # gewählt: geprüft wird die Grössenordnung, nicht eine Byte-Zahl, die bei
    # jeder Änderung an der Vorlage nachgezogen werden müsste.
    assert len(antwort) < len(seite) / 4
    # Und zwar, weil die anderen Züge nicht mitkommen — nicht, weil zufällig
    # gerade wenig dranhing.
    assert "Antwort zu Zug 0" not in antwort
    assert "Antwort zu Zug 5" not in antwort


def test_der_tipp_taucht_genau_die_eine_zeile_aus(db_datei, tmp_path):
    ids = _langer_verlauf(db_datei)
    sid, geschwister = ids[-1][0], ids[-1][1]
    client, _ = _client(db_datei, tmp_path)

    antwort = _entscheiden(client, sid, "kept").text

    assert f'id="vorschlag-{sid}"' in antwort
    assert f'id="vorschlag-{geschwister}"' not in antwort
    # Der Korb und die Zahl im Kopf kommen out-of-band mit, sonst sieht sie
    # nicht, dass ihr „Ja" etwas getan hat.
    assert 'id="korb" hx-swap-oob="true"' in antwort
    assert 'id="korb-anzahl"' in antwort


def test_der_tipp_nimmt_den_sammelknopf_mit_wenn_nichts_mehr_offen_ist(
        db_datei, tmp_path):
    """Sonst stünde unter einem fertigen Zug ein Knopf, der nichts mehr tut."""
    ids = _langer_verlauf(db_datei, zuege=1, je_zug=2)
    client, _ = _client(db_datei, tmp_path)

    erste = _entscheiden(client, ids[0][0], "kept").text
    assert "Alles übernehmen" in erste          # eine Zeile ist noch offen
    letzte = _entscheiden(client, ids[0][1], "kept").text
    assert "Alles übernehmen" not in letzte


# --------------------------------------------------------------------------
# Der eingeklappte Teil

def test_aeltere_zuege_werden_nicht_gerendert(db_datei, tmp_path):
    _langer_verlauf(db_datei, zuege=6, je_zug=2)
    client, _ = _client(db_datei, tmp_path)

    seite = client.get("/warenkorb").text

    # Die letzten drei Züge stehen da, die älteren nicht.
    for zug in (3, 4, 5):
        assert f"Antwort zu Zug {zug}" in seite
    for zug in (0, 1, 2):
        assert f"Antwort zu Zug {zug}" not in seite
    # Und es wird gesagt, wie viel fehlt — mit Zahlen.
    assert "3 ältere Züge" in seite
    assert "6 Vorschläge" in seite


def test_ein_eingeklappter_zug_ist_aufklappbar_und_traegt_seine_entscheidungen(
        db_datei, tmp_path):
    """Sie soll nachsehen können, was sie vor zehn Zügen entschieden hat."""
    ids = _langer_verlauf(db_datei, zuege=6, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    # Im ÄLTESTEN Zug entscheiden, danach klappt er weg.
    _entscheiden(client, ids[0][0], "kept")
    _entscheiden(client, ids[0][1], "removed")
    assert "Antwort zu Zug 0" not in client.get("/warenkorb").text

    alles = client.get("/warenkorb?verlauf=alles").text

    assert "Antwort zu Zug 0" in alles
    # Die Entscheidungen sind da, wo sie waren.
    assert alles.count("im Korb") >= 1
    assert "verworfen" in alles
    # Und über HTMX kommt dasselbe als Bruchstück.
    stueck = client.get("/warenkorb/chat?verlauf=alles", headers=HTMX).text
    assert "Antwort zu Zug 0" in stueck
    assert "<html" not in stueck.lower()


def test_einklappen_loescht_keine_entscheidung(db_datei, tmp_path):
    """Eine ANZEIGEgrenze — an der Datenbank ändert sie nichts."""
    ids = _langer_verlauf(db_datei, zuege=6, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    _entscheiden(client, ids[0][0], "kept")
    client.get("/warenkorb")

    con = db.connect(db_datei)
    try:
        assert con.execute("SELECT count(*) AS n FROM chat_message"
                           ).fetchone()["n"] == 12
        assert con.execute("SELECT count(*) AS n FROM chat_suggestion"
                           ).fetchone()["n"] == 12
        assert con.execute(
            "SELECT decision FROM chat_suggestion WHERE id = ?",
            (ids[0][0],)).fetchone()["decision"] == "kept"
    finally:
        con.close()


def test_ein_kurzer_verlauf_bekommt_keinen_aufklapper(db_datei, tmp_path):
    """„0 ältere Züge anzeigen" wäre ein Knopf ohne Gegenstand."""
    _langer_verlauf(db_datei, zuege=2, je_zug=1)
    client, _ = _client(db_datei, tmp_path)
    seite = client.get("/warenkorb").text
    assert "ältere Züge" not in seite
    assert "Antwort zu Zug 0" in seite


# --------------------------------------------------------------------------
# Der Verlauf lässt sich leeren — ohne eine Bestellung abzuschicken

def _chatzeilen(db_datei):
    con = db.connect(db_datei)
    try:
        return con.execute("SELECT count(*) AS n FROM chat_message"
                           ).fetchone()["n"]
    finally:
        con.close()


def test_verlauf_leeren_fragt_erst_nach(db_datei, tmp_path):
    _langer_verlauf(db_datei, zuege=2, je_zug=2)
    client, _ = _client(db_datei, tmp_path)

    frage = client.post("/warenkorb/chat/leeren", headers=HTMX).text

    assert "wirklich löschen?" in frage
    assert "Eval-Labels" in frage            # sie sagt, was auf dem Spiel steht
    assert "4 Vorschläge" in frage
    assert "/warenkorb/chat/leeren?ja=1" in frage
    assert _chatzeilen(db_datei) == 4         # gefragt, nicht gelöscht


def test_verlauf_leeren_laesst_sich_abbrechen(db_datei, tmp_path):
    _langer_verlauf(db_datei, zuege=2, je_zug=1)
    client, _ = _client(db_datei, tmp_path)
    client.post("/warenkorb/chat/leeren", headers=HTMX)

    zurueck = client.post("/warenkorb/chat/leeren?ja=0", headers=HTMX).text

    assert "wirklich löschen?" not in zurueck
    assert _chatzeilen(db_datei) == 4


def test_verlauf_leeren_loescht_und_laesst_den_korb_unangetastet(db_datei,
                                                                 tmp_path):
    """Der Kern: sie will den Verlauf loswerden, nicht ihren Einkauf."""
    ids = _langer_verlauf(db_datei, zuege=2, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    _entscheiden(client, ids[0][0], "kept")          # eine Zeile in den Korb
    vorher = _inhalt(db_datei)
    assert len(vorher) == 1

    antwort = client.post("/warenkorb/chat/leeren?ja=1", headers=HTMX).text

    assert _chatzeilen(db_datei) == 0
    assert "Der Verlauf ist gelöscht" in antwort
    assert "4 Vorschlägen" in antwort               # die Zahlen stehen dabei
    # Der Korb liegt unberührt da — die Posten und ihre ids.
    assert [z["id"] for z in _inhalt(db_datei)] == [z["id"] for z in vorher]


def test_verlauf_leeren_raeumt_auch_alles_ab_was_daran_haengt(db_datei,
                                                              tmp_path):
    """Vorschläge und Kandidaten gehen per CASCADE mit — nichts bleibt liegen."""
    _milch_zug(db_datei, tmp_path)
    client, _ = _client(db_datei, tmp_path)
    client.post("/warenkorb/chat/leeren?ja=1", headers=HTMX)

    con = db.connect(db_datei)
    try:
        for tabelle in ("chat_message", "chat_suggestion", "chat_kandidat"):
            assert con.execute(f"SELECT count(*) AS n FROM {tabelle}"
                               ).fetchone()["n"] == 0, tabelle
    finally:
        con.close()


def test_der_leere_korb_mit_vollem_verlauf_ist_keine_sackgasse_mehr(db_datei,
                                                                    tmp_path):
    """Genau der Zustand aus dem Ticket: 0 Posten, viele Chatzeilen.

    Der einzige Reset war das Abschicken, und das wirft bei leerem Korb. Sie
    kam da nur heraus, indem sie etwas einlegte und eine Bestellung
    abschickte, die sie nicht wollte.
    """
    _langer_verlauf(db_datei, zuege=3, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    assert _inhalt(db_datei) == []
    # Abschicken ist versperrt …
    abgelehnt = client.post("/warenkorb/abschicken", headers=HTMX)
    assert "Der Warenkorb ist leer" in abgelehnt.text

    # … das Leeren nicht.
    client.post("/warenkorb/chat/leeren?ja=1", headers=HTMX)

    assert _chatzeilen(db_datei) == 0
    con = db.connect(db_datei)
    try:
        # Und die Bestellung ist weiter ein Entwurf: nichts wurde abgeschickt.
        assert con.execute("SELECT state FROM orders").fetchone()["state"] \
            == "draft"
    finally:
        con.close()


def test_ohne_verlauf_gibt_es_nichts_zu_leeren(db_datei, tmp_path):
    """Der Knopf steht nur da, wo er etwas tut."""
    client, _ = _client(db_datei, tmp_path)
    assert "Verlauf leeren" not in client.get("/warenkorb").text
    _langer_verlauf(db_datei, zuege=1, je_zug=1)
    assert "Verlauf leeren" in client.get("/warenkorb").text


def test_jedes_tauschziel_gibt_es_auch_auf_der_seite(db_datei, tmp_path):
    """Ein `hx-target`, das ins Leere zeigt, tut nichts — und sagt es nicht.

    Seit WB-372 zielt nicht mehr alles auf `#chat`, sondern auf `#zug-N` und
    `#vorschlag-N`. Ein Tippfehler in einer dieser ids wäre in keiner anderen
    Prüfung zu sehen: die Antwort käme mit 200 zurück, HTMX fände nichts zum
    Tauschen, und der Knopf sähe aus wie kaputt. Deshalb wird hier stumpf
    verglichen — jedes Ziel gegen alle ids, die die Seite wirklich trägt.
    """
    ids = _langer_verlauf(db_datei, zuege=2, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    # Ein „Nein" bringt die Alternativenliste mit ihren eigenen Zielen dazu.
    _entscheiden(client, ids[-1][0], "removed")

    seite = client.get("/warenkorb").text
    vorhanden = set(re.findall(r'id="([\w-]+)"', seite))
    ziele = set(re.findall(r'hx-target="#([\w-]+)"', seite))

    assert ziele, "die Seite hat gar keine Tauschziele — der Test misst nichts"
    assert ziele <= vorhanden, f"zeigt ins Leere: {sorted(ziele - vorhanden)}"
    # Und die drei Grössen kommen wirklich alle vor.
    assert "chat" in ziele
    assert any(z.startswith("zug-") for z in ziele)
    assert any(z.startswith("vorschlag-") for z in ziele)


# --------------------------------------------------------------------------
# WB-378: der Chat sagt, dass etwas passiert — und führt nicht in Sackgassen
#
# Vier Stellen, an denen die Oberfläche vorher schwieg. Sie hängen zusammen:
# überall geht es darum, dass ein Tipp am Telefon sichtbar etwas auslöst, und
# zwar DORT, wo der Daumen gerade ist. Der Verlauf ist tausende Zeilen lang;
# was am Seitenkopf oder am Seitenfuss blinkt, ist beim Tippen aus dem Bild.

FORMULAR = re.compile(r"<form\b[^>]*>", re.S)


def _formulare(text):
    """Alle Formulare eines Stücks, als Rohtext ihres öffnenden Tags."""
    return FORMULAR.findall(text)


def test_jedes_chatformular_sperrt_seinen_knopf_beim_antippen(db_datei,
                                                              tmp_path):
    """Ohne sichtbares Warten tippt man ein zweites Mal.

    Vor WB-378 trugen das nur das Sendefeld und die Sortenauswahl; die
    Entscheidungsknöpfe — die man in Serie durch eine Liste von 157 Zeilen
    drückt — trugen es nicht. Geprüft wird stumpf über alle Formulare des
    Chats, damit ein neu dazukommendes nicht wieder durchrutscht.
    """
    ids = _langer_verlauf(db_datei, zuege=2, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    # Ein „Nein" bringt Alternativen und Freitext mit ihren Formularen dazu.
    _entscheiden(client, ids[-1][0], "removed")

    chat = _chatteil(client.get("/warenkorb").text)
    formulare = [f for f in _formulare(chat) if "hx-post" in f or "hx-get" in f]

    assert formulare, "der Chat hat gar keine Formulare — der Test misst nichts"
    ohne = [f for f in formulare if "hx-disabled-elt" not in f]
    assert not ohne, f"ohne Knopfsperre: {ohne}"


def test_die_entscheidungsknoepfe_zeigen_genau_das_stueck_das_sich_aendert(
        db_datei, tmp_path):
    """Der Indikator ist das Tauschziel — nicht der Streifen am Seitenfuss.

    `#chat-laeuft` steht unter dem Eingabefeld, also am Ende von siebentausend
    Zeilen. Für ein „Ja" mitten in der Liste ist er keine Rückmeldung.
    """
    ids = _langer_verlauf(db_datei, zuege=1, je_zug=2)
    sid = ids[0][0]
    client, _ = _client(db_datei, tmp_path)

    chat = _chatteil(client.get("/warenkorb").text)
    zeile = chat.split(f'id="vorschlag-{sid}"', 1)[1]
    for entscheidung in ("kept", "removed"):
        block = zeile.split(f"decision={entscheidung}", 1)[1].split("</form>", 1)[0]
        assert f'hx-indicator="#vorschlag-{sid}"' in block
        assert 'hx-disabled-elt="find button"' in block

    # Und der Rückweg an der entschiedenen Zeile ebenso.
    antwort = _entscheiden(client, sid, "kept").text
    assert f'hx-indicator="#vorschlag-{sid}"' in antwort


def test_jeder_indikator_gibt_es_auch_auf_der_seite(db_datei, tmp_path):
    """Dieselbe stumpfe Prüfung wie für die Tauschziele.

    Ein `hx-indicator`, der ins Leere zeigt, blendet nichts ein — und sagt es
    nicht: die Anfrage läuft normal, der Knopf sieht bloss weiter tot aus.
    """
    ids = _langer_verlauf(db_datei, zuege=2, je_zug=2)
    client, _ = _client(db_datei, tmp_path)
    _entscheiden(client, ids[-1][0], "removed")

    seite = client.get("/warenkorb").text
    vorhanden = set(re.findall(r'id="([\w-]+)"', seite))
    indikatoren = set(re.findall(r'hx-indicator="#([\w-]+)"', seite))

    assert indikatoren, "die Seite hat gar keine Indikatoren"
    assert indikatoren <= vorhanden, \
        f"zeigt ins Leere: {sorted(indikatoren - vorhanden)}"
    assert any(i.startswith("vorschlag-") for i in indikatoren)
    assert any(i.startswith("zug-") for i in indikatoren)


def test_die_schlafende_box_meldet_sich_am_eingabefeld(db_datei, tmp_path):
    """`ChatNichtVerfuegbar` setzte nur `zustand` — am Formular kam nichts an.

    Das Band mit dem Grund steht ÜBER dem ganzen Verlauf. Wer unten „Fragen"
    tippt, während die Box schläft, sah vorher: nichts.
    """
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.NICHT_ERREICHBAR, grund="Box antwortet nicht."))
    antwort = client.post("/warenkorb/chat", data={"satz": "Landmilch"},
                          headers=HTMX).text

    unten = antwort.split('id="chat-fehler-unten"', 1)[1].split("</div>", 1)[0]
    assert "Das Modell antwortet gerade nicht" in unten
    # Und sie steht wirklich am Formular, nicht wieder oben.
    assert antwort.index('id="chat-fehler-unten"') < antwort.index('class="chatform"')
    assert antwort.index('id="chat-fehler"') < antwort.index('id="chat-fehler-unten"')
    # Der technische Grund bleibt am Band — er gehört nicht an den Knopf.
    assert "Box antwortet nicht." in antwort


def test_die_wachende_box_sagt_am_feld_dass_es_gleich_geht(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.WACHT_AUF, grund="Weckruf läuft."))
    antwort = client.post("/warenkorb/chat", data={"satz": "Landmilch"},
                          headers=HTMX).text
    unten = antwort.split('id="chat-fehler-unten"', 1)[1].split("</div>", 1)[0]
    assert "wacht gerade auf" in unten
    assert 'value="Landmilch"' in antwort


def test_ein_misslungener_tipp_traegt_die_meldung_an_beide_stellen_nach(
        db_datei, tmp_path):
    """Die Teilantwort tauscht nur eine Zeile — beide Hüllen müssen mit."""
    ids = _langer_verlauf(db_datei, zuege=1, je_zug=2)
    client, _ = _client(db_datei, tmp_path)

    antwort = _entscheiden(client, ids[0][0], "quatsch").text

    assert 'id="chat-fehler" hx-swap-oob="true"' in antwort
    assert 'id="chat-fehler-unten" hx-swap-oob="true"' in antwort
    unten = antwort.split('id="chat-fehler-unten"', 1)[1]
    assert 'class="fehler"' in unten


def test_der_zustandsstreifen_fragt_auch_im_fehlerfall_weiter_nach(db_datei,
                                                                   tmp_path):
    """Die Box schläft nach 120 min — das ist der häufigste Zustand.

    Vorher pollte nur `wacht_auf`. Kippte es auf `nicht_erreichbar`, hörte das
    Nachfragen auf, und das Band blieb bis zum manuellen Neuladen stehen —
    auch wenn die Box längst wieder bediente.
    """
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.NICHT_ERREICHBAR, grund="schläft"))
    stueck = client.get("/warenkorb/chat/zustand").text

    assert 'hx-get="/warenkorb/chat/zustand"' in stueck
    # Mit grösserem Abstand als beim Aufwachen: `wake.NEUVERSUCH_S` wartet
    # ohnehin eine Minute, und häufiger zu fragen kostet nur health-Timeouts.
    assert 'hx-trigger="load delay:30s"' in stueck
    # Und ein Weg von Hand, für den Fall, dass sie es besser weiss.
    assert "Jetzt nachsehen" in stueck


def test_beim_aufwachen_bleibt_der_kurze_takt(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.WACHT_AUF, grund="Weckruf läuft."))
    stueck = client.get("/warenkorb/chat/zustand").text
    assert 'hx-trigger="load delay:5s"' in stueck
    # Solange der Zähler läuft, braucht es keinen Knopf.
    assert "Jetzt nachsehen" not in stueck


def test_der_abgelaufene_zaehler_steht_nicht_bei_null(db_datei, tmp_path):
    """„noch ~0 s" blieb unbegrenzt stehen (WB-378)."""

    class AlteBox:
        def zustand(self):
            return wake.Zustand(wake.WACHT_AUF, seit_s=200.0,
                                grund="Weckruf läuft.")

    client, _ = _client(db_datei, tmp_path, box=AlteBox())
    stueck = client.get("/warenkorb/chat/zustand").text
    assert "~0 s" not in stueck
    assert "dauert länger als sonst" in stueck
