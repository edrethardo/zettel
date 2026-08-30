"""Die Farbwelt ist dunkel, und sie ist gemeint (WB-360, WB-418).

Bis WB-418 gab es zwei Modi: `:root` war hell, und
`@media (prefers-color-scheme: dark)` setzte dieselben Variablen neu. Seit
WB-418 gibt es einen — den dunklen, ohne Media-Query. Der Nutzer: „Mach die
normale Webseite ebenfalls dunkel. Darkmode Leute." Die beiden benutzen den
Shop abends und im Laden; „hell, wenn das Betriebssystem hell sagt" hiess für
sie: auf dem Telefon dunkel, auf dem Laptop nicht.

Was dieser Test seither hält, ist deshalb ein anderes Versprechen — aber
dasselbe Prinzip: **die Fläche hängt an den Variablen, nicht an Hexwerten.**
Sonst ist der nächste Umbau wieder ein weisser Kasten mitten in der Tafel.
"""
from __future__ import annotations

import re
from pathlib import Path

import zettel.web.app as webapp

STIL = Path(webapp.STATIC_DIR) / "stil.css"

#: Die Töne, die die Fläche tragen. Wer einen umbenennt, muss ihn in BEIDEN
#: Modi umbenennen — genau das prüft dieser Test.
TRAGENDE_TOENE = {"grund", "karte", "tinte", "gedaempft", "linie",
                  "akzent", "honig", "fehler-text", "ok-text"}


def _wurzelblock(text: str) -> str:
    return text.split(":root {", 1)[1].split("}", 1)[0]


def _toene(block: str) -> set[str]:
    return set(re.findall(r"--([a-z-]+)\s*:", block))


def _wert(block: str, name: str) -> str:
    return re.search(r"--%s:\s*([^;]+);" % re.escape(name), block).group(1).strip()


def test_die_wurzel_traegt_alle_tragenden_toene():
    stil = STIL.read_text(encoding="utf-8")
    assert TRAGENDE_TOENE <= _toene(_wurzelblock(stil)), "in :root fehlen Töne"


def test_es_gibt_nur_noch_einen_modus(datei=None):
    """Kein Farbmodus mehr am Betriebssystem (WB-418).

    Eine zurückkehrende `prefers-color-scheme`-Abfrage wäre kein Fehler an
    sich — sie wäre der Rückfall in genau das, was der Nutzer abgestellt
    haben wollte: auf dem Telefon dunkel, auf dem Laptop nicht.
    """
    stil = STIL.read_text(encoding="utf-8")
    assert "prefers-color-scheme" not in stil, (
        "Das Blatt hängt wieder am Betriebssystem.")


def test_die_tafel_ist_dunkel_und_nicht_bloss_grau():
    """Grund dunkel, Tinte hell — und dazwischen Luft.

    Ohne diese Schranke wäre eine „dunkle" Fassung mit `--grund: #888` und
    `--tinte: #aaa` formal ein Dunkelmodus und praktisch unlesbar.
    """
    wurzel = _wurzelblock(STIL.read_text(encoding="utf-8"))

    def helligkeit(ton: str) -> float:
        h = _wert(wurzel, ton).lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert helligkeit("grund") < 0.15, "der Grund ist nicht dunkel"
    assert helligkeit("karte") < 0.20, "die Karte ist nicht dunkel"
    assert helligkeit("tinte") > 0.75, "die Tinte ist nicht hell"


def test_die_flaechen_haengen_an_den_variablen_nicht_an_hexwerten():
    """`body` malt mit den Variablen. Nur dann wirkt der Tausch im Media-Block
    überhaupt — ein hart verdrahtetes `background: #fff` sähe der Test oben
    nicht."""
    stil = STIL.read_text(encoding="utf-8")
    body = stil.split("body {", 1)[1].split("}", 1)[0]
    assert "var(--grund)" in body
    assert "var(--tinte)" in body


def test_wer_weniger_bewegung_will_bekommt_weniger():
    assert "prefers-reduced-motion" in STIL.read_text(encoding="utf-8")
