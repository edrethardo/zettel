#!/usr/bin/env python3
"""Nimmt echte Chefkoch-Antworten als Test-Fixtures auf (WB-338).

Das ist — neben `record_fixture.py` für den Katalog — das EINZIGE, was
chefkoch.de anfasst. Die Testsuite geht nie ins Netz (Spec 13): sie liest die
beiden Dateien, die dieses Skript schreibt.

    python3 scripts/record_chefkoch.py --gericht pho

schreibt

    tests/fixtures/chefkoch_pho_suche.json     GET /v2/recipes?query=pho
    tests/fixtures/chefkoch_pho_rezept.json    GET /v2/recipes/<bestes>

Bricht danach ein Test, ist das ein echter Fund: Chefkoch hat das Format
geändert und `picknick.gerichte.chefkoch` muss nach.

**Höflich:** eine Pause zwischen den beiden Anfragen, ein ehrlicher
User-Agent, und von Hand gestartet statt in einer Schleife. robots.txt
erlaubt genau diese zwei Endpunkte; die Nutzungsbedingungen sind damit nicht
gelesen (siehe README).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from picknick.gerichte import chefkoch  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gericht", default="pho")
    ap.add_argument("--limit", type=int, default=chefkoch.LIMIT,
                    help="klein halten — die Fixture wird eingecheckt")
    ap.add_argument("--ziel", default=str(FIXTURES))
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
