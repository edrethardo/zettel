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

from picknick import db

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

    @property
    def ausrechenbar(self) -> bool:
        return self.packungen is not None


def rechne(bedarf, einheit, unit_text) -> Rechnung:
    """Benötigte Menge gegen Packungsgrösse -> Packungszahl, aufgerundet.

    **Das ist der letzte Schritt und wird als letzter aufgerufen.** Wer diese
    Funktion je Rezept aufruft und die Ergebnisse addiert, hat genau den
    Fehler gemacht, um den es im Nachtrag des Tickets geht. Zusammengezählt
    wird vorher, in `orders.korb`, über die Produkt-ID.

    Aufgerundet wird immer auf mindestens 1: wer 10 g Butter braucht, kauft
    kein Zehntel Päckchen.
    """
    gebraucht = in_grundeinheit(bedarf, einheit)
    if gebraucht is None:
        return Rechnung()
    menge, menge_einheit = gebraucht
    gebinde = packungsgroesse(unit_text)
    if gebinde is None:
        return Rechnung(
            bedarf=menge, bedarf_einheit=menge_einheit,
            grund=(f"die Packungsgrösse steht nicht lesbar am Produkt"
                   f"{_zitat(unit_text)}"))
    packung, packung_einheit = gebinde
    passt = vergleichbar(menge_einheit, packung_einheit)
    if passt is None:
        return Rechnung(
            bedarf=menge, bedarf_einheit=menge_einheit,
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
            bedarf=menge, bedarf_einheit=menge_einheit,
            packung=packung, packung_einheit=packung_einheit,
            grund="die Packungsgrösse ist null")
    zahl = max(1, math.ceil(_rund(menge * umrechnung / packung)))
    return Rechnung(bedarf=menge, bedarf_einheit=menge_einheit,
                    packung=packung, packung_einheit=packung_einheit,
                    packungen=zahl, annahme=annahme)


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
    Alles, was keine der drei Grundeinheiten ist, ist im Deutschen ein
    Hauptwort und bekommt seinen grossen Anfangsbuchstaben zurück.
    """
    if menge is None:
        return ""
    if einheit in (GRAMM, MILLILITER, STUECK, None):
        return f"{zahl_deutsch(menge)} {einheit or STUECK}".strip()
    return f"{zahl_deutsch(menge)} {einheit[:1].upper()}{einheit[1:]}"


def _zitat(text) -> str:
    sauber = str(text or "").strip()
    return f" („{sauber}“)" if sauber else ""


def satz(rechnung: Rechnung, produkt: str | None = None,
         unit_text: str | None = None, qty: int | None = None) -> str | None:
    """Der Satz, den die Oberfläche zeigt — oder `None`, wenn es nichts zu
    sagen gibt.

    Regel 6 des Tickets in einer Funktion: „1000 ml gebraucht — 2 × Pomito
    500 g". Er steht hier und nicht in der Vorlage, damit die Tests denselben
    Satz prüfen, den die Nutzerin liest (dieselbe Begründung wie bei
    `recipes.uebernahme._meldung`).
    """
    if rechnung.bedarf is None:
        return None
    gebraucht = f"{schreibe(rechnung.bedarf, rechnung.bedarf_einheit)} gebraucht"
    if not rechnung.ausrechenbar:
        return (f"{gebraucht} — {rechnung.grund}, die Menge bleibt, "
                "wie sie ist.")
    gebinde = str(unit_text or "").strip() or schreibe(rechnung.packung,
                                                       rechnung.packung_einheit)
    name = f" {produkt}" if produkt else ""
    teile = [f"{gebraucht} — {rechnung.packungen} ×{name} {gebinde}".rstrip()
             + "."]
    if qty is not None and qty != rechnung.packungen:
        # Im Korb liegt etwas anderes, als die Rechnung verlangt: jemand hat
        # von Hand nachgelegt oder heruntergetippt. Das zu verschweigen hiesse,
        # eine Zahl anzuzeigen, die nicht im Korb steht — und im Laden stünde
        # dann jemand vor der falschen.
        wort = "liegt" if qty == 1 else "liegen"
        woher = "von Hand dazugelegt" if qty > rechnung.packungen \
            else "von Hand heruntergesetzt"
        teile.append(f"Im Korb {wort} {qty} — {woher}.")
    if rechnung.annahme:
        teile.append(f"({rechnung.annahme}).")
    return " ".join(teile)
