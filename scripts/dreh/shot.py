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
    # Die Kopfleiste rastet beim Laden auf eine ganze Kante — gerechnet mit
    # der Breite, die das Dokument DANN hat, und das sind hier noch 500.
    # Nach dem Zwang auf 390 muss dieselbe Rechnung noch einmal laufen,
    # sonst zeigt das Bild einen Bildlauf für eine Breite, die kein Telefon
    # hat (WB-400 Runde 3).
    skript(sid, "if (window.leisteRasten) { window.leisteRasten(); }")
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

SEITEN = [
    ("chat",        "/chat", None),
    ("chat-alternativen", "/chat",
     "document.querySelectorAll('details.alternativen')"
     ".forEach(d => d.open = true);"),
    ("warenkorb",   "/warenkorb", None),
    ("katalog",     "/katalog", None),
    ("katalog-suche", "/katalog?q=milch", None),
    ("pick",        "/pick", None),
    ("rezept",      "/rezepte/1", None),
    ("rezepte",     "/rezepte", None),
    ("bestellungen", "/bestellungen", None),
    ("status",      "/status", None),
    ("bons",        "/bons", None),
    ("vierohvier",  "/rezepte/99", None),
    ("frage",       "/rezepte/1/loeschen", None),
]

if __name__ == "__main__":
    ziel = sys.argv[1]
    nur = sys.argv[2].split(",") if len(sys.argv) > 2 else None
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
