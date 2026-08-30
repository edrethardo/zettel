"""Handprobe der Auffächerung gegen die echte Box und den echten Katalog.

KEIN Test — die Testsuite läuft gegen einen Fake-LLM und geht nie ins Netz
(Spec 13). Hier wird gezeigt, was WB-368 im Betrieb tut:

    .venv/bin/python scripts/sorten_probe.py
    .venv/bin/python scripts/sorten_probe.py --wort Käse --sorte "Käse in Scheiben"

Gearbeitet wird auf einer KOPIE der Datenbank. Ein Chat-Zug schreibt
Nachrichten und Vorschläge an den Warenkorb; in der laufenden Datei wären das
Zeilen, die niemand bestellt hat.

Gezeigt wird jede Stufe mit ihrer Zeit, weil genau daran die Frage hängt, ob
der Umweg über die Sorte teuer ist: die Auffächerung aus dem Katalog kostet
keinen Modellaufruf, die Zuordnung eines Worts, das keine Kategorie ist, genau
einen — und die Wahl danach den einen, den auch jeder andere Zug hat.
"""
import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zettel import db  # noqa: E402
from zettel.assistant import chat as chatmodul  # noqa: E402
from zettel.assistant import oberbegriffe  # noqa: E402
from zettel.llm import wake  # noqa: E402


def zeile(text=""):
    print(text, flush=True)


def zug(agent, con, satz, **weitere):
    los = time.monotonic()
    ergebnis = agent.turn(con, satz, **weitere)
    return ergebnis, time.monotonic() - los


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=db.DEFAULT_DB)
    p.add_argument("--wort", default="Aufschnitt")
    p.add_argument("--sorte", default=None,
                   help="welche Sorte danach gewählt wird (Vorgabe: die erste)")
    p.add_argument("--auch", default="Tomatenmark",
                   help="ein Wort, das NICHT auffächern darf")
    args = p.parse_args()

    zustand = wake.zustand()
    zeile(f"Box: {zustand.zustand} {zustand.modell or ''} "
          f"{zustand.grund or ''}".rstrip())
    if not zustand.bedient:
        zeile("Die Box bedient nicht — `wake-vllm` und noch einmal.")
        return 1

    with tempfile.TemporaryDirectory(prefix="zettel-sorten-") as tmp:
        kopie = Path(tmp) / "probe.db"
        shutil.copyfile(args.db, kopie)
        con = db.connect(kopie)
        db.migrate(con)
        n = con.execute("SELECT count(*) AS n FROM product WHERE active = 1"
                        ).fetchone()["n"]
        zeile(f"Katalog: {n} aktive Produkte (Kopie von {args.db})")
        agent = chatmodul.Chat()

        zeile()
        zeile(f"== „{args.wort}“ ==")
        ergebnis, dauer = zug(agent, con, args.wort)
        zeile(f"weg={ergebnis.weg} kategorie={ergebnis.kategorie!r} "
              f"herkunft={ergebnis.sorten_herkunft!r} "
              f"verworfen={ergebnis.sorten_verworfen!r} [{dauer:.1f} s]")
        zeile(ergebnis.meldung)
        for s in ergebnis.sorten:
            zeile(f"   [ ] {s['name']:<32} {s['anzahl']:>4}")
        for v in ergebnis.vorschlaege:
            zeile(f"   - {v['qty']} × {v['name']}")

        if ergebnis.sorten:
            sorte = args.sorte or ergebnis.sorten[0]["name"]
            gewaehlt = oberbegriffe.gewaehlte(con, ergebnis.chat_message_id,
                                              [sorte])
            zeile()
            zeile(f"== Sorte gewählt: {gewaehlt} ==")
            if not gewaehlt:
                zeile(f"„{sorte}“ wurde gar nicht angeboten — verworfen.")
            else:
                zweit, dauer = zug(agent, con, ", ".join(gewaehlt),
                                   auffaechern=False,
                                   aus_sorten=(ergebnis.kategorie, gewaehlt))
                zeile(f"weg={zweit.weg} kategorie={zweit.kategorie!r} "
                      f"sorten={zweit.gewaehlte_sorten} [{dauer:.1f} s]")
                zeile(zweit.meldung)
                for v in zweit.vorschlaege:
                    zeile(f"   - {v['qty']} × {v['name']} "
                          f"({v['unit_text'] or '?'}) "
                          f"— {v['n_alternativen']} Alternativen")

        zeile()
        zeile(f"== „{args.auch}“ — darf nicht auffächern ==")
        dritt, dauer = zug(agent, con, args.auch)
        zeile(f"weg={dritt.weg} kategorie={dritt.kategorie!r} "
              f"verworfen={dritt.sorten_verworfen!r} [{dauer:.1f} s]")
        zeile(dritt.meldung)
        for v in dritt.vorschlaege:
            zeile(f"   - {v['qty']} × {v['name']}")
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
