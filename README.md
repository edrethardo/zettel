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

Tests: `.venv/bin/python -m pytest` (ohne Netz, ohne Modell, ohne Phoenix).

