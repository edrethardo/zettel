"""Aus dem Text eines Kassenbons werden Posten (WB-358).

Gemessen an je einem echten Bon (2026-08-28), nicht ausgedacht:

    ANTIPASTI VARIAT                 5,69 B      Rewe, eBon als PDF
    SCHOTT. CHEDDAR                  4,38 B
                  2 Stk x   2,19                 die Menge steht DARUNTER
    PFAND 0,25 EURO                  0,25 A *
     --------------------------------------
     SUMME                   EUR     41,23

    Walter Popps Colesla          2,49 A          Lidl, Screenshot
    Hähnchenbrustroulade          1,89 A

Beide Läden schreiben dieselbe Zeile: Name links, Preis rechtsbündig,
**Steuerklasse als einzelner Buchstabe dahinter**. Dieser Buchstabe ist der
Anker dieses Moduls. Er unterscheidet eine Artikelzeile von allem anderen auf
dem Bon — `SUMME EUR 41,23` trägt ihn nicht, `Geg. Mastercard EUR 41,23` auch
nicht, und die Steuertabelle am Fuss (`A= 19,0% 1,63 0,31 1,94`) endet auf
einer Zahl statt auf einem Buchstaben. Eine Regel über die Zeilenform allein
(„irgendwo steht ein Preis") träfe alle drei.

Drei Dinge, die dieses Modul deshalb ausdrücklich tut:

**1. Die Mengenzeile wird nach oben gezogen.** `2 Stk x 2,19` ist keine Zeile
für sich, sondern die Menge der Zeile darüber. Wer den Bon zeilenweise liest
und jede Zeile für sich beurteilt, verliert sie — und kauft im Datenbestand
eine Packung Cheddar statt zwei.

**2. Die Artikel enden bei der Summe.** Alles ab der Trennlinie beziehungsweise
ab `SUMME` gehört zum Zahlungsteil und wird nicht mehr angesehen. Das ist
nicht nur Ordnung, sondern die STRUKTURELLE Hälfte der Datenschutzzusage aus
dem Ticket: die maskierte Kartennummer, VU-Nummer, Terminal-ID und Trace-Nummer
stehen sämtlich hinter dieser Grenze, und was hinter ihr steht, wird gar nicht
erst zu einem Posten. Die andere Hälfte ist das Schema — `receipt_item` hat
keine Spalte, in die so etwas passte (siehe `bons.kaeufe`).

**3. Pfand, Rabatt und Leergut sind keine Artikel.** Sie tragen dieselbe
Zeilenform samt Steuerklasse und lassen sich nur am Namen erkennen. Die Liste
unten ist deshalb die Pflegestelle: nennt ein Laden seinen Rabatt anders,
rutscht er durch. Das ist der Preis dafür, ohne Modell auszukommen — und weil
die Zuordnung danach ohnehin durch das Modell geht (`bons.zuordnung`), das
Nicht-Artikel ein zweites Mal erkennt, ist eine Lücke hier keine falsche
Kaufhistorie, sondern eine Zeile, die niemand bestätigt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from zettel import db

#: Die Läden, die dieses Modul auseinanderhält. `unbekannt` ist kein
#: Fehlerfall: ein Bon ohne erkennbaren Laden ist immer noch ein Bon mit
#: Preisen, und ein geratener Laden wäre schlimmer als ein leeres Feld.
REWE, LIDL, UNBEKANNT = "rewe", "lidl", "unbekannt"

#: Wie die Läden auf der Seite heissen. Eigenes Verzeichnis und nicht
#: `orders.LADEN_TITEL`: dort heisst der dritte Eintrag „Egal wo" und ist ein
#: Wunsch. Ein Bon ohne erkennbaren Laden ist kein Wunsch, sondern eine Lücke.
LADEN_TITEL = {REWE: "Rewe", LIDL: "Lidl", UNBEKANNT: "Laden unbekannt"}

#: Wonach im Text gesucht wird. Als ganzes Wort, damit „Lidl" in einer
#: Strassenadresse nicht denselben Rang hat wie die Kopfzeile — und
#: case-insensitiv, weil der eBon GROSS schreibt und der Screenshot gemischt.
_LADEN_MUSTER = ((REWE, re.compile(r"\bREWE\b", re.IGNORECASE)),
                 (LIDL, re.compile(r"\bLIDL\b", re.IGNORECASE)))

#: Die Artikelzeile. Zwei Leerzeichen mindestens zwischen Name und Preis: der
#: Preis steht rechtsbündig, also klafft dort immer eine Lücke — und ein
#: Produktname mit EINEM Leerzeichen darin („SALITOS ORIGINAL") bleibt so ein
#: Name und wird nicht am Leerzeichen zerschnitten.
#:
#: Der Stern am Ende ist Rewes Fussnotenzeichen („keine Rabatte auf …"). Er
#: gehört nicht zum Preis und nicht zum Namen.
ARTIKEL = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?P<preis>-?\d{1,4},\d{2})\s+(?P<steuer>[A-Z])"
    r"\s*\*?\s*$")

#: Die Mengenzeile darunter: `2 Stk x 2,19`, `2 x 1,19`, `3 St. x 0,99`.
MENGE = re.compile(
    r"^\s*(?P<menge>\d{1,3})\s*(?:Stk\.?|St\.?|Stück)?\s*[xX*]\s*"
    r"(?P<einzel>\d{1,4},\d{2})\s*(?:EUR)?\s*$")

#: Die Gewichtszeile: `0,532 kg x 9,99 EUR/kg`. Sie ist KEINE Stückzahl — die
#: 9,99 sind ein Kilopreis und wären als Einzelpreis eine Lüge. Festgehalten
#: wird sie trotzdem, als Text an der Zeile: sie erklärt, warum eine Packung
#: Weintrauben 4,17 gekostet hat.
GEWICHT = re.compile(
    r"^\s*(?P<menge>\d{1,3}(?:,\d{1,3})?)\s*(?P<einheit>kg|g|l|ml)\s*[xX*]\s*"
    r"(?P<preis>\d{1,4},\d{2})\s*(?:EUR)?\s*(?:/\s*(?:kg|g|l|ml))?\s*$")

#: Wo der Artikelteil endet. Die erste Zeile, die auf eines dieser Muster
#: passt, beendet ihn — alles danach ist Summe, Zahlung und Fuss.
#:
#: `Geg.` und `Kundenbeleg` stehen mit in der Liste, obwohl `SUMME` bei beiden
#: gemessenen Bons davor steht. Sie sind der Gürtel zum Hosenträger: ein Bon
#: ohne Summenzeile darf nicht dazu führen, dass der ganze Zahlungsteil
#: durchgereicht wird.
ENDE = (
    re.compile(r"^\s*[-=_]{6,}\s*$"),
    re.compile(r"^\s*(SUMME|GESAMT|GESAMTSUMME|ZU\s*ZAHLEN|TOTAL)\b",
               re.IGNORECASE),
    re.compile(r"^\s*(Geg\.|Gegeben|Bar\b|EC-Cash|Kartenzahlung)",
               re.IGNORECASE),
    re.compile(r"Kundenbeleg|Händlerbeleg", re.IGNORECASE),
)

#: Die Summenzeile selbst — dieselbe Grenze, hier zum Auslesen des Betrags.
SUMME = re.compile(
    r"^\s*(?:SUMME|GESAMT|GESAMTSUMME|ZU\s*ZAHLEN|TOTAL)\b[^\d-]*"
    r"(?P<preis>\d{1,4},\d{2})", re.IGNORECASE)

#: Datum des Einkaufs. Zwei- und vierstellige Jahre, weil beide vorkommen.
DATUM = re.compile(r"\b(?P<tag>\d{2})\.(?P<monat>\d{2})\.(?P<jahr>\d{4}|\d{2})\b")

#: Wortstämme, die eine Zeile trotz Artikelform zu einem Nicht-Artikel machen.
#: Verglichen wird gegen den WORTANFANG (siehe `_ist_nicht_artikel`) und nicht
#: als Teilstring: „BON" als Teilstring träfe „BONBONS", und eine Tüte Bonbons
#: ist ein Einkauf.
#:
#: Umlaute sind hier schon aufgelöst (`db.normalisiere`), damit „RUECKGABE" vom
#: eBon und „Rückgabe" vom Screenshot dieselbe Zeile treffen.
NICHT_ARTIKEL = (
    "PFAND", "LEERGUT", "RUECKGABE", "RUECKNAHME",
    "RABATT", "NACHLASS", "COUPON", "GUTSCHEIN", "AKTION", "PREISVORTEIL",
    "TREUE", "PAYBACK", "BONUS", "PUNKTE",
    "SUMME", "ZWISCHENSUMME", "GESAMT", "POSTEN", "TRINKGELD",
)


class BonFormatFehler(ValueError):
    """Aus diesem Text wird kein Bon — und der Satz sagt, woran es liegt.

    Kein Absturz und kein leeres Ergebnis: eine Datei, die keine Artikelzeilen
    hergibt, ist entweder kein Kassenbon oder in einem Format, das dieses
    Modul nicht kennt. Beides muss die Nutzerin lesen können, sonst sieht sie
    nur, dass nichts passiert ist.
    """


@dataclass(frozen=True)
class Posten:
    """Eine Artikelzeile: was gekauft wurde und was es gekostet hat.

    `text` ist der Name, WIE ER AUF DEM BON STEHT — abgekürzt, ohne Umlaute,
    in Grossbuchstaben. Er wird nicht schöngeschrieben: er ist die
    Ausgangslage für die Zuordnung und muss später noch einmal nachvollziehbar
    sein, wenn der Katalog gewachsen ist und ein zweiter Versuch mehr findet.

    `gesamt_cents` ist der Betrag, der auf dem Bon steht, also der tatsächlich
    bezahlte Zeilenbetrag. `einzel_cents` steht nur da, wo der Bon ihn nennt
    (Mengenzeile). Nichts wird gerechnet: `gesamt / menge` wäre eine
    Behauptung über Rundung, die der Bon nicht deckt.
    """
    text: str
    gesamt_cents: int
    menge: int = 1
    einzel_cents: int | None = None
    mengentext: str | None = None
    zeile: int = 0


@dataclass(frozen=True)
class Bon:
    """Was auf einem Kassenbon steht — und NUR das.

    Diese Klasse hat kein Feld für eine Kartennummer, keins für eine
    Terminal-ID und keins für eine Belegnummer. Das ist Absicht und die erste
    von zwei Verteidigungslinien: was hier nicht hineinpasst, kann auch nicht
    weitergereicht werden. Die zweite ist das Schema in `bons.kaeufe`.
    """
    laden: str
    datum: str | None
    posten: list[Posten] = field(default_factory=list)
    summe_cents: int | None = None

    @property
    def summe_posten_cents(self) -> int:
        """Was die erkannten Posten zusammen ergeben.

        Nicht dasselbe wie `summe_cents`: das Pfand fehlt hier bewusst, und
        genau die Differenz ist die nützliche Auskunft. Weicht sie um mehr ab,
        als Pfand und Rabatte erklären, hat die Zerlegung etwas übersehen —
        die Oberfläche zeigt beides nebeneinander, statt eine Zahl zu
        behaupten.
        """
        return sum(p.gesamt_cents for p in self.posten)


def cents(text: str) -> int:
    """`"4,38"` -> `438`. Auch mit Punkt als Trenner und mit Minus.

    Über `Decimal` liefe es genauer, kostet aber einen Import für eine Zahl
    mit zwei Nachkommastellen; hier wird ganzzahlig gerechnet, damit gar keine
    Fliesskommazahl entsteht.
    """
    sauber = text.strip().replace(".", ",")
    negativ = sauber.startswith("-")
    sauber = sauber.lstrip("+-")
    euro, _, rest = sauber.partition(",")
    rest = (rest + "00")[:2]
    wert = int(euro or 0) * 100 + int(rest)
    return -wert if negativ else wert


def erkenne_laden(text: str) -> str:
    """`rewe`, `lidl` oder `unbekannt`. Rät nie."""
    for name, muster in _LADEN_MUSTER:
        if muster.search(text):
            return name
    return UNBEKANNT


def erkenne_datum(text: str) -> str | None:
    """Das Einkaufsdatum als ISO-Zeichenkette, oder `None`.

    Genommen wird das ERSTE vollständige Datum im Text. Auf beiden gemessenen
    Bons ist das das Einkaufsdatum; die Zeitstempel der TSE weiter unten
    stehen ohnehin schon in ISO und werden von diesem Muster nicht getroffen.
    Ein zweistelliges Jahr wird als 20xx gelesen — ein Kassenbon von 1998 ist
    kein Fall, der hier auftritt.
    """
    treffer = DATUM.search(text or "")
    if not treffer:
        return None
    jahr = treffer.group("jahr")
    if len(jahr) == 2:
        jahr = f"20{jahr}"
    tag, monat = treffer.group("tag"), treffer.group("monat")
    if not (1 <= int(monat) <= 12 and 1 <= int(tag) <= 31):
        return None
    return f"{jahr}-{monat}-{tag}"


def _ist_nicht_artikel(name: str) -> bool:
    """Pfand, Rabatt, Leergut und Verwandtes — trotz Artikelform.

    Verglichen wird Wort für Wort und über den Wortanfang: „PFANDRUECKGABE"
    trifft über „PFAND", „BONBONS" trifft über nichts. Ziffern und
    Satzzeichen fallen vorher weg, damit „PFAND 0,25 EURO" auf sein erstes
    Wort zusammenschnurrt.
    """
    gefaltet = db.normalisiere(name).upper()
    for wort in re.findall(r"[A-Z]+", gefaltet):
        for stamm in NICHT_ARTIKEL:
            if wort.startswith(stamm):
                return True
    return False


def artikelzeilen(text: str) -> list[str]:
    """Die Zeilen bis zur Summe. Alles danach ist Zahlungsteil.

    Eigene Funktion, weil genau hier die Datenschutzgrenze liegt und sie
    einzeln prüfbar sein soll (siehe `tests/test_bons_lesen.py`).
    """
    zeilen = (text or "").replace("\f", "\n").splitlines()
    for i, zeile in enumerate(zeilen):
        if any(m.search(zeile) for m in ENDE):
            return zeilen[:i]
    return zeilen


def zerlege(text: str) -> Bon:
    """Bon-Text -> `Bon`. Wirft `BonFormatFehler`, wenn nichts zu erkennen ist.

    Der Ablauf in einem Satz: Artikelteil abschneiden, jede Zeile auf die
    Artikelform prüfen, Mengen- und Gewichtszeilen der Zeile DARÜBER
    zuschlagen, Nicht-Artikel wegwerfen.

    Die Summe und das Datum werden aus dem GANZEN Text gelesen und nicht nur
    aus dem Artikelteil — beides steht per Definition dahinter. Es sind auch
    die einzigen zwei Dinge, die von hinter der Grenze geholt werden, und
    keines davon ist ein Zahlungsdatum.
    """
    roh = text or ""
    posten: list[Posten] = []
    letzter: int | None = None       # Index des Postens, an den eine
    #                                  Mengenzeile noch andocken darf

    for nr, zeile in enumerate(artikelzeilen(roh), start=1):
        if not zeile.strip():
            continue

        menge = MENGE.match(zeile)
        if menge and letzter is not None:
            # Die Zeile gehört nach OBEN. `letzter` wird danach gelöscht:
            # eine zweite Mengenzeile hintereinander gehört nicht demselben
            # Posten, sondern ist etwas, das dieses Modul nicht kennt.
            alt = posten[letzter]
            posten[letzter] = Posten(
                text=alt.text, gesamt_cents=alt.gesamt_cents,
                menge=max(1, int(menge.group("menge"))),
                einzel_cents=cents(menge.group("einzel")),
                mengentext=" ".join(zeile.split()), zeile=alt.zeile)
            letzter = None
            continue

        gewicht = GEWICHT.match(zeile)
        if gewicht and letzter is not None:
            # Menge bleibt 1 und Einzelpreis bleibt leer: 9,99 ist ein
            # Kilopreis. Der Text bleibt trotzdem stehen, er erklärt den
            # Betrag.
            alt = posten[letzter]
            posten[letzter] = Posten(
                text=alt.text, gesamt_cents=alt.gesamt_cents, menge=1,
                einzel_cents=None, mengentext=" ".join(zeile.split()),
                zeile=alt.zeile)
            letzter = None
            continue

        artikel = ARTIKEL.match(zeile)
        if artikel is None:
            # Kopfzeilen, Adresse, Leerzeilen. Sie beenden nur die
            # Andockmöglichkeit nicht — die Mengenzeile steht auf beiden
            # gemessenen Bons unmittelbar unter ihrem Artikel.
            continue

        name = " ".join(artikel.group("name").split())
        betrag = cents(artikel.group("preis"))
        if _ist_nicht_artikel(name) or betrag <= 0:
            # Ein Rabatt steht als negativer Betrag da und ist auch dann kein
            # Artikel, wenn er anders heisst, als die Liste erwartet. Eine
            # Zeile über 0,00 ist eine Zugabe und kein Kauf.
            letzter = None
            continue

        posten.append(Posten(text=name, gesamt_cents=betrag, zeile=nr))
        letzter = len(posten) - 1

    if not posten:
        raise BonFormatFehler(
            "In dieser Datei steht kein Kassenbon, den ich lesen kann. "
            "Erwartet werden Zeilen wie „ANTIPASTI VARIAT   5,69 B“ — Name, "
            "Preis, Steuerklasse. Gelesen wurden "
            f"{len(roh.splitlines())} Zeilen, keine davon sah so aus.")

    return Bon(laden=erkenne_laden(roh), datum=erkenne_datum(roh),
               posten=posten, summe_cents=_summe(roh))


def _summe(text: str) -> int | None:
    for zeile in (text or "").splitlines():
        treffer = SUMME.match(zeile)
        if treffer:
            return cents(treffer.group("preis"))
    return None
