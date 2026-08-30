#!/usr/bin/env python3
"""Handprobe WB-402: was ein Rezeptwechsel kostet — in Sekunden und in Pixeln.

KEIN Test. Die Zusicherungen (Tauschziel, Karte ohne Modell, `show:`, der
Indikator am getauschten Stück) stehen in `tests/test_rezeptwechsel.py` und
laufen ohne Netz. Hier geht es um die ZAHLEN am echten Aufbau: echte Box,
echter Katalog, echter Chefkoch, Firefox über geckodriver in Handybreite.

    .venv/bin/python scripts/dreh/wechsel_probe.py <basis-url> <source_id>

Gemessen wird, was das Ticket verlangt:

    Dauer bis die KARTE wechselt        soll: sofort
    Dauer bis die VORSCHLÄGE stehen     darf dauern
    scrollY vorher / nachher            die Seite darf nicht wegspringen
    Seitenhöhe vorher / nachher
    Antwortgrösse je Request            aus der Performance-API des Browsers

Der Aufbau ist derselbe wie in `shot.py`: rohes WebDriver-Protokoll, Fenster
500 px (Firefox lässt nichts darunter zu), Dokument auf 390 px gesetzt.
"""
import json
import sys
import time
import urllib.request

DRIVER = "http://127.0.0.1:4455"
BREITE, HOEHE = 390, 844
#: Wie lange auf die Vorschläge gewartet wird. Zwei Modellstufen an einer
#: 27B-Box liegen bei 20 bis 35 s; 120 s ist reichlich und endlich.
GEDULD_S = 120
TAKT_S = 0.1


def ruf(methode, pfad, daten=None):
    req = urllib.request.Request(
        DRIVER + pfad,
        data=json.dumps(daten).encode() if daten is not None else None,
        headers={"Content-Type": "application/json"}, method=methode)
    with urllib.request.urlopen(req, timeout=180) as a:
        return json.loads(a.read())["value"]


def session():
    caps = {"capabilities": {"alwaysMatch": {
        "moz:firefoxOptions": {
            "args": ["-headless"],
            "prefs": {
                "layout.css.prefers-color-scheme.content-override": 1,
                "ui.systemUsesDarkTheme": 0,
            },
        }}}}
    sid = ruf("POST", "/session", caps)["sessionId"]
    ruf("POST", f"/session/{sid}/window/rect",
        {"width": 500, "height": HOEHE})
    return sid


def skript(sid, js, args=None):
    return ruf("POST", f"/session/{sid}/execute/sync",
               {"script": js, "args": args or []})


#: Was in JEDER Probe festgehalten wird. Eine Zeichenkette, weil sie zweimal
#: gebraucht wird: einmal je Takt während des Wechsels und einmal davor.
LAGE = """
const w = document.getElementById(arguments[0]);
const karten = [...document.querySelectorAll('.zugrezept h3')];
const sicht = e => { const r = e.getBoundingClientRect();
                     return r.top < innerHeight && r.bottom > 0; };
return {
  t: performance.now(),
  scrollY: Math.round(scrollY),
  hoehe: document.body.scrollHeight,
  wechsel: !!w,
  wechsel_sichtbar: w ? sicht(w) : false,
  titel: karten.map(h => h.textContent.trim()),
  sichtbare_titel: karten.filter(sicht).map(h => h.textContent.trim()),
  zuege: [...document.querySelectorAll('.zug')].map(z => z.id),
  vorschlaege: document.querySelectorAll('.vorschlaege .zeile').length,
  // Sagt IRGENDETWAS im Bild, dass gerade etwas läuft? Der Indikator aus
  // WB-378 (`#chat-laeuft`) steht am Seitenfuss; in 201 Proben über 24 s war
  // er kein einziges Mal zu sehen. Das ist die Zahl, die das Ticket meint.
  laeuft_sichtbar: [...document.querySelectorAll(
      '.htmx-indicator, .zugband.laeuft')]
    .filter(e => getComputedStyle(e).display !== 'none' && sicht(e)).length,
};
"""


