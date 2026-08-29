"""Tests für Modellzugang und Weckhandling (WB-326).

**Kein Test geht ins Netz und keiner weckt die echte Box.** Beides wird
erzwungen und nicht bloss versprochen:

* Der Modellzugang bekommt einen `openai`-Client untergeschoben, dessen
  Transport ein `MockTransport` ist — es entsteht kein Socket, die Antworten
  werden auf HTTP-Ebene erfunden. Dass dabei das echte SDK die Antwort
  auspackt, ist der Punkt: `reasoning_content` ist kein Feld des
  OpenAI-Schemas, und ob es die Fahrt durchs SDK überlebt, kann nur ein
  echter Client beantworten.
* Der Wecker bekommt einen gefälschten Prozessstart. Der gefälschte Prozess
  wirft, wenn jemand ihn abwartet — das ist der Test für „nicht blockierend",
  denn ein `wait()` im Request-Pfad würde die Oberfläche 90 Sekunden lang
  einfrieren und sähe im Code harmlos aus.

`httpx2` ist der httpx-Zweig, auf dem `openai` 3.x fährt; der Shop selbst
benutzt weiterhin `httpx`. Deshalb stehen hier zwei Doppelgänger-Bauarten
nebeneinander.
"""
import json
import time

import httpx
import httpx2
import openai
import pytest

from picknick.llm import client as llm
from picknick.llm import wake
from picknick.web import app as webapp

ENDPUNKT = "http://vllm-box.local:8000/v1"
KUERZEL = "Qwen3.8-27B-Instruct"


# --------------------------------------------------------------------------
# Doppelgänger

class Box:
    """Ein vLLM-Doppelgänger auf HTTP-Ebene."""

    def __init__(self, modell=KUERZEL, content="ok", reasoning=None,
                 usage=True, modelle=None, denkfeld="reasoning"):
        self.modell = modell
        self.modelle = modelle if modelle is not None else [modell]
        self.content = content
        self.reasoning = reasoning
        # Wie das Feld mit dem Denken heisst. Die Box nennt es am 2026-08-28
        # gemessen `reasoning`; andere vLLM-Stände `reasoning_content`.
        self.denkfeld = denkfeld
        self.usage = usage
        self.anfragen = []

    def __call__(self, request):
        self.anfragen.append(request)
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={
                "object": "list",
                "data": [{"id": m, "object": "model"} for m in self.modelle]})
        rumpf = json.loads(request.content)
        nachricht = {"role": "assistant", "content": self.content}
        if self.reasoning is not None:
            nachricht[self.denkfeld] = self.reasoning
        antwort = {
            "id": "c1", "object": "chat.completion", "created": 1,
            "model": rumpf["model"],
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": nachricht}],
        }
        if self.usage:
            antwort["usage"] = {"prompt_tokens": 11, "completion_tokens": 3,
                                "total_tokens": 14}
        return httpx2.Response(200, json=antwort)

    @property
    def chat_rumpf(self):
        """Der Rumpf der letzten Chat-Anfrage, so wie er über die Leitung ging."""
        chats = [a for a in self.anfragen
                 if a.url.path.endswith("/chat/completions")]
        return json.loads(chats[-1].content)


def zugang(box, endpunkt=ENDPUNKT, **kw):
    sdk = openai.OpenAI(
        base_url=endpunkt, api_key="1", max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(box)))
    return llm.Modellzugang(endpunkt, client=sdk, umgebung={}, **kw)


def http_doppel(handler):
    """Ein `httpx.Client`, der nie ein Socket öffnet — für `wake.health`."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def stille_box(request):
    """Die suspendierte Box: es nimmt niemand an."""
    raise httpx.ConnectError("No route to host", request=request)


def ladende_box(request):
    """Der Port ist offen, vLLM lädt noch — `wake-vllm` nennt das genauso."""
    return httpx.Response(503, text="loading")


def wache_box(request):
    return httpx.Response(200, json={"data": [{"id": KUERZEL}]})


class FakeProzess:
    """Ein gestarteter Weckruf, der nie wirklich läuft."""

    def __init__(self):
        self._laeuft = True
        self.returncode = None

    def poll(self):
        return None if self._laeuft else self.returncode

    def beende(self, code=0):
        self._laeuft = False
        self.returncode = code

    def wait(self, *a, **kw):
        raise AssertionError(
            "Der Weckruf darf nicht abgewartet werden — das sind 90 Sekunden "
            "im Request-Pfad.")

    def communicate(self, *a, **kw):
        return self.wait()


class FakeStarter:
    def __init__(self):
        self.aufrufe = []
        self.prozesse = []

    def __call__(self, befehl):
        self.aufrufe.append(befehl)
        p = FakeProzess()
        self.prozesse.append(p)
        return p


class Uhr:
    """Eine Uhr, die nur weitergeht, wenn ein Test sie stellt."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def weiter(self, s):
        self.t += s


