#!/usr/bin/env python3
"""Nimmt eine echte Knuspr-Antwort als Test-Fixture auf.

Das ist das EINZIGE Skript im Projekt, das ins Netz geht — von Hand gestartet,
nie aus der Testsuite. Grund: Eine Testsuite, die live gegen knuspr.de läuft,
ist genau dann rot, wenn die Seite sich ändert — also im ungünstigsten Moment,
und ohne dass am eigenen Code etwas kaputt wäre.

    python3 scripts/record_fixture.py > tests/fixtures/knuspr_milch.json
    python3 scripts/record_fixture.py --begriff spuelmittel --limit 20

Wird die Fixture erneuert und bricht danach ein Test, ist das ein echter Fund:
Knuspr hat das Format geändert und der Parser muss nach.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from zettel.scrapers import knuspr  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--begriff", default="milch")
    ap.add_argument("--limit", type=int, default=25,
                    help="klein halten — die Fixture wird eingecheckt")
    args = ap.parse_args()

    try:
        import httpx
    except ImportError:
        print("httpx fehlt: pip install -r requirements.txt", file=sys.stderr)
        return 2

    url = knuspr.such_url(args.begriff, offset=0, limit=args.limit)
    print(f"hole {url}", file=sys.stderr)
    antwort = httpx.get(url, headers={"User-Agent": UA}, timeout=30.0)
    antwort.raise_for_status()
    payload = antwort.json()

    n = len(knuspr.parse_products(payload))
    print(f"{n} Produkte, totalHits={knuspr.total_hits(payload)}", file=sys.stderr)
    if n == 0:
        print("KEINE Produkte geparst — Format geändert?", file=sys.stderr)
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
