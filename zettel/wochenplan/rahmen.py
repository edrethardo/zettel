"""Die Rahmenbedingungen eines Plans — aus dem Formular gelesen, ohne Modell.

Der Vorschlag aus dem Familienchat („2.200 kcal, proteinreich, max. 20
Minuten, 80 € pro Woche, und was noch im Kühlschrank liegt") ist ein Satz.
Hier wird er trotzdem nicht von einem Modell gelesen, sondern aus Feldern:
Tage, Personen, Minuten, Budget sind vier Zahlen, und **eine Zahl, die ein
Modell aus einem Satz liest, ist eine Zahl, die das Modell erfunden haben
kann.** Vier Felder auf einem Telefon sind billiger als ein Verwurf-Zähler
für Rahmenzahlen.

Der Bestand ist der einzige Freitext — „500 g Kartoffeln, 6 Eier, Nudeln" —
und der wird mit einem Zerleger gelesen, der nichts errät: Zahl, Einheit
(wenn `mengen` sie kennt), Rest ist der Name. „Nudeln" ohne Zahl ist ein
Bestand ohne Menge; er deckt nichts ab, steht aber im Plan, damit niemand
Nudeln kauft, ohne es zu merken.

**Seit 2026-09-10 gibt es den Satz doch** — Aaron wollte „eine Mahlzeit pro
Tag, 700 Kalorien, viel Protein, Kartoffeln, Eier und Nudeln sind da" tippen
und die Maske gefüllt sehen. Der Einwand oben bleibt richtig, und er wird
nicht umgangen, sondern beantwortet: das Modell liest den Satz
(`assistant.plan.rahmen_lesen`), und **jede Zahl, die es liefert, muss
wörtlich im Satz stehen** — „700" im Satz, sonst kein kcal-Ziel; jeder
Bestandsname muss ein Stück des Satzes sein. Was nicht belegt ist, wird
verworfen und gezählt, nie übernommen. Und die gefüllte Maske steht sichtbar
unter dem Satz, damit der Mensch sieht, was verstanden wurde, bevor er ihr
glaubt. `aus_lesung()` baut daraus denselben `Rahmen` wie `aus_formular()`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from zettel import mengen

#: Vorgaben, wenn das Formular leer bleibt. Fünf Tage sind die Arbeitswoche;
#: zwei Personen sind dieser Haushalt (Spec 10).
TAGE_VORGABE = 5
PERSONEN_VORGABE = 2

#: Obergrenzen gegen Vertipper. 14 Tage sind schon eine lange Planung; „50"
#: ist eine Null zu viel.
MAX_TAGE = 14
MAX_PERSONEN = 12


@dataclass(frozen=True)
class Rahmen:
    tage: int = TAGE_VORGABE
    personen: int | None = PERSONEN_VORGABE
    max_minuten: int | None = None
    budget_cents: int | None = None
    #: kcal je Person und Tag — ein Ziel zum Vergleichen, keine Regel für
    #: das Modell (es sieht keine Nährwerte und rechnet keine).
    kcal_ziel: int | None = None
    #: Der Bestandstext, wörtlich, wie eingegeben.
    text: str | None = None
    bestand: list[dict] = field(default_factory=list)
    #: Der Satz aus dem Chat, wörtlich — leer bei einem Plan aus dem Formular.
    satz: str | None = None
    #: Was das Modell als Vorliebe las („eiweissreich"); geht in die Vorlage.
    vorlieben: str | None = None


_ZAHL = re.compile(r"\d+(?:[.,]\d+)?")


def _zahl(wert) -> float | None:
    """Die erste Zahl in einem Feld — „30 min" -> 30.0, leer -> None."""
    treffer = _ZAHL.search(str(wert or ""))
    if not treffer:
        return None
    return float(treffer.group(0).replace(",", "."))


def _ganz(wert, *, vorgabe=None, hoechstens=None):
    zahl = _zahl(wert)
    if zahl is None or zahl < 1:
        return vorgabe
    ganz = int(zahl)
    if hoechstens is not None and ganz > hoechstens:
        return hoechstens
    return ganz


