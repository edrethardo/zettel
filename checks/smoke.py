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

from picknick import db, obs, orders  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.catalog import categories, search  # noqa: E402
from picknick.llm import wake  # noqa: E402
from picknick.llm.client import Modellzugang  # noqa: E402
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
                "die Suche gibt einen Rang zurück, grösser ist besser",
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
    wahr(treffer, "Keine Treffer für „butter“.")
    raenge = [t["rang"] for t in treffer]
    wahr(all(r > 0 for r in raenge), f"Nicht alle Ränge positiv: {raenge}")
    wahr(raenge == sorted(raenge, reverse=True),
         f"Ränge nicht absteigend: {raenge}")
    return (f"{len(treffer)} Treffer, Ränge absteigend "
            f"{raenge[0]:.3g} … {raenge[-1]:.3g}")


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
    wahr(scores == sorted(scores, reverse=True),
         f"Dokumente nicht absteigend sortiert: {scores}")
    gleich(a["picknick.rank_top"], scores[0], "rank_top")
    return f"{n} Dokumente, bester Score {scores[0]:.4g}"


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
# 6. Die Bindung

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
    checks_bindung(b)
    return b.ende()


if __name__ == "__main__":
    sys.exit(main())
