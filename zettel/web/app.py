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
from urllib.parse import parse_qsl, quote, urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from zettel import (betrieb, bons as bonmodul, db, gerichte, miniaturen,
                    obs, orders, recipes)
from zettel import umgebung as umg
from zettel.assistant import chat as chatmodul
from zettel.assistant import entwurf as entwuerfe
from zettel.assistant import oberbegriffe
from zettel.assistant import vorschlaege as vorschlagsliste
from zettel.assistant import zugrezept as zugrezepte
from zettel.catalog import categories, search
from zettel.llm import wake
from zettel.web import multipart

HIER = Path(__file__).parent
TEMPLATE_DIR = HIER / "templates"
STATIC_DIR = HIER / "static"

#: Wo die Bilder liegen, die der Crawler heruntergeladen hat (Spec 5.2).
DEFAULT_IMAGE_DIR = "data/images"

#: Wo die hochgeladenen Kassenbons landen (WB-344). Unter `data/`, wie die
#: Bilder — das ist der Ort für alles, was Nutzdaten sind und nicht Quelle,
#: und `.gitignore` hält es aus dem Repo. Die nächtliche Sicherung fasst
#: allerdings nur die Datenbank an: ein Bon, der hier gelöscht wird, ist weg.
DEFAULT_BON_DIR = "data/bons"

#: Wieviel ein `multipart/form-data`-Rumpf über die Datei hinaus wiegen darf:
#: Grenzzeichenketten, Feldköpfe, das zweite Formularfeld. 64 KB sind dafür
#: reichlich. Der Zuschlag existiert, damit die Grenze aus `Content-Length`
#: eine Datei von exakt zulässiger Grösse nicht doch noch abweist.
MULTIPART_ZUSCHLAG = 64 * 1024

#: Wie viele Produkte zur Korrektur einer Bon-Zeile vorgelegt werden (WB-358).
#: Fünf, wie `plan.KANDIDATEN_MODELL`: die Liste steht auf dem Handy neben
#: einer einzigen Zeile, und wer nach fünf Treffern nichts Passendes sieht,
#: tippt einen anderen Begriff — er scrollt nicht.
KORREKTUREN = 5

#: Wie viele Kacheln eine Liste höchstens zeigt. Auf dem Handy scrollt niemand
#: durch 900 Produkte; wer mehr will, sucht oder steigt eine Ebene tiefer.
SEITE = 60

#: Ab wann das Hinweisband erscheint (Spec 11).
HINWEIS_AB_TAGEN = 3

#: Wie viele Züge des Chats offen dastehen (WB-372). Ältere werden GAR NICHT
#: gerendert und mit einem Tipp nachgeholt — sie sind eingeklappt, nicht weg;
#: an der Datenbank ändert diese Zahl nichts (siehe `vorschlaege.verlauf`).
#:
#: Drei, und das ist eine Abwägung und keine runde Zahl. Gearbeitet wird immer
#: am NEUESTEN Zug: dort stehen die Zeilen, die noch zu entscheiden sind. Der
#: Zug davor ist der, in dem man nachsieht, ob man dieselbe Zutat nicht eben
#: schon bestätigt hat, und der dritte fängt die Auffächerung ab — ein
#: Oberbegriff (WB-368) kostet einen eigenen Zug, ohne selbst einer zu sein,
#: und mit zwei Zügen wäre die Frage schon aus dem Bild, während man ihre
#: Antwort noch bearbeitet.
#:
#: Weiter zurück wird nicht gearbeitet, sondern nachgeschlagen, und dafür gibt
#: es den Knopf. Gemessen mit `scripts/groesse_probe.py` (17 Züge, 153
#: Vorschläge), Seitengrösse je Grenze:
#:
#:     ohne Grenze   215.410 Bytes      3 Züge    45.543 Bytes
#:     1 Zug          21.240 Bytes      5 Züge    70.077 Bytes
#:
#: Rund 12 KB je Zug, und die gehen bei JEDEM Blick in den Warenkorb über
#: Mobilfunk. Zwischen 1 und 3 liegen 24 KB und der Unterschied zwischen
#: „arbeiten" und „nachschlagen"; ab 5 wird es wieder teuer, ohne dass dort
#: noch entschieden würde.
VERLAUF_ZUEGE = 3

#: Wie lange ein Produktfoto im Browser liegen bleiben darf (WB-374). Sieben
#: Tage, und das ist eine Abwägung: der Bildweg heisst `/bild/{id}` und trägt
#: keine Version, also wäre `immutable` falsch — wechselt knuspr.de das Foto
#: eines Produkts, zeigt das Telefon bis zu einer Woche das alte. Das ist bei
#: Lebensmitteln folgenlos, und dafür fragt der Katalog innerhalb einer Woche
#: gar nicht erst nach. Danach greift der ETag: die Antwort ist dann `304`
#: ohne Rumpf, nicht das Bild noch einmal.
BILD_MAX_AGE = 7 * 24 * 3600
BILD_CACHE = f"public, max-age={BILD_MAX_AGE}"

#: Nur für die Endungen, die Pythons `mimetypes` unter 3.10 nicht kennt. Alles
#: andere darf Starlette selbst raten.
BILD_TYPEN = {".webp": "image/webp"}

#: Rollen-Cookie. Siehe `waehle_rolle()`: das ist KEINE Authentifizierung.
COOKIE_ROLLE = "zettel_rolle"
#: Der Name von vor WB-401. Nur gelesen, nie geschrieben. Ein Cookie lebt im
#: Browser und wandert bei einer Umbenennung nicht mit: ohne diesen Rückfall
#: landete er nach dem Neustart auf dem Katalog statt auf der Pick-Liste und
#: müsste die Rolle neu wählen — eine kleine Sache, aber eine, die niemand
#: bestellt hat. Fällt mit `umgebung.RUECKFALL_BIS` weg.
ALT_COOKIE_ROLLE = "picknick_rolle"
ROLLEN = ("sie", "er")
COOKIE_TAGE = 365


def rolle_aus(request) -> str | None:
    """Die gewählte Rolle aus dem Cookie — neuer Name vor altem (WB-401)."""
    return (request.cookies.get(COOKIE_ROLLE)
            or request.cookies.get(ALT_COOKIE_ROLLE))


# --------------------------------------------------------------------------
# Bindung — die einzige Sicherheitsgrenze
#
# Kein Passwort, der Rahmen ist das Tailnet (Spec 10). Ein Prozess, der auf
# 0.0.0.0 lauscht, steht im nächsten fremden WLAN offen — deshalb wird die
# Adresse hier geprüft, bevor überhaupt ein Socket entsteht, und ein
# unerlaubter Wunsch mit einem Fehler beantwortet statt stillschweigend
# korrigiert. Wer die Vorgabe überschreibt, soll es merken.

ENV_HOST = "ZETTEL_HOST"
ENV_PORT = "ZETTEL_PORT"
ENV_DB = "ZETTEL_DB"
ENV_IMAGE_DIR = "ZETTEL_IMAGE_DIR"
ENV_BON_DIR = "ZETTEL_BON_DIR"

DEFAULT_PORT = 8730

#: Der CGNAT-Bereich, aus dem Tailscale seine Adressen vergibt.
TAILNET = ipaddress.ip_network("100.64.0.0/10")

#: Ein Ziel im Tailnet für die Routenfrage in `eigene_tailnet_adresse()`.
#: 100.100.100.100 ist der MagicDNS-Dienst, den Tailscale auf jeder Maschine
#: einrichtet — gesendet wird an ihn nie ein Paket, er ist nur die Frage
#: „welchen Absender nähme ein Weg dorthin?".
_TAILNET_SONDE = ("100.100.100.100", 53)


