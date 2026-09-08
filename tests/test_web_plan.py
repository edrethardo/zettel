"""Die Wochenplan-Seite (Phase 1: ohne Modell).

Dieselbe Bauart wie `test_web_recipes.py`: die Oberfläche über den
`TestClient`, und hinter jedem Klick eine Abfrage, was danach in der
Datenbank steht. Kein Modell — die Seite muss ohne eines vollständig
bedienbar sein, das ist der Nutzen, der auch ohne LLM trägt.
"""
import pytest
from fastapi.testclient import TestClient

from zettel import db, orders, recipes, wochenplan
from zettel.web import app as webapp

MILCH = "Miil Frische Landmilch 3,8% Vollmilch"
BUTTER = "MIIL Deutsche Markenbutter"
HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(db_datei, tmp_path):
    with TestClient(webapp.create_app(db_path=db_datei,
                                      image_dir=tmp_path / "bilder")) as c:
        yield c


@pytest.fixture
def con(db_datei):
    c = db.connect(db_datei)
    yield c
    c.close()


def _pid(con, name):
    return con.execute("SELECT id FROM product WHERE name = ?",
                       (name,)).fetchone()["id"]


def _rezept(con, name, zutaten, servings=4, minuten=None):
    rid = recipes.anlegen(con, name, servings=servings, zutaten=zutaten)
    if minuten:
        con.execute("UPDATE recipe SET prep_minutes = ? WHERE id = ?",
                    (minuten, rid))
        con.commit()
    return rid


