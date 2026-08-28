"""Verifikation des Span-Vertrags gegen das ECHTE Phoenix (Spec 7.4). KEIN Test.

    .venv/bin/python scripts/trace_probe.py
    .venv/bin/python scripts/trace_probe.py --satz "Milch und Klopapier"
    .venv/bin/python scripts/trace_probe.py --projekt "Picknick Probe"

**Nicht über die Oberfläche.** Ein leerer Span sieht dort genauso gut aus wie
ein voller und misst nichts — die Zusicherungen unten prüfen deshalb
programmatisch, dass die interessierenden Attribute nicht null sind. Das ist
der Unterschied zwischen „es kommt etwas an" und „es steht etwas drin".

Die Testsuite (`tests/test_obs.py`) prüft denselben Vertrag gegen einen
In-Memory-Exporter und beantwortet damit die Frage, ob der Code die richtigen
Spans BAUT. Dieses Skript beantwortet die andere Hälfte: ob sie in Phoenix
ANKOMMEN und dort so aussehen, wie die Auswertung sie später braucht. Beides
zusammen, weil ein Vertrag, der nur auf einer Seite geprüft wird, an der
Übergabe bricht — Serialisierung, Attributnamen, Projektzuordnung.

Gebraucht wird: ein laufendes Phoenix auf localhost:6006, die wache vLLM-Box
und ein gefüllter Katalog (`data/picknick.db`). Das Skript weckt die Box
selbst und wartet auf sie; im Web-Prozess darf das nicht passieren (Spec 11),
hier schon.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picknick import db, obs  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.llm import wake  # noqa: E402

#: Der Satz aus WB-327, an dem der Unterschied zwischen Modell- und
#: Retrieval-Fehler zuerst aufgefallen ist: bei „Butter" waren alle fünf
#: vorgelegten Kandidaten Spezialbutter, normale Butter stand nie zur Wahl.
SATZ = "alles für Spaghetti Bolognese, und dazu Zahnpasta und Butter"

#: Was ohne Wert nicht nur unvollständig, sondern wertlos wäre. Genau die
#: Liste aus Spec 7.4.
PFLICHT_CHAIN = ["attributes.input.value", "attributes.output.value"]
PFLICHT_LLM = ["attributes.llm.token_count.prompt",
               "attributes.llm.token_count.completion",
               "attributes.llm.token_count.total",
               "attributes.llm.model_name",
               "attributes.input.value", "attributes.output.value"]
PFLICHT_RETRIEVER = ["attributes.input.value"]


def warte_bis_wach(frist_s: float = 300.0) -> bool:
    w = wake.Wecker()
    begonnen = time.monotonic()
    while time.monotonic() - begonnen < frist_s:
        z = w.zustand()
        if z.bedient:
            print(f"Box bedient: {z.modell}")
            return True
        rest = "" if z.rest_s is None else f", noch ~{z.rest_s:.0f} s"
        print(f"  {z.zustand}{rest} — {z.grund}")
        time.sleep(5)
    return False


# --------------------------------------------------------------------------
# Prüfen

class Pruefung:
    """Sammelt Zusicherungen und merkt sich, ob eine gerissen ist."""

    def __init__(self):
        self.gerissen = 0

    def __call__(self, bedingung: bool, text: str, zusatz: str = "") -> bool:
        marke = "OK  " if bedingung else "FEHL"
        if not bedingung:
            self.gerissen += 1
        print(f"  [{marke}] {text}" + (f"   {zusatz}" if zusatz else ""))
        return bool(bedingung)


def _wert(zeile, spalte):
    """Ein Attribut aus einer Span-Zeile, oder `None`.

    Pandas macht aus einem fehlenden Attribut `NaN` und nicht `None` — und
    `NaN` ist wahrheitswertig wahr genug, um eine schlampige Prüfung zu
    bestehen. Deshalb geht jede Prüfung durch diese Funktion.
    """
    import pandas as pd

    if spalte not in zeile.index:
        return None
    wert = zeile[spalte]
    if isinstance(wert, (list, dict)):
        return wert or None
    try:
        if pd.isna(wert):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(wert, str) and not wert.strip():
        return None
    return wert


def _fehlend(zeile, spalten):
    return [s for s in spalten if _wert(zeile, s) is None]


def _picknick(zeile, name):
    """Ein `picknick.*`-Attribut.

    Phoenix fasst gleichnamige Präfixe zu einer verschachtelten Spalte
    zusammen: die zehn `picknick.*`-Attribute kommen als EIN Wörterbuch in
    `attributes.picknick` zurück und nicht als zehn Spalten. Wer sie als
    Spalte sucht, findet nichts und hält den Span für leer — genau der
    Irrtum, den dieses Skript verhindern soll.
    """
    kasten = _wert(zeile, "attributes.picknick")
    if not isinstance(kasten, dict):
        return None
    wert = kasten.get(name)
    return None if wert == "" else wert


def _dok(d, feld):
    """Ein Feld eines Retriever-Dokuments.

    Auch hier die Schreibweise von Phoenix: die Felder heissen im Dataframe
    `document.id`, `document.content`, `document.score` — flach, nicht unter
    einem Schlüssel `document`.
    """
    return d.get(f"document.{feld}")


def pruefe(df, pruef: Pruefung, traces: set) -> None:
    """Die Zusicherungen aus Spec 7.4 gegen den geholten Dataframe."""
    print("\n== Zusicherungen ==")
    pruef(len(df) > 0, f"Spans in Phoenix angekommen: {len(df)}")
    if not len(df):
        return

    eigene = df[df["context.trace_id"].isin(traces)] if traces else df
    pruef(len(eigene) > 0,
          f"davon aus diesem Lauf: {len(eigene)} in {len(traces)} Trace(s)")

    namen = list(eigene["name"])
    for name, mindest in (("chat.turn", 1), ("plan.extract", 1),
                          ("catalog.search", 1), ("plan.choose", 1)):
        pruef(namen.count(name) >= mindest,
              f"Span {name}: {namen.count(name)}x")

    # Die Kinds — RETRIEVER statt TOOL ist der Kern des Tickets.
    kinds = dict(zip(eigene["name"], eigene["span_kind"]))
    pruef(kinds.get("chat.turn") == "CHAIN", "chat.turn ist CHAIN",
          str(kinds.get("chat.turn")))
    pruef(kinds.get("catalog.search") == "RETRIEVER",
          "catalog.search ist RETRIEVER", str(kinds.get("catalog.search")))
    pruef(kinds.get("plan.extract") == "LLM", "plan.extract ist LLM",
          str(kinds.get("plan.extract")))

    # Verschachtelung: alles unter einer Wurzel.
    wurzeln = eigene[eigene["parent_id"].isna()]
    pruef(set(wurzeln["name"]) == {"chat.turn"},
          "einzige Wurzel je Trace ist chat.turn", str(set(wurzeln["name"])))

    print("\n== Attribute, die nicht null sein dürfen ==")
    for _, zeile in eigene.sort_values("start_time").iterrows():
        pflicht = list(PFLICHT_CHAIN)
        if zeile["span_kind"] == "LLM":
            pflicht = list(PFLICHT_LLM)
        elif zeile["span_kind"] == "RETRIEVER":
            pflicht = list(PFLICHT_RETRIEVER)
            # Dokumente nur da, wo die Suche welche hatte. Ein Begriff ohne
            # Treffer ist eine Retrieval-Lücke und kein fehlendes Attribut —
            # das eine gehört ins Eval, das andere wäre ein Fehler im Code.
            if (_picknick(zeile, "candidates") or 0) > 0:
                pflicht.append("attributes.retrieval.documents")
        fehlt = _fehlend(zeile, pflicht)
        if zeile["name"] == "chat.turn" and _picknick(zeile, "path") is None:
            fehlt.append("picknick.path")
        pruef(not fehlt,
              f"{zeile['name']:<16} {len(pflicht) + 1:>1} Pflichtattribute"
              if zeile["name"] == "chat.turn"
              else f"{zeile['name']:<16} {len(pflicht)} Pflichtattribute",
              "" if not fehlt else f"fehlt: {fehlt}")

    print("\n== Die Retriever-Dokumente ==")
    for _, zeile in eigene[eigene["span_kind"] == "RETRIEVER"].iterrows():
        doks = list(_wert(zeile, "attributes.retrieval.documents") or [])
        begriff = _wert(zeile, "attributes.input.value")
        vollstaendig = all(
            _dok(d, "id") and _dok(d, "content")
            and _dok(d, "score") is not None for d in doks)
        scores = [_dok(d, "score") for d in doks]
        pruef(vollstaendig,
              f"„{begriff}“: {len(doks)} Dokumente mit id, content und score",
              f"bester Score {max(scores):.2f}" if scores
              else "keine Treffer — Retrieval-Lücke, kein Formfehler")

    print("\n== Tokenzahlen (Spec 7.3: getrennte Klassen) ==")
    for _, zeile in eigene[eigene["span_kind"] == "LLM"].iterrows():
        p = _wert(zeile, "attributes.llm.token_count.prompt")
        c = _wert(zeile, "attributes.llm.token_count.completion")
        t = _wert(zeile, "attributes.llm.token_count.total")
        pruef(p and c and t and int(p) + int(c) == int(t),
              f"{zeile['name']}: prompt {p} + completion {c} = total {t}")


# --------------------------------------------------------------------------
# Zeigen

def zeige_baum(df, traces: set) -> None:
    """Der Baum so, wie Phoenix ihn hat — nicht so, wie der Code ihn meinte."""
    print("\n== Der Baum in Phoenix ==")
    eigene = df[df["context.trace_id"].isin(traces)] if traces else df
    for trace_id in eigene["context.trace_id"].unique():
        teil = eigene[eigene["context.trace_id"] == trace_id]
        teil = teil.sort_values("start_time")
        print(f"  Trace {trace_id}")
        for _, z in teil.iterrows():
            tiefe = 0 if z["parent_id"] is None or _wert(z, "parent_id") is None else 1
            ein = str(_wert(z, "attributes.input.value") or "")[:52]
            print(f"  {'  ' * tiefe}{z['span_kind']:<10} {z['name']:<16} {ein}")


def zeige_schwachstelle(df, traces: set) -> None:
    """Die Frage des Tickets: Modell- oder Retrieval-Fehler?"""
    print("\n== Modell- oder Retrieval-Fehler? ==")
    eigene = df[df["context.trace_id"].isin(traces)] if traces else df
    for _, z in eigene[eigene["name"] == "chat.turn"].iterrows():
        schwach = _picknick(z, "weakest_term")
        rang = _picknick(z, "weakest_rank")
        verworfen = _picknick(z, "rejected") or 0
        print(f"  Weg: {_picknick(z, 'path')}, "
              f"Begriffe {_picknick(z, 'terms')}, "
              f"Produkte {_picknick(z, 'products')}, "
              f"Freitext {_picknick(z, 'free_text')}, "
              f"verworfene Modell-IDs {int(verworfen)}")
        print(f"  verworfene Modell-IDs = 0 heisst: das Modell hat nur "
              "vorgelegte Produkte genannt. Was schieflief, lief in der "
              "Suche schief.")
        if schwach is not None:
            print(f"  Schwächste Suche: „{schwach}“ mit Rang {rang:.2f}")

    print("\n  Ränge je Suchbegriff, schwächste zuerst (grösser ist besser):")
    zeilen = []
    for _, z in eigene[eigene["span_kind"] == "RETRIEVER"].iterrows():
        doks = list(_wert(z, "attributes.retrieval.documents") or [])
        oben = doks[0] if doks else {}
        zeilen.append((_dok(oben, "score") if doks else 0.0,
                       str(_wert(z, "attributes.input.value")), len(doks),
                       str(_dok(oben, "content") or "")))
    for score, begriff, n, inhalt in sorted(zeilen):
        print(f"    {begriff:<22} {n} Kandidaten, bester Rang "
              f"{score:6.2f}   {inhalt[:56]}")


# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--satz", default=SATZ)
    p.add_argument("--projekt", default=obs.PROJEKT,
                   help="Phoenix-Projekt (Vorgabe: %(default)s)")
    p.add_argument("--phoenix", default="http://localhost:6006")
    p.add_argument("--db", default=None)
    p.add_argument("--kandidaten", type=int, default=5)
    args = p.parse_args()

    print(f"Phoenix: {args.phoenix}, Projekt: {args.projekt!r}")
    provider = obs.einrichten(projekt=args.projekt,
                              endpunkt=f"{args.phoenix}/v1/traces")
    if provider is None:
        print("Tracing liess sich nicht einrichten — abgebrochen.")
        return 2

    if not warte_bis_wach():
        print("Die Box bedient nicht. Ohne Modell gibt es keinen LLM-Span.")
        return 2

    con = db.connect(args.db or db.DEFAULT_DB)
    db.migrate(con)
    n = con.execute("SELECT count(*) AS n FROM product"
                    " WHERE active = 1").fetchone()["n"]
    print(f"Katalog: {n} aktive Produkte\n")

    begonnen = datetime.now(timezone.utc) - timedelta(seconds=5)
    agent = chatmodul.Chat(kandidaten=args.kandidaten)
    t0 = time.monotonic()
    ergebnis = agent.turn(con, args.satz)
    dauer = time.monotonic() - t0
    print(f"Zug fertig in {dauer:.1f} s — Weg {ergebnis.weg}, "
          f"{ergebnis.n_produkte} Produkte, {ergebnis.n_freitext} Freitext")
    print(f"  {ergebnis.meldung}")
    for v in ergebnis.vorschlaege:
        rang = "" if v["rang"] is None else f"  (Rang {v['rang']:.2f})"
        print(f"    {v['search_term']:<22} -> {v['name']}{rang}")

    span_id = con.execute("SELECT span_id FROM chat_message WHERE id = ?",
                          (ergebnis.chat_message_id,)).fetchone()["span_id"]
    print(f"\nchat_message.span_id = {span_id!r} "
          "(daran hängen die Annotationen aus Spec 8.1)")
    con.close()

    # Ohne das endet das Skript, bevor der Hintergrund-Thread den letzten Span
    # exportiert hat — und die Abfrage fände nichts.
    obs.flush()

    from phoenix.client import Client
    client = Client(base_url=args.phoenix)
    # Phoenix schreibt asynchron; ein bis zwei Sekunden Nachlauf sind normal.
    df = None
    for versuch in range(10):
        df = client.spans.get_spans_dataframe(project_identifier=args.projekt,
                                              start_time=begonnen)
        if len(df) and (df["name"] == "chat.turn").any():
            break
        time.sleep(1.0)

    if df is None or not len(df):
        print("\nKeine Spans in Phoenix gefunden.")
        return 1

    pruef = Pruefung()
    if span_id and (df["context.span_id"] == span_id).any():
        traces = set(df[df["context.span_id"] == span_id]["context.trace_id"])
        pruef(True, f"chat_message.span_id {span_id} in Phoenix wiedergefunden")
    else:
        traces = set(df["context.trace_id"])
        pruef(False, f"chat_message.span_id {span_id} in Phoenix wiedergefunden")

    zeige_baum(df, traces)
    zeige_schwachstelle(df, traces)
    pruefe(df, pruef, traces)

    schluss = ("ALLES GRÜN" if not pruef.gerissen
               else f"{pruef.gerissen} ZUSICHERUNG(EN) GERISSEN")
    print(f"\n{schluss}")
    return 1 if pruef.gerissen else 0


if __name__ == "__main__":
    raise SystemExit(main())