def eigene_tailnet_adresse() -> str | None:
    """Die IPv4-Adresse dieser Maschine im Tailnet — oder None.

    Die Adresse steht mit Absicht nicht im Code: sie ist auf jeder Maschine
    eine andere, und in einem veröffentlichten Repo wäre sie die eines
    privaten Geräts (WB-388). Statt alle Interfaces zu durchsuchen, wird die
    Routing-Tabelle gefragt: ein UDP-`connect` sendet nichts, es wählt nur
    die Absenderadresse für den Weg ins Tailnet. Ohne Tailscale fällt die
    Wahl auf eine Adresse ausserhalb von 100.64.0.0/10 oder scheitert — dann
    gibt es keine, und die Vorgabe ist allein loopback.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonde:
            sonde.connect(_TAILNET_SONDE)
            adresse = ipaddress.ip_address(sonde.getsockname()[0])
    except OSError:
        return None
    if adresse.version == 4 and adresse in TAILNET:
        return str(adresse)
    return None


def standard_hosts() -> list[str]:
    """Die Vorgabe: die eigene Tailnet-Adresse (falls vorhanden), dann loopback.

    Dieselbe Reihenfolge wie die frühere feste Vorgabe, damit ein Neustart
    auf derselben Maschine auf denselben Adressen lauscht wie bisher.
    """
    tailnet = eigene_tailnet_adresse()
    return ([tailnet] if tailnet else []) + ["127.0.0.1"]


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
    """Die Bindeadressen aus `ZETTEL_HOST`, komma-getrennt. Alle geprüft.

    Ungesetzt gilt `standard_hosts()`: die Tailnet-Adresse wird erfragt statt
    hinterlegt. Auch die erfragte Adresse geht durch `pruefe_host()` — die
    Weissliste gilt für jede Quelle.
    """
    umgebung = os.environ if umgebung is None else umgebung
    roh = umg.wert(ENV_HOST, umgebung)
    if roh is None:
        return [pruefe_host(h) for h in standard_hosts()]
    hosts = [h.strip() for h in roh.split(",") if h.strip()]
    if not hosts:
        # Gesetzt, aber leer: das ist ein Konfigurationsfehler und wird nicht
        # stillschweigend zur Vorgabe geglättet — sonst lauscht der Prozess
        # woanders, als in der Konfiguration steht.
        raise UnsichereBindung(f"{ENV_HOST} ist gesetzt, enthält aber keine Adresse.")
    return [pruefe_host(h) for h in hosts]


def port_aus_umgebung(umgebung=None) -> int:
    umgebung = os.environ if umgebung is None else umgebung
    return int(umg.wert(ENV_PORT, umgebung) or DEFAULT_PORT)


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
            "und stimmt die Adresse in ZETTEL_HOST noch?") from e
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


def menge(wert) -> str:
    """3.0 -> '3', 0.5 -> '0,5' (WB-338).

    Die Zutatenmengen einer Rezeptseite sind Fliesskommazahlen, und „3.0
    Liter Wasser" liest sich wie ein Messwert statt wie eine Angabe im
    Kochbuch. Komma statt Punkt aus demselben Grund wie beim Preis: die
    Oberfläche ist deutsch.
    """
    if wert in (None, ""):
        return ""
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return str(wert)
    if zahl.is_integer():
        return str(int(zahl))
    return f"{zahl:g}".replace(".", ",")


def zeit(wert, jetzt: datetime | None = None) -> str:
    """'2026-08-28 17:32:47' -> 'gestern um 17:32'. Auch die Form mit `T`.

    Die Datenbank trägt DENSELBEN Zeitpunkt in zwei Schreibweisen: `orders`
    schreibt mit Leerzeichen (`orders.bestellung.jetzt`), `scrape_run` mit `T`
    (`scrapers.knuspr`). Beide sind ISO, `fromisoformat` liest beide — und die
    Oberfläche soll den Unterschied gar nicht erst zeigen müssen.

    Heute, gestern und vorgestern bekommen ein Wort statt eines Datums: an
    einer Bestellzeile ist „gestern um 17:32" die Antwort auf die Frage, die
    man wirklich hat, und ein SQL-Feld ist es nicht. Älteres bekommt das
    deutsche Datum — dieselbe Form, in der die Bonliste seit jeher schreibt
    (`bons.ablage`).

    Unlesbares kommt unverändert zurück. Ein erfundener Zeitpunkt wäre die
    schlechtere Antwort als ein hässlicher echter.
    """
    if wert in (None, ""):
        return ""
    if isinstance(wert, datetime):
        gelesen = wert
    else:
        try:
            gelesen = datetime.fromisoformat(str(wert).strip())
        except (TypeError, ValueError):
            return str(wert)
    uhr = gelesen.strftime("%H:%M")
    # Auf Kalendertage gerechnet und nicht auf 24-Stunden-Abstände: um 00:30
    # ist 23:50 „gestern", auch wenn es vierzig Minuten her ist.
    tage = ((jetzt or datetime.now()).date() - gelesen.date()).days
    if tage == 0:
        return f"heute um {uhr}"
    if tage == 1:
        return f"gestern um {uhr}"
    if tage == 2:
        return f"vorgestern um {uhr}"
    return f"{gelesen.strftime('%d.%m.%Y')} um {uhr}"


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


def _etag_passt(kopf: str | None, etag: str) -> bool:
    """Deckt `If-None-Match` den ausgelieferten ETag ab?

    Der Kopf ist eine Liste (`"a", "b"`), darf `*` sein und darf jeden Eintrag
    als schwachen Vergleich markieren (`W/"a"`). Für eine unveränderte Datei
    ist schwach und stark dasselbe, also wird das Präfix schlicht abgestreift.
    """
    if not kopf:
        return False
    if kopf.strip() == "*":
        return True
    for teil in kopf.split(","):
        teil = teil.strip()
        if teil.startswith(("W/", "w/")):
            teil = teil[2:]
        if teil == etag:
            return True
    return False


def _bild_antwort(request: Request, datei: Path) -> Response:
    """Eine Bilddatei mit Zwischenspeicher-Köpfen und bedingter Antwort.

    `FileResponse` allein liefert weder `Cache-Control` noch `304`: Starlette
    beantwortet bedingte Anfragen nur in `StaticFiles`, nicht in einer
    einzelnen Dateiantwort. Vor WB-374 lud deshalb jeder Aufruf des Katalogs
    sämtliche Bilder erneut — gemessen 17,3 MB je Trefferliste, jedes Mal.

    Der ETag kommt aus `stat()` (Zeitstempel und Grösse) und wird hier von
    Starlette selbst gesetzt; `stat_result` wird nur deshalb mitgegeben, damit
    er schon vor dem Senden feststeht und mit dem Kopf der Anfrage verglichen
    werden kann.
    """
    antwort = FileResponse(
        datei,
        stat_result=datei.stat(),
        # `.webp` kennt Pythons `mimetypes` unter 3.10 nicht; ohne diese Zeile
        # geht die Miniatur als `application/octet-stream` raus und der Browser
        # bietet sie zum Herunterladen an, statt sie in die Kachel zu setzen.
        media_type=BILD_TYPEN.get(datei.suffix.lower()),
        headers={"Cache-Control": BILD_CACHE})
    etag = antwort.headers.get("etag", "")
    if etag and _etag_passt(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={
            "ETag": etag,
            "Cache-Control": BILD_CACHE,
            "Last-Modified": antwort.headers.get("last-modified", "")})
    return antwort


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


async def mehrfach(request: Request, name: str) -> list[str]:
    """Alle Werte eines Feldes, das mehrfach vorkommen darf (Kästchen).

    `eingaben()` faltet den Rumpf in ein Wörterbuch und behält bei doppelten
    Namen den letzten Wert — für ein Formular mit einem Wert je Feld genau
    richtig, für die Sortenauswahl aus WB-368 genau falsch: dort ist „mehrere
    gehen" der Sinn der Sache, und von fünf angekreuzten Sorten käme sonst
    eine an.
    """
    werte = [v for k, v in request.query_params.multi_items() if k == name]
    if request.method in ("POST", "PUT", "PATCH"):
        rumpf = (await request.body()).decode("utf-8", "replace")
        werte += [v for k, v in parse_qsl(rumpf, keep_blank_values=True)
                  if k == name and v]
    return werte


class Formular(dict):
    """Die Eingaben eines Requests, EINMAL gelesen (WB-409).

    Sie sieht aus wie das Wörterbuch aus `eingaben()` und kann daneben
    `alle()` wie `mehrfach()`. Der Grund für sie ist nicht Bequemlichkeit,
    sondern das `await`: solange ein Eingang den Rumpf selbst liest, MUSS er
    `async def` sein — und ein `async def`, das danach dreissig Sekunden auf
    ein Modell wartet, hält die ganze Ereignisschleife an.

    Gemessen am laufenden Shop, 2026-08-30: während eines Modellaufrufs
    brauchte `GET /chat` **14,17 s** statt 4 ms. Der Shop stand für BEIDE
    Nutzerinnen, solange irgendwo ein Zug lief — und ein zweiter Wechsel
    stellte sich hinter den ersten, statt neben ihn.

    Mit dieser Abhängigkeit liest die Ereignisschleife den Rumpf, und der
    Eingang selbst ist ein gewöhnliches `def`. FastAPI führt ein solches im
    Threadpool aus, und die Schleife bleibt frei.
    """

    def __init__(self, werte: dict, mehrfach_werte: dict):
        super().__init__(werte)
        self._mehrfach = mehrfach_werte

    def alle(self, name: str) -> list[str]:
        """Alle Werte eines Feldes, das mehrfach vorkommen darf (Kästchen)."""
        return list(self._mehrfach.get(name, ()))


async def formular(request: Request) -> Formular:
    """Query und Rumpf in EINEM Lesevorgang — als Abhängigkeit.

    Der Rumpf gewinnt über die URL, genau wie in `eingaben()`: er trägt die
    Eingabe, die der Mensch gerade gemacht hat.
    """
    paare = list(request.query_params.multi_items())
    if request.method in ("POST", "PUT", "PATCH"):
        rumpf = (await request.body()).decode("utf-8", "replace")
        if rumpf:
            paare += parse_qsl(rumpf, keep_blank_values=True)
    werte: dict = {}
    viele: dict = {}
    for k, v in paare:
        werte[k] = v
        if v:
            viele.setdefault(k, []).append(v)
    return Formular(werte, viele)


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
    # Beim Herunterfahren die Schlange leeren: der Export läuft in einem
    # Hintergrund-Thread (zettel.obs.otel), und ein systemd-Neustart nähme
    # sonst die letzten Spans mit. Wartet höchstens fünf Sekunden — ein
    # Neustart soll nicht an einem Collector hängen bleiben.
    obs.flush(5_000)


def create_app(db_path: str | Path | None = None,
               image_dir: str | Path | None = None,
               bon_dir: str | Path | None = None,
               chat=None, zuordner=None, bonlaeufe=None) -> FastAPI:
    """Baut die Anwendung. Pfade als Argument, damit Tests sie umlenken können.

    `chat` ist der Agent aus Spec 6. Er wird hier nur GEBAUT und nicht
    benutzt: `Chat()` legt weder eine Verbindung an noch fragt es die Box.
    Tests schieben einen mit Fake-LLM unter — kein Test darf ins Netz oder die
    Box wecken (Spec 13).
    """
    # Der Tracer wird hier eingerichtet und nicht beim ersten Chat-Zug: der
    # OpenAIInstrumentor patcht das openai-SDK, und das soll einmal beim
    # Hochfahren passieren und nicht mitten in einem Request. `einrichten()`
    # wirft nie und blockiert nicht — ein Phoenix, das nicht läuft, darf den
    # Shop nicht am Starten hindern (Spec 7.3). Abschaltbar über
    # ZETTEL_TRACING=0; die Testsuite tut genau das (tests/conftest.py).
    obs.einrichten()

    app = FastAPI(title="Zettel", lifespan=_lifespan)
    app.state.db_path = str(db_path or umg.wert(ENV_DB) or db.DEFAULT_DB)
    app.state.image_dir = Path(image_dir or umg.wert(ENV_IMAGE_DIR)
                               or DEFAULT_IMAGE_DIR)
    app.state.bon_dir = Path(bon_dir or umg.wert(ENV_BON_DIR)
                             or DEFAULT_BON_DIR)
    app.state.chat = chat if chat is not None else chatmodul.Chat()
    # Beide wie `chat`: hier nur GEBAUT, nicht benutzt. `Zuordner()` legt keine
    # Verbindung an und fragt die Box nicht, `Laeufe()` startet keinen Thread.
    # Tests schieben einen Zuordner mit Fake-LLM unter und ein `Laeufe`, das
    # synchron arbeitet (`bons.sofort`) — kein Test darf ins Netz oder die Box
    # wecken (Spec 13).
    app.state.bonzuordner = (zuordner if zuordner is not None
                             else bonmodul.Zuordner())
    app.state.bonlaeufe = (bonlaeufe if bonlaeufe is not None
                           else bonmodul.Laeufe())
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    vorlagen = Jinja2Templates(directory=str(TEMPLATE_DIR))
    vorlagen.env.filters["euro"] = euro
    vorlagen.env.filters["menge"] = menge
    vorlagen.env.filters["zeit"] = zeit

    def con() -> sqlite3.Connection:
        return db.connect(app.state.db_path)

    def _liste(c, q, l1, l2, l3) -> dict:
        """Suche ODER Kategorie — nie beides. Ein Suchbegriff schlägt die
        Kategorie, weil er die frischere Absicht der Nutzerin ist.

        Gibt `produkte` UND `gesamt` zurück (WB-375). `gesamt` ist die Zahl
        aller Treffer, nicht die der gezeigten — nur mit ihr kann die Liste
        sagen, dass sie bei `SEITE` geschnitten wurde. Ohne diesen Satz
        verspricht das Abzeichen an der Kategorie („Aufschnitt 240") mehr, als
        der Zweig liefert, und die Suche nach „Milch" sieht aus, als hätte der
        Katalog nur 60 davon.
        """
        if q and q.strip():
            treffer = search.search(c, q, limit=SEITE)
            gesamt = search.count(c, q)
        else:
            treffer = categories.by_category(c, l1, l2, l3, limit=SEITE)
            gesamt = categories.count_by_category(c, l1, l2, l3)
        return {"produkte": _mit_bild(treffer, app.state.image_dir),
                "gesamt": gesamt}

    @app.get("/")
    def start(request: Request):
        """Startweiche nach Rolle (Spec 10): sie -> Katalog, er -> Pick-Liste."""
        if rolle_aus(request) == "er":
            return RedirectResponse("/pick", status_code=303)
        # Ohne Cookie ebenfalls der Katalog: die Rollenwahl ist eine Bequem-
        # lichkeit, kein Tor, durch das man erst hindurch muss.
        return RedirectResponse("/katalog", status_code=303)

    @app.get("/rolle")
    def rolle_waehlen(request: Request):
        # Öffnet eine Verbindung nur für den Rahmen — die Rollenwahl selbst
        # braucht keine. Das ist der Preis dafür, dass KEINE Vollseite den
        # Katalogstand verschweigt (WB-379); eine Ausnahme wäre genau die
        # Sorte Sonderfall, die später niemand mehr erklärt.
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "rolle.html",
                                             _rahmen(request, c))
        finally:
            c.close()

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
        """Was jede Vollseite braucht: Rolle, Korbzahl — und der Katalogstand.

        Das Hinweisband steht seit jeher im Grundgerüst (`basis.html`), wurde
        aber nur von `/katalog` und `/status` gefüttert. Ausgerechnet `/pick`
        liest im Laden Namen, Gebinde und Bilder aus demselben Katalog und
        schrieb „nicht mehr im Katalog" auf Daten, deren Alter die Seite
        verschwieg. Hier gehört es hin und nirgends sonst: eine Stelle, alle
        Vollseiten, und keine Seite kann es beim nächsten Umbau vergessen.

        Eine zusätzliche Abfrage je Vollseite (`scrape_run ORDER BY id DESC
        LIMIT 1`) — dieselbe, die `/katalog` bisher allein bezahlt hat.
        """
        return {"rolle": rolle_aus(request),
                "korb_anzahl": orders.korb_anzahl(c),
                "hinweis": katalog_hinweis(c)}

    # ----------------------------------------------------------------------
    # Zwei Seiten, die es vor WB-376 nicht gab: die Adresse ins Leere und die
    # Rückfrage vor etwas Endgültigem.

    #: Der Rückweg, wenn kein näherer bekannt ist.
    WEGE_ALLGEMEIN = [{"url": "/katalog", "text": "Zum Katalog"},
                      {"url": "/warenkorb", "text": "Zum Korb"}]
    _WEGE_REZEPT = [{"url": "/rezepte", "text": "Alle Rezepte"},
                    {"url": "/katalog", "text": "Zum Katalog"}]
    _WEGE_BON = [{"url": "/bons", "text": "Alle Bons"},
                 {"url": "/katalog", "text": "Zum Katalog"}]

    def _nicht_gefunden(request: Request, satz: str, wege=None):
        """Eine 404 mit Kopf, Navigation und Rückweg — für alle drei Wege.

        Vorher gab es drei Antworten auf dieselbe Frage: `/pick/99` und
        `/rezepte/99` lieferten 0 Bytes, `/bons/gibtesnicht.pdf` eine 200 mit
        einer Seite über eine Datei, die es nicht gibt. Diese Funktion ist die
        eine Antwort; `wege` sagt, wohin zurück.

        Sie öffnet eine eigene Verbindung: sie wird auch aus dem
        Ausnahmebehandler heraus gerufen, wo keine offen ist. Zwei lesende
        sqlite-Verbindungen nebeneinander sind kein Problem, und der Preis
        einer 404 ist der falsche Ort zum Sparen.
        """
        c = con()
        try:
            return vorlagen.TemplateResponse(
                request, "nicht_gefunden.html",
                {**_rahmen(request, c), "satz": satz,
                 "wege": wege or WEGE_ALLGEMEIN},
                status_code=404)
        finally:
            c.close()

    @app.exception_handler(404)
    def _kein_weg(request: Request, exc):
        """Jede unbekannte Adresse bekommt dieselbe Seite.

        Nicht nur die drei aus dem Ticket: ein vertippter Link im Tailnet
        landete sonst wieder auf einem weissen Blatt. Routen, die absichtlich
        ein nacktes `Response(404)` zurückgeben — die HTMX-Bruchstücke —
        laufen hier NICHT durch: dieser Behandler sieht nur geworfene
        Ausnahmen, und ein Bruchstück, in das eine ganze Seite getauscht
        würde, wäre schlimmer als gar keine Antwort.
        """
        return _nicht_gefunden(
            request, "Diese Adresse gibt es im Zettel-Shop nicht.")

    def _bestaetigen(request: Request, titel: str, frage: str, aktion: str,
                     knopf: str, zurueck: str, verlust=None,
                     bleibt: str | None = None):
        """Der zweite Schritt vor etwas, das keinen dritten hat."""
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "bestaetigen.html", {
                **_rahmen(request, c), "titel": titel, "frage": frage,
                "aktion": aktion, "knopf": knopf, "zurueck": zurueck,
                "verlust": verlust or [], "bleibt": bleibt})
        finally:
            c.close()

    def _korb_kontext(c: sqlite3.Connection, fehler: str | None = None,
                      weg: dict | None = None,
                      meldung: str | None = None) -> dict:
        """Alles, was `_korb.html` braucht — für Vollseite und HTMX-Bruchstück.

        Eine Funktion für beide Wege, aus demselben Grund wie bei der
        Trefferliste in WB-323: sonst entwickelt sich das Bruchstück von der
        ersten Ansicht weg, und niemand merkt es.

        `weg` ist die gerade entfernte Zeile (WB-376) — nicht als Fehler,
        sondern als Rückweg: der Korb zeigt darüber, was verschwunden ist, und
        einen Knopf, der es zurückholt.
        """
        posten = _posten_mit_bild(orders.inhalt(c), app.state.image_dir)
        return {"posten": posten,
                "summe": orders.summe(posten),
                "stores": db.STORES,
                "laden_titel": orders.LADEN_TITEL,
                "korb_anzahl": orders.korb_anzahl(c),
                "fehler": fehler,
                "weg": weg,
                "meldung": meldung}

    def _korb_zahlen(c: sqlite3.Connection) -> dict:
        """Nur die Korbzahl — was der Chat vom Korb noch wissen muss (WB-382).

        Eine Abfrage statt `orders.inhalt()` samt Bildern: seit der Korb eine
        eigene Seite ist, trägt eine Chat-Antwort ihn nicht mehr mit. Sie
        trägt die Zahl, und aus der Zahl werden zwei Dinge — die Ziffer im
        Kopf und die Korbbrücke unter dem Chat.
        """
        return {"korb_anzahl": orders.korb_anzahl(c)}

    def _korb_antwort(request: Request, c: sqlite3.Connection,
                      fehler: str | None = None, weg: dict | None = None,
                      meldung: str | None = None):
        """HTMX bekommt den Korb, ein Formular ohne JavaScript die ganze Seite.

        `korb_oob` schaltet die Zahl im Kopf dazu (WB-372). Nur hier, wo der
        Korb die ganze Antwort ist: eingebettet trägt sie der Aufrufer
        (`_nachtrag.html`), und zweimal wäre sie ein Tausch im Tausch.
        """
        if ist_htmx(request):
            return vorlagen.TemplateResponse(
                request, "_korb.html",
                {**_korb_kontext(c, fehler, weg, meldung), "korb_oob": True})
        if fehler or weg or meldung:
            # Mit einer Weiterleitung ginge die Begründung verloren, und die
            # Nutzerin sähe nur, dass nichts passiert ist. Für den Rückweg
            # (WB-376) gilt dasselbe doppelt: ohne JavaScript wäre er nach der
            # Weiterleitung gar nicht mehr da.
            return vorlagen.TemplateResponse(
                request, "warenkorb.html",
                {**_rahmen(request, c), **_korb_kontext(c, fehler, weg, meldung)})
        return RedirectResponse("/warenkorb", status_code=303)

    def _zeile_im_korb(c: sqlite3.Connection, item_id: int) -> dict | None:
        """Die Korbzeile, wie sie JETZT ist — gebraucht, bevor sie weg ist.

        Der Rückweg braucht den Namen für die Meldung und die Zahlen für das
        Wiedereinlegen; nach dem `DELETE` sind beide nicht mehr zu haben.
        """
        return next((z for z in orders.inhalt(c) if z["id"] == item_id), None)

    @app.get("/katalog")
    def katalog(request: Request, q: str = "",
                l1: str | None = None, l2: str | None = None,
                l3: str | None = None):
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "katalog.html", {
                **_rahmen(request, c),
                **_liste(c, q, l1, l2, l3),
                "baum": categories.tree(c),
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
                **_liste(c, q, l1, l2, l3),
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
        """Der Korb — Zeilen, Mengen, Laden, Abschicken. Und sonst nichts.

        Bis WB-382 hing der ganze Chatverlauf an derselben Seite (Spec 9 in
        ihrer alten Fassung). Sie war damit die grösste des Shops, und die
        beiden Dinge drängelten sich um denselben Platz. Der Chat hat jetzt
        einen eigenen Ort (`/chat`); am Datenmodell hat sich dabei nichts
        geändert — der Verlauf hängt weiter am `draft`.
        """
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "warenkorb.html", {
                **_rahmen(request, c), **_korb_kontext(c)})
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
        """„+" und „−". Unter 1 nimmt „−" die Zeile aus dem Korb — mit Rückweg.

        Das war die vierte stille Zerstörung aus WB-376: bei Menge 1 löschte
        „−" die Zeile, ohne zu fragen und ohne Spur. Der Knopf soll das
        weiterhin können — ein „−", das bei 1 nichts tut, ist ein kaputter
        Knopf —, aber die Zeile ist danach zurückzuholen.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            menge = zahl(werte.get("qty"), 1)
            # VOR der Änderung gelesen: ab qty < 1 gibt es die Zeile nicht
            # mehr, und mit ihr wäre der Rückweg weg.
            vorher = _zeile_im_korb(c, item_id) if menge < 1 else None
            try:
                orders.menge_setzen(c, item_id, menge)
            except orders.UngueltigerPosten as e:
                fehler, vorher = str(e), None
            return _korb_antwort(request, c, fehler, weg=vorher)
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
        """Das „×" am Posten — und seit WB-376 mit Rückweg statt Rückfrage.

        Rückweg und nicht Rückfrage, weil der Korb die Stelle ist, an die
        WB-361 für die Rücknahme ausdrücklich verweist („dort steht ein
        Löschknopf"): eine Zeile im Korb ist eine Absicht und kein Ergebnis,
        und ein Zwischenschritt vor jedem Wegnehmen wäre auf dem Telefon der
        doppelte Aufwand für die häufigste Geste des Shops.
        """
        c = con()
        try:
            fehler = None
            vorher = _zeile_im_korb(c, item_id)
            try:
                orders.entfernen(c, item_id)
            except orders.UngueltigerPosten as e:
                fehler, vorher = str(e), None
            return _korb_antwort(request, c, fehler, weg=vorher)
        finally:
            c.close()

    @app.post("/warenkorb/wiederherstellen")
    async def posten_wiederherstellen(request: Request):
        """Der Rückweg (WB-376): die entfernte Zeile noch einmal hinlegen.

        Die Zeile reist im Formular mit und nicht in einem Serverzustand: der
        Shop läuft auf zwei Telefonen gleichzeitig, und ein „zuletzt
        gelöscht"-Fach im Prozess wäre eines für beide zusammen — die eine
        nähme zurück, was die andere weggenommen hat. Im Formular gehört der
        Rückweg zu der Seite, auf der gelöscht wurde, und überlebt auch einen
        Neustart des Web-Prozesses.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = meldung = None
            try:
                orders.wieder_einlegen(
                    c, product_id=werte.get("product_id") or None,
                    free_text=werte.get("free_text") or None,
                    qty=zahl(werte.get("qty"), 1),
                    store=werte.get("store"),
                    need_amount=werte.get("need_amount"),
                    need_unit=werte.get("need_unit") or None,
                    hand_qty=zahl(werte.get("hand_qty"), 0))
            except orders.UngueltigerPosten as e:
                fehler = str(e)
            else:
                meldung = f"{werte.get('name') or 'Die Zeile'} liegt wieder im Korb."
            return _korb_antwort(request, c, fehler, meldung=meldung)
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
            # `?fertig=` trägt die Quittung (UI-Review 2026-09-01, Fund 7):
            # nach dem Abschicken sah die Seite aus wie vorher plus eine
            # Karte, und ein Fremder wusste nicht, ob etwas passiert war.
            # Ein Query-Parameter und keine Sitzung — der Shop hat keine.
            #
            # Kein `#b<id>` dahinter: der Browser zieht das Ziel eines Ankers
            # an den OBEREN Rand, die Quittung steht aber über der Liste —
            # auf einer Seite, die scrollt, sprang der Anker also über genau
            # die Meldung hinweg, für die er gedacht war. Die Quittung nennt
            # die Bestellung ohnehin beim Namen; die ID bleibt in der URL.
            ziel = f"/bestellungen?fertig={bestellung['id']}"
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
    # Der Chat hat seit WB-382 einen eigenen Ort (`/chat`) und hängt trotzdem
    # weiter am `draft` — „eigener Ort" heisst eine eigene ANSICHT, nicht ein
    # eigener Besitzer. Drei Dinge sind an dieser Stelle wichtiger als sie
    # aussehen:
    #
    # * **Das Rendern der Seite fragt die vLLM-Box NICHT.** Der Zustand wird
    #   nachgeladen (`/chat/zustand`). Sonst hinge jeder Blick in den Chat bis
    #   zu drei Sekunden am health-Timeout — und jeder Testlauf ginge ins
    #   Netz.
    # * **Ein Vorschlag ist noch kein Posten.** Erst „Ja" legt ein.
    # * **Und der Korb steht nicht mehr daneben.** Bis WB-382 trug jede
    #   Antwort auf eine Entscheidung den ganzen Korb mit, weil er auf
    #   derselben Seite stand. Jetzt trägt sie die KORBBRÜCKE („5 im Korb —
    #   ansehen") und einen Satz an der Zeile selbst. Das ist billiger und
    #   näher am Daumen: die Knöpfe stehen mitten in einer Liste von 150
    #   Zeilen, und was am Seitenkopf passiert, ist beim Tippen aus dem Bild.
    #
    # Und seit WB-372 gibt es DREI Antwortgrössen statt einer. Sie sind keine
    # Optimierung nebenbei, sondern die Sache selbst: ein Tipp auf „Ja" trug
    # vorher den kompletten Verlauf (gemessen 259.941 Bytes bei 17 Zügen), und
    # genau diese Tipps macht man in Serie durch eine Liste von 150 Zeilen, am
    # Telefon über Mobilfunk.
    #
    #   `_entscheidung_antwort`  eine ZEILE    Ja, Nein, rückgängig
    #   `_zug_antwort`           ein ZUG       alles, was Zeilen ANLEGT
    #                                          (Korrektur, Freitext) oder
    #                                          mehrere auf einmal ändert
    #                                          (Sammelknopf, Rezeptentwurf)
    #   `_chat_antwort`          der VERLAUF   ein neuer Zug, Sorten, leeren
    #
    # Die Regel dahinter ist kurz: **so gross wie das, was sich ändert, und
    # keinen Zug grösser.** Was sich ausserhalb des getauschten Stücks ändert,
    # kommt out-of-band (`_nachtrag.html`) — nicht dadurch, dass
    # sicherheitshalber alles neu gerendert wird.

    def _zug_fuellen(c: sqlite3.Connection, zeile: dict) -> dict:
        """Ergänzt eine Chatzeile um alles, was `_zug.html` an ihr zeigt.

        Eine Funktion für den Verlauf UND für den einzeln getauschten Zug
        (WB-372) — aus demselben Grund wie bei der Trefferliste in WB-323:
        sonst entwickelt sich das Bruchstück von der Vollansicht weg, und
        niemand merkt es.
        """
        _posten_mit_bild(zeile["vorschlaege"], app.state.image_dir)
        for v in zeile["vorschlaege"]:
            v["alternativen"] = _alternativen(c, v)
        # Die Sorten einer Auffächerung (WB-368). Sie stehen an der
        # Antwortzeile und bleiben im Verlauf stehen: wer erst Salami
        # gewählt hat und zwei Sätze später doch noch Kochschinken will,
        # findet die Liste noch vor.
        zeile["faecher"] = oberbegriffe.zu_nachricht(c, zeile["id"])
        # Der Rezeptentwurf (WB-337). Er bekommt die schon geholte
        # Vorschlagsliste gereicht statt sie ein zweites Mal zu holen —
        # sonst hätte er andere Wörterbücher als die Liste darüber, und
        # an denen fehlten die Bilder.
        zeile["entwurf"] = entwuerfe.zu_nachricht(c, zeile["id"],
                                                  zeile["vorschlaege"])
        # Das vorgeschlagene Rezept selbst (WB-383) — nicht nur ein Satz
        # darüber. Dieselbe Übergabe der Vorschlagsliste wie beim Entwurf:
        # daraus entsteht die Deckungszahl („23 Zutaten, 8 davon nicht auf
        # dem Zettel") ohne eine zweite Abfrage je Zug.
        zeile["rezepte"] = zugrezepte.zum_zug(c, zeile["id"],
                                              zeile["vorschlaege"])
        return zeile

    def _warten_kontext(satz: str, zustand) -> dict:
        """Was der Wartezähler braucht (WB-414).

        Leer, wenn niemand wartet — die Vorlage prüft auf `warten_satz`.
        """
        if not satz or zustand is None or zustand.bedient:
            return {}
        return {"warten_satz": satz,
                "warten_meldung": _nicht_verfuegbar_kurz(zustand),
                "warten_rest": (round(zustand.rest_s)
                                if zustand.rest_s else None),
                "warten_ueberfaellig": zustand.ueberfaellig,
                "warten_grund": zustand.grund}

    def _zuege_umdrehen(verlauf: list[dict]) -> list[dict]:
        """Der jüngste Zug nach oben (WB-417).

        „Das Rezept soll auch von oben nachrücken." Das Eingabefeld steht seit
        WB-416 im klebenden Kopf; eine Antwort, die danach am unteren Ende
        einer 5.000 px langen Seite erscheint, ist genau der Weg, den jenes
        Ticket abgeschafft hat.

        **Gedreht werden ZÜGE und nicht Zeilen.** Innerhalb eines Zugs bleibt
        die Frage über ihrer Antwort — sie ist der Grund, aus dem die Antwort
        dasteht. Ein Zug beginnt an jeder Zeile der Nutzerin; was davor steht
        (ein Verlauf, dessen Frage jemand gelöscht hat), bleibt eine Gruppe
        für sich und rutscht als älteste nach unten.

        **Und gedreht wird HIER und nicht in `verlauf()`.** Die Reihenfolge in
        der Datenbank ist eine Tatsache — `ORDER BY coalesce(ersetzt, id), id`
        hält einen ersetzten Zug an seiner Stelle (WB-403), und daran hängt
        auch, welcher Zug welchen abgelöst hat. Die Reihenfolge auf dem Schirm
        ist eine Entscheidung, und sie gehört dorthin, wo der Schirm gebaut
        wird.
        """
        zuege: list[list[dict]] = []
        for zeile in verlauf:
            if zeile["role"] == vorschlagsliste.ROLLE_NUTZERIN or not zuege:
                zuege.append([])
            zuege[-1].append(zeile)
        return [zeile for zug in reversed(zuege) for zeile in zug]

    def _chat_kontext(c: sqlite3.Connection, fehler: str | None = None,
                      zustand=None, satz: str = "",
                      aufklappen: int | None = None, alles: bool = False,
                      leeren_fragt: bool = False, geleert=None,
                      gewechselt: str | None = None,
                      warten: str = "") -> dict:
        """Alles, was `_chat.html` braucht — für Vollseite und Bruchstück.

        **Gerendert wird nur der Schwanz des Verlaufs** (WB-372): die letzten
        `VERLAUF_ZUEGE` Züge. `alles=True` hebt die Grenze für diese eine
        Antwort auf — der Weg zurück zu den älteren Zügen. Es ist eine
        Anzeigegrenze; gelöscht oder verändert wird dabei nichts.

        Die Grenze spart nicht nur Bytes, sondern auch Abfragen: an jeder
        Chatzeile hängen die Alternativen jedes Vorschlags, die Sorten und der
        Rezeptentwurf. Über 34 Chatzeilen war das der eigentliche Aufwand
        dieser Ansicht.
        """
        # Nur nachsehen, nicht anlegen: ein Blick in den Warenkorb darf keine
        # Bestellung erzeugen. Angelegt wird er erst im Chat-Zug selbst.
        korb_id = orders.warenkorb_id(c)
        ab = (None if korb_id is None or alles else
              vorschlagsliste.verlauf_ab(c, korb_id, VERLAUF_ZUEGE))
        verlauf = vorschlagsliste.verlauf(c, korb_id, ab=ab) if korb_id else []
        for zeile in verlauf:
            _zug_fuellen(c, zeile)
        verlauf = _zuege_umdrehen(verlauf)
        return {"verlauf": verlauf, "chat_fehler": fehler,
                "chat_zustand": zustand, "satz": satz,
                "aufklappen": aufklappen,
                # Was eingeklappt ist — mit Zahlen, sonst sagt der Knopf
                # nicht, ob dort noch Arbeit wartet.
                "aeltere": (vorschlagsliste.umfang(c, korb_id, bis=ab)
                            if ab else None),
                # Nur für die Rückfrage vor dem Leeren, und nur dann: zwei
                # Zählabfragen bei jedem Blick in den Korb wären umsonst.
                "umfang": (vorschlagsliste.umfang(c, korb_id)
                           if leeren_fragt and korb_id else None),
                "leeren_fragt": leeren_fragt, "verlauf_geleert": geleert,
                # Das gewechselte Rezept (WB-387). Eine Rückmeldung an dem
                # Tipp, der sie ausgelöst hat — und die zweite Hälfte der
                # Zusage über den Korb: die Oberfläche sagt vor dem Wechsel,
                # dass nichts herausfliegt, und danach, dass nichts
                # herausgeflogen ist.
                "gewechselt": gewechselt,
                # **Wer vorwärmen darf** (WB-408): genau der jüngste Zug, und
                # nur er. Die Karten darüber stehen im Verlauf, nicht vor
                # Augen — für sie zu rechnen hiesse, bei jedem Blick in den
                # Chat die halbe Rezeptliste durch das Modell zu schicken.
                "vorwaermen": max((z["id"] for z in verlauf
                                   if z["role"] == vorschlagsliste.ROLLE_AGENT),
                                  default=None),
                **_warten_kontext(warten, zustand)}

    def _nicht_verfuegbar(e: chatmodul.ChatNichtVerfuegbar) -> str:
        """Was am Eingabefeld stehen soll, wenn die Box nicht bedient (WB-378).

        Bis hierher wurde in diesem Fall nur `zustand` gesetzt: das Band mit
        dem Grund steht ÜBER dem ganzen Verlauf, das Eingabefeld darunter. Auf
        dem Telefon heisst das — sie tippt „Fragen", und sichtbar passiert gar
        nichts. Der technische Grund bleibt am Band; hier steht der Satz, den
        sie an ihrem Knopf braucht.

        `str(e)` wäre der Grund selbst („wake-vllm endete mit Code 1 …"). Er
        ist richtig und für diese Stelle unbrauchbar.
        """
        return _nicht_verfuegbar_kurz(e.zustand)

    def _nicht_verfuegbar_kurz(zustand) -> str:
        """Derselbe Satz für die Fehlerzeile und für den Wartezähler.

        **Er sagt seit WB-414 nicht mehr „gleich noch einmal tippen".** Der
        Satz war schon getippt und schon abgeschickt; ihn ein zweites Mal
        abzuschicken ist Arbeit, die der Shop selbst tun kann — und seither
        tut er sie (`/chat/warten`).
        """
        if zustand.zustand == wake.WACHT_AUF:
            return ("Das Modell wacht auf. Dein Satz ist gemerkt und läuft "
                    "von selbst, sobald es antwortet.")
        return ("Das Modell antwortet gerade nicht. Dein Satz ist gemerkt und "
                "läuft von selbst, sobald es wieder da ist — ein "
                "gespeichertes Rezept beim Namen zu nennen, geht auch ohne "
                "Modell.")

    def _alternativen(c: sqlite3.Connection, v: dict) -> list[dict]:
        """Die aufgehobenen Kandidaten einer verworfenen Zeile (WB-359).

        **Keine neue Suche** — sie stehen seit dem Chat-Zug in
        `chat_kandidat`. Geholt werden sie nur für die Zeilen, an denen sie
        auch angeboten werden: verworfen und noch nicht korrigiert. Für jede
        Zeile des ganzen Verlaufs wäre es eine Abfrage je Vorschlag, und der
        Verlauf wächst mit jedem Satz.
        """
        if v["decision"] != vorschlagsliste.VERWORFEN or v["korrektur"]:
            return []
        if not v["n_alternativen"]:
            return []
        return _mit_bild(vorschlagsliste.alternativen(c, v["id"]),
                         app.state.image_dir)

    def _chat_antwort(request: Request, c: sqlite3.Connection,
                      fehler: str | None = None, zustand=None,
                      satz: str = "", aufklappen: int | None = None,
                      alles: bool = False, leeren_fragt: bool = False,
                      geleert=None, gewechselt: str | None = None,
                      warten: str = ""):
        """HTMX bekommt den Chat, ein Formular ohne JavaScript die Seite.

        Die GRÖSSTE der drei Antworten (WB-372) und seit dem Ticket die
        seltenste: sie bleibt dem vorbehalten, was den Verlauf selbst ändert —
        ein neuer Zug, eine Sortenauswahl, das Leeren. Ein „Ja" nimmt
        `_entscheidung_antwort`, alles Zeilenanlegende `_zug_antwort`.

        `_korb_zahlen` statt `_korb_kontext` seit WB-382: der Korb steht nicht
        mehr auf dieser Seite, also braucht die Antwort auch nicht mehr seine
        Zeilen — nur die Zahl für die Brücke und den Kopf.
        """
        kontext = {**_chat_kontext(c, fehler, zustand, satz, aufklappen,
                                   alles, leeren_fragt, geleert, gewechselt,
                                   warten),
                   **_korb_zahlen(c)}
        if ist_htmx(request):
            return vorlagen.TemplateResponse(request, "_chat_antwort.html",
                                             kontext)
        return vorlagen.TemplateResponse(
            request, "chat.html", {**_rahmen(request, c), **kontext})

    def _teilantwort(request: Request, c: sqlite3.Connection, mid: int | None,
                     vorlage: str, fehler: str | None = None,
                     aufklappen: int | None = None, sid: int | None = None,
                     gerade: int | None = None):
        """Ein Zug oder eine Zeile statt des ganzen Verlaufs (WB-372).

        **Ohne HTMX gibt es hier nichts zu tauschen**, dann geht die ganze
        Seite zurück — derselbe Ausgang wie beim Korb, und der Grund, warum
        der Shop weiter ohne JavaScript bedienbar bleibt.

        Findet sich der Zug oder die Zeile nicht mehr, fällt die Antwort
        ebenfalls auf den ganzen Chat zurück. Das ist kein Sonderfall, den man
        wegdiskutieren kann: zwei Telefone bedienen denselben Korb, und wenn
        der andere den Verlauf inzwischen geleert hat, gäbe es sonst eine
        Antwort, die auf ein Element zielt, das es nicht mehr gibt — der Tipp
        sähe aus, als hätte er nichts getan.

        `gerade` ist die Zeile, die dieser Tipp EBEN entschieden hat (WB-382).
        Nur an ihr steht der Satz „liegt jetzt im Korb" — im gerenderten
        Verlauf steht er nirgends, denn dort ist nichts gerade passiert. Er
        ist Rückmeldung und keine Zustandsanzeige; die trägt die Marke.
        """
        zeile = vorschlagsliste.zug(c, mid) if mid is not None else None
        v = None
        if zeile is not None:
            _zug_fuellen(c, zeile)
            if sid is not None:
                v = next((x for x in zeile["vorschlaege"]
                          if x["id"] == sid), None)
        if not ist_htmx(request) or zeile is None or (sid is not None
                                                      and v is None):
            return _chat_antwort(request, c, fehler=fehler,
                                 aufklappen=aufklappen)
        return vorlagen.TemplateResponse(
            request, vorlage,
            {"m": zeile, "v": v, "aufklappen": aufklappen, "gerade": gerade,
             "chat_fehler": fehler, **_korb_zahlen(c)})

    def _zug_von(c: sqlite3.Connection, sid: int) -> int | None:
        """Zu welchem Zug eine Vorschlagszeile gehört. `None`, wenn es sie
        nicht (mehr) gibt — dann geht der ganze Chat zurück."""
        try:
            return vorschlagsliste.eine(c, sid)["chat_message_id"]
        except vorschlagsliste.VorschlagFehler:
            return None

    def _entscheidung_antwort(request: Request, c: sqlite3.Connection,
                              sid: int, fehler: str | None = None,
                              aufklappen: int | None = None,
                              gerade: int | None = None):
        """Die Antwort auf ein „Ja"/„Nein": eine ZEILE plus die Nachträge.

        **Ausser an einer Korrekturzeile** — dort geht der ganze Zug zurück.
        Eine Korrektur zählt nur, solange sie entschieden ist
        (`vorschlaege.liste`), und mit ihrer Rücknahme kommt an der Zeile, die
        sie korrigiert, die Alternativenliste samt Rückweg wieder hervor. Ein
        Tipp, der eine ANDERE Zeile mitverändert, lässt sich nicht als diese
        eine Zeile beantworten; die Regel „so gross wie das, was sich ändert"
        heisst hier eben: ein Zug.
        """
        try:
            v = vorschlagsliste.eine(c, sid)
        except vorschlagsliste.VorschlagFehler:
            return _chat_antwort(request, c, fehler=fehler,
                                 aufklappen=aufklappen)
        if v["ist_korrektur"]:
            return _teilantwort(request, c, v["chat_message_id"],
                                "_zugantwort.html", fehler=fehler,
                                aufklappen=aufklappen, gerade=gerade)
        return _teilantwort(request, c, v["chat_message_id"],
                            "_entscheidung.html", fehler=fehler,
                            aufklappen=aufklappen, sid=sid, gerade=gerade)

    def _zug_antwort(request: Request, c: sqlite3.Connection, mid: int,
                     fehler: str | None = None):
        """Die Antwort auf alles, was Zeilen ANLEGT oder mehrere ändert."""
        return _teilantwort(request, c, mid, "_zugantwort.html", fehler=fehler)

    @app.get("/chat")
    def chat_seite(request: Request, verlauf: str = ""):
        """Der eigene Ort des Chats (WB-382) — und sein eigenes Bruchstück.

        EINE Adresse für beides, weil es dieselbe Sache in zwei Grössen ist:
        ohne HTMX die ganze Seite, mit HTMX nur `#chat`. Das ist der Weg
        zurück zu den eingeklappten Zügen aus WB-372 (`?verlauf=alles` hebt
        die Anzeigegrenze für diese eine Antwort auf) — bis dahin brauchte er
        zwei Adressen, weil die Vollseite `/warenkorb` hiess und der Chat kein
        eigenes Ziel hatte. Der Knopf am eingeklappten Teil zeigt jetzt mit
        `action` und `hx-get` auf dieselbe Stelle.
        """
        c = con()
        try:
            kontext = _chat_kontext(c, alles=verlauf == "alles")
            if ist_htmx(request):
                return vorlagen.TemplateResponse(request, "_chat.html", kontext)
            # `_rahmen` trägt die Korbzahl schon — die Brücke rechnet nicht
            # zum zweiten Mal nach, was oben im Kopf ohnehin steht.
            return vorlagen.TemplateResponse(request, "chat.html", {
                **_rahmen(request, c), **kontext})
        finally:
            c.close()

    @app.post("/chat/leeren")
    async def chat_leeren(request: Request):
        """Den Verlauf loswerden, ohne eine Bestellung abzuschicken (WB-372).

        Drei Ausgänge an einer Adresse, weil die Rückfrage kein eigener Ort
        ist, sondern ein Zustand derselben Handlung:

        * **ohne `ja`** — die Rückfrage. Sie nennt, was verloren geht, statt
          bloss „wirklich?" zu fragen: an den Vorschlägen hängen die
          Entscheidungen, aus denen beim Abschicken die Eval-Labels werden
          (Spec 8.1, WB-329).
        * **`ja=0`** — Abbruch, und nichts ist passiert.
        * **`ja=1`** — gelöscht, mit einer Meldung, die die Zahlen nennt.

        Serverseitig und nicht per `hx-confirm`: eine Rückfrage, die ohne
        JavaScript ausfällt, fehlt genau dann, wenn sie gebraucht wird.

        **Der Korb wird nicht angefasst** — siehe `vorschlaege.leeren()`.
        """
        werte = await eingaben(request)
        ja = werte.get("ja", "")
        c = con()
        try:
            korb_id = orders.warenkorb_id(c)
            if korb_id is None or ja == "0":
                # Kein Korb heisst kein Verlauf: nichts zu fragen, nichts zu
                # löschen. Dieselbe Antwort wie beim Abbruch.
                return _chat_antwort(request, c)
            if ja == "1":
                return _chat_antwort(request, c,
                                     geleert=vorschlagsliste.leeren(c, korb_id))
            return _chat_antwort(request, c, leeren_fragt=True)
        finally:
            c.close()

    @app.get("/chat/zustand")
    def chat_zustand(request: Request):
        """Bedient die Box? Nachgeladen, damit der Korb sofort da ist.

        Dieser Aufruf ist es, der `wake-vllm` anstösst (Spec 6) — nicht
        blockierend, und die Antwort trägt den Zähler „noch ~N s".
        """
        return vorlagen.TemplateResponse(request, "_chatzustand.html",
                                         {"chat_zustand": app.state.chat.zustand()})

    @app.post("/chat")
    def chat_senden(request: Request,
                    werte: Formular = Depends(formular)):
        """Ein Chat-Zug. Legt NICHTS in den Korb — nur Vorschläge (Spec 6)."""
        satz = werte.get("satz", "")
        c = con()
        try:
            try:
                app.state.chat.turn(c, satz)
            except chatmodul.ChatNichtVerfuegbar as e:
                # Nur der Chat ist betroffen (Spec 11). Der Satz bleibt im
                # Feld stehen, damit er nicht noch einmal getippt werden muss —
                # und die Meldung steht daneben (WB-378), nicht bloss oben am
                # Band.
                return _chat_antwort(request, c, zustand=e.zustand, satz=satz,
                                     fehler=_nicht_verfuegbar(e),
                                     warten=satz)
            except chatmodul.ChatFehler as e:
                return _chat_antwort(request, c, fehler=str(e), satz=satz)
            return _chat_antwort(request, c)
        finally:
            c.close()

    @app.post("/chat/warten")
    def chat_warten(request: Request,
                    werte: Formular = Depends(formular)):
        """Derselbe Satz noch einmal — von selbst, bis die Box antwortet.

        „Sorg dafür dass der Timer bis die Antwort kommt interaktiv ist …
        und dass man danach nicht nochmal klicken muss." (WB-414.)

        **Ein Eingang für beides, weil es dieselbe Frage ist.** Bedient die
        Box nicht, kommt der Wartekasten zurück (rund 700 Bytes, mit neuem
        Zählerstand) und tauscht sich selbst. Bedient sie, läuft der Zug — und
        die Antwort ist der ganze Chat, also muss sie woanders hin als der
        Kasten. Das sagt `HX-Retarget`, nicht eine zweite Adresse.

        **Der ganze Chat je Wartesekunde wäre das falsche Paket.** WB-372 hat
        ihn von 215 KB auf 45 KB gedrückt; dreissig Runden Warten daraus
        wären 1,3 MB auf einem Telefon.
        """
        satz = werte.get("satz", "")
        c = con()
        try:
            if not satz.strip():
                return _chat_antwort(request, c,
                                     fehler="Schreib hin, was du brauchst — "
                                            "leer geht nicht.")
            try:
                app.state.chat.turn(c, satz)
            except chatmodul.ChatNichtVerfuegbar as e:
                if not ist_htmx(request):
                    # Ohne JavaScript gibt es nichts, was sich selbst
                    # tauschen könnte: dann die Vollseite mit dem Kasten
                    # darin, und der Knopf darin ist der Weg.
                    return _chat_antwort(request, c, zustand=e.zustand,
                                         satz=satz,
                                         fehler=_nicht_verfuegbar(e),
                                         warten=satz)
                return vorlagen.TemplateResponse(
                    request, "_chatwarten.html",
                    _warten_kontext(satz, e.zustand))
            except chatmodul.ChatFehler as e:
                return _mit_ziel(_chat_antwort(request, c, fehler=str(e),
                                               satz=satz), request)
            return _mit_ziel(_chat_antwort(request, c), request)
        finally:
            c.close()

    def _mit_ziel(antwort, request: Request):
        """Die Antwort gehört an den ganzen Chat und nicht an den Kasten.

        Der Wartekasten tauscht sich selbst (`hx-target="this"`), weil er das
        dreissigmal tut. Der eine geglückte Zug tauscht mehr — und sagt es
        per Kopfzeile, statt dafür eine zweite Adresse zu brauchen.
        """
        if ist_htmx(request):
            antwort.headers["HX-Retarget"] = "#chat"
            # `show:` wie am Eingabefeld (WB-417): der jüngste Zug steht
            # ganz oben, direkt unter dem klebenden Kopf.
            antwort.headers["HX-Reswap"] = "outerHTML show:window:top"
        return antwort

    @app.post("/chat/{mid}/sorten")
    def chat_sorten(request: Request, mid: int,
                    werte: Formular = Depends(formular)):
        """Die angekreuzten Sorten einer Auffächerung (WB-368).

        Zwei Ausgänge, und beide führen in den NORMALEN Ablauf:

        * **Sorten angekreuzt** — sie werden zum Satz des nächsten Zugs, und
          die Kandidaten kommen aus ihren Kategorien. Danach ist alles wie
          immer: Stufe 3 wählt, Ja/Nein entscheidet, ein „Nein" klappt die
          Alternativen auf (WB-359).
        * **nichts angekreuzt** — dann ist der Oberbegriff übersprungen und es
          wird direkt nach dem gesucht, was getippt wurde. **Ein Oberbegriff
          darf keine Sackgasse sein**; wer die Rückfrage nicht will, kommt mit
          einem Tipp an ihr vorbei.

        In beiden Fällen wird NICHT noch einmal aufgefächert — dieselbe Frage
        zweimal wäre eine Schleife statt einer Auswahl.
        """
        gewuenscht = werte.alle("sorte")
        c = con()
        try:
            faecher = oberbegriffe.zu_nachricht(c, mid)
            if faecher is None:
                return _chat_antwort(
                    request, c, fehler="Zu dieser Antwort gibt es keine "
                                       "Sortenauswahl (mehr).")
            # Gewählt werden kann nur, was angeboten wurde — dieselbe Regel
            # wie bei den Produkt-IDs in `plan.choose`, nur für das Formular.
            gewaehlt = oberbegriffe.gewaehlte(c, mid, gewuenscht)
            satz = ", ".join(gewaehlt) if gewaehlt else faecher["wort"]
            try:
                app.state.chat.turn(
                    c, satz, auffaechern=False,
                    aus_sorten=((faecher["kategorie"], gewaehlt)
                                if gewaehlt else None))
            except chatmodul.ChatNichtVerfuegbar as e:
                return _chat_antwort(request, c, zustand=e.zustand,
                                     fehler=_nicht_verfuegbar(e))
            except chatmodul.ChatFehler as e:
                return _chat_antwort(request, c, fehler=str(e))
            return _chat_antwort(request, c)
        finally:
            c.close()

    @app.post("/chat/{mid}/rezept")
    def chat_rezept(request: Request, mid: int,
                    werte: Formular = Depends(formular)):
        """Ein anderes Rezept zu demselben Gericht (WB-387).

        Chefkoch liefert zwölf Rezepte je Suche; eines wurde vorgeschlagen,
        elf standen bis zu diesem Ticket nirgends. Gemessen an „Lasagne"
        wählt die Gewichtung eine vegetarische Spinatlasagne — sie ist nicht
        kaputt, aber „am besten bewertet" ist nicht „was ich gemeint habe",
        und das löst kein Sortierschlüssel. **Deshalb wählt hier ein Mensch.**

        Der Tipp führt in den NORMALEN Ablauf und nicht in einen zweiten
        daneben (dieselbe Regel wie bei den Sorten in WB-368): das Gericht
        zeigt danach auf das gewählte Rezept, und derselbe Satz läuft noch
        einmal durch `chat.turn` — Zutaten, Suche, Vorschläge, Ja/Nein.

        **Keine zweite Suche.** Die zwölf Treffer liegen seit dem ersten Abruf
        in `dish_treffer`; geholt wird höchstens das Detail des gewählten
        Rezepts, und war es schon einmal da, gar nichts.

        **Der Korb wird nicht angefasst.** Was bestätigt wurde, gehört dem
        Korb (WB-361, WB-384) — der neue Zug legt eine neue Vorschlagsliste
        an und nimmt nichts zurück. Die Oberfläche sagt es vor dem Tipp, und
        das Band danach sagt es noch einmal.

        **Seit WB-402 antwortet dieser Eingang in ZWEI Schritten**, und das
        ist die ganze Kur des Tickets:

            1. hier          Detail holen (~20 ms) und die KARTE
                             zurückgeben — sofort
            2. `/rezept/vorschlaege`   das Gericht umhängen und der Zug mit
                             den zwei Modellstufen, gemessen 24,29 s

        **Umgehängt wird erst im zweiten Schritt** (WB-406). Schritt 1
        kostet kein Modell und kommt immer durch; Schritt 2 kann ausfallen,
        und dann hätte die Wahl in der Datenbank gestanden, ohne dass je ein
        Zug zu ihr entstanden wäre. Das Detail bleibt geholt — es kostet
        nichts und liegt danach in `recipe`.

        Der Grund steht in `zugrezept.zur_wahl`: alles, was die Karte zeigt,
        liegt in der Datenbank, und nur die Vorschlagsliste kostet das
        Modell. Vorher lief beides in einem Request, und fünfundzwanzig
        Sekunden lang war nicht zu sehen, dass die Wahl angekommen ist —
        danach wurde die ganze Seite getauscht und die Bildlaufposition war
        weg. Ohne JavaScript bleibt es bei EINEM Request und der Vollseite.
        """
        gewuenscht = (werte.get("rezept") or "").strip()
        c = con()
        try:
            karte, treffer = _wahl_vollziehen(request, c, mid, gewuenscht)
            if karte is None:
                return treffer          # eine fertige Fehlerantwort
            if not ist_htmx(request):
                # Ohne JavaScript gibt es nichts nachzuladen: der ganze
                # Wechsel läuft in DIESEM Request, und zurück geht die
                # Vollseite. Langsam, aber vollständig — und es bleibt bei
                # einer Zusicherung, nicht bei zweien.
                return _wechsel_vollziehen(request, c, mid, karte, treffer)
            # Die Karte, die schon dasteht (WB-402). `dish.recipe_id` zeigt
            # nach `waehlen` bereits auf das gewählte Rezept — Name, Zeiten,
            # Zutatenliste, Bewertung stehen in der Datenbank und kosten kein
            # Modell. Die Vorschläge holt das Bruchstück selbst nach.
            vorschau = zugrezepte.zur_wahl(c, int(karte["dish_id"]))
            # **Und dann zeigt das Gericht wieder dorthin, wo der Chat
            # hinzeigt** (WB-406). Umgehängt wird erst in der zweiten Hälfte,
            # zusammen mit dem Zug — denn diese hier kommt IMMER durch (sie
            # kostet kein Modell), und die zweite kann ausfallen. Gemessen am
            # laufenden Shop am 2026-08-30: zwei Wechsel, beide Hälften mit
            # 200 beantwortet, kein einziger neuer Zug in `chat_message` — und
            # `dish` zeigte danach auf ein Rezept, das im Chat als „noch
            # wählbar" dastand. Kommt die zweite Hälfte gar nicht (Telefon zu,
            # Verbindung weg), war die Wahl sonst gebucht, ohne dass je etwas
            # zu ihr entstanden wäre.
            #
            # Das Detail bleibt geholt: es kostet nichts, liegt in `recipe`,
            # und die zweite Hälfte findet es dort wieder (WB-387).
            app.state.chat.quelle.zeigt_auf(c, karte["gericht"],
                                            int(karte["id"]))
            return vorlagen.TemplateResponse(
                request, "_zugwechsel.html",
                {"mid": mid, "rezept": treffer["rezept_id"],
                 "gewechselt": treffer["titel"], "r": vorschau})
        finally:
            c.close()

    @app.post("/chat/{mid}/rezept/vorschlaege")
    def chat_rezept_vorschlaege(request: Request, mid: int,
                                werte: Formular = Depends(formular)):
        """Die zweite Hälfte des Wechsels: der Zug selbst (WB-402).

        **Das ist der Teil, der wirklich dauert** — `plan.zutatenbegriffe`,
        die Katalogsuchen, `plan.choose`. Gemessen am laufenden Shop waren es
        24,29 s, und bis zu diesem Ticket war das die Zeit, die verging, BEVOR
        überhaupt zu sehen war, dass die Wahl angekommen ist. Jetzt steht die
        Karte da, während hier gesucht wird.

        Angestossen wird das aus `_zugwechsel.html` per `hx-trigger="load"`.
        Ein eigener Eingang und kein Umweg: die Adresse trägt dieselbe
        Rezept-ID wie der Tipp davor und prüft sie noch einmal gegen
        `dish_treffer` — was nicht angeboten wurde, wird auch hier nicht
        gewählt. `waehlen` läuft dabei zum zweiten Mal und kostet nichts: das
        Rezept ist schon geholt, es bleibt eine Zeile in `dish` (WB-387).
        """
        gewuenscht = (werte.get("rezept") or "").strip()
        c = con()
        try:
            karte, treffer = _wahl_vollziehen(request, c, mid, gewuenscht)
            if karte is None:
                return treffer          # eine fertige Fehlerantwort
            return _wechsel_vollziehen(request, c, mid, karte, treffer)
        finally:
            c.close()

    @app.post("/chat/{mid}/rezept/vorwaermen")
    def chat_rezept_vorwaermen(request: Request, mid: int,
                               werte: Formular = Depends(formular)):
        """Die Zuordnung einer Alternative rechnen, bevor jemand sie antippt.

        **Der zweite Teil von WB-408.** Die gemerkte Zuordnung macht einen
        Wechsel zu einem BEKANNTEN Rezept zu einer Datenbankabfrage; damit
        auch der ERSTE Tipp sofort ist, muss sie schon dastehen. Also rechnet
        die Karte sie für die obersten Alternativen, während der Mensch das
        Rezept liest — angestossen aus `_zugrezept.html` mit
        `hx-trigger="load delay:Ns"`, gestaffelt, ohne Antwort im Dokument.

        **Der Zeiger des Gerichts wird dabei nicht angefasst** (WB-406).
        Vorwärmen ist keine Wahl: `Quelle.bereitstellen` holt das Detail und
        hängt hinterher zurück auf das Rezept, das der Chat zeigt.

        **Antwortet immer mit 204 und nie mit einem Fehler.** Niemand hat
        etwas gefragt; was hier schiefgeht, kostet die Wartezeit des nächsten
        Tipps und sonst nichts. `HX-Reswap: none` hält auch eine Antwort mit
        Körper aus dem Dokument heraus.
        """
        gewuenscht = (werte.get("rezept") or "").strip()

        def leer(warum: str) -> Response:
            # **Der Grund steht im Kopf und nicht im Körper.** 204 heisst
            # „nichts anzuzeigen", und das bleibt so. Aber ein Eingang, der
            # jeden Ausgang gleich beantwortet, ist von aussen nicht mehr zu
            # unterscheiden — „stand schon da" und „mir ist etwas
            # kaputtgegangen" sähen identisch aus. Diese Zeile kostet nichts
            # und macht den Vorwärmlauf im Protokoll lesbar.
            return Response(status_code=204,
                            headers={"X-Zettel-Vorwaermen": warum})

        c = con()
        try:
            karte, treffer = _andere_wahl(c, mid, gewuenscht)
            if treffer is None or not karte.get("id"):
                return leer("nicht_angeboten")
            rid = app.state.chat.quelle.bereitstellen(
                c, karte["gericht"], treffer, zurueck_auf=int(karte["id"]))
            if rid is None:
                return leer("kein_detail")
            return leer(app.state.chat.zuordnung_vorwaermen(c, int(rid)))
        except Exception as e:                   # noqa: BLE001 — bewusst breit
            # Ein Vorwärmlauf darf keine Seite zerbrechen, die jemand ansieht.
            # Er hat kein Ergebnis, das jemand vermisst.
            return leer(f"fehler: {type(e).__name__}")
        finally:
            c.close()

    @app.post("/chat/{mid}/neusuche")
    def chat_neusuche(request: Request, mid: int,
                      werte: Formular = Depends(formular)):
        """Denselben Satz noch einmal — ohne Gedächtnis (WB-411).

        „Melde dann dass sie cached sind und gib die Möglichkeit neu zu
        suchen." Das hier ist die zweite Hälfte: der Zug läuft noch einmal,
        und diesmal wird weder die Zuordnung des Rezepts noch eine gemerkte
        Wahl je Begriff gelesen. Was dabei herauskommt, **überschreibt
        beide** — der Weg zurück aus einer Wahl, die einmal danebengriff.

        Gelöscht wird dabei nichts im Voraus. Ein `DELETE` vor dem Lauf
        hiesse, bei einem Ausfall der Box mit leeren Händen dazustehen; so
        gilt die alte Erinnerung genau so lange, bis eine neue da ist.

        Der Zug tritt an die Stelle des alten, wie beim Rezeptwechsel
        (WB-403) — dieselbe Antwort, dieselbe Zusicherung über den Korb: was
        bestätigt wurde, bleibt liegen.
        """
        c = con()
        try:
            zeile = _satzzeile_zum_zug(c, mid)
            satz = (zeile["content"] if zeile is not None else "").strip()
            if not satz:
                return _wechsel_fehler(
                    request, c, mid,
                    "Zu diesem Zug steht kein Satz mehr da — dann gibt es "
                    "auch nichts, was noch einmal laufen könnte.")
            stand = c.execute(
                "SELECT max(id) AS letzte FROM chat_message"
                " WHERE order_id = (SELECT order_id FROM chat_message"
                "                    WHERE id = ?)", (mid,)).fetchone()["letzte"]
            try:
                app.state.chat.turn(c, satz, frisch=True)
            except chatmodul.ChatNichtVerfuegbar as e:
                return _wechsel_fehler(request, c, mid, zustand=e.zustand,
                                       fehler=_nicht_verfuegbar(e))
            except chatmodul.ChatFehler as e:
                return _wechsel_fehler(request, c, mid, fehler=str(e))
            neue = _zug_ersetzen(c, mid,
                                 zeile["id"] if zeile is not None else None,
                                 stand)
            return _wechsel_antwort(request, c, mid, neue)
        finally:
            c.close()

    def _wahl_vollziehen(request: Request, c: sqlite3.Connection, mid: int,
                         gewuenscht: str):
        """Prüft die gewählte Rezept-ID und holt ihr Rezept.

        Gibt `(karte, treffer)` zurück — oder `(None, antwort)` mit einer
        fertigen Fehlerantwort; `karte is None` unterscheidet die beiden
        Fälle. Die Antwort hat die Grösse des Wartekastens (WB-402/403): sie
        tritt an die STELLE des Zugs, und ein ganzer Zug als Antwort stünde
        danach zweimal im Dokument.

        **`waehlen` hängt das Gericht dabei um** — der Aufrufer entscheidet,
        ob es so bleibt. Die zweite Hälfte lässt es stehen, die erste hängt
        es zurück (WB-406): sie ist nur die Vorschau, und was sie zeigt, ist
        noch keine Entscheidung in der Datenbank.
        """
        karte, treffer = _andere_wahl(c, mid, gewuenscht)
        if treffer is None:
            # Der Zug ist weg, die Trefferliste erneuert, oder die ID kam
            # nicht aus dieser Liste. Gewählt werden kann nur, was
            # angeboten wurde — dieselbe Regel wie bei den Produkt-IDs in
            # `plan.choose` und bei den Sorten in WB-368.
            return None, _wechsel_fehler(
                request, c, mid, "Dieses Rezept steht zu dem Zug nicht "
                                 "(mehr) zur Wahl.")
        if str(karte.get("source_id") or "") == treffer["rezept_id"]:
            # Schon das vorgeschlagene. Ein ganzer Zug (20 bis 35 s am
            # Modell) für ein Ergebnis, das bereits dasteht, wäre die
            # teuerste Art, nichts zu tun.
            return None, _wechsel_fehler(
                request, c, mid,
                f"„{treffer['titel']}“ ist bereits das vorgeschlagene "
                "Rezept.")
        zustand = app.state.chat.quelle.waehlen(c, karte["gericht"], treffer)
        if zustand != gerichte.OK:
            return None, _wechsel_fehler(
                request, c, mid,
                f"„{treffer['titel']}“ liess sich nicht holen — Chefkoch war "
                "gerade nicht zu erreichen. Das bisherige Rezept steht "
                "unverändert da.")
        return karte, treffer

    def _wechsel_fehler(request: Request, c: sqlite3.Connection, mid: int,
                        fehler: str, zustand=None):
        """Eine Meldung dort, wo die neue Karte gestanden hätte (WB-402).

        Ohne HTMX geht die Vollseite zurück — dann gibt es kein Bruchstück,
        in das etwas getauscht werden könnte, und die Meldung steht am Chat.

        **Der alte Zug reist mit, und das ist neu in WB-403.** Solange der
        Wartekasten HINTER den Zug gesetzt wurde (`afterend`), stand der Zug
        auch im Fehlerfall noch da. Seit der Tausch ihn ERSETZT, nimmt jede
        Antwort seinen Platz ein — eine Meldung allein liesse eine Lücke, wo
        eben noch die Karte war, und der Wechsel sähe aus wie geglückt.

        `HX-Reswap` hängt dem Tausch ein `show:` an, aber nur hier: die
        Meldung steht am oberen Rand des ersetzten Stücks und wäre sonst
        genau die Sorte Rückmeldung, die niemand sieht (WB-378). Der
        gelungene Wechsel scrollt nicht — darum geht dieses Ticket.
        """
        if not ist_htmx(request):
            return _chat_antwort(request, c, fehler=fehler, zustand=zustand)
        alt = vorschlagsliste.zug(c, mid)
        if alt is not None:
            _zug_fuellen(c, alt)
        antwort = vorlagen.TemplateResponse(
            request, "_zugwechsel_fehler.html",
            # `chat_zustand` trägt das Band mit dem Weckzustand. Es hängt am
            # Seitenkopf und nicht am Zug, kommt also out-of-band mit —
            # dieselbe Zeile wie in `_chat_antwort.html`.
            {"mid": mid, "chat_fehler": fehler, "alt": alt,
             "chat_zustand": zustand, "oob": True,
             "aufklappen": None, "gerade": None, **_korb_zahlen(c)})
        antwort.headers["HX-Reswap"] = f"outerHTML show:#wechsel-{mid}:top"
        return antwort

    def _wechsel_vollziehen(request: Request, c: sqlite3.Connection, mid: int,
                            karte: dict, treffer: dict):
        """Der Zug zum gewählten Rezept — die zwei Modellstufen.

        Der Tipp führt in den NORMALEN Ablauf und nicht in einen zweiten
        daneben (dieselbe Regel wie bei den Sorten in WB-368): derselbe Satz
        läuft noch einmal durch `chat.turn`, und heraus kommt ein ganz
        gewöhnlicher Zug.
        """
        # Derselbe SATZ wie beim ersten Mal, damit derselbe Zug
        # herauskommt: „alles für Lasagne, und Klopapier" hängt das
        # Klopapier auch diesmal an. Fehlt er (ein Verlauf, den jemand
        # zwischendurch geleert hat), tut es der Gerichtsname.
        zeile = _satzzeile_zum_zug(c, mid)
        satz = (zeile["content"] if zeile is not None else None) \
            or karte["gericht"]
        # Der Stand VOR dem Zug. „Alles nach `mid`" wäre falsch, sobald
        # jemand einen ÄLTEREN Zug wechselt — dann läge der halbe Verlauf
        # dahinter, und der würde mit abgelöst.
        stand = c.execute(
            "SELECT max(id) AS letzte FROM chat_message"
            " WHERE order_id = (SELECT order_id FROM chat_message WHERE id = ?)",
            (mid,)).fetchone()["letzte"]
        try:
            app.state.chat.turn(c, satz, gewechselt=treffer["titel"])
        except chatmodul.ChatNichtVerfuegbar as e:
            # **Kein Zug, kein Wechsel** (WB-406). Bis dahin stand hier „„Pho
            # Ga“ ist jetzt das Rezept zu „Pho“" — und das stimmte sogar, war
            # aber genau der Schaden: das Gericht zeigte auf ein Rezept, zu
            # dem es keinen Zug gab, während der Chat unverändert das alte
            # zeigte und es weiter zur Wahl stellte. Ersetzt wird nichts, also
            # ändert sich auch nichts.
            _zurueck_zum_zug(c, karte)
            return _wechsel_fehler(
                request, c, mid, zustand=e.zustand,
                fehler=f"„{treffer['titel']}“ liess sich nicht wählen: "
                       f"{_nicht_verfuegbar(e)} Das bisherige Rezept steht "
                       "unverändert da.")
        except chatmodul.ChatFehler as e:
            _zurueck_zum_zug(c, karte)
            return _wechsel_fehler(
                request, c, mid,
                fehler=f"„{treffer['titel']}“ liess sich nicht wählen: {e} "
                       "Das bisherige Rezept steht unverändert da.")
        neue = _zug_ersetzen(c, mid, zeile["id"] if zeile is not None else None,
                             stand)
        return _wechsel_antwort(request, c, mid, neue,
                                gewechselt=treffer["titel"])

    def _zurueck_zum_zug(c: sqlite3.Connection, karte: dict) -> None:
        """Das Gericht zeigt wieder auf das Rezept, das der Chat zeigt (WB-406).

        **Die Karte des Zugs IST der Beleg dafür, was gilt.** Sie steht in
        `chat_rezept` und ändert sich nicht, wenn ein Wechsel scheitert; der
        Verweis in `dish` dagegen wurde von `waehlen` schon umgehängt. Beide
        wieder gleichzuziehen ist dieselbe Zusicherung, die WB-403 für den
        Verlauf gibt: was im Dokument steht und was in der Datenbank steht,
        soll dasselbe sein.

        Kein Netz und keine Anfrage — das Rezept ist geholt, es geht nur um
        eine Zeile in `dish`.
        """
        if karte.get("gericht") and karte.get("id"):
            app.state.chat.quelle.zeigt_auf(c, karte["gericht"],
                                            int(karte["id"]))

    def _zug_ersetzen(c: sqlite3.Connection, mid: int, satz_id: int | None,
                      stand: int | None) -> list[int]:
        """Der frische Zug tritt an die Stelle des gewechselten (WB-403).

        **Das ist die Hälfte des Tickets, die in der Datenbank steht.** Bis
        WB-402 hängte ein Wechsel einen zweiten Zug unter den ersten: nach
        drei Versuchen standen vier fast gleiche Lasagne-Züge untereinander,
        und ein Neuladen brachte sie alle wieder. Jetzt sagt jede neue Zeile,
        welche sie ablöst (`chat_message.ersetzt`), und der Verlauf zeigt von
        einer Kette nur ihr letztes Glied.

        **Gelöscht wird nichts**, und das ist die begründete Wahl zwischen
        den beiden Wegen des Tickets: an der alten Antwortzeile hängen
        Vorschläge, und ein bestätigter Vorschlag liegt im Korb und trägt ein
        Eval-Label (Spec 8.1, WB-329). `ON DELETE CASCADE` nähme beim Löschen
        der Zeile genau die mit. Dieses Projekt hat mehrfach anders
        entschieden — `zurueckgenommen` in WB-361, `corrected_from` in
        WB-359 —, und hier kostet es eine Spalte statt einer Entscheidung.

        Zugeordnet wird über die ROLLE und nicht über die Reihenfolge: Frage
        löst Frage ab, Antwort löst Antwort ab. Beide behalten damit ihren
        Platz im Verlauf, denn `ersetzt` ist auch die Sortiergrösse.

        `stand` ist die höchste id VOR dem Zug — nicht `mid`. Wer einen
        älteren Zug wechselt, hätte sonst alles dahinter mit abgelöst.
        """
        alt = {vorschlagsliste.ROLLE_NUTZERIN: satz_id,
               vorschlagsliste.ROLLE_AGENT: mid}
        neue = []
        for r in c.execute(
                "SELECT id, role FROM chat_message WHERE id > ?"
                "   AND order_id = (SELECT order_id FROM chat_message"
                "                    WHERE id = ?)"
                " ORDER BY id", (stand if stand is not None else mid, mid)):
            neue.append(int(r["id"]))
            # `pop`: jede alte Zeile wird höchstens einmal abgelöst. Schriebe
            # ein Zug je Rolle zwei Zeilen, wäre die zweite ein neuer Zug und
            # kein Ersatz — und nicht stillschweigend die Wurzel der ersten.
            vorher = alt.pop(r["role"], None)
            if vorher is not None:
                vorschlagsliste.ersetzen(c, vorher, int(r["id"]))
        return neue

    def _wechsel_antwort(request: Request, c: sqlite3.Connection, mid: int,
                         neue: list[int], gewechselt: str | None = None):
        """Das Band und der neue Zug — AN DER STELLE des alten (WB-403).

        Ohne HTMX geht die Vollseite zurück; dort steht das Band oben am Chat
        und der neue Zug an der Stelle des alten, wie im Bruchstück.

        Drei Stücke, und jedes hat einen Grund:

            1. der REST des alten Zugs, falls an ihm eine Entscheidung
               hängt — sonst gar nichts
            2. das Band mit dem Namen des neuen Rezepts
            3. der neue Zug

        **Die neue FRAGE ist nicht dabei.** Sie ist Wort für Wort derselbe
        Satz wie die alte (`_wechsel_vollziehen` schickt ihn noch einmal
        durch `chat.turn`), und die alte steht als eigene Zeile über dem
        getauschten Stück. Sie mitzuschicken hiesse, den Satz zweimal
        untereinander zu setzen — genau der Zuwachs, den dieses Ticket
        abschafft. In der Datenbank löst sie die alte trotzdem ab, und nach
        dem Neuladen steht sie an ihrer Stelle.

        **Der Rest des alten Zugs steht davor und nicht darunter**, weil er
        älter ist: dieselbe Reihenfolge, die `verlauf()` nach dem Neuladen
        liefert (`ORDER BY coalesce(ersetzt, id), id`). Was im Dokument steht
        und was in der Datenbank steht, soll dasselbe sein.
        """
        rest = vorschlagsliste.zug(c, mid)
        if rest is not None and (not rest["ueberholt"]
                                 or not rest["vorschlaege"]):
            # Nicht abgelöst gibt es hier nicht (der Zug lief gerade), und
            # ohne entschiedene Zeile bleibt von ihm nichts übrig — offene
            # Vorschläge zu einem abgewählten Rezept zählen nach Spec 8.1
            # nirgends.
            rest = None
        if not ist_htmx(request):
            return _chat_antwort(request, c, gewechselt=gewechselt)
        gesucht = (vorschlagsliste.zug(c, i) for i in neue)
        zeilen = [z for z in gesucht if z is not None
                  and z["role"] != vorschlagsliste.ROLLE_NUTZERIN]
        for z in ([rest] if rest is not None else []) + zeilen:
            _zug_fuellen(c, z)
        return vorlagen.TemplateResponse(
            request, "_zugwechsel_fertig.html",
            {"mid": mid, "rest": rest, "neue": zeilen,
             "gewechselt": gewechselt, "aufklappen": None, "gerade": None,
             "chat_fehler": None, "chat_zustand": None, "oob": True,
             # Der frische Zug darf vorwärmen (WB-408) — wer einmal
             # gewechselt hat, wechselt oft noch einmal, und dann soll die
             # nächste Wahl schon gerechnet sein.
             "vorwaermen": zeilen[-1]["id"] if zeilen else None,
             **_korb_zahlen(c)})

    @app.post("/chat/{mid}/portionen")
    async def chat_portionen(request: Request, mid: int):
        """Für wie viele Portionen dieser Zug rechnet (WB-384).

        **Bis zu diesem Ticket gab es das Feld im Chat gar nicht.** Wer
        „alles für Lasagne" schrieb, bekam Chefkochs Portionszahl und konnte
        sie nicht ändern — die Mengen aus WB-369 wurden für eine Personenzahl
        gerechnet, die niemand gewählt hatte. WB-369 hatte das bewusst
        offengelassen (aus dem Satz zu raten wäre falsch gewesen: „in 35
        echten Nutzersätzen kommt keine einzige Ziffer vor"), und ein Feld ist
        kein Raten.

        Geändert werden die Mengen der VORSCHLAGSZEILEN dieses Zugs, und zwar
        über `mengen.skaliere` — es bleibt bei der einen Rechnung aus WB-362.
        Das Rezept selbst bleibt unberührt: „diesmal für acht" ist eine
        Aussage über diesen Einkauf.

        **Ein bereits eingelegter Posten wird nicht nachgerechnet** (WB-361,
        WB-387). Was bestätigt wurde, gehört dem Korb; die neue Zahl wirkt auf
        das Nächste, und die Karte sagt es vorher wie nachher.

        Die Antwort ist ein ZUG und nicht der ganze Verlauf (WB-372): es
        ändern sich mehrere Zeilen auf einmal, aber nur die dieses Zugs.
        """
        werte = await eingaben(request)
        c = con()
        try:
            zeilen = vorschlagsliste.liste(c, mid)
            karten = zugrezepte.zum_zug(c, mid, zeilen)
            gewuenscht = (werte.get("rezept") or "").strip()
            karte = next((k for k in karten
                          if str(k["id"]) == gewuenscht), None)
            if karte is None:
                # Der Zug ist weg oder die Karte gehört nicht dazu. Dieselbe
                # Regel wie bei der Rezeptwahl darüber: geändert werden kann
                # nur, was angeboten wurde.
                return _chat_antwort(
                    request, c, fehler="Zu diesem Zug steht dieses Rezept "
                                       "nicht (mehr) da.")
            eigene = zugrezepte.zeilen_zur_karte(karte, zeilen, len(karten))
            try:
                bericht = zugrezepte.portionen_setzen(
                    c, mid, int(karte["id"]), werte.get("portionen"), eigene)
            except zugrezepte.ZugrezeptFehler as e:
                return _zug_antwort(request, c, mid, fehler=str(e))
            return _zug_antwort(request, c, mid, fehler=bericht["grund"])
        finally:
            c.close()

    def _andere_wahl(c: sqlite3.Connection, mid: int, source_id: str):
        """Die Rezeptkarte eines Zugs und der gewählte Treffer dazu.

        `(None, None)`, wenn es den Zug, seine Karte oder diesen Treffer
        nicht (mehr) gibt. Gesucht wird über GENAU DIE Liste, die der Zug
        angeboten hat — an der Karte hängt das Gericht (`chat_rezept.dish_id`)
        und daran die Trefferliste.
        """
        if not source_id:
            return None, None
        for karte in zugrezepte.zum_zug(c, mid):
            if not karte.get("dish_id"):
                continue
            treffer = app.state.chat.quelle.angeboten(c, int(karte["dish_id"]),
                                                      source_id)
            if treffer is not None:
                return karte, treffer
        return None, None

    def _satzzeile_zum_zug(c: sqlite3.Connection, mid: int):
        """Die Zeile mit dem Satz, auf den dieser Zug geantwortet hat.

        Die letzte Nutzerinnenzeile VOR der Antwortzeile. `chat.turn` schreibt
        beide in derselben Sekunde (`_schreiben`), also ist es die davor —
        über die id und nicht über den Zeitstempel, denn zwei Zeilen können
        dieselbe Sekunde tragen.

        Die ID kommt seit WB-403 mit: beim Rezeptwechsel löst die neue Frage
        die alte ab, und dafür muss man wissen, welche das war. `None` ist
        praktisch nicht zu erreichen — `chat.turn` schreibt immer beide
        Zeilen —, und der Wechsel kommt auch damit zurecht: dann bleibt die
        neue Frage eine Zeile ohne Vorgängerin.
        """
        return c.execute(
            "SELECT id, content FROM chat_message"
            " WHERE role = ? AND id < ?"
            "   AND order_id = (SELECT order_id FROM chat_message WHERE id = ?)"
            " ORDER BY id DESC LIMIT 1",
            (vorschlagsliste.ROLLE_NUTZERIN, mid, mid)).fetchone()

    @app.post("/chat/vorschlag/{sid}/entscheiden")
    async def vorschlag_entscheiden(request: Request, sid: int):
        """„Ja", „Nein" — oder zurück auf `offen` (WB-361).

        Ein „Nein" verwirft nicht mehr bloss, es **klappt die Alternativen
        auf** (WB-359): die Kandidaten, die die Suche zu dieser Zutat ohnehin
        vorgelegt hat. Aufgeklappt wird genau die eine Zeile, die gerade
        verworfen wurde — ältere bleiben eingeklappt, sonst stünde nach fünf
        „Nein" eine Seite voller Listen.

        **Der Rückweg ist dieselbe Adresse mit `decision=offen`** und kein
        eigener Endpunkt. Er ist keine vierte Sache, die man mit einem
        Vorschlag tun kann, sondern die dritte Entscheidung, die es seit
        Spec 8.1 gibt — sie war bloss nie anzutippen. Ein zweiter Endpunkt
        müsste dieselbe Prüfung und dieselbe Antwort noch einmal bauen.

        **Zurück kommt seit WB-372 die ZEILE und nicht der Verlauf.** Genau
        dieser Tipp wird in Serie durch eine Liste von 150 Zeilen gemacht, auf
        dem Telefon über Mobilfunk; vorher kostete jeder einzelne den
        kompletten Chat (gemessen 259.941 Bytes bei 17 Zügen).
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            entscheidung = werte.get("decision", "")
            try:
                vorschlagsliste.entscheiden(c, sid, entscheidung)
            except vorschlagsliste.VorschlagFehler as e:
                fehler = str(e)
            auf = sid if entscheidung == vorschlagsliste.VERWORFEN else None
            # Nur ein „Ja" bekommt den Ankunftssatz an der Zeile (WB-382).
            # „Nein" und der Rückweg ändern am Korb nichts, und ein Satz, der
            # bei jeder Entscheidung erscheint, sagt nichts mehr.
            eben = sid if entscheidung == vorschlagsliste.BEHALTEN else None
            return _entscheidung_antwort(request, c, sid, fehler=fehler,
                                         aufklappen=auf, gerade=eben)
        finally:
            c.close()

    @app.post("/chat/vorschlag/{sid}/statt")
    async def vorschlag_korrigieren(request: Request, sid: int,
                                    produkt_id: int = 0):
        """Eine Alternative statt des Vorschlags in den Korb (WB-359).

        Der ursprüngliche Vorschlag bleibt als `removed` stehen und die neue
        Zeile verweist auf ihn — die Korrektur ist dadurch als Korrektur
        erkennbar und nicht als zwei lose Entscheidungen.

        Zurück kommt der ZUG und nicht die Zeile (WB-372): die Korrektur ist
        eine NEUE Vorschlagszeile, und die hat im Dokument noch kein Element,
        in das sie getauscht werden könnte.
        """
        c = con()
        try:
            fehler = None
            mid = _zug_von(c, sid)
            try:
                vorschlagsliste.korrigieren(c, sid, produkt_id)
            except vorschlagsliste.VorschlagFehler as e:
                fehler = str(e)
            return _zug_antwort(request, c, mid, fehler=fehler)
        finally:
            c.close()

    @app.post("/chat/vorschlag/{sid}/freitext")
    async def vorschlag_freitext(request: Request, sid: int):
        """„Nichts davon" — die Zutat kommt als Freitext in den Korb.

        Bei einer echten Katalog-Lücke (das gemessene Beispiel ist „Sellerie"
        mit zwei unbrauchbaren Treffern) ist das der richtige Ausgang und
        nicht der Notausgang.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            mid = _zug_von(c, sid)
            try:
                vorschlagsliste.stattdessen_freitext(c, sid,
                                                     werte.get("text", ""))
            except vorschlagsliste.VorschlagFehler as e:
                fehler = str(e)
            # Der Zug: auch der Freitext ist eine neu angelegte Zeile.
            return _zug_antwort(request, c, mid, fehler=fehler)
        finally:
            c.close()

    @app.post("/chat/{mid}/alle")
    async def vorschlaege_alle(request: Request, mid: int):
        """Sammelknopf. Rührt nur an, was noch offen ist.

        Der ganze Zug zurück (WB-372): er ändert jede offene Zeile darin.

        **`decision=offen` ist „Doch nicht alles"** (WB-397) — der Rückweg für
        den letzten Sammelvorgang dieses Zugs, und dieselbe Adresse aus
        demselben Grund wie an der einzelnen Zeile: es ist keine vierte Sache,
        die man mit einem Zug tun kann, sondern die dritte Entscheidung aus
        Spec 8.1. Zurückgenommen wird ausschliesslich, was der Sammeltipp
        entschieden hat; was sie einzeln angetippt hat, bleibt stehen.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                vorschlagsliste.alle_entscheiden(c, mid,
                                                 werte.get("decision", ""))
            except vorschlagsliste.VorschlagFehler as e:
                fehler = str(e)
            return _zug_antwort(request, c, mid, fehler=fehler)
        finally:
            c.close()

    # ----------------------------------------------------------------------
    # Der Rezeptentwurf (WB-337)
    #
    # Vier kleine Formulare, jedes an seiner eigenen Adresse — dieselbe
    # Bauart wie die Entscheidungen darüber: `action` und `hx-post` zeigen auf
    # dieselbe Stelle, ohne JavaScript lädt die Seite eben neu.
    #
    # **Gespeichert wird hier nichts.** Alle vier ändern nur den Entwurf; das
    # Rezept entsteht beim Abschicken (`orders.abschicken`), aus demselben
    # Grund wie die Eval-Annotationen aus WB-329 — bis dahin darf sie ihre
    # Meinung ändern.

    @app.post("/chat/{mid}/entwurf/name")
    async def entwurf_benennen(request: Request, mid: int):
        """Der Rezeptname — überschreibbar, und das ist keine Kosmetik.

        `rezeptweg.erkenne` sucht den Namen im Satz. Heisst das Rezept
        „Bolognese al Forno à la Mama", greift die Abkürzung beim nächsten
        Mal nicht, und der Zug kostet wieder Modell und Wartezeit.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                entwuerfe.benennen(c, mid, werte.get("name", ""))
            except entwuerfe.EntwurfFehler as e:
                fehler = str(e)
            return _zug_antwort(request, c, mid, fehler=fehler)
        finally:
            c.close()

    @app.post("/chat/{mid}/entwurf/verwerfen")
    async def entwurf_verwerfen(request: Request, mid: int):
        """„Daraus soll kein Rezept werden" — und mit `ja=0` zurück.

        Der Knopf muss es geben: das Rezept entsteht beim Abschicken von
        selbst, und was von selbst entsteht, braucht einen Weg, es zu lassen.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                entwuerfe.verwerfen(c, mid, werte.get("ja", "1") != "0")
            except entwuerfe.EntwurfFehler as e:
                fehler = str(e)
            return _zug_antwort(request, c, mid, fehler=fehler)
        finally:
            c.close()

    @app.post("/chat/vorschlag/{sid}/rezeptzeile")
    async def entwurf_zeile(request: Request, sid: int):
        """Eine Zutat aus dem Entwurf nehmen (`drin=0`) oder zurückholen.

        **Nicht dasselbe wie „Nein".** „Nein" heisst „dieses Produkt ist
        falsch" und ist das Eval-Label aus Spec 8.1; hier heisst es „das
        kaufe ich, aber es gehört nicht ins Rezept". Ein gemeinsamer Knopf
        würde genau die Zahl verfälschen, um die es im Projekt geht.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                entwuerfe.zeile_setzen(c, sid, werte.get("drin", "1") != "0")
            except (entwuerfe.EntwurfFehler,
                    vorschlagsliste.VorschlagFehler) as e:
                fehler = str(e)
            return _zug_antwort(request, c, _zug_von(c, sid), fehler=fehler)
        finally:
            c.close()

    @app.post("/chat/vorschlag/{sid}/bedarf")
    async def entwurf_bedarf(request: Request, sid: int):
        """Die benötigte Menge einer Zutat im Entwurf — „500 g", nicht „2 ×".

        Was schon im Korb liegt, ändert sich davon nicht (siehe
        `entwurf.bedarf_setzen`): dort steht ein eigenes Mengenfeld.
        """
        werte = await eingaben(request)
        c = con()
        try:
            fehler = None
            try:
                entwuerfe.bedarf_setzen(c, sid, werte.get("menge"),
                                        werte.get("einheit"))
            except (entwuerfe.EntwurfFehler,
                    vorschlagsliste.VorschlagFehler) as e:
                fehler = str(e)
            return _zug_antwort(request, c, _zug_von(c, sid), fehler=fehler)
        finally:
            c.close()

    # ----------------------------------------------------------------------
    # Bestellungen und Pick-Ansicht (Spec 9)

    @app.get("/bestellungen")
    def bestelluebersicht(request: Request, fertig: str | None = None):
        c = con()
        try:
            liste = orders.bestellungen(c)
            for b in liste:
                b["posten"] = orders.posten(c, b["id"])
            # `fertig` kommt als Text und wird hier selbst gewandelt: als
            # `int` deklariert, beantwortet FastAPI „?fertig=abc" mit 422 und
            # die ganze Übersicht ist unerreichbar. Eine unbekannte ID gibt
            # schon jetzt still keine Quittung — eine kaputte soll sich
            # genauso verhalten und nicht die Seite mitnehmen.
            # `isascii()` davor, weil `"²".isdigit()` True ist und
            # `int("²")` trotzdem wirft — eine Ziffer im Sinne von Unicode
            # ist noch keine Zahl im Sinne von `int`.
            gerade = None
            if fertig is not None and fertig.isascii() and fertig.isdigit():
                gerade = next((b for b in liste if b["id"] == int(fertig)), None)
            # Nur für eine Bestellung, die wirklich gerade weg ist: die URL
            # überlebt Reload, Zurück und das Weiterreichen im Haushalts-Chat.
            # „Steht jetzt auf der Pick-Liste" über einen gestern erledigten
            # Einkauf wäre eine grüne Lüge.
            quittung = None
            if gerade is not None and gerade["state"] == "offen":
                quittung = {"n": len(gerade["posten"]),
                            "summe": orders.summe(gerade["posten"])}
            return vorlagen.TemplateResponse(request, "bestellungen.html", {
                **_rahmen(request, c), "bestellungen": liste,
                "quittung": quittung})
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
                # Beide Zahlen bis in die Vorlage, damit die Fertigmeldung
                # „7 geholt, 2 gab's nicht" sagen kann statt „alles abgehakt"
                # (WB-373) — letzteres wäre bei einem leeren Regal gelogen.
                "geholt": sum(g["n_geholt"] for g in gruppen),
                "fehlt": sum(g["n_fehlt"] for g in gruppen),
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
                # Eine Seite und kein weisses Blatt (WB-376): ein Lesezeichen
                # auf eine erledigte Bestellung ist der Normalfall, nicht der
                # Fehlerfall.
                return _nicht_gefunden(
                    request, f"Die Bestellung {order_id} gibt es nicht.",
                    [{"url": "/pick", "text": "Zur Pick-Liste"},
                     {"url": "/bestellungen", "text": "Alle Bestellungen"}])
            return vorlagen.TemplateResponse(request, "pick.html", {
                **_rahmen(request, c), **_pick_kontext(c, order_id)})
        finally:
            c.close()

    @app.post("/pick/{order_id}/posten/{item_id}")
    async def pick_stand(request: Request, order_id: int, item_id: int):
        """Setzt einen Posten auf `gepickt`, `fehlt` oder wieder `offen`.

        EIN Weg für alle drei Stände und nicht zwei Adressen: es ist dieselbe
        Entscheidung über dieselbe Zeile, und der gewünschte Stand steht in der
        Adresse — so ist der Tipp auch dann eindeutig, wenn zwei Telefone
        dieselbe Liste offen haben (WB-373).
        """
        werte = await eingaben(request)
        stand = werte.get("stand", "gepickt")
        # Getrennt von der 404 unten: ein Posten, den es nicht gibt, und ein
        # Stand, den es nicht gibt, sind zwei verschiedene Irrtümer, und wer
        # den zweiten als „nicht gefunden" gemeldet bekommt, sucht am
        # falschen Ende.
        if stand not in orders.POSTEN_STAENDE:
            return Response(status_code=400)
        c = con()
        try:
            try:
                orders.setze_stand(c, item_id, stand)
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
                        fehler: str | None = None,
                        amount: str = "", unit: str = "") -> dict:
        """Alles, was `_rezept.html` braucht — für Vollseite und Bruchstück.

        `amount` und `unit` reisen durch die Suche hindurch (WB-362): wer an
        einer Zutat des Rezepts auf „im Katalog suchen" tippt, nimmt deren
        Menge mit — „500 ml" —, und das „+" am Treffer verknüpft das Produkt
        samt Menge. Ohne diesen Durchreichweg müsste die Menge von Hand
        nachgetragen werden, und sie bliebe in der Praxis leer.
        """
        r = recipes.rezept(c, recipe_id)
        _posten_mit_bild(r["zutaten"], app.state.image_dir)
        return {"rezept": r, "q": q, "meldung": meldung, "fehler": fehler,
                "amount": amount, "unit": unit,
                "treffer": _mit_bild(search.search(c, q, limit=SEITE),
                                     app.state.image_dir) if q.strip() else []}

    def _rezept_antwort(request: Request, c: sqlite3.Connection, recipe_id: int,
                        meldung: str | None = None, fehler: str | None = None,
                        rueckmeldung: str | None = None):
        """HTMX bekommt die Zutatenliste, ein Formular ohne JS die ganze Seite.

        `rueckmeldung` ist die Produkt-id, an deren „+" gedrückt wurde. Sie
        bekommt ihr „im Rezept" AN DER STELLE, an der der Daumen war: die
        Zutatenliste hängt am Seitenanfang, die Trefferliste steht unten, und
        ohne ein Zeichen unten drückt man ein zweites Mal — derselbe Grund,
        aus dem der Korb-Knopf seit WB-323 eine Rückmeldung hat. Der Weg
        dorthin ist ein `hx-swap-oob`, weil der Haupttausch die Zutatenliste
        bleibt und ein Tausch nur einen Platz hat.
        """
        kontext = _rezept_kontext(c, recipe_id, meldung=meldung, fehler=fehler)
        if ist_htmx(request):
            if rueckmeldung:
                return vorlagen.TemplateResponse(
                    request, "_rezept_eingelegt.html",
                    {**kontext, "rueckmeldung": rueckmeldung})
            return vorlagen.TemplateResponse(request, "_rezept.html", kontext)
        if meldung or fehler:
            # Eine Weiterleitung würde die Begründung verlieren, und die
            # Nutzerin sähe nur, dass nichts passiert ist.
            return vorlagen.TemplateResponse(
                request, "rezept.html", {**_rahmen(request, c), **kontext})
        return RedirectResponse(f"/rezepte/{recipe_id}", status_code=303)

    @app.get("/rezepte")
    def rezeptliste(request: Request, weg: str = ""):
        """Die Rezeptliste. `?weg=` meldet ein gerade gelöschtes (WB-376)."""
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "rezepte.html", {
                **_rahmen(request, c), "rezepte": recipes.rezepte(c),
                "weg": weg or None})
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
    def rezept_ansicht(request: Request, recipe_id: int, q: str = "",
                       amount: str = "", unit: str = ""):
        c = con()
        try:
            try:
                kontext = _rezept_kontext(c, recipe_id, q=q, amount=amount,
                                          unit=unit)
            except recipes.RezeptFehler:
                return _nicht_gefunden(
                    request, f"Das Rezept {recipe_id} gibt es nicht (mehr).",
                    _WEGE_REZEPT)
            return vorlagen.TemplateResponse(request, "rezept.html", {
                **_rahmen(request, c), **kontext})
        finally:
            c.close()

    @app.get("/rezepte/{recipe_id}/zubereitung")
    def rezept_zubereitung(request: Request, recipe_id: int):
        """Nur die Schritte — das Stück, das die Rezeptkarte im Chat nachlädt.

        **Der Grund ist die Seitengrösse** (WB-372, WB-383). Pho Bos
        Zubereitung sind 3.924 Zeichen; stünde sie in jedem Rezeptzug des
        Verlaufs, wäre die Ersparnis von 215 KB auf 45 KB wieder aufgebraucht.
        Ein blosses `<details>` hilft dagegen nicht — es versteckt, was
        trotzdem übertragen wurde.

        Ohne JavaScript wird hier nie gefragt; dort steht in der Karte ein
        Link auf die Rezeptseite, auf der dasselbe vollständig steht.
        """
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            r = recipes.rezept(c, recipe_id)
            return vorlagen.TemplateResponse(request, "_zubereitung.html", {
                "rezept": r})
        finally:
            c.close()

    @app.get("/rezepte/{recipe_id}/suche")
    def rezept_suche(request: Request, recipe_id: int, q: str = "",
                     amount: str = "", unit: str = ""):
        """Nur die Trefferliste — das Stück, das HTMX beim Tippen austauscht."""
        c = con()
        try:
            try:
                kontext = _rezept_kontext(c, recipe_id, q=q, amount=amount,
                                          unit=unit)
            except recipes.RezeptFehler:
                return Response(status_code=404)
            return vorlagen.TemplateResponse(request, "_rezept_treffer.html",
                                             kontext)
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/bearbeiten")
    async def rezept_bearbeiten(request: Request, recipe_id: int):
        """Kopfdaten — und die Portionszahl, die alle Mengen mitnimmt (WB-384).

        Bis zu diesem Ticket änderte dieses Feld eine Zahl und eine
        Überschrift, nicht aber die Mengen darunter: „servings" von 4 auf 8,
        und die Zutaten standen weiter bei 500,0 g unter der Zeile „Zutaten
        laut Rezept (für 8 Portionen)". Jetzt rechnet `recipes.aendern` mit —
        und sagt es, statt es geräuschlos zu tun.
        """
        werte = await eingaben(request)
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            try:
                r = recipes.aendern(c, recipe_id, name=werte.get("name"),
                                    servings=werte.get("servings"),
                                    note=werte.get("note"))
            except recipes.RezeptFehler as e:
                # Der Name war leer: die Begründung muss stehen bleiben, sonst
                # sieht die Nutzerin nur, dass nichts gespeichert wurde.
                return _rezept_antwort(request, c, recipe_id, fehler=str(e))
            if r.get("umgerechnet"):
                # Eine Weiterleitung verlöre den Satz — und gerade dieser
                # Handgriff hat mehr getan, als das Formular zeigt.
                return _rezept_antwort(request, c, recipe_id,
                                       meldung=r["umgerechnet"]["meldung"])
            return RedirectResponse(f"/rezepte/{recipe_id}", status_code=303)
        finally:
            c.close()

    @app.get("/rezepte/{recipe_id}/loeschen")
    def rezept_loeschen_fragen(request: Request, recipe_id: int):
        """Die Rückfrage vor dem teuersten Löschknopf des Shops (WB-376).

        `DELETE FROM recipe` nimmt per CASCADE Zutaten, Zubereitung, Zeiten
        und Herkunft mit; bei einem geholten Chefkoch-Rezept sind das zwei
        Modellläufe plus Abruf. Und der Knopf sass direkt unter „Speichern".

        Aufgezählt wird, was WIRKLICH an diesem Rezept hängt, mit Zahlen —
        „alles wird gelöscht" liest sich schneller und sagt weniger. Ein
        Papierkorb ist ausdrücklich nicht Teil davon: Rückfrage und Meldung
        reichen, ein zweiter Speicherort wäre mehr Technik als das Problem
        verlangt.
        """
        c = con()
        try:
            try:
                r = recipes.rezept(c, recipe_id)
            except recipes.RezeptFehler:
                return _nicht_gefunden(
                    request, f"Das Rezept {recipe_id} gibt es nicht (mehr).",
                    _WEGE_REZEPT)
            verlust = []
            if r["n_zutaten"]:
                verlust.append(f"{r['n_zutaten']} verknüpfte Zutaten aus dem"
                               " Katalog")
            if r["rezeptzutaten"]:
                verlust.append(f"{len(r['rezeptzutaten'])} Zutaten laut"
                               " Rezept, mit Menge und Einheit")
            if r["zubereitung"]:
                verlust.append(f"die Zubereitung in"
                               f" {len(r['zubereitung'])} Schritten")
            if r.get("prep_minutes") or r.get("cook_minutes"):
                verlust.append("die Zeiten fürs Planen")
            if r.get("source_url"):
                verlust.append(f"die Herkunft: {r['source_url']}")
            if not verlust:
                verlust.append("Dieses Rezept ist noch leer — es hängt nichts"
                               " daran.")
            return _bestaetigen(
                request,
                titel=f"„{r['name']}“ löschen?",
                frage=("Das Rezept wird endgültig gelöscht. Es gibt keinen"
                       " Papierkorb, aus dem es zurückzuholen wäre."),
                verlust=verlust,
                bleibt=("Was schon im Warenkorb liegt, bleibt dort liegen —"
                        " der Korb ist eine eigene Liste."),
                aktion=f"/rezepte/{recipe_id}/loeschen",
                knopf="Ja, Rezept löschen",
                zurueck=f"/rezepte/{recipe_id}")
        finally:
            c.close()

    @app.post("/rezepte/{recipe_id}/loeschen")
    def rezept_loeschen(request: Request, recipe_id: int):
        """Löscht wirklich. Der zweite Schritt der Rückfrage von oben."""
        c = con()
        try:
            try:
                name = recipes.rezept(c, recipe_id)["name"]
                recipes.loeschen(c, recipe_id)
            except recipes.RezeptFehler:
                return Response(status_code=404)
            # Die Meldung reist im Query mit und nicht in einer Sitzung: der
            # Shop hat keine, und ohne sie sähe die Rezeptliste nach dem
            # Löschen genauso aus wie nach einem Abbruch.
            return RedirectResponse(f"/rezepte?weg={quote(name)}",
                                    status_code=303)
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
                    qty=zahl(werte.get("qty"), 1),
                    # Menge und Einheit kommen aus der Zutatenliste des
                    # Rezepts, wenn dort gesucht wurde (WB-362). Sie sind die
                    # Grösse, die mit den Portionen wächst — ohne sie ist eine
                    # verknüpfte Zutat eine Packung und sonst nichts.
                    amount=werte.get("amount"), unit=werte.get("unit"))
            except orders.UngueltigerPosten:
                fehler = ("Schreib hin, was es sein soll — ein leeres Feld "
                          "ergibt keine Zutat.")
            # Nur der Weg über die Trefferliste hat einen Knopf, an dem eine
            # Rückmeldung stehen könnte: „nichts davon, ich schreibe es
            # selbst" (`free_text`) hat keinen.
            return _rezept_antwort(
                request, c, recipe_id, fehler=fehler,
                rueckmeldung=(None if fehler else werte.get("product_id")))
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

    @app.post("/rezepte/{recipe_id}/zutaten/{item_id}/bedarf")
    async def rezept_zutat_bedarf(request: Request, recipe_id: int,
                                  item_id: int):
        """Die benötigte MENGE einer Zutat — nicht ihre Packungszahl (WB-362).

        Eine eigene Route und nicht `…/menge`, weil es zwei verschiedene
        Grössen sind: die Menge wächst mit den Portionen, die Packungszahl
        nicht. Ein gemeinsames „Menge setzen" baute genau die Verwechslung
        ein, um die es in diesem Ticket geht.

        Ein leeres Feld löscht die Menge. Das muss gehen: wer sich vertippt
        hat, soll die Zutat nicht löschen und neu verknüpfen müssen.
        """
        werte = await eingaben(request)
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            fehler = None
            try:
                recipes.zutat_menge_setzen(c, item_id,
                                           amount=werte.get("amount"),
                                           unit=werte.get("unit"))
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
    async def rezept_in_den_korb(request: Request, recipe_id: int):
        """„Alles in den Warenkorb“ — mit Bericht, nicht mit blossem „ok“.

        Der Bericht ist der Grund, warum diese Route eine Meldung zurückgibt
        und nicht einfach weiterleitet: liegt eine Zutat im Korb, die nicht
        mehr im Katalog steht, muss die Nutzerin das hier lesen — nicht erst
        im Laden. Seit WB-362 steht darin auch, wofür gerechnet wurde: „für 8
        statt 4 Portionen: 1000 ml, das sind 2 × Pomito 500 g".

        `portionen` kommt aus dem Feld neben dem Knopf und ändert das Rezept
        NICHT: „diesmal für acht" ist eine Aussage über diesen Einkauf.
        """
        werte = await eingaben(request)
        c = con()
        try:
            if not _gibt_es(c, recipe_id):
                return Response(status_code=404)
            try:
                bericht = recipes.in_den_korb(c, recipe_id,
                                              portionen=werte.get("portionen"))
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

    @app.get("/status")
    def status(request: Request):
        """Statusseite: Tracer, Agent und nächtlicher Lauf (Spec 11, Spec 12).

        Die Seite ist absichtlich schlicht und absichtlich vollständig. Sie
        zeigt vor allem die VERWORFENEN Läufe mit ihrer Begründung — ein
        Crawler, der still scheitert, ist schlimmer als einer, der laut
        scheitert: der Katalog altert dann weiter und niemand weiss, warum.
        Seit WB-377 gilt dasselbe für den Tracer, der der eigentliche
        Gegenstand dieses Projekts ist.

        **Diese Route ruft `app.state.chat.zustand()` nicht auf, und keine
        künftige Fassung darf das tun.** Der Aufruf schickt ein Magic Packet
        und weckt die vLLM-Box (siehe `/chat/zustand`) — eine Seite,
        die jemand aufmacht, um nachzusehen, ob alles läuft, darf keinen
        Rechner hochfahren. Was `betrieb.statusbericht()` liefert, kommt aus
        der Datenbank und aus Prozesszählern, nichts davon aus dem Netz.
        `tests/test_betrieb.py` hält das mit einem Doppelgänger fest, der beim
        Fragen wirft.
        """
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "status.html", {
                **_rahmen(request, c),
                **betrieb.statusbericht(c),
            })
        finally:
            c.close()

    # ----------------------------------------------------------------------
    # Kassenbons (WB-344 hochladen, WB-358 auslesen)
    #
    # Zwei Schritte, und die Trennung ist keine Kosmetik:
    #
    # * **Hochladen** ist Transport und passiert im Request. Es kennt nur
    #   Bytes.
    # * **Auslesen** startet einen Lauf im Hintergrund (`bons.lauf`) und kehrt
    #   sofort zurück. `pdftotext` ist zwar schnell (gemessen: Millisekunden),
    #   OCR und der Modellaufruf sind es nicht, und eine schlafende Box
    #   braucht 96 s. Nichts davon gehört in einen Request — die Seite fragt
    #   den Stand nach, wie beim Wecken des Modells.
    #
    # Kein Passwort, wie überall sonst im Shop: der Rahmen ist das Tailnet
    # (Spec 10). Eine Anmeldung ausgerechnet auf dieser Seite wäre ein zweites
    # Zugangsmodell neben dem, das die Bindung durchsetzt — und zwei Modelle
    # heissen am Ende, dass keines gilt.
    #
    # **Was hier NICHT passiert:** der Text des Bons wird nirgends abgelegt. Er
    # entsteht im Lauf, wird zerlegt und fällt weg. In die Datenbank gehen nur
    # Laden, Datum, Artikelname, Menge und Preis (siehe `zettel.bons`).

    def _bon_pfad(name: str) -> Path | None:
        return bonmodul.pfad_im_verzeichnis(app.state.bon_dir, name)

    def _bon_kontext(request: Request, c: sqlite3.Connection,
                     fehler: str | None = None, neu: str | None = None,
                     weg: str | None = None) -> dict:
        """Die Bon-Liste, jede Datei mit ihrem Auslesestand.

        Der Stand kommt aus zwei Quellen, und beide werden gebraucht: ein
        laufender Lauf steht nur im Prozess (`bons.lauf`), ein fertiger Beleg
        nur in der Datenbank. Nach einem Neustart des Web-Prozesses ist der
        Lauf vergessen, der Beleg aber nicht — und genau dann muss die Seite
        „ausgelesen" sagen und nicht „läuft".
        """
        eintraege = bonmodul.liste(app.state.bon_dir)
        for e in eintraege:
            e["beleg"] = bonmodul.beleg_zu_datei(c, e["name"])
            e["lauf"] = app.state.bonlaeufe.stand(e["name"])
            # Ein Bild ohne OCR bekommt gar keinen Knopf, statt einen, der
            # verlässlich scheitert. Die Begründung steht direkt daneben.
            e["lesbar"] = e["name"].lower().endswith(".pdf") or bonmodul.ocr_da()
        return {**_rahmen(request, c),
                "bons": eintraege,
                "max_mb": bonmodul.MAX_BYTES // (1024 * 1024),
                "erlaubt": bonmodul.ERLAUBT,
                "laden_titel": bonmodul.LADEN_TITEL,
                "ocr_da": bonmodul.ocr_da(),
                "ocr_fehlt_text": bonmodul.OCR_FEHLT_TEXT,
                "fehler": fehler,
                "neu": neu,
                # Der gerade gelöschte Bon (WB-376): ohne die Meldung sieht die
                # Liste nach dem Löschen aus wie nach einem Abbruch.
                "weg": weg}

    def _bon_seite(request: Request, fehler: str | None = None,
                   neu: str | None = None, code: int = 200,
                   weg: str | None = None):
        """Die ganze Seite — auch im Fehlerfall.

        Eine Weiterleitung nach einem abgelehnten Upload verlöre die
        Begründung, und die Nutzerin sähe nur, dass nichts passiert ist.
        Dasselbe Muster wie beim Warenkorb (`_korb_antwort`).
        """
        c = con()
        try:
            return vorlagen.TemplateResponse(
                request, "bons.html",
                _bon_kontext(request, c, fehler, neu, weg),
                status_code=code)
        finally:
            c.close()

    @app.get("/bons")
    def bonliste(request: Request, neu: str = "", weg: str = ""):
        return _bon_seite(request, neu=neu or None, weg=weg or None)

    @app.post("/bons")
    async def bon_hochladen(request: Request):
        """Nimmt eine Datei entgegen — oder sagt in einem Satz, warum nicht.

        Jeder Abbruch hat hier einen sichtbaren Grund. Das ist der Anlass des
        Tickets: auf der Werkbank-Seite stand am Telefon nur „Load failed",
        und dahinter steckte ein 401. Wer hier scheitert, soll lesen können,
        woran — und was zu tun ist.
        """
        # Die Länge steht im Kopf, bevor ein einziges Byte des Rumpfs gelesen
        # ist. Ein 300-MB-Video hier abzuweisen kostet nichts; es erst in den
        # Speicher zu holen, um dann „zu gross" zu sagen, wäre die teuerste
        # Art, dasselbe zu antworten. Der Zuschlag deckt Grenzen und Köpfe des
        # Formulars, die mitzählen, aber nicht zur Datei gehören.
        angekuendigt = zahl(request.headers.get("content-length"), 0)
        if angekuendigt > bonmodul.MAX_BYTES + MULTIPART_ZUSCHLAG:
            return _bon_seite(request, code=413, fehler=(
                f"Die Datei ist mit rund {angekuendigt // (1024 * 1024)} MB zu"
                f" gross, erlaubt sind {bonmodul.MAX_BYTES // (1024 * 1024)} MB."
                " Als PDF aus der Rewe- oder Lidl-App ist ein Bon deutlich"
                " kleiner als ein Foto davon."))

        teil = multipart.datei(await request.body(),
                               request.headers.get("content-type"))
        if teil is None or not teil.dateiname:
            # Ein Formular ohne gewählte Datei schickt trotzdem ein Feld, nur
            # ohne Namen und ohne Inhalt. Das ist kein Fehler der Nutzerin,
            # sondern ein vergessener Handgriff — und die Meldung sagt genau
            # das statt „ungültige Anfrage".
            return _bon_seite(request, code=400, fehler=(
                "Es war keine Datei ausgewählt. Bitte auf „Bon auswählen"
                "“ tippen und ein Bild oder PDF aussuchen."))
        try:
            name = bonmodul.speichern(app.state.bon_dir, teil.dateiname,
                                      teil.inhalt)
        except bonmodul.BonFehler as fehler:
            return _bon_seite(request, code=400, fehler=str(fehler))
        # Weiterleitung statt direkt gerenderter Seite: sonst lädt ein
        # Neuladen im Browser denselben Bon ein zweites Mal hoch, und weil der
        # Zeitstempel im Namen steckt, fällt das niemandem auf.
        return RedirectResponse(f"/bons?neu={quote(name)}", status_code=303)

    def _bon_fehlt(request: Request, name: str):
        """Dieselbe Seite für jeden Bon, den es nicht (mehr) gibt (WB-376)."""
        return _nicht_gefunden(
            request, f"Den Bon „{name}“ gibt es nicht (mehr).", _WEGE_BON)

    @app.get("/bons/{name}/loeschen")
    def bon_loeschen_fragen(request: Request, name: str):
        """Die Rückfrage vor dem Löschen (WB-376).

        Löschen ist hier endgültig im wörtlichen Sinn: die Datei liegt in
        `data/bons/`, es gibt keine zweite Kopie und keinen Papierkorb. Der
        Knopf sass auf dem Telefon unter dem Daumen, direkt neben „Auslesen".

        Die Rückfrage sagt auch, was BLEIBT — sonst rechnet sie mit dem
        grösseren Verlust und bricht ab, obwohl sie das Richtige wollte.
        """
        pfad = _bon_pfad(name)
        if pfad is None or not pfad.is_file():
            return _bon_fehlt(request, name)
        c = con()
        try:
            beleg = bonmodul.beleg_zu_datei(c, name)
            verlust = [f"Die Datei {name} wird endgültig gelöscht — sie liegt"
                       " nur hier, es gibt keine zweite Kopie."]
            bleibt = None
            if beleg:
                b = bonmodul.bilanz(c, beleg["id"])
                bleibt = (f"Die ausgelesenen Käufe bleiben: {b['posten']}"
                          f" Posten, davon {b['bestaetigt']} bestätigt. Wer"
                          " den Zettel wegwirft, will nicht seine"
                          " Einkaufshistorie löschen.")
            else:
                bleibt = ("Ausgelesen wurde dieser Bon noch nicht — es gehen"
                          " keine Käufe mit.")
            return _bestaetigen(
                request,
                titel=f"{name} löschen?",
                frage="Die Bon-Datei wird gelöscht. Das lässt sich nicht"
                      " zurücknehmen.",
                verlust=verlust,
                bleibt=bleibt,
                aktion=f"/bons/{quote(name)}/loeschen",
                knopf="Ja, Bon löschen",
                zurueck="/bons")
        finally:
            c.close()

    @app.post("/bons/{name}/loeschen")
    def bon_loeschen(request: Request, name: str):
        """Löscht einen Bon. Der zweite Schritt der Rückfrage von oben.

        Der Name kommt aus der URL und ist damit beliebig. Er wird NICHT
        bereinigt, sondern geprüft und im Zweifel abgelehnt (siehe
        `bons.pfad_im_verzeichnis`): bereinigen hiesse raten, was gemeint war,
        und beim Löschen ist Raten die falsche Antwort.

        Der Beleg bleibt stehen. Das ist Absicht: die Datei ist die Quelle,
        die bestätigten Käufe sind das Ergebnis — wer den Zettel wegwirft,
        will nicht seine Einkaufshistorie löschen.
        """
        if not bonmodul.loeschen(app.state.bon_dir, name):
            return _bon_seite(request, code=404, fehler=(
                "Diesen Bon gibt es nicht (mehr). Die Liste unten ist der"
                " aktuelle Stand."))
        app.state.bonlaeufe.vergiss(name)
        return RedirectResponse(f"/bons?weg={quote(name)}", status_code=303)

    # -- Auslesen ---------------------------------------------------------

    def _auslesen(name: str):
        """Die Arbeit eines Laufs: Datei -> Text -> Posten -> Zuordnung.

        Läuft in einem eigenen Thread und braucht deshalb eine eigene
        Datenbankverbindung — eine sqlite3-Verbindung gehört dem Thread, der
        sie geöffnet hat.

        Die Zuordnung darf scheitern, ohne den Lauf zu verlieren: schläft die
        Box, ist der Beleg trotzdem vollständig eingelesen und die Verbindung
        zum Katalog wird später nachgeholt. Umgekehrt geht es nicht — ohne
        Posten gibt es nichts zuzuordnen.
        """
        def arbeit(melde):
            pfad = _bon_pfad(name)
            if pfad is None or not pfad.is_file():
                raise bonmodul.LeseFehler(
                    f"„{name}“ liegt nicht (mehr) im Bon-Verzeichnis.")
            melde("liest die Datei")
            bon = bonmodul.zerlege(bonmodul.text_aus_datei(pfad))
            c = con()
            try:
                # `ersetzen=True`: ein zweiter Lauf über dieselbe Datei ist
                # ein NEUES Einlesen und keine Verdopplung. Die Entscheidungen
                # des vorigen Laufs gehen dabei verloren — sie gehören zu
                # Zeilen, die es danach nicht mehr gibt.
                receipt_id = bonmodul.anlegen(c, bon, datei=name,
                                              ersetzen=True)
                melde("ordnet dem Katalog zu — das Modell überlegt")
                try:
                    ergebnis = app.state.bonzuordner.zuordnen(c, receipt_id)
                    meldung = ergebnis.meldung
                except bonmodul.ZuordnungFehler as e:
                    meldung = (
                        f"{len(bon.posten)} Posten eingelesen, mit Preis und "
                        f"Datum. Die Zuordnung zum Katalog fehlt noch: {e}")
                return receipt_id, meldung
            finally:
                c.close()
        return arbeit

    @app.get("/bons/{name}/auslesen")
    def bon_auslesen_fragen(request: Request, name: str):
        """„Neu lesen" sagt vorher, was es verwirft (WB-376).

        Der Code wusste es längst — `kaeufe.anlegen(ersetzen=True)` wirft den
        früheren Beleg weg, „mitsamt seinen Entscheidungen, denn sie gehören
        zu Zeilen, die es dann nicht mehr gibt". Der Knopf sagte es nicht, und
        er sass in derselben Zeile wie das Löschen.

        Genannt wird die ZAHL, nicht bloss die Tatsache: „14 Entscheidungen"
        ist der Unterschied zwischen einer Rückfrage und einem Türsteher. Beim
        ersten Auslesen gibt es nichts zu verlieren — dann steht die Seite
        ohne Verlustliste da, und der Knopf heisst schlicht „Auslesen".
        """
        pfad = _bon_pfad(name)
        if pfad is None or not pfad.is_file():
            return _bon_fehlt(request, name)
        c = con()
        try:
            beleg = bonmodul.beleg_zu_datei(c, name)
            verlust, bleibt = [], None
            if beleg:
                b = bonmodul.bilanz(c, beleg["id"])
                entschieden = b["bestaetigt"] + b["verworfen"]
                verlust.append(
                    f"{entschieden} Entscheidungen zu diesem Bon"
                    f" ({b['bestaetigt']} bestätigt, {b['verworfen']}"
                    " verworfen) — sie gehören zu Zeilen, die es nach dem"
                    " neuen Lauf nicht mehr gibt.")
                verlust.append(
                    f"Die {b['posten']} eingelesenen Posten werden ersetzt,"
                    " samt ihrer Zuordnung zum Katalog.")
                bleibt = ("Die Bon-Datei selbst bleibt liegen. Neu gelesen"
                          " wird sie, das Modell ordnet noch einmal zu, und"
                          " die Ja/Nein fangen von vorn an.")
            return _bestaetigen(
                request,
                titel=(f"{name} neu lesen?" if beleg else f"{name} auslesen?"),
                frage=("Der Bon wird noch einmal eingelesen. Die bisherigen"
                       " Ja/Nein gehen dabei verloren." if beleg else
                       "Der Bon wird eingelesen — das dauert einen Moment und"
                       " fragt das Modell."),
                verlust=verlust,
                bleibt=bleibt,
                aktion=f"/bons/{quote(name)}/auslesen",
                knopf="Ja, neu lesen" if beleg else "Auslesen",
                zurueck=f"/bons/{quote(name)}" if beleg else "/bons")
        finally:
            c.close()

    @app.post("/bons/{name}/auslesen")
    def bon_auslesen(request: Request, name: str):
        """Startet den Lauf und leitet zur Bon-Ansicht weiter.

        Kehrt sofort zurück, auch wenn der Lauf Minuten dauert (siehe
        `bons.lauf`). Zweimal Tippen startet nicht zweimal.
        """
        pfad = _bon_pfad(name)
        if pfad is None or not pfad.is_file():
            return _bon_seite(request, code=404, fehler=(
                "Diesen Bon gibt es nicht (mehr). Die Liste unten ist der"
                " aktuelle Stand."))
        app.state.bonlaeufe.starte(name, _auslesen(name))
        return RedirectResponse(f"/bons/{quote(name)}", status_code=303)

    def _bonstand_kontext(c: sqlite3.Connection, name: str,
                          fehler: str | None = None) -> dict:
        """Alles, was `_bonstand.html` braucht — für Vollseite und Bruchstück.

        Eine Funktion für beide Wege, aus demselben Grund wie beim Warenkorb:
        sonst entwickelt sich das Bruchstück von der ersten Ansicht weg und
        niemand merkt es.
        """
        beleg = bonmodul.beleg_zu_datei(c, name)
        zeilen = (_posten_mit_bild(bonmodul.posten(c, beleg["id"]),
                                   app.state.image_dir) if beleg else [])
        return {
            "name": name,
            "lauf": app.state.bonlaeufe.stand(name),
            "nachfrage_s": bonmodul.NACHFRAGE_S,
            "beleg": beleg,
            "zeilen": zeilen,
            "bilanz": bonmodul.bilanz(c, beleg["id"]) if beleg else None,
            "laden_titel": bonmodul.LADEN_TITEL,
            "fehler": fehler,
        }

    def _bonstand_antwort(request: Request, c: sqlite3.Connection, name: str,
                          fehler: str | None = None, code: int = 200):
        """HTMX bekommt das Bruchstück, ein Formular ohne JavaScript die Seite."""
        kontext = _bonstand_kontext(c, name, fehler)
        if ist_htmx(request):
            return vorlagen.TemplateResponse(request, "_bonstand.html",
                                             kontext, status_code=code)
        return vorlagen.TemplateResponse(
            request, "bon.html", {**_rahmen(request, c), **kontext},
            status_code=code)

    @app.get("/bons/{name}")
    def bon_ansicht(request: Request, name: str):
        """Die Ansicht eines Bons — und nur, wenn es ihn gibt (WB-376).

        Vorher wurde hier bloss die NAMENSGÜLTIGKEIT geprüft. `/bons/
        gibtesnicht.pdf` antwortete deshalb mit 200 und einer Geisterseite
        über eine Datei, die nie existiert hat, samt Auslesen-Knopf, der dann
        an derselben Prüfung scheiterte. Das trifft sie im Alltag: die
        Bon-Ansicht auf dem zweiten Telefon, während der Bon auf dem ersten
        gelöscht wird.

        Ein gültiger Name OHNE Datei ist derselbe Fall wie ein ungültiger —
        beide Male gibt es den Bon nicht, und beide bekommen dieselbe Seite.
        """
        c = con()
        try:
            pfad = _bon_pfad(name)
            if pfad is None or not pfad.is_file():
                return _bon_fehlt(request, name)
            return _bonstand_antwort(request, c, name)
        finally:
            c.close()

    @app.get("/bons/{name}/stand")
    def bon_stand(request: Request, name: str):
        """Das Bruchstück, das die Seite alle paar Sekunden nachlädt.

        Solange der Lauf läuft, trägt die Antwort ihren eigenen nächsten
        Auslöser (`hx-trigger` in `_bonstand.html`) — ist er fertig, trägt sie
        keinen mehr und das Nachfragen hört von selbst auf.
        """
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "_bonstand.html",
                                             _bonstand_kontext(c, name))
        finally:
            c.close()

    # -- Zuordnung bestätigen oder korrigieren ----------------------------
    #
    # Dieselbe Ja/Nein-Geste wie beim Chat, und aus demselben Grund: nichts
    # gilt, was nicht bestätigt wurde. Ein falsch zugeordneter Kauf verfälscht
    # die Vorlieben dauerhaft, und man sieht es ihm später nicht an.

    def _posten_aktion(request: Request, c: sqlite3.Connection, item_id: int,
                       tun):
        """Eine Entscheidung an einer Bon-Zeile, mit den zwei Fehlerwegen.

        Sie sind verschieden und dürfen nicht zusammenfallen: eine Zeile, die
        es nicht gibt, hat auch keine Bon-Ansicht, in der eine Meldung stehen
        könnte (404 auf der Bon-Liste). Eine Zeile, die es gibt, deren
        Entscheidung aber abgelehnt wird — „Ja" zu einer Zeile ohne Produkt —,
        bekommt ihre Ansicht mitsamt Begründung zurück (400). Eine
        Weiterleitung verlöre die Begründung, wie überall sonst im Shop.
        """
        try:
            zeile = bonmodul.posten_zeile(c, item_id)
            datei = bonmodul.beleg(c, zeile["receipt_id"])["file_name"] or ""
        except bonmodul.KaufFehler as e:
            return _bon_seite(request, code=404, fehler=str(e))
        try:
            tun()
        except bonmodul.KaufFehler as e:
            return _bonstand_antwort(request, c, datei, fehler=str(e), code=400)
        return _bonstand_antwort(request, c, datei)

    @app.post("/bons/posten/{item_id}/entscheiden")
    def bon_entscheiden(request: Request, item_id: int, decision: str = ""):
        c = con()
        try:
            return _posten_aktion(
                request, c, item_id,
                lambda: bonmodul.entscheiden(c, item_id, decision))
        finally:
            c.close()

    @app.post("/bons/posten/{item_id}/korrigieren")
    def bon_korrigieren(request: Request, item_id: int, produkt_id: int = 0):
        """Setzt von Hand ein anderes Produkt an die Zeile und bestätigt sie."""
        c = con()
        try:
            return _posten_aktion(
                request, c, item_id,
                lambda: bonmodul.korrigieren(c, item_id, produkt_id))
        finally:
            c.close()

    @app.get("/bons/posten/{item_id}/suche")
    def bon_suche(request: Request, item_id: int, q: str = ""):
        """Kandidaten zum Korrigieren — dieselbe Suche wie überall sonst.

        Ohne Modell und ohne Wartezeit: das hier ist die Handbewegung, mit der
        die Nutzerin einen Fehlgriff des Modells geradezieht, und sie soll
        sofort antworten.
        """
        c = con()
        try:
            zeile = bonmodul.posten_zeile(c, item_id)
            begriff = q.strip() or zeile["note"] or zeile["bon_text"]
            treffer = _mit_bild(search.search(c, begriff, limit=KORREKTUREN),
                                app.state.image_dir)
            return vorlagen.TemplateResponse(request, "_bonsuche.html", {
                "zeile": zeile, "q": q or begriff, "treffer": treffer})
        except bonmodul.KaufFehler as e:
            return _bon_seite(request, code=404, fehler=str(e))
        finally:
            c.close()

    def _bilddatei_zu(produkt_id: int) -> Path | None:
        c = con()
        try:
            row = c.execute("SELECT image_path FROM product WHERE id = ?",
                            (produkt_id,)).fetchone()
        finally:
            c.close()
        return bilddatei(app.state.image_dir, row["image_path"] if row else None)

    @app.get("/bild/{produkt_id}")
    def bild(request: Request, produkt_id: int):
        """Das Bild für eine Kachel — die Miniatur, nicht das Original (WB-374).

        Fehlt die Miniatur, geht das Original raus. Das ist der Zustand direkt
        nach einem Crawl und zwischen zwei Läufen von `zettel.miniaturen`:
        die Kachel ist dann richtig und nur teuer, statt leer zu bleiben.
        """
        datei = _bilddatei_zu(produkt_id)
        if datei is None:
            # 404 statt Platzhalterbild: die Vorlage fragt Bilder gar nicht
            # erst an, die es nicht gibt — kommt trotzdem eine Anfrage, ist
            # das ein Fehler und soll wie einer aussehen. Ausdrücklich NICHT
            # zwischenspeicherbar: ein Bild, das der nächste Crawl nachliefert,
            # soll nicht eine Woche lang als fehlend im Browser stehen.
            return Response(status_code=404,
                            headers={"Cache-Control": "no-store"})
        return _bild_antwort(
            request,
            miniaturen.vorhandene(app.state.image_dir, datei) or datei)

    @app.get("/bild/{produkt_id}/original")
    def bild_original(request: Request, produkt_id: int):
        """Das ungerechnete Originalfoto, falls jemand genauer hinsehen will.

        Nicht die Vorgabe der Kachel und bewusst ein eigener Weg: `/bild/{id}`
        soll das Billige liefern, ohne dass ein Aufrufer daran denken muss.
        """
        datei = _bilddatei_zu(produkt_id)
        if datei is None:
            return Response(status_code=404,
                            headers={"Cache-Control": "no-store"})
        return _bild_antwort(request, datei)

    return app


app = create_app()


if __name__ == "__main__":
    serve()
