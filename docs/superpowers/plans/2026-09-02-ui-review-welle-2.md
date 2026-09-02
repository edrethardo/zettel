# UI-Review Welle 2 — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Welle-2-Funde des adversarialen UI-Reviews vom 2026-09-01 umsetzen — feste untere Tab-Leiste, eine Knopfregel, Monospace nur noch für Code, kürzere Rechensätze im Korb, gestrichene Anleitungsprosa, Einheiten wie im Kochbuch, ein Entwurf, der offen bleibt, und ein Nachtlauf, der die Knuspr-Einheiten nachzieht.

**Architecture:** Neun kleine, voneinander unabhängige Aufgaben, jede mit einem Test, der heute rot ist, einer Änderung an Vorlage, Stilblatt oder reiner Funktion und einem Commit direkt auf `master`. Die Leiste (Aufgabe 1) ist die einzige, die jede Seite ändert; sie kommt zuerst, damit alle Bildschritte danach den Endzustand zeigen. Die Demo-Datenbank (Aufgabe 9) ist eine Datenänderung ausserhalb des Repos und wird von der Steuerung selbst gemacht, nicht von einem Subagenten.

**Tech Stack:** FastAPI + Jinja2 + HTMX 1.9.12 + SQLite; Tests `.venv/bin/python -m pytest -q` (1325 grün bei `fad52ff`); CSS-als-Text-Tests mit `_block(stil, selektor)` in `tests/test_web_politur.py`.

**Entscheidungen (Aaron, 2026-09-02):** Fund 6 → (a) untere Tab-Leiste. Fund 9 → Prosa nach meinem Urteil streichen, jeder gestrichene Satz steht in der Commit-Nachricht. Fund 3 → Hostnamen auf /status bleiben. Alle anderen Zeilen nach der Empfehlung im Welle-1-Plan — mit einer begründeten Abweichung bei Fund 8 (siehe Aufgabe 4).

**Regeln für jeden Subagenten:** Commit-Sätze auf Deutsch ohne Präfix, kein `Co-Authored-By`, nur die berührten Dateien committen. Niemals `pkill -f`. Keine Hostnamen der vLLM-Box, kein Benutzername, kein Heimverzeichnis-Pfad in getrackten Dateien (`tests/test_betrieb.py::test_keine_privaten_angaben_im_repo`). In Inline-JavaScript nur Blockkommentare (WB-323). Nichts an `~/picknick-demo/demo.db` ändern.

---

## Dateiübersicht

| Datei | Aufgabe | Was sich ändert |
|---|---|---|
| `zettel/web/templates/basis.html` | 1 | Kopf nur noch Marke; `<nav class="leiste">` am Ende des `<body>`; `leisteRasten` und `<footer class="fuss">` weg |
| `zettel/web/templates/mehr.html` (neu) | 1 | Sammelseite für Rezepte, Bestellungen, Bons, Rolle, Status |
| `zettel/web/app.py` | 1, 7 | Route `/mehr`; `entwurf_offen` in `_teilantwort`/`_zug_antwort` |
| `zettel/web/static/stil.css` | 1, 2, 3, 5 | `--leiste`, `.leiste`, `.mehrliste`, `main`/`.kasse`-Abstände; Monospace nur in `code`; Knopfregel; `.gekappt` neutral |
| `tests/test_web_politur.py` | 1, 2, 3 | Leistentests neu; Mono-Test; Knopfregel-Test; `.gekappt`-Test |
| `tests/test_web_chat.py` | 1 | Docstring der Leiste (Z. 360–366) |
| `scripts/dreh/mess.py`, `scripts/dreh/shot.py` | 1 | Messung der Leiste statt der Kopfleiste; `leisteRasten`-Aufruf weg |
| `zettel/mengen.py` | 4, 5 | `kurzgrund()`, kürzere `satz()`/`nachsatz()`; `einheit_text()` + `SCHREIBWEISE` |
| `tests/test_mengen.py`, `tests/test_mengen_chat.py` | 4, 5 | Sätze neu gepinnt; Einheitenschreibweise |
| `zettel/web/templates/_korb.html` | 4 | „Laden: " im Auswahlfeld |
| `zettel/recipes/sammlung.py`, `zettel/web/templates/_entwurf.html`, `_chat.html`, `status.html` | 5 | Einheit im Feld; Wartezeile grösser; „Der eine Zug" |
| `zettel/web/templates/warenkorb.html`, `rezept.html`, `_rezept.html`, `bons.html`, `status.html`, `katalog.html` | 6 | Prosa gestrichen |
| `zettel/web/templates/_entwurf.html`, `bestellungen.html` | 7 | `<details open>` nach Entwurfsaktion; Link zur Pick-Liste in der Quittung |
| `zettel/scrapers/nachtlauf.py`, `tests/test_miniaturen.py` | 8 | `repariere_einheiten` nach dem Crawl |
| `~/picknick-demo/demo.db` | 9 | Rezepte 1, 25, 26 samt Zutaten weg (Kopie, dann zurück) |

---

### Aufgabe 1: Die untere Tab-Leiste (Fund 6a, Fund 16 `.korbzahl`)

**Warum:** Sieben Ziele in einer quer scrollenden Kopfleiste — mit Maske, Rast-Skript und Bildlaufleiste, drei Runden Feinarbeit in WB-400 — passen auf kein Telefon. Eine feste Leiste am unteren Rand mit fünf Reitern (Katalog · Chat · Korb · Pick-Liste · Mehr) ist auf jedem Telefon vollständig sichtbar, liegt unter dem Daumen, und alles, was heute quer scrollt, rastet und maskiert, fällt ersatzlos weg. „Mehr" ist eine eigene Seite `/mehr` mit Rezepten, Bestellungen, Bons — und darunter, leiser, Rolle und Status (die Fusszeile aus Welle 1 geht darin auf).

**Files:**
- Modify: `zettel/web/templates/basis.html`
- Create: `zettel/web/templates/mehr.html`
- Modify: `zettel/web/app.py` (Route neben `/rolle`, Z. 721)
- Modify: `zettel/web/static/stil.css` (`:root`-Token; `main` Z. 163; `.kopf nav`-Regeln Z. 212–267; `.fuss` Z. 272–282; `.korbzahl` Z. 466–472; `.kasse` Z. 578–583)
- Modify: `tests/test_web_politur.py` (Z. 32–33, 165–256)
- Modify: `tests/test_web_chat.py` (Z. 360–366, nur Docstring)
- Modify: `scripts/dreh/mess.py` (Z. 92, 118–130, 160–162), `scripts/dreh/shot.py` (Z. 56–61)

- [ ] **Schritt 1: Die Tests umschreiben**

In `tests/test_web_politur.py` die Zeilen 32–33 ersetzen:

```python
VOLLSEITEN = ["/katalog", "/chat", "/warenkorb", "/rezepte", "/bestellungen",
              "/pick", "/bons", "/status", "/rolle", "/mehr"]

#: Welcher Reiter der Leiste auf einer Seite markiert ist. Fünf Reiter für
#: zehn Seiten: was nicht selbst einen hat, gehört unter „Mehr".
REITER = {"/rezepte": "/mehr", "/bestellungen": "/mehr", "/bons": "/mehr",
          "/status": "/mehr", "/rolle": "/mehr"}
```

Den Test `test_jede_vollseite_markiert_sich_selbst_in_der_navigation` (Z. 165–171) ersetzen:

```python
@pytest.mark.parametrize("pfad", VOLLSEITEN)
def test_jede_vollseite_markiert_ihren_reiter(client, pfad):
    text = client.get(pfad).text
    treffer = re.findall(r'<a href="([^"]+)" class="aktiv" aria-current="page"',
                         text)
    assert treffer == [REITER.get(pfad, pfad)], (
        f"{pfad} markiert {treffer} statt {REITER.get(pfad, pfad)}")
```

Die Tests von `test_die_markierung_ist_nicht_nur_ansage_sondern_auch_sichtbar` (Z. 186) bis einschliesslich `test_das_letzte_ziel_der_leiste_ist_erreichbar` (Z. 256, endet vor `test_das_foto_faengt_auf_derselben_hoehe_an_wie_der_name`) durch diesen Block ersetzen:

