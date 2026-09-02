#!/usr/bin/env python3
"""Misst, was sich an Bildern schlecht beurteilen laesst (WB-400 Runde 3).

Sechs Fragen je Seite und Modus, gegen dieselbe Instanz wie `shot.py`:

* scrollt die Seite auf 390 px waagerecht?
* gibt es ein Tap-Ziel unter 44 px?  (Ein Link MITTEN IN EINEM SATZ meldet
  sich hier und ist trotzdem in Ordnung — WCAG 2.5.8 nimmt ihn aus.)
* gibt es ein Eingabefeld unter 16 px?  (Ein Kaestchen meldet sich hier und
  ist trotzdem in Ordnung: es nimmt keinen Text auf.)
* steht Text unter 4,5:1 gegen seinen Grund?  Seit Runde 4 rechnet die
  Probe die Deckkraft der Vorfahren mit: `.zug.ersetzt { opacity: 0.85 }`
  drückte 82 Texte unter die Schwelle, und die alte Formel sah davon
  nichts, weil sie nur `color` gegen `background-color` hielt.
* wie viele VERSCHIEDENE Abstaende hat das Bild einer Zeile zu ihrem Text?
  Mehr als einer heisst: die linke Spalte franst aus.
* liegt die Leiste unten am Fenster, und ist jeder Reiter ganz im Bild?

Und EINE Frage an die Buehne selbst (Runde 4): /chat muss einen ERSETZTEN
Zug zeigen. Bis dahin besuchte der Stand nur Seiten ohne einen, und die
schlechteste Flaeche der Anwendung — die Quittung aus WB-403 — war in
keiner Messung zu sehen. Fehlt sie, meldet der Stand das laut, statt
vakuumgruen zu sein; `stage.py` baut sie auf, notfalls ohne Modell.

Voraussetzung: geckodriver auf 4455 und die Vorfuehr-Instanz auf 8748,
Aufbau wie in `shot.py` beschrieben.
"""
import json, sys, time, urllib.request

DRIVER = "http://127.0.0.1:4455"
BASIS = "http://127.0.0.1:8748"

def ruf(m, p, d=None):
    req = urllib.request.Request(
        DRIVER + p, data=json.dumps(d).encode() if d is not None else None,
        headers={"Content-Type": "application/json"}, method=m)
    with urllib.request.urlopen(req, timeout=90) as a:
        return json.loads(a.read())["value"]

def session(dunkel):
    caps = {"capabilities": {"alwaysMatch": {"moz:firefoxOptions": {
        "args": ["-headless"],
        "prefs": {
            "layout.css.prefers-color-scheme.content-override": 0 if dunkel else 1,
            "ui.systemUsesDarkTheme": 1 if dunkel else 0,
        }}}}}
    s = ruf("POST", "/session", caps)
    sid = s["sessionId"]
    ruf("POST", f"/session/{sid}/window/rect", {"width": 500, "height": 844})
    return sid

def js(sid, s):
    return ruf("POST", f"/session/{sid}/execute/sync", {"script": s, "args": []})

