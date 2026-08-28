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
