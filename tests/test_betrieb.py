"""Tests für Betrieb: Sicherung, Statusseite, systemd-Units (WB-331).

Ohne Netz und ohne systemd. Die Units werden als Text geprüft — das ist hier
angemessen und nicht faul: was an ihnen schiefgehen kann, ist genau eine
falsche Zeile (`python3` statt `.venv/bin/python`, ein fehlendes
`Persistent=true`), und dass sie syntaktisch gültig sind, sagt
`systemd-analyze verify` von Hand. Ein Test, der Dienste startet, würde auf
dieser Maschine echte Timer scharf machen.
"""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zettel import betrieb, db
from zettel.scrapers import begriffe as begriffsliste
from zettel.scrapers import nachtlauf
from zettel.web import app as webapp

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"
FIXTURE = Path(__file__).parent / "fixtures" / "knuspr_milch.json"
MILCH = "Miil Frische Landmilch 3,8% Vollmilch"


class FakeHTTP:
    """Liefert die aufgezeichnete Antwort als erste Seite, danach nichts.

    Seit 2026-09-06 fragt der Nachtlauf VOR dem Crawl die Produkt-Sitemap ab
    (`knuspr.hole_sitemap`). Der Doppelgänger muss diese Anfrage deshalb an
    der URL erkennen und darf ihr keine Katalogseite geben — sonst verbraucht
    sie die aufgezeichnete Antwort, und der Crawl liefe ins Leere.
    `sitemap` ist die Menge der Produkt-IDs, die er als geführt meldet.
    """

    def __init__(self, seiten, sitemap=()):
        self.seiten = list(seiten)
        self.sitemap = list(sitemap)

    def get(self, url):
        if url.endswith("sitemap_products.xml"):
            return _Antwort({}, text="".join(
                f"<url><loc>https://www.knuspr.de/{i}-x</loc></url>"
                for i in self.sitemap))
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


class _Antwort:
    def __init__(self, payload, text=""):
        self._payload = payload
        self.content = b""
        self.text = text

    def json(self):
        return self._payload


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def client(db_datei, tmp_path):
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=bilder)) as c:
        yield c


# --------------------------------------------------------------------------
# Sicherung

def test_sicherung_ist_lesbar_und_hat_dieselben_daten(db_datei, tmp_path):
    """`VACUUM INTO` erzeugt eine eigenständige, vollständige Datei."""
    ziel, weg = betrieb.sichern(db_datei, tmp_path / "stände")
    assert ziel.is_file() and not weg

    original = db.connect(db_datei)
    kopie = sqlite3.connect(str(ziel))
    kopie.row_factory = sqlite3.Row
    try:
        erwartet = [tuple(r) for r in original.execute(
            "SELECT external_id, name, price_cents FROM product ORDER BY id")]
        tatsaechlich = [tuple(r) for r in kopie.execute(
            "SELECT external_id, name, price_cents FROM product ORDER BY id")]
    finally:
        original.close()
        kopie.close()
    assert erwartet == tatsaechlich
    assert len(erwartet) > 1
    assert MILCH in {name for _, name, _ in erwartet}


def test_sicherung_landet_ohne_angabe_neben_der_datenbank(db_datei, monkeypatch,
                                                          tmp_path):
    """Kein fester relativer Pfad: der zeigt dorthin, wo der Prozess steht.

    Beim Probestart der systemd-Unit sicherte ein `data/sicherungen` die
    Testdatenbank ins Projektverzeichnis (2026-08-28).
    """
    woanders = tmp_path / "woanders"
    woanders.mkdir()
    monkeypatch.chdir(woanders)
    ziel, _ = betrieb.sichern(db_datei)
    assert not (woanders / "sicherungen").exists()
    assert ziel.parent == db_datei.parent / "sicherungen"


def test_sicherung_ohne_datenbank_sagt_es(tmp_path):
    with pytest.raises(FileNotFoundError):
        betrieb.sichern(tmp_path / "gibtsnicht.db", tmp_path / "stände")