PROBE = r"""
function lum(c){
  var m = c.match(/[\d.]+/g).map(Number);
  var f = m.slice(0,3).map(function(v){v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4);});
  return 0.2126*f[0]+0.7152*f[1]+0.0722*f[2];
}
function grund(el){
  var e = el;
  while (e) {
    var b = getComputedStyle(e).backgroundColor;
    if (b && !/rgba\(0, 0, 0, 0\)|transparent/.test(b)) return b;
    e = e.parentElement;
  }
  return "rgb(255,255,255)";
}
/* Die Deckkraft ALLER Vorfahren (Runde 4): `opacity` am Kasten wirkt auf
   jeden Text darin, steht aber an keinem der Texte selbst. Die Schrift wird
   dafuer rechnerisch in den Grund gemischt — dieselbe Mischung, die der
   Bildschirm zeigt. */
function deckkraft(el){
  var o = 1, e = el;
  while (e) { o *= parseFloat(getComputedStyle(e).opacity || 1); e = e.parentElement; }
  return o;
}
function mischen(vg, bg, o){
  var a = vg.match(/[\d.]+/g).map(Number), b = bg.match(/[\d.]+/g).map(Number);
  return "rgb(" + [0,1,2].map(function(i){return Math.round(a[i]*o + b[i]*(1-o));}).join(",") + ")";
}
function kontrast(el){
  var g = grund(el), o = deckkraft(el);
  var vg = getComputedStyle(el).color;
  if (o < 1) vg = mischen(vg, g, o);
  var a = lum(vg), b = lum(g);
  var hi = Math.max(a,b), lo = Math.min(a,b);
  return (hi+0.05)/(lo+0.05);
}
document.documentElement.style.width = '390px';
var out = {breite: document.body.scrollWidth, klein: [], feld: [], kontrast: [], zeilen: []};
document.querySelectorAll('a,button,summary,input[type=checkbox],select,[role=button]').forEach(function(e){
  var r = e.getBoundingClientRect();
  if (r.width < 1 && r.height < 1) return;
  if (r.height < 44 || r.width < 24) out.klein.push([e.tagName+'.'+e.className, Math.round(r.width), Math.round(r.height), (e.textContent||'').trim().slice(0,28)]);
});
document.querySelectorAll('input,select,textarea').forEach(function(e){
  var fs = parseFloat(getComputedStyle(e).fontSize);
  if (fs < 16) out.feld.push([e.name||e.type, fs]);
});
document.querySelectorAll('main *').forEach(function(e){
  if (!e.childNodes.length) return;
  var hatText = Array.prototype.some.call(e.childNodes, function(n){return n.nodeType===3 && n.textContent.trim().length>2;});
  if (!hatText) return;
  var r = e.getBoundingClientRect(); if (r.height < 1) return;
  var k = kontrast(e);
  if (k < 4.5) out.kontrast.push([e.tagName+'.'+String(e.className).slice(0,30), Math.round(k*100)/100, (e.textContent||'').trim().slice(0,40)]);
});
document.querySelectorAll('.zeile').forEach(function(z){
  var b = z.querySelector('.bild'), t = z.querySelector('.text');
  if (!b || !t) return;
  var rb = b.getBoundingClientRect(), rt = t.getBoundingClientRect();
  out.zeilen.push([Math.round(rb.top - rt.top), (t.textContent||'').trim().slice(0,22)]);
});
out.ersetzt = document.querySelectorAll('.zug.ersetzt').length;
var leiste = document.querySelector('.leiste');
if (leiste) {
  var lr = leiste.getBoundingClientRect();
  /* Die Leiste muss am unteren Rand liegen und jeder Reiter ganz im Bild —
     ein Reiter, dessen Wort breiter ist als sein Fünftel, ist abgeschnitten. */
  out.leiste = {unten: Math.round(window.innerHeight - lr.bottom), hoehe: Math.round(lr.height), kanten: []};
  leiste.querySelectorAll('a').forEach(function(a){
    var r = a.getBoundingClientRect();
    if (r.left < -0.5 || r.right > window.innerWidth + 0.5 || a.scrollWidth > a.clientWidth + 0.5) out.leiste.kanten.push([a.textContent.trim(), Math.round(r.left), Math.round(r.right), a.scrollWidth, a.clientWidth]);
  });
}
return JSON.stringify(out);
"""

SEITEN = ["/chat", "/warenkorb", "/katalog", "/katalog?q=milch", "/pick",
          "/rezepte/1", "/rezepte", "/bestellungen", "/status", "/bons",
          "/rezepte/99", "/rezepte/1/loeschen", "/mehr"]

if __name__ == "__main__":
    for dunkel in (False, True):
        sid = session(dunkel)
        modus = "dunkel" if dunkel else "hell"
        try:
            for p in SEITEN:
                ruf("POST", f"/session/{sid}/url", {"url": BASIS + p})
                time.sleep(0.9)
                d = json.loads(js(sid, PROBE))
                kopf = f"--- {modus} {p}"
                zeilen = []
                if d["breite"] > 390:
                    zeilen.append(f"  QUER: scrollWidth {d['breite']}")
                for k in d["klein"]:
                    zeilen.append(f"  klein: {k}")
                for f in d["feld"]:
                    zeilen.append(f"  feld<16: {f}")
                for k in d["kontrast"]:
                    zeilen.append(f"  kontrast: {k}")
                versatz = sorted({z[0] for z in d["zeilen"]})
                if len(versatz) > 1:
                    zeilen.append(f"  bild-versatz zu text-oben: {versatz}")
                if d.get("leiste", {}).get("kanten") or d.get("leiste", {}).get("unten"):
                    zeilen.append(f"  leiste: {d['leiste']}")
                # Die Quittung MUSS im Bild sein (Runde 4): ohne einen
                # ersetzten Zug misst der Stand an /chat nur die halbe
                # Seite und meldet gruen, was er nie gesehen hat.
                if p == "/chat" and not d.get("ersetzt"):
                    zeilen.append("  FEHLT: kein ersetzter Zug auf /chat"
                                  " — Buehne mit stage.py aufbauen, sonst"
                                  " ist die Quittung wieder unsichtbar")
                print(kopf)
                for z in zeilen:
                    print(z)
        finally:
            ruf("DELETE", f"/session/{sid}")
