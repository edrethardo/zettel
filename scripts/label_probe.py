"""Verifikation der Eval-Labels gegen das ECHTE Phoenix (Spec 8.1). KEIN Test.

    .venv/bin/python scripts/label_probe.py
    .venv/bin/python scripts/label_probe.py --satz "Milch und Klopapier"

Der ganze Weg einmal in echt: ein Chat-Zug durch `Chat.turn()` gegen die
vLLM-Box und den echten Katalog, ein paar Vorschläge behalten, einer
verworfen, einer bewusst **offen gelassen** — dann abschicken und die
Annotationen **programmatisch zurücklesen** (`spans.get_span_annotations`).
Die Oberfläche taugt als Beweis nicht: dort sieht eine Annotation, die es gar
nicht gibt, genauso aus wie eine, die man übersehen hat.

Drei Dinge prüft nur dieses Skript und keine Testsuite:

1. **Kommt es an?** Der Client baut die Anfrage, Phoenix nimmt sie an, und
   die Annotation hängt an genau dem `chat.turn`-Span des Zugs — geprüft über
   `chat_message.span_id`. Das ist die Übergabe, an der ein Vertrag bricht,
   der nur auf einer Seite geprüft wurde.
2. **Bleibt `HUMAN` stehen?** Was zurückkommt, muss `annotator_kind = HUMAN`
   tragen. Daran hängt der Wert dieser Daten: menschliches Urteil, kein
   LLM-Judge.
3. **Gibt es beim zweiten Mal Dubletten?** Der zweite Schreibvorgang mit
   denselben `identifier` darf die Zahl der Annotationen NICHT erhöhen. Die
   Testsuite kann nur zeigen, dass die Schlüssel gleich bleiben; ob Phoenix
   darauf aktualisiert, entscheidet Phoenix.

**Der echte Warenkorb wird nicht angefasst.** Die Datenbank wird vorher
kopiert (`--db` überschreibt das): das Skript schickt eine Bestellung ab, und
das wäre sonst die eine, in der die beiden gerade ihren Einkauf sammeln.
"""
import argparse
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picknick import db, obs, orders  # noqa: E402
from picknick.assistant import chat as chatmodul  # noqa: E402
from picknick.assistant import vorschlaege  # noqa: E402
from picknick.llm import wake  # noqa: E402
from picknick.obs import labels  # noqa: E402

SATZ = "alles für Spaghetti Bolognese, und dazu Zahnpasta und Butter"


class Pruefung:
    """Sammelt Zusicherungen und merkt sich, ob eine gerissen ist."""

    def __init__(self):
        self.gerissen = 0

    def __call__(self, bedingung: bool, text: str, zusatz: str = "") -> bool:
        if not bedingung:
            self.gerissen += 1
        print(f"  [{'OK  ' if bedingung else 'FEHL'}] {text}"
              + (f"   {zusatz}" if zusatz else ""))
        return bool(bedingung)


def warte_bis_wach(frist_s: float = 300.0) -> bool:
    w = wake.Wecker()
    begonnen = time.monotonic()
    while time.monotonic() - begonnen < frist_s:
        z = w.zustand()
        if z.bedient:
            print(f"Box bedient: {z.modell}")
            return True
        print(f"  {z.zustand} — {z.grund}")
        time.sleep(5)
    return False


def entscheiden(con, ergebnis) -> dict:
    """Behalten, verwerfen, offen lassen — wie eine Nutzerin es täte.

    Der letzte Vorschlag bleibt bewusst unangetastet. Er ist die Probe auf das
    Herzstück von Spec 8.1: ein offener Vorschlag darf weder in der Quote
    auftauchen noch eine eigene Annotation bekommen.
    """
    zeilen = vorschlaege.liste(con, ergebnis.chat_message_id)
    if len(zeilen) < 3:
        print("Zu wenige Vorschläge für eine sinnvolle Probe.")
        return {}
    behalten = zeilen[:-2]
    verworfen = [zeilen[-2]]
    offen = [zeilen[-1]]
    for v in behalten:
        vorschlaege.entscheiden(con, v["id"], vorschlaege.BEHALTEN)
    for v in verworfen:
        vorschlaege.entscheiden(con, v["id"], vorschlaege.VERWORFEN)

    print("\n== Die Entscheidungen der „Nutzerin\" ==")
    for v in behalten:
        print(f"    kept     {v['search_term']:<22} {v['name'][:44]}")
    for v in verworfen:
        print(f"    removed  {v['search_term']:<22} {v['name'][:44]}")
    for v in offen:
        print(f"    offen    {v['search_term']:<22} {v['name'][:44]}"
              "   (zählt nicht mit)")
    return {"behalten": behalten, "verworfen": verworfen, "offen": offen,
            "quote": vorschlaege.quote(con, ergebnis.chat_message_id)}