def test_achter_stand_raeumt_den_aeltesten_ab(db_datei, tmp_path):
    """Sieben Stände (Spec 12) — der achte verdrängt den ersten."""
    ziel_dir = tmp_path / "stände"
    angelegt = []
    for tag in range(1, 9):
        stand, weg = betrieb.sichern(
            db_datei, ziel_dir, stempel=f"2026-08-{tag:02d}T03-30-00")
        angelegt.append(stand)
        if tag <= betrieb.STAENDE:
            assert weg == [], f"nach {tag} Ständen darf nichts weg sein"

    assert weg == [angelegt[0]], "der ÄLTESTE muss weichen, nicht der neueste"
    assert not angelegt[0].exists()
    vorhanden = betrieb.staende(ziel_dir)
    assert len(vorhanden) == betrieb.STAENDE == 7
    assert vorhanden == angelegt[1:]


def test_abraeumen_fasst_fremde_dateien_nicht_an(db_datei, tmp_path):
    """Im Sicherungsverzeichnis wird gelöscht — aber nur, was von uns ist."""
    ziel_dir = tmp_path / "stände"
    for tag in range(1, 9):
        betrieb.sichern(db_datei, ziel_dir, stempel=f"2026-08-{tag:02d}T03-30-00")
    fremd = ziel_dir / "vor-dem-umzug.db"
    fremd.write_bytes(b"nicht von uns")
    betrieb.abraeumen(ziel_dir, behalten=1)
    assert fremd.exists()
    assert len(betrieb.staende(ziel_dir)) == 1


def test_staende_von_vor_der_umbenennung_bleiben_sichtbar(db_datei, tmp_path):
    """Ein Stand mit dem alten Präfix ist ein Stand (WB-401).

    Ein Muster, das nur `zettel-` kennt, sähe die Sicherungen von vor der
    Umbenennung nicht: sie wären in `staende()` unsichtbar — also genau dann
    nicht da, wenn jemand nach einer Sicherung sucht — und würden nie mehr
    abgeräumt.
    """
    ziel_dir = tmp_path / "stände"
    ziel_dir.mkdir()
    alt = ziel_dir / "picknick-2026-08-28T03-30-00.db"
    alt.write_bytes(b"alter Stand")
    neu, _ = betrieb.sichern(db_datei, ziel_dir,
                             stempel="2026-08-30T03-30-00")
    assert betrieb.staende(ziel_dir) == [alt, neu]


def test_neue_staende_tragen_den_neuen_namen(db_datei, tmp_path):
    stand, _ = betrieb.sichern(db_datei, tmp_path / "stände",
                               stempel="2026-08-30T03-30-00")
    assert stand.name == "zettel-2026-08-30T03-30-00.db"


def test_sortiert_wird_nach_zeitstempel_und_nicht_nach_namen(db_datei, tmp_path):
    """Seit WB-401 stehen zwei Präfixe im selben Verzeichnis.

    Über den ganzen Dateinamen sortiert stünde jeder `picknick-`-Stand vor
    jedem `zettel-`-Stand, egal wie jung er ist — und `abraeumen()` löschte
    dann den falschen. Hier ist der alte Stand der JÜNGERE.
    """
    ziel_dir = tmp_path / "stände"
    ziel_dir.mkdir()
    jung = ziel_dir / "picknick-2026-08-29T03-30-00.db"
    jung.write_bytes(b"jung, aber alter Name")
    alt, _ = betrieb.sichern(db_datei, ziel_dir,
                             stempel="2026-08-01T03-30-00")
    assert betrieb.staende(ziel_dir) == [alt, jung]
    assert betrieb.abraeumen(ziel_dir, behalten=1) == [alt]
    assert jung.exists()


def test_zwei_sicherungen_in_derselben_sekunde(db_datei, tmp_path):
    """`VACUUM INTO` überschreibt nicht — der zweite Stand bekommt einen Namen."""
    ziel_dir = tmp_path / "stände"
    a, _ = betrieb.sichern(db_datei, ziel_dir, stempel="2026-08-28T03-30-00")
    b, _ = betrieb.sichern(db_datei, ziel_dir, stempel="2026-08-28T03-30-00")
    assert a != b and a.exists() and b.exists()


# --------------------------------------------------------------------------
# Statusbericht

