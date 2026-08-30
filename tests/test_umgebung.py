"""Der Rückfall auf die Namen von vor WB-401.

Was hier geprüft wird, ist nicht die Umbenennung — die sähe man am ersten
Importfehler. Geprüft wird das Versprechen, das die Umbenennung erst
verantwortbar macht: **ein Haushalt, der nichts umstellt, läuft weiter.**

Der konkrete Fall dahinter: die echte Adresse der vLLM-Box steht gitignort auf
einer einzigen Maschine (WB-388). Ein Umbau im Repo erreicht sie nicht. Ohne
Rückfall redete der Shop nach dem nächsten Neustart gegen `localhost:8000`,
und die Oberfläche sagte „Modell nicht erreichbar" — was aussieht wie eine
schlafende Box und Stunden kostet, bis jemand auf den Gedanken kommt, dass
eine Variable anders heisst.

Deshalb ist hier auch die **Warnung** zugesichert und nicht nur der Wert. Ein
stiller Rückfall wäre ein Rückfall, den niemand je abschaltet.
"""
from __future__ import annotations

import logging

import pytest

from zettel import umgebung
from zettel.llm import client as llm
from zettel.obs import otel
from zettel.scrapers import begriffe
from zettel.web import app as webapp


@pytest.fixture(autouse=True)
def _frisch_gewarnt():
    """Jeder Test fängt ungewarnt an.

    `umgebung._gewarnt` ist prozessweit — genau darum geht es, eine Warnung je
    Variable und nicht je Aufruf. In einer Testsuite hiesse dasselbe: der erste
    Test, der eine Variable benutzt, nähme allen folgenden die Warnung weg.
    """
    umgebung._gewarnt.clear()
    yield
    umgebung._gewarnt.clear()


# --------------------------------------------------------------------------
# Die Namensabbildung

def test_alter_name_wird_abgeleitet_und_nicht_gepflegt():
    """Zwölf Variablen, eine Regel — zwei Listen liefen auseinander."""
    assert umgebung.alt_name("ZETTEL_DB") == "PICKNICK_DB"
    assert umgebung.alt_name("ZETTEL_LLM_ENDPOINT") == "PICKNICK_LLM_ENDPOINT"


def test_fremde_namen_bekommen_keinen_alten_zwilling():
    """`PATH` hat keinen alten Namen. Ohne diese Grenze führe die Abbildung
    irgendwann `PICKNICK_PATH` ein, das es nie gab."""
    for fremd in ("PATH", "OPENAI_API_KEY", "HOME"):
        assert umgebung.alt_name(fremd) == fremd
        assert umgebung.wert(fremd, {}) is None


# --------------------------------------------------------------------------
# Die Prozessumgebung

def test_neuer_name_gilt():
    assert umgebung.wert("ZETTEL_DB", {"ZETTEL_DB": "a.db"}) == "a.db"


def test_alter_name_gilt_weiter(caplog):
    with caplog.at_level(logging.WARNING, logger="zettel.umgebung"):
        assert umgebung.wert("ZETTEL_DB", {"PICKNICK_DB": "a.db"}) == "a.db"
    text = caplog.text
    assert "PICKNICK_DB" in text and "ZETTEL_DB" in text
    # Die Warnung sagt, bis wann — ein Übergang ohne Ende ist keiner.
    assert umgebung.RUECKFALL_BIS in text


def test_neuer_name_schlaegt_den_alten_ohne_warnung(caplog):
    """Der Zustand mitten in einer Umstellung ist kein Fehler."""
    with caplog.at_level(logging.WARNING, logger="zettel.umgebung"):
        assert umgebung.wert(
            "ZETTEL_DB", {"ZETTEL_DB": "neu.db", "PICKNICK_DB": "alt.db"}) \
            == "neu.db"
    assert caplog.text == ""


def test_ungesetzt_bleibt_none():
    assert umgebung.wert("ZETTEL_DB", {}) is None


def test_gesetzt_aber_leer_ist_nicht_ungesetzt():
    """Der Unterschied trägt: `ZETTEL_HOST=""` ist ein Konfigurationsfehler,
    den `web.app` beantwortet, statt ihn zur Vorgabe zu glätten."""
    assert umgebung.wert("ZETTEL_HOST", {"ZETTEL_HOST": ""}) == ""
    assert umgebung.wert("ZETTEL_HOST", {"PICKNICK_HOST": ""}) == ""


def test_gewarnt_wird_einmal_je_variable(caplog):
    """Sonst wäre das Logfile nach einem Tag eine einzige Zeile — und nach
    zwei Tagen eine, die niemand mehr liest."""
    with caplog.at_level(logging.WARNING, logger="zettel.umgebung"):
        for _ in range(5):
            umgebung.wert("ZETTEL_DB", {"PICKNICK_DB": "a.db"})
        umgebung.wert("ZETTEL_PORT", {"PICKNICK_PORT": "8730"})
    assert caplog.text.count("PICKNICK_DB") == 1
    assert caplog.text.count("PICKNICK_PORT") == 1


# --------------------------------------------------------------------------
# Dieselbe Zusicherung an jedem der zwölf Anschlüsse
#
# Nicht stellvertretend an einem: die Lesestellen sitzen in sieben Modulen,
# und eine, die beim Umbau vergessen wurde, fiele sonst erst dem Nutzer auf.

