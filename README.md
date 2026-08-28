# Picknick

Ein privater Bestell-Shop für zwei Personen im Tailnet. Eine Person legt Lebensmittel in einen Warenkorb und schickt die
Bestellung ab, eine zweite kauft sie physisch im Laden ein und hakt sie
dort auf dem Handy ab. **Es wird nie eine
Bestellung an einen echten Händler geschickt.** Dazu ein Chat-Feld: freier Text
(„alles für Spaghetti Bolognese, und Klopapier") wird auf echte
Katalogprodukte abgebildet und als Vorschlag vorgelegt.

Zweiter, gleichrangiger Zweck: Der Chat-Agent ist in Arize Phoenix vollständig
beobachtbar, bewertbar und reproduzierbar vergleichbar. Der Entwurf steht in
`docs/superpowers/specs/2026-08-28-picknick-design.md`.

## Wozu die Observability gut ist — in einem Fall

Auf „…dazu brauche ich noch Zahnpasta und Butter" wählte das Modell eine
**ButterBoyz-Spezialbutter für 4,69 €**. Ein Fehlgriff. Die naheliegende
Erklärung ist ein schlechtes Modell; der Trace sagt, dass sie falsch ist:

```
„Butter“ — 5 Kandidaten vorgelegt        picknick.rejected = 0
   4,01  #1771  ButterBoyz BIO Butter Chili & Röstzwiebel
   4,01  #1772  ButterBoyz BIO Butter Feige & Anis
   3,96  #1757  ButterBoyz BIO Kräuterbutter
   3,96  #1766  ButterBoyz BIO Salzbutter      ← gewählt
   3,96  #1768  ButterBoyz BIO Steinpilzbutter
„Zahnpasta“ — 0 Kandidaten vorgelegt
```

`rejected = 0` heisst: das Modell hat nichts erfunden, es hat aus der Liste
gewählt — und in der Liste stand keine normale Butter. **Der Fehlgriff gehört
dem Retrieval, nicht dem Modell.** Genau deshalb ist `catalog.search` ein
`RETRIEVER`-Span und kein `TOOL`. Der ganze Vertrag steht in
[`OBSERVABILITY.md`](OBSERVABILITY.md).

## Die Dokumente

| Datei | worum es geht |
|---|---|
| [`DESIGN.md`](DESIGN.md) | Architektur und die Entscheidungen, die von aussen wie ein Versehen aussehen |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | **der Span-Vertrag**: welcher Span, welche Attribute, was sie bedeuten |
| [`EVALS.md`](EVALS.md) | Dataset, Evaluatoren, die vier Varianten und die echten Zahlen |
| [`DEMO.md`](DEMO.md) | der Klickpfad zum Vorführen, eine Prüfung pro Klick |
| [`GATES.md`](GATES.md) | was das Gate abdeckt — und was ausdrücklich nicht |

## Schnellstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m picknick.scrapers.nachtlauf --begriff milch   # etwas Katalog
.venv/bin/python -m picknick.web.app
```

Der Shop lauscht dann auf `http://100.64.0.1:8730` (Tailscale) und auf
`http://127.0.0.1:8730`. Auf `0.0.0.0` bindet er nicht — der Versuch bricht mit
einer Fehlermeldung ab, weil es kein Passwort gibt und das Tailnet der einzige
Schutz ist.

Für den Chat braucht es die lokale vLLM-Box; ohne sie läuft alles ausser dem
Chat weiter, und der Rezeptweg sogar auch. Für Traces und Evals ein Phoenix
auf `localhost:6006` — ohne läuft der Shop unverändert, siehe
[`OBSERVABILITY.md`](OBSERVABILITY.md).

## Prüfen

```bash
.venv/bin/python checks/smoke.py     # das Gate: 37 Checks, exit 0 / 1
.venv/bin/python -m pytest -q        # 433 Tests
```

Beides ohne Netz, ohne Modell, ohne Phoenix — und im Fall des Gates ist das
nicht zugesichert, sondern **erzwungen**: `checks/smoke.py` sperrt vor dem
ersten Projektimport `connect`, `bind` und `getaddrinfo` im eigenen Prozess
und prüft als Erstes, dass eine Verbindung nach `localhost:6006` scheitert.
Was das abdeckt und was ausdrücklich nicht, steht in
[`GATES.md`](GATES.md).

## Der Katalog: was er ist und was er nicht ist

**Die Preise sind Knuspr-Preise, nicht Rewe- oder Lidl-Preise.** Eingekauft
wird bei Rewe und Lidl; der Katalog stammt von knuspr.de, weil es die einzige
Quelle mit einer benutzbaren Suche war. Die Preise sind damit Richtwerte für
die Planung, nicht die Summe an der Kasse. Und **Handelsmarken wie `ja!` oder
`Milbona` fehlen** — genau die also, nach denen im Laden am ehesten gegriffen
wird. Der Satz steht auch in der Oberfläche unter jeder Kachelliste, nicht nur
hier.

Zwei weitere Dinge, die man kennen muss, bevor man dem Katalog etwas anlastet:

* **Die Suche kennt nur Wortanfänge.** „milch" findet „Landmilch" nicht über
  den Namen (nur über die Kategorie), „Klopapier" findet nie
  „Toilettenpapier" — dort ist das Umformulieren ausdrücklich die Aufgabe des
  Modells.
* **Der Katalog ist so breit wie die Begriffsliste** in
  `picknick/scrapers/begriffe.py` — Knuspr hat keinen Endpunkt für den ganzen
  Katalog, nur die Suche. Von den 170 Begriffen liefern derzeit **59 gar
  keinen Treffer** (gemessen 2026-08-28, 2.498 Produkte): der Vollcrawl ist
  nie durchgelaufen. „Zahnpasta" im Beispiel oben ist so eine Lücke — sie
  steht in der Liste, sie wurde nur nie geholt.