def _lauf(pfad, **felder):
    con = db.connect(pfad)
    spalten = ", ".join(felder)
    platz = ", ".join("?" for _ in felder)
    con.execute(f"INSERT INTO scrape_run ({spalten}) VALUES ({platz})",
                tuple(felder.values()))
    con.commit()
    con.close()


def test_verworfener_lauf_steht_mit_begruendung_auf_der_statusseite(db_datei, client):
    grund = ("nur 3 Produkte gegenüber 900 im letzten guten Lauf "
             "(Schwelle 50%) — Katalog unverändert gelassen")
    _lauf(db_datei, source="knuspr", started_at="2026-08-27T03:30:00",
          finished_at="2026-08-27T03:31:00", status="rejected", n_products=3,
          error=grund)
    r = client.get("/status")
    assert r.status_code == 200
    assert "Verworfene" in r.text
    assert "rejected" in r.text
    # Die Begründung selbst, nicht bloss die Tatsache eines Fehlschlags.
    assert "im letzten guten Lauf" in r.text
    # Der Zeitpunkt steht da, seit WB-379 aber lesbar statt als ISO-Feld: das
    # `T` aus `scrape_run` sah neben „2026-08-28 17:32:47" aus `orders` wie
    # zwei verschiedene Shops. Geprüft wird der ECHTE Zeitpunkt dieses Laufs,
    # durch denselben Filter geschickt — eine festgeschriebene Form („vorgestern
    # um 03:30") wäre je nach Kalendertag des Testlaufs falsch.
    assert webapp.zeit("2026-08-27T03:30:00") in r.text
    assert "2026-08-27T03:30:00" not in r.text, "das rohe ISO-Feld steht noch da"


def test_abgebrochener_lauf_ohne_status_faellt_auf(db_datei, client):
    """Ein Lauf ohne Abschluss ist der Fall, in dem ein Crawler still scheitert."""
    _lauf(db_datei, source="knuspr", started_at="2026-08-26T03:30:00")
    text = client.get("/status").text
    assert "ohne Abschluss" in text
    assert "abgebrochen" in text


def test_statusseite_ohne_jeden_lauf_sagt_etwas_sinnvolles(tmp_path):
    """Der Zustand direkt nach der Installation — Schweigen wäre irreführend."""
    pfad = tmp_path / "leer.db"
    con = db.connect(pfad)
    db.migrate(con)
    con.close()
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    with TestClient(webapp.create_app(db_path=pfad, image_dir=bilder)) as c:
        r = c.get("/status")
    assert r.status_code == 200
    assert "noch keinen erfolgreichen Lauf" in r.text
    assert "Noch kein Lauf verzeichnet" in r.text
    assert "Kein Lauf wurde verworfen." in r.text
    assert "0" in r.text


def test_statusseite_zeigt_produktzahl_und_letzten_guten_lauf(db_datei, client):
    con = db.connect(db_datei)
    n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                    ).fetchone()["n"]
    con.close()
    text = client.get("/status").text
    assert f"<strong>{n}</strong>" in text
    assert "Letzter erfolgreicher Lauf" in text


def test_statusbericht_zaehlt_nur_aktive_produkte(db_datei):
    con = db.connect(db_datei)
    con.execute("UPDATE product SET active = 0 WHERE id = (SELECT min(id) FROM product)")
    con.commit()
    n_aktiv = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                          ).fetchone()["n"]
    bericht = betrieb.statusbericht(con)
    con.close()
    assert bericht["produkte"] == n_aktiv
    assert bericht["letzter_ok"]["status"] == "ok"


# --------------------------------------------------------------------------
# Der Agent auf der Statusseite (WB-377)
#
# Die Statusseite eines Observability-Vorführstücks muss über die eigene
# Beobachtung Auskunft geben, sonst widerlegt sie sich selbst. Gemessen am
# 2026-08-29 in der echten Datenbank: 13 von 35 Nutzerzügen ohne `span_id` —
# nie in Phoenix angekommen, und der Shop sagte es niemandem.