```python
def test_die_markierung_ist_nicht_nur_ansage_sondern_auch_sichtbar():
    """`aria-current` hört ein Vorleseprogramm, sehen kann man es nicht.
    Der Reiter bekommt Farbe UND eine Kante — ein Farbton allein ist auf
    einem Telefon in der Sonne kein Unterschied."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".leiste a.aktiv")
    assert "color" in block and "box-shadow" in block


def test_die_leiste_hat_fuenf_reiter_und_liegt_unter_dem_blatt(client):
    """Sieben Ziele passten in keine 390 px (UI-Review 2026-09-01, Fund 6).
    Fünf Reiter passen; alles Weitere sammelt „Mehr". Die Leiste steht im
    HTML NACH `<main>`: sie ist fest am unteren Rand, und ein
    Vorleseprogramm soll erst den Inhalt hören."""
    text = client.get("/katalog").text
    leiste = text.split('<nav class="leiste" id="hauptnavigation"', 1)[1]
    leiste = leiste.split("</nav>", 1)[0]
    assert re.findall(r'<a href="([^"]+)"', leiste) == [
        "/katalog", "/chat", "/warenkorb", "/pick", "/mehr"]
    assert 'id="korb-anzahl"' in leiste
    assert text.index("</main>") < text.index('<nav class="leiste"')
    assert '<footer class="fuss"' not in text
    assert "leisteRasten" not in text


def test_mehr_sammelt_was_keinen_reiter_hat(client):
    """Rezepte, Bestellungen, Bons — und darunter, leiser, Rolle und Status:
    die Fusszeile aus Welle 1 geht in dieser Seite auf."""
    text = client.get("/mehr").text
    seite = text.split("<main>", 1)[1].split("</main>", 1)[0]
    assert re.findall(r'<a href="([^"]+)"', seite) == [
        "/rezepte", "/bestellungen", "/bons", "/rolle", "/status"]


def test_eine_seite_unter_mehr_markiert_den_reiter_mehr(client):
    text = client.get("/rezepte").text
    assert '<a href="/mehr" class="aktiv" aria-current="page"' in text
    assert '<a href="/rezepte" class="aktiv"' not in text


def test_die_leiste_ist_fest_und_das_blatt_macht_ihr_platz():
    """Eine feste Leiste deckt die unteren 56 px des Fensters. Was darunter
    liegt, ist unerreichbar: das Blatt bekommt unten den Abstand der Leiste,
    und die klebende Kasse setzt sich auf sie statt unter sie."""
    stil = STIL.read_text(encoding="utf-8")
    leiste = _block(stil, ".leiste")
    assert "position: fixed" in leiste and "bottom: 0" in leiste
    assert "var(--leiste)" in _block(stil, "main")
    assert "bottom: var(--leiste)" in _block(stil, ".kasse")
    assert "scroll-snap" not in stil and "mask-image" not in stil
    # `.fussnote` bleibt; die Regeln `.fuss`, `.fuss a`, `.kopf nav …` gehen.
    assert not re.search(r"^\.fuss\b", stil, re.M)
    assert not re.search(r"^\.kopf nav", stil, re.M)
```

- [ ] **Schritt 2: Tests laufen lassen — rot**

Run: `.venv/bin/python -m pytest -q tests/test_web_politur.py -k "reiter or leiste or mehr or markierung"`
Expected: FAIL — `/mehr` antwortet 404, `.leiste` steht nicht im Blatt, `_block` findet `.leiste a.aktiv` nicht.

- [ ] **Schritt 3: `basis.html` umbauen**

Den `<body>` von `basis.html` (ab `<body>` bis `</html>`) durch diesen ersetzen — der `<head>` bleibt, nur der Kommentar zu `viewport-fit=cover` (Z. 10–16) nennt statt `.fuss` jetzt `.leiste`:

```html
<body>
  {# Wo bin ich? Die Klasse ist der Teil, den man sieht, `aria-current="page"`
     der, den ein Vorleseprogramm ansagt; beides zusammen, weil Farbe allein
     für beides nicht reicht.

     Verglichen wird mit Präfix, damit /pick/12 und /rezepte/3 ihren Reiter
     mitmarkieren — sonst wäre die Markierung genau auf den Unterseiten weg,
     auf denen man sich am ehesten verläuft. #}
  {%- set hier = request.url.path if request else "" %}
  {%- macro wo(pfad) %}{% if hier == pfad or hier.startswith(pfad ~ "/") %} class="aktiv" aria-current="page"{% endif %}{% endmacro %}
  {# „Mehr" ist der Reiter für alles, was keinen eigenen hat (UI-Review
     2026-09-01, Fund 6, Welle 2): Rezepte, Bestellungen, Bons — und Rolle
     und Status, die seit Welle 1 in einer Fusszeile standen. Auf jeder
     dieser Seiten ist „Mehr" der markierte Reiter; ein Reiter, der auf
     /rezepte nirgends leuchtet, sagt „du bist nirgends". #}
  {%- set ns = namespace(mehr=false) %}
  {%- for p in ["/mehr", "/rezepte", "/bestellungen", "/bons", "/status", "/rolle"] %}
  {%- if hier == p or hier.startswith(p ~ "/") %}{% set ns.mehr = true %}{% endif %}
  {%- endfor %}
  <header class="kopf">
    <a class="marke" href="/">Zettel</a>
  </header>

  {% if hinweis %}
  {# Spec 11: die Oberfläche schweigt nicht, wenn der Katalog alt ist. #}
  <p class="band" role="status">{{ hinweis }}</p>
  {% endif %}

  <main>{% block inhalt %}{% endblock %}</main>

  {# Die Leiste liegt fest am unteren Rand — unter dem Daumen, auf jedem
     Telefon ganz im Bild. Bis Welle 2 stand sie im Kopf und scrollte quer:
     sieben Ziele, eine Maske, ein Rast-Skript und drei Runden Feinarbeit
     (WB-400), und auf 375 px war „Bons" trotzdem nie zu sehen. Fünf
     Reiter brauchen nichts davon.

     Sie steht im HTML NACH dem Inhalt: fest positioniert ist die Reihenfolge
     fürs Auge egal, und ein Vorleseprogramm soll zuerst die Seite hören.
     Der Chat steht vor dem Korb, weil er der Weg dorthin ist (WB-382). #}
  <nav class="leiste" id="hauptnavigation" aria-label="Hauptbereiche">
    <a href="/katalog"{{ wo('/katalog') }}>Katalog</a>
    <a href="/chat"{{ wo('/chat') }}>Chat</a>
    {# Die Zahl wird nach jedem „+" per hx-swap-oob nachgezogen (siehe
       _eingelegt.html) — deshalb braucht sie eine feste id. #}
    <a href="/warenkorb"{{ wo('/warenkorb') }}>Korb <span id="korb-anzahl"
       class="korbzahl">{{ korb_anzahl or 0 }}</span></a>
    <a href="/pick"{{ wo('/pick') }}>Pick-Liste</a>
    <a href="/mehr"{% if ns.mehr %} class="aktiv" aria-current="page"{% endif %}>Mehr</a>
  </nav>
</body>
</html>
```

Im `<head>` den Kommentar zu `viewport-fit=cover` so ändern (Z. 10–11): „ohne das bleibt `env(safe-area-inset-bottom)` in `.leiste` auf iOS immer 0". Der grosse Kommentarblock zum Rasten (der vor dem `<script>` stand) und das Skript selbst fallen weg — sie erklärten ein Bauteil, das es nicht mehr gibt.

- [ ] **Schritt 4: `mehr.html` anlegen und die Route**

`zettel/web/templates/mehr.html`:

```html
{% extends "basis.html" %}
{% block titel %}Mehr — Zettel{% endblock %}
{% block inhalt %}
<h1>Mehr</h1>
{# Was keinen Reiter in der Leiste hat (UI-Review 2026-09-01, Fund 6). Drei
   Bereiche, die man seltener braucht als Katalog, Chat, Korb und Pick-Liste
   — und darunter, leiser, die zwei Seiten, die keine Bereiche sind: eine
   Einstellung und eine Diagnose (Fund 3). #}
<ul class="mehrliste">
  <li><a href="/rezepte">Rezepte</a></li>
  <li><a href="/bestellungen">Bestellungen</a></li>
  <li><a href="/bons">Bons</a></li>
</ul>
<ul class="mehrliste still">
  <li><a href="/rolle">Wer bin ich?{% if rolle %} <span class="anzahl">{{ rolle }}</span>{% endif %}</a></li>
  <li><a href="/status">Status</a></li>
</ul>
{% endblock %}
```

In `zettel/web/app.py` direkt VOR `@app.get("/rolle")` (Z. 721) einfügen:

```python
    @app.get("/mehr")
    def mehr(request: Request):
        """Der fünfte Reiter: was in der Leiste keinen Platz hat (Fund 6)."""
        c = con()
        try:
            return vorlagen.TemplateResponse(request, "mehr.html",
                                             _rahmen(request, c))
        finally:
            c.close()
```

- [ ] **Schritt 5: Das Stilblatt**

In `:root` (bei `--rand: var(--v3);`, Z. ~91) ergänzen:

```css
  --leiste: calc(56px + env(safe-area-inset-bottom)); /* die feste Reiterleiste */
```

Zeile 163 ersetzen:

```css
/* Unten so viel Luft, wie die feste Leiste deckt — sonst liegt die letzte
   Zeile jeder Seite unerreichbar unter ihr. */
main { padding: var(--rand) var(--rand) calc(var(--leiste) + var(--v6));
       max-width: 680px; margin: 0 auto; }
```

Den Block von `/* Sieben Ziele passen auf kein Telefon nebeneinander …` (Z. 212) bis einschliesslich `.fuss a.aktiv { … }` (Z. 282) ersetzen durch:

```css
/* Die Reiterleiste (UI-Review 2026-09-01, Fund 6, Welle 2) ---------------
   Fest am unteren Rand, fünf Reiter, jeder ein Fünftel breit. Bis Welle 2
   scrollte die Leiste quer im Kopf: Maske, Rasten, dünne Bildlaufleiste —
   drei Runden Feinarbeit dafür, dass „Bons" auf 375 px trotzdem nie im Bild
   war. Fünf Reiter brauchen nichts davon. Der Aufschlag: 56 px am unteren
   Rand gehören der Leiste; `main` und die klebende Kasse rechnen das ein. */
.leiste {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 3;
  display: flex; height: var(--leiste);
  padding: 0 var(--v1) env(safe-area-inset-bottom);
  background: var(--karte); border-top: 1px solid var(--linie);
}
.leiste a {
  flex: 1 1 0; min-width: 0; display: flex; align-items: center;
  justify-content: center; gap: var(--v1); padding: 0 var(--v1);
  color: var(--gedaempft); text-decoration: none;
  font-size: var(--t-fein); font-weight: 600; white-space: nowrap;
}
.leiste a:active { background: var(--karte-tief); }
/* Wo man gerade ist. Nicht nur Farbe: eine Kante oben am Reiter, und
   `aria-current` in der Vorlage für alle, die die Kante nicht sehen. */
.leiste a.aktiv {
  color: var(--akzent-tinte); font-weight: 700;
  box-shadow: inset 0 3px 0 var(--akzent);
}

/* Die Seite hinter „Mehr": eine Liste von Zielen, jedes eine ganze Zeile
   hoch. Die zweite Liste ist leiser — Einstellung und Diagnose sind keine
   Bereiche der App. */
.mehrliste {
  list-style: none; margin: 0 0 var(--v5); padding: 0;
  background: var(--karte); border: 1px solid var(--linie);
  border-radius: var(--radius);
}
.mehrliste li + li { border-top: 1px solid var(--linie); }
.mehrliste a {
  display: flex; align-items: center; gap: var(--v2); min-height: 52px;
  padding: 0 var(--v4); color: var(--tinte); text-decoration: none;
  font-size: var(--t-text); font-weight: 600;
}
.mehrliste a::after { content: "›"; margin-left: auto; color: var(--gedaempft);
                      font-size: var(--t-h2); }
.mehrliste.still a { font-weight: 500; color: var(--gedaempft); }
```