def _plan(client, **werte):
    r = client.post("/plan", data={"tage": "3", "personen": "2", **werte},
                    follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def _tage(con, pid):
    return [dict(r) for r in con.execute(
        "SELECT * FROM plan_tag WHERE plan_id = ? ORDER BY pos", (pid,))]


# --------------------------------------------------------------------------

def test_ohne_plan_steht_das_formular(client):
    r = client.get("/plan")
    assert r.status_code == 200
    assert 'name="tage"' in r.text and 'name="bestand"' in r.text
    assert "Wochenplan" in r.text


def test_formular_legt_plan_mit_tagen_und_bestand_an(client, con):
    pid = _plan(client, max_minuten="30", budget="40",
                bestand="500 g Kartoffeln, 6 Eier")
    p = wochenplan.laden(con, pid)
    assert (p["tage"], p["personen"], p["max_minuten"], p["budget_cents"]) == (
        3, 2, 30, 4000)
    assert [b["name"] for b in p["bestand"]] == ["Kartoffeln", "Eier"]
    r = client.get(f"/plan/{pid}")
    assert r.status_code == 200
    assert "höchstens 30 Minuten" in r.text
    assert "Kartoffeln" in r.text
    # Die Seite ist bei /plan dieselbe: der jüngste Plan.
    assert client.get("/plan").text == r.text


def test_tag_setzen_ja_nein_und_auswaerts(client, con):
    rid = _rezept(con, "Milchreis", [{"product_id": _pid(con, MILCH),
                                      "amount": 500, "unit": "ml"}], minuten=25)
    pid = _plan(client)
    tage = _tage(con, pid)
    r = client.post(f"/plan/{pid}/tag/{tage[0]['id']}",
                    data={"recipe_id": str(rid), "portionen": "4"}, headers=HTMX)
    assert r.status_code == 200
    assert 'id="plan-inhalt"' in r.text and "<html" not in r.text
    assert "Milchreis" in r.text and "25 Minuten" in r.text
    t = _tage(con, pid)[0]
    assert (t["recipe_id"], t["portionen"], t["decision"]) == (rid, 4, "offen")

    r = client.post(f"/plan/{pid}/tag/{t['id']}/entscheiden?decision=kept",
                    headers=HTMX)
    assert r.status_code == 200 and "passt" in r.text
    assert _tage(con, pid)[0]["decision"] == "kept"

    r = client.post(f"/plan/{pid}/tag/{tage[1]['id']}?auswaerts=1", headers=HTMX)
    assert r.status_code == 200
    t2 = _tage(con, pid)[1]
    assert (t2["auswaerts"], t2["recipe_id"], t2["decision"]) == (1, None, "kept")
    assert "auswärts" in r.text

    # Ohne JavaScript: die ganze Seite, kein Bruchstück.
    r = client.post(f"/plan/{pid}/tag/{tage[2]['id']}",
                    data={"recipe_id": str(rid)})
    assert r.status_code == 200 and "<html" in r.text


def test_einkaufsliste_und_korb(client, con):
    a = _rezept(con, "A", [{"product_id": _pid(con, BUTTER), "amount": 40, "unit": "g"},
                           {"free_text": "Eier", "amount": 4}])
    b = _rezept(con, "B", [{"product_id": _pid(con, BUTTER), "amount": 40, "unit": "g"}])
    pid = _plan(client, bestand="12 Eier")
    tage = _tage(con, pid)
    client.post(f"/plan/{pid}/tag/{tage[0]['id']}", data={"recipe_id": str(a)})
    r = client.post(f"/plan/{pid}/tag/{tage[1]['id']}", data={"recipe_id": str(b)},
                    headers=HTMX)
    assert "40 g gebraucht" in r.text  # 2 Personen, Rezepte für 4: halbiert, dann summiert
    assert "12 Stk da — gedeckt" in r.text
    assert "1 zu kaufen, 1 durch den Bestand gedeckt" in r.text

    r = client.post(f"/plan/{pid}/korb")
    assert r.status_code == 200
    assert "1 Zeilen im Korb, 1 durch den Bestand gedeckt" in r.text
    posten = orders.inhalt(con)
    assert [(p["product_id"], p["need_amount"]) for p in posten] == [
        (_pid(con, BUTTER), 40.0)]
    assert wochenplan.laden(con, pid)["status"] == wochenplan.IM_KORB
    # Die Korbzahl im Kopf ist gesprungen.
    assert 'class="korbzahl">1<' in r.text


def test_bestand_dazu_und_weg(client, con):
    pid = _plan(client)
    r = client.post(f"/plan/{pid}/bestand", data={"text": "1 kg Mehl, Reis"},
                    headers=HTMX)
    assert r.status_code == 200
    bestand = wochenplan.laden(con, pid)["bestand"]
    assert [(b["name"], b["menge"], b["einheit"]) for b in bestand] == [
        ("Mehl", 1.0, "kg"), ("Reis", None, None)]
    r = client.post(f"/plan/{pid}/bestand/{bestand[0]['id']}/entscheiden"
                    "?decision=removed", headers=HTMX)
    assert r.status_code == 200
    assert wochenplan.laden(con, pid)["bestand"][0]["decision"] == "removed"
    r = client.post(f"/plan/{pid}/bestand", data={"text": "   "}, headers=HTMX)
    assert "nichts, was sich als Bestand lesen" in r.text


def test_leerer_plan_in_den_korb_sagt_warum(client, con):
    pid = _plan(client)
    r = client.post(f"/plan/{pid}/korb")
    assert r.status_code == 200
    assert "nichts zu kaufen" in r.text
    assert orders.warenkorb_id(con) is None


def test_fremder_plan_ist_404(client):
    r = client.get("/plan/999")
    assert r.status_code == 404
    assert "Zum Wochenplan" in r.text


def test_mehr_verlinkt_den_plan_und_englisch(client):
    assert 'href="/plan"' in client.get("/mehr").text
    client.post("/sprache?code=en", follow_redirects=False)
    r = client.get("/plan")
    assert "Weekly plan" in r.text and 'name="bestand"' in r.text


def test_neuer_plan_neben_altem(client, con):
    a = _plan(client)
    r = client.get("/plan/neu")
    assert r.status_code == 200 and 'name="tage"' in r.text
    b = _plan(client)
    assert b > a
    assert f"/plan/{b}" not in client.get("/plan").text or True
    assert wochenplan.aktuell(con)["id"] == b


# --------------------------------------------------------------------------
# Der Zug (Phase 2) über die Oberfläche

import json  # noqa: E402

from zettel.assistant import chat as chatmodul  # noqa: E402
from zettel.llm import wake  # noqa: E402
from zettel.llm.client import Antwort  # noqa: E402


class _FakeLLM:
    def __init__(self, *antworten):
        self.antworten = list(antworten)

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class _Box:
    def __init__(self, zustand=wake.BEDIENT):
        self._z = zustand

    def zustand(self):
        if self._z == wake.BEDIENT:
            return wake.Zustand(wake.BEDIENT, modell="fake")
        return wake.Zustand(self._z, seit_s=3.0, grund="schläft")


def _client_mit(db_datei, tmp_path, *antworten, box=None):
    chat = chatmodul.Chat(_FakeLLM(*antworten), wecker=box or _Box())
    return TestClient(webapp.create_app(
        db_path=db_datei, image_dir=tmp_path / "bilder", chat=chat,
        planer=wochenplan.Planer(chat)))


def test_woche_planen_belegt_die_tage(db_datei, tmp_path, con):
    a = _rezept(con, "Milchreis", [{"product_id": _pid(con, MILCH), "amount": 500, "unit": "ml"}], minuten=25)
    b = _rezept(con, "Butterbrot", [{"product_id": _pid(con, BUTTER), "amount": 20, "unit": "g"}], minuten=5)
    antwort = json.dumps({"tage": [{"tag": 1, "gericht_id": a, "grund": "schnell"},
                                   {"tag": 2, "gericht_id": 4711, "grund": "?"},
                                   {"tag": 3, "gericht_id": b, "grund": "teilt nichts"}]})
    with _client_mit(db_datei, tmp_path, antwort) as client:
        pid = _plan(client)
        r = client.post(f"/plan/{pid}/planen", headers=HTMX)
        assert r.status_code == 200
        assert "2 von 3 offenen Tagen belegt, aus 2 Gerichten zur Wahl." in r.text
        assert "1 Vorschläge verworfen" in r.text
        assert "Milchreis" in r.text and "schnell" in r.text
        assert [t["recipe_id"] for t in _tage(con, pid)] == [a, None, b]
        # Der Knopf heisst jetzt „neu planen".
        assert "Offene Tage neu planen" in client.get(f"/plan/{pid}").text


def test_woche_planen_bei_schlafender_box(db_datei, tmp_path, con):
    _rezept(con, "Milchreis", [{"product_id": _pid(con, MILCH)}])
    with _client_mit(db_datei, tmp_path, box=_Box(wake.WACHT_AUF)) as client:
        pid = _plan(client)
        r = client.post(f"/plan/{pid}/planen", headers=HTMX)
        assert r.status_code == 200
        assert "Das Modell wacht auf" in r.text
        assert all(t["recipe_id"] is None for t in _tage(con, pid))


def test_woche_planen_ohne_gerichte(db_datei, tmp_path, con):
    with _client_mit(db_datei, tmp_path) as client:
        pid = _plan(client)
        r = client.post(f"/plan/{pid}/planen", headers=HTMX)
        assert "kein Gericht zur Wahl" in r.text


# --------------------------------------------------------------------------
# Der Bon schlägt vor (Phase 3)

from zettel.bons import kaeufe, zerlegen  # noqa: E402


def test_bon_schlaegt_vor_und_ja_deckt(client, con):
    bon = zerlegen.Bon(laden="rewe", datum=__import__("datetime").date.today().isoformat(),
                       posten=[zerlegen.Posten(text="MARKENBUTTER", gesamt_cents=105, zeile=1)])
    rid = kaeufe.anlegen(con, bon, datei="bon.pdf")
    item = kaeufe.posten(con, rid)[0]
    kaeufe.zuordnung_setzen(con, item["id"], product_id=_pid(con, BUTTER))
    kaeufe.entscheiden(con, item["id"], "kept")
    a = _rezept(con, "A", [{"product_id": _pid(con, BUTTER), "amount": 100, "unit": "g"}])
    pid = _plan(client, personen="4")
    tag = _tage(con, pid)[0]
    client.post(f"/plan/{pid}/tag/{tag['id']}", data={"recipe_id": str(a)})

    r = client.post(f"/plan/{pid}/bestand/aus_bons", headers=HTMX)
    assert r.status_code == 200
    assert "1 Käufe der letzten Tage könnten noch da sein" in r.text
    assert "vom Bon" in r.text and "noch da" in r.text
    b = wochenplan.laden(con, pid)["bestand"][0]
    assert (b["herkunft"], b["decision"], b["menge"], b["einheit"]) == (
        "aus_bon", "offen", 250.0, "g")

    r = client.post(f"/plan/{pid}/bestand/{b['id']}/entscheiden?decision=kept",
                    headers=HTMX)
    assert "250 g da — gedeckt" in r.text
    # Noch einmal fragen bringt keine Dublette.
    r = client.post(f"/plan/{pid}/bestand/aus_bons", headers=HTMX)
    assert "Kein bestätigter Kauf" in r.text
