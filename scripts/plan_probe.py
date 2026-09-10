"""Der Wochenplaner gegen die echte Box, mit Traces nach Phoenix. KEIN Test.

    .venv/bin/python scripts/plan_probe.py --db kopie.db
    ZETTEL_PHOENIX_PROJECT="Zettel Eval Wochenplan" \\
    .venv/bin/python scripts/plan_probe.py --db kopie.db --trace \\
        --json evals/plan_probe-2026-09-06-qwen.json

Derselbe Gedanke wie `breite_probe.py`, eine Ebene höher: nicht ein Satz ->
eine Liste, sondern eine Woche -> eine Liste. Je Szenario eine eigene
Arbeitskopie der Datenbank, ein Plan, `Planer.planen()` gegen die Box, dann
„Ja" auf jeden belegten Tag, die Liste in den Korb, Labels. Gemessen wird,
was der Span trägt: vorgelegt, belegt, verworfen, Rest, Zeilen, gedeckt,
Preis — und die Dauer.

**Was diese Probe misst und was nicht.** Sie misst, ob das Modell aus der
Vorlage wählt (`rejected`), ob es alle offenen Tage belegt (`assigned` gegen
`days`), und ob die Liste danach rechnet (`lines`, `covered`, `rest`). Sie
misst NICHT, ob der Plan „gut" ist — das sagt nur ein Mensch, und zwar über
die Labels, die diese Probe mit „Ja auf alles" ausdrücklich nicht liefert.

`--db` ist die KOPIE (`sqlite3 data/picknick.db "VACUUM INTO 'kopie.db'"`);
die echte Datei fasst die Probe nie an. Mit `--trace` gehen die Spans in
das Projekt aus `ZETTEL_PHOENIX_PROJECT` — für einen Messlauf ein EIGENES,
damit das Alltagsprojekt sauber bleibt (WB-393). Danach prüft die Probe
gegen das laufende Phoenix, dass die Spans angekommen sind und die
Pflichtfelder tragen (Spec 7.4): ein leerer Span sieht in der Oberfläche
genauso gut aus wie ein voller und misst nichts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zettel import db, obs, wochenplan  # noqa: E402
from zettel.assistant import chat as chatmodul  # noqa: E402
from zettel.gerichte import quelle  # noqa: E402
from zettel.llm import wake  # noqa: E402
from zettel.llm.client import Modellzugang  # noqa: E402
from zettel.obs import labels  # noqa: E402

#: Die Szenarien. Jedes steht für eine Frage:
#:   A  der Normalfall — Zeitgrenze und Bestand, drei Tage
#:   B  Budget, vier Personen, fünf Tage: skaliert die Liste, rechnet der Preis?
#:   C  ein Tag auswärts und ein Bestand ohne Menge — hält das Modell sich an
#:      die Festlegung, bleibt „Nudeln" ein Hinweis statt einer Rechnung?
#:   D  Neuplanung: Tag 1 „Nein" — wird nur der eine Tag neu belegt, ohne das
#:      abgelehnte Gericht, mit den anderen als Festlegung?
#:   E  sieben Tage bei knapper Vorlage — lässt das Modell Tage leer oder
#:      erfindet es? (`rejected` und `assigned` gegen `days`)
SZENARIEN = [
    {"name": "A", "tage": "3", "personen": "2", "max_minuten": "40",
     "bestand": "500 g Kartoffeln, 6 Eier"},
    {"name": "B", "tage": "5", "personen": "4", "budget": "60"},
    {"name": "C", "tage": "4", "personen": "2",
     "bestand": "Nudeln, 200 g Parmesan", "auswaerts": 1},
    {"name": "D", "tage": "3", "personen": "2", "max_minuten": "40",
     "bestand": "500 g Kartoffeln, 6 Eier", "neuplanung": 0},
    {"name": "E", "tage": "7", "personen": "2", "max_minuten": "30"},
]

PFLICHT_CHAIN = ["attributes.input.value", "attributes.output.value"]
#: Die `zettel.plan.*`-Attribute kommen aus Phoenix als EIN verschachteltes
#: Wörterbuch in `attributes.zettel` zurück (`{"plan": {"presented": …}}`),
#: nicht als Spalten — dieselbe Falle wie in `trace_probe.py`.
PFLICHT_PLAN = ["presented", "rejected", "assigned", "days", "lines"]
PFLICHT_LLM = ["attributes.llm.model_name", "attributes.llm.token_count.prompt",
               "attributes.llm.token_count.completion"]


def warte_bis_wach(frist_s: float = 600.0) -> bool:
    w = wake.Wecker()
    begonnen = time.monotonic()
    while time.monotonic() - begonnen < frist_s:
        z = w.zustand()
        if z.bedient:
            print(f"Box bedient: {z.modell}")
            return True
        rest = "" if z.rest_s is None else f", noch ~{z.rest_s:.0f} s"
        print(f"  Box: {z.zustand}{rest} — {z.grund or ''}")
        time.sleep(10)
    return False


def _kopie(basis: str, ziel: Path) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if ziel.exists():
        ziel.unlink()
    q = sqlite3.connect(basis)
    try:
        q.execute("VACUUM INTO ?", (str(ziel),))
    finally:
        q.close()


def _lauf(sz: dict, basis: str, ordner: Path, zugang) -> dict:
    ziel = ordner / f"plan_{sz['name']}.db"
    _kopie(basis, ziel)
    con = db.connect(ziel)
    db.migrate(con)
    d: dict = {"szenario": sz["name"], "rahmen": {k: v for k, v in sz.items()
                                                  if k not in ("name",)},
               "fehler": None}
    try:
        agent = chatmodul.Chat(zugang, wecker=wake.Wecker(),
                               quelle=quelle.Quelle())
        planer = wochenplan.Planer(agent)
        rahmen = wochenplan.aus_formular(sz)
        pid = wochenplan.anlegen(con, rahmen, von=date.today() + timedelta(days=1))
        tage = wochenplan.laden(con, pid)["tage_liste"]
        if sz.get("auswaerts") is not None:
            wochenplan.auswaerts_setzen(con, tage[sz["auswaerts"]]["id"])

        t0 = time.monotonic()
        bericht = planer.planen(con, pid)
        d["dauer_s"] = round(time.monotonic() - t0, 1)
        d.update({k: bericht[k] for k in ("offen", "vorgelegt", "belegt",
                                          "verworfen", "verworfen_gruende",
                                          "vorgewaermt",
                                          "bestand_vorgeschlagen", "meldung")})
        d["fehler_modell"] = bericht.get("fehler")

        if sz.get("neuplanung") is not None:
            # Tag n „Nein", die anderen „Ja", dann derselbe Zug noch einmal.
            tage = wochenplan.laden(con, pid)["tage_liste"]
            for i, t in enumerate(tage):
                if not t["recipe_id"]:
                    continue
                wochenplan.tag_entscheiden(
                    con, t["id"],
                    "removed" if i == sz["neuplanung"] else "kept")
            abgelehnt = tage[sz["neuplanung"]]["recipe_id"]
            t1 = time.monotonic()
            zweiter = planer.planen(con, pid)
            d["neuplanung"] = {
                "dauer_s": round(time.monotonic() - t1, 1),
                "offen": zweiter["offen"], "belegt": zweiter["belegt"],
                "verworfen": zweiter["verworfen"],
                "verworfen_gruende": zweiter.get("verworfen_gruende", []),
                "meldung": zweiter.get("meldung"),
                "abgelehnt_wieder_gewaehlt": any(
                    w["recipe_id"] == abgelehnt for w in zweiter["gewaehlt"]),
            }

        plan = wochenplan.laden(con, pid)
        d["tage"] = [{"datum": t["datum"],
                      "rezept": (t["rezept"] or {}).get("name"),
                      "minuten": (t["rezept"] or {}).get("minuten"),
                      "grund": t["grund"], "auswaerts": bool(t["auswaerts"])}
                     for t in plan["tage_liste"]]
        d["bestand"] = [{"name": b["name"], "menge": b["menge"],
                         "einheit": b["einheit"], "herkunft": b["herkunft"],
                         "decision": b["decision"]} for b in plan["bestand"]]
        liste = wochenplan.einkaufsliste(con, plan)
        d["liste"] = {k: liste[k] for k in ("zu_kaufen", "gedeckt",
                                            "ohne_produkt", "n_rest",
                                            "preis_cents", "ohne_preis",
                                            "budget_ueber")}
        d["zusammenfassung"] = plan["zusammenfassung"]

        # „Ja" auf jeden belegten Tag, dann in den Korb — das ist der Moment
        # der Labels. Die günstigste Annahme, wie in `breite_probe`.
        for t in plan["tage_liste"]:
            if t["recipe_id"] and t["decision"] == "offen":
                wochenplan.tag_entscheiden(con, t["id"], "kept")
        try:
            korb = wochenplan.in_den_korb(con, pid)
            d["korb"] = {"eingelegt": korb["n_eingelegt"],
                         "gedeckt": korb["gedeckt"],
                         "ohne_produkt": korb["ohne_produkt"]}
        except wochenplan.WochenplanFehler as e:
            d["korb"] = {"fehler": str(e)}
        d["labels"] = len(labels.plan_annotationen(con, pid))
        d["span_id"] = plan["span_id"]
    except chatmodul.ChatNichtVerfuegbar as e:
        d["fehler"] = f"Box: {e}"
    except Exception as e:  # noqa: BLE001 — ein Szenario ist ein Messwert
        d["fehler"] = f"{e.__class__.__name__}: {e}"
    finally:
        con.close()
        for p in (ziel, ziel.with_name(ziel.name + "-wal"),
                  ziel.with_name(ziel.name + "-shm")):
            p.unlink(missing_ok=True)
    return d


def bericht(ergebnisse: list[dict]) -> None:
    print()
    print(f"{'Sz':<3} {'Tage':>4} {'vorgel.':>7} {'belegt':>6} {'verw.':>5} "
          f"{'Rest':>4} {'Zeilen':>6} {'gedeckt':>7} {'Freitext':>8} "
          f"{'Preis':>8} {'Dauer':>6}")
    for d in ergebnisse:
        if d["fehler"]:
            print(f"{d['szenario']:<3} FEHLER {d['fehler']}")
            continue
        li = d["liste"]
        print(f"{d['szenario']:<3} {d['offen']:>4} {d['vorgelegt']:>7} "
              f"{d['belegt']:>6} {d['verworfen']:>5} {li['n_rest']:>4} "
              f"{li['zu_kaufen']:>6} {li['gedeckt']:>7} {li['ohne_produkt']:>8} "
              f"{li['preis_cents'] / 100:>7.2f}€ {d['dauer_s']:>5.1f}s")
        for t in d["tage"]:
            was = "auswärts" if t["auswaerts"] else (t["rezept"] or "—")
            grund = f"  · {t['grund']}" if t.get("grund") else ""
            print(f"      {t['datum']}  {was}{grund}")
        if d.get("neuplanung"):
            n = d["neuplanung"]
            print(f"      Neuplanung: {n['belegt']}/{n['offen']} belegt, "
                  f"{n['verworfen']} verworfen, abgelehntes Gericht wieder "
                  f"gewählt: {n['abgelehnt_wieder_gewaehlt']}, {n['dauer_s']} s")
        if d["bestand"]:
            print("      Bestand: " + ", ".join(
                f"{b['name']} ({b['herkunft']}/{b['decision']})"
                for b in d["bestand"]))


def pruefe_phoenix(projekt: str, seit: datetime, url: str) -> bool:
    """Sind die Spans angekommen und tragen sie die Pflichtfelder?"""
    try:
        from phoenix.client import Client
        c = Client(base_url=url)
        df = c.spans.get_spans_dataframe(project_identifier=projekt,
                                         start_time=seit)
    except Exception as e:  # noqa: BLE001
        print(f"Phoenix nicht geprüft: {e.__class__.__name__}: {e}")
        return False
    if df is None or len(df) == 0:
        print("Phoenix: KEINE Spans im Zeitfenster gefunden.")
        return False
    ok = True
    ketten = df[df["name"] == "plan.woche"]
    llm = ketten[ketten["span_kind"] == "LLM"]
    chains = ketten[ketten["span_kind"] == "CHAIN"]

    def _plan_attr(zeile) -> dict:
        kasten = zeile.get("attributes.zettel")
        if not isinstance(kasten, dict):
            return {}
        return kasten.get("plan") if isinstance(kasten.get("plan"), dict) else {}
    print(f"Phoenix: {len(df)} Spans, {len(chains)} plan.woche-Chains, "
          f"{len(llm)} plan.woche-LLM-Spans, "
          f"{int((df['name'] == 'recipe.zuordnung').sum())} recipe.zuordnung, "
          f"{int((df['name'] == 'catalog.search').sum())} catalog.search")
    for spalte in PFLICHT_CHAIN:
        fehlt = (chains[spalte].isna().sum() if spalte in chains
                 else len(chains))
        if fehlt:
            print(f"  ! {fehlt} plan.woche-Chains ohne {spalte}")
            ok = False
    for feld in PFLICHT_PLAN:
        fehlt = sum(1 for _, z in chains.iterrows()
                    if _plan_attr(z).get(feld) is None)
        if fehlt:
            print(f"  ! {fehlt} plan.woche-Chains ohne zettel.plan.{feld}")
            ok = False
    for spalte in PFLICHT_LLM:
        fehlt = llm[spalte].isna().sum() if spalte in llm else len(llm)
        if fehlt:
            print(f"  ! {fehlt} LLM-Spans ohne {spalte}")
            ok = False
    if len(chains):
        print("  Attribute der Chains:")
        for _, z in chains.iterrows():
            a = _plan_attr(z)
            print(f"    plan {(z.get('attributes.zettel') or {}).get('plan_id')}: "
                  f"days={a.get('days')} presented={a.get('presented')} "
                  f"assigned={a.get('assigned')} rejected={a.get('rejected')} "
                  f"rest={a.get('rest')} lines={a.get('lines')} "
                  f"covered={a.get('covered')} prewarmed={a.get('prewarmed')}")
    if ok:
        print("  alle Pflichtfelder da")
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="KOPIE der Datenbank")
    p.add_argument("--json", default=None)
    p.add_argument("--trace", action="store_true")
    p.add_argument("--phoenix", default="http://localhost:6006")
    p.add_argument("--nur", action="append", default=[])
    args = p.parse_args()

    szenarien = [s for s in SZENARIEN if not args.nur or s["name"] in args.nur]
    if not warte_bis_wach():
        print("Die Box bedient nicht.")
        return 1
    projekt = None
    if args.trace:
        if obs.einrichten() is not None:
            stand = obs.tracerstand()
            projekt = stand["projekt"]
            print(f"Trace: Projekt {projekt!r} auf {stand['endpunkt']}")
        else:
            print("Trace: nicht eingerichtet (ZETTEL_TRACING=0?).")
    seit = datetime.now(timezone.utc)
    zugang = Modellzugang()
    modell = zugang.modell()
    ordner = Path(args.db).resolve().parent / "plan_probe_kopien"
    ergebnisse = []
    t0 = time.monotonic()
    for sz in szenarien:
        print(f"\n== Szenario {sz['name']} ==")
        d = _lauf(sz, args.db, ordner, zugang)
        d["modell"] = modell
        ergebnisse.append(d)
        print(f"   {d.get('meldung')} — {d.get('belegt')} belegt, "
              f"{d.get('verworfen')} verworfen, {d.get('dauer_s')} s"
              if not d["fehler"] else f"   FEHLER {d['fehler']}")
    dauer = time.monotonic() - t0
    shutil.rmtree(ordner, ignore_errors=True)
    bericht(ergebnisse)
    print(f"\n  {len(ergebnisse)} Szenarien in {dauer:.0f} s, Modell {modell}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(ergebnisse, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        print(f"  Rohdaten: {args.json}")
    if args.trace:
        obs.flush()
        labels.abwarten()
        time.sleep(2.0)
        if projekt:
            pruefe_phoenix(projekt, seit, args.phoenix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