def _cents(wert) -> int | None:
    """„40,50" -> 4050, „40 €" -> 4000, leer -> None."""
    zahl = _zahl(wert)
    if zahl is None or zahl <= 0:
        return None
    return int(round(zahl * 100))


def aus_formular(werte: dict) -> Rahmen:
    """Formularwerte -> `Rahmen`. Nachsichtig bei Zahlen, unnachgiebig sonst.

    Unsinn in einem Feld fällt auf die Vorgabe zurück statt das Anlegen zu
    verhindern — dieselbe Regel wie bei `recipes.uebernahme._portionen`: die
    Zahl kommt von einem Telefon, und ein Tippfehler darf keinen Plan kosten.
    """
    text = " ".join(str(werte.get("bestand") or "").split()) or None
    return Rahmen(
        tage=_ganz(werte.get("tage"), vorgabe=TAGE_VORGABE, hoechstens=MAX_TAGE),
        personen=_ganz(werte.get("personen"), vorgabe=PERSONEN_VORGABE,
                       hoechstens=MAX_PERSONEN),
        max_minuten=_ganz(werte.get("max_minuten")),
        budget_cents=_cents(werte.get("budget")),
        kcal_ziel=_ganz(werte.get("kcal"), hoechstens=10_000),
        text=text,
        bestand=bestand_aus_text(text),
    )


#: Komma trennt Zeilen — ausser zwischen zwei Ziffern: „1,5 kg Mehl" ist
#: eine Zeile mit Dezimalkomma und nicht „1" und „5 kg Mehl".
_TRENNER = re.compile(r"[;\n]+|(?<!\d),|,(?!\d)")


def bestand_aus_text(text) -> list[dict]:
    """„500 g Kartoffeln, 6 Eier, Nudeln" -> drei Zeilen.

        {"menge": 500.0, "einheit": "g", "name": "Kartoffeln"}
        {"menge": 6.0,   "einheit": None, "name": "Eier"}
        {"menge": None,  "einheit": None, "name": "Nudeln"}

    Die Einheit wird nur erkannt, wenn `mengen` sie kennt — sonst gehört das
    Wort zum Namen („6 Eier" hat keine Einheit, „Eier" ist der Name). Eine
    Zeile ohne Namen („500 g") ist keine Aussage und fällt weg.
    """
    zeilen = []
    for stueck in _TRENNER.split(str(text or "")):
        woerter = stueck.split()
        if not woerter:
            continue
        menge = einheit = None
        if _ZAHL.fullmatch(woerter[0]):
            menge = float(woerter[0].replace(",", "."))
            woerter = woerter[1:]
            if woerter and mengen.falte(woerter[0]) in mengen.UMRECHNUNG:
                einheit = mengen.falte(woerter[0])
                woerter = woerter[1:]
        name = " ".join(woerter).strip(" .")
        if not name:
            continue
        zeilen.append({"menge": menge, "einheit": einheit, "name": name})
    return zeilen


def aus_lesung(werte: dict, satz: str) -> Rahmen:
    """Die geprüfte Lesung eines Satzes -> `Rahmen`, wie `aus_formular`.

    `werte` ist das Ergebnis von `assistant.plan.rahmen_lesen` — dort ist
    schon geprüft, dass jede Zahl im Satz steht. Hier gelten dieselben
    Obergrenzen und Vorgaben wie beim Formular, damit ein Satz keinen Plan
    anlegen kann, den das Formular verweigert hätte.
    """
    text = ", ".join(str(b).strip() for b in (werte.get("bestand") or [])
                     if str(b).strip()) or None
    return Rahmen(
        tage=_ganz(werte.get("tage"), vorgabe=TAGE_VORGABE, hoechstens=MAX_TAGE),
        personen=_ganz(werte.get("personen"), vorgabe=PERSONEN_VORGABE,
                       hoechstens=MAX_PERSONEN),
        max_minuten=_ganz(werte.get("max_minuten")),
        budget_cents=_cents(werte.get("budget_euro")),
        kcal_ziel=_ganz(werte.get("kcal"), hoechstens=10_000),
        text=text,
        bestand=bestand_aus_text(text),
        satz=" ".join(str(satz or "").split()) or None,
        vorlieben=(str(werte.get("vorlieben")).strip() or None)
        if werte.get("vorlieben") else None,
    )
