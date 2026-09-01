# Adversarial UI-Review — Entwurf (2026-09-01)

**Messlatte:** erster Eindruck für Fremde (Juroren, Arize, LinkedIn).
**Gerät:** Handy hochkant, 390 × 844, zuerst. **Umfang:** alle Seiten.
**Ergebnis:** Fundliste mit Rangfolge; Fixes werden danach einzeln entschieden.

## 1. Bildersatz

* Zweiter Shop-Prozess auf `127.0.0.1:8748` gegen eine **Kopie** von
  `~/picknick-demo/demo.db`; `pruefstand.frisch()` auf der Kopie, damit kein
  Hinweisband die Bilder verfälscht. Die Demo-Datenbank bleibt unberührt.
* Headless Firefox mit Wegwerfprofil, `--window-size=390,844`, ganze Seite.
  Nachträglich eine Falzlinie bei 844 px.
* Statische Seiten: chat (leer), katalog, katalog?q=lasagne, rezepte, ein
  rezept, warenkorb (leer und gefüllt), pick, bestellungen, bons, ein bon,
  status, rolle, nicht_gefunden.
* Chat-Zustände aus dem Rohband des Handy-Clips (ohne gemalte Finger):
  Satz getippt, Warten, Vorschlag mit „gabs nicht", Klopapier-Zeile,
  Sammelknöpfe, Warenkorb, Bestellknopf, Pick-Liste.
* Ablage: `docs/superpowers/review/2026-09-01/bilder/`.

## 2. Drei Reviewer

Drei Agenten parallel, sehen nur den Bilderordner, kennen einen Satz Kontext:
„Ein privater Bestell-Shop für zwei Personen, der aus einem Satz einen
Einkaufswagen macht."

| Rolle | Frage |
|---|---|
| Juror, 10 s | Was ist das? Warum interessant? Würde ich weiterklicken? Erst nur der Falz. |
| Design-Lead | Hierarchie, Typografie, Leerraum, Konsistenz, was billig wirkt. |
| Mobile-Prüfer | Zielgrößen, Daumenzone, Kontrast, Textgrößen, Abgeschnittenes. |

Ausgabe je Reviewer: Bildname · Fund (ein Satz, was ein Fremder denkt) ·
Schwere 1–3 · Belegstelle. Pflicht: drei Dinge, die funktionieren.

## 3. Gegenprobe und Rangfolge

* Jeder Fund wird am CSS/Template geprüft: echt, Screenshot-Artefakt oder
  Geschmack? Kontrast und Zielgrößen werden nachgemessen (Stil-Werte aus
  `stil.css`, Pixel aus dem Bild), nicht geschätzt.
* Doppelte über Reviewer hinweg zusammenlegen; ein Fund, den zwei Reviewer
  unabhängig nennen, wiegt schwerer.
* Rangfolge nach **Wirkung auf den ersten Eindruck × Häufigkeit im Demo-Pfad**,
  dann Aufwand (S/M/L) als zweite Spalte — nicht als Sortierkriterium.
* Verworfene Funde stehen mit Begründung am Ende, nicht gelöscht.

## 4. Dokument

`docs/superpowers/review/2026-09-01/REVIEW.md`: Gesamturteil (drei Sätze),
Rangliste, was funktioniert, verworfene Funde, Bilder verlinkt. Kein Fix
wird im Rahmen des Reviews umgesetzt.
