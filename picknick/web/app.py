"""Web-Grundgerüst und Katalogansicht (WB-323).

Ein FastAPI-Prozess, server-gerendertes Jinja2, HTMX für Suche und
Kategoriewechsel (Spec 3). Der Shop liest ausschliesslich aus der Datenbank —
in diesem Modul steht bewusst kein einziger ausgehender HTTP-Aufruf. Auch das
ausgelieferte HTML verweist auf keine fremde Domain: HTMX liegt als Datei unter
`static/`, weil das Tailnet nicht zwingend online ist (Spec 15).

Die Bindung (Abschnitt „Bindung" weiter unten) ist die einzige
Sicherheitsgrenze des Projekts. Sie wird deshalb hier aktiv durchgesetzt und
nicht bloss in der Dokumentation empfohlen.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from picknick import db
from picknick.catalog import categories, search

HIER = Path(__file__).parent
TEMPLATE_DIR = HIER / "templates"
STATIC_DIR = HIER / "static"

#: Wo die Bilder liegen, die der Crawler heruntergeladen hat (Spec 5.2).
DEFAULT_IMAGE_DIR = "data/images"

#: Wie viele Kacheln eine Liste höchstens zeigt. Auf dem Handy scrollt niemand
#: durch 900 Produkte; wer mehr will, sucht oder steigt eine Ebene tiefer.
SEITE = 60

#: Ab wann das Hinweisband erscheint (Spec 11).
HINWEIS_AB_TAGEN = 3

#: Rollen-Cookie. Siehe `waehle_rolle()`: das ist KEINE Authentifizierung.
COOKIE_ROLLE = "picknick_rolle"
ROLLEN = ("sie", "er")
COOKIE_TAGE = 365


# --------------------------------------------------------------------------
# Bindung — die einzige Sicherheitsgrenze
#
# Kein Passwort, der Rahmen ist das Tailnet (Spec 10). Ein Prozess, der auf
# 0.0.0.0 lauscht, steht im nächsten fremden WLAN offen — deshalb wird die
# Adresse hier geprüft, bevor überhaupt ein Socket entsteht, und ein
# unerlaubter Wunsch mit einem Fehler beantwortet statt stillschweigend
# korrigiert. Wer die Vorgabe überschreibt, soll es merken.

ENV_HOST = "PICKNICK_HOST"
ENV_PORT = "PICKNICK_PORT"
ENV_DB = "PICKNICK_DB"
ENV_IMAGE_DIR = "PICKNICK_IMAGE_DIR"

#: Vorgabe: die Tailscale-Adresse dieser Maschine und localhost. Beides steht
#: in der Umgebung und nicht als einzige Wahrheit im Code, weil die Adresse
#: beim Umzug auf einen Dauerläufer (Spec 12) eine andere ist.
DEFAULT_HOSTS = "100.64.0.1,127.0.0.1"
DEFAULT_PORT = 8730

#: Der CGNAT-Bereich, aus dem Tailscale seine Adressen vergibt.
TAILNET = ipaddress.ip_network("100.64.0.0/10")


class UnsichereBindung(RuntimeError):
    """Der Prozess sollte auf eine Adresse gebunden werden, die er nicht darf."""


def pruefe_host(host: str) -> str:
    """Gibt `host` zurück, wenn darauf gebunden werden darf, sonst Fehler.

    Erlaubt sind genau zwei Sorten: loopback (`localhost`, `127.0.0.1`, `::1`)
    und eine Adresse aus dem Tailnet. Alles andere — `0.0.0.0`, `::`, die
    LAN-Adresse des Laptops, ein Hostname, den irgendein DNS irgendwohin
    auflöst — wird abgelehnt. Eine Weissliste, weil eine Schwarzliste nur die
    Schreibweisen abfängt, an die jemand gedacht hat.
    """
    name = (host or "").strip()
    if not name:
        raise UnsichereBindung(
            "Leere Bindeadresse. Erlaubt sind localhost und die "
            "Tailscale-Adresse, siehe Spec Abschnitt 10.")
    if name == "localhost":
        return name
    try:
        adresse = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        raise UnsichereBindung(
            f"{name!r} ist keine IP-Adresse. Erlaubt sind 'localhost' und eine "
            "Tailscale-Adresse aus 100.64.0.0/10 — ein Name kann sich hinter "
            "unserem Rücken auf etwas Öffentliches auflösen.") from None
    if adresse.is_unspecified:
        # 0.0.0.0 und :: — der Fall, um den es in Spec 10 überhaupt geht.
        raise UnsichereBindung(
            f"Bindung auf {name} verweigert: das lauscht auf JEDER Schnittstelle "
            "und stellt den Shop im nächsten fremden WLAN offen ins Netz "
            "(Spec 10). Erlaubt sind localhost und die Tailscale-Adresse.")
    if adresse.is_loopback:
        return name
    if adresse.version == 4 and adresse in TAILNET:
        return name
    raise UnsichereBindung(
        f"Bindung auf {name} verweigert: weder loopback noch eine Adresse aus "
        f"dem Tailnet ({TAILNET}). Es gibt kein Passwort — ausserhalb des "
        "Tailnets gibt es damit auch keinen Schutz (Spec 10).")


def hosts_aus_umgebung(umgebung=None) -> list[str]:
    """Die Bindeadressen aus `PICKNICK_HOST`, komma-getrennt. Alle geprüft."""
    umgebung = os.environ if umgebung is None else umgebung
    roh = umgebung.get(ENV_HOST)
    if roh is None:
        roh = DEFAULT_HOSTS
    hosts = [h.strip() for h in roh.split(",") if h.strip()]
    if not hosts:
        # Gesetzt, aber leer: das ist ein Konfigurationsfehler und wird nicht
        # stillschweigend zur Vorgabe geglättet — sonst lauscht der Prozess
        # woanders, als in der Konfiguration steht.
        raise UnsichereBindung(f"{ENV_HOST} ist gesetzt, enthält aber keine Adresse.")
    return [pruefe_host(h) for h in hosts]


def port_aus_umgebung(umgebung=None) -> int:
    umgebung = os.environ if umgebung is None else umgebung
    return int(umgebung.get(ENV_PORT) or DEFAULT_PORT)


def sockets_bauen(hosts, port: int) -> list[socket.socket]:
    """Ein lauschendes Socket je erlaubter Adresse.

    Erst werden ALLE Adressen geprüft, dann wird das erste Socket geöffnet —
    sonst lauscht der Prozess schon auf der halben Liste, wenn die zweite
    Adresse abgelehnt wird.
    """
    geprueft = [pruefe_host(h) for h in hosts]
    offen: list[socket.socket] = []
    try:
        for host in geprueft:
            info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
            s = socket.socket(info[0], info[1])
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(info[4])
            s.listen(128)
            offen.append(s)
    except OSError:
        for s in offen:
            s.close()
        raise
    return offen


def serve(hosts=None, port: int | None = None, app: FastAPI | None = None) -> None:
    """Startet den Web-Prozess auf den erlaubten Adressen.

    Mehrere Sockets statt eines: uvicorn kann pro Aufruf nur eine Adresse
    binden, und „localhost und Tailnet" sind zwei. Der naheliegende Ausweg
    wäre `0.0.0.0` — genau der ist verboten.
    """
    import uvicorn

    hosts = hosts_aus_umgebung() if hosts is None else [pruefe_host(h) for h in hosts]
    port = port_aus_umgebung() if port is None else port
    app = app if app is not None else create_app()
    try:
        sockets = sockets_bauen(hosts, port)
    except OSError as e:
        raise RuntimeError(
            f"Konnte nicht auf {hosts}:{port} binden ({e}). Läuft tailscaled "
            "und stimmt die Adresse in PICKNICK_HOST noch?") from e
    # ws="none": der Shop spricht kein WebSocket, HTMX braucht keines — und
    # uvicorns Auto-Erkennung importiert sonst das systemweite `websockets`,
    # das auf dieser Maschine zu alt ist und den Start mit einem ImportError
    # abbricht (gemessen 2026-08-28).
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", ws="none"))
    server.run(sockets=sockets)


# --------------------------------------------------------------------------
# Darstellung

def euro(cents) -> str:
    """119 -> '1,19 €'. Ohne Preis ein ehrlicher Strich statt '0,00 €'."""
    if cents is None:
        return "—"
    return f"{int(cents) // 100},{int(cents) % 100:02d} €"


def _stand_letzter_lauf(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "SELECT coalesce(finished_at, started_at) AS stand FROM scrape_run"
        " WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    return row["stand"] if row else None


def katalog_hinweis(con: sqlite3.Connection, jetzt: datetime | None = None) -> str | None:
    """Das Hinweisband aus Spec 11, oder `None`, wenn der Katalog frisch ist.

    Ein Katalog ohne jeden erfolgreichen Lauf bekommt seinen eigenen Satz: das
    ist der Zustand direkt nach der Installation, und Schweigen sähe dort aus
    wie „alles in Ordnung".
    """
    stand = _stand_letzter_lauf(con)
    if stand is None:
        return ("Der Katalog wurde noch nie erfolgreich aktualisiert — "
                "es liegen keine Preise aus einem bekannten Lauf vor.")
    try:
        gelaufen = datetime.fromisoformat(stand)
    except ValueError:
        return f"Der letzte Katalog-Lauf trägt einen unlesbaren Zeitstempel ({stand})."
    tage = ((jetzt or datetime.now()) - gelaufen).days
    if tage > HINWEIS_AB_TAGEN:
        return f"Preise sind {tage} Tage alt."
    return None


def bilddatei(image_dir, image_path: str | None) -> Path | None:
    """`product.image_path` -> tatsächlich vorhandene Datei, oder `None`.

    Nur der Dateiname wird verwendet und unter das konfigurierte Bildver-
    zeichnis gehängt. Damit kann kein Wert aus der Datenbank den Prozess dazu
    bringen, eine Datei ausserhalb dieses Verzeichnisses auszuliefern — und
    ein Katalog, dessen Bilder noch nicht heruntergeladen sind, liefert
    schlicht kein Bild statt eines kaputten Verweises auf `cdn.knuspr.de`.
    """
    if not image_path:
        return None
    name = str(image_path).rsplit("/", 1)[-1]
    if not name or name in (".", ".."):
        return None
    kandidat = Path(image_dir) / name
    return kandidat if kandidat.is_file() else None


def _mit_bild(produkte: list[dict], image_dir) -> list[dict]:
    """Hängt jeder Kachel an, ob sie ein Bild zeigen kann.

    Die Vorlage darf das nicht selbst herausfinden — ein `<img>` auf eine
    fehlende Datei ergibt auf dem Handy ein kaputtes Symbol quer durch das
    Raster.
    """
    for p in produkte:
        p["bild_url"] = (f"/bild/{p['id']}"
                         if bilddatei(image_dir, p.get("image_path")) else None)
    return produkte


# --------------------------------------------------------------------------
# App

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Schema anlegen, bevor der erste Request kommt. Idempotent, teilen sich
    # Web, Crawler und Evals (siehe db.migrate).
    con = db.connect(app.state.db_path)
    try:
        db.migrate(con)
    finally:
        con.close()
    yield


def create_app(db_path: str | Path | None = None,
               image_dir: str | Path | None = None) -> FastAPI:
    """Baut die Anwendung. Pfade als Argument, damit Tests sie umlenken können."""
    app = FastAPI(title="Picknick", lifespan=_lifespan)
    app.state.db_path = str(db_path or os.environ.get(ENV_DB) or db.DEFAULT_DB)
    app.state.image_dir = Path(image_dir or os.environ.get(ENV_IMAGE_DIR)
                               or DEFAULT_IMAGE_DIR)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    vorlagen = Jinja2Templates(directory=str(TEMPLATE_DIR))
    vorlagen.env.filters["euro"] = euro

    def con() -> sqlite3.Connection:
        return db.connect(app.state.db_path)

    def _liste(c, q, l1, l2, l3):
        """Suche ODER Kategorie — nie beides. Ein Suchbegriff schlägt die
        Kategorie, weil er die frischere Absicht der Nutzerin ist."""
        if q and q.strip():
            treffer = search.search(c, q, limit=SEITE)
        else:
            treffer = categories.by_category(c, l1, l2, l3, limit=SEITE)
        return _mit_bild(treffer, app.state.image_dir)

    @app.get("/")
    def start(request: Request):
        """Startweiche nach Rolle (Spec 10): sie -> Katalog, er -> Pick-Liste."""
        if request.cookies.get(COOKIE_ROLLE) == "er":
            return RedirectResponse("/pick", status_code=303)
        # Ohne Cookie ebenfalls der Katalog: die Rollenwahl ist eine Bequem-
        # lichkeit, kein Tor, durch das man erst hindurch muss.
        return RedirectResponse("/katalog", status_code=303)

    @app.get("/rolle")
    def rolle_waehlen(request: Request):
        return vorlagen.TemplateResponse(request, "rolle.html", {
            "rolle": request.cookies.get(COOKIE_ROLLE)})

    @app.post("/rolle")
    def rolle_setzen(request: Request, wer: str = ""):
        """Setzt das Rollen-Cookie.

        ACHTUNG, damit das später niemand für etwas anderes hält: das ist
        KEINE Authentifizierung und keine Sicherheitsgrenze. Das Cookie ist
        frei wählbar, wird nirgends geprüft und steuert ausschliesslich, welche
        Ansicht `/` zeigt. Der Schutz des Shops ist das Tailnet und nichts
        sonst (Spec 10). Wer hier einmal eine Berechtigung aufhängt, hat eine
        Zugangskontrolle gebaut, die jeder Besucher selbst ausstellt.
        """
        antwort = RedirectResponse("/", status_code=303)
        if wer in ROLLEN:
            antwort.set_cookie(COOKIE_ROLLE, wer, max_age=COOKIE_TAGE * 86400,
                               httponly=True, samesite="lax")
        return antwort

    @app.get("/katalog")
    def katalog(request: Request, q: str = "",
                l1: str | None = None, l2: str | None = None,
                l3: str | None = None):
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "katalog.html", {
                "produkte": _liste(c, q, l1, l2, l3),
                "baum": categories.tree(c),
                "hinweis": katalog_hinweis(c),
                "q": q, "l1": l1, "l2": l2, "l3": l3,
                "rolle": request.cookies.get(COOKIE_ROLLE)})
        finally:
            c.close()

    @app.get("/produkte")
    def produkte(request: Request, q: str = "",
                 l1: str | None = None, l2: str | None = None,
                 l3: str | None = None):
        """Nur die Trefferliste — das Stück, das HTMX austauscht.

        Dieselbe Vorlage wie im Vollbild, damit Suche und Kategoriewechsel
        nicht anders aussehen können als der erste Aufruf."""
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "_produkte.html", {
                "produkte": _liste(c, q, l1, l2, l3),
                "q": q, "l1": l1, "l2": l2, "l3": l3})
        finally:
            c.close()

    @app.get("/pick")
    def pick(request: Request):
        return vorlagen.TemplateResponse(request, "pick.html", {
            "rolle": request.cookies.get(COOKIE_ROLLE)})

    @app.get("/bild/{produkt_id}")
    def bild(produkt_id: int):
        c = con()
        try:
            row = c.execute("SELECT image_path FROM product WHERE id = ?",
                            (produkt_id,)).fetchone()
        finally:
            c.close()
        datei = bilddatei(app.state.image_dir, row["image_path"] if row else None)
        if datei is None:
            # 404 statt Platzhalterbild: die Vorlage fragt Bilder gar nicht
            # erst an, die es nicht gibt — kommt trotzdem eine Anfrage, ist
            # das ein Fehler und soll wie einer aussehen.
            return Response(status_code=404)
        return FileResponse(datei)

    return app


app = create_app()


if __name__ == "__main__":
    serve()
