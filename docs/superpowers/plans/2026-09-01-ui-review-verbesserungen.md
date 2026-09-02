# UI-Review-Verbesserungen — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die zehn Funde aus `docs/superpowers/review/2026-09-01/REVIEW.md`, die vor dem 08.09. und vor einem neuen Video-Take zu haben sind, so beheben, dass ein Fremder die App auf dem Handy in zehn Sekunden versteht und ihr die Zahlen glaubt.

**Architecture:** Zwei Wellen. Welle 1 sind zehn kleine, unabhängige Aufgaben — je Aufgabe ein Test, der heute rot ist, eine Änderung an Vorlage, Stilblatt oder einer reinen Funktion, ein Commit. Nichts davon fasst den Chat-Kern, die Datenbankstruktur oder die Vorschlagslogik an. Welle 2 sind die Funde, bei denen erst eine Entscheidung fällig ist; dort steht die Entscheidung, keine Aufgabe.

**Tech Stack:** FastAPI + Jinja2 + HTMX, ein Stilblatt (`zettel/web/static/stil.css`), Tests mit `fastapi.testclient` und Textprüfungen am Stilblatt (`tests/test_web_politur.py` ist das Vorbild). Kein Browser im Test — was nur ein Gerät entscheiden kann, steht bei der Aufgabe.

---

## Wie dieser Plan gelesen wird

* Der Review nennt Funde mit Nummer (Fund 1–16). Jede Aufgabe sagt, welchen Fund sie ganz oder teilweise schliesst.
* Commit-Betreff im Stil des Projekts: ein deutscher Satz, kein Präfix (`git log` zeigt die Form).
* Alle Pfade relativ zu `<Repo>`. Tests laufen mit `.venv/bin/python -m pytest`.
* Die Kommentare im Code sind Teil der Arbeit: dieses Projekt schreibt an jede Stelle, **warum** sie so ist (siehe die Nachbarn in denselben Dateien). Die Kommentare in den Codeblöcken unten sind so gemeint und werden übernommen.
* Reihenfolge: Aufgabe 1–4 sind reine S-Aufgaben und lassen sich in beliebiger Folge machen. Aufgabe 5 (Einheiten) ist die einzige mit Daten-Reparatur. Aufgabe 6–9 verändern, was im Video zu sehen ist — sie kommen vor dem nächsten Take.

## Dateien

| Datei | Aufgabe | Was sich ändert |
|---|---|---|
| `zettel/web/static/stil.css` | 1, 3, 6, 7, 8, 9, 10 | Zutatentabelle, Fusszeile, Beispiel-Chips, Summe, klebender Bestellknopf, Entwurfs-Summary, Pick-Zeile |
| `zettel/web/templates/_vorschlag.html` | 2 | „Rang 6.0" raus |
| `zettel/web/templates/basis.html` | 3 | Status und „wer bin ich?" wandern aus der Leiste in eine Fusszeile |
| `zettel/bons/lesen.py` | 4 | OCR-Hinweis in Nutzersprache |
| `zettel/scrapers/knuspr.py` | 5 | `normalisiere_einheit()`, `repariere_einheiten()` |
| `zettel/web/templates/chat.html`, `_chat.html` | 6 | Beispiel-Chips statt Erklärprosa |
| `zettel/orders/korb.py`, `zettel/orders/__init__.py`, `zettel/web/app.py`, `_korb.html`, `bestellungen.html` | 7 | Summe, Bestellkarte als Zeilen, Quittung nach dem Abschicken |
| `zettel/web/templates/_entwurf.html` | 9 | Rezeptentwurf eingeklappt |
| `tests/test_web_politur.py`, `tests/test_web_chat.py`, `tests/test_knuspr.py`, `tests/test_orders.py`, `tests/test_web_orders.py`, `tests/test_bons_lesen.py`, `tests/test_entwurf.py` | alle | je ein bis drei Tests |

---

## Welle 1

### Aufgabe 1: Die Zutatentabelle bricht keine Wörter mehr (Fund 4, Teil von 13)

**Ursache:** `.rezeptzutaten a.name { display: flex }` macht den Hinweis (`<span class="hinweis">, aus der Mühle</span>`) zum Flex-Geschwister des Namens; beide teilen sich die Breite, „Lavendelblüten" bricht dreifach. Die Mengenspalte ist starr `8ch` in Monospace, „1 gr. Dose/n" braucht drei Zeilen.

**Files:**
- Modify: `zettel/web/static/stil.css:861-870`
- Test: `tests/test_web_politur.py` (hinter `test_das_tap_ziel_der_rezeptzutat_haelt_die_44_px_des_blattes`)

- [x] **Schritt 1: Die beiden Tests schreiben**

```python
def test_der_griff_an_der_rezeptzutat_ist_kein_flex_container():
    """UI-Review 2026-09-01, Fund 4: als `display: flex` wurde der Hinweis
    („, aus der Mühle") zum Geschwister des Namens, beide teilten sich die
    Breite — „Lavend / elblüte / n". Ein Block lässt beide als EINEN Satz
    fliessen; die 44 px (WB-379) bleiben."""
    stil = STIL.read_text(encoding="utf-8")
    block = stil.split(".rezeptzutaten a.name {", 1)[1].split("}", 1)[0]
    assert "display: flex" not in block
    assert "display: block" in block
    assert "min-height: var(--tap)" in block


def test_die_mengenspalte_der_rezeptzutat_ist_weder_starr_noch_mono():
    """„1 gr. Dose/n" stand dreizeilig in 8ch Monospace. Eine Kochangabe ist
    keine Maschinenausgabe; für fluchtende Ziffern reicht `tabular-nums`."""
    stil = STIL.read_text(encoding="utf-8")
    block = stil.split(".rezeptzutaten .menge {", 1)[1].split("}", 1)[0]
    assert "8ch" not in block
    assert "var(--mono)" not in block
    assert "tabular-nums" in block
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py -k "rezeptzutat" -v`
Expected: die beiden neuen FAIL (`display: flex` steht drin; `8ch` steht drin), der alte Tap-Test PASS.

- [x] **Schritt 3: Stilblatt ändern**

In `zettel/web/static/stil.css` den Block von `.rezeptzutaten .menge {` bis zum Ende von `.rezeptzutaten a.name { … }` ersetzen durch:

```css
/* Die Mengenspalte ist so breit wie ihre längste Angabe, aber nie mehr als
   ein Drittel: „1 gr. Dose/n" stand dreizeilig in 8ch Monospace (UI-Review
   2026-09-01, Fund 4). Keine Monospace — die Zahl ist eine Kochangabe und
   keine Maschinenausgabe; `tabular-nums` genügt, damit Ziffern fluchten. */
.rezeptzutaten .menge { flex: 0 0 auto; min-width: 5ch; max-width: 33%;
                        text-align: right; color: var(--gedaempft);
                        font-size: var(--t-klein);
                        font-variant-numeric: tabular-nums; }
/* Der Griff „Zutat im Katalog suchen" ist ein Tap-Ziel wie jedes andere und
   hatte ~24 px — die Höhe der Textzeile. In diesem Blatt sind es 44 (WB-379).
   Volle Restbreite, damit auch neben einem kurzen Namen getroffen wird.

   Als BLOCK und nicht als Flex-Container (Fund 4): als Flex wurde der
   Hinweis („, aus der Mühle") zum Geschwister des Namens, beide teilten sich
   die Breite, und „Lavendelblüten" brach in drei Zeilen. Der Innenabstand
   zentriert die Textzeile in den 44 px, wie `align-items` es vorher tat. */
.rezeptzutaten a.name {
  flex: 1 1 auto; min-width: 0; display: block; box-sizing: border-box;
  min-height: var(--tap); padding: calc((var(--tap) - 1.4em) / 2) 0;
  color: var(--akzent-tinte); text-decoration: none;
}
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py tests/test_web_stil.py -q`
Expected: alle PASS.

- [x] **Schritt 5: Auf dem Bild prüfen** (kein Test kann das)

Shop lokal starten und `/rezepte/<id>` eines Chefkoch-Rezepts mit langen Zutatennamen bei 390 px ansehen — die Aufnahme aus dem Review war `docs/superpowers/review/2026-09-01/bilder/seite_05_rezept.png`, y 1290–1660. Erwartet: Name und Hinweis in einer Zeile fliessend, Menge höchstens zweizeilig.

- [x] **Schritt 6: Commit**

```bash
git add zettel/web/static/stil.css tests/test_web_politur.py
git commit -m "Die Zutatentabelle bricht keine Wörter mehr — der Griff ist ein Block"
```

---

### Aufgabe 2: „Rang 6.0" verschwindet von der Vorschlagskarte (Fund 3, Teil)

**Ursache:** `_vorschlag.html:58` gibt `v.rang` mit `%.1f` aus. Der Rang ist eine Retriever-Zahl für den Trace (OBSERVABILITY.md), keine Auskunft für den Menschen — dort bleibt er.

**Files:**
- Modify: `zettel/web/templates/_vorschlag.html:56-60`
- Test: `tests/test_web_chat.py`

- [x] **Schritt 1: Test schreiben** (hinter `test_der_chat_steht_in_der_navigation`)

