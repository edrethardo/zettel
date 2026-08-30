"""Beide Farbmodi sind definiert und gemeint (WB-360).

Der Dunkelmodus ist kein Filter, sondern ein zweiter Entwurf: das Blatt
definiert seine Farbwelt als Variablen in `:root`, und der Block
`@media (prefers-color-scheme: dark)` setzt DIESELBEN Variablen neu. Fällt
eine Seite dieser Abmachung beim nächsten Umbau weg, merkt es dieser Test —
sonst merkt es erst jemand abends auf dem Sofa.
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


def test_beide_farbmodi_definieren_die_tragenden_toene():
    stil = STIL.read_text(encoding="utf-8")
    assert "@media (prefers-color-scheme: dark)" in stil

    hell = _wurzelblock(stil)
    dunkel = _wurzelblock(stil.split("@media (prefers-color-scheme: dark)", 1)[1])

    assert TRAGENDE_TOENE <= _toene(hell), "im hellen Modus fehlen Töne"
    assert TRAGENDE_TOENE <= _toene(dunkel), "im dunklen Modus fehlen Töne"


def test_der_dunkelmodus_ist_ein_eigener_entwurf_und_keine_kopie():
    stil = STIL.read_text(encoding="utf-8")
    hell = _wurzelblock(stil)
    dunkel = _wurzelblock(stil.split("@media (prefers-color-scheme: dark)", 1)[1])
    for ton in ("grund", "karte", "tinte", "akzent"):
        assert _wert(hell, ton) != _wert(dunkel, ton), (
            f"--{ton} ist in beiden Modi gleich — der Dunkelmodus wäre damit "
            "keiner")


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
