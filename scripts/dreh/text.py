#!/usr/bin/env python3
"""Der sichtbare Text aller Seiten, Zeile fuer Zeile — der Beleg dafuer, dass
eine Gestaltungsrunde keine Aussage angefasst hat (WB-400).

„600 g gebraucht — 2 × 0,54 kg", „nicht ausrechenbar", „gab's nicht": diese
Saetze sind in eigenen Tickets erarbeitet worden und sagen die Wahrheit ueber
die Rechnung. Gestaltung darf sie umstellen und umfaerben, nicht umformulieren
— und das laesst sich belegen statt behaupten:

    .venv/bin/python scripts/dreh/text.py /tmp/nachher.txt
    git stash && .venv/bin/python scripts/dreh/text.py /tmp/vorher.txt
    git stash pop && diff /tmp/vorher.txt /tmp/nachher.txt

Aufklapper werden vorher geoeffnet, sonst fehlt der halbe Text. Voraussetzung
ist derselbe Aufbau wie bei `shot.py`.
"""
import json, sys, time, urllib.request

DRIVER = "http://127.0.0.1:4455"
BASIS = "http://127.0.0.1:8748"
SEITEN = ["/katalog", "/katalog?q=milch", "/chat", "/warenkorb", "/rezepte",
          "/rezepte/1", "/rezepte/1/loeschen", "/rezepte/99", "/bestellungen",
          "/pick", "/bons", "/status", "/rolle"]

def ruf(m, p, d=None):
    r = urllib.request.Request(
        DRIVER + p, data=json.dumps(d).encode() if d is not None else None,
        headers={"Content-Type": "application/json"}, method=m)
    return json.loads(urllib.request.urlopen(r, timeout=60).read())["value"]

sid = ruf("POST", "/session", {"capabilities": {"alwaysMatch": {
    "moz:firefoxOptions": {"args": ["-headless"]}}}})["sessionId"]
ruf("POST", f"/session/{sid}/window/rect", {"width": 500, "height": 844})
JS = ("document.querySelectorAll('details').forEach(function(d){d.open=true;});"
      "return document.body.innerText;")
try:
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        for p in SEITEN:
            ruf("POST", f"/session/{sid}/url", {"url": BASIS + p})
            time.sleep(1.2)
            for z in ruf("POST", f"/session/{sid}/execute/sync",
                         {"script": JS, "args": []}).splitlines():
                z = z.strip()
                if z:
                    f.write(z + "\n")
finally:
    ruf("DELETE", f"/session/{sid}")