```python
def test_die_vorschlagskarte_verschweigt_den_rang(db_datei, tmp_path):
    """„Käse · Rang 6.0" stand am Vorschlag (UI-Review 2026-09-01, Fund 3).
    Der Rang ist eine Retriever-Zahl für den Trace und bleibt dort; auf der
    Karte liest ein Fremder ihn als Note, die er nicht vergeben hat."""
    client, _ = _client(db_datei, tmp_path)
    con = db.connect(db_datei)
    try:
        korb = orders.warenkorb(con)
        mid = vorschlagsliste.nachricht(con, korb, vorschlagsliste.ROLLE_AGENT,
                                        "Antwort")
        vorschlagsliste.vorschlag(con, mid, product_id=_pid(db_datei, MILCH),
                                  search_term="Käse", rang=6.0)
    finally:
        con.close()
    seite = client.get("/chat").text
    assert "Käse" in seite
    assert "Rang" not in _chatteil(seite)
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_chat.py -k verschweigt_den_rang -v`
Expected: FAIL — `"Rang" in …` („· Rang 6.0" steht im Chatteil).

- [x] **Schritt 3: Zeile entfernen**

In `zettel/web/templates/_vorschlag.html` den Block

```jinja
    <span class="woher">
      {{ v.search_term or "—" }}
      {%- if v.rang is not none %} · Rang {{ "%.1f" | format(v.rang) }}{% endif %}
      {%- if v.ist_korrektur %} · statt „{{ v.statt_name }}“{% endif %}
    </span>
```

ersetzen durch

```jinja
    {# Ohne den Rang (UI-Review 2026-09-01, Fund 3): „Käse · Rang 6.0" las
       ein Fremder als Note. Die Zahl gehört dem Retriever-Span im Trace
       (OBSERVABILITY.md) und steht dort weiterhin. #}
    <span class="woher">
      {{ v.search_term or "—" }}
      {%- if v.ist_korrektur %} · statt „{{ v.statt_name }}“{% endif %}
    </span>
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_web_chat.py tests/test_entwurf.py -q`
Expected: alle PASS. (Fällt ein anderer Test, weil er „Rang" auf der Karte erwartete, wird DER Test geändert — die Karte ist jetzt die Vorgabe.)

- [x] **Schritt 5: Commit**

```bash
git add zettel/web/templates/_vorschlag.html tests/test_web_chat.py
git commit -m "Die Vorschlagskarte verschweigt den Rang — er gehört dem Trace"
```

---

### Aufgabe 3: Status und „wer bin ich?" wandern in eine Fusszeile (Fund 3, Teil von 6)

**Warum so:** Neun Ziele passen in keine 390 px (Fund 6). Zwei davon sind keine Bereiche der App: „Status" ist eine Diagnoseseite, „wer bin ich?" eine Einstellung. Beide bekommen eine Fusszeile unter `<main>`. Die Leiste behält sieben Ziele — sie scrollt weiterhin (das bleibt Welle 2), aber „Bons" wird nun das letzte und liegt bei 390 px näher am Bild.

Der bestehende Test `test_jede_vollseite_markiert_sich_selbst_in_der_navigation` sucht die Markierung `class="aktiv" aria-current="page"` **irgendwo** auf der Seite, nicht in `<nav>` — die Fusszeilen-Links nutzen dasselbe `wo()` und der Test bleibt grün. `window.leisteRasten()` findet auf `/status` kein `a[aria-current]` in der Leiste und kehrt früh zurück — gewollt.

**Files:**
- Modify: `zettel/web/templates/basis.html:58-59` und `:117-118` (`<main>`)
- Modify: `zettel/web/static/stil.css` (hinter dem Block `.kopf nav a.aktiv`)
- Test: `tests/test_web_politur.py`

- [x] **Schritt 1: Test schreiben** (hinter `test_die_markierung_ist_nicht_nur_ansage_sondern_auch_sichtbar`)

```python
def test_diagnose_und_einstellung_stehen_nicht_in_der_hauptleiste(client):
    """Neun Ziele passen in keine 390 px (UI-Review 2026-09-01, Fund 6), und
    zwei davon sind keine Bereiche: „Status" ist Diagnose, „wer bin ich?"
    eine Einstellung. Beide stehen in einer Fusszeile — erreichbar, mit
    Markierung, aber nicht im ersten Blick."""
    text = client.get("/katalog").text
    leiste = text.split('<nav id="hauptnavigation"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/status"' not in leiste
    assert 'href="/rolle"' not in leiste
    fuss = text.split('<footer class="fuss"', 1)[1].split("</footer>", 1)[0]
    assert 'href="/status"' in fuss
    assert 'href="/rolle"' in fuss


def test_die_fusszeile_markiert_ihre_seite_wie_die_leiste(client):
    text = client.get("/status").text
    fuss = text.split('<footer class="fuss"', 1)[1].split("</footer>", 1)[0]
    assert '<a href="/status" class="aktiv" aria-current="page"' in fuss
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py -k "hauptleiste or fusszeile" -v`
Expected: beide FAIL (`/status` steht in der Leiste; keine `<footer class="fuss"`).

- [x] **Schritt 3: Vorlage ändern**

In `zettel/web/templates/basis.html` die beiden Zeilen

```jinja
      <a href="/status"{{ wo('/status') }}>Status</a>
      <a href="/rolle"{{ wo('/rolle') }}>{% if rolle %}{{ rolle }}{% else %}wer bin ich?{% endif %}</a>
```

aus `<nav>` entfernen, und `<main>{% block inhalt %}{% endblock %}</main>` ersetzen durch:

```jinja
  <main>{% block inhalt %}{% endblock %}</main>

  {# Diagnose und Einstellung stehen NICHT in der Hauptleiste (UI-Review
     2026-09-01, Fund 3 und 6): neun Ziele passten in keine 390 px, und ein
     Fremder las „Status" — Ports, Hostnamen, Trace-Adressen — als Teil der
     App. Hier unten sind beide erreichbar und tragen dieselbe Markierung wie
     die Leiste; nur den ersten Blick bekommen sie nicht mehr. #}
  <footer class="fuss">
    <a href="/status"{{ wo('/status') }}>Status</a>
    <a href="/rolle"{{ wo('/rolle') }}>{% if rolle %}{{ rolle }}{% else %}wer bin ich?{% endif %}</a>
  </footer>
```

- [x] **Schritt 4: Stilblatt ergänzen** — hinter dem Block `.kopf nav a.aktiv { … }` in `zettel/web/static/stil.css`:

```css
/* Die Fusszeile (UI-Review 2026-09-01, Fund 3): Diagnose und Einstellung,
   aus der Hauptleiste herausgenommen. Dieselbe Höhe wie ein Leistenziel,
   damit der Daumen sie trifft; gedämpft, damit das Auge sie nicht sucht. */
.fuss {
  display: flex; gap: var(--v4); justify-content: center;
  margin: var(--v6) 0 0; padding: var(--v3) var(--v3) calc(var(--v3) + env(safe-area-inset-bottom));
  border-top: 1px solid var(--linie); font-size: var(--t-klein);
}
.fuss a {
  display: inline-flex; align-items: center; min-height: var(--tap);
  padding: 0 var(--v3); color: var(--gedaempft); text-decoration: none;
}
.fuss a.aktiv { color: var(--tinte); font-weight: 600; }
```

Prüfen, dass `--v6` im `:root`-Block existiert (`grep -n -- "--v6" zettel/web/static/stil.css`); sonst `var(--v5)` nehmen.

- [x] **Schritt 5: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py tests/test_web_chat.py tests/test_betrieb.py -q`
Expected: alle PASS — insbesondere `test_jede_vollseite_markiert_sich_selbst_in_der_navigation[/status]` und `[/rolle]`.

- [x] **Schritt 6: Commit**

```bash
git add zettel/web/templates/basis.html zettel/web/static/stil.css tests/test_web_politur.py
git commit -m "Status und wer-bin-ich stehen in der Fusszeile, nicht in der Hauptleiste"
```

---

### Aufgabe 4: Der OCR-Hinweis auf der Bon-Seite spricht zuerst vom Nutzer (Fund 3, Teil)

**Korrektur am Befund, Gegenprobe am Code:** „Browse… No file selected." ist die Beschriftung des *Browsers* (der Review-Firefox lief englisch; ein deutsches Telefon zeigt „Durchsuchen… Keine ausgewählt"). Die Gestalt des Knopfs ist bereits unsere: `stil.css:1505` stylt `::file-selector-button` seit WB-400 wie `.mini`. Was im Review als „nackt" auffiel, war das Wort, nicht der Knopf — **kein Stil-Teil in dieser Aufgabe**, nur eine Notiz in REVIEW.md (siehe Abschluss).

Was bleibt, ist unser Text: der Absatz über `tesseract-ocr` und „Systemänderung" wird umgestellt — erst, was der Nutzer tun kann, dann in Klammern das Paket. Die Tests `tests/test_bons_lesen.py:222` und `tests/test_web_bonlesen.py:290/301` verlangen weiterhin den Paketnamen — zu Recht, er ist die Handlungsanweisung für den Betreiber.

**Files:**
- Modify: `zettel/bons/lesen.py:93-97` (`OCR_FEHLT_TEXT`)
- Test: `tests/test_bons_lesen.py`

- [x] **Schritt 1: Test schreiben** — in `tests/test_bons_lesen.py` hinter dem Test, der `bons.OCR_FEHLT_TEXT == lesen.OCR_FEHLT_TEXT` prüft:

```python
def test_der_ocr_hinweis_faengt_mit_dem_an_was_geht():
    """UI-Review 2026-09-01, Fund 3: der Absatz begann mit „fehlt OCR" und
    „Systemänderung". Ein Nutzer will zuerst wissen, was er tun kann — der
    PDF-eBon —, das Paket steht danach für den, der die Maschine betreibt."""
    text = lesen.OCR_FEHLT_TEXT
    assert text.startswith("Foto-Bons")
    assert text.index("eBon") < text.index("tesseract-ocr")
    assert "Systemänderung" not in text
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_bons_lesen.py -k ocr_hinweis -v`
Expected: FAIL (`text.startswith("Foto-Bons")` ist falsch).

- [x] **Schritt 3: Text ändern** — `zettel/bons/lesen.py`, `OCR_FEHLT_TEXT` ersetzen durch:

```python
# Erst, was geht, dann, was fehlt (UI-Review 2026-09-01, Fund 3): der Absatz
# begann mit „fehlt OCR" und „Systemänderung", und ein Fremder las die
# Bon-Seite als Fehlerseite. Der Paketname bleibt — er ist die Anweisung
# für den, der die Maschine betreibt, und die Tests verlangen ihn.
OCR_FEHLT_TEXT = (
    "Foto-Bons kann diese Maschine noch nicht lesen — ein eBon als PDF aus "
    "der Rewe- oder Lidl-App geht immer. (Fürs Lesen von Fotos fehlen die "
    "Pakete `tesseract-ocr` und `tesseract-ocr-deu`; das installiert der "
    "Betreiber, nicht dieser Prozess.)")
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_bons_lesen.py tests/test_web_bons.py tests/test_web_bonlesen.py -q`
Expected: alle PASS.

- [x] **Schritt 5: Commit**

```bash
git add zettel/bons/lesen.py tests/test_bons_lesen.py
git commit -m "Bons: der OCR-Hinweis sagt zuerst, was geht, und dann, was fehlt"
```

---

### Aufgabe 5: Einheiten im Katalog stimmen — „0,25 g" wird „250 g" (Fund 5)

**Befund, nachgemessen an der Review-Kopie der Demo-Datenbank:** Knuspr liefert `textualAmount` in **167** aktiven Produkten als „0,x" mit der *kleinen* Einheit (104× `g`, 63× `ml`) — z. B. „Byodo Tagliatelle … 250g": `unit_text = "0,25 g"`, `unit = "g"`, `price_cents = 239`, `price_per_unit_cents = 956`. Die Quotienten beweisen die Kilo-Lesart: 239/956 = 0,25 → die Zahl ist in kg/l, die Einheit wurde von der Quelle nicht mitskaliert. „0,75 l" und „0,7 kg" (409 bzw. 277 Zeilen) sind dagegen **richtig** und bleiben. Der Review nannte 107; das war eine engere Zählung, die Regel ist dieselbe.

**Folge über das Bild hinaus:** `mengen.packungsgroesse("0,25 g")` liest 0,25 g, und die Packungsrechnung im Korb (`orders.korb.rechnung`) rechnet damit — „2 kg Nudeln" ergäbe 8000 Packungen. Es ist ein Datenfehler mit Rechenfolgen, kein Layout.

**Fix in zwei Teilen:** (a) `parse_products()` normalisiert beim Crawl — der nächste Lauf schreibt alle Zeilen per `ON CONFLICT … unit_text = excluded.unit_text` neu; (b) `repariere_einheiten(con)` bringt die bestehenden Datenbanken sofort in Ordnung, weil der nächste Crawl vor dem Video nicht sicher ist.

**Files:**
- Modify: `zettel/scrapers/knuspr.py:95-124` (`parse_products`), neu `normalisiere_einheit`, `repariere_einheiten`
- Test: `tests/test_knuspr.py`

- [x] **Schritt 1: Tests schreiben** (hinter `test_fehlender_preis_ist_none_nicht_null`)

```python
@pytest.mark.parametrize("text, unit, erwartet", [
    ("0,25 g", "g", "250 g"),        # Byodo Tagliatelle 250 g
    ("0,225 ml", "ml", "225 ml"),    # Develey Sauce
    ("0,27 g", "g", "270 g"),        # Piccolinis 9×30 g
    ("0,5 g", "g", "500 g"),
    ("0,75 l", "l", "0,75 l"),       # richtig — Liter bleiben Liter
    ("0,7 kg", "kg", "0,7 kg"),      # richtig
    ("250 g", "g", "250 g"),         # schon in Ordnung
    ("1 l", "l", "1 l"),
    ("2 x 0,25 g", "g", "2 x 0,25 g"),  # Multipack: nicht angefasst, nie gesehen
    (None, "g", None),
    ("", "g", ""),
])
def test_eine_kilozahl_mit_grammeinheit_wird_zu_gramm(text, unit, erwartet):
    """UI-Review 2026-09-01, Fund 5. Knuspr liefert „0,25 g" für 250 g: die
    Zahl ist in kg, die Einheit blieb klein. 167 aktive Produkte in der Demo-
    Datenbank; `price_cents / price_per_unit_cents` (239/956 = 0,25) beweist
    die Kilo-Lesart. Eine Menge unter 1 in g oder ml gibt es im Lebensmittel-
    handel nicht — deshalb ist „< 1 und kleine Einheit" das Merkmal."""
    assert knuspr.normalisiere_einheit(text, unit) == erwartet


def test_parse_products_normalisiert_die_einheit():
    roh = _roh(7, "Tagliatelle")
    roh["textualAmount"] = "0,25 g"
    roh["unit"] = "g"
    zeile = knuspr.parse_products(_seite([roh], 1))[0]
    assert zeile["unit_text"] == "250 g"


def test_repariere_einheiten_bringt_bestehende_zeilen_in_ordnung(con):
    http = FakeHTTP([_seite([_roh(1, "Milch"), _roh(2, "Nudeln")], 2)])
    knuspr.crawl(con, http, ["milch"], pause_s=0)
    con.execute("UPDATE product SET unit_text = '0,25 g', unit = 'g'"
                " WHERE name = 'Nudeln'")
    con.commit()

    n = knuspr.repariere_einheiten(con)

    assert n == 1
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Nudeln'"
                       ).fetchone()["unit_text"] == "250 g"
    assert con.execute("SELECT unit_text FROM product WHERE name = 'Milch'"
                       ).fetchone()["unit_text"] == "1 l"
    assert knuspr.repariere_einheiten(con) == 0     # idempotent
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_knuspr.py -k "einheit" -v`
Expected: FAIL mit `AttributeError: module 'zettel.scrapers.knuspr' has no attribute 'normalisiere_einheit'`.

- [x] **Schritt 3: Implementieren**

In `zettel/scrapers/knuspr.py` oberhalb von `parse_products` einfügen (`import re` oben ergänzen, falls nicht da):

```python
#: „0,25 g" — eine Zahl unter 1 mit der KLEINEN Einheit. Knuspr liefert
#: `textualAmount` bei 167 aktiven Produkten so (UI-Review 2026-09-01,
#: Fund 5); gemeint ist die Zahl in kg bzw. l, mitskaliert wurde die Einheit
#: nicht (`price_cents / price_per_unit_cents` = 239/956 = 0,25 → pro kg).
#: Unter 1 g oder 1 ml verkauft der Lebensmittelhandel nichts, deshalb ist
#: das Muster eindeutig. Kilo und Liter („0,75 l") sind richtig und bleiben.
_KLEINE_EINHEIT_UNTER_EINS = re.compile(r"^0[.,](\d+)\s*(g|ml)$")


def normalisiere_einheit(text, unit):
    """`„0,25 g"` -> `„250 g"`. Alles andere unverändert — auch `None`."""
    if not text:
        return text
    treffer = _KLEINE_EINHEIT_UNTER_EINS.match(str(text).strip())
    if not treffer or unit not in ("g", "ml"):
        return text
    nachkomma, einheit = treffer.groups()
    # „0,25" -> 250, „0,225" -> 225, „0,5" -> 500: die Nachkommastellen auf
    # drei auffüllen und als ganze Zahl lesen. Mehr als drei Stellen gäbe
    # Bruchteile von Gramm — die gibt es nicht, also bleibt so ein Text stehen.
    if len(nachkomma) > 3:
        return text
    return f"{int(nachkomma.ljust(3, '0'))} {einheit}"


def repariere_einheiten(con) -> int:
    """Bestehende Zeilen nachziehen. Gibt die Zahl der geänderten zurück.

    Der nächste Crawl täte es auch (`ON CONFLICT … unit_text = excluded`),
    aber der ist nicht sicher vor dem nächsten Blick auf die Seite.
    Idempotent: eine normalisierte Zeile trifft das Muster nicht mehr.
    """
    zeilen = con.execute(
        "SELECT id, unit_text, unit FROM product"
        " WHERE unit IN ('g', 'ml') AND unit_text LIKE '0,%'").fetchall()
    n = 0
    for z in zeilen:
        neu = normalisiere_einheit(z["unit_text"], z["unit"])
        if neu != z["unit_text"]:
            con.execute("UPDATE product SET unit_text = ? WHERE id = ?",
                        (neu, z["id"]))
            n += 1
    con.commit()
    return n
```

In `parse_products` die Zeile `"unit_text": p.get("textualAmount"),` ersetzen durch:

```python
            "unit_text": normalisiere_einheit(p.get("textualAmount"),
                                              p.get("unit")),
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_knuspr.py tests/test_mengen.py tests/test_catalog.py -q`
Expected: alle PASS.

- [x] **Schritt 5: Bestehende Datenbanken reparieren** — je Datei ein Aufruf, Zahl notieren:

```bash
cd <Repo>
for DB in data/picknick.db "$HOME/picknick-demo/demo.db"; do
  ZETTEL_DB="$DB" .venv/bin/python - <<'EOF'
import os
from zettel import db
from zettel.scrapers import knuspr
c = db.connect(os.environ["ZETTEL_DB"])
try:
    print(os.environ["ZETTEL_DB"], "->", knuspr.repariere_einheiten(c), "Zeilen")
finally:
    c.close()
EOF
done
```

Expected: `data/picknick.db -> ~167 Zeilen` (die Zahl kann abweichen, wenn der Katalog inzwischen neu gecrawlt wurde), `demo.db -> 167 Zeilen`. Die Demo-Datenbank ist das, was im Video zu sehen ist — genau deshalb wird sie hier repariert. Vorher eine Kopie: `cp ~/picknick-demo/demo.db ~/picknick-demo/demo.db.vor-einheiten`.

- [x] **Schritt 6: Commit**

```bash
git add zettel/scrapers/knuspr.py tests/test_knuspr.py
git commit -m "Knuspr: eine Kilozahl mit Grammeinheit wird zu Gramm — 167 Produkte trugen 0,x g"
```

---

### Aufgabe 6: Die Startseite zeigt, was sie kann — drei Beispiele zum Antippen (Fund 1, Teil von 9)

**Heute:** `/chat` ist über dem Falz zu 70 % leer; `chat.html` erklärt in einer `fussnote`, dass der Chat am Korb hängt, `_chat.html` erklärt im leeren Verlauf, dass „vorgeschlagen wird". Beides ist Anleitung. Ein Juror will in zehn Sekunden sehen, was passiert.

**Neu:** Kein Erklärabsatz im Kopf; im leeren Verlauf ein Satz und drei Chips. Ein Chip füllt das Feld und schickt ab — das *ist* die Vorführung. Ohne JavaScript ist der Chip ein normaler Absender desselben Formulars (`form="chatform"` + `name="satz"` + `value`), der Weg funktioniert also auch ohne Skript; das Skript spart nur den Tipp auf „Fragen".

**Files:**
- Modify: `zettel/web/templates/chat.html` (die `fussnote` unter `<h1>`)
- Modify: `zettel/web/templates/_chat.html:112-118` (`{% else %}`-Zweig des Verlaufs), `<form class="chatform"` bekommt `id="chatform"`
- Modify: `zettel/web/static/stil.css` (hinter den `.chatform`-Regeln)
- Test: `tests/test_web_chat.py`

- [x] **Schritt 1: Tests schreiben** (hinter `test_der_chat_hat_einen_eigenen_ort`)

```python
BEISPIELE = ["Alles für Spaghetti Bolognese, und Klopapier",
             "Lasagne für 6 Personen",
             "Milch, Butter, Eier"]


def test_der_leere_chat_zeigt_drei_beispiele_statt_einer_anleitung(db_datei, tmp_path):
    """UI-Review 2026-09-01, Fund 1: über dem Falz stand Erklärprosa, und
    ein Fremder wusste nach zehn Sekunden nicht, was die App kann. Drei
    Sätze zum Antippen zeigen es. Jeder Chip ist ein Absender des
    Chat-Formulars — ohne Skript ein zweiter „Fragen"-Knopf mit Vorgabe."""
    client, _ = _client(db_datei, tmp_path)
    seite = client.get("/chat").text
    chat = _chatteil(seite)
    for satz in BEISPIELE:
        assert (f'<button type="submit" form="chatform" name="satz" '
                f'class="beispiel" value="{satz}">') in chat
    assert 'id="chatform"' in chat
    assert "Vorgeschlagen wird nur" not in chat
    assert "Ein gemeinsamer Chat" not in seite


def test_ein_beispiel_schickt_den_satz_wirklich_ab(db_datei, tmp_path):
    milch = _pid(db_datei, MILCH)
    client, _ = _client(db_datei, tmp_path, _extract(("Milch", 1)),
                        _choose(("Milch", milch, 1)))
    r = client.post("/chat", data={"satz": BEISPIELE[2]}, headers=HTMX)
    assert r.status_code == 200
    assert BEISPIELE[2] in r.text
    assert "beispiel" not in _chatteil(r.text)    # nach dem ersten Zug weg


def test_mit_verlauf_gibt_es_keine_beispiele(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path)
    _langer_verlauf(db_datei, zuege=1, je_zug=1)
    assert 'class="beispiel"' not in client.get("/chat").text
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_chat.py -k "beispiel" -v`
Expected: der erste FAIL (kein `class="beispiel"`), der zweite FAIL ist erlaubt oder PASS (je nachdem, ob `_extract`/`_choose` den Satz durchreichen — er prüft vor allem, dass die Chips nach dem Zug weg sind), der dritte PASS (heute gibt es nie Chips).

- [x] **Schritt 3: Vorlagen ändern**

`zettel/web/templates/chat.html`: die Zeile

```jinja
<p class="fussnote">Ein gemeinsamer Chat — er hängt am Korb und wandert beim Abschicken mit der Bestellung mit.</p>
```

ersatzlos streichen (der Satz steht als Kommentar oben in `_chat.html`, wo er hingehört).

`zettel/web/templates/_chat.html`: `<form class="chatform" method="post" action="/chat"` wird `<form class="chatform" id="chatform" method="post" action="/chat"`. Dann den Block

```jinja
  {% for m in verlauf %}
  {% include "_zug.html" %}
  {% else %}
  <p class="fussnote">Schreib, was du brauchst — „alles für Spaghetti Bolognese, und Klopapier". Vorgeschlagen wird nur; in den Korb kommt nichts, was du nicht bestätigst.</p>
  {% endfor %}
```

ersetzen durch

```jinja
  {% for m in verlauf %}
  {% include "_zug.html" %}
  {% else %}
  {# Der erste Blick (UI-Review 2026-09-01, Fund 1): kein Erklärabsatz,
     sondern drei Sätze, die man antippen kann. Jeder Chip ist ein
     Absender des Chat-Formulars — `form="chatform"` und `name="satz"` —,
     also funktioniert er ohne Skript genauso wie „Fragen" mit Vorgabe. Die
     Zusage „in den Korb kommt nichts ohne dein Ja" steht danach an jeder
     Karte, wo sie gebraucht wird, nicht davor. #}
  <div class="beispiele">
    <p>Sag, was du brauchst — zum Beispiel:</p>
    {% for satz in ["Alles für Spaghetti Bolognese, und Klopapier",
                    "Lasagne für 6 Personen",
                    "Milch, Butter, Eier"] %}
    <button type="submit" form="chatform" name="satz" class="beispiel" value="{{ satz }}">{{ satz }}</button>
    {% endfor %}
  </div>
  {% endfor %}
```

**Achtung HTMX:** Das Formular trägt `hx-post="/chat"`. Ein `<button form="chatform" name="satz" value="…">` wird von HTMX 1.9+ beim Absenden mitgesammelt (`htmx` nimmt den auslösenden Absender und seine `name`/`value` auf) — im Test ist das ein normaler POST, im Browser prüft Schritt 5. Fällt der Wert im Browser weg, ist die Fallback-Lösung ein zweizeiliges Skript im bestehenden `<script>`-Block von `chat.html`:

```javascript
    document.addEventListener('click', function (e) {
      var b = e.target.closest('button.beispiel');
      if (!b) { return; }
      var feld = document.querySelector('#chatform input[name="satz"]');
      if (feld) { feld.value = b.value; }
    });
```

- [x] **Schritt 4: Stilblatt ergänzen** — hinter den `.chatform`-Regeln (`grep -n "\.chatform" zettel/web/static/stil.css`):

```css
/* Die drei Beispiele des leeren Chats (UI-Review 2026-09-01, Fund 1).
   Chips, keine Knöpfe: umrandet, in Akzenttinte, untereinander — auf
   390 px liegen drei Sätze nicht nebeneinander. */
.beispiele { margin: var(--v4) 0; }
.beispiele p { margin: 0 0 var(--v3); color: var(--gedaempft); font-size: var(--t-klein); }
.beispiel {
  display: block; width: 100%; min-height: var(--tap); margin-bottom: var(--v2);
  padding: var(--v2) var(--v4); text-align: left;
  font-size: var(--t-feld); font-weight: 500; font-family: inherit;
  color: var(--akzent-tinte); background: var(--karte);
  border: 1.5px solid var(--knopflinie); border-radius: var(--radius-knopf);
  cursor: pointer;
}
.beispiel:active { transform: scale(0.98); }
```

- [x] **Schritt 5: Grün sehen, dann im Browser**

Run: `.venv/bin/python -m pytest tests/test_web_chat.py tests/test_entwurf.py -q`
Expected: alle PASS.

Dann im Browser (Shop lokal, `/chat` mit leerem Verlauf): ein Chip antippen → der Satz erscheint als Nutzerzug, „Das Modell überlegt …" darunter. Wenn der Nutzerzug leer ankommt, das Fallback-Skript aus Schritt 3 einbauen.

- [x] **Schritt 6: Commit**

```bash
git add zettel/web/templates/chat.html zettel/web/templates/_chat.html zettel/web/static/stil.css tests/test_web_chat.py
git commit -m "Der leere Chat zeigt drei Beispiele zum Antippen statt einer Anleitung"
```

---

### Aufgabe 7: Summe im Korb, Bestellkarte als Zeilen, Quittung nach dem Abschicken (Fund 7)

Drei Stücke, drei Commits, eine Aufgabe — sie teilen die Frage „was kostet das, was habe ich bestellt, ist es angekommen".

#### 7a: Die Summe vor dem Bestellknopf

**Files:**
- Modify: `zettel/orders/korb.py` (neu `summe`), `zettel/orders/__init__.py` (Export)
- Modify: `zettel/web/app.py:830-849` (`_korb_kontext`)
- Modify: `zettel/web/templates/_korb.html:158-162` (vor `<form class="abschicken"`)
- Modify: `zettel/web/static/stil.css`
- Test: `tests/test_orders.py`, `tests/test_web_orders.py`

- [x] **Schritt 1: Tests schreiben**

`tests/test_orders.py`, ans Ende:

```python
def test_die_summe_rechnet_menge_mal_preis_und_zaehlt_preislose_mit():
    """UI-Review 2026-09-01, Fund 7: vor „Bestellung abschicken" stand keine
    Zahl. `summe` ist eine reine Funktion über die Korbzeilen, wie
    `orders.inhalt()` sie liefert — Freitext hat keinen Preis und wird
    gezählt, nicht geschätzt."""
    posten = [{"qty": 2, "price_cents": 119},
              {"qty": 1, "price_cents": 250},
              {"qty": 3, "price_cents": None}]
    assert korb_modul.summe(posten) == {"cents": 488, "ohne_preis": 1}
    assert korb_modul.summe([]) == {"cents": 0, "ohne_preis": 0}
```

`tests/test_web_orders.py`, hinter `test_warenkorb_zeigt_die_zeilen`:

```python
def test_der_warenkorb_nennt_die_summe_vor_dem_bestellknopf(client, con):
    pid = _pid(con, MILCH)                       # 1,19 €
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    client.post("/warenkorb/einlegen", data={"free_text": "Blumen"}, headers=HTMX)
    text = client.get("/warenkorb").text
    summe = text.split('class="summe"', 1)[1].split("</p>", 1)[0]
    assert "2,38 €" in summe
    assert "1 Posten ohne Preis" in summe
    assert text.index('class="summe"') < text.index("Bestellung abschicken")
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_orders.py tests/test_web_orders.py -k summe -v`
Expected: `AttributeError: … has no attribute 'summe'`; der Web-Test FAIL (kein `class="summe"`).

- [x] **Schritt 3: Implementieren**

`zettel/orders/korb.py`, hinter `inhalt()`:

```python
def summe(posten: list[dict]) -> dict:
    """Menge × Preis über die Korbzeilen; Zeilen ohne Preis werden GEZÄHLT.

    Eine reine Funktion über das, was `inhalt()` liefert — keine zweite
    Abfrage, die von der Zeilenliste abweichen könnte. Freitext hat keinen
    Preis, ein ausgemustertes Produkt einen alten: die Summe ist deshalb
    „etwa", und die Vorlage sagt das dazu (UI-Review 2026-09-01, Fund 7).
    """
    cents = 0
    ohne = 0
    for p in posten:
        if p.get("price_cents") is None:
            ohne += 1
        else:
            cents += int(p["qty"]) * int(p["price_cents"])
    return {"cents": cents, "ohne_preis": ohne}
```

`zettel/orders/__init__.py`: `summe` in den `from zettel.orders.korb import (…)`-Block und in `__all__` aufnehmen.

`zettel/web/app.py`, `_korb_kontext`: die Zeile `return {"posten": _posten_mit_bild(orders.inhalt(c), app.state.image_dir),` wird

```python
        posten = _posten_mit_bild(orders.inhalt(c), app.state.image_dir)
        return {"posten": posten,
                "summe": orders.summe(posten),
```

(die übrigen Schlüssel bleiben).

`zettel/web/templates/_korb.html`: `{% if posten %}` vor `<form class="abschicken"` wird

```jinja
{% if posten %}
{# Die Zahl vor dem Knopf (UI-Review 2026-09-01, Fund 7). „Etwa", weil
   Freitext keinen Preis hat und ein Katalogpreis alt sein kann — beides
   steht dabei, statt eine glatte Summe vorzutäuschen. #}
<p class="summe">Zusammen etwa <strong>{{ summe.cents | euro }}</strong>
  {%- if summe.ohne_preis %} · {{ summe.ohne_preis }} Posten ohne Preis{% endif %}</p>
<form class="abschicken" method="post" action="/warenkorb/abschicken">
```

`zettel/web/static/stil.css`, hinter `.abschicken { margin-top: var(--v4); }`:

```css
.summe { margin: var(--v4) 0 0; padding: 0 var(--v1); text-align: right;
         font-size: var(--t-text); color: var(--gedaempft);
         font-variant-numeric: tabular-nums; }
.summe strong { color: var(--tinte); font-size: 1.2em; }
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_orders.py tests/test_web_orders.py tests/test_web_chat.py -q`
Expected: alle PASS.

- [x] **Schritt 5: Commit**

```bash
git add zettel/orders/korb.py zettel/orders/__init__.py zettel/web/app.py zettel/web/templates/_korb.html zettel/web/static/stil.css tests/test_orders.py tests/test_web_orders.py
git commit -m "Der Korb nennt die Summe vor dem Bestellknopf — etwa, und sagt warum"
```

#### 7b: Die Bestellkarte zeigt Zeilen, nicht einen Klumpen

**Files:**
- Modify: `zettel/web/templates/bestellungen.html:37`
- Modify: `zettel/web/static/stil.css`
- Test: `tests/test_web_orders.py`

- [x] **Schritt 1: Test schreiben**

```python
def test_die_bestellkarte_zeigt_die_ersten_posten_als_zeilen(client, con):
    namen = [r["name"] for r in con.execute(
        "SELECT name FROM product ORDER BY id LIMIT 6")]
    for n in namen:
        client.post(f"/katalog/einlegen?product_id={_pid(con, n)}", headers=HTMX)
    client.post("/warenkorb/abschicken", follow_redirects=False)
    text = client.get("/bestellungen").text
    karte = text.split('<ul class="posten"', 1)[1].split("</ul>", 1)[0]
    zeilen = re.findall(r"<li>(.*?)</li>", karte, re.S)
    assert len(zeilen) == 4
    assert "und 2 weitere" in text
    assert ", ".join(namen) not in text
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_orders.py -k bestellkarte -v`
Expected: FAIL (kein `<ul class="posten"`).

- [x] **Schritt 3: Vorlage ändern** — in `bestellungen.html` die Zeile

```jinja
        <span class="menge">{{ b.posten | map(attribute='name') | join(', ') }}</span>
```

ersetzen durch

```jinja
        {# Vier Zeilen statt fünfzehn Namen mit Kommas in 13 px (UI-Review
           2026-09-01, Fund 7): die Karte ist ein Blick, die Pick-Liste
           dahinter ist die ganze Bestellung. Menge vorn, weil „2× Milch"
           die Zeile ist, die man wiedererkennt. #}
        <ul class="posten">
          {% for p in b.posten[:4] %}
          <li>{% if p.qty > 1 %}{{ p.qty }}× {% endif %}{{ p.name }}</li>
          {% endfor %}
        </ul>
        {% if b.posten | length > 4 %}
        <span class="menge">und {{ b.posten | length - 4 }} weitere</span>
        {% endif %}
```

`zettel/web/static/stil.css`, hinter `.bestellung.erledigt .name { … }`:

```css
.bestellung .posten { list-style: none; margin: var(--v1) 0 0; padding: 0;
                      font-size: var(--t-klein); line-height: 1.45; color: var(--tinte); }
.bestellung.erledigt .posten { color: var(--gedaempft); }
```

- [x] **Schritt 4: Grün sehen, Commit**

Run: `.venv/bin/python -m pytest tests/test_web_orders.py tests/test_web_politur.py -q`
Expected: alle PASS.

```bash
git add zettel/web/templates/bestellungen.html zettel/web/static/stil.css tests/test_web_orders.py
git commit -m "Die Bestellkarte zeigt vier Zeilen und zählt den Rest, statt fünfzehn Namen zu kleben"
```

#### 7c: Nach dem Abschicken steht eine Quittung

**Files:**
- Modify: `zettel/web/app.py:1089` (`ziel`), `:2356-2366` (`/bestellungen` liest `?fertig=`)
- Modify: `zettel/web/templates/bestellungen.html` (hinter dem `fehler`-Block)
- Test: `tests/test_web_orders.py`

- [x] **Schritt 1: Test schreiben**

```python
def test_nach_dem_abschicken_steht_eine_quittung(client, con):
    pid = _pid(con, MILCH)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    client.post(f"/katalog/einlegen?product_id={pid}", headers=HTMX)
    r = client.post("/warenkorb/abschicken", follow_redirects=False)
    assert r.status_code == 303
    ziel = r.headers["location"]
    assert ziel.startswith("/bestellungen?fertig=")
    text = client.get(ziel).text
    quittung = text.split('class="fertig"', 1)[1].split("</p>", 1)[0]
    assert "Abgeschickt" in quittung
    assert "1 Posten" in quittung
    assert "2,38 €" in quittung
    assert "Pick-Liste" in quittung
    # Ohne den Parameter — etwa beim zweiten Aufruf — keine Quittung.
    assert 'class="fertig"' not in client.get("/bestellungen").text
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_orders.py -k quittung -v`
Expected: FAIL — `location` ist `/bestellungen#b<id>`.

- [x] **Schritt 3: Implementieren**

`zettel/web/app.py`, in `warenkorb_abschicken`: `ziel = f"/bestellungen#b{bestellung['id']}"` wird

```python
            # `?fertig=` trägt die Quittung (UI-Review 2026-09-01, Fund 7):
            # nach dem Abschicken sah die Seite aus wie vorher plus eine
            # Karte, und ein Fremder wusste nicht, ob etwas passiert war.
            # Ein Query-Parameter und keine Sitzung — der Shop hat keine.
            ziel = f"/bestellungen?fertig={bestellung['id']}#b{bestellung['id']}"
```

`bestelluebersicht` wird:

```python
    @app.get("/bestellungen")
    def bestelluebersicht(request: Request, fertig: int | None = None):
        c = con()
        try:
            liste = orders.bestellungen(c)
            for b in liste:
                b["posten"] = orders.posten(c, b["id"])
            quittung = None
            if fertig is not None:
                gerade = next((b for b in liste if b["id"] == fertig), None)
                if gerade is not None:
                    quittung = {"n": len(gerade["posten"]),
                                "summe": orders.summe(gerade["posten"])}
            return vorlagen.TemplateResponse(request, "bestellungen.html", {
                **_rahmen(request, c), "bestellungen": liste,
                "quittung": quittung})
        finally:
            c.close()
```

`bestellungen.html`, hinter dem `{% if fehler %}…{% endif %}`-Block:

```jinja
{% if quittung %}
<p class="fertig" role="status">Abgeschickt: {{ quittung.n }}
  {{ "Posten" }}, zusammen etwa {{ quittung.summe.cents | euro }}.
  Die Bestellung steht jetzt auf der Pick-Liste.</p>
{% endif %}
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_web_orders.py tests/test_web_politur.py tests/test_web_chat.py -q`
Expected: alle PASS. `test_eine_unterseite_markiert_ihren_bereich_mit` folgt der Weiterleitung nicht und bleibt grün.

- [x] **Schritt 5: Commit**

```bash
git add zettel/web/app.py zettel/web/templates/bestellungen.html tests/test_web_orders.py
git commit -m "Nach dem Abschicken steht eine Quittung — Posten, Summe, wo es weitergeht"
```

---

### Aufgabe 8: Der Bestellknopf klebt am unteren Rand (Fund 8, Teil)

**Heute:** „Bestellung abschicken" kommt nach ~2 500 px. Der Knopf bleibt am Ende der Liste — er wird nur *sichtbar*, sobald der Korb länger als der Bildschirm ist: `position: sticky; bottom: 0` auf dem Formular. Die Summe aus 7a klebt mit, weil beide in ein `<div class="kasse">` wandern.

Die Rechen-Entschuldigungen („2 Stk lässt sich nicht gegen die Packung rechnen …") und das unbeschriftete „Egal wo" bleiben Welle 2 — beide brauchen eine Entscheidung (siehe unten).

**Files:**
- Modify: `zettel/web/templates/_korb.html` (Summe + Formular in `<div class="kasse">`)
- Modify: `zettel/web/static/stil.css`
- Test: `tests/test_web_politur.py`

- [x] **Schritt 1: Test schreiben**

```python
def test_summe_und_bestellknopf_kleben_am_unteren_rand():
    """Der Knopf stand nach 2 500 px Scrollweg (UI-Review 2026-09-01,
    Fund 8). Ob er auf einem Telefon wirklich im Bild bleibt, entscheidet
    das Gerät — hier steht, dass die Kasse überhaupt klebt und einen
    eigenen Grund hat, damit die Liste nicht durch sie hindurchscheint."""
    stil = STIL.read_text(encoding="utf-8")
    block = stil.split(".kasse {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in block
    assert "bottom: 0" in block
    assert "background: var(--grund)" in block


def test_die_kasse_umschliesst_summe_und_knopf(client, con):
    client.post(f"/katalog/einlegen?product_id={_pid(con)}", headers=HTMX)
    text = client.get("/warenkorb").text
    kasse = text.split('<div class="kasse">', 1)[1].split("</div>", 1)[0]
    assert 'class="summe"' in kasse
    assert "Bestellung abschicken" in kasse
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py -k kasse -v`
Expected: beide FAIL.

- [x] **Schritt 3: Vorlage und Stil ändern**

`_korb.html`: Der Block aus 7a wird

```jinja
{% if posten %}
{# Die Kasse klebt am unteren Rand (UI-Review 2026-09-01, Fund 8): der Knopf
   stand nach 2 500 px. Sie ist nur dann „unten", wenn der Korb länger als
   der Bildschirm ist — bei drei Zeilen steht sie einfach unter ihnen. #}
<div class="kasse">
  <p class="summe">Zusammen etwa <strong>{{ summe.cents | euro }}</strong>
    {%- if summe.ohne_preis %} · {{ summe.ohne_preis }} Posten ohne Preis{% endif %}</p>
  <form class="abschicken" method="post" action="/warenkorb/abschicken">
    <button class="gross" type="submit">Bestellung abschicken</button>
  </form>
</div>
{% endif %}
```

`stil.css`, hinter `.summe strong { … }`:

```css
.kasse {
  position: sticky; bottom: 0; z-index: 2;
  margin: var(--v4) calc(-1 * var(--rand)) 0; padding: var(--v2) var(--rand)
    calc(var(--v3) + env(safe-area-inset-bottom));
  background: var(--grund); border-top: 1px solid var(--linie);
}
.kasse .summe { margin-top: 0; }
.kasse .abschicken { margin-top: var(--v2); }
```

Der negative Seitenrand hebt den Innenabstand von `main` auf (`stil.css:160`: `padding: var(--rand) var(--rand) var(--v6)`), damit die Kasse bis an die Bildkante reicht.

- [x] **Schritt 4: Grün sehen; auf dem Bild prüfen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py tests/test_web_orders.py -q`
Expected: alle PASS. Dann im Browser bei 390 px mit 15 Posten: Kasse bleibt unten im Bild, Freitext-Feld darüber ist erreichbar, das Formular „Dazu" wird nicht verdeckt.

- [x] **Schritt 5: Commit**

```bash
git add zettel/web/templates/_korb.html zettel/web/static/stil.css tests/test_web_politur.py
git commit -m "Summe und Bestellknopf kleben am unteren Rand des Korbs"
```

---

### Aufgabe 9: Der Rezeptentwurf ist eingeklappt, bis man ihn will (Fund 2)

**Heute:** Nach „Fragen" stehen Ja/Nein-Karten *und* darunter der volle Rezeptentwurf mit Zahl/Einheit/ok/raus für dieselben Produkte. Zwei Bediensysteme mit gleichem Gewicht; der Entwurf ist der Nebenpfad.

**Neu:** Der offene Entwurf (weder gespeichert noch verworfen) wird ein `<details>` mit einer `<summary>`, die den Stand nennt: „Rezeptentwurf „Spaghetti Bolognese" — 0 von 6 Zutaten im Rezept" (`n_drin` zählt, was beim Abschicken ins Rezept ginge — ein noch offener Vorschlag zählt nicht, das ist die Regel aus `entwurf.py:_geht_ins_rezept`). Zugeklappt per Vorgabe; nach jedem Ja/Nein tauscht HTMX den ganzen Zug und der Entwurf ist wieder zu — das ist richtig, er ist der Nebenpfad. Die beiden anderen Zustände (gespeichert, verworfen) sind ein Satz und bleiben, wie sie sind. Das reduziert Fund 2 von L auf M; die Frage, ob die Entwurfszeilen überhaupt eigene ok/raus-Knöpfe brauchen, ist Welle 2.

**Files:**
- Modify: `zettel/web/templates/_entwurf.html:43-134` (der `{% else %}`-Zweig)
- Modify: `zettel/web/static/stil.css` (`.entwurf h3`)
- Test: `tests/test_entwurf.py`

- [x] **Schritt 1: Test schreiben** (hinter `test_der_entwurf_steht_im_warenkorb_und_nennt_das_klopapier_nicht`)

```python
def test_der_offene_entwurf_ist_eingeklappt_und_nennt_seinen_stand(
        con, db_pfad, tmp_path):
    """UI-Review 2026-09-01, Fund 2: Ja/Nein-Karten UND darunter der volle
    Entwurf für dieselben Produkte — zwei Bediensysteme mit einem Gewicht.
    Der Entwurf ist der Nebenpfad; zugeklappt sagt er nur, wie er steht."""
    _bolo_geholt(con)
    client = _web(db_pfad, tmp_path, _web_zug(con))
    stueck = client.post(
        "/chat",
        data={"satz": "alles für Spaghetti Bolognese, und Klopapier"},
        headers={"HX-Request": "true"}).text

    entwurf = stueck.split('class="entwurf"', 1)[1].split("</section>", 1)[0]
    assert "<details" in entwurf
    assert " open" not in entwurf.split("<details", 1)[1].split(">", 1)[0]
    summary = entwurf.split("<summary", 1)[1].split("</summary>", 1)[0]
    assert "Rezeptentwurf" in summary
    assert "Spaghetti Bolognese" in summary
    assert "0 von" in summary and "im Rezept" in summary
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_entwurf.py -k eingeklappt -v`
Expected: FAIL (`<details` fehlt).

- [x] **Schritt 3: Vorlage ändern** — in `_entwurf.html` den `{% else %}`-Zweig von `<h3>Rezeptentwurf</h3>` bis vor `</section>`:

```jinja
  {% else %}
  {# Eingeklappt (UI-Review 2026-09-01, Fund 2): über diesem Block stehen
     die Ja/Nein-Karten, und der Entwurf wiederholte dieselben Produkte
     mit eigenen Knöpfen in gleicher Grösse — zwei Bediensysteme. Der
     Entwurf ist der Nebenpfad. Die Zusammenfassung nennt den Stand; wer
     Mengen ändern oder eine Zeile herausnehmen will, klappt auf. Nach
     jedem Ja/Nein tauscht HTMX den Zug und der Entwurf ist wieder zu —
     gewollt, denn das Ja oben ist der Hauptweg. #}
  <details>
    <summary>Rezeptentwurf „{{ m.entwurf.name }}“ —
      {{ m.entwurf.n_drin }} von {{ m.entwurf.zeilen | length }}
      {{ "Zutat" if m.entwurf.zeilen | length == 1 else "Zutaten" }} im Rezept</summary>
  {# Der Name ist überschreibbar, und das ist keine Kosmetik: der Rezeptweg
     sucht ihn beim nächsten Mal wörtlich im Satz. #}
  <form class="entwurf-name" method="post"
        action="/chat/{{ m.id }}/entwurf/name"
        hx-post="/chat/{{ m.id }}/entwurf/name"
        hx-target="#zug-{{ m.id }}" hx-swap="outerHTML"
        hx-indicator="#zug-{{ m.id }}" hx-disabled-elt="find button">
    <input type="text" name="name" autocomplete="off"
           value="{{ m.entwurf.name }}"
           aria-label="Name des Rezepts">
    <button class="mini" type="submit">Name merken</button>
  </form>
```

… `<ul class="entwurfsliste">` bis zum `Kein Rezept daraus`-Formular bleiben **unverändert** …, und vor `</section>` kommt `</details>`:

```jinja
    <button class="mini zurueck" type="submit">Kein Rezept daraus</button>
  </form>
  </details>
  {% endif %}
</section>
```

`stil.css`, hinter `.entwurf h3 { … }`:

```css
.entwurf summary { min-height: var(--tap); display: flex; align-items: center;
                   font-size: var(--t-feld); font-weight: 600; cursor: pointer;
                   color: var(--gedaempft); }
.entwurf details[open] summary { color: var(--tinte); margin-bottom: var(--v2); }
```

- [x] **Schritt 4: Grün sehen**

Run: `.venv/bin/python -m pytest tests/test_entwurf.py tests/test_web_chat.py tests/test_zugersetzung.py -q`
Expected: alle PASS. Tests, die `"Rezeptentwurf" in stueck` prüfen, bleiben grün (das Wort steht in der `<summary>`).

- [x] **Schritt 5: Commit**

```bash
git add zettel/web/templates/_entwurf.html zettel/web/static/stil.css tests/test_entwurf.py
git commit -m "Der Rezeptentwurf ist eingeklappt — die Ja/Nein-Karten sind der Hauptweg"
```

---

### Aufgabe 10: Die Pick-Zeile ist eine Zeile — „gab's nicht" neben dem Text (Fund 11)

**Heute:** `.pickzeile > form { flex: 1 1 100% }` schiebt `.stellen` mit dem Knopf in eine eigene Zeile; darüber 40 px Leerraum, jede Zeile ~140 px, fünfzehn Knöpfe untereinander.

**Files:**
- Modify: `zettel/web/static/stil.css:617-619`
- Test: `tests/test_web_politur.py`

- [x] **Schritt 1: Test schreiben**

```python
def test_gabs_nicht_steht_neben_dem_text_und_nicht_darunter():
    """Fünfzehn Knöpfe je in eigener Zeile mit 40 px Luft darüber (UI-Review
    2026-09-01, Fund 11). Das Formular mit dem Haken nimmt den Rest, der
    Knopf so viel, wie er braucht — beide in EINER Zeile. Ob ein langer
    Name dann umbricht, statt den Knopf zu verdrängen, entscheidet das
    Gerät; hier steht, dass der Umbruch nicht mehr erzwungen ist."""
    stil = STIL.read_text(encoding="utf-8")
    form = stil.split(".pickzeile > form {", 1)[1].split("}", 1)[0]
    assert "100%" not in form
    assert "flex: 1 1 0" in form
    stellen = stil.split(".pickzeile .stellen {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 auto" in stellen
```

- [x] **Schritt 2: Rot sehen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py -k gabs_nicht -v`
Expected: FAIL.

- [x] **Schritt 3: Stil ändern** — die Zeilen

```css
.pickzeile > form { flex: 1 1 100%; min-width: 0; }
.pickzeile .stellen { flex: 1 1 auto; margin-left: auto;
                      padding: 0 var(--v1) var(--v3); }
```

werden

```css
/* Eine Zeile, nicht zwei (UI-Review 2026-09-01, Fund 11): mit `1 1 100%`
   fiel „gab's nicht" unter den Text, und jede Zeile war 140 px hoch. Das
   Formular nimmt den Rest, der Knopf seine Breite; unter 180 px Textbreite
   bricht die Zeile — dann ist der Name so lang, dass er ohnehin zwei
   Zeilen braucht. */
.pickzeile > form { flex: 1 1 0; min-width: 180px; }
.pickzeile .stellen { flex: 0 0 auto; margin-left: auto;
                      padding: 0 var(--v1); }
```

- [x] **Schritt 4: Grün sehen; auf dem Bild prüfen**

Run: `.venv/bin/python -m pytest tests/test_web_politur.py -q`
Expected: PASS. Im Browser `/pick/<id>` bei 390 px: Haken, Bild, Text, Knopf in einer Zeile; abgehakte Zeilen durchgestrichen; „gab's nicht"-Zeilen beerenfarben — wie vorher, nur kürzer. Wenn Namen wie „Alnatura BIO Passata fein passiert" den Knopf regelmässig in die zweite Zeile drücken, `min-width` auf 160px senken.

- [x] **Schritt 5: Commit**

```bash
git add zettel/web/static/stil.css tests/test_web_politur.py
git commit -m "Die Pick-Zeile ist eine Zeile — gab's nicht steht neben dem Text"
```

---

### Abschluss Welle 1

> Erledigt am 2026-09-01, Stand `731b264`. Welle 1 hat 20 Commits (`2ac6759`..`305af45`); Abweichungen vom Plan stehen im Nachtrag von `docs/superpowers/review/2026-09-01/REVIEW.md` — vor allem Aufgabe 10: der Knopf „gab's nicht" darf zwei Zeilen hoch sein, sonst brach „Champignons" mitten im Wort.

- [x] **Alles laufen lassen:** `.venv/bin/python -m pytest -q` — erwartet: grün, keine Warnung über neue Vorlagenfehler.
- [x] **Bildersatz neu aufnehmen** — dieselbe Strecke wie im Review (`docs/superpowers/review/2026-09-01/bilder/`, 390 px, Shop auf `127.0.0.1:8748` gegen eine *Kopie* der Demo-Datenbank — Anleitung in `docs/superpowers/specs/2026-09-01-ui-review-design.md`, Abschnitt Bildersatz) und in `docs/superpowers/review/2026-09-01/REVIEW.md` unter der Rangliste eine Zeile je geschlossenem Fund: „erledigt am …, Commit …". Nicht zu vergessen: das Demo-Skript `~/picknick-video/dreh_handy.py` klickt auf Koordinaten — Aufgabe 3, 6, 8 verschieben Elemente. Vor dem nächsten Take einen Trockenlauf.
- [x] **REVIEW.md ergänzen** — Fund 5: „167, nicht 107; Regel „< 1 und g/ml"; Fund 3: „Browse… No file selected" war die Browsersprache des Review-Firefox; der Knopf selbst ist seit WB-400 gestylt (`stil.css:1505`) — das Bild zeigte das Wort, nicht die Gestalt.

---

## Welle 2 — Entscheidungen, keine Aufgaben

Hier steht je Fund, was zu entscheiden ist und was ich empfehle. Erst nach der Entscheidung wird daraus ein Plan.

**Entschieden am 2026-09-02** — die Antworten und die Aufgaben daraus stehen in [`2026-09-02-ui-review-welle-2.md`](2026-09-02-ui-review-welle-2.md) (Fund 6 → a, Fund 9 → Streichung nach eigenem Urteil, Fund 3 → bleibt; alle anderen Zeilen nach Empfehlung, Fund 8 mit einer Abweichung). Die Tabelle bleibt als Stand der Empfehlungen stehen.

| Fund | Frage | Empfehlung |
|---|---|---|
| **6** Reiterleiste nie ganz | Nach Aufgabe 3 sind es sieben Ziele (~620 px bei 15 px Schrift) — sie scrollt weiter. Entweder (a) eine feste untere Tab-Leiste mit vier Zielen (Katalog · Chat · Korb · Pick) plus „Mehr" für Rezepte/Bestellungen/Bons, oder (b) die Leiste behalten und die Schrift/Abstände so setzen, dass sieben Ziele in 390 px passen (≈12 px Schrift — unter der Grenze des Blatts), oder (c) so lassen. | **(a)**, aber erst nach dem Contest: es ändert jede Seite und das Drehskript. Bis dahin (c). |
| **8** Rechen-Entschuldigungen im Korb | „2 Stk lässt sich nicht gegen die Packung rechnen" steht bei fast jeder Zeile. Der Satz stammt aus `mengen.rechne` und ist ehrlich. Frage: (a) Satz kürzen auf „Packung nicht ausgerechnet — 2 Stk gebraucht", (b) nur zeigen, wenn die Packungszahl *von Hand* gesetzt wurde, sonst still, (c) lassen. | **(b)** — die Zeile steht heute in ~80 % der Fälle und ist dort keine Information. Das braucht ein Feld `hand_qty` (gibt es) und einen Test in `test_mengen_chat.py`. |
| **8** „Egal wo"-Auswahlfeld ohne Beschriftung | Das Feld hat `aria-label`, aber kein sichtbares Wort. (a) Präfix „Laden:" ins Feld (`<option>` mit „Laden: egal"), (b) sichtbares `<label>` „Laden", (c) Feld aus dem Korb nehmen und nur auf der Pick-Liste anbieten. | **(c)** wäre die grösste Vereinfachung, ändert aber Spec 4 (Laden hängt am Posten). Vorerst **(a)** — eine Zeile in `_korb.html:128-133`. |
| **9** Anleitungsprosa in 13 px | Aufgabe 6 nimmt sie vom Chat. Übrig: `warenkorb.html`, `rezept.html`, `bons.html`, `bestellungen.html` (leerer Zustand), `status.html`. Entscheidung je Absatz: streichen, in einen `<details>`-Hinweis, oder lassen. | Durchgang von 30 Minuten zusammen mit Aaron am Handy — Absätze, die man beim dritten Lesen überspringt, fallen. Nicht ohne den Nutzer: „er hängt am Korb" war für die beiden mal wichtig. |
| **10** Rezeptliste ungepflegt | Dubletten sind Demo-Daten (Chefkoch-Import ohne Prüfung). „Einkauf vom 2026-08-28 · 0 Zutaten" entsteht in `orders.abschicken` → Rezept aus Bestellung. Fragen: (a) Demo-Datenbank aufräumen (Daten, kein Code), (b) `recipes` beim Chefkoch-Import auf gleichen Namen prüfen und nachfragen, (c) kein automatisches Rezept aus einer Bestellung ohne Zutaten. | **(a)** sofort vor dem Video (drei `DELETE` auf der Demo-Kopie, dann Kopie zurück), **(c)** als Bug-Ticket (Werkbank), **(b)** nach dem Contest. |
| **12** Textlinks als Bedienelemente | Gegenprobe: „raus", „zurück", „rückgängig", „Kein Rezept daraus" sind `button.mini.zurueck` mit 44 px Höhe (`stil.css:498`, `:966`) — das *Ziel* ist gross, nur der *Text* ist 13 px unterstrichen. Der Fund ist ein Wahrnehmungsbefund, kein Bedienfehler. (a) Rahmen zurückholen (`border-color: var(--knopflinie)`), (b) lassen. | **(b)**. WB-361 hat den Rahmen bewusst entfernt, damit „rückgängig" nicht wie eine dritte Wahl neben Ja/Nein aussieht. Der Review-Fund wird mit dieser Gegenprobe in REVIEW.md als „verworfen" nachgetragen. |
| **13** Monospace ohne Bedeutung | Aufgabe 1 nimmt sie aus der Zutatentabelle. 12 weitere Stellen (`grep -n "var(--mono)" stil.css`). (a) alle auf `inherit` + `tabular-nums`, `--mono` nur noch für Trace-IDs und Pfade auf /status, (b) lassen. | **(a)** — eine Stunde, ein Test, der `var(--mono)` ausserhalb von `.status`-Regeln verbietet. Nach dem Contest, weil es jedes Bild ändert. |
| **14** Sechs Knopfstile | Braucht eine Regel: Primär (gefüllt, Akzent) = genau ein Knopf je Seite; Sekundär (umrandet) = alles, was ein Feld abschickt; Entscheidung = Ja/Nein; Rückweg = `zurueck`. Dann `Suchen`/`Dazu`/`Fragen` angleichen. | Regel aufschreiben in `stil.css` als Kommentarblock, dann in einem Durchgang anwenden. Nach dem Contest. |
| **15** Rhythmus, gelber Hinweiskasten | „60 von 10066" als gelbe Box liest sich als Warnung. (a) `.band`-Stil für diese Meldung auf neutral, (b) lassen. Leere Seiten unter dem Falz: Folge von Fund 9 und 10. | **(a)** — eine Regel, fünf Minuten; kann in Aufgabe 9-Durchgang mit. |
| **16** Kleinkram | `.korbzahl` bei 0: heute `min-width: 22px` — der 12×11-Befund passt nicht zum Blatt; am Gerät nachmessen. „el"/„paket": Einheiten aus dem Chefkoch-Import, `mengen.falte` könnte sie schreiben wie das Kochbuch („EL", „Paket"). „Das Modell überlegt …" nur 13 px grau: auf `--t-klein` heben und den Wartezähler (gibt es in `chat.html`) sichtbar lassen. Klebendes Feld verdeckt erste Zeile: `scroll-margin-top` am Zug. „Züge ohne Trace" leer: einen Satz. | Je fünf Minuten; ein Sammel-Commit nach dem Contest. `.korbzahl` erst messen. |
| **3** Hostnamen auf /status | `vllm-box.local` und `localhost:6006` stehen auf einer Seite, die nach Aufgabe 3 nicht mehr im Hauptmenü ist. Für das Video reicht das. Ob die Namen dort überhaupt stehen sollen (Privatscan des Repos ist rot bei Hostnamen, siehe Commit `c37edf8`), ist eine eigene Frage. | Vor dem Video `/status` nicht zeigen; danach entscheiden. |

---

## Selbstprüfung des Plans

* **Abdeckung:** Funde 1, 2 (auf M reduziert), 3 (Rang, Leiste, OCR-Text; der Datei-Knopf war schon WB-400), 4, 5, 7, 8 (Knopf), 11 — Welle 1. Funde 6, 8 (Rest), 9, 10, 12, 13, 14, 15, 16 und die Hostnamen — Welle 2 mit Empfehlung. Kein Fund ohne Ort.
* **Platzhalter:** keine. Jede Aufgabe hat Testcode, Änderungscode, Kommando, erwartetes Ergebnis.
* **Namen quer über Aufgaben:** `orders.summe(posten) -> {"cents", "ohne_preis"}` wird in 7a definiert und in 7c und 8 benutzt (`summe.cents`, `summe.ohne_preis`). `knuspr.normalisiere_einheit(text, unit)`, `knuspr.repariere_einheiten(con)` nur in 5. `_chatteil()` und `_langer_verlauf()` gibt es in `tests/test_web_chat.py` bereits. `_bolo_geholt`, `_web`, `_web_zug` gibt es in `tests/test_entwurf.py` bereits. `<div class="kasse">` aus 8 setzt `class="summe"` aus 7a voraus — 8 kommt nach 7.
* **Was nur ein Gerät entscheidet:** Aufgabe 1 (Umbruch), 6 (HTMX + `form=`-Absender), 8 (Kasse im Bild), 10 (Knopf in der Zeile). Alle vier haben einen Bildschritt.
