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
from urllib.parse import parse_qsl, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from picknick import db, orders, recipes
from picknick.assistant import chat as chatmodul
from picknick.assistant import vorschlaege as vorschlagsliste
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
    # ws bleibt auf der Vorgabe "auto". Es stand hier einmal auf "none", weil
    # uvicorns Auto-Erkennung das systemweite `websockets` 9.1 importierte und
    # der Start an einem ImportError zerbrach. Das war eine Eigenheit des
    # System-Interpreters, nicht des Shops: im Projekt-venv ist `websockets`
    # gar nicht installiert (der Shop spricht keines), uvicorn schaltet es
    # still ab und lädt sauber. Gemessen 2026-08-28, beide Wege direkt
    # gegeneinander. Läuft das hier je wieder gegen System-Python, kommt der
    # ImportError zurück — dann ist das venv die Antwort, nicht ws="none".
    server = uvicorn.Server(uvicorn.Config(app, log_level="info"))
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


def _posten_mit_bild(zeilen: list[dict], image_dir) -> list[dict]:
    """Dasselbe für Bestellposten — deren `id` ist die Zeile, nicht das Produkt.

    Eine eigene Funktion und nicht `_mit_bild()`: dort steht die Produkt-id im
    Feld `id`, hier die des Postens. Mit derselben Funktion zeigte die
    Pick-Ansicht im Laden fremde Bilder, und zwar plausibel aussehende.
    Freitext-Zeilen haben kein Produkt und damit nie ein Bild.
    """
    for z in zeilen:
        z["bild_url"] = (
            f"/bild/{z['product_id']}"
            if z.get("product_id") and bilddatei(image_dir, z.get("image_path"))
            else None)
    return zeilen


# --------------------------------------------------------------------------
# Formulareingaben
#
# `python-multipart` ist keine Abhängigkeit des Projekts (Spec 15), und
# Starlette 1.6 verweigert `request.form()` ohne sie — auch für schlicht
# urlencodierte Formulare, die es selbst parsen könnte (gemessen 2026-08-28).
# Deshalb wird der Rumpf hier von Hand gelesen. Das ist wenig Code, es hält
# die Abhängigkeitsliste bei dem, was die Spec nennt, und es gilt für beide
# Wege gleich: HTMX schickt seine Werte im Rumpf, ein Formular ohne
# JavaScript ebenso, und Knöpfe ohne Feld bringen ihre Werte in der URL mit.

async def eingaben(request: Request) -> dict[str, str]:
    """Query-Parameter und urlencodierter Rumpf in einem Wörterbuch.

    Der Rumpf gewinnt: er trägt die Eingabe, die der Mensch gerade gemacht
    hat, die URL nur, was in der Vorlage stand.
    """
    werte = dict(request.query_params)
    if request.method in ("POST", "PUT", "PATCH"):
        rumpf = (await request.body()).decode("utf-8", "replace")
        if rumpf:
            werte.update(dict(parse_qsl(rumpf, keep_blank_values=True)))
    return werte


def zahl(wert, vorgabe: int) -> int:
    """`'3'` -> 3, alles Unlesbare -> Vorgabe. Wirft nie.

    Eingaben aus einer URL sind beliebig; eine 500er-Seite wegen eines
    verrutschten Zeichens wäre eine Fehlfunktion und keine Strenge.
    """
    try:
        return int(str(wert).strip())
    except (TypeError, ValueError):
        return vorgabe


def ist_htmx(request: Request) -> bool:
    """Kommt die Anfrage von HTMX? Dann reicht das Bruchstück als Antwort."""
    return request.headers.get("HX-Request") == "true"


