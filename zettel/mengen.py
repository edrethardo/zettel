"""Zutatenmenge, Packungsgrösse, Einkaufsmenge — und die Reihenfolge dazwischen.

**Die Einkaufsmenge ist nicht die Zutatenmenge** (WB-362). Man kauft
Packungen, keine Gramm. Ein Rezept für acht statt vier braucht doppelt so
viele passierte Tomaten — aber nicht doppelt so viele Zwiebelnetze, weil ein
Kilo Zwiebeln beide Portionszahlen längst abdeckt. Wer die Zutatenmenge stumpf
verdoppelt und daraus die Korbmenge macht, legt zwei Kilo Zwiebeln in den
Korb.

Deshalb trennt dieses Modul zwei Grössen und rechnet in genau einer Richtung:

    Zutatenmenge  --skalieren-->  benötigte Menge
    benötigte Menge + Packungsgrösse  --aufrunden-->  Packungszahl

**Aufgerundet wird zuletzt, nie zwischendurch.** Das ist keine Feinheit,
sondern die ganze Ordnung: zwei Rezepte mit je 40 g Knoblauch ergeben
zusammen 80 g und damit EINE Packung à 100 g. Wer je Rezept aufrundet, kauft
zwei — und zwar systematisch, bei n Rezepten bis zu n-1 Packungen zu viel.
Das Zusammenzählen selbst steht nicht hier, sondern in `orders.korb`; hier
steht nur, dass diese Datei keine Funktion anbietet, die eine Einzelmenge
aufrundet, ohne dass der Aufrufer sie vorher zusammengezählt hat.

**Wo die Einheiten nicht zusammenpassen, wird nicht geraten.** „1 Stange
Staudensellerie" gegen eine Packung „ca. 500 g" ist nicht ausrechenbar. Dann
bleibt die Menge, wie sie ist, und der Aufrufer bekommt einen Grund, den er
hinschreiben kann — ein stillschweigend falscher Faktor ist schlimmer als
eine unveränderte Menge.

Die eine Ausnahme davon ist benannt und wird an der Rechnung ausgewiesen:
**Milliliter gegen Gramm** werden 1:1 gerechnet. Das Ticket verlangt es an
seinem eigenen Beispiel (500 ml passierte Tomaten gegen „Pomito 500 g"), und
für alles Wässrige stimmt es. Für Mehl (1 l wiegt rund 550 g) und Öl (rund
910 g) stimmt es nicht. Deshalb steht die Annahme in `Rechnung.annahme` und
in dem Satz, den die Oberfläche zeigt: sie darf gesehen und bestritten
werden, statt unsichtbar zu wirken.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from zettel import db

#: Die drei Grundeinheiten, auf die alles Rechenbare zurückgeführt wird.
GRAMM = "g"
MILLILITER = "ml"
STUECK = "Stk"

#: Einheit (gefaltet) -> (Grundeinheit, Faktor). Was hier nicht steht, ist
#: damit nicht unbrauchbar: es wird zu seiner EIGENEN Grundeinheit (siehe
#: `grundeinheit`). „Zehe" lässt sich mit „Zehe" zusammenzählen und mit
#: nichts anderem — genau das ist richtig.
UMRECHNUNG = {
    "mg": (GRAMM, 0.001),
    "g": (GRAMM, 1.0),
    "gr": (GRAMM, 1.0),
    "gramm": (GRAMM, 1.0),
    "kg": (GRAMM, 1000.0),
    "kilo": (GRAMM, 1000.0),
    "kilogramm": (GRAMM, 1000.0),
    "ml": (MILLILITER, 1.0),
    "milliliter": (MILLILITER, 1.0),
    "cl": (MILLILITER, 10.0),
    "dl": (MILLILITER, 100.0),
    "l": (MILLILITER, 1000.0),
    "ltr": (MILLILITER, 1000.0),
    "liter": (MILLILITER, 1000.0),
    "stk": (STUECK, 1.0),
    "stck": (STUECK, 1.0),
    "st": (STUECK, 1.0),
    "stueck": (STUECK, 1.0),
    # Chefkoch schreibt bei zählbaren Zutaten oft ein Grössenwort statt einer
    # Einheit: „1 große Zwiebel", „2 m.-große Kartoffeln". Gezählt wird
    # trotzdem in Stück — das Wort beschreibt die Zwiebel, nicht die Menge.
    # Ohne diese Zeilen skalierte ausgerechnet der Zwiebelfall nicht.
    "grosse": (STUECK, 1.0),
    "kleine": (STUECK, 1.0),
    "mittelgrosse": (STUECK, 1.0),
    "m.-grosse": (STUECK, 1.0),
    "m.grosse": (STUECK, 1.0),
}

#: Grundeinheiten, die gegeneinander gerechnet werden dürfen, samt Faktor und
#: der Annahme, die dabei gemacht wird. Genau ein Paar, und es ist benannt.
DICHTE_ANNAHME = "1 ml als 1 g gerechnet"


def falte(einheit) -> str:
    """Einheit -> vergleichbare Form. `„Zehe(n)"` -> `„zehe"`.

    Dieselbe Umlautfaltung wie die Suche (`db.normalisiere`), damit „Stück"
    und „Stueck" dieselbe Einheit sind. Die Pluralklammer und der Schrägstrich
    fallen weg: Chefkoch schreibt „Zehe(n)", „Stange/n", „Scheibe/n" — das ist
    dreimal dieselbe Einheit in Einzahl und Mehrzahl, und drei getrennte
    Einheiten daraus zu machen hiesse, dass sich zwei Rezepte über dieselbe
    Zutat nicht zusammenzählen lassen.
    """
    text = str(einheit or "").strip()
    text = re.split(r"[(/]", text, maxsplit=1)[0]
    text = db.normalisiere(text.casefold()).strip()
    return text.rstrip(".").strip()


def grundeinheit(einheit) -> tuple[str, float] | None:
    """Einheit -> `(Grundeinheit, Faktor)`, oder `None` für „keine Einheit".

    Eine leere Einheit ist STÜCK und nicht „unbekannt": „2 Zwiebeln" hat bei
    Chefkoch keine Einheit, und die 2 zählt trotzdem Zwiebeln. Erst wenn auch
    keine Menge dasteht („etwas Öl"), gibt es nichts zu rechnen — das prüft
    der Aufrufer an der Menge, nicht hier.

    Eine Einheit, die `UMRECHNUNG` nicht kennt, wird ihre eigene
    Grundeinheit. „Bund" ist damit mit „Bund" zusammenzählbar und mit „g"
    nicht — und das ist die richtige Antwort, nicht eine fehlende.
    """
    gefaltet = falte(einheit)
    if not gefaltet:
        return (STUECK, 1.0)
    if gefaltet in UMRECHNUNG:
        return UMRECHNUNG[gefaltet]
    return (gefaltet, 1.0)


def in_grundeinheit(menge, einheit) -> tuple[float, str] | None:
    """`(2, "kg")` -> `(2000.0, "g")`. `None`, wenn keine Menge dasteht."""
    if menge in (None, ""):
        return None
    try:
        zahl = float(menge)
    except (TypeError, ValueError):
        return None
    basis = grundeinheit(einheit)
    if basis is None:
        return None
    name, faktor = basis
    return (zahl * faktor, name)


def vergleichbar(einheit_a: str, einheit_b: str) -> tuple[float, str | None] | None:
    """Faktor, um Grundeinheit A in Grundeinheit B zu rechnen — oder `None`.

    Der zweite Rückgabewert ist die ANNAHME, unter der das gilt. Sie ist bei
    gleichen Einheiten `None` und bei ml gegen g gesetzt; ein Aufrufer, der
    sie ignoriert, verschweigt sie in der Oberfläche, und genau das soll
    auffallen können.
    """
    if einheit_a == einheit_b:
        return (1.0, None)
    fluessig = {GRAMM, MILLILITER}
    if einheit_a in fluessig and einheit_b in fluessig:
        return (1.0, DICHTE_ANNAHME)
    return None


# --------------------------------------------------------------------------
# Packungsgrösse aus `product.unit_text`

#: „500 g", „0,75 l", „1 Stk", „ca. 500 g", „10 l 20Stk" — der erste Zahl-
#: Einheit-Block gewinnt. Gemessen am echten Katalog (2026-08-28, 10.361
#: Produkte) liest dieses Muster ALLE: 7.291 in Gramm, 2.493 in Millilitern,
#: 543 in Stück — und 34 in einer Einheit, die nur mit sich selbst vergleichbar
#: ist („286 m" Toilettenpapier, „15 Wäschen", „140 Blatt"). Die 34 sind kein
#: Fehler des Musters, sondern der Fall aus Regel 4 des Tickets: gegen eine
#: Zutatenmenge in Gramm ist „15 Wäschen" nicht ausrechenbar, und geraten wird
#: nicht. Kochzutaten sind ohnehin keine darunter.
_PACKUNG = re.compile(
    r"(?P<zahl>\d+(?:[.,]\d+)?)\s*(?P<einheit>[A-Za-zÄÖÜäöüß.\-]+)")

#: „2 x 250 g" heisst 500 g und nicht „2 Stück". Ohne diesen Vorgriff läse das
#: Muster oben die 2 als Menge und das „x" als Einheit.
_MULTIPACK = re.compile(
    r"(?P<anzahl>\d+)\s*[x×]\s*(?P<zahl>\d+(?:[.,]\d+)?)\s*"
    r"(?P<einheit>[A-Za-zÄÖÜäöüß.\-]+)")


def _komma(text: str) -> float:
    return float(text.replace(",", "."))


def packungsgroesse(unit_text) -> tuple[float, str] | None:
    """`„500 g"` -> `(500.0, "g")`, in der Grundeinheit. `None`, wenn unlesbar.

    `None` ist eine Antwort und kein Fehler: ohne Packungsgrösse lässt sich
    keine Packungszahl ausrechnen, und der Aufrufer soll das sagen statt zu
    raten.
    """
    text = str(unit_text or "").strip()
    if not text:
        return None
    treffer = _MULTIPACK.search(text)
    if treffer:
        roh = _komma(treffer["zahl"]) * int(treffer["anzahl"])
        return in_grundeinheit(roh, treffer["einheit"])
    treffer = _PACKUNG.search(text)
    if not treffer:
        return None
    return in_grundeinheit(_komma(treffer["zahl"]), treffer["einheit"])


# --------------------------------------------------------------------------
# Skalieren

def faktor(von_portionen, auf_portionen) -> float:
    """Wie stark eine Zutatenmenge wächst. Ohne beide Zahlen bleibt sie gleich.

    Kein Rundungsschritt: „1 Zwiebel für 4" ergibt bei 6 Portionen 1,5
    Zwiebeln, und das ist die ehrliche Zwischenzahl. Gerundet wird erst die
    Packungszahl, und zwar nach dem Zusammenzählen.
    """
    try:
        von = int(von_portionen or 0)
        auf = int(auf_portionen or 0)
    except (TypeError, ValueError):
        return 1.0
    if von <= 0 or auf <= 0:
        return 1.0
    return auf / von


def skaliere(menge, von_portionen, auf_portionen):
    """Zutatenmenge -> benötigte Menge für die gewählte Portionszahl.

    `None` bleibt `None`: eine Zutat ohne Mengenangabe („etwas Öl") wird
    durch das Skalieren nicht zu einer mit.
    """
    if menge in (None, ""):
        return None
    try:
        zahl = float(menge)
    except (TypeError, ValueError):
        return None
    return zahl * faktor(von_portionen, auf_portionen)


# --------------------------------------------------------------------------
# Die Rechnung

@dataclass(frozen=True)
class Rechnung:
    """Was aus benötigter Menge und Packungsgrösse geworden ist.

    Ein Datensatz und kein blosser `int`, weil die Oberfläche laut Ticket
    zeigen muss, WAS gerechnet wurde: „für 8 statt 4 Portionen: 1000 ml, das
    sind 2 × Pomito 500 g". Eine stumme 2 im Mengenfeld ist das, was dieses
    Ticket verhindern soll — und der Fall „nicht ausrechenbar" braucht
    ohnehin einen Grund, den man hinschreiben kann.
    """
    #: Die benötigte Menge in ihrer Grundeinheit, oder `None`.
    bedarf: float | None = None
    bedarf_einheit: str | None = None
    #: Die Packungsgrösse in ihrer Grundeinheit, oder `None`.
    packung: float | None = None
    packung_einheit: str | None = None
    #: Die Packungszahl — `None` heisst „nicht ausrechenbar", NICHT „null
    #: Packungen". Der Unterschied ist der ganze Punkt von Regel 4.
    packungen: int | None = None
    #: Warum nicht gerechnet werden konnte. Genau dann gesetzt, wenn
    #: `packungen is None` und ein Bedarf dastand.
    grund: str | None = None
    #: Die Annahme, unter der gerechnet wurde (`DICHTE_ANNAHME`), sonst `None`.
    annahme: str | None = None
    #: Diese Zeile hat gar kein Produkt (Freitext). Dann ist die fehlende
    #: Packungsgrösse kein Mangel, sondern die Bauart der Zeile — und die
    #: Sätze weiter unten dürfen ihr keinen unterschieben (WB-385).
    freitext: bool = False

    @property
    def ausrechenbar(self) -> bool:
        return self.packungen is not None


#: Warum an einem Freitext keine Packungszahl entsteht. Kein Mangel: es gibt
#: kein Produkt, an dem eine Packungsgrösse stehen könnte. Der Satz von der
#: unlesbaren Packungsgrösse „am Produkt" schöbe einer Zeile einen Fehler
#: unter, die gar kein Produkt hat — dieselbe Begründung wie in
#: `assistant.vorschlaege._mengensatz`.
FREITEXT_GRUND = "ein Freitext hat keine Packung"


def rechne(bedarf, einheit, unit_text, *, freitext: bool = False) -> Rechnung:
    """Benötigte Menge gegen Packungsgrösse -> Packungszahl, aufgerundet.

    **Das ist der letzte Schritt und wird als letzter aufgerufen.** Wer diese
    Funktion je Rezept aufruft und die Ergebnisse addiert, hat genau den
    Fehler gemacht, um den es im Nachtrag des Tickets geht. Zusammengezählt
    wird vorher, in `orders.korb`, über die Produkt-ID.

    Aufgerundet wird immer auf mindestens 1: wer 10 g Butter braucht, kauft
    kein Zehntel Päckchen.

    `freitext=True` sagt, dass die Zeile gar kein Produkt hat. An der
    Rechnung ändert das nichts — ohne Packungsgrösse gibt es so oder so keine
    Packungszahl —, wohl aber am Grund und damit an jedem Satz, der ihn
    zitiert: „die Packungsgrösse steht nicht lesbar am Produkt" ist bei einer
    Zeile ohne Produkt schlicht unwahr (WB-385).
    """
    gebraucht = in_grundeinheit(bedarf, einheit)
    if gebraucht is None:
        return Rechnung(freitext=freitext)
    menge, menge_einheit = gebraucht
    gebinde = packungsgroesse(unit_text)
    if gebinde is None:
        return Rechnung(
            bedarf=menge, bedarf_einheit=menge_einheit, freitext=freitext,
            grund=(FREITEXT_GRUND if freitext else
                   f"die Packungsgrösse steht nicht lesbar am Produkt"
                   f"{_zitat(unit_text)}"))
    packung, packung_einheit = gebinde
    passt = vergleichbar(menge_einheit, packung_einheit)
    if passt is None:
        return Rechnung(
            bedarf=menge, bedarf_einheit=menge_einheit, freitext=freitext,
            packung=packung, packung_einheit=packung_einheit,
            # Zitiert wird der `unit_text`, wie er am Produkt steht, und nicht
            # die umgerechnete Grundeinheit: an der Packung steht „1 kg", und
            # ein Grund, der von „1000 g" spricht, schickt jemanden im Laden
            # nach einer Packung suchen, die es so nicht gibt.
            # „4 Stk passt nicht" wäre falsches Deutsch und „passen" bei
            # „0,5 Tube" auch — die Angabe steht deshalb vorn und der Satz
            # kommt ohne Zahlkongruenz aus.
            grund=(f"{schreibe(menge, menge_einheit)} lässt sich nicht gegen "
                   f"die Packung"
                   f"{_zitat(unit_text) or ' ' + schreibe(packung, packung_einheit)}"
                   " rechnen"))
    umrechnung, annahme = passt
    if packung <= 0:
        return Rechnung(
            bedarf=menge, bedarf_einheit=menge_einheit, freitext=freitext,
            packung=packung, packung_einheit=packung_einheit,
            grund="die Packungsgrösse ist null")
    zahl = max(1, math.ceil(_rund(menge * umrechnung / packung)))
    return Rechnung(bedarf=menge, bedarf_einheit=menge_einheit,
                    packung=packung, packung_einheit=packung_einheit,
                    packungen=zahl, annahme=annahme, freitext=freitext)


#: Fliesskomma frisst genau diesen Fall: 3 × 0,1 l sind 0,30000000000000004 l,
#: und `ceil` macht daraus zwei Flaschen statt einer. Auf zehn Stellen gerundet
#: bleibt die Rechnung eine Kommazahl und wird trotzdem nicht von einem
#: Rundungsfehler nach oben gedrückt.
_GENAUIGKEIT = 10


def _rund(wert: float) -> float:
    return round(wert, _GENAUIGKEIT)


def summiere(menge_a, einheit_a, menge_b, einheit_b):
    """Zwei Bedarfe zu einem — in der Grundeinheit des ERSTEN.

    Gibt `(menge, einheit)` zurück, oder `None`, wenn sich die beiden nicht
    zusammenzählen lassen (4 Stangen und 200 g sind keine Summe). Der
    Aufrufer entscheidet dann, was er behält; stillschweigend zu addieren
    wäre die schlimmste Antwort.

    Fehlt einer der beiden, ist die Summe der andere: der erste Bedarf an
    einem Posten ist auch eine Summe.
    """
    a = in_grundeinheit(menge_a, einheit_a)
    b = in_grundeinheit(menge_b, einheit_b)
    if a is None:
        return b
    if b is None:
        return a
    passt = vergleichbar(b[1], a[1])
    if passt is None:
        return None
    return (_rund(a[0] + b[0] * passt[0]), a[1])


# --------------------------------------------------------------------------
# Sätze für die Oberfläche

def zahl_deutsch(wert) -> str:
    """`3.0` -> `„3"`, `0.5` -> `„0,5"`. Wie der `menge`-Filter im Web.

    Steht hier und nicht nur dort, weil auch die Meldung aus
    `recipes.in_den_korb` diese Zahlen enthält — und die Nutzerin liest
    denselben Satz einmal aus der Vorlage und einmal aus dem Bericht.
    """
    try:
        gleitend = float(wert)
    except (TypeError, ValueError):
        return str(wert)
    if gleitend.is_integer():
        return str(int(gleitend))
    return f"{gleitend:g}".replace(".", ",")


def schreibe(menge, einheit) -> str:
    """`(1000.0, "ml")` -> `„1000 ml"`, `(3.0, "stange")` -> `„3 Stange"`.

    Die Grundeinheiten werden gespeichert, wie die Suche sie faltet — klein
    und ohne Umlaute, damit „Zehe(n)" und „Zehen" dieselbe Einheit sind. Zum
    LESEN taugt das nicht: „3 stange gebraucht" sieht aus wie ein Fehler.
    Alles, was keine der drei Grundeinheiten ist, schreibt `einheit_text`
    aus — Hauptwörter mit grossem Anfangsbuchstaben, Abkürzungen so, wie sie
    im Kochbuch stehen.
    """
    if menge is None:
        return ""
    if einheit in (GRAMM, MILLILITER, STUECK, None):
        return f"{zahl_deutsch(menge)} {einheit or STUECK}".strip()
    return f"{zahl_deutsch(menge)} {einheit_text(einheit)}"


#: Gefaltete Einheit -> Schreibweise im Kochbuch. Nur, was `schreibe` nicht
#: aus der Faltung zurückrechnen kann: die Abkürzungen. Jede Schreibweise
#: faltet auf ihren Schlüssel zurück (`test_die_einheit_wird_geschrieben_wie_
#: im_kochbuch`), damit ein Feld, das sie zeigt, sie auch zurückgeben darf.
SCHREIBWEISE = {"el": "EL", "tl": "TL", "pkt": "Pkt.", "pck": "Pck.",
                "msp": "Msp."}


def einheit_text(einheit) -> str:
    """`„el"` -> `„EL"`, `„paket"` -> `„Paket"`, `„g"` -> `„g"`, `None` -> `„"`."""
    if not einheit:
        return ""
    if einheit in (GRAMM, MILLILITER, STUECK):
        return einheit
    return SCHREIBWEISE.get(einheit, f"{einheit[:1].upper()}{einheit[1:]}")


def _zitat(text) -> str:
    sauber = str(text or "").strip()
    return f" („{sauber}“)" if sauber else ""


def bedarf_text(rechnung: Rechnung) -> str | None:
    """`„1000 ml gebraucht"` — die GEBRAUCHTE Menge, oder `None`.

    Die Hauptangabe einer Einkaufszeile (WB-381). Vor dem Regal ist sie die
    einzige Frage, die sich dort stellt; was auf der Packung steht, liest man
    dort ohnehin ab. `None` heisst „an diesem Posten wurde nie eine Menge
    ausgerechnet" — dann steht dort nichts, und vor allem rückt keine
    Packungsgrösse an ihre Stelle.
    """
    if rechnung.bedarf is None:
        return None
    return f"{schreibe(rechnung.bedarf, rechnung.bedarf_einheit)} gebraucht"


def _gebinde(rechnung: Rechnung, unit_text=None) -> str:
    """Wie die Packung heisst: der Text am Produkt, sonst die gerechnete Grösse.

    Der Text am Produkt gewinnt, weil er im Laden am Regal steht: „0,7 kg"
    steht auf der Dose, „700 g" auf keiner.
    """
    return str(unit_text or "").strip() or schreibe(rechnung.packung,
                                                    rechnung.packung_einheit)


def gebinde_text(rechnung: Rechnung, unit_text: str | None = None,
                 qty: int | None = None) -> str | None:
    """`„dafür 2 × 1 kg"` — die Packungsangabe als NEBENangabe, oder `None`.

    Die Packungszahl steht hier und nicht mehr vor dem Namen (WB-381): sie
    multipliziert die Packungsgrösse und nichts sonst. Neben ihr kann „2 ×"
    nicht mehr als „zwei Kilo" gelesen werden, und verschwiegen ist sie
    trotzdem nicht — im Laden ist sie das, was in den Wagen wandert.

    „dafür" schreibt nur, wer es belegen kann: es bindet die Zahl an den
    Bedarf, und das gilt allein, wenn sie wirklich aus ihm gerechnet wurde.
    Wo nicht gerechnet werden konnte, sagt die Angabe das selbst — „1 × 1 Stk
    — nicht ausrechenbar" gegen „6 Stange gebraucht" ist die bekannte Lücke
    aus WB-362 und liest sich sonst wie ein Rechenfehler.
    """
    gebinde = _gebinde(rechnung, unit_text)
    if not gebinde:
        return None
    if qty is None:
        return gebinde
    text = f"{int(qty)} × {gebinde}"
    if rechnung.bedarf is not None and not rechnung.ausrechenbar:
        return f"{text} — nicht ausrechenbar"
    if rechnung.ausrechenbar and int(qty) == rechnung.packungen:
        return f"dafür {text}"
    return text


def _handsatz(rechnung: Rechnung, qty) -> str | None:
    """„Im Korb liegen 3 — von Hand dazugelegt.", oder `None`.

    Im Korb liegt etwas anderes, als die Rechnung verlangt: jemand hat von
    Hand nachgelegt oder heruntergetippt. Das zu verschweigen hiesse, eine
    Zahl anzuzeigen, die nicht im Korb steht — und im Laden stünde dann
    jemand vor der falschen.
    """
    if qty is None or not rechnung.ausrechenbar or qty == rechnung.packungen:
        return None
    wort = "liegt" if qty == 1 else "liegen"
    woher = "von Hand dazugelegt" if qty > rechnung.packungen \
        else "von Hand heruntergesetzt"
    return f"Im Korb {wort} {qty} — {woher}."


def kurzgrund(rechnung: Rechnung, unit_text: str | None = None) -> str:
    """Warum nicht gerechnet wurde — in einer Zeile, ohne die Menge.

    `rechnung.grund` ist der ganze Satz für den Trace (`zettel.reason`) und
    nennt die Menge noch einmal: „2 Stk lässt sich nicht gegen die Packung
    („1 kg") rechnen". Unter einer Korbzeile, die mit „2 Stk gebraucht"
    beginnt, ist das die Menge zweimal (UI-Review 2026-09-01, Fund 8). Hier
    steht nur der Grund; leer, wenn gerechnet wurde.
    """
    if rechnung.ausrechenbar or rechnung.bedarf is None:
        return ""
    # Beide Aufrufer kehren beim Freitext schon vorher um; stünde hier je
    # etwas, wäre es „keine lesbare Packungsgrösse" an einer Zeile ohne
    # Produkt — und damit unwahr (WB-385).
    if rechnung.freitext:
        return ""
    if rechnung.packung is None:
        return f"keine lesbare Packungsgrösse{_zitat(unit_text)}"
    if rechnung.packung <= 0:
        return "Packungsgrösse null"
    packung = _zitat(unit_text) or " " + schreibe(rechnung.packung,
                                                  rechnung.packung_einheit)
    return f"nicht gegen die Packung{packung} zu rechnen"


def nachsatz(rechnung: Rechnung, unit_text: str | None = None,
             qty: int | None = None) -> str | None:
    """Was NEBEN Bedarf und Packungsangabe noch zu sagen bleibt, oder `None`.

    Der Rest von `satz()` für eine Zeile, die ihre beiden Zahlen schon selbst
    zeigt (WB-381): dort wäre der ganze Satz eine Wiederholung im
    Kleingedruckten — sie macht die Zeile länger und die Aussage nicht
    wahrer, und die Zeile ist ein Tap-Ziel auf einem Telefon.

    Übrig bleiben die drei Dinge, die aus den beiden Angaben nicht
    hervorgehen: dass von Hand nachgelegt wurde, unter welcher Annahme
    gerechnet wurde — und WARUM nicht gerechnet werden konnte, aber nur,
    wenn es gar keine Packungsangabe gibt, die es selbst sagen könnte.
    """
    if rechnung.bedarf is None:
        return None
    teile = []
    # Beim Freitext bleibt der Grund weg (WB-385): dass hier nichts gerechnet
    # wurde, sagt die Zeile schon selbst — sie trägt „Freitext" an der
    # Stelle, an der sonst die Packungsangabe steht. Ein Satz darunter, der
    # dasselbe noch einmal erklärt, ist auf einem Telefon im Laden eine Zeile
    # zu viel.
    if not rechnung.ausrechenbar and not rechnung.freitext \
            and not _gebinde(rechnung, unit_text):
        grund = kurzgrund(rechnung, unit_text)
        teile.append(f"{grund[:1].upper()}{grund[1:]}.")
    hand = _handsatz(rechnung, qty)
    if hand:
        teile.append(hand)
    if rechnung.annahme:
        teile.append(f"({rechnung.annahme}).")
    return " ".join(teile) or None


def satz(rechnung: Rechnung, produkt: str | None = None,
         unit_text: str | None = None, qty: int | None = None) -> str | None:
    """Der ganze Satz am Stück — oder `None`, wenn es nichts zu sagen gibt.

    Regel 6 des WB-362-Tickets in einer Funktion: „1000 ml gebraucht — 2 ×
    Pomito 500 g". Er steht hier und nicht in der Vorlage, damit die Tests
    denselben Satz prüfen, den die Nutzerin liest (dieselbe Begründung wie
    bei `recipes.uebernahme._meldung`).

    Wo eine Zeile Bedarf und Packungsangabe schon getrennt zeigt (die
    Pick-Liste seit WB-381), gehört nicht dieser Satz darunter, sondern nur
    sein Rest: `nachsatz()`.
    """
    gebraucht = bedarf_text(rechnung)
    if gebraucht is None:
        return None
    if rechnung.freitext and not rechnung.ausrechenbar:
        # „3 Stk gebraucht." und sonst nichts (WB-385): beim Freitext war nie
        # eine Rechnung im Gang, und ein Grund erklärte eine Lücke, die keine
        # ist.
        return f"{gebraucht}."
    if not rechnung.ausrechenbar:
        return f"{gebraucht} — {kurzgrund(rechnung, unit_text)}."
    name = f" {produkt}" if produkt else ""
    teile = [f"{gebraucht} — {rechnung.packungen} ×{name} "
             f"{_gebinde(rechnung, unit_text)}".rstrip() + "."]
    hand = _handsatz(rechnung, qty)
    if hand:
        teile.append(hand)
    if rechnung.annahme:
        teile.append(f"({rechnung.annahme}).")
    return " ".join(teile)