@pytest.fixture
def weckbefehl(tmp_path):
    """Ein ausführbarer Platzhalter, damit `shutil.which` etwas findet.

    Ausgeführt wird er nie — der Starter ist in jedem Test gefälscht.
    """
    pfad = tmp_path / "wake-vllm"
    pfad.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pfad.chmod(0o755)
    return str(pfad)


def wecker(handler, starter=None, uhr=None, weckbefehl="/bin/false", **kw):
    return wake.Wecker(ENDPUNKT, http=http_doppel(handler),
                       starter=starter or FakeStarter(), uhr=uhr or Uhr(),
                       weckbefehl=weckbefehl, umgebung={}, **kw)


# --------------------------------------------------------------------------
# Endpunkt: die Fallen, die schon Zeit gekostet haben

def test_vorgabe_ist_die_box_im_lan():
    assert llm.endpunkt_aus_umgebung({}) == ENDPUNKT
    assert llm.endpunkt_aus_umgebung({llm.ENV_ENDPUNKT: "http://anders:8000/v1"}) \
        == "http://anders:8000/v1"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/v1",
    "http://localhost:8000/v1",
    "http://[::1]:8000/v1",
])
def test_loopback_auf_8000_ist_ein_konfigurationsfehler(url):
    """Auf diesem Rechner lauscht auf 8000 nichts — die Box ist ein anderer."""
    with pytest.raises(llm.KonfigurationsFehler) as e:
        llm.pruefe_endpunkt(url)
    assert "eigener Rechner" in str(e.value)
    assert ENDPUNKT in str(e.value)


def test_loopback_auf_anderem_port_bleibt_erlaubt():
    """Ein lokaler Forward auf einem anderen Port ist eine legitime Wahl."""
    assert llm.pruefe_endpunkt("http://127.0.0.1:8011/v1") == "http://127.0.0.1:8011/v1"


def test_die_alte_ip_wird_als_altlast_erkannt():
    """„No route to host" sieht aus wie ein Netzfehler und ist Konfiguration."""
    with pytest.raises(llm.KonfigurationsFehler) as e:
        llm.pruefe_endpunkt(f"http://{llm.ALTE_IP}:8000/v1")
    assert "2026-08-16" in str(e.value)


@pytest.mark.parametrize("url", ["", "   ", "vllm-box:8000", "ftp://box/v1"])
def test_unbrauchbare_endpunkte(url):
    with pytest.raises(llm.KonfigurationsFehler):
        llm.pruefe_endpunkt(url)


# --------------------------------------------------------------------------
# Modellkürzel: erfragt, nicht geraten

def test_modellkuerzel_kommt_aus_der_box():
    box = Box(modell="Ein-Ganz-Anderes-Modell-9B")
    z = zugang(box)
    assert z.modell() == "Ein-Ganz-Anderes-Modell-9B"


def test_chat_benutzt_das_erfragte_kuerzel():
    """Der Rumpf über die Leitung trägt den Namen der Box, keinen aus dem Code."""
    box = Box(modell="Ein-Ganz-Anderes-Modell-9B")
    zugang(box).chat([{"role": "user", "content": "hi"}])
    assert box.chat_rumpf["model"] == "Ein-Ganz-Anderes-Modell-9B"


def test_kuerzel_wird_gemerkt_und_laesst_sich_neu_erfragen():
    box = Box(modell="Erstes-Modell")
    z = zugang(box)
    assert z.modell() == "Erstes-Modell"
    box.modelle = ["Zweites-Modell"]
    assert z.modell() == "Erstes-Modell", "gemerkt, sonst kostet jeder Zug eine Anfrage"
    assert z.modell(neu=True) == "Zweites-Modell"
    assert z.modell() == "Zweites-Modell"


def test_erzeugen_geht_nicht_ins_netz():
    """Der Web-Prozess darf beim Start nicht auf das Modell warten (Spec 11)."""
    box = Box()
    zugang(box)
    assert box.anfragen == []


