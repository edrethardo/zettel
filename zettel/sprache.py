"""Die Sprache der Oberfläche — Deutsch und Englisch, umschaltbar.

Der Haushalt spricht Deutsch, und das bleibt die Vorgabe. Englisch kommt
dazu, damit dieses Repo von aussen benutzbar ist: wer den Shop in einem
anderen Land aufsetzt, soll ihn bedienen können, ohne vorher Deutsch zu
lernen — und wer etwas beitragen will, soll sehen, wo ein Satz herkommt.

**Drei Entscheidungen, die von aussen wie ein Versehen aussehen könnten:**

1. **Der Code bleibt deutsch.** Bezeichner, Kommentare und Tabellennamen
   werden nicht übersetzt. Die Kommentare dieses Projekts tragen die
   Begründungen — sie sind der Teil, der beim Übersetzen kaputtginge, und
   `CONTRIBUTING.md` erklärt Auswärtigen, warum sie trotzdem mitmachen
   können. Übersetzt wird, was auf dem Bildschirm steht.

2. **Eine fehlende Übersetzung fällt auf Deutsch zurück, nie auf den
   Schlüssel.** Ein Blatt, auf dem „korb.leer" steht, ist kaputt; eines, auf
   dem ein deutscher Satz zwischen englischen steht, ist unvollständig — und
   unvollständig ist benutzbar. Damit kann jemand eine Zeile Englisch
   beitragen, ohne die ganze Datei zu schaffen.

3. **Kein gettext, keine .po-Dateien, kein Build-Schritt.** Zwei JSON-Dateien
   unter `zettel/web/texte/`. Wer eine Übersetzung beisteuern will, ändert
   eine Textdatei und startet nichts neu — das ist der niedrigste Zaun, den
   dieses Projekt um einen Beitrag ziehen kann.

Was fehlt und bewusst fehlt: die Sätze, die der Chat-Agent zur Laufzeit baut
(„… 14 Zutaten im Rezept, 10 davon auf dem Zettel"). Sie stehen in
`zettel/assistant/` und sind Text MIT Zahlen darin; sie zu übersetzen ist eine
eigene Welle und keine Nebensache dieser hier. Bis dahin bleiben sie deutsch,
auch wenn die Oberfläche englisch steht — sichtbar unvollständig ist besser
als still falsch.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Die Sprachen, die es gibt. Eine neue kommt als Datei dazu und muss hier
#: eingetragen werden — damit ein Tippfehler im Cookie keine halbe Oberfläche
#: erzeugt, sondern nichts.
SPRACHEN = ("de", "en")

#: Die Sprache des Haushalts. Sie ist auch der Rückfall für jeden Satz, den
#: eine andere Datei nicht kennt.
VORGABE = "de"

COOKIE = "zettel_sprache"

TEXT_DIR = Path(__file__).parent / "web" / "texte"

_zwischenspeicher: dict[str, dict[str, str]] = {}


def lade(sprache: str) -> dict[str, str]:
    """Die Texte einer Sprache. Wird einmal gelesen und dann behalten.

    Eine fehlende oder kaputte Datei ist kein Grund, den Shop anzuhalten: sie
    ergibt eine leere Sprache, und die fällt Satz für Satz auf Deutsch
    zurück. Der Fehler steht dann auf dem Bildschirm (deutsche Sätze in einer
    englischen Oberfläche) statt in einem Stacktrace.
    """
    if sprache in _zwischenspeicher:
        return _zwischenspeicher[sprache]
    pfad = TEXT_DIR / f"{sprache}.json"
    try:
        texte = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(texte, dict):
            texte = {}
    except (OSError, json.JSONDecodeError):
        texte = {}
    _zwischenspeicher[sprache] = texte
    return texte


def vergiss() -> None:
    """Den Zwischenspeicher leeren — für Tests und für einen Neustart ohne."""
    _zwischenspeicher.clear()


def uebersetzer(sprache: str):
    """Gibt das `t()`, das die Vorlagen benutzen.

    `t("korb.leer")` liefert den Satz; `t("korb.zahl", n=3)` füllt Platzhalter
    ein. Ein unbekannter Platzhalter lässt den Satz stehen, statt eine
    Ausnahme zu werfen — ein fehlendes Wort darf keine Seite kosten.
    """
    sprache = sprache if sprache in SPRACHEN else VORGABE
    texte = lade(sprache)
    rueckfall = lade(VORGABE) if sprache != VORGABE else texte

    def t(schluessel: str, **werte) -> str:
        satz = texte.get(schluessel)
        if satz is None:
            satz = rueckfall.get(schluessel, schluessel)
        if not werte:
            return satz
        try:
            return satz.format(**werte)
        except (KeyError, IndexError, ValueError):
            return satz

    return t


def aus_request(request) -> str:
    """Welche Sprache dieser Besuch will.

    Reihenfolge: das Cookie (eine getroffene Wahl), sonst `Accept-Language`
    des Browsers, sonst Deutsch. Der Browser wird nur gefragt, solange
    niemand gewählt hat — eine Wahl gilt danach, auch wenn das Telefon etwas
    anderes meldet.
    """
    gewaehlt = (request.cookies.get(COOKIE) if request is not None else None)
    if gewaehlt in SPRACHEN:
        return gewaehlt
    kopf = ""
    try:
        kopf = request.headers.get("accept-language", "") or ""
    except AttributeError:
        kopf = ""
    for stueck in kopf.split(","):
        code = stueck.split(";")[0].strip().lower()[:2]
        if code in SPRACHEN:
            return code
    return VORGABE


def fehlende(sprache: str) -> list[str]:
    """Welche Schlüssel diese Sprache gegenüber der Vorgabe nicht hat.

    Für Beitragende: `python -m zettel.sprache en` sagt, was noch zu tun ist.
    Bewusst kein Test, der daran scheitert — eine unvollständige Übersetzung
    soll man beisteuern dürfen, ohne die ganze Datei zu schaffen.
    """
    return sorted(set(lade(VORGABE)) - set(lade(sprache)))


def ueberzaehlige(sprache: str) -> list[str]:
    """Schlüssel, die es nur in dieser Sprache gibt — fast immer ein Tippfehler."""
    return sorted(set(lade(sprache)) - set(lade(VORGABE)))


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Welche Texte fehlen einer Sprache noch?")
    p.add_argument("sprache", nargs="?", default="en", choices=SPRACHEN)
    args = p.parse_args(argv)
    fehlt = fehlende(args.sprache)
    zuviel = ueberzaehlige(args.sprache)
    gesamt = len(lade(VORGABE))
    print(f"{args.sprache}: {gesamt - len(fehlt)}/{gesamt} Texte übersetzt")
    for s in fehlt:
        print(f"  fehlt:       {s}")
    for s in zuviel:
        print(f"  überzählig:  {s}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
