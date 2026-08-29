# GTC-Auftritt: Video, Fallstudie, Eval-Report — Design

**Datum:** 2026-08-29 · **Frist:** 10.09.2026 (NVIDIA GTC Golden Ticket Contest)
**Entschieden im Brainstorming, vom Nutzer freigegeben** („lets go", Artefakt-Seite
vom 29.08.). Variante B von dreien: jeder Kanal bekommt das Format, in dem er
stark ist. Kanäle: Contest-Jury, Arize-Bewerbung (läuft zeitnah), Portfolio.

## Rahmenentscheidungen (vom Nutzer getroffen)

1. **Nur Konserven.** Nichts läuft öffentlich — kein Demo-Server, kein
   öffentliches Phoenix. Video, Doku, GIFs, statische Artefakte.
2. **Volle Produktion beim Video:** Stimme + Gesichts-Einstieg + Zwei-Fenster-
   Regie (App neben live wachsendem Phoenix-Trace, nvtop-Streifen mit der 3090).
3. Aufbau auf dem vorhandenen Kit (WB-388/389): SHOWCASE.md, Drehbuch v1,
   POST.md, 7 Screenshots, Repo veröffentlichungsfähig.

## Stück 1 — Drehbuch v2 (`docs/contest/VIDEO.md`, ersetzt v1)

~65 Sekunden. Shot 0 (0–5 s): der Nutzer, ein Satz in die Kamera („I built a
grocery agent for my girlfriend and me — it runs on one RTX 3090 in my living
room."). Shots 1–7: Satz tippen → Rezeptkarte (Zeit, 5 Alternativen, Portionen
3→6 mit Neurechnung) → zwei „Ja" → Korb mit gerechneten Mengen, Klopapier als
Freitext → Pick-Liste mit „gab's nicht" → Phoenix-Zoom auf die Span-Kette samt
Labels. Endcard: die eine Zeile, Stack, Zahlen, Repo-Link.

Drei Ebenen, wo es zählt: links App (Handybreite), rechts Phoenix live, unten
nvtop. Wortwörtliches **englisches Voice-Over-Skript mit Zeitmarken**;
Untertitel bleiben (LinkedIn spielt stumm). Fensterlayout mit Koordinaten,
Aufnahmeweg ohne OBS, Refilm-Reset aus v1 übernehmen (Rezeptspeicher-Falle).
**Der Dreh braucht die Box live und hat Vorrang vor jedem Modell-Experiment.**

## Stück 2 — Fallstudie (`CASE-STUDY.md`, Englisch, Repo-Wurzel)

~2 Seiten, drei Bögen: (a) Butter-Fall als Einstieg — was ein Trace sieht, was
ein Test nie sieht; (b) der Salat-Bogen vollständig: Breitenmessung → 4 Gerichte
bei null → eingefrorene Probe isoliert EINEN String im Prompt → Ein-Zeilen-Fix
→ 76 % → 83 % über 64 Gerichte, mit den echten Tabellen; (c) das
Eval-Label-Design: die Ja/Nein-Tipps der Partnerin sind die Ground Truth,
geschrieben erst beim Abschicken, zurücknehmbar ohne Label-Müll. Plus ehrlicher
Grenzen-Abschnitt im Hausstil. Verlinkung aus SHOWCASE/README übernimmt Stück 3
(Dateibesitz-Trennung).

## Stück 3 — Eval-Report-Aufwertung (`SHOWCASE.md`, `docs/images/`)

SHOWCASE auf die aktuellen Zahlen (83 %, 1150 Tests, 76 Checks); Vorher/
Nachher-Diagramm als SVG im Repo (kein externer Dienst); 2–3 GIFs < 5 MB
(Rezeptkarte erscheint · Portionen 3→6 · Pick-Liste), aufgenommen gegen die
Demo-Instanz mit gelöschten Haushaltsdaten (geckodriver-Aufbau aus WB-389).
Rückfall ohne ffmpeg/ImageMagick: Bildstreifen. Links auf CASE-STUDY.md in
SHOWCASE und README setzen. Privatscan-Test läuft über alles; Pixel von Hand.

## Stück 4 — optional: Nemotron messen statt tunen

KEIN Finetune (kein Wertungsvorteil, keine Fähigkeitslücke, Box-Downtime).
Stattdessen, falls der Nutzer den Modellwechsel macht: Nemotron-Nano als
Inferenz-Kandidat durch `scripts/breite_probe.py --messen` — Vergleichstabelle
im selben Harness. Erst NACH dem Dreh; fällt bei Zeitnot ersatzlos weg.
Verwandt mit WB-342 (Modellvergleich, blockiert auf Modellwechsel).

## Was nicht passieren darf

Keine öffentliche Instanz. Keine Haushaltsdaten in Pixeln. Keine Zahl ohne
Messung. Kein Produktionscode-Eingriff (Tests/Checks bleiben unverändert grün).
Nichts wird gepostet oder gepusht — alles sind Entwürfe für den Nutzer.

## Zeitplan

31.08. Stücke 1–3 fertig (Werkbank-Tickets) · 03.09. Nutzer: Repo public,
Video drehen · 05.09. optional Stück 4 · 08.09. letzter Zahlenabgleich, Nutzer
postet · 10.09. Frist (zwei Tage Puffer).

## Abnahme

`pytest -q` und `checks/smoke.py` unverändert grün. Drehbuch v2 vollständig
abarbeitbar ohne Rückfragen; Fallstudie belegt jede Zahl mit Quelle im Repo;
SHOWCASE-Zahlen stimmen mit EVALS.md überein; GIFs < 5 MB und ohne private
Pixel.
