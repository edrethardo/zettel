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
from picknick.llm import wake
from picknick.llm.client import Antwort
from picknick.scrapers import knuspr
from picknick.web import app as webapp

FIXTURE = Path(__file__).parent / "fixtures" / "knuspr_milch.json"
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


class FakeHTTP:
    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, url):
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


@pytest.fixture
def db_datei(tmp_path):
    pfad = tmp_path / "picknick.db"
    con = db.connect(pfad)
    db.migrate(con)
    knuspr.crawl(con, FakeHTTP([json.loads(FIXTURE.read_text(encoding="utf-8"))]),
                 ["milch"], pause_s=0)
    con.close()
    return pfad


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
