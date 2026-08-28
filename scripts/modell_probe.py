"""Handprobe gegen die echte vLLM-Box. Ausdrücklich KEIN Test.

Die Testsuite darf weder ins Netz gehen noch die Box wecken (Spec 13) — sie
prüft deshalb gegen Doppelgänger. Was ein Doppelgänger nicht beantworten kann,
ist die Frage, ob die echte Box heute noch so antwortet wie gedacht: welches
Kürzel sie führt und ob `reasoning_content` wirklich getrennt von `content`
ankommt. Dafür ist dieses Skript da, von Hand gestartet:

    .venv/bin/python scripts/modell_probe.py

Schläft die Box, weckt es sie und wartet — hier darf gewartet werden, im
Web-Prozess nicht.
"""
import sys
import time
from pathlib import Path

# Das Projekt hat keine Installationsdatei; `pytest` findet `picknick`, weil es
# aus dem Projektverzeichnis heraus läuft. Ein Skript in `scripts/` nicht.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picknick.llm import wake  # noqa: E402
from picknick.llm.client import (Modellzugang,  # noqa: E402
                                 ModellNichtErreichbar)


def warte_bis_wach(frist_s: float = 300.0) -> bool:
    w = wake.Wecker()
    begonnen = time.monotonic()
    while time.monotonic() - begonnen < frist_s:
        z = w.zustand()
        if z.bedient:
            print(f"bedient: {z.modell}")
            return True
        rest = "" if z.rest_s is None else f", noch ~{z.rest_s:.0f} s"
        print(f"{z.zustand}{rest} — {z.grund}")
        if z.zustand == wake.NICHT_ERREICHBAR:
            return False
        time.sleep(10)
    print("Frist abgelaufen.")
    return False


def main() -> int:
    if not warte_bis_wach():
        return 1
    zugang = Modellzugang()
    print("Kürzel laut /v1/models:", zugang.modell())
    try:
        antwort = zugang.chat(
            [{"role": "user",
              "content": "Nenne genau ein Lebensmittel. Nur das Wort."}],
            temperature=0.0, max_tokens=64)
    except ModellNichtErreichbar as e:
        print("Fehlgeschlagen:", e)
        return 1
    print("modell          :", antwort.modell)
    print("content         :", repr(antwort.content))
    print("reasoning_content:", repr(antwort.reasoning_content))
    print("tokens          :", antwort.prompt_tokens, "/", antwort.completion_tokens)
    return 0


if __name__ == "__main__":
    sys.exit(main())