def hole(client, projekt: str, span_id: str, versuche: int = 10) -> list:
    """Die Annotationen dieses Spans, zurückgelesen. Phoenix schreibt asynchron."""
    for _ in range(versuche):
        annos = client.spans.get_span_annotations(
            span_ids=[span_id], project_identifier=projekt)
        if annos:
            return list(annos)
        time.sleep(1.0)
    return []


def _feld(anno, *pfad):
    """Ein Feld aus einer zurückgelesenen Annotation (dict oder Objekt)."""
    wert = anno
    for name in pfad:
        if wert is None:
            return None
        wert = wert.get(name) if isinstance(wert, dict) else getattr(
            wert, name, None)
    return wert


def zeige(annos) -> None:
    print(f"\n== {len(annos)} Annotationen, aus Phoenix zurückgelesen ==")
    for a in annos:
        print(f"    {str(_feld(a, 'name')):<18} "
              f"{str(_feld(a, 'annotator_kind')):<6} "
              f"label={str(_feld(a, 'result', 'label')):<8} "
              f"score={_feld(a, 'result', 'score')} "
              f"id={_feld(a, 'identifier')}")
        erkl = _feld(a, "result", "explanation")
        if erkl:
            print(f"        „{erkl}“")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--satz", default=SATZ)
    p.add_argument("--projekt", default=obs.PROJEKT)
    p.add_argument("--phoenix", default="http://localhost:6006")
    p.add_argument("--db", default=None,
                   help="Vorgabe: eine Kopie von data/picknick.db")
    p.add_argument("--kandidaten", type=int, default=5)
    args = p.parse_args()

    print(f"Phoenix: {args.phoenix}, Projekt: {args.projekt!r}")
    if obs.einrichten(projekt=args.projekt,
                      endpunkt=f"{args.phoenix}/v1/traces") is None:
        print("Tracing liess sich nicht einrichten — abgebrochen.")
        return 2
    if not warte_bis_wach():
        print("Die Box bedient nicht. Ohne Zug gibt es nichts zu annotieren.")
        return 2

    pfad = args.db
    if pfad is None:
        pfad = str(Path(tempfile.mkdtemp(prefix="picknick-label-"))
                   / "picknick.db")
        shutil.copy(db.DEFAULT_DB, pfad)
        print(f"Kopie der Datenbank: {pfad} (der echte Warenkorb bleibt heil)")
    con = db.connect(pfad)
    db.migrate(con)
    aktiv = con.execute("SELECT count(*) AS n FROM product"
                        " WHERE active = 1").fetchone()["n"]
    print(f"Katalog: {aktiv} aktive Produkte\n")

    begonnen = datetime.now(timezone.utc) - timedelta(seconds=5)
    ergebnis = chatmodul.Chat(kandidaten=args.kandidaten).turn(con, args.satz)
    print(f"Zug fertig — Weg {ergebnis.weg}, {len(ergebnis.vorschlaege)} "
          "Vorschläge")

    gemacht = entscheiden(con, ergebnis)
    if not gemacht:
        return 1
    span_id = con.execute("SELECT span_id FROM chat_message WHERE id = ?",
                          (ergebnis.chat_message_id,)).fetchone()["span_id"]
    korb = orders.warenkorb_id(con)

    pruef = Pruefung()
    print("\n== Vor dem Abschicken ==")
    obs.flush()
    from phoenix.client import Client
    client = Client(base_url=args.phoenix)
    # Erst muss der Span da sein, sonst lehnt Phoenix die Annotation ab.
    for _ in range(15):
        df = client.spans.get_spans_dataframe(project_identifier=args.projekt,
                                              start_time=begonnen)
        if len(df) and (df["context.span_id"] == span_id).any():
            break
        time.sleep(1.0)
    pruef(bool(span_id) and len(df) and (df["context.span_id"] == span_id).any(),
          f"chat.turn-Span {span_id} liegt in Phoenix")
    pruef(hole(client, args.projekt, span_id, versuche=2) == [],
          "vor dem Abschicken steht keine Annotation daran")

    print("\n== Abschicken ==")
    t0 = time.monotonic()
    bestellung = orders.abschicken(con)
    dauer = time.monotonic() - t0
    pruef(bestellung["state"] == "offen",
          f"Bestellung {bestellung['id']} abgeschickt in {dauer * 1000:.1f} ms",
          "der HTTP-Aufruf läuft im Hintergrund")
    pruef(labels.abwarten(30.0), "Annotationen übergeben")

    annos = hole(client, args.projekt, span_id)
    zeige(annos)

    print("\n== Zusicherungen ==")
    quote = [a for a in annos if _feld(a, "name") == labels.NAME_QUOTE]
    einzel = [a for a in annos if _feld(a, "name") == labels.NAME_ENTSCHEIDUNG]
    erwartet = gemacht["quote"]["quote"]
    pruef(len(quote) == 1, f"genau eine {labels.NAME_QUOTE}-Annotation",
          f"{len(quote)}")
    if quote:
        pruef(abs(float(_feld(quote[0], "result", "score")) - erwartet) < 1e-9,
              f"mapping_precision = {erwartet:.2f} "
              f"({gemacht['quote']['behalten']} behalten / "
              f"{gemacht['quote']['behalten'] + gemacht['quote']['verworfen']}"
              " entschieden)",
              f"in Phoenix: {_feld(quote[0], 'result', 'score')}")
    n_entschieden = len(gemacht["behalten"]) + len(gemacht["verworfen"])
    pruef(len(einzel) == n_entschieden,
          f"eine Annotation je entschiedenem Vorschlag: {n_entschieden}",
          f"in Phoenix: {len(einzel)}")
    pruef(all(_feld(a, "annotator_kind") == "HUMAN" for a in annos),
          "annotator_kind ist überall HUMAN — kein LLM-Judge",
          str({_feld(a, "annotator_kind") for a in annos}))

    begriffe = {_feld(a, "result", "explanation") for a in einzel}
    erwartete_begriffe = {v["search_term"] for v in
                          gemacht["behalten"] + gemacht["verworfen"]}
    pruef(erwartete_begriffe <= begriffe,
          "der Suchbegriff steht als Erklärung an jeder Einzelannotation",
          f"{sorted(begriffe)}")

    offen_id = f"picknick-suggestion-{gemacht['offen'][0]['id']}"
    pruef(offen_id not in {_feld(a, "identifier") for a in annos},
          "der offen gelassene Vorschlag hat KEINE Annotation", offen_id)

    labels_zurueck = {_feld(a, "result", "label") for a in einzel}
    pruef(labels_zurueck <= {"kept", "removed"},
          "die Labels heissen kept/removed wie in der Datenbank",
          str(sorted(labels_zurueck)))

    print("\n== Zweimal schreiben (Dubletten?) ==")
    labels.schreiben(con, korb, client=client)
    labels.abwarten(30.0)
    time.sleep(2.0)
    nochmal = hole(client, args.projekt, span_id)
    pruef(len(nochmal) == len(annos),
          f"nach dem zweiten Schreiben immer noch {len(annos)} Annotationen",
          f"jetzt: {len(nochmal)}")
    pruef({_feld(a, "identifier") for a in nochmal}
          == {_feld(a, "identifier") for a in annos},
          "dieselben identifier — Phoenix hat aktualisiert, nicht angehängt")

    con.close()
    schluss = ("ALLES GRÜN" if not pruef.gerissen
               else f"{pruef.gerissen} ZUSICHERUNG(EN) GERISSEN")
    print(f"\n{schluss}")
    return 1 if pruef.gerissen else 0


if __name__ == "__main__":
    raise SystemExit(main())
