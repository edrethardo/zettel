#!/usr/bin/env python3
"""Zeichnet echte Chefkoch-Antworten auf — LOKAL, nicht fürs Repo (WB-604).

Das ist — neben `record_fixture.py` für den Katalog — das EINZIGE, was
chefkoch.de anfasst.

    python3 scripts/record_chefkoch.py --gericht pho

schreibt

    data/chefkoch/chefkoch_pho_suche.json     GET /v2/recipes?query=pho
    data/chefkoch/chefkoch_pho_rezept.json    GET /v2/recipes/<bestes>

**`data/chefkoch/` ist gitignoriert, und das ist der Punkt dieses Skripts.**
Bis WB-604 schrieb es nach `tests/fixtures/`, und damit standen zwei
vollständige fremde Rezepte samt Zutatenmengen, Zubereitung und den Namen
ihrer Einsteller in einem öffentlichen Repo. Die Nutzungsbedingungen von
chefkoch.de untersagen in §5.1 das automatische Auslesen, in §6.9 die
Nutzung der Kennzeichen und in §6.10 kommerzielles Text und Data Mining;
über die eigene Abwägung zum privaten Abruf mag man streiten, über das
Weiterverbreiten nicht. Die eingecheckten Fixtures
(`tests/fixtures/chefkoch_zwirbel_*.json`) sind deshalb ERFUNDEN — gleiche
Struktur, erfundenes Gericht, erfundene IDs.

Wozu dann noch aufzeichnen? Damit „die Struktur ist die gemessene" eine
prüfbare Aussage bleibt: liegt eine echte Antwort lokal, vergleicht
`test_die_erfundene_fixture_hat_die_form_der_echten_antwort` sie gegen die
erfundene. Ohne sie wird der Test übersprungen — die Suite geht nie ins Netz
(Spec 13), und CI darf ihn nicht verlangen.

**Höflich:** eine Pause zwischen den beiden Anfragen, ein ehrlicher
User-Agent, und von Hand gestartet statt in einer Schleife.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zettel.gerichte import chefkoch  # noqa: E402

#: Gitignoriert (siehe `.gitignore`). Fremde Inhalte bleiben auf der
#: Maschine, auf der sie geholt wurden.
AUFNAHMEN = Path(__file__).resolve().parents[1] / "data" / "chefkoch"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gericht", default="pho")
    ap.add_argument("--limit", type=int, default=chefkoch.LIMIT,
                    help="klein halten — es ist fremder Inhalt")
    ap.add_argument("--ziel", default=str(AUFNAHMEN),
                    help="Vorgabe: data/chefkoch/ (gitignoriert). Ein Ziel "
                         "unter tests/fixtures/ ist ein Fehler — das Gate "
                         "checks/veroeffentlichung.py schlägt darauf an.")
    args = ap.parse_args()

    try:
        import httpx
    except ImportError:
        print("httpx fehlt: pip install -r requirements.txt", file=sys.stderr)
        return 2

    http = httpx.Client(timeout=30.0,
                        headers={"User-Agent": chefkoch.USER_AGENT})
    ziel = Path(args.ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    marke = "".join(c for c in args.gericht.lower() if c.isalnum())

    url = chefkoch.such_url(args.gericht, limit=args.limit)
    print(f"hole {url}", file=sys.stderr)
    suche = http.get(url)
    suche.raise_for_status()
    payload = suche.json()

    treffer = chefkoch.parse_treffer(payload)
    print(f"{len(treffer)} Treffer, count={payload.get('count')}",
          file=sys.stderr)
    if not treffer:
        print("KEINE Treffer geparst — Format geändert?", file=sys.stderr)
        return 1
    for t in treffer:
        print(f"  {t['rezept_id']:>18}  {t['rating']:.2f} aus "
              f"{t['votes']:>4} Stimmen  gewicht {chefkoch.gewicht(t):.3f}"
              f"  {'PLUS ' if t['plus'] else '     '}{t['titel'][:44]}",
              file=sys.stderr)
    bestes = chefkoch.bestes(treffer)
    print(f"-> gewählt: {bestes['titel']}", file=sys.stderr)

    time.sleep(chefkoch.PAUSE_S)
    detail_url = chefkoch.detail_url(bestes["rezept_id"])
    print(f"hole {detail_url}", file=sys.stderr)
    detail = http.get(detail_url)
    detail.raise_for_status()
    roh = detail.json()

    rezept = chefkoch.parse_rezept(roh)
    print(f"{len(rezept['zutaten'])} Zutaten, {len(rezept['schritte'])} "
          f"Schritte, {rezept['servings']} Portionen", file=sys.stderr)
    if not rezept["zutaten"]:
        print("KEINE Zutaten geparst — Format geändert?", file=sys.stderr)
        return 1

    for name, inhalt in ((f"chefkoch_{marke}_suche.json", payload),
                         (f"chefkoch_{marke}_rezept.json", roh)):
        datei = ziel / name
        datei.write_text(json.dumps(inhalt, ensure_ascii=False, indent=1)
                         + "\n", encoding="utf-8")
        print(f"geschrieben: {datei} ({datei.stat().st_size} B)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