def zurueck_zum_katalog(request: Request, vorgabe: str = "/katalog") -> str:
    """Wohin ein Formular ohne JavaScript zurückspringt.

    Aus dem Referer wird ausschliesslich Pfad und Query übernommen und nur,
    wenn der Pfad `/katalog` ist. Der Host wird bewusst weggeworfen: ein
    Weiterleitungsziel, das aus einem Kopf des Aufrufers stammt, ist sonst
    eine offene Weiterleitung — hier ohne echten Schaden, aber es ist die
    Sorte Kleinigkeit, die man nicht stehen lässt.
    """
    referer = request.headers.get("referer") or ""
    teile = urlsplit(referer)
    if teile.path == "/katalog":
        return teile.path + (f"?{teile.query}" if teile.query else "")
    return vorgabe


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
               image_dir: str | Path | None = None,
               chat=None) -> FastAPI:
    """Baut die Anwendung. Pfade als Argument, damit Tests sie umlenken können.

    `chat` ist der Agent aus Spec 6. Er wird hier nur GEBAUT und nicht
    benutzt: `Chat()` legt weder eine Verbindung an noch fragt es die Box.
    Tests schieben einen mit Fake-LLM unter — kein Test darf ins Netz oder die
    Box wecken (Spec 13).
    """
    app = FastAPI(title="Picknick", lifespan=_lifespan)
    app.state.db_path = str(db_path or os.environ.get(ENV_DB) or db.DEFAULT_DB)
    app.state.image_dir = Path(image_dir or os.environ.get(ENV_IMAGE_DIR)
                               or DEFAULT_IMAGE_DIR)
    app.state.chat = chat if chat is not None else chatmodul.Chat()
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

    def _rahmen(request: Request, c: sqlite3.Connection) -> dict:
        """Was jede Vollseite braucht: Rolle und Zahl im Warenkorb-Knopf."""
        return {"rolle": request.cookies.get(COOKIE_ROLLE),
                "korb_anzahl": orders.korb_anzahl(c)}

    def _korb_kontext(c: sqlite3.Connection, fehler: str | None = None) -> dict:
        """Alles, was `_korb.html` braucht — für Vollseite und HTMX-Bruchstück.

        Eine Funktion für beide Wege, aus demselben Grund wie bei der
        Trefferliste in WB-323: sonst entwickelt sich das Bruchstück von der
        ersten Ansicht weg, und niemand merkt es.
        """
        return {"posten": _posten_mit_bild(orders.inhalt(c), app.state.image_dir),
                "stores": db.STORES,
                "laden_titel": orders.LADEN_TITEL,
                "korb_anzahl": orders.korb_anzahl(c),
                "fehler": fehler}

    def _korb_antwort(request: Request, c: sqlite3.Connection,
                      fehler: str | None = None):
        """HTMX bekommt den Korb, ein Formular ohne JavaScript die ganze Seite."""
        if ist_htmx(request):
            return vorlagen.TemplateResponse(request, "_korb.html",
                                             _korb_kontext(c, fehler))
        if fehler:
            # Mit einer Weiterleitung ginge die Begründung verloren, und die
            # Nutzerin sähe nur, dass nichts passiert ist.
            return vorlagen.TemplateResponse(
                request, "warenkorb.html",
                {**_rahmen(request, c), **_korb_kontext(c, fehler),
                 **_chat_kontext(c)})
        return RedirectResponse("/warenkorb", status_code=303)

    @app.get("/katalog")
    def katalog(request: Request, q: str = "",
                l1: str | None = None, l2: str | None = None,
                l3: str | None = None):
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "katalog.html", {
                **_rahmen(request, c),
                "produkte": _liste(c, q, l1, l2, l3),
                "baum": categories.tree(c),
                "hinweis": katalog_hinweis(c),
                "q": q, "l1": l1, "l2": l2, "l3": l3})
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

    # ----------------------------------------------------------------------
    # Warenkorb (Spec 9)

    @app.post("/katalog/einlegen")
    async def katalog_einlegen(request: Request):
        """Der „+"-Knopf an der Kachel: einlegen ohne Seitenwechsel.

        WB-323 hat ihn weggelassen, weil es keinen Warenkorb gab. Jetzt gibt
        es einen — und weil er der meistgedrückte Knopf des Shops ist, gibt er
        sichtbar Rückmeldung: die Menge an der Kachel und die Zahl oben im
        Kopf. Ohne Rückmeldung drückt man zweimal.
        """
        werte = await eingaben(request)
        c = con()
        try:
            try:
                item = orders.einlegen(c, product_id=werte.get("product_id"),
                                       qty=zahl(werte.get("qty"), 1))
            except orders.UngueltigerPosten:
                return Response(status_code=404)
            zeile = next(z for z in orders.inhalt(c) if z["id"] == item)
            if ist_htmx(request):
                return vorlagen.TemplateResponse(request, "_eingelegt.html", {
                    "zeile": zeile, "korb_anzahl": orders.korb_anzahl(c)})
            return RedirectResponse(zurueck_zum_katalog(request),
                                    status_code=303)
        finally:
            c.close()

    @app.get("/warenkorb")
    def warenkorb(request: Request):
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "warenkorb.html", {
                **_rahmen(request, c), **_korb_kontext(c), **_chat_kontext(c)})
        finally:
            c.close()

    @app.post("/warenkorb/einlegen")
    async def warenkorb_einlegen(request: Request):
        """Das Freitext-Feld — das Ventil für alles, was der Katalog nicht hat."""
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                orders.einlegen(c, product_id=werte.get("product_id"),
                                free_text=werte.get("free_text"),
                                qty=zahl(werte.get("qty"), 1))
            except orders.UngueltigerPosten:
                fehler = ("Schreib hin, was es sein soll — ein leeres Feld "
                          "ergibt keine Zeile im Laden.")
            return _korb_antwort(request, c, fehler)
        finally:
            c.close()

    @app.post("/warenkorb/posten/{item_id}/menge")
    async def posten_menge(request: Request, item_id: int):
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                orders.menge_setzen(c, item_id, zahl(werte.get("qty"), 1))
            except orders.UngueltigerPosten as e:
                fehler = str(e)
            return _korb_antwort(request, c, fehler)
        finally:
            c.close()

    @app.post("/warenkorb/posten/{item_id}/laden")
    async def posten_laden(request: Request, item_id: int):
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                orders.laden_setzen(c, item_id, werte.get("store", ""))
            except orders.UngueltigerPosten as e:
                fehler = str(e)
            return _korb_antwort(request, c, fehler)
        finally:
            c.close()

    @app.post("/warenkorb/posten/{item_id}/loeschen")
    def posten_loeschen(request: Request, item_id: int):
        c = con()
        try:
            fehler = None
            try:
                orders.entfernen(c, item_id)
            except orders.UngueltigerPosten as e:
                fehler = str(e)
            return _korb_antwort(request, c, fehler)
        finally:
            c.close()

    @app.post("/warenkorb/abschicken")
    async def warenkorb_abschicken(request: Request):
        """`draft -> offen`. Danach fängt der nächste Korb leer an."""
        werte = await eingaben(request)
        c = con()
        try:
            try:
                bestellung = orders.abschicken(c, note=werte.get("note"))
            except orders.LeererWarenkorb as e:
                return _korb_antwort(request, c, str(e))
            ziel = f"/bestellungen#b{bestellung['id']}"
            if ist_htmx(request):
                # Ein 303 würde HTMX die neue Seite in den Korb hineintauschen;
                # HX-Redirect lässt den Browser richtig navigieren.
                return Response(status_code=204, headers={"HX-Redirect": ziel})
            return RedirectResponse(ziel, status_code=303)
        finally:
            c.close()

    # ----------------------------------------------------------------------
    # Chat (Spec 6 und 9)
    #
    # Der Chat gehört in den Warenkorb und bekommt keinen eigenen Ort. Zwei
    # Dinge sind an dieser Stelle wichtiger als sie aussehen:
    #
    # * **Das Rendern der Seite fragt die vLLM-Box NICHT.** Der Zustand wird
    #   nachgeladen (`/warenkorb/chat/zustand`). Sonst hinge jeder Blick in
    #   den Warenkorb bis zu drei Sekunden am health-Timeout — und jeder
    #   Testlauf ginge ins Netz.
    # * **Ein Vorschlag ist noch kein Posten.** Erst „Ja" legt ein. Deshalb
    #   antwortet die Entscheidung mit Chat UND Korb (`_chat_antwort`), sonst
    #   sähe die Nutzerin ihre Zeile im Korb erst nach dem nächsten Laden.

    def _chat_kontext(c: sqlite3.Connection, fehler: str | None = None,
                      zustand=None, satz: str = "") -> dict:
        """Alles, was `_chat.html` braucht — für Vollseite und Bruchstück."""
        # Nur nachsehen, nicht anlegen: ein Blick in den Warenkorb darf keine
        # Bestellung erzeugen. Angelegt wird er erst im Chat-Zug selbst.
        korb_id = orders.warenkorb_id(c)
        verlauf = vorschlagsliste.verlauf(c, korb_id) if korb_id else []
        for zeile in verlauf:
            _posten_mit_bild(zeile["vorschlaege"], app.state.image_dir)
        return {"verlauf": verlauf, "chat_fehler": fehler,
                "chat_zustand": zustand, "satz": satz}

    def _chat_antwort(request: Request, c: sqlite3.Connection,
                      fehler: str | None = None, zustand=None,
                      satz: str = ""):
        """HTMX bekommt Chat + Korb, ein Formular ohne JavaScript die Seite."""
        kontext = {**_chat_kontext(c, fehler, zustand, satz),
                   **_korb_kontext(c)}
        if ist_htmx(request):
            return vorlagen.TemplateResponse(request, "_chat_antwort.html",
                                             kontext)
        return vorlagen.TemplateResponse(
            request, "warenkorb.html", {**_rahmen(request, c), **kontext})

    @app.get("/warenkorb/chat/zustand")
    def chat_zustand(request: Request):
        """Bedient die Box? Nachgeladen, damit der Korb sofort da ist.

        Dieser Aufruf ist es, der `wake-vllm` anstösst (Spec 6) — nicht
        blockierend, und die Antwort trägt den Zähler „noch ~N s".
        """
        return vorlagen.TemplateResponse(request, "_chatzustand.html",
                                         {"chat_zustand": app.state.chat.zustand()})

    @app.post("/warenkorb/chat")
    async def chat_senden(request: Request):
        """Ein Chat-Zug. Legt NICHTS in den Korb — nur Vorschläge (Spec 6)."""
        werte = await eingaben(request)
        satz = werte.get("satz", "")
        c = con()
        try:
            try:
                app.state.chat.turn(c, satz)
            except chatmodul.ChatNichtVerfuegbar as e:
                # Nur der Chat ist betroffen (Spec 11). Der Satz bleibt im
                # Feld stehen, damit er nicht noch einmal getippt werden muss.
                return _chat_antwort(request, c, zustand=e.zustand, satz=satz)
            except chatmodul.ChatFehler as e:
                return _chat_antwort(request, c, fehler=str(e), satz=satz)
            return _chat_antwort(request, c)
        finally:
            c.close()

    @app.post("/warenkorb/vorschlag/{sid}/entscheiden")
    async def vorschlag_entscheiden(request: Request, sid: int):
        """„Ja" oder „Nein" zu einer Zeile — das Eval-Label (Spec 8.1)."""
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                vorschlagsliste.entscheiden(c, sid, werte.get("decision", ""))
            except vorschlagsliste.VorschlagFehler as e:
                fehler = str(e)
            return _chat_antwort(request, c, fehler=fehler)
        finally:
            c.close()

    @app.post("/warenkorb/chat/{mid}/alle")
    async def vorschlaege_alle(request: Request, mid: int):
        """Sammelknopf. Rührt nur an, was noch offen ist."""
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                vorschlagsliste.alle_entscheiden(c, mid,
                                                 werte.get("decision", ""))
            except vorschlagsliste.VorschlagFehler as e:
                fehler = str(e)
            return _chat_antwort(request, c, fehler=fehler)
        finally:
            c.close()

    # ----------------------------------------------------------------------
    # Bestellungen und Pick-Ansicht (Spec 9)

    @app.get("/bestellungen")
    def bestelluebersicht(request: Request):
        c = con()
        try:
            liste = orders.bestellungen(c)
            for b in liste:
                b["posten"] = orders.posten(c, b["id"])
            return vorlagen.TemplateResponse(request, "bestellungen.html", {
                **_rahmen(request, c), "bestellungen": liste})
        finally:
            c.close()

    def _pick_kontext(c: sqlite3.Connection, order_id: int | None) -> dict:
        if order_id is None:
            return {"bestellung": None, "gruppen": [],
                    "auswahl": orders.offene(c)}
        gruppen = orders.nach_laden(c, order_id)
        for g in gruppen:
            _posten_mit_bild(g["posten"], app.state.image_dir)
        return {"bestellung": orders.bestellung(c, order_id),
                "gruppen": gruppen,
                "offen": sum(g["n_offen"] for g in gruppen),
                "auswahl": [b for b in orders.offene(c) if b["id"] != order_id]}

    @app.get("/pick")
    def pick(request: Request):
        """Die Pick-Ansicht macht mit der ältesten offenen Bestellung auf.

        Ohne Auswahlschritt: im Laden will man die Liste sehen, nicht erst
        eine Liste von Listen. Gibt es weitere offene, stehen sie unten.
        """
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "pick.html", {
                **_rahmen(request, c), **_pick_kontext(c, orders.naechste(c))})
        finally:
            c.close()

    @app.get("/pick/{order_id}")
    def pick_eine(request: Request, order_id: int):
        c = con()
        try:
            if orders.bestellung(c, order_id) is None:
                return Response(status_code=404)
            return vorlagen.TemplateResponse(request, "pick.html", {
                **_rahmen(request, c), **_pick_kontext(c, order_id)})
        finally:
            c.close()

    @app.post("/pick/{order_id}/posten/{item_id}")
    async def pick_abhaken(request: Request, order_id: int, item_id: int):
        werte = await eingaben(request)
        c = con()
        try:
            try:
                orders.abhaken(c, item_id, gepickt=werte.get("gepickt") != "0")
            except orders.UngueltigerPosten:
                return Response(status_code=404)
            if ist_htmx(request):
                return vorlagen.TemplateResponse(request, "_pick.html",
                                                 _pick_kontext(c, order_id))
            return RedirectResponse(f"/pick/{order_id}", status_code=303)
        finally:
            c.close()

    def _gibt_es(c: sqlite3.Connection, recipe_id: int) -> bool:
        """Gibt es dieses Rezept? Eine 404 ist die ehrliche Antwort auf eine
        Adresse, die ins Leere zeigt — ein Fehlertext im Rezept wäre es nicht,
        denn es gibt kein Rezept, in dem er stehen könnte."""
        return c.execute("SELECT 1 FROM recipe WHERE id = ?",
                         (recipe_id,)).fetchone() is not None

    # ----------------------------------------------------------------------
    # Rezepte (Spec 9)
    #
    # Eine Spalte, dieselben Tap-Ziele wie überall (stil.css), dieselben
    # HTMX-Bruchstücke wie im Warenkorb. Der Unterschied zum Korb ist der
    # Punkt, an dem dieses Ticket steht: ein Rezept überlebt den Katalog, und
    # eine Zutat, die inzwischen `active = 0` ist, wird angezeigt und
    # eingelegt statt stillschweigend zu verschwinden.

    def _rezept_kontext(c: sqlite3.Connection, recipe_id: int,
                        q: str = "", meldung: str | None = None,
                        fehler: str | None = None) -> dict:
        """Alles, was `_rezept.html` braucht — für Vollseite und Bruchstück."""
        r = recipes.rezept(c, recipe_id)
        _posten_mit_bild(r["zutaten"], app.state.image_dir)
        return {"rezept": r, "q": q, "meldung": meldung, "fehler": fehler,
                "treffer": _mit_bild(search.search(c, q, limit=SEITE),
                                     app.state.image_dir) if q.strip() else []}

    def _rezept_antwort(request: Request, c: sqlite3.Connection, recipe_id: int,
                        meldung: str | None = None, fehler: str | None = None):
        """HTMX bekommt die Zutatenliste, ein Formular ohne JS die ganze Seite."""
        kontext = _rezept_kontext(c, recipe_id, meldung=meldung, fehler=fehler)
        if ist_htmx(request):
            return vorlagen.TemplateResponse(request, "_rezept.html", kontext)
        if meldung or fehler:
            # Eine Weiterleitung würde die Begründung verlieren, und die
            # Nutzerin sähe nur, dass nichts passiert ist.
            return vorlagen.TemplateResponse(
                request, "rezept.html", {**_rahmen(request, c), **kontext})
        return RedirectResponse(f"/rezepte/{recipe_id}", status_code=303)

    @app.get("/rezepte")
    def rezeptliste(request: Request):
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "rezepte.html", {
                **_rahmen(request, c), "rezepte": recipes.rezepte(c)})
        finally:
            c.close()

    @app.post("/rezepte")
    async def rezept_anlegen(request: Request):
        werte = await eingaben(request)
        c = con()
        try:
            try:
                neu = recipes.anlegen(c, werte.get("name"))
            except recipes.RezeptFehler as e:
                return vorlagen.TemplateResponse(request, "rezepte.html", {
                    **_rahmen(request, c), "rezepte": recipes.rezepte(c),
                    "fehler": str(e)})
            return RedirectResponse(f"/rezepte/{neu}", status_code=303)
        finally:
            c.close()

    @app.get("/rezepte/{recipe_id}")
    def rezept_ansicht(request: Request, recipe_id: int, q: str = ""):
        c = con()
        try:
            try:
                kontext = _rezept_kontext(c, recipe_id, q=q)
            except recipes.RezeptFehler:
                return Response(status_code=404)
            return vorlagen.TemplateResponse(request, "rezept.html", {
                **_rahmen(request, c), **kontext})
        finally:
            c.close()

    @app.get("/rezepte/{recipe_id}/suche")
    def rezept_suche(request: Request, recipe_id: int, q: str = ""):
        """Nur die Trefferliste — das Stück, das HTMX beim Tippen austauscht."""
        c = con()
        try:
            try:
                kontext = _rezept_kontext(c, recipe_id, q=q)
            except recipes.RezeptFehler:
                return Response(status_code=404)
            return vorlagen.TemplateResponse(request, "_rezept_treffer.html",
                                             kontext)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/bearbeiten")
    async def rezept_bearbeiten(request: Request, recipe_id: int):
        werte = await eingaben(request)
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            try:
                recipes.aendern(c, recipe_id, name=werte.get("name"),
                                servings=werte.get("servings"),
                                note=werte.get("note"))
            except recipes.RezeptFehler as e:
                # Der Name war leer: die Begründung muss stehen bleiben, sonst
                # sieht die Nutzerin nur, dass nichts gespeichert wurde.
                return _rezept_antwort(request, c, recipe_id, fehler=str(e))
            return RedirectResponse(f"/rezepte/{recipe_id}", status_code=303)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/loeschen")
    def rezept_loeschen(request: Request, recipe_id: int):
        c = con()
        try:
            try:
                recipes.loeschen(c, recipe_id)
            except recipes.RezeptFehler:
                return Response(status_code=404)
            return RedirectResponse("/rezepte", status_code=303)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/zutaten")
    async def rezept_zutat(request: Request, recipe_id: int):
        werte = await eingaben(request)
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            fehler = None
            try:
                recipes.zutat_hinzufuegen(
                    c, recipe_id, product_id=werte.get("product_id"),
                    free_text=werte.get("free_text"),
                    qty=zahl(werte.get("qty"), 1))
            except orders.UngueltigerPosten:
                fehler = ("Schreib hin, was es sein soll — ein leeres Feld "
                          "ergibt keine Zutat.")
            return _rezept_antwort(request, c, recipe_id, fehler=fehler)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/zutaten/{item_id}/menge")
    async def rezept_zutat_menge(request: Request, recipe_id: int, item_id: int):
        werte = await eingaben(request)
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            fehler = None
            try:
                recipes.zutat_menge(c, item_id, zahl(werte.get("qty"), 1))
            except orders.UngueltigerPosten as e:
                fehler = str(e)
            return _rezept_antwort(request, c, recipe_id, fehler=fehler)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/zutaten/{item_id}/loeschen")
    def rezept_zutat_loeschen(request: Request, recipe_id: int, item_id: int):
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            fehler = None
            try:
                recipes.zutat_entfernen(c, item_id)
            except orders.UngueltigerPosten as e:
                fehler = str(e)
            return _rezept_antwort(request, c, recipe_id, fehler=fehler)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/korb")
    def rezept_in_den_korb(request: Request, recipe_id: int):
        """„Alles in den Warenkorb“ — mit Bericht, nicht mit blossem „ok“.

        Der Bericht ist der Grund, warum diese Route eine Meldung zurückgibt
        und nicht einfach weiterleitet: liegt eine Zutat im Korb, die nicht
        mehr im Katalog steht, muss die Nutzerin das hier lesen — nicht erst
        im Laden.
        """
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            try:
                bericht = recipes.in_den_korb(c, recipe_id)
            except recipes.LeeresRezept as e:
                return _rezept_antwort(request, c, recipe_id, fehler=str(e))
            return _rezept_antwort(request, c, recipe_id,
                                   meldung=bericht["meldung"])
        finally:
            c.close()

    @app.post("/bestellungen/{order_id}/rezept")
    async def bestellung_zu_rezept(request: Request, order_id: int):
        """Spec 6: der Knopf an der erledigten Bestellung."""
        werte = await eingaben(request)
        c = con()
        try:
            try:
                neu = recipes.aus_bestellung(c, order_id,
                                             name=werte.get("name"))
            except recipes.RezeptFehler as e:
                liste = orders.bestellungen(c)
                for b in liste:
                    b["posten"] = orders.posten(c, b["id"])
                return vorlagen.TemplateResponse(request, "bestellungen.html", {
                    **_rahmen(request, c), "bestellungen": liste,
                    "fehler": str(e)}, status_code=404)
            return RedirectResponse(f"/rezepte/{neu}", status_code=303)
        finally:
            c.close()

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