class WeckenderChat:
    """Ein Chat, dessen `zustand()` die vLLM-Box wecken würde.

    Der Doppelgänger für die wichtigste Zusicherung dieses Tickets: er wirft,
    statt ein Magic Packet zu schicken. Ein `/status`, das ihn fragt, fällt
    damit im Test auf und nicht erst daran, dass nachts ein Rechner angeht.
    """

    def zustand(self):
        raise AssertionError(
            "/status hat nach dem Modellzustand gefragt. Dieser Aufruf weckt "
            "die vLLM-Box per Wake-on-LAN — eine Seite, die jemand nur "
            "aufmacht, um nachzusehen, darf keinen Rechner hochfahren.")


def _chatzug(pfad, *, span_id=None, wann="2026-08-28 20:00:00",
             vorschlaege=()):
    """Ein Zug: Zeile der Nutzerin plus Antwort, beide mit derselben Span-ID.

    So schreibt `assistant.chat.turn()` es auch — genau deshalb muss die
    Statusseite die Zeilen der Nutzerin zählen und nicht beide.
    """
    con = db.connect(pfad)
    # Es darf nur EINEN Warenkorb geben (Teilindex auf `orders.state`), also
    # hängen alle Züge am selben — wie im Betrieb auch.
    row = con.execute("SELECT id FROM orders WHERE state = 'draft'").fetchone()
    order_id = row["id"] if row else con.execute(
        "INSERT INTO orders (state, created_at) VALUES ('draft', ?)",
        (wann,)).lastrowid
    for rolle, inhalt in (("user", "brauche Butter"), ("assistant", "bitte")):
        mid = con.execute(
            "INSERT INTO chat_message (order_id, role, content, span_id,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (order_id, rolle, inhalt, span_id, wann)).lastrowid
        if rolle == "user":
            nutzerzeile = mid
    for entscheidung in vorschlaege:
        con.execute(
            "INSERT INTO chat_suggestion (chat_message_id, free_text, qty,"
            " decision) VALUES (?, 'Butter', 1, ?)",
            (nutzerzeile, entscheidung))
    con.commit()
    con.close()
    return nutzerzeile


def test_status_fragt_den_modellzustand_nicht_ab_und_weckt_die_box_nicht(
        db_datei, tmp_path, monkeypatch):
    """Die wichtigste Zusicherung von WB-377.

    `chat.zustand()` schickt `wake-vllm` los. Stünde dieser Aufruf auf der
    Statusseite, weckte jeder Blick auf sie einen Rechner im Nebenzimmer —
    96 s Anlaufzeit, für eine Auskunft, die niemand erbeten hat. Deshalb ein
    Doppelgänger, der wirft: der Test bleibt nur grün, solange die Seite
    schweigt und den zuletzt bekannten Stand zeigt.
    """
    from zettel.llm import wake

    def _nie(*a, **k):
        raise AssertionError("/status hat die Box angefasst.")

    # Zweiter Riegel: auch ein Weg an `app.state.chat` vorbei fällt auf.
    monkeypatch.setattr(wake, "health", _nie)
    monkeypatch.setattr(wake, "zustand", _nie)
    monkeypatch.setattr(wake, "wecker", _nie)

    bilder = tmp_path / "bilder"
    bilder.mkdir()
    app = webapp.create_app(db_path=db_datei, image_dir=bilder,
                            chat=WeckenderChat())
    with TestClient(app) as c:
        r = c.get("/status")
    assert r.status_code == 200
    assert "Modell" in r.text
    # Und die Seite sagt auch, warum sie schweigt.
    assert "weckt" in r.text


def _flach(text: str) -> str:
    """HTML ohne Zeilenumbrüche — ein Satz in der Vorlage darf umbrechen."""
    return " ".join(text.split())


def test_die_luecke_steht_als_zahl_auf_der_seite(db_datei, client):
    """„N von M Zügen ohne Trace" ist die ehrlichste Zeile dieser Seite."""
    _chatzug(db_datei, span_id="aabbccdd00112233", wann="2026-08-28 19:00:00")
    _chatzug(db_datei, span_id=None, wann="2026-08-28 20:00:00")
    _chatzug(db_datei, span_id=None, wann="2026-08-28 21:00:00")
    text = _flach(client.get("/status").text)
    assert "<strong>2 von 3</strong> Zügen haben keine Span-ID" in text
    assert "nie in Phoenix angekommen" in text
    assert "1 Züge sind angekommen (33 %)" in text
    # Der Zeitpunkt gehört dazu, sonst ist die Zahl nicht einzuordnen. Seit
    # WB-379 durch denselben Filter wie alle Zeitpunkte des Shops — eine
    # festgeschriebene Form („gestern um 19:00") wäre je nach Kalendertag des
    # Testlaufs falsch.
    assert f"Letzter Zug MIT Trace: {webapp.zeit('2026-08-28 19:00:00')}" in text
    assert f"Letzter Zug OHNE Trace: {webapp.zeit('2026-08-28 21:00:00')}" in text


def test_trace_luecke_zaehlt_zuege_und_nicht_zeilen(db_datei):
    """Jeder Zug schreibt zwei Zeilen — über beide gezählt stünde die
    doppelte Zahl auf der Seite, und niemand könnte sie einordnen."""
    _chatzug(db_datei, span_id="aabbccdd00112233")
    _chatzug(db_datei, span_id=None)
    con = db.connect(db_datei)
    luecke = betrieb.trace_luecke(con)
    con.close()
    assert luecke["zuege"] == 2
    assert luecke["mit_trace"] == 1
    assert luecke["ohne_trace"] == 1
    assert luecke["anteil"] == 0.5


def test_ohne_chat_gibt_es_keinen_anteil_statt_null_prozent(db_datei):
    """0 % hiesse „nichts kommt an", richtig ist „noch nichts passiert"."""
    con = db.connect(db_datei)
    luecke = betrieb.trace_luecke(con)
    con.close()
    assert luecke == {"zuege": 0, "mit_trace": 0, "ohne_trace": 0,
                      "letzter_mit": None, "letzter_ohne": None,
                      "anteil": None}


def test_zuordnungen_lassen_offene_vorschlaege_aus_der_quote(db_datei):
    """Dieselbe Rechnung wie `vorschlaege.quote()`, über den ganzen Bestand."""
    _chatzug(db_datei, vorschlaege=("kept", "removed", "removed", "offen"))
    con = db.connect(db_datei)
    z = betrieb.zuordnungen(con)
    con.close()
    assert (z["behalten"], z["verworfen"], z["offen"]) == (1, 2, 1)
    assert z["vorgeschlagen"] == 4
    assert z["quote"] == pytest.approx(1 / 3)


def test_ohne_entscheidung_gibt_es_keine_trefferquote(db_datei):
    """Eine 0.0 hiesse „alles falsch", wo „noch nichts gesagt" richtig ist."""
    _chatzug(db_datei, vorschlaege=("offen", "offen"))
    con = db.connect(db_datei)
    z = betrieb.zuordnungen(con)
    con.close()
    assert z["offen"] == 2 and z["quote"] is None


def test_die_zuordnungen_stehen_mit_ihren_labelnamen_auf_der_seite(
        db_datei, client):
    """Aus genau diesen Zahlen entstehen die Eval-Labels (Spec 8.1)."""
    _chatzug(db_datei, vorschlaege=("kept", "removed", "offen"))
    text = client.get("/status").text
    assert "1 behalten" in text
    assert "1 verworfen" in text
    assert "1 offen" in text
    assert "kept" in text and "removed" in text


def test_die_seite_nennt_den_tracer_auch_ohne_phoenix(db_datei, client):
    """Die Testsuite läuft mit `ZETTEL_TRACING=0` — genau der Fall, in dem
    eine Seite ohne diesen Abschnitt einfach schweigen würde."""
    text = client.get("/status").text
    assert "Beobachtung" in text
    assert "Tracing ist abgeschaltet" in text
    assert "ZETTEL_TRACING" in text


def test_ein_ablehnendes_phoenix_steht_auf_der_seite(db_datei, tmp_path):
    """Der Fall, für den es diese Seite gibt: der Exporter läuft, und nichts
    kommt an. Ohne Buchführung sähe das genauso aus wie „alles in Ordnung"."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (SimpleSpanProcessor,
                                                SpanExporter, SpanExportResult)

    from zettel import obs
    from zettel.obs import otel

    class Ablehnend(SpanExporter):
        def export(self, spans):
            return SpanExportResult.FAILURE

        def shutdown(self):
            pass

    provider = TracerProvider()
    schlange = otel.NichtBlockierend(
        SimpleSpanProcessor(otel.Buchfuehrend(Ablehnend())))
    provider.add_span_processor(schlange)
    obs.setze_provider(provider)
    try:
        t = provider.get_tracer("test")
        with t.start_as_current_span("chat.turn"):
            pass
        assert schlange.force_flush(10_000)

        bilder = tmp_path / "bilder"
        bilder.mkdir()
        with TestClient(webapp.create_app(db_path=db_datei, image_dir=bilder,
                                          chat=WeckenderChat())) as c:
            text = _flach(c.get("/status").text)
    finally:
        schlange.shutdown()
        obs.abbauen()

    assert "Phoenix nimmt die Spans nicht an" in text
    assert "1 Spans abgelehnt" in text
    # Ein gesetzter Provider schlägt die Umgebung: die Suite läuft mit
    # ZETTEL_TRACING=0, und „abgeschaltet" wäre hier trotzdem gelogen.
    assert "Tracing ist abgeschaltet" not in text


def test_der_statusbericht_geht_nicht_ins_netz(db_datei):
    """Kein Zweig darf eine Verbindung aufbauen — auch nicht der Modellteil.

    `modellstand()` liest die Konfiguration und mehr nicht; ein `httpx`, das
    hier eine Verbindung öffnete, fiele sofort auf.
    """
    import httpx

    def _nie(*a, **k):
        raise AssertionError("Der Statusbericht wollte ins Netz.")

    echt = httpx.Client.request
    httpx.Client.request = _nie
    try:
        con = db.connect(db_datei)
        bericht = betrieb.statusbericht(con)
        con.close()
    finally:
        httpx.Client.request = echt
    assert bericht["modell"]["endpunkt"]
    assert bericht["modell"]["fehler"] is None
    assert bericht["tracer"]["endpunkt"]


def test_eine_kaputte_modelladresse_steht_auf_der_seite(db_datei, tmp_path,
                                                        monkeypatch):
    """Ein Endpunkt, der keine Adresse sein kann, ist eine Altlast der
    Konfiguration und keine Netzstörung — und das ist die einzige Aussage
    über das Modell, die ohne Netzaufruf sicher zu treffen ist."""
    monkeypatch.setenv("ZETTEL_LLM_ENDPOINT", "ftp://alte-box/v1")
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    with TestClient(webapp.create_app(db_path=db_datei, image_dir=bilder,
                                      chat=WeckenderChat())) as c:
        text = c.get("/status").text
    assert "ftp://alte-box/v1" in text
    assert "nicht benutzbar" in text


# --------------------------------------------------------------------------
# Nachtlauf

def test_nachtlauf_crawlt_und_sichert_ohne_netz(tmp_path):
    """Derselbe Weg wie nachts, nur mit einem Doppelgänger statt knuspr.de."""
    pfad = tmp_path / "zettel.db"
    ziel_dir = tmp_path / "stände"
    ausgabe = []
    bericht = nachtlauf.lauf(str(pfad), begriffe=["milch"], image_dir=None,
                             http=FakeHTTP([_payload()]), pause_s=0,
                             sicherung_dir=str(ziel_dir), schreib=ausgabe.append)
    assert bericht["status"] == "ok"
    assert bericht["n_products"] > 1
    assert len(betrieb.staende(ziel_dir)) == 1
    assert any("Sicherung" in z for z in ausgabe)


def test_nachtlauf_sichert_auch_wenn_der_crawl_scheitert(tmp_path):
    """Die Sicherung schützt Bestellungen und Rezepte, nicht den Katalog."""
    class KaputtesHTTP:
        def get(self, url):
            raise OSError("Name or service not known")

    pfad = tmp_path / "zettel.db"
    ziel_dir = tmp_path / "stände"
    bericht = nachtlauf.lauf(str(pfad), begriffe=["milch"], image_dir=None,
                             http=KaputtesHTTP(), pause_s=0,
                             sicherung_dir=str(ziel_dir), schreib=lambda *_: None)
    assert bericht["status"] == "error"
    assert len(betrieb.staende(ziel_dir)) == 1


def test_nachtlauf_meldet_misserfolg_als_rueckgabewert(tmp_path, monkeypatch):
    """Ein Fehlschlag muss in `systemctl status` sichtbar sein, nicht nur in der DB."""
    pfad = tmp_path / "zettel.db"
    monkeypatch.setattr(nachtlauf, "lauf",
                        lambda *a, **k: {"status": "rejected", "run_id": 1,
                                         "n_products": 3, "error": "zu wenig"})
    assert nachtlauf.main(["--db", str(pfad), "--begriff", "milch"]) == 1
    monkeypatch.setattr(nachtlauf, "lauf",
                        lambda *a, **k: {"status": "ok", "run_id": 1,
                                         "n_products": 900, "error": None})
    assert nachtlauf.main(["--db", str(pfad), "--begriff", "milch"]) == 0


def test_user_agent_ist_ascii():
    """httpx kodiert Kopfzeilen als ASCII — ein Umlaut darin tötet den Lauf.

    Der Fehler passiert beim Bauen des Clients, also vor der ersten Anfrage:
    kein `scrape_run`-Eintrag, keine Begründung, nur ein Traceback im Journal.
    Genau so ist es am 2026-08-28 beim Probestart der Unit passiert.
    """
    nachtlauf.USER_AGENT.encode("ascii")


# --------------------------------------------------------------------------
# Begriffsliste

def test_begriffsliste_ist_breit_genug_fuer_einen_haushalt():
    """Der Katalog ist genau so breit wie diese Liste — sie ist der Umfang."""
    assert len(begriffsliste.BEGRIFFE) >= 100
    assert len(set(begriffsliste.BEGRIFFE)) == len(begriffsliste.BEGRIFFE)
    # Stichproben quer durch den Haushalt: Kühlregal, Vorrat, Reinigung,
    # Hygiene. Fehlt eine dieser Ecken, findet die Suche sie nie.
    for begriff in ("milch", "hackfleisch", "nudeln", "kartoffeln",
                    "toilettenpapier", "spuelmittel", "kaffee", "aepfel"):
        assert begriff in begriffsliste.BEGRIFFE


def test_begriffe_aus_umgebung_nimmt_datei_und_aufzaehlung(tmp_path):
    datei = tmp_path / "begriffe.txt"
    datei.write_text("milch\n# ein Kommentar\n\nbutter  # mit Rest\n",
                     encoding="utf-8")
    assert begriffsliste.begriffe_aus_umgebung(
        {"ZETTEL_BEGRIFFE": str(datei)}) == ["milch", "butter"]
    assert begriffsliste.begriffe_aus_umgebung(
        {"ZETTEL_BEGRIFFE": "milch, butter"}) == ["milch", "butter"]
    assert begriffsliste.begriffe_aus_umgebung({}) == list(begriffsliste.BEGRIFFE)
    with pytest.raises(ValueError):
        begriffsliste.begriffe_aus_umgebung({"ZETTEL_BEGRIFFE": " , "})


def test_begriffsliste_liegt_im_paket_und_nicht_im_skript():
    """WB-321 hatte den Ort offen gelassen: das Probeskript war der falsche."""
    assert (WURZEL / "zettel" / "scrapers" / "begriffe.py").is_file()


# --------------------------------------------------------------------------
# systemd-Units

@pytest.mark.parametrize("name", ["zettel.service", "zettel-crawl.service",
                                  "zettel-crawl.timer"])
def test_unit_existiert(name):
    assert (DEPLOY / name).is_file()


@pytest.mark.parametrize("name", ["zettel.service", "zettel-crawl.service"])
def test_unit_startet_das_venv_und_nicht_system_python(name):
    """System-Python hat ein zu altes `websockets`, der Shop startet dort nicht."""
    text = (DEPLOY / name).read_text(encoding="utf-8")
    zeilen = [z for z in text.splitlines() if z.startswith("ExecStart=")]
    assert len(zeilen) == 1
    (exec_start,) = zeilen
    assert exec_start.endswith(
        ("-m zettel.web.app", "-m zettel.scrapers.nachtlauf"))
    assert "/.venv/bin/python" in exec_start
    # Nicht bloss „enthält venv": ein zusätzliches nacktes python3 irgendwo im
    # Kommando wäre genau der Fehler, den dieser Test verhindern soll.
    assert " python3" not in exec_start and "=python3" not in exec_start
    assert "/usr/bin/python" not in exec_start


def test_timer_ist_persistent():
    """Ohne das fiele jeder Lauf aus, der in eine zugeklappte Nacht fällt."""
    text = (DEPLOY / "zettel-crawl.timer").read_text(encoding="utf-8")
    zeilen = [z.strip() for z in text.splitlines()]
    assert "Persistent=true" in zeilen
    assert any(z.startswith("OnCalendar=") for z in zeilen)
    assert "WantedBy=timers.target" in zeilen


def test_units_tragen_installationsabschnitte():
    """Ohne [Install] kann `systemctl --user enable` sie nicht einhängen."""
    web = (DEPLOY / "zettel.service").read_text(encoding="utf-8")
    assert "WantedBy=default.target" in web
    # Die Crawl-Unit ausdrücklich NICHT: sie wird vom Timer gestartet, ein
    # eigenes enable würde sie bei jedem Anmelden einmal loslaufen lassen.
    crawl = (DEPLOY / "zettel-crawl.service").read_text(encoding="utf-8")
    assert "[Install]" not in crawl


def test_readme_nennt_linger_und_die_installationsbefehle():
    """`Linger=no` heisst: der Dienst endet beim Abmelden (gemessen).

    Der Befehl steht mit Platzhalter statt Benutzername im README — das Repo
    ist zur Veröffentlichung gedacht (WB-388), die Zusicherung bleibt: der
    Satz muss da sein.
    """
    readme = (WURZEL / "README.md").read_text(encoding="utf-8")
    assert "loginctl enable-linger <benutzer>" in readme
    assert "systemctl --user enable --now zettel-crawl.timer" in readme
    assert "schläft" in readme


# --------------------------------------------------------------------------
# Veröffentlichungsfähig (WB-388): nichts Privates in getrackten Dateien

def test_keine_privaten_angaben_im_repo():
    """Die Tailnet-Adresse des Geräts, der Hostname der vLLM-Box und der
    Benutzername sind privat und stehen in keiner getrackten Datei mehr.

    Die verbotenen Wörter sind zusammengesetzt, damit dieser Test sich nicht
    selbst meldet. Der Benutzername wird klein geschrieben gesucht — der
    Klarname im LICENSE ist gewollt und beginnt gross.
    """
    verboten = ["100.117." + "80.100", "sphe" + "ron", "aar" + "on"]
    ergebnis = subprocess.run(["git", "ls-files", "-z"], cwd=WURZEL,
                              capture_output=True, check=True)
    funde = []
    for name in ergebnis.stdout.decode("utf-8").split("\0"):
        if not name:
            continue
        try:
            inhalt = (WURZEL / name).read_bytes().decode("utf-8",
                                                         errors="ignore")
        except OSError:
            continue  # im Index, aber gerade nicht auf der Platte
        funde += [f"{name}: {wort}" for wort in verboten if wort in inhalt]
    assert funde == []


@pytest.mark.parametrize("name", ["zettel.env", "picknick.env"])
def test_env_datei_ist_gitignort(name):
    """Die Datei trägt die private Adresse der vLLM-Box. Sie darf unter
    keinen Umständen Teil des Repos werden (WB-388).

    Beide Namen: `picknick.env` wird seit WB-401 noch gelesen, und eine
    private Adresse, die durch eine Umbenennung aus dem `.gitignore` fällt,
    muss genau einmal versehentlich eingecheckt werden, um für immer in der
    Historie zu stehen.
    """
    ergebnis = subprocess.run(["git", "check-ignore", "-q", name], cwd=WURZEL)
    assert ergebnis.returncode == 0