def test_box_ohne_modell_ist_nicht_erreichbar():
    """`/v1/models` antwortet, führt aber nichts: vLLM lädt noch."""
    with pytest.raises(llm.ModellNichtErreichbar):
        zugang(Box(modelle=[])).modell()


def test_schweigende_box_wird_nicht_fuer_tot_erklaert():
    def schweigt(request):
        raise httpx2.ConnectError("No route to host", request=request)

    sdk = openai.OpenAI(base_url=ENDPUNKT, api_key="1", max_retries=0,
                        http_client=httpx2.Client(
                            transport=httpx2.MockTransport(schweigt)))
    z = llm.Modellzugang(ENDPUNKT, client=sdk, umgebung={})
    with pytest.raises(llm.ModellNichtErreichbar) as e:
        z.modell()
    text = str(e.value)
    assert "suspendiert" in text and "wake" in text


# --------------------------------------------------------------------------
# Antwort: Denken und Ergebnis getrennt

@pytest.mark.parametrize("denkfeld", ["reasoning", "reasoning_content"])
def test_denken_kommt_getrennt_vom_ergebnis_an(denkfeld):
    """Beide Schreibweisen, weil die Box ihre schon gewechselt hat.

    Gemessen 2026-08-28 liefert `vllm-box.local` das Denken als
    `reasoning`; die Notiz zur Maschine sprach von `reasoning_content`. Ein
    geratener Feldname hätte das Denken stillschweigend verschluckt.
    """
    box = Box(content='{"begriff": "Milch"}', reasoning="Erst denke ich nach.",
              denkfeld=denkfeld)
    antwort = zugang(box).chat([{"role": "user", "content": "milch"}])
    assert antwort.content == '{"begriff": "Milch"}'
    assert antwort.reasoning_content == "Erst denke ich nach."
    assert antwort.modell == KUERZEL
    assert antwort.finish_reason == "stop"
    assert (antwort.prompt_tokens, antwort.completion_tokens) == (11, 3)


def test_antwort_ohne_reasoning_content_bricht_nichts():
    """Ein Server ohne Reasoning-Parser liefert das Feld gar nicht."""
    antwort = zugang(Box(content="ok")).chat([{"role": "user", "content": "hi"}])
    assert antwort.content == "ok"
    assert antwort.reasoning_content is None


def test_antwort_ohne_usage_bricht_nichts():
    antwort = zugang(Box(usage=False)).chat([{"role": "user", "content": "hi"}])
    assert antwort.prompt_tokens is None and antwort.completion_tokens is None


def test_zusatzargumente_gehen_unveraendert_durch():
    """Der Agent braucht `guided_json` und eigene Temperaturen (Spec 6, 8.3)."""
    box = Box()
    zugang(box).chat([{"role": "user", "content": "hi"}], temperature=0.0,
                     extra_body={"guided_json": {"type": "object"}})
    rumpf = box.chat_rumpf
    assert rumpf["temperature"] == 0.0
    assert rumpf["guided_json"] == {"type": "object"}


# --------------------------------------------------------------------------
# health: die drei Befunde, die `wake-vllm --check` auch unterscheidet

def test_health_bedient_meldet_das_kuerzel():
    befund = wake.health(ENDPUNKT, http=http_doppel(wache_box))
    assert befund.zustand == wake.BEDIENT and befund.modell == KUERZEL


def test_health_stille_box():
    assert wake.health(ENDPUNKT, http=http_doppel(stille_box)).zustand == wake.STILL


