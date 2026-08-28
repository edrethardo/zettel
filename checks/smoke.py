#!/usr/bin/env python3
"""Das Gate: läuft grün OHNE Netz, OHNE Modell und OHNE Phoenix.

    .venv/bin/python checks/smoke.py        # exit 0 grün, exit 1 echter Fehler

Fünf Fragen, jede mit eigener Zeile in der Ausgabe:

1. **Ist das Netz wirklich zu?** Nicht zugesichert, sondern erzwungen: bevor
   irgendetwas aus `picknick` importiert wird, werden `connect`, `bind`,
   `sendto`, `create_connection` und `getaddrinfo` für IPv4/IPv6 im ganzen
   Prozess durch etwas ersetzt, das `NetzVerboten` wirft. Der erste Check
   *versucht* dann eine Verbindung nach `localhost:6006` — dort läuft auf
   dieser Maschine tatsächlich ein Phoenix — und besteht genau dann, wenn sie
   scheitert. Damit ist „ohne Netz" für alles Folgende eine gemessene
   Eigenschaft dieses Prozesses und keine Behauptung im Kopf der Datei.
2. **Startet die App?**
3. **Antwortet die Katalogsuche?**
4. **Läuft der ganze Weg durch?** Katalog → einlegen → abschicken → abhaken,
   jeder Schritt am HTTP-Rand geklickt und in der Datenbank nachgesehen.
5. **Entsteht der Span-Baum aus Spec 7.1?** Gegen einen In-Memory-Exporter,
   mit einem echten `openai`-SDK auf `httpx.MockTransport` — der Weg durch den
   `OpenAIInstrumentor` ist derselbe wie im Betrieb, nur ohne Socket.
6. **Bindet der Prozess nicht auf `0.0.0.0`?**

Der Katalog dieses Laufs ist der Butter-Fall aus `OBSERVABILITY.md` in klein:
zu „Butter" gibt es ausschliesslich ButterBoyz-Spezialbutter, zu „Zahnpasta"
gar nichts. Der Trace soll den Unterschied zeigen, und er kann es nur, wenn
der Unterschied im Katalog angelegt ist statt behauptet.

Was dieses Gate NICHT abdeckt, steht in `GATES.md` und ist dort eine Liste,
keine Fussnote.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))


# --------------------------------------------------------------------------
# Die Netzsperre. Steht vor jedem Projekt-Import, damit sie auch für Module
# gilt, die sich `socket.socket` beim Import merken.

class NetzVerboten(RuntimeError):
    """Im Rauchtest darf nichts ins Netz. Etwas hat es trotzdem versucht."""


_IP = (socket.AF_INET, socket.AF_INET6)
_ECHTES_SOCKET = socket.socket


class _GesperrtesSocket(_ECHTES_SOCKET):
    """Ein Socket, das keine IP-Verbindung aufbaut und nicht lauscht.

    AF_UNIX bleibt erlaubt: `socket.socketpair()` ist auf Linux der
    Selbst-Wecker der asyncio-Schleife, und die braucht `TestClient`. Ein
    Prozess-interner Socket ist kein Netz.
    """

    def _nein(self, was, ziel=None) -> None:
        if self.family in _IP:
            raise NetzVerboten(
                f"{was}({ziel!r}) — der Rauchtest läuft ohne Netz. "
                "Was hier hin will, gehört hinter einen Doppelgänger.")

    def connect(self, address):
        self._nein("connect", address)
        return super().connect(address)

    def connect_ex(self, address):
        self._nein("connect_ex", address)
        return super().connect_ex(address)

    def bind(self, address):
        self._nein("bind", address)
        return super().bind(address)

    def sendto(self, *args):
        self._nein("sendto", args[-1] if args else None)
        return super().sendto(*args)


def _kein_getaddrinfo(host, port, *args, **kw):
    raise NetzVerboten(
        f"getaddrinfo({host!r}, {port!r}) — der Rauchtest löst keine Namen "
        "auf. Auch eine Namensauflösung ist ein Gang ins Netz.")


def _keine_verbindung(address, *args, **kw):
    raise NetzVerboten(
        f"create_connection({address!r}) — der Rauchtest läuft ohne Netz.")


def netz_sperren() -> None:
    socket.socket = _GesperrtesSocket
    socket.getaddrinfo = _kein_getaddrinfo
    socket.create_connection = _keine_verbindung


netz_sperren()

# Kein Tracer, also kein Exporter, der beim Bauen der App nach Phoenix greift.
# Die Span-Prüfung unten hängt ihren Provider selbst ein (`obs.setze_provider`)
# und braucht diese Variable nicht.
os.environ["PICKNICK_TRACING"] = "0"

import httpx  # noqa: E402
import openai  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openinference.instrumentation.openai import OpenAIInstrumentor  # noqa: E402
from openinference.semconv.trace import (  # noqa: E402
    DocumentAttributes,
    SpanAttributes,
)
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter)

from picknick import db, gerichte, obs, orders, recipes  # noqa: E402
from picknick.gerichte import chefkoch  # noqa: E402
from picknick.gerichte import lauf as gerichtelauf  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.assistant import entwurf as entwuerfe  # noqa: E402
from picknick.assistant import oberbegriffe  # noqa: E402
from picknick.catalog import categories, search  # noqa: E402
from picknick.assistant import vorschlaege  # noqa: E402
from picknick.llm import wake  # noqa: E402
from picknick.llm.client import Modellzugang  # noqa: E402
from picknick.obs import labels  # noqa: E402
from picknick.web import app as webapp  # noqa: E402

KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
DOKS = SpanAttributes.RETRIEVAL_DOCUMENTS


# --------------------------------------------------------------------------
# Ausgabe

class Bericht:
    """Eine Zeile je Frage. Am Ende eine Zahl und ein Rückgabewert."""

    def __init__(self) -> None:
        self.gruen = 0
        self.rot: list[str] = []

    def abschnitt(self, titel: str) -> None:
        print(f"\n-- {titel}")

    def ok(self, satz: str, beleg: str = "") -> None:
        self.gruen += 1
        print(f"[ok  ] {satz}" + (f" -- {beleg}" if beleg else ""))

    def fehler(self, satz: str, grund: str) -> None:
        self.rot.append(satz)
        print(f"[FAIL] {satz} -- {grund}")

    def pruefe(self, satz: str, fn) -> None:
        """Führt `fn` aus. Rückgabe ist der Beleg, eine Ausnahme der Grund."""
        try:
            beleg = fn()
        except Exception as e:  # noqa: BLE001 — der Grund gehört in die Zeile
            self.fehler(satz, f"{e.__class__.__name__}: {e}")
        else:
            self.ok(satz, "" if beleg is None else str(beleg))

    def ende(self) -> int:
        print()
        if self.rot:
            print(f"{len(self.rot)} von {self.gruen + len(self.rot)} Checks "
                  "rot:")
            for satz in self.rot:
                print(f"  - {satz}")
            return 1
        print(f"alle {self.gruen} Checks grün -- ohne Netz, ohne Modell, "
              "ohne Phoenix")
        return 0


def gleich(ist, soll, was: str) -> str:
    if ist != soll:
        raise AssertionError(f"{was}: {ist!r}, erwartet {soll!r}")
    return f"{was}={ist!r}"


def wahr(bedingung, grund: str) -> None:
    if not bedingung:
        raise AssertionError(grund)


# --------------------------------------------------------------------------
# Der Katalog dieses Laufs: der Butter-Fall in klein

#: Die fünf ButterBoyz stehen mit Namen, IDs und Preisen so im echten Katalog
#: (Stand 2026-08-28); nur die Gebinde sind hier gerundet, weil sie nichts
#: prüfen. Der Rest ist Beiwerk, damit bm25 überhaupt etwas zu unterscheiden
#: hat: in einem Katalog, in dem jedes Produkt „Butter" heisst, ist jeder Rang
#: gleich und die Sortierprüfung leer.
KATALOG = [
    # (external_id, name, cent, gebinde, l1, l2, l3)
    ("1771", "ButterBoyz handgemachte BIO Butter Chili & Röstzwiebel", 479,
     "125 g", "Milch, Molkerei & Butter", "Butter & Fette", "Markenbutter"),
    ("1772", "ButterBoyz handgemachte BIO Butter Feige & Anis", 469,
     "125 g", "Milch, Molkerei & Butter", "Butter & Fette", "Markenbutter"),
    ("1757", "ButterBoyz handgemachte BIO Kräuterbutter", 479, "150 g",
     "Milch, Molkerei & Butter", "Butter & Fette", "Markenbutter"),
    ("1766", "ButterBoyz handgemachte BIO Salzbutter", 469, "250 g",
     "Milch, Molkerei & Butter", "Butter & Fette", "Markenbutter"),
    ("1768", "ButterBoyz handgemachte BIO Steinpilzbutter", 499, "125 g",
     "Milch, Molkerei & Butter", "Butter & Fette", "Markenbutter"),
    ("mi1", "Miil Frische Landmilch 3,8% Vollmilch", 129, "1 l",
     "Milch, Molkerei & Butter", "Milch", "Frischmilch"),
    ("jo1", "Weihenstephan Naturjoghurt", 89, "500 g",
     "Joghurt & Desserts", "Naturjoghurt", "Joghurt mild"),
    ("zw1", "Zwiebeln", 149, "1 kg", "Obst & Gemüse", "Gemüse",
     "Zwiebeln & Knoblauch"),
    ("sp1", "Spaghetti No. 5", 189, "500 g", "Reis, Pasta & Getreide",
     "Pasta", "Spaghetti"),
    ("hf1", "Hackfleisch gemischt", 449, "500 g", "Fleisch & Fisch",
     "Hackfleisch", "gemischt"),
    ("pt1", "Passierte Tomaten", 99, "500 g", "Konserven & Eingelegtes",
     "Tomaten", "passiert"),
    ("tp1", "Toilettenpapier 3-lagig", 349, "10 Rollen",
     "Haushalt & Reinigung", "Papierwaren", "Toilettenpapier"),
]


def katalog_anlegen(pfad: Path) -> None:
    con = db.connect(pfad)
    try:
        db.migrate(con)
        for external_id, name, cent, gebinde, l1, l2, l3 in KATALOG:
            con.execute(
                "INSERT INTO product (source, external_id, name, brand,"
                " price_cents, unit_text, category_l1, category_l2,"
                " category_l3) VALUES ('knuspr', ?, ?, 'Testmarke', ?, ?,"
                " ?, ?, ?)",
                (external_id, name, cent, gebinde, l1, l2, l3))
        con.commit()
    finally:
        con.close()


def pid(con, name_teil: str) -> int:
    row = con.execute("SELECT id FROM product WHERE name LIKE ?",
                      (f"%{name_teil}%",)).fetchone()
    wahr(row is not None, f"Kein Produkt mit {name_teil!r} im Katalog.")
    return int(row["id"])


# --------------------------------------------------------------------------
# 1. Die Netzsperre steht

def checks_netz(b: Bericht) -> None:
    b.abschnitt("Netzfreiheit — erzwungen, nicht angenommen")

    def verbindung():
        # Auf DIESER Maschine läuft Phoenix auf 6006. Der Check besteht, weil
        # der Rauchtest es trotzdem nicht erreicht.
        try:
            socket.create_connection(("127.0.0.1", 6006), timeout=1)
        except NetzVerboten as e:
            return str(e).split(" — ")[0]
        raise AssertionError(
            "create_connection kam durch — die Sperre steht nicht, und "
            "jeder folgende Check könnte heimlich mit Phoenix geredet haben.")

    b.pruefe("create_connection nach 127.0.0.1:6006 wird abgewiesen",
             verbindung)

    def rohes_socket():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("127.0.0.1", 6006))
        except NetzVerboten:
            return "connect() wirft NetzVerboten"
        finally:
            s.close()
        raise AssertionError("socket.connect kam durch.")

    b.pruefe("ein rohes AF_INET-Socket kommt nicht heraus", rohes_socket)

    def namen():
        try:
            socket.getaddrinfo("localhost", 6006)
        except NetzVerboten:
            return "getaddrinfo wirft NetzVerboten"
        raise AssertionError("getaddrinfo kam durch.")

    b.pruefe("auch die Namensauflösung ist gesperrt", namen)

    def lauschen():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
        except NetzVerboten:
            return "bind() wirft NetzVerboten"
        finally:
            s.close()
        raise AssertionError("bind kam durch.")

    # Damit steht auch fest, dass dieser Lauf keinen lauschenden Socket
    # aufmacht — nicht auf 0.0.0.0 und auf sonst nichts.
    b.pruefe("nichts in diesem Prozess kann lauschen", lauschen)

    b.pruefe("Tracing ist aus, also greift auch der Exporter nicht",
             lambda: gleich(obs.an(), False, "obs.an()"))


# --------------------------------------------------------------------------
# 2.–4. Die App, die Suche, der ganze Weg

def checks_app(b: Bericht, db_datei: Path, bild_dir: Path) -> None:
    b.abschnitt("Die App startet und der Katalog antwortet")

    app = webapp.create_app(db_path=db_datei, image_dir=bild_dir)
    with TestClient(app) as client:
        b.pruefe("GET / weist auf den Katalog",
                 lambda: gleich(
                     client.get("/", follow_redirects=False).headers["location"],
                     "/katalog", "location"))

        seite = client.get("/katalog")
        b.pruefe("GET /katalog liefert eine Seite mit Produkten",
                 lambda: (gleich(seite.status_code, 200, "status"),
                          wahr("ButterBoyz" in seite.text,
                               "Kein Produkt auf der Katalogseite."))[0])

        treffer = client.get("/produkte", params={"q": "butter"})
        b.pruefe("die Suche antwortet mit der Trefferliste",
                 lambda: (gleich(treffer.status_code, 200, "status"),
                          wahr("Salzbutter" in treffer.text,
                               "„butter“ findet die Salzbutter nicht."))[0])

        con = db.connect(db_datei)
        try:
            b.pruefe(
                "die Suche sortiert nach Wortstufe, dann nach Rang",
                lambda: _rang_absteigend(search.search(con, "butter",
                                                       limit=5)))
            b.pruefe(
                "ein Begriff ohne Treffer liefert eine leere Liste, "
                "keinen Fehler",
                lambda: gleich(search.search(con, "Zahnpasta"), [],
                               "search('Zahnpasta')"))
            b.pruefe("der Kategoriebaum zählt den ganzen Teilbaum",
                     lambda: _baum(categories.tree(con)))

            b.abschnitt("Der ganze Weg: Katalog -> einlegen -> abschicken "
                        "-> abhaken")

            produkt = pid(con, "Salzbutter")
            b.pruefe(
                "einlegen legt genau eine Zeile in den Warenkorb",
                lambda: _einlegen(client, con, produkt))
            b.pruefe("der Warenkorb zeigt sie",
                     lambda: _korb_zeigt(client, "Salzbutter"))
            b.pruefe("abschicken macht aus dem Warenkorb eine offene "
                     "Bestellung",
                     lambda: _abschicken(client, con))
            b.pruefe("die Pick-Ansicht führt sie auf",
                     lambda: _pick_zeigt(client, con, "Salzbutter"))
            b.pruefe("abhaken setzt den Haken und die Bestellung auf erledigt",
                     lambda: _abhaken(client, con))
            b.pruefe("der Haken lässt sich zurücknehmen "
                     "(erledigt -> offen, bewusst)",
                     lambda: _haken_zurueck(client, con))
        finally:
            con.close()


def _rang_absteigend(treffer: list[dict]) -> str:
    """Rang positiv, Stufen absteigend, und der Rang fällt INNERHALB der Stufe.

    Seit WB-339 sortiert die Suche erst nach `wortstufe` (ein ganzes Wort im
    Namen schlägt einen blossen Präfixtreffer), dann nach Rang. Über die ganze
    Liste darf der Rang darum springen — geprüft wird deshalb beides getrennt.
    Ohne die Stufenprüfung wäre der Check stillschweigend schwächer geworden.
    """
    wahr(treffer, "Keine Treffer für „butter“.")
    raenge = [t["rang"] for t in treffer]
    wahr(all(r > 0 for r in raenge), f"Nicht alle Ränge positiv: {raenge}")
    stufen = [t["wortstufe"] for t in treffer]
    wahr(stufen == sorted(stufen, reverse=True),
         f"Wortstufen nicht absteigend: {stufen}")
    for stufe in sorted(set(stufen), reverse=True):
        innen = [t["rang"] for t in treffer if t["wortstufe"] == stufe]
        wahr(innen == sorted(innen, reverse=True),
             f"Ränge in Stufe {stufe} nicht absteigend: {innen}")
    return (f"{len(treffer)} Treffer, Stufen {stufen}, Ränge je Stufe "
            f"absteigend {raenge[0]:.3g} … {raenge[-1]:.3g}")


def _baum(baum: list[dict]) -> str:
    """Der Kategoriebaum: Namen alphabetisch, `anzahl` über den Teilbaum.

    Geprüft wird die eine Eigenschaft, die man leicht falsch baut: ein Knoten
    zählt alles unter sich und nicht nur, was direkt an ihm hängt — sonst
    stünde in der Oberfläche „Molkerei (0)".
    """
    namen = [k["name"] for k in baum]
    wahr(namen == sorted(namen), f"Ebene 1 nicht sortiert: {namen}")
    milch = [k for k in baum if k["name"] == "Milch, Molkerei & Butter"]
    wahr(milch, f"Die Molkerei fehlt im Baum: {namen}")
    gleich(milch[0]["anzahl"], 6, "Molkerei gesamt")
    butter = [k for k in milch[0]["kinder"] if k["name"] == "Butter & Fette"]
    wahr(butter, "„Butter & Fette“ fehlt unter der Molkerei.")
    gleich(butter[0]["anzahl"], 5, "Butter & Fette")
    return f"{len(baum)} Warengruppen, Molkerei 6 davon 5 Butter"


def _einlegen(client, con, produkt: int) -> str:
    antwort = client.post("/katalog/einlegen",
                          data={"product_id": str(produkt), "qty": "2"},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "status")
    zeilen = orders.inhalt(con)
    gleich(len(zeilen), 1, "Zeilen im Korb")
    gleich(zeilen[0]["product_id"], produkt, "product_id")
    gleich(zeilen[0]["qty"], 2, "qty")
    return f"1 Zeile, Menge {zeilen[0]['qty']}"


def _korb_zeigt(client, name_teil: str) -> str:
    antwort = client.get("/warenkorb")
    gleich(antwort.status_code, 200, "status")
    wahr(name_teil in antwort.text, f"{name_teil} steht nicht im Warenkorb.")
    return "die Zeile steht auf der Seite"


def _abschicken(client, con) -> str:
    antwort = client.post("/warenkorb/abschicken", data={"note": "Rauchtest"},
                          follow_redirects=False)
    wahr(antwort.status_code in (204, 303),
         f"Unerwarteter Status {antwort.status_code}")
    offen = orders.offene(con)
    gleich(len(offen), 1, "offene Bestellungen")
    gleich(orders.inhalt(con), [], "Warenkorb danach")
    return f"Bestellung {offen[0]['id']} steht auf 'offen'"


def _pick_zeigt(client, con, name_teil: str) -> str:
    order_id = orders.naechste(con)
    wahr(order_id is not None, "Keine offene Bestellung für die Pick-Liste.")
    antwort = client.get("/pick")
    gleich(antwort.status_code, 200, "status")
    wahr(name_teil in antwort.text, f"{name_teil} fehlt in der Pick-Liste.")
    return f"Bestellung {order_id} mit der Zeile"


def _abhaken(client, con) -> str:
    order_id = orders.naechste(con)
    item = orders.posten(con, order_id)[0]["id"]
    antwort = client.post(f"/pick/{order_id}/posten/{item}",
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "status")
    zeile = orders.posten(con, order_id)[0]
    wahr(zeile["gepickt"], "Der Posten ist nicht abgehakt.")
    gleich(orders.bestellung(con, order_id)["state"], "erledigt", "Zustand")
    return f"Bestellung {order_id} erledigt"


def _haken_zurueck(client, con) -> str:
    order_id = orders.bestellungen(con, "erledigt")[0]["id"]
    item = orders.posten(con, order_id)[0]["id"]
    antwort = client.post(f"/pick/{order_id}/posten/{item}",
                          data={"gepickt": "0"},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "status")
    gleich(orders.bestellung(con, order_id)["state"], "offen", "Zustand")
    return f"Bestellung {order_id} wieder offen"


# --------------------------------------------------------------------------
# 5. Der Span-Baum gegen einen In-Memory-Exporter

TOKEN_PROMPT, TOKEN_COMPLETION = 137, 42


def _mock_zugang(*inhalte: str) -> Modellzugang:
    """Ein echtes `openai`-SDK auf einem HTTP-Doppelgänger.

    Kein Socket, aber der ganze Weg durch das SDK — und damit durch den
    `OpenAIInstrumentor`, von dem die LLM-Spans kommen. Ein schlichter
    Fake-Client erzeugte gar keinen LLM-Span, und der Baum wäre genau an der
    Hälfte ungeprüft, die Tokenzahlen trägt.
    """
    antworten = list(inhalte)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "object": "list",
                "data": [{"id": "Qwen3.8-27B-Instruct", "object": "model",
                          "created": 0, "owned_by": "vllm"}]})
        wahr(antworten, "Es wurde öfter gefragt als geantwortet.")
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 0,
            "model": "Qwen3.8-27B-Instruct",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": antworten.pop(0)}}],
            "usage": {"prompt_tokens": TOKEN_PROMPT,
                      "completion_tokens": TOKEN_COMPLETION,
                      "total_tokens": TOKEN_PROMPT + TOKEN_COMPLETION}})

    client = openai.OpenAI(
        base_url="http://box.test/v1", api_key="1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return Modellzugang(endpunkt="http://box.test/v1", client=client)


class _Box:
    """Die Box bedient — ohne sie anzufassen."""

    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="Qwen3.8-27B-Instruct")


def _extract(*paare) -> str:
    """Stufe 1 wie seit WB-340: je Zutat eine KETTE von Suchbegriffen."""
    return json.dumps(
        {"begriffe": [{"suchbegriffe": list(b) if isinstance(b, tuple) else [b],
                       "menge": m} for b, m in paare]}, ensure_ascii=False)


def _choose(*tripel) -> str:
    return json.dumps({"auswahl": [{"begriff": b, "produkt_id": p, "menge": m}
                                   for b, p, m in tripel]},
                      ensure_ascii=False)


def checks_spans(b: Bericht, db_datei: Path) -> None:
    b.abschnitt("Der Span-Baum aus Spec 7.1 gegen einen In-Memory-Exporter")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs.setze_provider(provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    con = db.connect(db_datei)
    try:
        salzbutter = pid(con, "Salzbutter")
        # Der Fall aus OBSERVABILITY.md: „Butter" legt nur Spezialbutter vor,
        # „Zahnpasta" gar nichts.
        agent = chatmodul.Chat(
            _mock_zugang(_extract(("Butter", 1), ("Zahnpasta", 1)),
                         _choose(("Butter", salzbutter, 1))),
            wecker=_Box())
        ergebnis = agent.turn(
            con, "dazu brauche ich noch Zahnpasta und Butter")

        spans = exporter.get_finished_spans()
        namen = [s.name for s in spans]

        b.pruefe("der Baum hat genau die Form aus der Spec",
                 lambda: gleich(namen,
                                ["plan.extract", "catalog.search",
                                 "catalog.search", "plan.choose", "chat.turn"],
                                "Spans"))
        b.pruefe("die Span-Kinds stimmen — catalog.search ist RETRIEVER",
                 lambda: gleich([(s.name, s.attributes[KIND]) for s in spans],
                                [("plan.extract", "LLM"),
                                 ("catalog.search", "RETRIEVER"),
                                 ("catalog.search", "RETRIEVER"),
                                 ("plan.choose", "LLM"),
                                 ("chat.turn", "CHAIN")], "Kinds"))
        b.pruefe("alles hängt unter chat.turn, ein Trace statt fünf",
                 lambda: _verschachtelt(spans))
        b.pruefe("die LLM-Spans tragen getrennte Tokenzahlen "
                 "(vom Instrumentor, nicht von Hand)",
                 lambda: _tokenzahlen(spans))
        b.pruefe("„Butter“ legt Kandidaten mit ID, Inhalt und Score vor",
                 lambda: _dokumente(spans, "Butter"))
        b.pruefe("„Zahnpasta“ legt null Kandidaten vor — und der Span sagt es",
                 lambda: _leere_suche(spans, "Zahnpasta"))
        b.pruefe("chat.turn fasst den Zug zusammen (rejected, weakest_*)",
                 lambda: _zusammenfassung(spans, ergebnis))
        b.pruefe("die Span-ID des Zugs steht an der Chatzeile "
                 "(der Haken für die Annotation)",
                 lambda: _span_id_verankert(con, ergebnis, spans))

        # WB-340: eine Zutat mit zwei Begriffen. „Salzbutter" findet genau ein
        # Produkt, „Butter" die übrigen vier — gewählt wird eines, das NUR der
        # zweite Begriff gebracht hat.
        exporter.clear()
        kette_agent = chatmodul.Chat(
            _mock_zugang(_extract((("Salzbutter", "Butter"), 1)),
                         _choose(("Salzbutter",
                                  pid(con, "Kräuterbutter"), 1))),
            wecker=_Box())
        kette_ergebnis = kette_agent.turn(con, "Butter")
        kette_spans = exporter.get_finished_spans()

        b.pruefe("die Begriffskette wird ganz gesucht, die Treffer werden "
                 "nach Produkt-ID vereinigt",
                 lambda: _vereinigung(kette_spans))
        b.pruefe("je Kandidat steht im Span, über welchen Begriff er kam",
                 lambda: _herkunft(kette_spans))
        b.pruefe("search_term nennt den Begriff, der den GEWÄHLTEN "
                 "Kandidaten brachte",
                 lambda: _herkunft_am_vorschlag(kette_ergebnis))
    finally:
        con.close()
        OpenAIInstrumentor().uninstrument()
        obs.abbauen()


def _wurzel(spans):
    treffer = [s for s in spans if s.name == "chat.turn"]
    gleich(len(treffer), 1, "chat.turn-Spans")
    return treffer[0]


def _verschachtelt(spans) -> str:
    wurzel = _wurzel(spans)
    wahr(wurzel.parent is None, "chat.turn hat einen Elternteil.")
    kinder = [s for s in spans if s.name != "chat.turn"]
    for kind in kinder:
        wahr(kind.parent is not None, f"{kind.name} hängt an nichts.")
        wahr(kind.parent.span_id == wurzel.context.span_id,
             f"{kind.name} hängt nicht unter chat.turn.")
        wahr(kind.context.trace_id == wurzel.context.trace_id,
             f"{kind.name} liegt in einem anderen Trace.")
    return f"{len(kinder)} Kinder unter einer Wurzel"


def _tokenzahlen(spans) -> str:
    for name in ("plan.extract", "plan.choose"):
        a = [s for s in spans if s.name == name][0].attributes
        gleich(a[KIND], "LLM", f"{name} kind")
        gleich(a[SpanAttributes.LLM_TOKEN_COUNT_PROMPT], TOKEN_PROMPT,
               f"{name} prompt")
        gleich(a[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION],
               TOKEN_COMPLETION, f"{name} completion")
        gleich(a[SpanAttributes.LLM_MODEL_NAME], "Qwen3.8-27B-Instruct",
               f"{name} modell")
        wahr(a[SpanAttributes.INPUT_VALUE], f"{name} ohne Eingabe")
        wahr(a[SpanAttributes.OUTPUT_VALUE], f"{name} ohne Ausgabe")
    return f"{TOKEN_PROMPT} prompt / {TOKEN_COMPLETION} completion je Stufe"


def _suche(spans, begriff: str):
    # Gefunden wird über `picknick.term` — die Zutat. `input.value` ist seit
    # WB-340 die ganze Begriffskette als JSON.
    treffer = [s for s in spans if s.name == "catalog.search"
               and s.attributes.get("picknick.term") == begriff]
    gleich(len(treffer), 1, f"catalog.search({begriff})")
    return treffer[0]


def _dokumente(spans, begriff: str) -> str:
    a = _suche(spans, begriff).attributes
    n = a["picknick.candidates"]
    wahr(n == 5, f"{n} Kandidaten, erwartet 5 — genau der Fall aus OBSERVABILITY.md")
    scores = []
    for i in range(n):
        p = f"{DOKS}.{i}."
        wahr(a[p + DocumentAttributes.DOCUMENT_ID].isdigit(),
             "Dokument ohne ID")
        wahr("ButterBoyz" in a[p + DocumentAttributes.DOCUMENT_CONTENT],
             "Dokument ohne lesbaren Inhalt")
        wahr(a[p + DocumentAttributes.DOCUMENT_CONTENT].endswith("€"),
             "Der Inhalt zeigt nicht, was das Modell sah (Name/Gebinde/Preis)")
        scores.append(a[p + DocumentAttributes.DOCUMENT_SCORE])
    wahr(all(s > 0 for s in scores), f"Score nicht positiv: {scores}")
    # NICHT „Scores absteigend": die Dokumente stehen in der Reihenfolge, in
    # der sie dem Modell vorlagen — erst die Kette (WB-340), darin die
    # Wortstufe vor dem Rang (WB-339). Das sagt OBSERVABILITY.md auch so. Der
    # Score gilt innerhalb eines Begriffs; geprüft wird deshalb, dass er
    # positiv ist und dass `rank_top` wirklich der beste ist.
    gleich(a["picknick.rank_top"], max(scores), "rank_top")
    return f"{n} Dokumente, bester Score {max(scores):.4g}"


def _leere_suche(spans, begriff: str) -> str:
    a = _suche(spans, begriff).attributes
    gleich(a["picknick.candidates"], 0, "candidates")
    wahr("picknick.rank_top" not in a,
         "Ein rank_top ohne Treffer wäre eine erfundene Zahl.")
    return "candidates=0, kein rank_top"


def _zusammenfassung(spans, ergebnis) -> str:
    a = _wurzel(spans).attributes
    gleich(a[SpanAttributes.INPUT_VALUE],
           "dazu brauche ich noch Zahnpasta und Butter", "input")
    gleich(a[obs.PFAD], "llm", "picknick.path")
    gleich(a["picknick.terms"], 2, "terms")
    gleich(a["picknick.products"], 1, "products")
    gleich(a["picknick.free_text"], 1, "free_text")
    # Der Kern des Butter-Falls: das Modell hat NICHTS erfunden.
    gleich(a["picknick.rejected"], 0, "rejected")
    gleich(a["picknick.weakest_term"], "Zahnpasta", "weakest_term")
    gleich(a["picknick.weakest_rank"], 0.0, "weakest_rank")
    gleich(a["session.id"], f"korb-{ergebnis.order_id}", "session.id")
    ausgabe = json.loads(a[SpanAttributes.OUTPUT_VALUE])
    gleich([v["name"] for v in ausgabe],
           [v["name"] for v in ergebnis.vorschlaege], "Vorschlagsliste")
    return "rejected=0, weakest_term='Zahnpasta'"


def _vereinigung(spans) -> str:
    """Ein Span je Zutat, mit der ganzen Kette — und entdoppelten Treffern."""
    namen = [s.name for s in spans]
    gleich(namen, ["plan.extract", "catalog.search", "plan.choose",
                   "chat.turn"], "Spans (ein RETRIEVER je ZUTAT)")
    a = _suche(spans, "Salzbutter").attributes
    gleich(json.loads(a[SpanAttributes.INPUT_VALUE]), ["Salzbutter", "Butter"],
           "input.value")
    gleich(a["picknick.search_terms"], "Salzbutter, Butter", "search_terms")
    n = a["picknick.candidates"]
    ids = [a[f"{DOKS}.{i}." + DocumentAttributes.DOCUMENT_ID]
           for i in range(n)]
    gleich(n, 5, "Kandidaten der Vereinigung")
    gleich(len(set(ids)), n, "verschiedene Produkt-IDs")
    return f"{n} Kandidaten aus zwei Begriffen, keine Dopplung"


def _herkunft(spans) -> str:
    a = _suche(spans, "Salzbutter").attributes
    n = a["picknick.candidates"]
    via = [json.loads(a[f"{DOKS}.{i}." + DocumentAttributes.DOCUMENT_METADATA]
                      )["via"] for i in range(n)]
    gleich(sorted(via), ["Butter"] * 4 + ["Salzbutter"], "Herkunft je Dokument")
    return "1 × via „Salzbutter“, 4 × via „Butter“"


def _herkunft_am_vorschlag(ergebnis) -> str:
    zeile = ergebnis.vorschlaege[0]
    wahr("Kräuterbutter" in zeile["name"], f"Gewählt wurde {zeile['name']!r}.")
    # Die Kräuterbutter kam über „Butter", nicht über die Zutat „Salzbutter" —
    # daran hängt die Eval-Erklärung aus WB-329.
    gleich(zeile["search_term"], "Butter", "search_term")
    return "„Butter“ statt der Zutat „Salzbutter“"


def _span_id_verankert(con, ergebnis, spans) -> str:
    wurzel = _wurzel(spans)
    row = con.execute("SELECT span_id FROM chat_message WHERE id = ?",
                      (ergebnis.chat_message_id,)).fetchone()
    erwartet = f"{wurzel.context.span_id:016x}"
    gleich(row["span_id"], erwartet, "chat_message.span_id")
    return erwartet


# --------------------------------------------------------------------------
# 6. Die Gerichtequelle (WB-338) — ohne Netz, gegen die aufgezeichnete Antwort

FIXTURES = WURZEL / "tests" / "fixtures"


class _Chefkoch:
    """Chefkoch, aufgezeichnet. Der Rauchtest hat kein Netz — das ist Punkt 1.

    Genau deshalb steht dieser Abschnitt hier: er belegt, dass der Weg von
    der fremden Antwort bis in die Vorschlagsliste ohne einen einzigen Socket
    durchläuft. Was ins Netz will, gehört hinter einen Doppelgänger.
    """

    def __init__(self):
        self.suche = json.loads(
            (FIXTURES / "chefkoch_pho_suche.json").read_text(encoding="utf-8"))
        self.rezept = json.loads(
            (FIXTURES / "chefkoch_pho_rezept.json").read_text(encoding="utf-8"))
        self.geholt = []

    def get(self, url):
        self.geholt.append(url)
        payload = self.suche if "?query=" in url else self.rezept
        return _JSON(payload)


class _JSON:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


class _NieGefragt:
    def get(self, url):
        raise AssertionError(f"Es ging doch ins Netz: {url}")


def checks_gerichte(b: Bericht, db_datei: Path) -> None:
    b.abschnitt("Gerichte aus der Quelle statt aus dem Gedächtnis "
                "(WB-338, WB-367)")

    quelle = _Chefkoch()
    con = db.connect(db_datei)
    try:
        b.pruefe("gewählt wird nach gewichteter Note — die rohe Höchstnote "
                 "und die Platzhalter-Stimmen verlieren",
                 lambda: _beste_wahl(quelle))
        b.pruefe("der Abruf schreibt Rezept, Zutaten und Herkunft weg",
                 lambda: _abruf(con, quelle))
        b.pruefe("der zweite Zugriff kommt aus dem Speicher, ohne Netz",
                 lambda: _aus_dem_speicher(con))
        b.pruefe("der Chat-Zug nimmt die Zutaten aus dem Rezept "
                 "(picknick.path = chefkoch)", lambda: _zug_aus_quelle(con))
        b.pruefe("ein unbekanntes Gericht wird SOFORT geholt, und ein "
                 "Ausfall fällt sauber auf das Modell zurück",
                 lambda: _zug_ohne_speicher(con))
    finally:
        con.close()


def _beste_wahl(quelle) -> str:
    """Zwei naive Regeln, die beide etwas anderes gewählt hätten.

    In der aufgezeichneten Suche nach „pho" steht das gewählte Rezept
    zufällig an erster Stelle — „nimm das erste" wäre hier also nicht
    aufgefallen. Was auffällt, sind die beiden anderen Regeln: die rohe
    Höchstnote (5,00 aus zwei Stimmen) und die höchste GEWICHTETE Note ohne
    die Plus-Regel (4,71 aus „255" Stimmen, einem Platzhalter). Beide
    verlieren, und das ist der Check.
    """
    treffer = chefkoch.parse_treffer(quelle.suche)
    wahl = chefkoch.bestes(treffer)
    hoechste = max(treffer, key=lambda t: t["rating"])
    wahr(hoechste["rezept_id"] != wahl["rezept_id"],
         "Die rohe Höchstnote hat gewonnen — die Stimmen wiegen nicht mit.")
    ungefiltert = max(treffer, key=chefkoch.gewicht)
    wahr(ungefiltert["rezept_id"] != wahl["rezept_id"] and ungefiltert["plus"],
         "Ein Plus-Rezept mit Platzhalter-Stimmen hat gewonnen.")
    wahr(wahl is max((t for t in treffer if not t["plus"]),
                     key=chefkoch.gewicht),
         "Nicht das bestgewichtete der übrigen Rezepte.")
    return (f"{wahl['titel'][:24]!r} {wahl['rating']:.2f}/{wahl['votes']} "
            f"(Gewicht {chefkoch.gewicht(wahl):.2f}) schlägt "
            f"{hoechste['rating']:.2f}/{hoechste['votes']} und "
            f"{ungefiltert['rating']:.2f}/{ungefiltert['votes']} (Plus)")


def _abruf(con, quelle) -> str:
    zustand = gerichtelauf.hole_eines(con, quelle, "Pho", pause_s=0,
                                      schreib=lambda _: None)
    gleich(zustand, "ok", "status")
    gericht = gerichte.gericht(con, "Pho")
    wahr(gericht is not None, "Nach dem Abruf steht nichts im Speicher.")
    rezept = gericht["rezept"]
    wahr(rezept["source_url"].startswith("https://www.chefkoch.de/"),
         "Die Herkunft fehlt am Rezept.")
    wahr(rezept["cook_minutes"] == 480, "Die Kochzeit fehlt.")
    wahr(len(chefkoch.schritte(rezept["instructions"])) > 5,
         "Die Zubereitung ist eine Wand statt Schritte.")
    return (f"{len(gericht['zutaten'])} Zutaten, "
            f"{len(chefkoch.schritte(rezept['instructions']))} Schritte, "
            f"{len(quelle.geholt)} Anfragen")


def _aus_dem_speicher(con) -> str:
    q = gerichte.Quelle(holer=gerichte.nicht_holen)
    # Kein http-Doppelgänger im Spiel: was hier noch ins Netz wollte, hätte
    # keine Adresse — und die Netzsperre aus Punkt 1 fienge es ohnehin.
    gefunden = q.gericht(con, "pho")
    wahr(gefunden is not None, "Der Speicher trägt nicht.")
    wahr(q.holen(con, "Pho") is None,
         "Ein frischer Eintrag hat trotzdem einen Abruf ausgelöst.")
    return f"{len(gefunden['zutaten'])} Zutaten, 0 Anfragen"


def _zug_aus_quelle(con) -> str:
    zugang = _mock_zugang(
        _extract((("Passierte Tomaten", "Tomaten"), 1), (("Zwiebeln",), 1)),
        _choose(("Passierte Tomaten", pid(con, "Passierte Tomaten"), 1),
                ("Zwiebeln", pid(con, "Zwiebeln"), 1)))
    agent = chatmodul.Chat(zugang, wecker=_Box(),
                           quelle=gerichte.Quelle(holer=gerichte.nicht_holen))
    ergebnis = agent.turn(con, "alles für Pho")
    gleich(ergebnis.weg, "chefkoch", "picknick.path")
    wahr(ergebnis.quelle_url and ergebnis.quelle_name,
         "Die Herkunft steht nicht am Ergebnis.")
    return (f"{ergebnis.n_produkte} Produkte aus "
            f"{ergebnis.quelle_name[:26]!r}")


def _zug_ohne_speicher(con) -> str:
    """Der Kern von WB-367, in beide Richtungen.

    Stufe 1 nennt ein Gericht, zu dem nichts im Speicher steht. Der Zug muss
    es SOFORT holen und mit dem Rezept antworten — bis WB-367 stiess er
    einen eigenen Prozess an und riet solange weiter. Und wenn die Quelle
    ausfällt, muss derselbe Zug sauber auf das Modell zurückfallen.
    """
    def zugang_fuer(gericht: str, *antworten):
        erst = json.loads(_extract((("Spaghetti",), 1)))
        erst["gericht"] = gericht
        return _mock_zugang(json.dumps(erst, ensure_ascii=False), *antworten)

    # 1. Der Normalfall: geholt, und die Zutaten kommen aus dem Rezept.
    #    Der Holer bekommt den aufgezeichneten Doppelgänger; ein Socket
    #    entsteht nirgends (und dürfte es hier auch gar nicht, Punkt 1).
    geholt = []

    def holer(c, name, *, frist_s=None):
        geholt.append((name, frist_s))
        return gerichtelauf.hole_jetzt(c, name, http=_Chefkoch())

    zugang = zugang_fuer(
        "Spaghetti Carbonara",
        _extract((("Zwiebeln",), 1)),
        _choose(("Zwiebeln", pid(con, "Zwiebeln"), 1)))
    agent = chatmodul.Chat(zugang, wecker=_Box(),
                           quelle=gerichte.Quelle(holer=holer))
    ergebnis = agent.turn(con, "alles für Spaghetti Carbonara")
    gleich(ergebnis.weg, "chefkoch", "picknick.path")
    gleich(ergebnis.abruf, "ok", "picknick.dish_fetch")
    wahr(geholt == [("Spaghetti Carbonara", chefkoch.TIMEOUT_SYNC_S)],
         f"Nicht genau ein Abruf mit kurzer Frist: {geholt!r}")

    # 2. Der Ausfall: Chefkoch antwortet nicht. Derselbe Zug läuft mit den
    #    geratenen Begriffen zu Ende und sagt, dass sie geraten sind.
    def kaputt(c, name, *, frist_s=None):
        return gerichtelauf.hole_jetzt(c, name, http=_NieGefragt())

    zugang = zugang_fuer("Lasagne",
                         _choose(("Spaghetti", pid(con, "Spaghetti"), 1)))
    agent = chatmodul.Chat(zugang, wecker=_Box(),
                           quelle=gerichte.Quelle(holer=kaputt))
    ergebnis = agent.turn(con, "alles für Lasagne")
    gleich(ergebnis.weg, "llm", "picknick.path")
    gleich(ergebnis.abruf, "fehler", "picknick.dish_fetch")
    wahr(ergebnis.n_produkte == 1, "Der Zug ist nicht zu Ende gelaufen.")
    wahr("Chefkoch" in ergebnis.meldung,
         "Die Meldung verschweigt, dass die Zutaten geraten sind.")
    return ("erster Satz: Rezept statt Raten; "
            "Ausfall: Modellweg mit ehrlicher Meldung")


# --------------------------------------------------------------------------
# 7. Oberbegriffe auffächern (WB-368)
#
# Ein eigener Katalog: ein Aufschnittregal mit vier Sorten und einer fünften,
# die ausgemustert ist. Genau daran hängt die Zusage des Tickets — angeboten
# wird nur, was wirklich im Katalog steht.

SORTEN_KATALOG = [
    # (external_id, name, l1, l2, aktiv)
    ("sal1", "Levoni Salami Milano", "Aufschnitt", "Salami", 1),
    ("sal2", "Simonini Salami Napoli", "Aufschnitt", "Salami", 1),
    ("sal3", "Ferdi Fuchs Mini Salami", "Aufschnitt", "Salami", 1),
    ("koc1", "Gutfried Kochschinken", "Aufschnitt", "Kochschinken", 1),
    ("koc2", "Rügenwalder Kochschinken zart", "Aufschnitt", "Kochschinken", 1),
    ("bru1", "Jagdwurst Aufschnitt", "Aufschnitt", "Brühwurst", 1),
    ("gef1", "Hähnchenbrust Aufschnitt", "Aufschnitt", "Geflügelwurst", 1),
    ("sue1", "Sülze mit Gurken", "Aufschnitt", "Sülze & Wurst in Aspik", 0),
    ("tom1", "Mutti Tomatenmark", "Konserven & Eingelegtes", "Tomaten", 1),
]


def sorten_katalog_anlegen(pfad: Path) -> None:
    con = db.connect(pfad)
    try:
        db.migrate(con)
        for external_id, name, l1, l2, aktiv in SORTEN_KATALOG:
            con.execute(
                "INSERT INTO product (source, external_id, name, brand,"
                " price_cents, unit_text, category_l1, category_l2, active)"
                " VALUES ('knuspr', ?, ?, 'Testmarke', 249, '100 g', ?, ?, ?)",
                (external_id, name, l1, l2, aktiv))
        con.commit()
    finally:
        con.close()


class _NieGefragt:
    """Ein Modellzugang, der jeden Aufruf zum Fehler macht."""

    def modell(self, **_):
        raise AssertionError("Das Modell wurde nach dem Kürzel gefragt.")

    def chat(self, *_, **__):
        raise AssertionError("Das Modell wurde gefragt, obwohl der Katalog "
                             "schon antwortet.")


def checks_sorten(b: Bericht, db_datei: Path) -> None:
    b.abschnitt("Oberbegriffe auffächern — erst die Sorte, dann das Produkt "
                "(WB-368)")

    con = db.connect(db_datei)
    try:
        b.pruefe("„Aufschnitt“ fächert auf: die Sorten des Katalogs mit "
                 "echter Stückzahl, ohne einen Modellaufruf",
                 lambda: _faechert_auf(con))
        b.pruefe("eine ausgemusterte Sorte wird nicht angeboten",
                 lambda: _keine_leere_sorte(con))
        b.pruefe("„Tomatenmark“ fächert nicht auf — zwei Stufen wie immer",
                 lambda: _keine_ware_faechert(con))
        b.pruefe("das Modell kann keine Kategorie erfinden",
                 lambda: _keine_erfundene_kategorie(con))
        b.pruefe("eine gewählte Sorte führt in den normalen Kandidatenablauf",
                 lambda: _sorte_fuehrt_weiter(con))
        b.pruefe("mehrere Sorten lassen sich wählen, und die nicht "
                 "angebotene fällt weg", lambda: _mehrere_sorten(con))
    finally:
        con.close()


def _faechert_auf(con) -> str:
    agent = chatmodul.Chat(_NieGefragt(), wecker=_Box())
    ergebnis = agent.turn(con, "Aufschnitt")
    gleich(ergebnis.weg, chatmodul.WEG_FAECHER, "path")
    gleich([(s["name"], s["anzahl"]) for s in ergebnis.sorten],
           [("Salami", 3), ("Kochschinken", 2), ("Brühwurst", 1),
            ("Geflügelwurst", 1)], "sorten")
    wahr(not ergebnis.vorschlaege,
         "Es wurden Produkte vorgeschlagen, statt nach der Sorte zu fragen.")
    return "4 Sorten, 7 Produkte, 0 Modellaufrufe"


def _keine_leere_sorte(con) -> str:
    sorten = [s["name"] for s in categories.sorten(con, "Aufschnitt")]
    wahr("Sülze & Wurst in Aspik" not in sorten,
         "Eine Sorte ohne aktive Produkte wurde angeboten.")
    return "die ausgemusterte Sülze steht nicht in der Auswahl"


def _keine_ware_faechert(con) -> str:
    zugang = _mock_zugang(
        json.dumps({"gericht": None, "kategorie": None,
                    "begriffe": [{"suchbegriffe": ["Tomatenmark"],
                                  "menge": 1}]}),
        _choose(("Tomatenmark", pid(con, "Tomatenmark"), 1)))
    ergebnis = chatmodul.Chat(zugang, wecker=_Box()).turn(con, "Tomatenmark")
    gleich(ergebnis.weg, chatmodul.WEG_LLM, "path")
    wahr(ergebnis.kategorie is None, "Eine Ware wurde als Warengruppe gelesen.")
    gleich([v["name"] for v in ergebnis.vorschlaege], ["Mutti Tomatenmark"],
           "vorschlaege")
    return "unverändertes Verhalten, ein Vorschlag"


def _keine_erfundene_kategorie(con) -> str:
    """Dieselbe Zusicherung wie bei den Produkt-IDs — eine Ebene höher."""
    zugang = _mock_zugang(
        json.dumps({"gericht": None, "kategorie": "Wurstabteilung",
                    "begriffe": [{"suchbegriffe": ["Aufschnitt"],
                                  "menge": 1}]}),
        _choose(("Aufschnitt", pid(con, "Jagdwurst"), 1)))
    ergebnis = chatmodul.Chat(zugang, wecker=_Box()).turn(con, "Aufschnitt "
                                                              "bitte")
    wahr(ergebnis.kategorie is None,
         "Eine erfundene Kategorie hat aufgefächert.")
    gleich(ergebnis.sorten_verworfen, "Wurstabteilung", "fanout_rejected")
    return "„Wurstabteilung“ verworfen und benannt, der Begriff bleibt"


def _sorte_fuehrt_weiter(con) -> str:
    ergebnis = chatmodul.Chat(_NieGefragt(), wecker=_Box()).turn(
        con, "Aufschnitt")
    gewaehlt = oberbegriffe.gewaehlte(con, ergebnis.chat_message_id,
                                      ["Salami"])
    zugang = _mock_zugang(_choose(("Salami", pid(con, "Levoni Salami"), 1)))
    zweit = chatmodul.Chat(zugang, wecker=_Box()).turn(
        con, "Salami", auffaechern=False, aus_sorten=("Aufschnitt", gewaehlt))
    zeile = zweit.vorschlaege[0]
    gleich(zeile["name"], "Levoni Salami Milano", "vorschlag")
    gleich(zeile["search_term"], "Salami", "search_term")
    # Die übrigen Salami sind aufgehoben — ein „Nein“ zeigt sie (WB-359).
    gleich(zeile["n_alternativen"], 2, "alternativen")
    gleich(zweit.kategorie, "Aufschnitt", "fanout_category")
    return "ein Vorschlag, zwei Alternativen, dieselbe Ja/Nein-Zeile"


def _mehrere_sorten(con) -> str:
    ergebnis = chatmodul.Chat(_NieGefragt(), wecker=_Box()).turn(
        con, "Aufschnitt")
    gewaehlt = oberbegriffe.gewaehlte(
        con, ergebnis.chat_message_id,
        ["Salami", "Kochschinken", "Sülze & Wurst in Aspik"])
    gleich(gewaehlt, ["Salami", "Kochschinken"], "gewaehlt")
    zugang = _mock_zugang(_choose(("Salami", pid(con, "Levoni Salami"), 1),
                                  ("Kochschinken", pid(con, "Gutfried"), 2)))
    zweit = chatmodul.Chat(zugang, wecker=_Box()).turn(
        con, "Salami, Kochschinken", auffaechern=False,
        aus_sorten=("Aufschnitt", gewaehlt))
    gleich([v["name"] for v in zweit.vorschlaege],
           ["Levoni Salami Milano", "Gutfried Kochschinken"], "vorschlaege")
    gleich([v["qty"] for v in zweit.vorschlaege], [1, 2], "mengen")
    return "zwei Sorten, zwei Vorschläge; die ausgemusterte fiel weg"


# --------------------------------------------------------------------------
# 8. Die Bindung

def checks_zuruecknehmen(b: Bericht, db_datei: Path, bild_dir: Path) -> None:
    """Ein Fehltipp ist nicht endgültig — und kostet den Korb trotzdem nichts.

    Am HTTP-Rand geklickt, weil genau dort der Unterschied sitzt: `entscheiden`
    konnte `offen` immer schon, es fragte nur niemand danach. Und die Falle des
    Tickets („Ja, rückgängig, Ja") ist erst über die Oberfläche eine.
    """
    b.abschnitt("Eine Entscheidung ist ein Tipp und kein Urteil (WB-361)")

    con = db.connect(db_datei)
    try:
        butter = pid(con, "Salzbutter")
    finally:
        con.close()
    agent = chatmodul.Chat(
        _mock_zugang(_extract(("Butter", 1)), _choose(("Butter", butter, 1))),
        wecker=_Box())
    app = webapp.create_app(db_path=db_datei, image_dir=bild_dir, chat=agent)
    with TestClient(app) as client:
        client.post("/warenkorb/chat", data={"satz": "Butter"},
                    headers={"HX-Request": "true"})
        con = db.connect(db_datei)
        try:
            sid = int(con.execute("SELECT id FROM chat_suggestion ORDER BY id"
                                  ).fetchone()["id"])
            b.pruefe("„Ja“ lässt sich zurücknehmen — die Zeile ist wieder "
                     "unentschieden, der Korb bleibt stehen",
                     lambda: _ja_zuruecknehmen(client, con, sid))
            b.pruefe("„Ja“ / rückgängig / „Ja“ legt NUR EINMAL ein "
                     "(der Schutz hängt nicht an der letzten Entscheidung)",
                     lambda: _kein_doppeltes_einlegen(client, con, sid))
            b.pruefe("ein zurückgenommener Fehltipp hinterlässt kein "
                     "Eval-Label", lambda: _kein_label(client, con, sid))
        finally:
            con.close()


def _entscheiden(client, sid: int, decision: str):
    antwort = client.post(
        f"/warenkorb/vorschlag/{sid}/entscheiden?decision={decision}",
        headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, f"POST entscheiden?decision={decision}")
    return antwort.text


def _korb(con) -> list[tuple]:
    return [(z["product_id"], z["qty"]) for z in orders.inhalt(con)]


def _ja_zuruecknehmen(client, con, sid: int) -> str:
    stueck = _entscheiden(client, sid, "kept")
    wahr("rückgängig" in stueck, "Die entschiedene Zeile bietet keinen Rückweg.")
    vorher = _korb(con)
    gleich(len(vorher), 1, "Korbzeilen nach dem „Ja“")

    stueck = _entscheiden(client, sid, "offen")
    gleich(vorschlaege.eine(con, sid)["decision"], "offen", "decision")
    gleich(_korb(con), vorher, "Korb nach der Rücknahme")
    # Der Korb wird bewusst nicht angerührt (`orders.einlegen()` fasst
    # zusammen) — dann muss die Oberfläche es sagen.
    wahr("die Zeile bleibt im Korb" in stueck,
         "Die Rücknahme verschweigt, dass die Korbzeile stehen bleibt.")
    wahr("decision=kept" in stueck and "decision=removed" in stueck,
         "Nach der Rücknahme fehlen die Knöpfe — die Zeile wäre eine Sackgasse.")
    return "offen, Korb unverändert, beide Knöpfe wieder da"


def _kein_doppeltes_einlegen(client, con, sid: int) -> str:
    """Die Falle des Tickets: der alte Schutz verglich `decision`."""
    for _ in range(3):
        _entscheiden(client, sid, "kept")
        _entscheiden(client, sid, "offen")
    _entscheiden(client, sid, "kept")
    korb = _korb(con)
    gleich(korb, [(vorschlaege.eine(con, sid)["product_id"], 1)], "Korb")
    return "4 × „Ja“ über 3 Rücknahmen hinweg — eine Zeile, Menge 1"


def _kein_label(client, con, sid: int) -> str:
    """Vor dem Abschicken ist nichts geschrieben (WB-329) — und `offen`
    bekommt auch dann nichts. Die Rücknahme steht als Zähler da, nicht als
    Urteil."""
    _entscheiden(client, sid, "offen")
    order_id = orders.warenkorb_id(con)
    annos = labels.annotationen(con, order_id)
    gleich([a for a in annos
            if a["name"] == labels.NAME_ENTSCHEIDUNG], [], "Einzellabels")
    n = vorschlaege.eine(con, sid)["zurueckgenommen"]
    wahr(n >= 4, f"Die Rücknahmen wurden nicht gezählt ({n}).")
    return f"kein Label, aber withdrawn={n}"


# --------------------------------------------------------------------------
# Portionen und die Reihenfolge der Rechenschritte (WB-362)

def checks_portionen(b: Bericht, db_datei: Path, bild_dir: Path) -> None:
    """Skalieren, zusammenzählen, DANN aufrunden — am HTTP-Rand geklickt.

    Über die Oberfläche und nicht über `mengen.rechne()`: die Reihenfolge der
    Rechenschritte ist keine Eigenschaft einer Hilfsfunktion, sondern eine des
    Weges. Ein Gate, das die Hilfsfunktion prüft, bliebe grün, während der
    Shop je Rezept aufrundet und zwei Packungen Knoblauch in den Korb legt.
    """
    b.abschnitt("Portionen — skalieren, zusammenzählen, dann aufrunden "
                "(WB-362)")

    app = webapp.create_app(db_path=db_datei, image_dir=bild_dir)
    with TestClient(app) as client:
        con = db.connect(db_datei)
        try:
            butter = pid(con, "Salzbutter")      # 250 g
            zwiebel = pid(con, "Zwiebeln")       # 1 kg
            b.pruefe("ein Rezept für 4 auf 8 Portionen: 250 g werden 500 g "
                     "und damit 2 × 250 g",
                     lambda: _verdoppelt(client, con, butter))
            b.pruefe("zwei Rezepte à 100 g ergeben EINE Packung à 250 g — "
                     "aufgerundet wird nach dem Zusammenzählen",
                     lambda: _erst_zaehlen_dann_runden(client, con, butter))
            b.pruefe("1 Zwiebel aus dem 1-kg-Netz bleibt 1 Netz, auch für 8",
                     lambda: _zwiebeltest(client, con, zwiebel))
        finally:
            con.close()


def _rezept(client, name: str, servings: int, product_id: int,
            amount, unit) -> int:
    """Ein Rezept mit einer Zutat, angelegt über die Oberfläche."""
    antwort = client.post("/rezepte", data={"name": name},
                          follow_redirects=False)
    gleich(antwort.status_code, 303, "POST /rezepte")
    rid = int(antwort.headers["location"].rsplit("/", 1)[1])
    client.post(f"/rezepte/{rid}/bearbeiten",
                data={"name": name, "servings": str(servings)},
                follow_redirects=False)
    client.post(f"/rezepte/{rid}/zutaten?product_id={product_id}"
                f"&amount={amount}&unit={unit}",
                headers={"HX-Request": "true"})
    return rid


def _leeren(con) -> None:
    con.execute("DELETE FROM order_item")
    con.commit()


def _in_den_korb(client, rid: int, portionen) -> str:
    antwort = client.post(f"/rezepte/{rid}/korb",
                          data={"portionen": str(portionen)},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, f"POST /rezepte/{rid}/korb")
    return antwort.text


def _menge(con, product_id: int) -> tuple:
    zeile = next(z for z in orders.inhalt(con)
                 if z["product_id"] == product_id)
    return (zeile["qty"], zeile["need_amount"], zeile["need_unit"])


def _verdoppelt(client, con, butter: int) -> str:
    _leeren(con)
    rid = _rezept(client, "Butterkuchen", 4, butter, 250, "g")
    text = _in_den_korb(client, rid, 8)
    wahr("für 8 statt 4 Portionen" in text,
         "Die Meldung sagt nicht, wofür gerechnet wurde.")
    gleich(_menge(con, butter), (2, 500.0, "g"), "Korbzeile")
    return "500 g gebraucht, 2 × 250 g im Korb"


def _erst_zaehlen_dann_runden(client, con, butter: int) -> str:
    """Der Nachtrag des Tickets: 100 g + 100 g sind 200 g und EINE Packung.

    Wer je Rezept aufrundet, kommt hier auf zwei — und kauft 250 g Butter,
    die niemand braucht.
    """
    _leeren(con)
    for name in ("Aioli", "Pesto"):
        rid = _rezept(client, name, 4, butter, 100, "g")
        _in_den_korb(client, rid, 4)
    gleich(_menge(con, butter), (1, 200.0, "g"), "Korbzeile")
    gleich(len(orders.inhalt(con)), 1, "Korbzeilen")
    return "2 × 100 g -> 200 g -> 1 Packung à 250 g"


def _zwiebeltest(client, con, zwiebel: int) -> str:
    """Skaliert wird trotzdem — die Packungszahl bleibt nur bei 1.

    Geprüft wird BEIDES. Ein Test, der nur die 1 sieht, bliebe auch dann grün,
    wenn gar nichts skaliert würde, und bewiese nichts.
    """
    _leeren(con)
    rid = _rezept(client, "Zwiebelsuppe", 4, zwiebel, 1, "Stk")
    text = _in_den_korb(client, rid, 8)
    gleich(_menge(con, zwiebel), (1, 2.0, "Stk"), "Korbzeile")
    wahr("Nicht ausrechenbar" in text and "1 kg" in text,
         "Die Oberfläche sagt nicht, warum die Menge unverändert blieb.")
    return "2 Stk gebraucht, gegen „1 kg“ nicht ausrechenbar -> 1 Netz"


# --------------------------------------------------------------------------
# Der Chat-Weg trägt Mengen (WB-369)
#
# WB-362 hat das Rechnen gebaut, und es griff nur am gespeicherten Rezept.
# Der produktive Weg ist Chat -> Chefkoch -> `chat_suggestion` -> Korb; der
# Abschnitt hier klickt genau ihn, am HTTP-Rand und mit zwei Gerichten, die
# sich eine Zutat teilen.

#: Zwei erfundene Chefkoch-Rezepte mit gemeinsamem Hackfleisch. Die
#: aufgezeichnete Pho-Fixture taugt dafür nicht: sie ist EIN Rezept, und
#: zwei Rezepte mit derselben Zutat sind genau der Fall des Tickets.
MENGEN_REZEPTE = {
    "111": {"id": "111", "title": "Bolognese", "servings": 4,
            "siteUrl": "https://www.chefkoch.de/rezepte/111/",
            "instructions": "Alles kochen.",
            "ingredientGroups": [{"header": None, "ingredients": [
                {"name": "Hackfleisch, gemischtes", "amount": 200.0,
                 "unit": "g"},
                {"name": "Zwiebel(n)", "amount": 2.0, "unit": None}]}]},
    "222": {"id": "222", "title": "Chili con Carne", "servings": 4,
            "siteUrl": "https://www.chefkoch.de/rezepte/222/",
            "instructions": "Alles kochen.",
            "ingredientGroups": [{"header": None, "ingredients": [
                {"name": "Hackfleisch, gemischtes", "amount": 300.0,
                 "unit": "g"}]}]},
}


class _ChefkochZwei:
    """Die beiden Rezepte oben, über dieselben URLs wie die echte API."""

    def get(self, url):
        if "?query=" in url:
            rid = "111" if "olognese" in url else "222"
            r = MENGEN_REZEPTE[rid]
            return _JSON({"count": 1, "results": [
                {"recipe": {"id": rid, "title": r["title"],
                            "rating": {"rating": 4.5, "numVotes": 100},
                            "siteUrl": r["siteUrl"]}}]})
        return _JSON(MENGEN_REZEPTE[url.rsplit("/", 1)[-1]])


def checks_mengen_im_chat(b: Bericht, db_datei: Path, bild_dir: Path) -> None:
    """Zwei Chat-Züge, zwei Rezepte, eine Packung — über „Ja", nicht über
    `recipes.in_den_korb`."""
    b.abschnitt("Der Chat-Weg trägt Mengen (WB-369)")

    con = db.connect(db_datei)
    try:
        for gericht in ("Bolognese", "Chili con Carne"):
            gerichtelauf.hole_eines(con, _ChefkochZwei(), gericht, pause_s=0,
                                    schreib=lambda _: None)
        hack = pid(con, "Hackfleisch gemischt")
    finally:
        con.close()

    # Je Zug zwei Modellantworten: Stufe 1b übersetzt die Zutatenliste,
    # Stufe 3 wählt. Stufe 1 entfällt, weil der Gerichtsname im Satz steht.
    zugang = _mock_zugang(
        _extract((("gemischtes Hackfleisch", "Hackfleisch"), 2)),
        _choose(("gemischtes Hackfleisch", hack, 2)),
        _extract((("gemischtes Hackfleisch", "Hackfleisch"), 2)),
        _choose(("gemischtes Hackfleisch", hack, 2)))
    agent = chatmodul.Chat(zugang, wecker=_Box(),
                           quelle=gerichte.Quelle(holer=gerichte.nicht_holen))
    app = webapp.create_app(db_path=db_datei, image_dir=bild_dir, chat=agent)
    with TestClient(app) as client:
        con = db.connect(db_datei)
        try:
            b.pruefe("ein Chat-Zug legt die Menge des Rezepts in den Korb, "
                     "nicht die geratene Packungszahl",
                     lambda: _chat_menge(client, con, hack,
                                         "alles für Bolognese", 200.0))
            b.pruefe("zwei Rezepte über den Chat teilen sich EINE Packung — "
                     "200 g + 300 g gegen „500 g“",
                     lambda: _chat_menge(client, con, hack,
                                         "alles für Chili con Carne", 500.0))
        finally:
            con.close()


def _chat_menge(client, con, product_id: int, satz: str, erwartet) -> str:
    antwort = client.post("/warenkorb/chat", data={"satz": satz},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "POST /warenkorb/chat")
    sid = int(con.execute(
        "SELECT id FROM chat_suggestion WHERE product_id = ?"
        " ORDER BY id DESC LIMIT 1", (product_id,)).fetchone()["id"])
    stueck = _entscheiden(client, sid, "kept")
    wahr("gebraucht" in stueck,
         "Die Zeile sagt nicht, welche Menge gebraucht wird.")
    zeile = next(z for z in orders.inhalt(con)
                 if z["product_id"] == product_id)
    gleich((zeile["need_amount"], zeile["need_unit"]), (erwartet, "g"),
           "Bedarf im Korb")
    gleich(zeile["qty"], 1, "Packungen")
    return f"{erwartet:.0f} g gebraucht, {zeile['qty']} × 500 g im Korb"


# --------------------------------------------------------------------------
# Der Rezeptentwurf aus einem Chat-Zug (WB-337)
#
# Der Satz des Tickets, am HTTP-Rand: „alles für Spaghetti Bolognese, und
# Klopapier". Am Ende muss das Klopapier im Einkauf liegen und im Rezept
# fehlen — und derselbe Satz danach ohne einen einzigen Modellaufruf
# auskommen.

#: Ein erfundenes Chefkoch-Rezept, dessen Zutaten es im Katalog oben gibt.
BOLO = {
    "id": "42", "title": "Spaghetti Bolognese al Forno", "servings": 4,
    "siteUrl": "https://www.chefkoch.de/rezepte/42/",
    "instructions": "Alles kochen.",
    "ingredientGroups": [{"header": None, "ingredients": [
        {"name": "Hackfleisch, gemischtes", "amount": 500.0, "unit": "g"},
        {"name": "Tomaten, passierte", "amount": 500.0, "unit": "ml"},
        {"name": "Spaghetti", "amount": 400.0, "unit": "g"}]}]}


class _ChefkochBolo:
    """Das Rezept oben, über dieselben URLs wie die echte API."""

    def get(self, url):
        if "?query=" in url:
            return _JSON({"count": 1, "results": [
                {"recipe": {"id": BOLO["id"], "title": BOLO["title"],
                            "rating": {"rating": 4.7, "numVotes": 900},
                            "siteUrl": BOLO["siteUrl"]}}]})
        return _JSON(BOLO)


class _Wirft:
    """Ein Modellzugang, der bei jedem Aufruf auffliegt.

    Der Beleg für „der Rezeptweg kostet kein Modell": ein Zug, der doch
    fragt, macht den Check rot statt langsam.
    """

    def modell(self, **_):
        raise AssertionError("Das Modell wurde nach dem Kürzel gefragt.")

    def chat(self, *_, **__):
        raise AssertionError("Das Modell wurde gefragt — der Rezeptweg nicht.")


SATZ_337 = "alles für Spaghetti Bolognese, und Klopapier"


def checks_rezeptentwurf(b: Bericht, db_datei: Path, bild_dir: Path) -> None:
    b.abschnitt("Aus einem Chat-Zug wird ein Rezept — ohne das Klopapier "
                "(WB-337)")

    con = db.connect(db_datei)
    try:
        gerichtelauf.hole_eines(con, _ChefkochBolo(), "Spaghetti Bolognese",
                                pause_s=0, schreib=lambda _: None)
        hack = pid(con, "Hackfleisch gemischt")
        toma = pid(con, "Passierte Tomaten")
        spag = pid(con, "Spaghetti No. 5")
        klo = pid(con, "Toilettenpapier")
    finally:
        con.close()

    zugang = _mock_zugang(
        _extract((("gemischtes Hackfleisch", "Hackfleisch"), 1),
                 (("passierte Tomaten", "Tomaten"), 1), (("Spaghetti",), 1),
                 (("Klopapier", "Toilettenpapier"), 1)),
        _choose(("gemischtes Hackfleisch", hack, 1),
                ("passierte Tomaten", toma, 1), ("Spaghetti", spag, 1),
                ("Klopapier", klo, 1)))
    agent = chatmodul.Chat(zugang, wecker=_Box(),
                           quelle=gerichte.Quelle(holer=gerichte.nicht_holen))
    app = webapp.create_app(db_path=db_datei, image_dir=bild_dir, chat=agent)
    with TestClient(app) as client:
        con = db.connect(db_datei)
        try:
            b.pruefe("der Zug trennt Gerichtszutaten vom Klopapier — ohne ein "
                     "Feld im Prompt",
                     lambda: _entwurf_entsteht(client, con, klo))
            b.pruefe("vor dem Abschicken steht kein Rezept in der Sammlung",
                     lambda: _noch_kein_rezept(con))
            b.pruefe("der Name ist überschreibbar und die Zeilen sind es auch",
                     lambda: _entwurf_bearbeiten(client, con, spag))
            b.pruefe("beim Abschicken entsteht das Rezept — mit den "
                     "behaltenen Zutaten und ohne das Klopapier",
                     lambda: _abschicken_legt_an(client, con, klo))
        finally:
            con.close()

    # Ein ZWEITER Shop mit einem Modell, das wirft: derselbe Satz nimmt jetzt
    # den Rezeptweg. Genau dafür wurde das Rezept angelegt.
    agent2 = chatmodul.Chat(_Wirft(), wecker=_Box(),
                            quelle=gerichte.Quelle(holer=gerichte.nicht_holen))
    app2 = webapp.create_app(db_path=db_datei, image_dir=bild_dir, chat=agent2)
    with TestClient(app2) as client:
        con = db.connect(db_datei)
        try:
            b.pruefe("derselbe Satz kostet danach keinen Modellaufruf mehr",
                     lambda: _zweiter_satz(client, con))
        finally:
            con.close()


def _entwurf_entsteht(client, con, klo: int) -> str:
    antwort = client.post("/warenkorb/chat", data={"satz": SATZ_337},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "POST /warenkorb/chat")
    wahr("Rezeptentwurf" in antwort.text, "Kein Entwurf in der Antwort.")
    mid = _letzte_antwort(con)
    e = entwuerfe.zu_nachricht(con, mid)
    wahr(e is not None, "Zu dem Zug gibt es keinen Entwurf.")
    gleich(e["name"], "Spaghetti Bolognese", "vorgeschlagener Rezeptname")
    namen = [z["name"] for z in e["zeilen"]]
    gleich(len(namen), 3, "Zutaten im Entwurf")
    wahr(all("Toilettenpapier" not in n for n in namen),
         f"Das Klopapier steht im Entwurf: {namen}")
    zeile = con.execute("SELECT dish_item FROM chat_suggestion"
                        " WHERE product_id = ?", (klo,)).fetchone()
    wahr(zeile["dish_item"] is None,
         "Das Klopapier trägt eine Zugehörigkeit zum Gericht.")
    return f"3 Gerichtszutaten, Klopapier ohne Zugehörigkeit: {namen}"


def _noch_kein_rezept(con) -> str:
    n = con.execute("SELECT count(*) AS n FROM recipe_item").fetchone()["n"]
    gleich(n, 0, "verknüpfte Produkte vor dem Abschicken")
    # Die `recipe`-Zeile selbst gibt es: sie kam mit dem Chefkoch-Abruf und
    # trägt die Zubereitung. Ohne Produkte fängt sie keinen Zug ab.
    zutaten = [r["n_zutaten"] for r in recipes.rezepte(con)]
    gleich(zutaten, [0], "Zutaten je Rezept")
    return "das geholte Rezept steht da, verknüpft ist noch nichts"


def _entwurf_bearbeiten(client, con, spag: int) -> str:
    mid = _letzte_antwort(con)
    antwort = client.post(f"/warenkorb/chat/{mid}/alle?decision=kept",
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "POST alle?decision=kept")
    antwort = client.post(f"/warenkorb/chat/{mid}/entwurf/name",
                          data={"name": "Bolo"},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "POST entwurf/name")
    sid = con.execute("SELECT id FROM chat_suggestion WHERE product_id = ?",
                      (spag,)).fetchone()["id"]
    antwort = client.post(f"/warenkorb/vorschlag/{sid}/rezeptzeile?drin=0",
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "POST rezeptzeile?drin=0")
    e = entwuerfe.zu_nachricht(con, mid)
    gleich((e["name"], e["n_drin"]), ("Bolo", 2), "Entwurf nach dem Ändern")
    # Aus dem Entwurf ist NICHT aus dem Korb: die Spaghetti liegen weiter da.
    wahr(any(z["product_id"] == spag for z in orders.inhalt(con)),
         "Die aus dem Entwurf genommene Zeile ist aus dem Korb verschwunden.")
    return "\u201eBolo\u201c, 2 Zutaten im Entwurf, 3 Zeilen im Korb"


def _abschicken_legt_an(client, con, klo: int) -> str:
    antwort = client.post("/warenkorb/abschicken",
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 204, "POST /warenkorb/abschicken")
    liste = recipes.rezepte(con)
    gleich([r["name"] for r in liste], ["Bolo"], "Rezepte in der Sammlung")
    rezept = recipes.rezept(con, liste[0]["id"])
    namen = [z["name"] for z in rezept["zutaten"]]
    gleich(len(namen), 2, "Zutaten im Rezept")
    wahr(all("Toilettenpapier" not in n for n in namen),
         f"Das Klopapier steht im Rezept: {namen}")
    # Und im Einkauf liegt es sehr wohl.
    bestellung = orders.bestellungen(con, "offen")[0]
    wahr(any(z["product_id"] == klo
             for z in orders.posten(con, bestellung["id"])),
         "Das Klopapier fehlt in der Bestellung.")
    # Die Zubereitung des geholten Rezepts ist dabeigeblieben.
    wahr(rezept["instructions"], "Das Rezept hat seine Zubereitung verloren.")
    return f"\u201eBolo\u201c mit {namen}, Klopapier in der Bestellung"


def _zweiter_satz(client, con) -> str:
    antwort = client.post("/warenkorb/chat", data={"satz": "heute Bolo"},
                          headers={"HX-Request": "true"})
    gleich(antwort.status_code, 200, "POST /warenkorb/chat (Rezeptweg)")
    mid = _letzte_antwort(con)
    zeilen = vorschlaege.liste(con, mid)
    gleich(len(zeilen), 2, "Vorschläge aus dem Rezept")
    wahr(entwuerfe.zu_nachricht(con, mid) is None,
         "Der Rezeptweg hat einen zweiten Entwurf angelegt.")
    return "2 Vorschläge aus dem Rezept, kein Modellaufruf, kein Entwurf"


def _letzte_antwort(con) -> int:
    return int(con.execute("SELECT max(id) AS id FROM chat_message"
                           " WHERE role = 'assistant'").fetchone()["id"])


def checks_bindung(b: Bericht) -> None:
    b.abschnitt("Bindung — der Prozess lauscht nicht auf 0.0.0.0")

    def abgelehnt(host: str):
        try:
            webapp.pruefe_host(host)
        except webapp.UnsichereBindung as e:
            # Nur der erste Satz: die Begründungen sind für die Nutzerin
            # geschrieben und in einer Prüfzeile zu lang.
            return str(e).split(". ")[0][:70]
        raise AssertionError(f"{host!r} wurde durchgelassen.")

    for host, was in (("0.0.0.0", "0.0.0.0 lauscht auf jeder Schnittstelle"),
                      ("::", ":: ebenso, in IPv6"),
                      ("192.168.2.14", "die LAN-Adresse des Laptops"),
                      ("picknick.local", "ein Name, den DNS irgendwohin "
                                         "auflösen kann"),
                      ("", "eine leere Adresse")):
        b.pruefe(f"abgelehnt: {was}", lambda h=host: abgelehnt(h))

    for host in ("127.0.0.1", "localhost", "::1", "100.64.0.1"):
        b.pruefe(f"erlaubt: {host}",
                 lambda h=host: gleich(webapp.pruefe_host(h), h, "host"))

    b.pruefe("die Vorgabe enthält nur erlaubte Adressen",
             lambda: gleich(webapp.hosts_aus_umgebung({}),
                            ["100.64.0.1", "127.0.0.1"], "DEFAULT_HOSTS"))

    def env_verboten():
        try:
            webapp.hosts_aus_umgebung({"PICKNICK_HOST": "127.0.0.1,0.0.0.0"})
        except webapp.UnsichereBindung:
            return "PICKNICK_HOST wird geprüft, nicht geglaubt"
        raise AssertionError("PICKNICK_HOST=…,0.0.0.0 kam durch.")

    b.pruefe("auch aus der Umgebung kommt 0.0.0.0 nicht durch", env_verboten)

    def kein_socket():
        # `sockets_bauen` prüft ALLE Adressen, bevor es das erste Socket
        # öffnet. Mit 0.0.0.0 in der Liste darf also nichts lauschen — und
        # zwar auch nicht auf der davorstehenden, erlaubten Adresse.
        try:
            webapp.sockets_bauen(["127.0.0.1", "0.0.0.0"], 0)
        except webapp.UnsichereBindung:
            return "keine halbe Liste gebunden"
        raise AssertionError("sockets_bauen hat gebunden.")

    b.pruefe("eine Liste mit 0.0.0.0 öffnet gar kein Socket", kein_socket)


# --------------------------------------------------------------------------

def main() -> int:
    b = Bericht()
    print("picknick — Rauchtest (checks/smoke.py)")
    checks_netz(b)
    with tempfile.TemporaryDirectory(prefix="picknick-smoke-") as tmp:
        ordner = Path(tmp)
        db_datei = ordner / "picknick.db"
        bild_dir = ordner / "bilder"
        bild_dir.mkdir()
        katalog_anlegen(db_datei)
        checks_app(b, db_datei, bild_dir)
        # Eigene Datei: der Span-Lauf schreibt Chatzeilen, und die haben im
        # Warenkorb des Wegs oben nichts verloren.
        span_db = ordner / "spans.db"
        katalog_anlegen(span_db)
        checks_spans(b, span_db)
        # Wieder eine eigene Datei: der Gerichte-Lauf legt ein
        # Rezept an, und das hat im Katalog der Wege oben
        # nichts verloren.
        gerichte_db = ordner / "gerichte.db"
        katalog_anlegen(gerichte_db)
        checks_gerichte(b, gerichte_db)
        # Und noch eine: die Auffächerung braucht eine Oberkategorie mit
        # mehreren Sorten, und die Butter-Fall-Katalog hat keine.
        sorten_db = ordner / "sorten.db"
        sorten_katalog_anlegen(sorten_db)
        checks_sorten(b, sorten_db)
        # Und noch eine: der Rückweg klickt sich durch denselben Warenkorb
        # wie oben und hätte dort die abgeschickte Bestellung im Rücken.
        zurueck_db = ordner / "zuruecknehmen.db"
        katalog_anlegen(zurueck_db)
        checks_zuruecknehmen(b, zurueck_db, bild_dir)
        # Und noch eine: die Portionsrechnung legt Rezepte an und füllt den
        # Korb mehrfach — beides hätte in den Warenkörben oben nichts
        # verloren.
        portionen_db = ordner / "portionen.db"
        katalog_anlegen(portionen_db)
        checks_portionen(b, portionen_db, bild_dir)
        # Und noch eine: der Chat-Weg holt zwei Gerichte und füllt den Korb
        # über „Ja" — beides hat in den Warenkörben oben nichts verloren.
        mengen_db = ordner / "mengen_im_chat.db"
        katalog_anlegen(mengen_db)
        checks_mengen_im_chat(b, mengen_db, bild_dir)
        # Und noch eine: der Rezeptentwurf legt ein Rezept an und schickt den
        # Korb ab — beides hätte in den Warenkörben oben nichts verloren.
        entwurf_db = ordner / "entwurf.db"
        katalog_anlegen(entwurf_db)
        checks_rezeptentwurf(b, entwurf_db, bild_dir)
    checks_bindung(b)
    return b.ende()


if __name__ == "__main__":
    sys.exit(main())