def test_bindeadressen_aus_dem_alten_namen():
    assert webapp.hosts_aus_umgebung({"PICKNICK_HOST": "127.0.0.1"}) \
        == ["127.0.0.1"]


def test_alter_name_kommt_auch_nicht_mit_0000_durch():
    """Der Rückfall ist ein Rückfall auf den NAMEN, nicht auf die Prüfung."""
    with pytest.raises(webapp.UnsichereBindung):
        webapp.hosts_aus_umgebung({"PICKNICK_HOST": "0.0.0.0"})


def test_port_aus_dem_alten_namen():
    assert webapp.port_aus_umgebung({"PICKNICK_PORT": "8747"}) == 8747


def test_endpunkt_aus_dem_alten_namen():
    assert llm.endpunkt_aus_umgebung({"PICKNICK_LLM_ENDPOINT":
                                      "http://alt.local:8000/v1"}) \
        == "http://alt.local:8000/v1"


def test_schluessel_aus_dem_alten_namen():
    z = llm.Modellzugang(client=object(),
                         umgebung={"PICKNICK_LLM_API_KEY": "geheim"})
    assert z.schluessel == "geheim"


def test_tracing_aus_dem_alten_namen():
    assert otel.an({"PICKNICK_TRACING": "0"}) is False
    assert otel.an({"PICKNICK_TRACING": "1"}) is True


def test_begriffe_aus_dem_alten_namen():
    assert begriffe.begriffe_aus_umgebung(
        {"PICKNICK_BEGRIFFE": "butter, brot"}) == ["butter", "brot"]


def test_weckbefehl_aus_dem_alten_namen():
    from zettel.llm import wake
    w = wake.Wecker("http://box.local:8000/v1",
                    umgebung={"PICKNICK_WAKE_CMD": "/bin/true"})
    assert w.weckbefehl == "/bin/true"


# --------------------------------------------------------------------------
# Die private Datei
#
# Vier Wege hinein: neue Datei/neuer Name, neue Datei/alter Name, alte
# Datei/neuer Name, alte Datei/alter Name. Wer nur die Hälfte umgestellt hat,
# soll trotzdem seine Box erreichen.

@pytest.fixture
def env_dateien(monkeypatch, tmp_path):
    """Beide Dateipfade in einen leeren Ordner — auch die alte.

    Ohne das Umbiegen von `ALT_ENV_DATEI` hinge dieser Test an einer privaten
    Datei der Maschine, auf der er zufällig läuft. Genau das war schon vor
    WB-401 die Regel für `ENV_DATEI`.
    """
    neu, alt = tmp_path / "zettel.env", tmp_path / "picknick.env"
    monkeypatch.setattr(llm, "ENV_DATEI", neu)
    monkeypatch.setattr(llm, "ALT_ENV_DATEI", alt)
    monkeypatch.delenv(llm.ENV_ENDPUNKT, raising=False)
    monkeypatch.delenv(umgebung.alt_name(llm.ENV_ENDPUNKT), raising=False)
    return neu, alt


@pytest.mark.parametrize("datei,variable,warnt", [
    ("zettel.env", "ZETTEL_LLM_ENDPOINT", False),
    ("zettel.env", "PICKNICK_LLM_ENDPOINT", True),
    ("picknick.env", "ZETTEL_LLM_ENDPOINT", True),
    ("picknick.env", "PICKNICK_LLM_ENDPOINT", True),
])
def test_alle_vier_wege_in_die_private_datei(env_dateien, caplog, datei,
                                             variable, warnt):
    neu, alt = env_dateien
    (neu if datei == "zettel.env" else alt).write_text(
        f"{variable}=http://meine-box.local:8000/v1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="zettel.umgebung"):
        assert llm.endpunkt_aus_umgebung() == "http://meine-box.local:8000/v1"
    assert bool(caplog.text) is warnt


def test_neue_datei_schlaegt_die_alte(env_dateien):
    neu, alt = env_dateien
    neu.write_text("ZETTEL_LLM_ENDPOINT=http://neu.local:8000/v1\n",
                   encoding="utf-8")
    alt.write_text("PICKNICK_LLM_ENDPOINT=http://alt.local:8000/v1\n",
                   encoding="utf-8")
    assert llm.endpunkt_aus_umgebung() == "http://neu.local:8000/v1"


def test_ohne_beide_dateien_gilt_die_vorgabe(env_dateien):
    assert llm.endpunkt_aus_umgebung() == llm.DEFAULT_ENDPUNKT


# --------------------------------------------------------------------------
# Der Rollen-Cookie

class _Anfrage:
    def __init__(self, **cookies):
        self.cookies = cookies


def test_alter_rollen_cookie_gilt_weiter():
    """Ein Cookie lebt im Browser und wandert bei einer Umbenennung nicht
    mit. Ohne Rückfall stünde er nach dem Neustart wieder vor der Rollenwahl."""
    assert webapp.rolle_aus(_Anfrage(picknick_rolle="er")) == "er"


def test_neuer_rollen_cookie_schlaegt_den_alten():
    assert webapp.rolle_aus(_Anfrage(picknick_rolle="er",
                                     zettel_rolle="sie")) == "sie"


def test_ohne_cookie_keine_rolle():
    assert webapp.rolle_aus(_Anfrage()) is None
