"""Handprobe: der Artikel, der neben dem Gericht im Satz steht (WB-370).

KEIN Test. Diese Probe geht ins Netz (Chefkoch) und an die echte vLLM-Box.
Sie beantwortet die Frage, die kein Fake-LLM beantworten kann: **wie oft
liefert Stufe 1 auf dem Chefkoch-Weg einen Begriff, der zu keiner Zutat des
Rezepts gehört — und wie oft greift das Modell den Rest des Satzes auf?**

    .venv/bin/python scripts/rest_probe.py --messen --db kopie.db
    .venv/bin/python scripts/rest_probe.py --gericht "Quiche Lorraine"

`--messen` fährt je Gericht zwei Läufe von Stufe 1, einmal ohne und einmal
mit einem Rest im Satz, und zählt aus. Der Lauf ohne Rest ist der des Shops
und zugleich die saubere Grundrate: JEDER Begriff ohne Herkunftszutat ist
dort einer, den das Sicherheitsnetz von WB-337 fälschlich für den Rest
gehalten hätte. Der Lauf mit Rest stellt den Prompt von VOR WB-370 noch
einmal (`ZEILE_ALT`) — nur so lässt sich zeigen, was die Zeile gebracht hat
und was sie kostete.

Ohne `--messen` läuft EIN ganzer Chat-Zug („alles für <Gericht>, und
Klopapier") und zeigt den Korb. Ungeschönt: mit den Freitext-Zeilen, mit der
Meldung und mit dem, was `herkunft.zuordnen` zugeordnet hat.

**Immer auf einer KOPIE der Datenbank** (`--db`), nie auf `data/picknick.db`:
ein Chat-Zug schreibt Nachrichten und Vorschläge in den Warenkorb der beiden.

Das Ergebnis vom 2026-08-28 (Qwen3.8-27B, 35 Chefkoch-Gerichte) steht in
`chat._rest_sichern` und in OBSERVABILITY.md. Kurz:

    mindestens ein Begriff ohne Herkunftszutat   11 von 35 (31 %)
    der Rest kam als Begriff zurück               3 von 35
    der Rest kam ÜBERSETZT zurück                 0 von 35
    der Rest ging dadurch still verloren         10 von 35 (29 %)

Nach WB-371 (2026-08-29, dieselben 35 Gerichte) sind es 8 von 335 Begriffen
ohne Herkunftszutat und 6 von 35 Läufen — „Ei(er)" wird jetzt gefunden.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from picknick import db  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.assistant import herkunft, plan  # noqa: E402
from picknick.gerichte import chefkoch, quelle  # noqa: E402
from picknick.gerichte import lauf as gerichtelauf  # noqa: E402
from picknick.llm import wake  # noqa: E402
from picknick.llm.client import Modellzugang  # noqa: E402

#: Die Gerichte der Messung. Breit gestreut und nicht ausgesucht: eine Quote
#: über drei Gerichte ist keine Quote, und eine über die drei Gerichte, an
#: denen der Fehler auffiel, ist eine geschönte.
GERICHTE = [
    "Spaghetti Bolognese", "Pho Bo", "Käse-Lauch-Suppe", "Ratatouille",
    "Chili con Carne", "Kartoffelsalat", "Lasagne", "Gulasch",
    "Kaiserschmarrn", "Zwiebelkuchen", "Rouladen", "Königsberger Klopse",
    "Linsensuppe", "Gemüseauflauf", "Hühnerfrikassee", "Krautsalat",
    "Curry mit Kokosmilch", "Falafel", "Shakshuka", "Bratkartoffeln",
    "Erbsensuppe", "Griesbrei", "Quiche Lorraine", "Moussaka",
    "Paella", "Tortilla", "Risotto", "Frikadellen",
    "Apfelkuchen", "Kürbissuppe", "Nudelauflauf", "Bibimbap",
    "Tom Kha Gai", "Borschtsch", "Wirsingeintopf",
]

#: Reihum, damit die Zahl nicht an einem einzigen Wort hängt. „Klopapier"
#: findet der Katalog nicht, „Spülmittel" und „Zahnpasta" schon,
#: „Katzenstreu" seit WB-343 nicht mehr — die Mischung ist Absicht.
RESTE = ["Klopapier", "Spülmittel", "Zahnpasta", "Katzenstreu",
         "Taschentücher"]

#: Gegen die Box parallel. Sie verträgt es, und 70 Läufe nacheinander wären
#: eine halbe Stunde statt zweieinhalb Minuten.
PARALLEL = 6


def rezepte_holen(con, gerichte: list[str]) -> list[dict]:
    """Die Zutatenlisten — aus dem Speicher, sonst von Chefkoch."""
    q = quelle.Quelle()
    http = httpx.Client(timeout=chefkoch.TIMEOUT_S,
                        headers={"User-Agent": chefkoch.USER_AGENT})
    daten, gesehen = [], set()
    try:
        for name in gerichte:
            voll = q.gericht(con, name)
            if voll is None:
                gerichtelauf.hole_eines(con, http, name,
                                        schreib=lambda z: print("  " + z))
                voll = q.gericht(con, name)
            if voll is None or not voll["zutaten"]:
                continue
            # Zwei Gerichtsnamen können auf dasselbe Rezept zeigen („Gyros"
            # und „Nudelauflauf"). Zweimal derselbe Lauf wäre eine Zahl, die
            # sich selbst bestätigt.
            rid = int(voll["rezept"]["id"])
            if rid in gesehen:
                continue
            gesehen.add(rid)
            daten.append({"gericht": name, "zutaten": voll["zutaten"],
                          "servings": voll["rezept"].get("servings"),
                          "rest": RESTE[len(daten) % len(RESTE)]})
    finally:
        http.close()
    return daten


#: Die Prompt-Zeile, die es bis WB-370 gab. Sie steht HIER und nicht mehr in
#: `plan.zutatenliste`: im Shop ist sie weg, und eine Messung, die zeigen
#: soll, warum, muss sie trotzdem noch stellen können.
ZEILE_ALT = "Ausserdem gewünscht, nicht aus dem Rezept: {rest}"


def _stufe_1(zugang, eintrag: dict, rest: str | None) -> list[dict]:
    """Stufe 1 auf dem Chefkoch-Weg — mit oder ohne die alte Prompt-Zeile.

    Ohne `rest` ist das buchstäblich der Aufruf des Shops. Mit `rest` wird
    der Prompt um die Zeile von vor WB-370 ergänzt; alles andere (Schema,
    Temperatur, Zuordnung) bleibt gleich, sonst verglichen sich zwei
    verschiedene Dinge.
    """
    text = plan.zutatenliste(eintrag["zutaten"], gericht=eintrag["gericht"],
                             servings=eintrag["servings"])
    if rest:
        text += "\n" + ZEILE_ALT.format(rest=rest)
    antwort = plan._frage(zugang, plan.SYSTEM_ZUTATEN, text,
                          plan.SCHEMA_EXTRACT, "begriffe", True,
                          plan.TEMPERATUR, plan.MAX_TOKENS, plan.DENKEN)
    roh = plan._zutaten_aus(
        plan._eintraege(antwort, ("begriffe", "zutaten", "items", "liste")))
    return herkunft.zuordnen(eintrag["zutaten"], roh)


def _lauf(zugang, eintrag: dict, mit_rest: bool, sperre: Lock) -> dict:
    t0 = time.monotonic()
    try:
        begriffe = _stufe_1(zugang, eintrag,
                            eintrag["rest"] if mit_rest else None)
        fehler = None
    except Exception as e:                       # noqa: BLE001 — Probe
        begriffe, fehler = [], f"{type(e).__name__}: {e}"
    dauer = time.monotonic() - t0
    ohne = [b["suchbegriffe"] for b in begriffe if not b.get("zutat")]
    rest = eintrag["rest"] if mit_rest else None
    getroffen = [b["suchbegriffe"] for b in begriffe
                 if rest and _nennt(b["suchbegriffe"], rest)]
    with sperre:
        print(f"  {'mit ' if mit_rest else 'ohne'} Rest  "
              f"{eintrag['gericht'][:26]:<26} {len(begriffe):>2} Begriffe, "
              f"{len(ohne)} ohne Zutat {[k[0] for k in ohne]}"
              f"{'  REST DA' if getroffen else ''}  ({dauer:.0f} s)"
              + (f"  FEHLER {fehler}" if fehler else ""))
    return {"gericht": eintrag["gericht"], "mit_rest": mit_rest, "rest": rest,
            "begriffe": begriffe, "ohne_zutat": ohne, "rest_da": getroffen,
            "fehler": fehler}


def _nennt(kette: list[str], rest: str) -> bool:
    """Nennt diese Begriffskette den Rest? Derselbe Wortvergleich wie im Shop."""
    return any(herkunft.punkte(b, rest) >= herkunft.SCHWELLE for b in kette)


def messen(zugang, con, gerichte: list[str]) -> None:
    print("=" * 74)
    print("MESSUNG — Stufe 1 auf dem Chefkoch-Weg, je Gericht zweimal")
    print("=" * 74)
    daten = rezepte_holen(con, gerichte)
    print(f"  {len(daten)} Gerichte mit Zutatenliste\n")

    sperre = Lock()
    auftraege = [(e, m) for e in daten for m in (False, True)]
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        ergebnisse = list(pool.map(
            lambda a: _lauf(zugang, a[0], a[1], sperre), auftraege))
    print(f"\n  {len(ergebnisse)} Läufe in {time.monotonic() - t0:.0f} s")

    ohne = [e for e in ergebnisse if not e["mit_rest"]]
    mit = [e for e in ergebnisse if e["mit_rest"]]
    n_ohne = sum(1 for e in ohne if e["ohne_zutat"])
    begriffe = sum(len(e["begriffe"]) for e in ohne)
    einzeln = sum(len(e["ohne_zutat"]) for e in ohne)
    print()
    print("-" * 74)
    print(f"  Ohne Rest im Satz ({len(ohne)} Läufe) — die Grundrate:")
    print(f"    Läufe mit mindestens einem Begriff ohne Herkunftszutat: "
          f"{n_ohne} von {len(ohne)} ({_quote(n_ohne, len(ohne))})")
    print(f"    Begriffe ohne Herkunftszutat: {einzeln} von {begriffe} "
          f"({_quote(einzeln, begriffe)})")

    da = [e for e in mit if e["rest_da"]]
    still = [e for e in mit if not e["rest_da"] and e["ohne_zutat"]]
    print(f"\n  Mit Rest im Satz ({len(mit)} Läufe):")
    print(f"    Der Rest kam als Begriff zurück: {len(da)} von {len(mit)} "
          f"({_quote(len(da), len(mit))})")
    print(f"    Das Netz von WB-337 hätte ihn liegen lassen: {len(still)} "
          f"von {len(mit)} ({_quote(len(still), len(mit))})")
    for e in still:
        print(f"      {e['gericht'][:26]:<26} rest={e['rest']:<14} "
              f"Begriff ohne Zutat: {[k[0] for k in e['ohne_zutat']]}")
    print("\n  Seit WB-370 steht der Rest nicht mehr im Prompt: er wird im "
          "Code angehängt,\n  also in allen "
          f"{len(mit)} Läufen — und keinmal doppelt.")


def _quote(teil: int, ganz: int) -> str:
    return "—" if not ganz else f"{teil / ganz * 100:.0f} %"


def zug(zugang, con, gericht: str, rest: str) -> None:
    """EIN ganzer Chat-Zug, und der Korb dahinter. Ungeschönt."""
    satz = f"alles für {gericht}, und {rest}"
    print("=" * 74)
    print(f"EIN ZUG — {satz!r}")
    print("=" * 74)
    agent = chatmodul.Chat(zugang, wecker=wake.Wecker(), quelle=quelle.Quelle())
    t0 = time.monotonic()
    ergebnis = agent.turn(con, satz)
    print(f"  Weg: {ergebnis.weg}   Abruf: {ergebnis.abruf}   "
          f"({time.monotonic() - t0:.1f} s)")
    print(f"  Rest: {ergebnis.rest!r}   angehängt: "
          f"{ergebnis.rest_angehaengt}")
    print(f"  Herkunft: {ergebnis.quelle_name} — {ergebnis.quelle_url}\n")
    print(f"  Meldung: {ergebnis.meldung}\n")
    for b in ergebnis.begriffe:
        print(f"    {' -> '.join(b['suchbegriffe'])[:44]:<44} "
              f"Zutat: {b.get('zutat') or '—'}")
    print()
    for v in ergebnis.vorschlaege:
        art = "FREITEXT" if v["ist_freitext"] else f"#{v['product_id']}"
        zum = "Gericht" if v["dish_item"] else "daneben"
        print(f"    {v['qty']} × {v['name'][:40]:<40} {art:>9}  {zum}")
    print(f"\n  {ergebnis.n_produkte} Produkte, {ergebnis.n_freitext} "
          f"Freitext, {ergebnis.entwurf_zutaten} Zutaten im Entwurf.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True,
                   help="KOPIE der Datenbank — nicht data/picknick.db")
    p.add_argument("--messen", action="store_true")
    p.add_argument("--gericht", default="Quiche Lorraine")
    p.add_argument("--rest", default="Klopapier")
    p.add_argument("--gerichte", type=int, default=len(GERICHTE),
                   help="wie viele Gerichte die Messung nimmt")
    args = p.parse_args()

    if Path(args.db).resolve() == Path(db.DEFAULT_DB).resolve():
        print("Nicht auf data/picknick.db proben — erst kopieren "
              "(sqlite3 VACUUM INTO).")
        return 2

    con = db.connect(args.db)
    db.migrate(con)
    n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                    ).fetchone()["n"]
    zustand = wake.zustand()
    print(f"Katalog: {n} aktive Produkte in {args.db}")
    print(f"Box: {zustand.zustand} {zustand.modell or ''}\n")
    if not zustand.bedient:
        print("Die Box bedient nicht — erst `wake-vllm`.")
        return 1
    zugang = Modellzugang()
    try:
        if args.messen:
            messen(zugang, con, GERICHTE[:args.gerichte])
        else:
            zug(zugang, con, args.gericht, args.rest)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
