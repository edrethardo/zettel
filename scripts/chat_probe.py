"""Handprobe des Chat-Agenten gegen die echte vLLM-Box. KEIN Test.

Die Testsuite prüft gegen einen Fake-LLM: sie geht nie ins Netz und weckt nie
die Box (Spec 13). Was ein Fake nicht beantworten kann, ist die einzige Frage,
auf die es am Ende ankommt — **trifft der Agent mit einem echten Modell und
einem echten Katalog das Richtige?** Diese Zahl wird in WB-329/330 gemessen;
hier wird sie zum ersten Mal sichtbar gemacht, ungeschönt.

    .venv/bin/python scripts/chat_probe.py --crawl        # Katalog holen
    .venv/bin/python scripts/chat_probe.py
    .venv/bin/python scripts/chat_probe.py --kein-guided  # ohne guided_json
    .venv/bin/python scripts/chat_probe.py --satz "Milch und Klopapier"

Gezeigt wird jede Stufe einzeln, weil genau das die Frage beantwortet, die
sich sonst nicht beantworten lässt: lag ein Fehlgriff am Modell oder daran,
dass die Suche das richtige Produkt nie vorgelegt hat (Spec 7.1).

`--crawl` holt einen kleinen Katalog von knuspr.de — das ist der einzige
Netzzugriff, den dieses Skript ausser dem Modell macht, und er läuft durch
denselben Crawler wie der nächtliche Lauf. Der Web-Prozess selbst ruft nie
eine fremde Seite auf (Spec 3).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from picknick import db  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.assistant import plan  # noqa: E402
from picknick.catalog import search  # noqa: E402
from picknick.llm import wake  # noqa: E402
from picknick.llm.client import Modellzugang  # noqa: E402
from picknick.scrapers import knuspr  # noqa: E402

SATZ = "alles für Spaghetti Bolognese, und Klopapier"

#: Ein kleiner, aber ehrlicher Ausschnitt: die Zutaten aus dem Beispielsatz und
#: ein paar Nachbarn, damit die Suche etwas zu unterscheiden hat. Die Liste für
#: den nächtlichen VOLLcrawl steht seit WB-331 nicht mehr hier, sondern in
#: `picknick/scrapers/begriffe.py` — hier bleibt bewusst ein Ausschnitt, weil
#: eine Handprobe nicht 20 Minuten crawlen soll.
CRAWL_BEGRIFFE = [
    "hackfleisch", "tomaten", "nudeln", "spaghetti", "zwiebeln", "knoblauch",
    "toilettenpapier", "milch", "butter", "kaese", "spuelmittel", "moehren",
]


def warte_bis_wach(frist_s: float = 300.0) -> bool:
    """Wecken darf hier dauern — im Web-Prozess nicht (Spec 11)."""
    w = wake.Wecker()
    begonnen = time.monotonic()
    while time.monotonic() - begonnen < frist_s:
        z = w.zustand()
        if z.bedient:
            print(f"Box bedient: {z.modell}\n")
            return True
        rest = "" if z.rest_s is None else f", noch ~{z.rest_s:.0f} s"
        print(f"  {z.zustand}{rest} — {z.grund}")
        if z.zustand == wake.NICHT_ERREICHBAR:
            return False
        time.sleep(10)
    print("Frist abgelaufen.")
    return False


def krawl(con) -> None:
    http = httpx.Client(timeout=30.0, headers={"User-Agent": "picknick-probe"})
    try:
        bericht = knuspr.crawl(con, http, CRAWL_BEGRIFFE,
                               image_dir="data/images")
    finally:
        http.close()
    print(f"Katalog-Lauf: {bericht}\n")


def euro(cents) -> str:
    return "—" if cents is None else f"{cents // 100},{cents % 100:02d} €"


def zeige_stufen(zugang, con, satz: str, kandidaten: int, guided: bool) -> None:
    """Die drei Stufen einzeln — mit Zwischenergebnissen und Zeiten."""
    print("=" * 72)
    print(f"Satz: {satz!r}   (guided_json: {'an' if guided else 'aus'}, "
          f"{kandidaten} Kandidaten je Begriff)")
    print("=" * 72)

    t0 = time.monotonic()
    begriffe = plan.extract(zugang, satz, guided=guided)
    t1 = time.monotonic()
    print(f"\n[1] plan.extract — {t1 - t0:.1f} s")
    for b in begriffe:
        print(f"    {b['menge']} × {b['begriff']}")

    print("\n[2] catalog.search — der SHOP sucht, nicht das Modell")
    aufgaben = []
    for b in begriffe:
        treffer = search.search(con, b["begriff"], limit=kandidaten)
        aufgaben.append({**b, "kandidaten": treffer})
        if not treffer:
            print(f"    „{b['begriff']}“: KEIN TREFFER")
            continue
        print(f"    „{b['begriff']}“:")
        for t in treffer:
            print(f"        {t['id']:>7}  Rang {t['rang']:6.2f}  {t['name']} "
                  f"({t['unit_text'] or '?'}, {euro(t['price_cents'])})")

    t2 = time.monotonic()
    auswahl = plan.choose(zugang, satz, aufgaben, guided=guided)
    t3 = time.monotonic()
    print(f"\n[3] plan.choose — {t3 - t2:.1f} s")
    for w in auswahl.gewaehlt:
        p = w["produkt"]
        print(f"    „{w['begriff']}“ -> {p['id']} {p['name']} "
              f"({w['menge']} ×, {euro(p['price_cents'])})")
    for v in auswahl.verworfen:
        print(f"    VERWORFEN: {v}")
    ohne = [a["begriff"] for a in aufgaben
            if a["begriff"] not in {w["begriff"] for w in auswahl.gewaehlt}]
    if ohne:
        print("    ohne Produkt (wird Freitext): " + ", ".join(ohne))

    n = len(begriffe)
    print(f"\n    Trefferquote roh: {len(auswahl.gewaehlt)}/{n} Begriffe "
          f"haben ein Katalogprodukt bekommen "
          f"({100 * len(auswahl.gewaehlt) / n:.0f} %).")
    print("    (Ob es das RICHTIGE ist, sagt diese Zahl nicht — dafür liest "
          "man die Liste.)")


def zeige_zug(agent, con, satz: str) -> None:
    """Derselbe Weg noch einmal, aber durch `Chat.turn()` wie im Shop."""
    print("\n" + "=" * 72)
    print("Der ganze Zug durch Chat.turn() — was in der Oberfläche stünde")
    print("=" * 72)
    t0 = time.monotonic()
    ergebnis = agent.turn(con, satz)
    print(f"Weg: {ergebnis.weg}   ({time.monotonic() - t0:.1f} s)")
    print(f"Meldung: {ergebnis.meldung}\n")
    for v in ergebnis.vorschlaege:
        art = "FREITEXT" if v["ist_freitext"] else f"#{v['product_id']}"
        rang = "" if v["rang"] is None else f"  Rang {v['rang']:.2f}"
        print(f"  [{v['decision']}] {v['qty']} × {v['name']}  ({art}, "
              f"gesucht: {v['search_term']!r}{rang})")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=db.DEFAULT_DB)
    p.add_argument("--satz", default=SATZ)
    p.add_argument("--kandidaten", type=int, default=plan.KANDIDATEN)
    p.add_argument("--crawl", action="store_true",
                   help="vorher einen kleinen Katalog von knuspr.de holen")
    p.add_argument("--kein-guided", dest="guided", action="store_false",
                   help="ohne vLLMs guided_json — trägt der Prompt allein?")
    args = p.parse_args()

    con = db.connect(args.db)
    db.migrate(con)
    if args.crawl:
        krawl(con)
    n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                    ).fetchone()["n"]
    print(f"Katalog: {n} aktive Produkte in {args.db}")
    if not n:
        print("Leerer Katalog — mit --crawl einen holen.")
        return 1
    if not warte_bis_wach():
        return 1

    zugang = Modellzugang()
    print(f"Modell: {zugang.modell()}\n")
    zeige_stufen(zugang, con, args.satz, args.kandidaten, args.guided)
    agent = chatmodul.Chat(zugang, kandidaten=args.kandidaten,
                           guided=args.guided)
    zeige_zug(agent, con, args.satz)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