`.korbzahl` (Z. 466–472): `min-width: 22px` bleibt — die Pille sitzt jetzt im Reiter; sie bekommt `line-height: 1.4`, damit „0" nicht flacher ist als „12" (Fund 16, am Bild nachgemessen: die Pille hatte 19 px Höhe, der Fund „12×11" war die Ziffer, nicht die Pille):

```css
.korbzahl {
  display: inline-block; min-width: 22px; padding: var(--v0) var(--v2); margin-left: var(--v1);
  border-radius: 999px; background: var(--akzent); color: var(--auf-akzent);
  font-size: var(--t-fein); font-weight: 700; text-align: center; line-height: 1.4;
  font-family: var(--mono); font-variant-numeric: tabular-nums;
  animation: plopp 0.35s ease;
}
```

`.kasse` (Z. 578–583): sie klebt jetzt AUF der Leiste, und die Safe-Area rechnet die Leiste ein — nicht die Kasse noch einmal:

```css
.kasse {
  position: sticky; bottom: var(--leiste); z-index: 2;
  margin: var(--v4) calc(-1 * var(--rand)) 0; padding: var(--v2) var(--rand) var(--v3);
  background: var(--grund); border-top: 1px solid var(--linie);
}
```

- [ ] **Schritt 6: Messstand und Drehskripte**

`scripts/dreh/mess.py` Z. 92 (`if (window.leisteRasten) …`) löschen. Z. 118–130 (`var nav = …` bis `}`) ersetzen:

```js
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
```

Z. 160–162 (`if d.get("nav", {}).get("kanten"): …`) ersetzen:

```python
                if d.get("leiste", {}).get("kanten") or d.get("leiste", {}).get("unten"):
                    zeilen.append(f"  leiste: {d['leiste']}")
```

`scripts/dreh/shot.py` Z. 56–61 (Kommentar + `skript(sid, "if (window.leisteRasten) …")`) löschen; `SEITEN` in `mess.py` bekommt `"/mehr"` dazu.

- [ ] **Schritt 7: `tests/test_web_chat.py` Z. 362–364**

Docstring anpassen: „Sie scrollt quer, hat sieben Ziele und sieht auf jeder Seite gleich aus;" → „Sie hat fünf Reiter und sieht auf jeder Seite gleich aus;". Sonst nichts — `ohne_leiste()` schneidet weiter an `<nav id="hauptnavigation"`? NEIN: die id steht jetzt hinter der Klasse. `partition('<nav id="hauptnavigation"')` in Z. 369 und `split('<nav id="hauptnavigation"'` in Z. 179 auf `'<nav class="leiste" id="hauptnavigation"'` ändern.

- [ ] **Schritt 8: Alles grün**

Run: `.venv/bin/python -m pytest -q`
Expected: alle grün (die genaue Zahl steht im Commit).

- [ ] **Schritt 9: Bild**

Run: `.venv/bin/python scripts/dreh/mess.py` gegen den Bühnen-Server (`http://127.0.0.1:8747`, siehe Kopf von `mess.py`) — Erwartung: `leiste: {'unten': 0, 'hoehe': 56, 'kanten': []}` auf jeder Seite, keine `klein`-Zeile für `A.` in der Leiste (jeder Reiter ≥ 44 px hoch, ≥ 24 px breit). Wenn der Bühnen-Server noch den alten Stand serviert, steht das im Bericht — nicht neu starten, das macht die Steuerung (PID nur aus `~/picknick-video/buehne.pids`).

- [ ] **Schritt 10: Commit**

```bash
git add zettel/web/templates/basis.html zettel/web/templates/mehr.html zettel/web/app.py zettel/web/static/stil.css tests/test_web_politur.py tests/test_web_chat.py scripts/dreh/mess.py scripts/dreh/shot.py
git commit -m "Die Leiste liegt unten und hat fünf Reiter"
```

---

### Aufgabe 2: Monospace nur noch für Code (Fund 13)

**Warum:** Zwölf Regeln setzen `var(--mono)`; die Begründung „gerechnete Zahlen wie von der Kasse gedruckt" gab an fünf Orten fünf Antworten und ist für den Leser kein Unterschied, den er deuten kann. Ziffern, die untereinander stehen sollen, brauchen `tabular-nums`, keine zweite Schrift. Übrig bleibt Monospace dort, wo es Code IST: `code` (Trace-IDs, Endpunkte auf /status).

**Files:**
- Modify: `zettel/web/static/stil.css` (Kommentarblock Z. 18–40; Regeln Z. 362, 405, 406, 470, 542, 742, 748, 841, 1170, 1213, 1396; `.lauf .menge` Z. 1566)
- Test: `tests/test_web_politur.py`

- [ ] **Schritt 1: Test**

Am Ende von `tests/test_web_politur.py` anfügen:

```python
# --------------------------------------------------------------------------
# Welle 2: eine Schrift für das Blatt

def test_monospace_gibt_es_nur_noch_fuer_code():
    """Zwölf Regeln setzten `var(--mono)` — Preise, Packungszahlen,
    Portionsfelder, die Gesamtzeit (UI-Review 2026-09-01, Fund 13). Die
    Regel „gerechnete Zahlen wie von der Kasse" war für den Leser kein
    Unterschied, den er deuten konnte. Ziffern, die untereinander stehen,
    bekommen `tabular-nums`; Monospace bleibt, wo Code steht."""
    stil = STIL.read_text(encoding="utf-8")
    assert stil.count("var(--mono)") == 1
    assert "var(--mono)" in _block(stil, "code")
```

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_web_politur.py -k monospace`
Expected: FAIL — `12 == 1`.

- [ ] **Schritt 3: Stilblatt**

In jeder der elf Regeln (Z. 362 `.anzahl`, 405 `.menge`, 406 `.preis`, 470 `.korbzahl`, 542 `.stellen .anzahl`, 742 `.pickzeile .menge`, 748 `.pickzeile .gebinde`, 841 `.abschicken.portionen input`, 1170 `.entwurfsliste .menge`, 1213 `.gesamtzeit`, 1396 `.zugportionen input`) `font-family: var(--mono);` streichen; wo in derselben Regel noch kein `font-variant-numeric: tabular-nums;` steht (742, 748, 841, 1170, 1213, 1396), es an die Stelle setzen. Z. 1566 `.lauf .menge { font-family: inherit; }` löschen — sie nahm eine Schrift zurück, die es nicht mehr gibt. Den Kommentar Z. 739–740 („Sie ist gerechnet — also Monospace, wie von der Kasse gedruckt: …") auf einen Satz kürzen: „Sie ist gerechnet und steht deshalb gross."

Den Kommentarblock Z. 18–40 (von `- GERECHNETE ZAHLEN stehen in Monospace` bis `… tragen sie die Schrift des Blattes, mit `tabular-nums`.`) ersetzen durch:

```
   - EINE SCHRIFT. Bis Welle 2 des UI-Reviews 2026-09-01 standen gerechnete
     Zahlen in Monospace, „wie von der Kasse gedruckt" — zwölf Regeln, und
     die Grenze (was ist gerechnet, was abgeschrieben, was Fliesstext?) gab
     an fünf Orten fünf Antworten. Für den Leser war die zweite Schrift kein
     Unterschied, den er deuten konnte (Fund 13). Jetzt gilt:

       Ziffern, die untereinander stehen sollen, bekommen
       `font-variant-numeric: tabular-nums`. Monospace bekommt nur, was Code
       IST: Trace-IDs und Endpunkte auf /status, in `<code>`.
```

- [ ] **Schritt 4: grün**

Run: `.venv/bin/python -m pytest -q tests/test_web_politur.py tests/test_mengen.py`
Expected: PASS (der Test `test_mengen.py:409` verbietet Monospace in `.rezeptzutaten` — bleibt grün).

- [ ] **Schritt 5: Commit**

```bash
git add zettel/web/static/stil.css tests/test_web_politur.py
git commit -m "Eine Schrift für das Blatt — Monospace nur noch für Code"
```

---

### Aufgabe 3: Eine Regel für Knöpfe, und der Hinweiskasten wird neutral (Fund 14, Fund 15)

**Warum:** Sechs Knopfstile ohne Regel. Die Regel, die schon fast gilt und jetzt aufgeschrieben wird: **Primär** (gefüllt, Akzent) ist die eine Handlung, für die die Seite da ist — „Fragen" auf /chat, „Abschicken" im Korb, „Hochladen" auf /bons; genau einer je Seite. **Sekundär** (umrandet) schickt ein Feld ab, ohne die Seite zu sein: „Suchen", „Dazu", die Rollenwahl. **Entscheidung** ist Ja/Nein (`.mini.ja`/`.mini.nein`), **Rückweg** ist `.mini.zurueck` (ohne Rahmen, WB-361, Fund 12 bleibt verworfen). Drei Regeln im Blatt deklarierten den Primärstil dreimal mit kleinen Abweichungen (52/54 px) — sie werden eine. Fund 15: „60 von 10066" in Honig liest sich als Warnung; es ist eine Zählung.

**Files:**
- Modify: `zettel/web/static/stil.css` (Z. 307–316, 427–431, 565–572, 586–591, 1503–1511, 1600–1607)
- Test: `tests/test_web_politur.py`

- [ ] **Schritt 1: Tests**

Anfügen:

```python
def _bloecke(stil: str):
    """(Selektor, Deklarationen) für jeden Block im Blatt, ohne Kommentare."""
    ohne = re.sub(r"/\*.*?\*/", "", stil, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", ohne)


def test_der_primaere_knopf_ist_genau_eine_regel():
    """Sechs Knopfstile (UI-Review 2026-09-01, Fund 14). Die Regel: gefüllt
    ist die eine Handlung, für die die Seite da ist — Fragen, Abschicken,
    Hochladen. Drei Regeln sagten dasselbe mit 52 und 54 px; jetzt eine."""
    stil = STIL.read_text(encoding="utf-8")
    gefuellt = sorted(sel.strip() for sel, dekl in _bloecke(stil)
                      if ("button" in sel or ".gross" in sel or ".knopf" in sel)
                      and "background: var(--akzent)" in dekl)
    assert gefuellt == [".abschicken .gross, .chatform button, .bonupload button"]


def test_der_sekundaere_knopf_ist_genau_eine_regel():
    """„Dazu" (`.freitext button`) war eine Kopie von „Suchen" — Zeile für
    Zeile dieselben Deklarationen unter zweitem Namen."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".suche button, .freitext button, .knopf, .rollen button")
    assert "border: 1.5px solid var(--knopflinie)" in block
    assert "background: var(--karte)" in block
    assert ".freitext button, .abschicken .gross {" not in stil


def test_die_gekappte_trefferzahl_ist_keine_warnung():
    """„60 von 10066" in Honig las sich als Warnung (Fund 15); es ist eine
    Zählung. Honig heisst in diesem Blatt „es fehlt etwas" — hier fehlt
    nichts, die Liste ist nur geschnitten."""
    stil = STIL.read_text(encoding="utf-8")
    block = _block(stil, ".gekappt")
    assert "honig" not in block
    assert "var(--gedaempft)" in block
```

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_web_politur.py -k "knopf or gekappt"`
Expected: FAIL (drei Tests).

- [ ] **Schritt 3: Stilblatt**

Vor Z. 307 (`.suche button, .knopf, .rollen button {`) diesen Kommentar und die Regeln setzen; die alten Blöcke Z. 307–316 und Z. 565–572 (`.freitext button, .abschicken .gross { … }`, `.freitext button { flex … }`, `.freitext button:active`) und Z. 586–591 (`.abschicken .gross { … }`, `:active`) und Z. 1503–1511 (`.chatform button`) und Z. 1600–1607 (`.bonupload button`) werden dadurch ersetzt:

```css
/* Knöpfe — die Regel (UI-Review 2026-09-01, Fund 14) --------------------
   Vier Sorten, und jede hat einen Grund:
   PRIMÄR, gefüllt in Akzent: die eine Handlung, für die die Seite da ist —
     „Fragen" auf /chat, „Abschicken" im Korb, „Hochladen" auf /bons. Genau
     einer je Seite; ein zweiter gefüllter Knopf macht den ersten unwahr.
   SEKUNDÄR, umrandet: schickt ein Feld ab, ohne die Seite zu sein —
     „Suchen", „Dazu", die Rollenwahl, jeder `.knopf`-Link.
   ENTSCHEIDUNG: Ja/Nein an einer Zeile (`.mini.ja`, `.mini.nein`, unten).
   RÜCKWEG: „zurück", „rückgängig", „raus" — `.mini.zurueck`, ohne Rahmen
     (WB-361), damit er nicht wie eine dritte Wahl neben Ja/Nein aussieht. */
.suche button, .freitext button, .knopf, .rollen button {
  min-height: var(--tap); padding: 0 var(--v4); font-size: var(--t-text); font-weight: 600;
  border: 1.5px solid var(--knopflinie); border-radius: var(--radius-knopf);
  background: var(--karte); color: var(--tinte); text-decoration: none;
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer; transition: transform 0.12s ease;
  white-space: nowrap;
}
.suche button, .freitext button { flex: 0 0 auto; }
.suche button:active, .freitext button:active, .knopf:active,
.rollen button:active { transform: scale(0.96); }
.abschicken .gross, .chatform button, .bonupload button {
  min-height: var(--tap); padding: 0 var(--v4); font-size: var(--t-text); font-weight: 700;
  border: none; border-radius: var(--radius-knopf);
  background: var(--akzent); color: var(--auf-akzent);
  box-shadow: var(--schatten); cursor: pointer;
  transition: transform 0.12s ease; white-space: nowrap;
}
.chatform button { flex: 0 0 auto; }
.abschicken .gross, .bonupload button { width: 100%; min-height: 54px; }
.abschicken .gross:active, .chatform button:active,
.bonupload button:active { transform: scale(0.98); }
```

Der Kommentar vor Z. 565 (`.freitext input`-Umfeld) bleibt; `.abschicken { margin-top: var(--v4); }` (Z. 573) bleibt. `.rollen button { width: 100%; min-height: 56px; }` (Z. 318) bleibt.

`.gekappt` (Z. 424–431) ersetzen:

```css
/* „60 von 657" über einer geschnittenen Trefferliste (WB-375). Eine
   Zählung, keine Warnung (UI-Review 2026-09-01, Fund 15): Honig heisst in
   diesem Blatt „es fehlt etwas" — hier fehlt nichts, die Liste ist nur
   geschnitten. */
.gekappt {
  margin: 0 0 var(--v3); padding: var(--v2) var(--v3); font-size: var(--t-fein);
  color: var(--gedaempft); background: var(--karte-tief);
  border: 1px solid var(--linie); border-radius: var(--radius-knopf);
}
```

- [ ] **Schritt 4: grün, und ein Blick**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Dann mit dem Browser (oder `scripts/dreh/shot.py`) /chat, /warenkorb, /bons, /katalog?q=milch ansehen: je Seite genau ein gefüllter Knopf; „Dazu" und „Suchen" sehen gleich aus.

- [ ] **Schritt 5: Commit**

```bash
git add zettel/web/static/stil.css tests/test_web_politur.py
git commit -m "Vier Sorten Knöpfe, aufgeschrieben — und die Trefferzahl warnt nicht mehr"
```

---

### Aufgabe 4: Der Rechensatz im Korb wird kurz, das Ladenfeld bekommt ein Wort (Fund 8)

**Warum und Abweichung von der Empfehlung:** Die Empfehlung (b) — den Satz nur zeigen, wenn die Packungszahl von Hand gesetzt wurde — hält nicht: der Satz trägt im Korb die GEBRAUCHTE Menge („2 Stk gebraucht"), die Hauptangabe der Zeile (WB-381, `_korb.html:87`). Ihn zu verstecken nähme der Zeile ihre Aussage. Stattdessen wird er kurz: „2 Stk gebraucht — 2 Stk lässt sich nicht gegen die Packung („1 kg") rechnen, die Menge bleibt, wie sie ist." wiederholt die Menge und entschuldigt sich in einem Nebensatz; „2 Stk gebraucht — nicht gegen die Packung („1 kg") zu rechnen." sagt dasselbe in einer Zeile. `Rechnung.grund` bleibt UNVERÄNDERT — er geht als `zettel.reason` in den Trace (`korb.py:258`) und wird von `test_mengen_chat.py:273` gepinnt; nur die zwei Sätze für den Leser (`satz()`, `nachsatz()`) ändern sich.

**Files:**
- Modify: `zettel/mengen.py` (vor `nachsatz` Z. 480; `nachsatz` Z. 500–503; `satz` Z. 537–539)
- Modify: `zettel/web/templates/_korb.html` Z. 130–131
- Test: `tests/test_mengen.py` Z. 229–233, 304–311; `tests/test_web_orders.py` (neu)

- [ ] **Schritt 1: Tests**

`tests/test_mengen.py` Z. 229–233 ersetzen:

```python
def test_der_satz_sagt_kurz_warum_nichts_gerechnet_wurde():
    """„2 Stk gebraucht — 2 Stk lässt sich nicht gegen die Packung („1 kg")
    rechnen, die Menge bleibt, wie sie ist." stand an fast jeder Korbzeile
    (UI-Review 2026-09-01, Fund 8): die Menge zweimal, die Entschuldigung
    einmal. Der Grund im Trace bleibt lang; der Satz für den Leser sagt es
    in einer Zeile."""
    r = mengen.rechne(2, None, "1 kg")
    satz = mengen.satz(r, produkt="Zwiebeln", unit_text="1 kg", qty=1)
    assert satz == "2 Stk gebraucht — nicht gegen die Packung („1 kg“) zu rechnen."
    assert "bleibt, wie sie ist" in r.grund or "rechnen" in r.grund
```

Z. 304–311 (`test_der_nachsatz_nennt_den_grund_nur_wenn_ihn_sonst_niemand_nennt`) — die letzte Zeile ersetzen:

```python
    ohne = mengen.rechne(200, "g", None)
    assert mengen.nachsatz(ohne, qty=1) == "Keine lesbare Packungsgrösse."
```

Und einen Test für die drei Kurzformen dazu (nach Z. 311):

```python
def test_der_kurzgrund_kennt_die_drei_faelle():
    assert mengen.kurzgrund(mengen.rechne(200, "g", None)) == \
        "keine lesbare Packungsgrösse"
    assert mengen.kurzgrund(mengen.rechne(200, "g", "Beutel"), "Beutel") == \
        "keine lesbare Packungsgrösse („Beutel“)"
    assert mengen.kurzgrund(mengen.rechne(2, None, "1 kg"), "1 kg") == \
        "nicht gegen die Packung („1 kg“) zu rechnen"
    assert mengen.kurzgrund(mengen.rechne(200, "g", "0 g"), "0 g") == \
        "Packungsgrösse null"
```

In `tests/test_web_orders.py` am Ende:

```python
def test_das_ladenfeld_sagt_was_es_ist(client, con):
    """Ein Auswahlfeld mit „Egal wo" als einzigem sichtbaren Wort (UI-Review
    2026-09-01, Fund 8). Das `aria-label` hört nur ein Vorleseprogramm."""
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    text = client.get("/warenkorb").text
    assert "Laden: egal" in text or "Laden: Egal" in text
```

(`_pid(con, name)`, `MILCH` und `HTMX` stehen in `test_web_orders.py` schon — siehe Z. 47–63.)

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_mengen.py tests/test_web_orders.py -k "kurz or nachsatz or ladenfeld"`
Expected: FAIL — `kurzgrund` gibt es nicht.

- [ ] **Schritt 3: `mengen.py`**

Vor `def nachsatz` (Z. 480) einfügen:

```python
def kurzgrund(rechnung: Rechnung, unit_text: str | None = None) -> str:
    """Warum nicht gerechnet wurde — in einer Zeile, ohne die Menge.

    `rechnung.grund` ist der ganze Satz für den Trace (`zettel.reason`) und
    nennt die Menge noch einmal: „2 Stk lässt sich nicht gegen die Packung
    („1 kg") rechnen". Unter einer Korbzeile, die mit „2 Stk gebraucht"
    beginnt, ist das die Menge zweimal (UI-Review 2026-09-01, Fund 8). Hier
    steht nur der Grund; leer, wenn gerechnet wurde.
    """
    if rechnung.ausrechenbar or rechnung.bedarf is None:
        return ""
    if rechnung.packung is None:
        return f"keine lesbare Packungsgrösse{_zitat(unit_text)}"
    if rechnung.packung <= 0:
        return "Packungsgrösse null"
    packung = _zitat(unit_text) or " " + schreibe(rechnung.packung,
                                                  rechnung.packung_einheit)
    return f"nicht gegen die Packung{packung} zu rechnen"
```

In `nachsatz` Z. 500–503 ersetzen:

```python
        grund = kurzgrund(rechnung, unit_text)
        teile.append(f"{grund[:1].upper()}{grund[1:]}.")
```

In `satz` Z. 537–539 ersetzen:

```python
    if not rechnung.ausrechenbar:
        return f"{gebraucht} — {kurzgrund(rechnung, unit_text)}."
```

Den Kommentar in `satz` Z. 531–535 („Der Zusatz „die Menge bleibt, wie sie ist" antwortet …") kürzen auf: „„3 Stk gebraucht." und sonst nichts (WB-385): beim Freitext war nie eine Rechnung im Gang, und ein Grund erklärte eine Lücke, die keine ist."

- [ ] **Schritt 4: `_korb.html` Z. 130–131**

```html
          <option value="{{ s }}" {% if s == i.store %}selected{% endif %}>
            Laden: {{ laden_titel[s] | lower if s == 'egal' else laden_titel[s] }}</option>
```

Vorher mit `grep -n "LADEN_TITEL" -A6 zettel/orders/__init__.py zettel/orders/*.py | head` nachsehen, wie der Schlüssel für „Egal wo" heisst und wie der Titel lautet; die Zeile so setzen, dass jedes `<option>` mit „Laden: " beginnt (z. B. „Laden: egal wo", „Laden: Rewe", „Laden: Lidl"). Der Test oben lässt beide Schreibweisen zu.

- [ ] **Schritt 5: grün**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Falls `tests/test_portionen.py:378` oder `tests/test_mengen.py:155/170` (Docstrings, keine Asserts) — nichts zu tun. Bricht ein anderer Test auf den alten Satz, seine Erwartung auf den neuen Satz setzen und im Commit nennen.

- [ ] **Schritt 6: Commit**

```bash
git add zettel/mengen.py zettel/web/templates/_korb.html tests/test_mengen.py tests/test_web_orders.py
git commit -m "Der Rechensatz im Korb sagt es in einer Zeile, das Ladenfeld sagt, was es ist"
```

---

### Aufgabe 5: Einheiten wie im Kochbuch, und drei kleine Dinge (Fund 16)

**Warum:** Im Entwurfsfeld steht „el", „paket" — die gefaltete Grundeinheit, in der `mengen.falte` alles kleinschreibt, damit „Zehe(n)" und „Zehen" dieselbe Einheit sind. Zum Lesen taugt das nicht. `schreibe()` gibt Hauptwörtern den grossen Anfangsbuchstaben zurück („Paket"), kennt aber die Abkürzungen nicht: „El" ist falsch. Dazu: „Das Modell überlegt …" ist 13 px grau; „Alle 1 Züge tragen eine Span-ID" ist kein Deutsch. Das `scroll-margin-top` aus der Welle-2-Tabelle entfällt: seit WB-417 steht der jüngste Zug oben (`show:window:top`), nichts liegt mehr unter dem klebenden Kopf — das steht so in REVIEW.md (Aufgabe 10).

**Files:**
- Modify: `zettel/mengen.py` (nach `schreibe` Z. 404)
- Modify: `zettel/recipes/sammlung.py` Z. 288
- Modify: `zettel/web/app.py` Z. 686 (Filter) 
- Modify: `zettel/web/templates/_entwurf.html` Z. 109; `_chat.html` Z. 68; `status.html` Z. 97–99
- Test: `tests/test_mengen.py`, `tests/test_entwurf.py`, `tests/test_web_politur.py`

- [ ] **Schritt 1: Tests**

`tests/test_mengen.py` anfügen:

```python
def test_die_einheit_wird_geschrieben_wie_im_kochbuch():
    """Gefaltet ist „el"; gelesen wird „EL" (UI-Review 2026-09-01, Fund 16).
    `schreibe` gab Hauptwörtern den grossen Anfangsbuchstaben zurück —
    „Paket" — und machte aus der Abkürzung „El"."""
    assert mengen.einheit_text("el") == "EL"
    assert mengen.einheit_text("tl") == "TL"
    assert mengen.einheit_text("pck") == "Pck."
    assert mengen.einheit_text("paket") == "Paket"
    assert mengen.einheit_text("g") == "g"
    assert mengen.einheit_text(None) == ""
    assert mengen.schreibe(2, "el") == "2 EL"
    # Die Schreibweise faltet auf sich selbst zurück — ein Feld, das „EL"
    # anzeigt und „EL" zurückschickt, speichert wieder „el".
    for gefaltet in mengen.SCHREIBWEISE:
        assert mengen.falte(mengen.SCHREIBWEISE[gefaltet]) == gefaltet
```

In `tests/test_entwurf.py` neben `test_die_menge_laesst_sich_ueber_die_oberflaeche_aendern` (Z. ~972, dort steht die Aufrufform mit `_bolo_geholt`, `_web`, `_web_zug` und `/bedarf`) anfügen:

```python
def test_das_einheitenfeld_zeigt_die_kochbuchschreibweise(con, db_pfad, tmp_path):
    """„el" im Feld sah aus wie ein Tippfehler (Fund 16). Gespeichert bleibt
    die gefaltete Form; gezeigt wird „EL"."""
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    client.post("/chat",
                data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
                headers={"HX-Request": "true"})
    hack = con.execute(
        "SELECT s.id FROM chat_suggestion s JOIN product p ON p.id = s.product_id"
        " WHERE p.name LIKE 'Rinderhack%'").fetchone()["id"]
    stueck = client.post(f"/chat/vorschlag/{hack}/bedarf",
                         data={"menge": "2", "einheit": "EL"},
                         headers={"HX-Request": "true"}).text
    assert 'name="einheit" autocomplete="off" value="EL"' in stueck
    assert 'value="el"' not in stueck
```

`tests/test_web_politur.py` anfügen:

```python
def test_die_wartezeile_im_chat_ist_lesbar():
    """„Das Modell überlegt …" in 13 px grau (Fund 16): die einzige Zeile,
    die während der Wartezeit etwas sagt, war die kleinste der Seite."""
    stil = STIL.read_text(encoding="utf-8")
    assert "font-size: var(--t-klein)" in _block(stil, ".chatkopf #chat-laeuft")


def test_ein_einzelner_zug_ohne_luecke_bekommt_einen_ganzen_satz(client):
    """„Alle 1 Züge tragen eine Span-ID." ist kein Deutsch (Fund 16)."""
    client.post("/chat", data={"satz": "Milch"})
    text = client.get("/status").text
    assert "Alle 1 Züge" not in text
```

(Der zweite Test setzt voraus, dass ein Chat-Zug ohne Modell entsteht — `POST /chat` mit einem Katalogwort geht den Rezept-/Suchweg ohne Modell, wie `tests/test_web_chat.py` es tut. Antwortet die Seite ohne Zug mit „Noch kein Chat-Zug", den Zug so anlegen, wie `test_web_chat.py::_langer_verlauf` es macht.)

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_mengen.py tests/test_entwurf.py tests/test_web_politur.py -k "kochbuch or wartezeile or einzelner"`
Expected: FAIL.

- [ ] **Schritt 3: `mengen.py`**

Nach `schreibe` (Z. 404) einfügen und `schreibe` darauf umstellen:

```python
#: Gefaltete Einheit -> Schreibweise im Kochbuch. Nur, was `schreibe` nicht
#: aus der Faltung zurückrechnen kann: die Abkürzungen. Jede Schreibweise
#: faltet auf ihren Schlüssel zurück (`test_die_einheit_wird_geschrieben_wie_
#: im_kochbuch`), damit ein Feld, das sie zeigt, sie auch zurückgeben darf.
SCHREIBWEISE = {"el": "EL", "tl": "TL", "pck": "Pck.", "msp": "Msp."}


def einheit_text(einheit) -> str:
    """`„el"` -> `„EL"`, `„paket"` -> `„Paket"`, `„g"` -> `„g"`, `None` -> `„"`."""
    if not einheit:
        return ""
    if einheit in (GRAMM, MILLILITER, STUECK):
        return einheit
    return SCHREIBWEISE.get(einheit, f"{einheit[:1].upper()}{einheit[1:]}")
```

Und in `schreibe` die letzte Zeile:

```python
    return f"{zahl_deutsch(menge)} {einheit_text(einheit)}"
```

`zettel/recipes/sammlung.py` Z. 288: `z["einheit_text"] = mengen.einheit_text(z.get("unit")) or STUECK` — prüfen, ob `STUECK` dort importiert ist (`mengen.STUECK`), sonst `mengen.STUECK`; das Verhalten „schreibt Stk, auch wenn NULL steht" (Kommentar Z. 289–291) bleibt so erhalten.

`zettel/web/app.py` Z. 686 ergänzen: `vorlagen.env.filters["einheit"] = mengen.einheit_text` (prüfen, dass `mengen` in `app.py` importiert ist: `grep -n "^from zettel import\|import mengen" zettel/web/app.py`).

`_entwurf.html` Z. 109: `value="{{ z.need_unit | einheit }}"`.

`_chat.html` Z. 68 bleibt; im Stilblatt Z. 1468: `.chatkopf #chat-laeuft { margin: var(--v2) 0 0; font-size: var(--t-klein); }`.

`status.html` Z. 97–99 ersetzen:

```html
  {% elif not trace_luecke.ohne_trace %}
  <p class="fertig">{% if trace_luecke.zuege == 1 %}Der eine Zug trägt eine
    Span-ID.{% else %}Alle <strong>{{ trace_luecke.zuege }}</strong> Züge tragen
    eine Span-ID.{% endif %}</p>
```

- [ ] **Schritt 4: grün**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Schritt 5: Commit**

```bash
git add zettel/mengen.py zettel/recipes/sammlung.py zettel/web/app.py zettel/web/templates/_entwurf.html zettel/web/templates/status.html zettel/web/static/stil.css tests/test_mengen.py tests/test_entwurf.py tests/test_web_politur.py
git commit -m "Einheiten stehen wie im Kochbuch — und drei kleine Sätze"
```

---

### Aufgabe 6: Anleitungsprosa streichen (Fund 9, nach eigenem Urteil)

**Warum:** Sätze, die man beim dritten Lesen überspringt, kosten beim ersten Lesen den Blick. Gestrichen wird, was die Seite selbst zeigt oder was niemand beim Einkaufen wissen muss; es bleibt, was ein Missverständnis verhindert (der gemeinsame Korb). **Jeder gestrichene Satz steht wörtlich in der Commit-Nachricht**, damit Aaron ihn zurückholen kann.

**Files:**
- Modify: `zettel/web/templates/rezept.html` Z. 91–93, 125–129; `_rezept.html` Z. 43–46; `bons.html` Z. 19–22, 113–115; `status.html` Z. 111–114, 171–177; `katalog.html` Z. 60–61
- Unverändert: `warenkorb.html:7` („Ein gemeinsamer Korb …" bleibt — er verhindert ein Missverständnis), `bestellungen.html` (leerer Zustand ist schon ein Satz)
- Test: `tests/test_web_politur.py`

- [ ] **Schritt 1: Test**

```python
def test_die_seiten_erklaeren_sich_nicht_mehr_selbst(client, con):
    """Anleitungsprosa in 13 px (UI-Review 2026-09-01, Fund 9), gestrichen
    nach eigenem Urteil; die Sätze stehen in der Commit-Nachricht."""
    assert "Vorrang vor der Suche" not in client.get("/rezepte/1").text \
        if con.execute("SELECT 1 FROM recipe WHERE id = 1").fetchone() else True
    bons = client.get("/bons").text
    assert "Kartennummer" not in bons and "besser lesbar" not in bons
    status = client.get("/status").text
    assert "Magic Packet" not in status and "Warum eine Zeile leer blieb" not in status
    assert "Spec 5.1" not in client.get("/katalog").text
```

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_web_politur.py -k erklaeren`
Expected: FAIL.

- [ ] **Schritt 3: Streichen**

`rezept.html` Z. 91–93:

```html
<p class="fussnote" role="status">Menge aus dem Rezept:
  {{ amount | menge }} {{ unit or "Stk" }} — geht beim „+“ mit.</p>
```

`rezept.html` Z. 125–129 (Test `tests/test_portionsfeld.py:116` erwartet „Dauerhaft, und alle Mengen" — bleibt):

```html
  <p class="fussnote" id="portionen-hinweis">Dauerhaft, und alle Mengen
    rechnen mit.</p>
```

`_rezept.html` Z. 43–46:

```html
  <p class="leer">Noch kein Produkt verknüpft — die Zutaten laut Rezept
    stehen unten. Schreib im Chat „alles für {{ rezept.name }}“, dann sucht
    der Shop sie im Katalog.</p>
```

`bons.html` Z. 19–22:

```html
<p class="fussnote">
  {{ erlaubt }}, bis {{ max_mb }} MB — ein eBon als PDF liest sich besser als
  ein Foto.
</p>
```

`bons.html` Z. 112–116 (`<p class="fussnote">Ausgelesen werden …</p>`): ganz löschen.

`status.html` Z. 111–114 (`<p class="fussnote">Warum eine Zeile leer blieb …</p>`): ganz löschen. Z. 171–177 kürzen auf:

```html
  <p class="fussnote">Ob die Box in diesem Moment bedient, steht hier
    absichtlich nicht: die Frage danach weckt sie. Sie wird im
    <a href="/chat">Chat</a> gestellt, wo jemand das Modell braucht.</p>
```

`katalog.html` Z. 60–61: „Preise stammen von knuspr.de und sind Richtwerte — eingekauft wird bei Rewe und Lidl." (das „(Spec 5.1)" fällt).

- [ ] **Schritt 4: grün**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (`test_portionsfeld.py:116` bleibt grün, `test_betrieb.py:277` prüft Verhalten, nicht den Satz).

- [ ] **Schritt 5: Commit — mit jedem gestrichenen Satz**

```bash
git add zettel/web/templates/rezept.html zettel/web/templates/_rezept.html zettel/web/templates/bons.html zettel/web/templates/status.html zettel/web/templates/katalog.html tests/test_web_politur.py
git commit -F - <<'MSG'
Anleitungsprosa gestrichen — die Seiten erklären sich nicht mehr selbst

UI-Review 2026-09-01, Fund 9. Gestrichen nach eigenem Urteil; jeder Satz
steht hier, damit er zurückgeholt werden kann:

rezept.html (Menge aus dem Rezept):
  „— sie wird beim „+" mit verknüpft und wächst mit den Portionen."
  → „— geht beim „+" mit."
rezept.html (Portionen):
  „: aus „500 g" für 4 werden „1000 g" für 8 — in der Zutatenliste wie an
  den verknüpften Produkten. Steht hier nichts, gibt es keinen Faktor, und
  die Mengen bleiben, wie sie sind."
_rezept.html (leer):
  „Was du hier von Hand zuordnest, hat beim nächsten Mal Vorrang vor der
  Suche."
bons.html (oben):
  „Ein eBon als PDF aus der Rewe- oder Lidl-App ist besser lesbar als ein
  Foto — und er lässt sich hier auslesen." → „— ein eBon als PDF liest
  sich besser als ein Foto."
bons.html (unten), ganz:
  „Ausgelesen werden Laden, Datum, Artikel, Menge und Preis — und sonst
  nichts. Kartennummer, Terminal-ID und Belegnummern stehen auf dem Bon
  und bleiben dort."
status.html (Züge ohne Trace), ganz:
  „Warum eine Zeile leer blieb, steht nirgends: Tracing abgeschaltet und
  Phoenix nicht erreichbar hinterlassen dieselbe leere Spalte.
  Nachträglich lässt sich das nicht mehr auseinanderhalten, und geraten
  wird hier nicht."
status.html (Box):
  „die Frage danach schickt ein Magic Packet und weckt einen Rechner im
  Nebenzimmer. Sie wird genau dort gestellt, wo jemand das Modell
  wirklich braucht — im Chat. Und ob beim letzten Zug überhaupt ein
  Modell gefragt wurde, steht nicht in der Datenbank: der Rezeptweg
  schreibt dieselbe Zeile ohne einen einzigen Modellaufruf."
katalog.html:
  „(Spec 5.1)"

Geblieben: warenkorb.html „Ein gemeinsamer Korb — was hier steht, sieht
auch der andere." (verhindert ein Missverständnis) und der leere Zustand
von bestellungen.html.
MSG
```

---

### Aufgabe 7: Der Entwurf bleibt offen, die Quittung verlinkt die Pick-Liste

**Warum:** Jede Aktion im Rezeptentwurf (Name merken, Menge ok, „raus") rendert den ganzen Zug neu und liefert das `<details>` zu zurück — wer drei Mengen ändert, klappt dreimal auf (`_entwurf.html:44–58` beschreibt das als „Produktentscheidung, keine Aufräumarbeit"; hier ist die Entscheidung). Und „Die Bestellung steht jetzt auf der Pick-Liste." nennt die Seite, ohne hinzuführen.

**Files:**
- Modify: `zettel/web/app.py` (`_teilantwort` Z. 1348–1383, `_zug_antwort` Z. 1421–1424, die vier Entwurf-Endpunkte Z. 2300, 2319, 2341, 2362)
- Modify: `zettel/web/templates/_entwurf.html` Z. 44–59; `bestellungen.html` Z. 23
- Test: `tests/test_entwurf.py`, `tests/test_web_orders.py`

- [ ] **Schritt 1: Tests**

`tests/test_entwurf.py` neben `test_die_menge_laesst_sich_ueber_die_oberflaeche_aendern` (Z. ~972; Aufrufform von dort):

```python
def test_nach_einer_entwurfsaktion_bleibt_der_entwurf_offen(con, db_pfad, tmp_path):
    """Wer drei Mengen ändert, klappte dreimal auf (UI-Review 2026-09-01,
    Fund 2, Rest): jede Aktion im Entwurf rendert den Zug neu und lieferte
    das `<details>` zu zurück. Nach einer Aktion IM Entwurf kommt es offen;
    nach einem Ja/Nein oben bleibt es zu."""
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    client.post("/chat",
                data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
                headers={"HX-Request": "true"})
    mid = con.execute("SELECT max(id) AS id FROM chat_message"
                      " WHERE role = 'assistant'").fetchone()["id"]
    hack = con.execute(
        "SELECT s.id FROM chat_suggestion s JOIN product p ON p.id = s.product_id"
        " WHERE p.name LIKE 'Rinderhack%'").fetchone()["id"]
    antwort = client.post(f"/chat/vorschlag/{hack}/bedarf",
                          data={"menge": "0,25", "einheit": "kg"},
                          headers={"HX-Request": "true"}).text
    assert "<details open>" in antwort
    oben = client.post(f"/chat/{mid}/alle?decision=kept",
                       headers={"HX-Request": "true"}).text
    assert "<details open>" not in oben and "<details>" in oben
```

`tests/test_web_orders.py`:

```python
def test_die_quittung_fuehrt_zur_pick_liste(client, con):
    """„Die Bestellung steht jetzt auf der Pick-Liste." nannte die Seite,
    ohne hinzuführen."""
    client.post(f"/katalog/einlegen?product_id={_pid(con, MILCH)}", headers=HTMX)
    text = client.post("/warenkorb/abschicken", follow_redirects=True).text
    assert 'auf der <a href="/pick">Pick-Liste</a>.' in text
```

(Antwortet `/warenkorb/abschicken` ohne HTMX mit einer Weiterleitung auf `/bestellungen`, folgt `follow_redirects=True` ihr; sonst `client.get("/bestellungen")` danach prüfen — `grep -n "abschicken" -A12 zettel/web/app.py`.)

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_entwurf.py tests/test_web_orders.py -k "offen or quittung"`
Expected: FAIL.

- [ ] **Schritt 3: `app.py`**

`_teilantwort` bekommt einen Parameter `entwurf_offen: bool = False` (hinter `gerade`), und im Kontext-Dict (Z. 1382) `"entwurf_offen": entwurf_offen,`. `_zug_antwort`:

```python
    def _zug_antwort(request: Request, c: sqlite3.Connection, mid: int,
                     fehler: str | None = None, entwurf_offen: bool = False):
        """Die Antwort auf alles, was Zeilen ANLEGT oder mehrere ändert.

        `entwurf_offen`: die Aktion kam aus dem Rezeptentwurf — das
        `<details>` kommt offen zurück, sonst klappt es bei jeder Menge zu.
        """
        return _teilantwort(request, c, mid, "_zugantwort.html", fehler=fehler,
                            entwurf_offen=entwurf_offen)
```

In den vier Endpunkten `entwurf_benennen` (Z. 2300), `entwurf_verwerfen` (Z. 2319), `entwurf_zeile` (Z. 2341), `entwurf_bedarf` (Z. 2362): `return _zug_antwort(request, c, mid, fehler=fehler, entwurf_offen=True)` (bei 2341/2362 mit `_zug_von(c, sid)` wie bisher).

- [ ] **Schritt 4: Vorlagen**

`_entwurf.html` Z. 44–59: den Kommentar ab „Zu ist er danach aber nicht nur …" bis „… keine Aufräumarbeit." ersetzen durch:

```
     Offen bleibt er nach einer Aktion IM Entwurf (UI-Review 2026-09-01,
     Fund 2, Rest): Name merken, Menge ok, „raus" zielen mit `outerHTML`
     auf den ganzen Zug, und ein `<details>` ohne `open` klappte bei jeder
     Menge wieder zu. `entwurf_offen` setzen nur die vier Entwurf-Endpunkte;
     das Ja/Nein oben lässt ihn zu — dort ist er der Nebenpfad.
```

und Z. 59: `<details{% if entwurf_offen %} open{% endif %}>`.

`bestellungen.html` Z. 23: `Die Bestellung steht jetzt auf der <a href="/pick">Pick-Liste</a>.</p>`

- [ ] **Schritt 5: grün**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Schritt 6: Commit**

```bash
git add zettel/web/app.py zettel/web/templates/_entwurf.html zettel/web/templates/bestellungen.html tests/test_entwurf.py tests/test_web_orders.py
git commit -m "Der Entwurf bleibt offen, wenn man darin arbeitet — und die Quittung führt zur Pick-Liste"
```

---

### Aufgabe 8: Der Nachtlauf zieht die Knuspr-Einheiten nach

**Warum:** `knuspr.repariere_einheiten(con)` (Welle 1, Fund 5) hat keinen Aufrufer ausser den Tests. Der Crawl schreibt neue Zeilen richtig (`ON CONFLICT … unit_text = excluded`), aber nur die, die er anfasst; alles andere bleibt „0,25 g", bis jemand die Funktion von Hand ruft.

**Files:**
- Modify: `zettel/scrapers/nachtlauf.py` Z. 77–82
- Test: `tests/test_miniaturen.py` (neben `test_der_nachtlauf_zieht_die_miniaturen_nach`, Z. 131)

- [ ] **Schritt 1: Test**

```python
def test_der_nachtlauf_zieht_die_einheiten_nach(tmp_path, monkeypatch):
    """`repariere_einheiten` hatte keinen Aufrufer ausser den Tests (UI-Review
    2026-09-01, Fund 5, Rest). Der Crawl schreibt nur die Zeilen richtig, die
    er anfasst; der Nachtlauf zieht die anderen nach."""
    from zettel.scrapers import nachtlauf
    monkeypatch.setattr(nachtlauf.knuspr, "crawl",
                        lambda *a, **k: {"run_id": 1, "status": "ok",
                                         "n_products": 0, "error": None})
    db_datei = tmp_path / "p.db"
    con = db.connect(db_datei)
    db.migrate(con)
    con.execute("INSERT INTO product (name, unit_text, unit, price_cents)"
                " VALUES ('Nudeln', '0,25 g', 'g', 239)")
    con.commit()
    con.close()
    meldungen = []
    nachtlauf.lauf(str(db_datei), begriffe=["nudeln"], image_dir=None,
                   http=object(), sichern=False, schreib=meldungen.append)
    con = db.connect(db_datei)
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Nudeln'"
                       ).fetchone()["unit_text"] == "250 g"
    con.close()
    assert "Einheiten nachgezogen: 1" in meldungen
```

(Die Pflichtspalten von `product` vorher mit `grep -n "CREATE TABLE product" -A20 zettel/db.py` prüfen und das INSERT darauf setzen — `NOT NULL`-Spalten ohne Vorgabe brauchen einen Wert.)

- [ ] **Schritt 2: rot**

Run: `.venv/bin/python -m pytest -q tests/test_miniaturen.py -k einheiten`
Expected: FAIL — `unit_text` bleibt „0,25 g".

- [ ] **Schritt 3: `nachtlauf.py`** — nach der Fehlermeldung des Crawls (Z. 82, vor dem äusseren `finally`):

```python
        # Was der Crawl nicht angefasst hat, steht noch mit „0,25 g" da
        # (UI-Review 2026-09-01, Fund 5). Idempotent, deshalb bei jedem Lauf.
        schreib(f"Einheiten nachgezogen: {knuspr.repariere_einheiten(con)}")
```

- [ ] **Schritt 4: grün**

Run: `.venv/bin/python -m pytest -q tests/test_miniaturen.py tests/test_betrieb.py tests/test_knuspr.py`
Expected: PASS.

- [ ] **Schritt 5: Commit**

```bash
git add zettel/scrapers/nachtlauf.py tests/test_miniaturen.py
git commit -m "Der Nachtlauf zieht die Knuspr-Einheiten nach"
```

---

### Aufgabe 9: Die Demo-Datenbank aufräumen (Fund 10a) — Steuerung, kein Subagent

**Warum:** Rezept 1 „Einkauf vom 2026-08-28" (keine Quelle, 0 Zutaten) und die Dubletten 25 und 26 „Caesar Salad" (Chefkoch-Import ohne Prüfung) stehen in der Rezeptliste des Videos. Rezept 24 („Caesar's Salad mit Parmesan-Croûtons", von `dish` 11 referenziert) und 27 („Caesar Salat", 17 Zutaten) bleiben. Keins der drei wird von `dish`, `recipe_zuordnung` oder `chat_rezept` referenziert (geprüft am 2026-09-02). Fund 10c (kein Rezept aus einer leeren Bestellung) ist schon abgedeckt: `uebernahme.aus_bestellung` wirft `LeeresRezept` (Z. 271–274, Test `test_uebernahme.py:181`).

- [ ] **Schritt 1: Kopie, DELETE auf der Kopie, Gegenprobe**

```bash
cp ~/picknick-demo/demo.db ~/picknick-demo/demo.db.vor-welle-2
SCRATCH=<Scratchpad-Verzeichnis dieser Sitzung>
cp ~/picknick-demo/demo.db $SCRATCH/demo-kopie.db
python3 - <<'PY'
import sqlite3
p = "$SCRATCH/demo-kopie.db"
con = sqlite3.connect(p)
for t, sp in [("dish", "recipe_id"), ("recipe_zuordnung", "recipe_id"), ("chat_rezept", "recipe_id")]:
    n = con.execute(f"SELECT count(*) FROM {t} WHERE {sp} IN (1, 25, 26)").fetchone()[0]
    assert n == 0, (t, n)
for t in ("recipe_ingredient", "recipe_item", "recipe"):
    sp = "id" if t == "recipe" else "recipe_id"
    print(t, con.execute(f"DELETE FROM {t} WHERE {sp} IN (1, 25, 26)").rowcount)
con.commit()
print(con.execute("SELECT id, name FROM recipe ORDER BY id").fetchall())
con.close()
PY
```

- [ ] **Schritt 2: Kopie zurück** (nur wenn Schritt 1 die erwarteten Zeilen meldet: recipe 3, recipe_ingredient 0 für Nr. 1 plus die Zutaten von 25/26)

```bash
cp $SCRATCH/demo-kopie.db ~/picknick-demo/demo.db
```

Kein Commit — die Datenbank liegt ausserhalb des Repos. Was entfernt wurde, steht im Nachtrag zu REVIEW.md (Aufgabe 10).

---

### Aufgabe 10: Nachtrag, Bühne, Video — Steuerung

- [ ] REVIEW.md: Abschnitt „Welle 2 — Nachtrag (2026-09-02)": je Fund, was geändert wurde; Fund 8 mit der Abweichung; Fund 10a mit den drei gelöschten Rezepten; Fund 12 als verworfen (Gegenprobe aus der Welle-2-Tabelle); Fund 16 `scroll-margin-top` als durch WB-417 erledigt; die Knuspr-Messung: der Preisquotient kann die Lesart nicht beweisen (35 Zeilen mit Quotient ≈ Zahl, 30 davon echte Gramm mit Grundpreis je Gramm — ein echtes „0,5 g" Safran würde falsch „bestätigt"), die 12 Zeilen mit Einheitenfehler ≥ 1 („1 g" für 1 kg) bleiben ein offener Rest ohne Regel.
- [ ] Welle-1-Plan: Welle-2-Tabelle mit Verweis auf diesen Plan; hier alle Kästchen.
- [ ] Memory `ui-review-welle-2-offen.md` → Welle 2 auf master, offene Reste.
- [ ] Bühne: App-PID aus `~/picknick-video/buehne.pids` beenden (nur diese PID), neu starten, PID eintragen; `scripts/dreh/mess.py` auf allen Seiten.
- [ ] Video: `bereit.sh`, `MAUS=0 SKRIPT=dreh_handy.py STAMM=… bash aufnahme.sh 75`, `handy.py <STAMM>`; dann `MAUS=1 SKRIPT=dreh.py STAMM=… bash aufnahme.sh 300`, `schnitt.py`. Die Drehskripte suchen Elemente über `finde.py`; die Leiste unten verschiebt nichts im Blatt, aber die Kasse und der Chatkopf haben andere Abstände — jeden Tipp im Einzelbild prüfen.

---

## Selbstprüfung des Plans

* **Abdeckung:** Fund 6 → 1. Fund 8 (beide Zeilen) → 4. Fund 9 → 6. Fund 10a → 9, 10c geprüft (schon abgedeckt), 10b nach dem Contest (Werkbank). Fund 12 → verworfen, Nachtrag in 10. Fund 13 → 2. Fund 14, 15 → 3. Fund 16 (`.korbzahl`, Einheiten, Wartezeile, „Alle 1 Züge") → 1 und 5; `scroll-margin-top` durch WB-417 erledigt. Fund 3 → bleibt (Aaron). Knuspr-Rest → 8 und Nachtrag. Entwurf offen, Quittungslink → 7.
* **Platzhalter:** keine; wo ein Subagent einen Namen nachschlagen muss (`_pid`, `_web_zug`, Abschick-Pfad, `LADEN_TITEL`), steht der `grep` dazu.
* **Namen quer über Aufgaben:** `--leiste`, `.leiste`, `.leiste a.aktiv`, `.mehrliste` (1). `mengen.kurzgrund(rechnung, unit_text=None)` (4). `mengen.SCHREIBWEISE`, `mengen.einheit_text(einheit)`, Jinja-Filter `einheit` (5). `_teilantwort(..., entwurf_offen=False)`, `_zug_antwort(..., entwurf_offen=False)` (7). `_bloecke(stil)` nur in Aufgabe 3 definiert und benutzt.
* **Reihenfolge:** 1 zuerst (ändert jedes Bild); 2–8 unabhängig; 9 und 10 durch die Steuerung.