def main():
    basis = sys.argv[1]
    ziel = sys.argv[2]
    sid = session()
    try:
        ruf("POST", f"/session/{sid}/url", {"url": basis + "/chat"})
        for _ in range(60):
            if skript(sid, "return document.readyState") == "complete":
                break
            time.sleep(0.25)
        skript(sid, "document.documentElement.style.width = '390px';")
        time.sleep(1.0)

        # Der Knopf, den ein Mensch antippt: die Alternative im LETZTEN Zug.
        gefunden = skript(sid, """
const knoepfe = [...document.querySelectorAll('.andereliste button.wahl')]
  .filter(b => b.value === arguments[0]);
if (!knoepfe.length) return null;
const b = knoepfe[knoepfe.length - 1];
const zug = b.closest('.zug');
b.scrollIntoView({block: 'center'});
b.id = 'probe-knopf';
return {zug: zug.id, titel: b.textContent.trim().split('\\n')[0]};
""", [ziel])
        if gefunden is None:
            print(f"Kein Knopf mit value={ziel} auf der Seite.")
            return
        mid = gefunden["zug"].split("-", 1)[1]
        time.sleep(0.6)

        vorher = skript(sid, LAGE, [f"wechsel-{mid}"])
        skript(sid, "performance.clearResourceTimings();")
        t0 = time.time()
        skript(sid, "document.getElementById('probe-knopf').click();")

        proben = []
        karte_da = vorschlaege_da = None
        alt_zuege = set(vorher["zuege"])
        while time.time() - t0 < GEDULD_S:
            p = skript(sid, LAGE, [f"wechsel-{mid}"])
            p["dt"] = time.time() - t0
            proben.append(p)
            if karte_da is None and p["wechsel"] and p["wechsel_sichtbar"]:
                karte_da = p["dt"]
            neue = [z for z in p["zuege"] if z not in alt_zuege]
            if vorschlaege_da is None and neue and not p["wechsel"]:
                vorschlaege_da = p["dt"]
                break
            time.sleep(TAKT_S)
        # htmx scrollt mit `behavior: smooth` — die Endlage steht erst nach
        # der Animation fest. Eine Probe unmittelbar nach dem Tausch fienge
        # den Blick mitten im Flug.
        time.sleep(2.0)
        nachher = skript(sid, LAGE, [f"wechsel-{mid}"])

        netz = skript(sid, """
return performance.getEntriesByType('resource')
  .filter(e => e.initiatorType === 'xmlhttprequest'
               || e.name.includes('/chat/'))
  .map(e => ({name: e.name, dauer: Math.round(e.duration),
              bytes: e.decodedBodySize}));
""")

        print(f"Zug              #{gefunden['zug']}  ->  {gefunden['titel']}")
        print(f"Proben           {len(proben)} über {proben[-1]['dt']:.1f} s")
        print(f"Karte im Bild    "
              f"{'%.2f s' % karte_da if karte_da is not None else 'NIE'}")
        fertig = ("%.2f s" % vorschlaege_da
                  if vorschlaege_da is not None else "NIE")
        print(f"Vorschläge da    {fertig}")
        print(f"scrollY          {vorher['scrollY']} -> {nachher['scrollY']}")
        print(f"Seitenhöhe       {vorher['hoehe']:,}"
              f" -> {nachher['hoehe']:,} px")
        print(f"sichtbar vorher  {vorher['sichtbare_titel']}")
        print(f"sichtbar nachher {nachher['sichtbare_titel']}")
        print(f"Züge vorher      {vorher['zuege']}")
        print(f"Züge nachher     {nachher['zuege']}")
        sicht = sum(1 for p in proben if p["wechsel_sichtbar"])
        print(f"Wechselkasten    in {sicht} von {len(proben)} Proben im Bild")
        laeuft = sum(1 for p in proben if p["laeuft_sichtbar"])
        print(f"„läuft“ im Bild  in {laeuft} von {len(proben)} Proben")
        for e in netz:
            if "/chat" in e["name"]:
                print(f"  {e['dauer']:>7} ms  {e['bytes']:>9,} B  "
                      f"{e['name'].split('8748')[-1]}")
    finally:
        ruf("DELETE", f"/session/{sid}")


if __name__ == "__main__":
    main()
