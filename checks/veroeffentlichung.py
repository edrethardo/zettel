#!/usr/bin/env python3
"""Das zweite Gate: darf dieses Repo raus?

    .venv/bin/python checks/veroeffentlichung.py     # exit 0 grün, 1 rot

`smoke.py` fragt, ob der Shop läuft. Dieses Gate fragt etwas anderes: ob das,
was hier liegt, öffentlich werden darf — und ob es dann noch stimmt. Beides
sind Fragen, die man vor einem `git push` genau einmal falsch beantwortet.

Der Anlass war konkret. Am 08.09.2026 wurde ein Tag Arbeit committet, und
`test_keine_privaten_angaben_im_repo` schlug erst hinterher an: der Hostname
der Box stand in einer Plandatei. Der Test prüft `git ls-files` — den
Arbeitsbaum. Beim Veröffentlichen zählt aber die **Historie**, und dort steht
der Name bis heute in Commits aus dem August. Ein Gate, das nur den
Arbeitsbaum ansieht, hätte grün gemeldet und den Namen trotzdem ins Netz
getragen.

Sechs Fragen:

1. **Trägt die Historie noch private Angaben?** Nicht die Dateien von heute,
   sondern jeder Commit. Das ist der Grund, warum vor dem Push ein
   gefilterter Klon gebaut wird.
2. **Trägt der Arbeitsbaum noch welche?** Dieselbe Frage, billiger — damit
   ein Fund nicht erst nach dem Filtern auffällt.
3. **Stimmen die Zahlen untereinander?** Testzahl und Check-Zahl stehen in
   sechs Dokumenten. Am 08.09. standen sie in fünf davon auf einem Stand vom
   04.09. Ein Dokument, das eine andere Zahl nennt als das Dokument daneben,
   ist schlimmer als eines ohne Zahl.
4. **Zeigt jeder Verweis auf etwas, das es gibt?** Ein toter Link im README
   ist auf GitHub sofort sichtbar, und ein fehlendes Bild ist ein leerer
   Kasten an der Stelle, an der der Beleg stehen sollte.
5. **Stehen Platzhalter nur im Entwurf?** `<video link>` gehört in
   `docs/contest/POST.md`, den der Mensch beim Posten füllt — nirgends sonst.
6. **Deckt sich die Modellbehauptung mit den Messdaten?** Solange kein
   Nemotron-Lauf des Wochenplaners in `evals/` liegt, MUSS der Vorbehalt im
   Post stehen. Liegt einer, muss der Vorbehalt weg. Die Behauptung und ihr
   Beleg werden hier aneinander gebunden, damit keiner ohne den anderen
   wandern kann.

Was dieses Gate NICHT kann: sagen, ob eine Zahl richtig gemessen wurde. Es
prüft Übereinstimmung, nicht Wahrheit. Die Wahrheit steht in `EVALS.md` und
hängt an den Provenienz-Dateien.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))

from checks.bericht import Bericht  # noqa: E402

#: Zusammengesetzt, damit dieses Gate sich nicht selbst meldet — dieselbe
#: Vorsichtsmassnahme wie in `tests/test_betrieb.py`.
VERBOTEN = ["100.117." + "80.100", "sphe" + "ron", "aar" + "on"]

#: Platzhalter, die vor dem Posten ersetzt werden. Im Entwurf sind sie
#: richtig; in einem Dokument, das jemand als fertig liest, sind sie ein
#: Versprechen ins Leere.
PLATZHALTER = ["<video link>", "<HF dataset link>"]
ENTWURF = "docs/contest/POST.md"

#: Die Dokumente, die in der GEGENWART sprechen. Nur sie müssen sich über
#: eine Zahl einig sein. Specs, Reviews, Pläne und die Geschichtsabschnitte
#: von `VIDEO.md` tragen mit Absicht alte Zahlen — sie berichten, was an
#: einem Tag galt, und wären falsch, wenn jemand sie nachzöge.
JETZT = ["README.md", "README.en.md", "SHOWCASE.md", "GETTING-STARTED.md",
         "CASE-STUDY.md", "docs/contest/POST.md"]

#: Der Vorbehalt aus dem Post und das Messdatum, an dem er hängt.
VORBEHALT = "not yet on Nemotron"
NEMOTRON_PLAN = "plan_probe-*nemotron*.json"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=WURZEL, check=True,
                          capture_output=True, text=True).stdout


def getrackt() -> list[str]:
    return [n for n in git("ls-files", "-z").split("\0") if n]


def text_von(name: str) -> str:
    try:
        return (WURZEL / name).read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def checks_historie(b: Bericht) -> None:
    b.abschnitt("Die Historie — nicht nur der Arbeitsbaum")
    for wort in VERBOTEN:
        def pruefen(wort=wort):
            # `git log -S` zählt Commits, die das Vorkommen ÄNDERN — dass
            # eines dabei ist, heisst: der String steht in dieser Historie.
            treffer = [z for z in git("log", "--oneline", "-S", wort,
                                      "--all").splitlines() if z]
            if treffer:
                raise AssertionError(
                    f"{len(treffer)} Commits, ältester zuerst zu sehen mit "
                    f"`git log -S \"{wort}\"` — vor dem Push einen "
                    "gefilterten Klon bauen (git-filter-repo)")
            return "kein Commit"
        b.pruefe(f"kein „{wort}\" in der Historie", pruefen)


def checks_arbeitsbaum(b: Bericht) -> None:
    b.abschnitt("Der Arbeitsbaum")
    dateien = getrackt()

    def pruefen():
        funde = [f"{n}: {w}" for n in dateien for w in VERBOTEN
                 if w in text_von(n)]
        if funde:
            raise AssertionError("; ".join(funde[:5]))
        return f"{len(dateien)} Dateien"
    b.pruefe("keine privaten Angaben in getrackten Dateien", pruefen)

    def lizenz():
        t = text_von("LICENSE")
        if len(t.strip()) < 200:
            raise AssertionError("LICENSE fehlt oder ist zu kurz")
        return t.splitlines()[0]
    b.pruefe("LICENSE liegt bei", lizenz)


def _zahlen(muster: str, dateien: list[str]) -> dict[int, list[str]]:
    """Alle Zahlen zu einem Muster, gruppiert — wer sagt was?"""
    raus: dict[int, list[str]] = {}
    for name in dateien:
        for roh in re.findall(muster, text_von(name)):
            wert = int(roh.replace(".", "").replace(",", ""))
            raus.setdefault(wert, []).append(name)
    return raus


def checks_zahlen(b: Bericht) -> None:
    b.abschnitt("Die Zahlen, die in mehreren Dokumenten stehen")
    dateien = [n for n in JETZT if (WURZEL / n).exists()]

    def tests():
        gefunden = _zahlen(r"([\d][\d.,]{2,})\s*[Tt]ests", dateien)
        if len(gefunden) > 1:
            teile = [f"{z}: {', '.join(sorted(set(w)))}"
                     for z, w in sorted(gefunden.items())]
            raise AssertionError("verschiedene Testzahlen — " + " | ".join(teile))
        return f"{next(iter(gefunden), '?')} überall gleich"
    b.pruefe("die Testzahl ist in allen Dokumenten dieselbe", tests)

    def gates():
        gefunden = _zahlen(r"(\d+)[- ][Cc]heck", dateien)
        if len(gefunden) > 1:
            teile = [f"{z}: {', '.join(sorted(set(w)))}"
                     for z, w in sorted(gefunden.items())]
            raise AssertionError("verschiedene Check-Zahlen — "
                                 + " | ".join(teile))
        return f"{next(iter(gefunden), '?')} überall gleich"
    b.pruefe("die Check-Zahl ist in allen Dokumenten dieselbe", gates)

    def gegen_die_wirklichkeit():
        """Einig zu sein reicht nicht — die Zahl muss auch stimmen.

        Zweimal in zwei Tagen stand in fünf Dokumenten einträchtig dieselbe
        veraltete Zahl. Übereinstimmung hätte das nicht gefunden; `pytest
        --collect-only` findet es in Sekunden und ohne Netz.
        """
        roh = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--collect-only",
             "--disable-warnings"], cwd=WURZEL, capture_output=True,
            text=True).stdout
        treffer = re.search(r"(\d+)\s+tests? collected", roh)
        if not treffer:
            raise AssertionError("pytest hat nichts gesammelt — Suite kaputt?")
        echt = int(treffer.group(1))
        genannt = _zahlen(r"([\d][\d.,]{2,})\s*[Tt]ests", dateien)
        if genannt and echt not in genannt:
            raise AssertionError(
                f"die Dokumente sagen {sorted(genannt)}, gesammelt sind "
                f"{echt} — die Zahl ist veraltet")
        return f"{echt} gesammelt"
    b.pruefe("die genannte Testzahl ist die gemessene", gegen_die_wirklichkeit)


def checks_verweise(b: Bericht) -> None:
    b.abschnitt("Verweise — Bilder und Links, die auf Dateien zeigen")
    dateien = [n for n in getrackt() if n.endswith(".md")]
    # `[text](ziel)`; nur relative Ziele, keine URLs und keine Anker.
    muster = re.compile(r"\]\(([^)\s]+)\)")

    def pruefen():
        tot = []
        for name in dateien:
            basis = (WURZEL / name).parent
            for ziel in muster.findall(text_von(name)):
                if ziel.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                # Kein Pfad, sondern etwas, das nur wie einer aussieht: in
                # den Plandokumenten stehen Regexe in Klammern hinter einem
                # `]`, und `](\\d+)` ist kein toter Link, sondern Prosa.
                if "\\" in ziel:
                    continue
                pfad = (basis / ziel.split("#", 1)[0]).resolve()
                if not pfad.exists():
                    tot.append(f"{name} -> {ziel}")
        if tot:
            raise AssertionError(f"{len(tot)} tot: " + "; ".join(tot[:5]))
        return f"{len(dateien)} Dokumente"
    b.pruefe("jeder relative Verweis zeigt auf eine vorhandene Datei", pruefen)


def checks_platzhalter(b: Bericht) -> None:
    b.abschnitt("Platzhalter")
    dateien = [n for n in getrackt() if n.endswith(".md") and n != ENTWURF]

    def pruefen():
        funde = [f"{n}: {p}" for n in dateien for p in PLATZHALTER
                 if p in text_von(n)]
        if funde:
            raise AssertionError("; ".join(funde))
        return f"nur in {ENTWURF}"
    b.pruefe("Platzhalter stehen ausschliesslich im Post-Entwurf", pruefen)


def pruefe_modellbehauptung(gemessen, post: str) -> str:
    """Behauptung gegen Beleg. Eigene Funktion, damit ein Test sie erreicht.

    `gemessen` sind die gefundenen Nemotron-Läufe des Wochenplaners, `post`
    der Text des Entwurfs. Beide Richtungen sind ein Fehler: ein Vorbehalt
    ohne Messung fehlt, eine Messung ohne gestrichenen Vorbehalt veraltet.
    """
    steht = VORBEHALT in post
    if gemessen and steht:
        raise AssertionError(
            f"{gemessen[0].name} liegt vor, aber der Post sagt noch "
            f"„{VORBEHALT}\" — Zahl eintragen, Vorbehalt streichen")
    if not gemessen and not steht:
        raise AssertionError(
            "kein Nemotron-Lauf des Wochenplaners in evals/, aber der "
            f"Post trägt den Vorbehalt „{VORBEHALT}\" nicht mehr — "
            "so liest sich die Qwen-Zahl als Nemotron-Zahl")
    return ("Lauf liegt, Vorbehalt ist weg" if gemessen
            else "kein Lauf, Vorbehalt steht")


def checks_modellbehauptung(b: Bericht) -> None:
    b.abschnitt("Behauptung und Beleg")
    b.pruefe(
        "der Wochenplan-Absatz nennt das Modell, das gemessen wurde",
        lambda: pruefe_modellbehauptung(
            sorted((WURZEL / "evals").glob(NEMOTRON_PLAN)),
            text_von(ENTWURF)))


def main() -> int:
    b = Bericht("das Repo darf raus")
    checks_historie(b)
    checks_arbeitsbaum(b)
    checks_zahlen(b)
    checks_verweise(b)
    checks_platzhalter(b)
    checks_modellbehauptung(b)
    return b.ende()


if __name__ == "__main__":
    sys.exit(main())
