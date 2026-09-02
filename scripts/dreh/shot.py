#!/usr/bin/env python3
"""Handybreite Screenshots über geckodriver (WebDriver-Protokoll, roh).

Kein selenium: das Protokoll ist HTTP+JSON, und die vier Aufrufe, die es
braucht (Session, Navigate, Script, Screenshot), sind von Hand kürzer als
eine neue Abhängigkeit. `moz/screenshot/full` liefert die GANZE Seite;
vorher wird durchgescrollt, sonst bleiben die `loading="lazy"`-Bilder weiss
(Befund WB-389)."""
import base64, json, subprocess, sys, time, urllib.request

DRIVER = "http://127.0.0.1:4455"
BASIS = "http://127.0.0.1:8748"
BREITE, HOEHE = 390, 844

def ruf(methode, pfad, daten=None):
    req = urllib.request.Request(
        DRIVER + pfad,
        data=json.dumps(daten).encode() if daten is not None else None,
        headers={"Content-Type": "application/json"}, method=methode)
    with urllib.request.urlopen(req, timeout=90) as a:
        return json.loads(a.read())["value"]

def session(dunkel):
    caps = {"capabilities": {"alwaysMatch": {
        "moz:firefoxOptions": {
            "args": ["-headless"],
            "prefs": {
                # 0 = dunkel, 1 = hell — erzwungen, nicht vom System geerbt.
                "layout.css.prefers-color-scheme.content-override": 0 if dunkel else 1,
                "ui.systemUsesDarkTheme": 1 if dunkel else 0,
                # 2x, damit die Schrift im Bild scharf ist wie auf dem Handy.
                "layout.css.devPixelsPerPx": "2",
            },
        }}}}
    s = ruf("POST", "/session", caps)
    sid = s["sessionId"]
    # Firefox laesst kein Fenster unter 500 CSS-px zu. Der Ausweg: Fenster
    # bleibt 500, das Dokument wird pro Seite auf 390 px gesetzt (stil.css
    # hat keine Breiten-Media-Queries, das Layout ist damit identisch) und
    # das Bild hinterher auf 390 px beschnitten.
    ruf("POST", f"/session/{sid}/window/rect", {"width": 500, "height": HOEHE})
    return sid

def skript(sid, js, args=None):
    return ruf("POST", f"/session/{sid}/execute/sync",
               {"script": js, "args": args or []})

def schiesse(sid, pfad, datei, extra_js=None):
    ruf("POST", f"/session/{sid}/url", {"url": BASIS + pfad})
    for _ in range(40):
        if skript(sid, "return document.readyState") == "complete":
            break
        time.sleep(0.25)
    time.sleep(0.6)                      # HTMX-Nachzügler (chat-zustand)
    skript(sid, "document.documentElement.style.width = '390px';")
    # Die Leiste ist fest und misst sich am Fenster (500 px), nicht am
    # Dokument (390): im Bild wäre sie zu breit und der fünfte Reiter
    # abgeschnitten. Und ein festes Element malt Firefox in der ganzseitigen
    # Aufnahme EINMAL, an der aktuellen Scrollhöhe — gemessen: ein Band ein
    # Fünftel weit unten auf einer 3576 px langen Seite. Absolut steht sie
    # einmal am Ende des Dokuments, wo ein Leser des Bildes sie erwartet —
    # aber nur, wenn `body` ihr Bezug ist: ohne ein positioniertes Elternteil
    # rechnet `bottom: 0` gegen den Anfangsblock, der fenstergross ist, und
    # das Band lag wieder bei 702 px. Mit `body { position: relative }`
    # landet es bei 3520–3575, bündig am Ende (gemessen).
    skript(sid, "document.body.style.position = 'relative';"
                " var L = document.querySelector('.leiste');"
                " if (L) { L.style.position = 'absolute';"
                " L.style.width = '390px'; L.style.right = 'auto'; }")
    if extra_js:
        skript(sid, extra_js)
        time.sleep(0.4)
    # Durchscrollen weckt die lazy-Bilder.
    hoehe = skript(sid, "return document.body.scrollHeight")
    y = 0
    while y < hoehe:
        y += 700
        skript(sid, f"window.scrollTo(0, {y});")
        time.sleep(0.25)
        hoehe = skript(sid, "return document.body.scrollHeight")
    for _ in range(20):
        if skript(sid, "return Array.from(document.images)"
                       ".every(i => i.complete)"):
            break
        time.sleep(0.3)
    skript(sid, "window.scrollTo(0, 0);")
    time.sleep(0.3)
    schuss = ruf("GET", f"/session/{sid}/moz/screenshot/full")
    roh = datei + ".roh.png"
    with open(roh, "wb") as f:
        f.write(base64.b64decode(schuss))
    # Auf die 390 CSS-px (bei 2x: 780 Geraete-px) beschneiden.
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", roh,
                    "-vf", "crop=min(780\,iw):ih:0:0", datei], check=True)
    import os; os.unlink(roh)
    print(datei)

def rezept_id() -> int:
    """Das erste Rezept der Liste — nicht „1": die Demo-DB hat ihre Nummer 1
    verloren (Welle 2 hat einen leeren Bon-Import gelöscht), und ein Stand,
    der eine 404-Seite als Rezeptseite misst, ist stumm falsch."""
    import re as _re
    with urllib.request.urlopen(BASIS + "/rezepte", timeout=30) as a:
        m = _re.search(rb'href="/rezepte/(\d+)"', a.read())
    if not m:
        sys.exit("kein Rezept auf /rezepte — Buehne leer?")
    return int(m.group(1))

def seiten():
    r = rezept_id()
    return [
    ("chat",        "/chat", None),
    ("chat-alternativen", "/chat",
     "document.querySelectorAll('details.alternativen')"
     ".forEach(d => d.open = true);"),
    ("warenkorb",   "/warenkorb", None),
    ("katalog",     "/katalog", None),
    ("katalog-suche", "/katalog?q=milch", None),
    ("pick",        "/pick", None),
    ("rezept",      f"/rezepte/{r}", None),
    ("rezepte",     "/rezepte", None),
    ("bestellungen", "/bestellungen", None),
    ("status",      "/status", None),
    ("bons",        "/bons", None),
    ("mehr",        "/mehr", None),
    ("vierohvier",  "/rezepte/99", None),
    ("frage",       f"/rezepte/{r}/loeschen", None),
    ]

if __name__ == "__main__":
    ziel = sys.argv[1]
    nur = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    SEITEN = seiten()
    for dunkel in (False, True):
        sid = session(dunkel)
        modus = "dunkel" if dunkel else "hell"
        try:
            for name, pfad, js in SEITEN:
                if nur and name not in nur:
                    continue
                schiesse(sid, pfad, f"{ziel}/{name}-{modus}.png", js)
        finally:
            ruf("DELETE", f"/session/{sid}")