def test_health_connect_timeout_gilt_als_still():
    """Eine suspendierte Maschine antwortet nicht einmal auf ARP.

    Würde der Verbindungs-Timeout als „lädt noch" gelten, würde nie geweckt.
    """
    def zeit_ab(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    assert wake.health(ENDPUNKT, http=http_doppel(zeit_ab)).zustand == wake.STILL


def test_health_ladende_box():
    assert wake.health(ENDPUNKT, http=http_doppel(ladende_box)).zustand == wake.LAEDT


def test_health_antwort_ohne_modell_ist_laedt():
    def leer(request):
        return httpx.Response(200, json={"object": "list", "data": []})

    assert wake.health(ENDPUNKT, http=http_doppel(leer)).zustand == wake.LAEDT


# --------------------------------------------------------------------------
# Wecker

def test_bedienende_box_weckt_nichts(weckbefehl):
    starter = FakeStarter()
    z = wecker(wache_box, starter, weckbefehl=weckbefehl).zustand()
    assert z.zustand == wake.BEDIENT and z.modell == KUERZEL
    assert starter.aufrufe == []


def test_stille_box_fuehrt_zu_wacht_auf_und_blockiert_nicht(weckbefehl):
    """Der Kern des Tickets: keine Ausnahme, kein Warten, ein Zustand."""
    starter = FakeStarter()
    w = wecker(stille_box, starter, weckbefehl=weckbefehl)
    vorher = time.monotonic()
    z = w.zustand()
    assert time.monotonic() - vorher < 1.0
    assert z.zustand == wake.WACHT_AUF
    assert z.seit_s == 0.0 and z.erwartet_s == wake.WECKDAUER_S
    assert z.rest_s == wake.WECKDAUER_S
    assert starter.aufrufe == [[weckbefehl]]
    # Der gefälschte Prozess wirft, wenn jemand ihn abwartet — er wurde nicht.
    assert starter.prozesse[0].poll() is None


def test_hoechstens_ein_weckvorgang_gleichzeitig(weckbefehl):
    starter = FakeStarter()
    uhr = Uhr()
    w = wecker(stille_box, starter, uhr, weckbefehl=weckbefehl)
    assert w.zustand().zustand == wake.WACHT_AUF
    for _ in range(5):
        uhr.weiter(10)
        z = w.zustand()
        assert z.zustand == wake.WACHT_AUF
    assert len(starter.aufrufe) == 1, "ein laufender Weckruf genügt"
    assert z.seit_s == 50.0


def test_zaehler_laeuft_mit_der_zeit(weckbefehl):
    uhr = Uhr()
    w = wecker(stille_box, FakeStarter(), uhr, weckbefehl=weckbefehl)
    w.zustand()
    uhr.weiter(80)
    z = w.zustand()
    assert z.seit_s == 80.0 and z.rest_s == 10.0
    assert not z.ueberfaellig


def test_abgelaufener_zaehler_klemmt_nicht_bei_null(weckbefehl):
    """WB-378: `max(0.0, …)` liess unbegrenzt „noch ~0 s" stehen.

    Eine Restzeit von null ist keine Auskunft, sondern ein stehengebliebener
    Zähler — und er behauptet, es sei gleich soweit, während sie längst
    doppelt so lange wartet wie angekündigt. Danach gibt es keine Restzeit
    mehr, sondern `ueberfaellig`, und die Oberfläche schreibt einen Satz.
    """
    uhr = Uhr()
    w = wecker(stille_box, FakeStarter(), uhr, weckbefehl=weckbefehl)
    w.zustand()
    uhr.weiter(wake.WECKDAUER_S + 60)
    z = w.zustand()
    assert z.zustand == wake.WACHT_AUF
    assert z.rest_s is None
    assert z.ueberfaellig


def test_ohne_zaehler_ist_nichts_ueberfaellig():
    """`rest_s is None` allein reicht der Vorlage nicht als Unterscheidung."""
    z = wake.Zustand(wake.NICHT_ERREICHBAR, grund="kein Weckbefehl")
    assert z.seit_s is None and z.rest_s is None
    assert not z.ueberfaellig


def test_ladende_box_braucht_keinen_weckruf(weckbefehl):
    """Der Port nimmt an: die Maschine ist wach, vLLM lädt nur."""
    starter = FakeStarter()
    z = wecker(ladende_box, starter, weckbefehl=weckbefehl).zustand()
    assert z.zustand == wake.WACHT_AUF
    assert starter.aufrufe == []


def test_bedienen_setzt_den_weckvorgang_zurueck(weckbefehl):
    """Nach dem Aufwachen zählt der nächste Ausfall wieder von vorn."""
    antworten = [stille_box, wache_box, stille_box]
    starter = FakeStarter()

    def wechselnd(request):
        return antworten.pop(0)(request)

    uhr = Uhr()
    w = wecker(wechselnd, starter, uhr, weckbefehl=weckbefehl)
    assert w.zustand().zustand == wake.WACHT_AUF
    uhr.weiter(100)
    assert w.zustand().zustand == wake.BEDIENT
    uhr.weiter(100)
    z = w.zustand()
    assert z.zustand == wake.WACHT_AUF and z.seit_s == 0.0
    assert len(starter.aufrufe) == 2


def test_fehlgeschlagener_weckruf_meldet_nicht_erreichbar(weckbefehl):
    starter = FakeStarter()
    uhr = Uhr()
    w = wecker(stille_box, starter, uhr, weckbefehl=weckbefehl)
    w.zustand()
    starter.prozesse[0].beende(1)
    uhr.weiter(120)
    z = w.zustand()
    assert z.zustand == wake.NICHT_ERREICHBAR
    assert "Code 1" in z.grund
    # Und kein zweiter Weckruf im Sekundentakt, solange die Sperre gilt.
    uhr.weiter(5)
    assert w.zustand().zustand == wake.NICHT_ERREICHBAR
    assert len(starter.aufrufe) == 1
    # Nach der Wartezeit wird es noch einmal versucht.
    uhr.weiter(wake.NEUVERSUCH_S)
    assert w.zustand().zustand == wake.WACHT_AUF
    assert len(starter.aufrufe) == 2


def test_endloser_weckruf_wird_nicht_ewig_als_wacht_auf_verkauft(weckbefehl):
    uhr = Uhr()
    starter = FakeStarter()
    w = wecker(stille_box, starter, uhr, weckbefehl=weckbefehl)
    w.zustand()
    uhr.weiter(wake.WECKFRIST_S + 1)
    z = w.zustand()
    assert z.zustand == wake.NICHT_ERREICHBAR
    assert len(starter.aufrufe) == 1


def test_fehlender_weckbefehl_ist_ein_konfigurationsfehler():
    """Und kein Hinweis darauf, dass die Box aus wäre."""
    starter = FakeStarter()
    w = wecker(stille_box, starter, weckbefehl="gibt-es-hier-ganz-sicher-nicht")
    z = w.zustand()
    assert z.zustand == wake.NICHT_ERREICHBAR
    assert "PATH" in z.grund and wake.ENV_WECKBEFEHL in z.grund
    assert starter.aufrufe == []


def test_starter_der_nicht_startet(weckbefehl):
    def kaputt(befehl):
        raise OSError("Permission denied")

    z = wecker(stille_box, kaputt, weckbefehl=weckbefehl).zustand()
    assert z.zustand == wake.NICHT_ERREICHBAR
    assert "Permission denied" in z.grund


def test_kaputter_endpunkt_graut_nur_den_chat_aus(monkeypatch):
    """`wake.zustand()` wirft nicht — sonst reisst der Chat den Shop mit."""
    monkeypatch.setattr(wake, "_WECKER", None)
    monkeypatch.setenv(llm.ENV_ENDPUNKT, "http://127.0.0.1:8000/v1")
    z = wake.zustand()
    assert z.zustand == wake.NICHT_ERREICHBAR
    assert "eigener Rechner" in z.grund


# --------------------------------------------------------------------------
# Spec 11: ohne Modell bleibt alles ausser dem Chat benutzbar

@pytest.fixture
def shop(leere_db_datei, tmp_path, monkeypatch):
    """Der Shop mit leerem Katalog — und einem Modell, das jeden Aufruf sprengt.

    Jeder Zugriff auf `wake.health` oder `Modellzugang.chat` wirft hier. Ein
    Test, der trotzdem durchläuft, beweist, dass der Web-Prozess das Modell
    weder beim Start noch beim Einkaufen anfasst.
    """
    def verboten(*a, **kw):
        raise AssertionError(
            "Der Shop hat das Modell angefasst. Suchen, Einlegen, Bestellen "
            "und Abhaken müssen ohne laufen (Spec 11).")

    monkeypatch.setattr(wake, "health", verboten)
    monkeypatch.setattr(llm.Modellzugang, "chat", verboten)
    monkeypatch.setenv(llm.ENV_ENDPUNKT, "http://127.0.0.1:8000/v1")

    from fastapi.testclient import TestClient
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    with TestClient(webapp.create_app(db_path=leere_db_datei,
                                      image_dir=bilder)) as c:
        yield c


def test_shop_laeuft_ohne_modell(shop):
    assert shop.get("/katalog").status_code == 200
    assert shop.get("/warenkorb").status_code == 200
    assert shop.post("/warenkorb/einlegen",
                     data={"free_text": "Klopapier", "qty": "2"}).status_code == 200
    assert shop.post("/warenkorb/abschicken", data={"note": ""},
                     follow_redirects=False).status_code == 303
    assert shop.get("/bestellungen").status_code == 200
    assert shop.get("/pick").status_code == 200
