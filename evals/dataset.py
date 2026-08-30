"""Das Dataset `zettel-anfragen` (Spec 8.2).

Feste Eingaben mit erwartetem Ergebnis, vier Sorten, je drei Beispiele. Der
ganze Wert dieses Moduls hängt an einer einzigen Entscheidung:

**Erwartet wird auf Kategorieebene, nie auf Produkt-ID.**

Ein Dataset, das für „passierte Tomaten" die Produkt-ID 64 verlangt, ist beim
nächsten Katalog-Crawl ungültig — Knuspr vergibt die IDs, der Crawler
übernimmt sie, und ein ausgelistetes Produkt nimmt die Erwartung mit ins Grab.
Erwartet wird deshalb ein Kategoriepfad wie
`Konserven & Eingelegtes > Tomaten`. Der überlebt jeden Crawl, in dem es
diese Warengruppe überhaupt noch gibt — und wenn nicht, sagt
`pruefe_katalog()` das laut, statt dass die Eval still auf 0 fällt.

Zwei Zahlen, nicht eine
-----------------------

Die Trefferquote aus Spec 8.1 misst **Präzision**: hat jeder gelieferte
Vorschlag etwas Vernünftiges getroffen? Der teuerste Fehler dieses Agenten ist
aber ein anderer. Die Handprobe aus WB-327 gegen die echte Box lieferte für
„alles für Spaghetti Bolognese" in Stufe 1:

    Spaghetti, Hackfleisch, passierte Tomaten, Klopapier

**Zwiebeln und Knoblauch fehlten komplett.** Jeder gelieferte Begriff fand ein
Produkt — die Präzision war makellos. Die Sauce wird trotzdem nichts, und
gemerkt hätte man es erst im Laden. Keine Metrik zeigte das; nur Lesen zeigte
es.

Deshalb trägt jedes Beispiel eine **erwartete Zutatenmenge** (`zutaten`) und
nicht bloss eine erwartete Kategorie. Daraus entstehen in
`evals/experiment.py` zwei Scores nebeneinander:

* `zutaten_vollstaendigkeit` — Recall: wie viele der erwarteten Pflichtzutaten
  hat der Agent überhaupt geliefert? Der Bolognese-Lauf oben bekäme 3/5.
* `kategorie_praezision` — Präzision: liegen die gelieferten Produkte in der
  erwarteten Kategorie?

Felder eines Beispiels
----------------------

`input`  — `{"satz": …}`, die Anfrage der Nutzerin.

`output` (das Erwartete):
  `zutaten`          Liste von Zutaten (siehe `zutat()`).
  `zusatz_ok`        Darf der Agent mehr liefern als erwartet? Bei einem
                     Gericht ja (Parmesan zur Bolognese ist kein Fehler), bei
                     einer abgezählten Einkaufsliste nein.
  `kein_rezeptpfad`  Muss der Modellweg genommen werden? Für die Sorte
                     „gemischt" ja: „Klopapier und Spülmittel" ist kein
                     Rezept, und eine Antwort aus der Rezeptsammlung wäre
                     falsch, egal wie gut die Produkte passen.
  `beschreibung`     Ein Satz Prosa für den LLM-Judge. Er sieht die
                     Kategorien nicht — er soll beurteilen, was eine
                     Kategorieprüfung nicht kann.

`metadata`:
  `schluessel`  Stabiler Name des Beispiels. Er ist der Schlüssel für die
                Idempotenz (siehe `plan_fuer`) und überlebt jede Änderung am
                Wortlaut.
  `sorte`       Eine der vier aus Spec 8.2.
  `ermessen`    Ist „richtig" hier Ermessenssache? Nur dann urteilt der
                LLM-Judge; sonst schweigt er, statt eine Zahl zu erfinden.
  `abdruck`     Prüfsumme über Eingabe und Erwartung. Damit erkennt
                `anlegen()`, ob ein vorhandenes Beispiel noch dasselbe ist.

Was hier bewusst NICHT drinsteht
--------------------------------

„Toastbrot" wäre ein hübsches drittes Frühstücksbeispiel und findet im echten
Katalog **null** Produkte — die Suche kennt nur Wortanfänge (WB-322), und der
Toast heisst „Harry Butter Toast". Ein Beispiel, das daran scheitert, misst
die Suche und nicht den Agenten; es ist deshalb draussen. „Klopapier" bleibt
drin, obwohl es genauso null Treffer hat: dort ist das Umformulieren in
„Toilettenpapier" ausdrücklich die Aufgabe des Modells (Stufe 1 sieht den
Katalog nie), und die Spec nennt genau dieses Beispiel. Der Unterschied ist
die Absicht — einmal wird eine Modellfähigkeit geprüft, einmal eine
Katalog-Lücke.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Damit `python evals/dataset.py` genauso läuft wie `python -m evals.dataset`.
# Beim Import als Paketmodul ist das Wurzelverzeichnis längst drin und die
# Zeile ein No-Op; beim direkten Aufruf legt Python nur `evals/` auf den Pfad
# und `import zettel` scheiterte sonst.
_WURZEL = str(Path(__file__).resolve().parent.parent)
if _WURZEL not in sys.path:
    sys.path.insert(0, _WURZEL)

#: Der Name in Phoenix. Steht an genau einer Stelle, weil `experiment.py` ihn
#: zum Nachladen braucht und zwei Schreibweisen ein stiller zweiter Datensatz
#: wären.
#:
#: Hiess bis zum 30.08.2026 `picknick-anfragen` (WB-401). In Phoenix ist der
#: Name der Schlüssel: der erste Lauf unter dem neuen Namen legt ein NEUES
#: Dataset an, das alte bleibt samt seiner Experimente daneben stehen. Ein
#: Rückfall auf den alten Namen wäre hier falsch — er würde zwei Datasets
#: verschmelzen, die getrennt gemessen wurden (siehe EVALS.md).
NAME = "zettel-anfragen"

BESCHREIBUNG = (
    "Feste Einkaufsanfragen mit Erwartung auf Kategorieebene (Spec 8.2). "
    "Vier Sorten: gewöhnlich, mehrdeutig, gemischt, Falle. Bewertet werden "
    "Vollständigkeit (Recall über die erwarteten Zutaten) und Präzision "
    "(liegt das gewählte Produkt in der erwarteten Kategorie)."
)

#: Trennzeichen im Kategoriepfad. Knuspr liefert drei Ebenen; hier stehen sie
#: als ein lesbarer String, weil der so auch in Phoenix brauchbar ist.
TRENNER = " > "

GEWOEHNLICH = "gewoehnlich"
MEHRDEUTIG = "mehrdeutig"
GEMISCHT = "gemischt"
FALLE = "falle"

SORTEN = (GEWOEHNLICH, MEHRDEUTIG, GEMISCHT, FALLE)


# --------------------------------------------------------------------------
# Kategoriepfade

def pfad(produkt: dict) -> str:
    """Die drei Kategoriespalten eines Produkts als ein Pfad.

    Leere Ebenen fallen weg statt als leeres Segment stehen zu bleiben — ein
    Pfad `"Süßigkeiten >  > "` liesse jeden Präfixvergleich scheitern, und im
    echten Katalog haben ganze Warengruppen keine dritte Ebene.
    """
    teile = [str(produkt.get(s) or "").strip()
             for s in ("category_l1", "category_l2", "category_l3")]
    return TRENNER.join(t for t in teile if t)


def passt_kategorie(produkt_pfad: str, erwartet: str) -> bool:
    """Liegt `produkt_pfad` in (oder unter) der erwarteten Kategorie?

    Verglichen wird als Präfix **auf Segmentgrenzen**, nicht als Zeichenkette.
    Der Unterschied ist nicht theoretisch: `"Milch"` ist ein Präfix von
    `"Milch, Molkerei & Butter"`, und ein blosses `startswith` würde jedes
    Molkereiprodukt als Treffer der Kategorie „Milch" zählen. Umgekehrt darf
    eine Erwartung `"Käse"` alles unter `Käse > Weichkäse > …` einschliessen —
    genau dafür ist die Kategorieebene da: sie überlebt einen Crawl, der eine
    neue dritte Ebene einführt.
    """
    a = [t for t in (produkt_pfad or "").split(TRENNER) if t]
    b = [t for t in (erwartet or "").split(TRENNER) if t]
    if not b or len(b) > len(a):
        return False
    return a[:len(b)] == b


def irgendeine(produkt_pfad: str, erwartete: list[str]) -> bool:
    """Passt der Pfad auf mindestens eine der erwarteten Kategorien?"""
    return any(passt_kategorie(produkt_pfad, e) for e in erwartete)


# --------------------------------------------------------------------------
# Zutaten

def normbegriff(text: str) -> str:
    """Einen Suchbegriff auf die Form bringen, in der verglichen wird.

    Dieselbe Umlautfaltung wie die Katalogsuche (`db.normalisiere`), damit
    „Spülmittel" und „Spuelmittel" derselbe Begriff sind. Der Import steht in
    der Funktion, weil `zettel.db` das sqlite-Schema mitbringt und dieses
    Modul auch ohne Datenbank benutzbar bleiben soll.
    """
    from zettel import db

    return " ".join(db.normalisiere(text or "").casefold().split())


def zutat(name: str, kategorien: list[str], begriffe: list[str] = (),
          *, pflicht: bool = True, proben: list[str] = ()) -> dict:
    """Eine erwartete Zutat.

    `kategorien`  Kategoriepfade, von denen einer passen muss. Mehrere sind
                  die Regel und kein Notbehelf: „etwas Süßes" ist in acht
                  Warengruppen richtig beantwortet.
    `begriffe`    Suchbegriffe, an denen ein Vorschlag DIESER Zutat zugeordnet
                  wird. Sie sind die Bindung zwischen dem, was das Modell
                  gesucht hat, und dem, was erwartet wurde — ohne sie liesse
                  sich ein Fehlgriff („Spaghetti" -> Spaghettilöffel) nicht
                  von einer sinnvollen Zugabe („Parmesan") unterscheiden.
                  Leer heisst: diese Zutat bindet keinen Begriff, es zählt
                  allein die Kategorie (so bei den Fallen, wo es keinen
                  vorhersagbaren Begriff gibt).
    `pflicht`     Geht die Zutat in den Recall ein? Nur Pflichtzutaten tun
                  das; alles andere wäre eine Erwartung, die niemand
                  formuliert hat.
    `proben`      Begriffe, mit denen `pruefe_katalog()` die Erreichbarkeit
                  nachsieht. Nur dort gebraucht und nur dort nötig: bei einer
                  Falle heisst die Zutat „etwas Süßes", und danach würde
                  niemand suchen. Ohne Angabe werden `begriffe` genommen,
                  sonst der Name.
    """
    return {"name": name, "kategorien": list(kategorien),
            "begriffe": [normbegriff(b) for b in begriffe],
            "proben": list(proben),
            "pflicht": bool(pflicht)}


def zutat_zu_begriff(zutaten: list[dict], begriff: str) -> dict | None:
    """Welche erwartete Zutat ist mit diesem Suchbegriff gemeint?

    Verglichen wird beidseitig als Teilzeichenkette: „Rinderhackfleisch"
    trifft `hack`, „Zwiebeln" trifft `zwiebel`. Gewinnt die **längste**
    Übereinstimmung — sonst zöge bei „passierte Tomaten" die kurze Zutat
    „Tomaten" den Begriff an sich, und die Bewertung stünde auf der falschen
    Kategorie.

    Gibt `None`, wenn kein Begriff bindet. Das ist der Normalfall für alles,
    was der Agent über die Erwartung hinaus liefert.
    """
    norm = normbegriff(begriff)
    if not norm:
        return None
    beste, laenge = None, 0
    for z in zutaten:
        for b in z["begriffe"]:
            if not b or not (b in norm or norm in b):
                continue
            if len(b) > laenge:
                beste, laenge = z, len(b)
    return beste


# --------------------------------------------------------------------------
# Die Beispiele
#
# Jede Kategorie unten steht so im echten Katalog (2498 aktive Produkte,
# Stand 2026-08-28) — nachgeprüft mit `pruefe_katalog()`, das genau dafür da
# ist. Erfundene Kategorien wären der schnellste Weg zu einer Eval, die
# immer 0 misst und dabei überzeugend aussieht.

_PASTA = "Reis, Pasta & Getreide > Pasta"
#: Knuspr führt die Marks-&-Spencer-Grundnahrungsmittel in einem eigenen
#: Ast statt im Pasta-Baum: `Marks & Spencer Spaghetti` (500 g Hartweizen,
#: Produkt 644) liegt in `Pasta, Reis, Gemüse`. Im ersten Lauf am 2026-08-28
#: hat der Agent genau dieses Produkt gewählt und dafür einen Punkt verloren
#: — und das wäre eine Eval gewesen, die die KATEGORISIERUNG des Katalogs
#: misst und nicht den Agenten. Der Ast enthält sieben Produkte, alle
#: Trockenware; er gilt deshalb mit.
#: Ausdrücklich NICHT mit gilt `Fertiggerichte` — dort liegt zwar ein zweites
#: „Marks & Spencer Spaghetti" (Produkt 643, ebenfalls Trockenware und dort
#: schlicht falsch einsortiert), aber eben auch „Spaghetti-Loops in
#: Tomatensoße". Eine Erwartung, die Fertiggerichte durchlässt, könnte eine
#: Fünf-Minuten-Terrine nicht mehr von Nudeln unterscheiden.
_PASTA_MS = "Pasta, Reis, Gemüse"
_HACK = "Hackfleisch & Burgerpatties"
_PASSATA = "Konserven & Eingelegtes > Tomaten"
_TOMATE_FRISCH = "Gemüse > Tomaten"
_ZWIEBELN = "Gemüse > Zwiebeln & Knoblauch"
_MILCH = "Milch, Molkerei & Butter > Milch"
_BUTTER = "Milch, Molkerei & Butter > Butter & Fette"
_KAESE = "Käse"
_FRISCHKAESE = "Käse > Frischkäse, Hüttenkäse & Ricotta"
_JOGHURT = "Joghurt & Desserts"
_KLOPAPIER = "Papier- & Hygieneartikel > Toilettenpapier"
_SPUELI = "Putzen & Reinigen > Geschirrreiniger"

#: „Etwas Süßes" ist in mehreren Warengruppen richtig beantwortet. Alle acht
#: stehen im echten Katalog; die Falle ist nicht, die richtige zu treffen,
#: sondern überhaupt etwas zu liefern.
_SUESS = ["Schokolade", "Süßigkeiten", "Kekse", "Zuckriges & Co",
          "Süße Snacks & Donuts", "Eis", "Backen & Dessert",
          "Kuchen & Konditorei", "Joghurt & Desserts > Pudding & Milchreis"]

_FRUEHSTUECK = ["Joghurt & Desserts", "Müsli, Porridge & Cerealien",
                "Müslis, Cerealien, Porridge", "Haltbares Brot & Gebäck",
                "Backwaren & Feingebäck", "Brot", "Brötchen",
                "Marmeladen, Honig & Aufstriche",
                "Marmeladen, Honig, Aufstriche", "Milch, Molkerei & Butter",
                "Käse", "Aufschnitt", "Kaffee", "Süße Snacks & Donuts"]

_KNABBER = ["Chips & Dips", "Laugen- & Salzgebäck", "Popcorn",
            "Nüsse & Kerne", "Herzhafte Snacks", "Salziges",
            "Kekse > Salzige Waffeln"]


BEISPIELE = [
    # -- gewöhnlich --------------------------------------------------------
    {
        "schluessel": "gewoehnlich-bolognese",
        "sorte": GEWOEHNLICH,
        "satz": "alles für Spaghetti Bolognese",
        "ermessen": False,
        "zusatz_ok": True,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Erwartet werden die Zutaten, die man für eine Bolognese kaufen "
            "muss: Nudeln, Hackfleisch, passierte Tomaten, Zwiebeln und "
            "Knoblauch. Gewürze und Öl gehören nicht dazu, die stehen im "
            "Haushalt. Zusätzliche sinnvolle Zutaten (Parmesan, Sellerie, "
            "Karotten, Rotwein) sind kein Fehler; eine fehlende Grundzutat "
            "ist einer."),
        "zutaten": [
            zutat("Nudeln", [_PASTA, _PASTA_MS],
                  ["nudel", "spaghetti", "pasta", "penne", "tagliatelle",
                   "makkaroni"]),
            zutat("Hackfleisch", [_HACK],
                  ["hack", "gehacktes", "faschiertes"]),
            zutat("passierte Tomaten", [_PASSATA, _TOMATE_FRISCH],
                  ["passierte tomaten", "passata", "tomatenpassata",
                   "gehackte tomaten", "dosentomaten", "tomatenmark",
                   "tomaten"]),
            zutat("Zwiebeln", [_ZWIEBELN], ["zwiebel"]),
            zutat("Knoblauch", [_ZWIEBELN], ["knoblauch"]),
        ],
    },
    {
        "schluessel": "gewoehnlich-fruehstueck",
        "sorte": GEWOEHNLICH,
        "satz": "Butter, Frischkäse und Naturjoghurt fürs Frühstück",
        "ermessen": False,
        # Eine abgezählte Liste: was darüber hinaus im Korb landet, hat
        # niemand bestellt.
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Drei genannte Artikel, nicht mehr und nicht weniger: Butter, "
            "Frischkäse und ein Naturjoghurt. Ein Fruchtjoghurt statt "
            "Naturjoghurt ist ein Fehlgriff, Margarine statt Butter auch."),
        "zutaten": [
            zutat("Butter", [_BUTTER], ["butter"]),
            zutat("Frischkäse", [_FRISCHKAESE, _KAESE], ["frischkaese"]),
            zutat("Naturjoghurt", [_JOGHURT], ["joghurt", "naturjoghurt"]),
        ],
    },
    {
        "schluessel": "gewoehnlich-tomatensauce",
        "sorte": GEWOEHNLICH,
        "satz": "ich koche eine Tomatensauce selbst — was muss ich kaufen?",
        "ermessen": False,
        "zusatz_ok": True,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Eine selbst gekochte Tomatensauce braucht Tomaten (passiert "
            "oder frisch), Zwiebeln und Knoblauch. Eine fertige Sauce aus "
            "dem Glas verfehlt die Anfrage — es wurde ausdrücklich nach "
            "selbst kochen gefragt."),
        "zutaten": [
            zutat("Tomaten", [_PASSATA, _TOMATE_FRISCH],
                  ["passierte tomaten", "passata", "tomaten", "tomatenmark",
                   "dosentomaten"]),
            zutat("Zwiebeln", [_ZWIEBELN], ["zwiebel"]),
            zutat("Knoblauch", [_ZWIEBELN], ["knoblauch"]),
        ],
    },

    # -- mehrdeutig --------------------------------------------------------
    {
        "schluessel": "mehrdeutig-milch",
        "sorte": MEHRDEUTIG,
        "satz": "Milch",
        # Welche Milch gemeint ist, steht nicht im Satz. Genau dafür ist der
        # LLM-Judge da: die Kategorie ist getroffen, die Wahl trotzdem
        # diskutabel.
        "ermessen": True,
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Irgendein Produkt aus der Warengruppe Milch. Eine gewöhnliche "
            "Trink- oder H-Milch trifft die Anfrage; eine Schoko- oder "
            "Erdbeermilch ist zwar in derselben Warengruppe, aber nicht das, "
            "was jemand meint, der nur „Milch“ schreibt."),
        "zutaten": [zutat("Milch", [_MILCH], ["milch", "vollmilch",
                                              "frischmilch", "h-milch"])],
    },
    {
        "schluessel": "mehrdeutig-kaese-nudeln",
        "sorte": MEHRDEUTIG,
        "satz": "Käse für die Nudeln",
        "ermessen": True,
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Käse, der über Nudeln passt — gerieben oder ein Hartkäse. Die "
            "Warengruppe Käse allein genügt nicht: ein Weichkäse oder "
            "Blauschimmelkäse ist zwar Käse, aber nicht das, was hier "
            "gemeint ist."),
        "zutaten": [zutat("Käse", [_KAESE],
                          ["kaese", "reibekaese", "parmesan", "hartkaese"])],
    },
    {
        "schluessel": "mehrdeutig-joghurt",
        "sorte": MEHRDEUTIG,
        "satz": "Joghurt",
        "ermessen": True,
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Irgendein Joghurt. Natur oder Frucht ist beides vertretbar, "
            "ein Pudding oder Milchreis nicht."),
        "zutaten": [zutat("Joghurt", [_JOGHURT], ["joghurt"])],
    },

    # -- gemischt ----------------------------------------------------------
    {
        "schluessel": "gemischt-klopapier-spueli",
        "sorte": GEMISCHT,
        "satz": "Klopapier und Spülmittel",
        "ermessen": False,
        "zusatz_ok": False,
        # Kein Rezept weit und breit — wenn hier der Rezeptweg anspringt, ist
        # die Erkennung zu gierig, und das wäre ein Fehler, den keine
        # Kategorieprüfung sieht.
        "kein_rezeptpfad": True,
        "beschreibung": (
            "Zwei Non-Food-Artikel: Toilettenpapier und ein Handspülmittel. "
            "Kein Rezept, keine Lebensmittel. „Klopapier“ steht so in keinem "
            "Katalog — das Umformulieren in „Toilettenpapier“ ist Teil der "
            "Aufgabe."),
        "zutaten": [
            zutat("Toilettenpapier", [_KLOPAPIER],
                  ["klopapier", "toilettenpapier", "wc-papier"]),
            zutat("Spülmittel", [_SPUELI],
                  ["spuelmittel", "geschirrspuelmittel", "handspuelmittel"]),
        ],
    },
    {
        "schluessel": "gemischt-haushalt-und-butter",
        "sorte": GEMISCHT,
        "satz": "Toilettenpapier, Spülmittel und Butter",
        "ermessen": False,
        "zusatz_ok": False,
        "kein_rezeptpfad": True,
        "beschreibung": (
            "Zwei Non-Food-Artikel und ein Lebensmittel, alle drei wörtlich "
            "genannt. Kein Rezept."),
        "zutaten": [
            zutat("Toilettenpapier", [_KLOPAPIER],
                  ["klopapier", "toilettenpapier"]),
            zutat("Spülmittel", [_SPUELI],
                  ["spuelmittel", "geschirrspuelmittel", "handspuelmittel"]),
            zutat("Butter", [_BUTTER], ["butter"]),
        ],
    },
    {
        "schluessel": "gemischt-umgangssprachlich",
        "sorte": GEMISCHT,
        "satz": "wir haben kein Klopapier mehr und die Milch ist alle",
        "ermessen": False,
        "zusatz_ok": False,
        "kein_rezeptpfad": True,
        "beschreibung": (
            "Ein umgangssprachlicher Satz ohne Einkaufsliste: gemeint sind "
            "Toilettenpapier und Milch. Kein Rezept, und nichts darüber "
            "hinaus."),
        "zutaten": [
            zutat("Toilettenpapier", [_KLOPAPIER],
                  ["klopapier", "toilettenpapier"]),
            zutat("Milch", [_MILCH], ["milch"]),
        ],
    },

    # -- Falle -------------------------------------------------------------
    # Kein vorhersagbarer Suchbegriff, deshalb `begriffe=[]`: hier bindet
    # nichts an einen Begriff, es zählt allein, ob überhaupt etwas Passendes
    # kommt. `zusatz_ok=False` macht daraus eine echte Prüfung — was ausserhalb
    # der erlaubten Warengruppen landet, ist ein Fehlgriff.
    {
        "schluessel": "falle-etwas-suesses",
        "sorte": FALLE,
        "satz": "etwas Süßes",
        "ermessen": True,
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Eine vage Anfrage, die trotzdem beantwortet werden muss: "
            "irgendetwas Süsses — Schokolade, Kekse, Fruchtgummi, Eis. Ein "
            "leeres Ergebnis ist die falsche Antwort. Herzhaftes ist es "
            "auch."),
        "zutaten": [zutat("etwas Süßes", _SUESS,
                          proben=["Schokolade", "Kekse", "Süßigkeiten",
                                  "Eis", "Fruchtgummi"])],
    },
    {
        "schluessel": "falle-fruehstueck",
        "sorte": FALLE,
        "satz": "irgendwas zum Frühstück",
        "ermessen": True,
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Vage, aber beantwortbar: irgendetwas, das man frühstückt — "
            "Brot, Aufstrich, Joghurt, Müsli, Käse, Milch, Kaffee. Ein "
            "leeres Ergebnis ist die falsche Antwort."),
        "zutaten": [zutat("Frühstück", _FRUEHSTUECK,
                          proben=["Müsli", "Joghurt", "Brot", "Marmelade",
                                  "Kaffee"])],
    },
    {
        "schluessel": "falle-knabbern",
        "sorte": FALLE,
        "satz": "was zum Knabbern für den Filmabend",
        "ermessen": True,
        "zusatz_ok": False,
        "kein_rezeptpfad": False,
        "beschreibung": (
            "Vage: etwas Salziges zum Knabbern — Chips, Salzgebäck, "
            "Popcorn, Nüsse. Ein leeres Ergebnis ist die falsche Antwort. "
            "Süsses ist grenzwertig, aber keine grobe Verfehlung."),
        "zutaten": [zutat("Knabberzeug", _KNABBER,
                          proben=["Chips", "Salzstangen", "Nüsse",
                                  "Popcorn", "Cracker"])],
    },
]


# --------------------------------------------------------------------------
# Die Form, in der Phoenix die Beispiele bekommt

def eingabe(beispiel: dict) -> dict:
    return {"satz": beispiel["satz"]}


def erwartung(beispiel: dict) -> dict:
    return {"zutaten": beispiel["zutaten"],
            "zusatz_ok": beispiel["zusatz_ok"],
            "kein_rezeptpfad": beispiel["kein_rezeptpfad"],
            "beschreibung": beispiel["beschreibung"]}


def abdruck(beispiel: dict) -> str:
    """Prüfsumme über Eingabe und Erwartung.

    Damit erkennt `plan_fuer()` ein Beispiel, das zwar noch denselben
    Schlüssel trägt, inhaltlich aber ein anderes geworden ist — eine
    korrigierte Kategorie etwa. Ohne das bliebe die alte Fassung in Phoenix
    stehen, und Läufe von gestern und heute wären nicht mehr vergleichbar,
    ohne dass es jemandem auffällt.
    """
    roh = json.dumps([eingabe(beispiel), erwartung(beispiel)],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


def als_beispiele() -> list[dict]:
    """Alle Beispiele in der Form, die `create_dataset(examples=…)` erwartet."""
    raus = []
    for b in BEISPIELE:
        raus.append({
            "input": eingabe(b),
            "output": erwartung(b),
            "metadata": {"schluessel": b["schluessel"], "sorte": b["sorte"],
                         "ermessen": b["ermessen"], "abdruck": abdruck(b)},
        })
    return raus


def nach_schluessel() -> dict[str, dict]:
    return {b["schluessel"]: b for b in BEISPIELE}


# --------------------------------------------------------------------------
# Idempotenz — der rechnende Teil, ohne Phoenix

class DatasetFehler(RuntimeError):
    """Das Dataset in Phoenix passt nicht zu dem, was hier steht."""


def plan_fuer(vorhanden: list[dict] | None,
              gewuenscht: list[dict] | None = None) -> dict:
    """Was ist zu tun, damit Phoenix genau diese Beispiele führt?

    `vorhanden` sind die Beispiele, die Phoenix bereits führt (die Form von
    `Dataset.examples`), oder `None`, wenn es das Dataset noch nicht gibt.

    Gibt `{"aktion", "ergaenzen", "fehlend", "geaendert", "ueberzaehlig",
    "doppelt"}` zurück. Drei Ausgänge:

    * `"nichts"`     — Bestand und Wunsch sind deckungsgleich. **Das ist der
                       Normalfall beim zweiten Aufruf**, und es ist der ganze
                       Sinn dieser Funktion: zweimal anlegen darf nichts
                       verdoppeln.
    * `"ergaenzen"`  — es fehlen nur Beispiele, sonst stimmt alles. Dann geht
                       `add_examples_to_dataset` mit genau den fehlenden.
    * `"ersetzen"`   — irgendetwas ist geändert oder überzählig. Dann wird der
                       ganze Satz neu hochgeladen; Phoenix legt eine neue
                       Version an und die alte bleibt lesbar.

    Rein rechnend: kein Netz, kein Client, keine Phoenix-Typen. Genau deshalb
    ist die Idempotenz ohne laufendes Phoenix prüfbar.
    """
    gewuenscht = als_beispiele() if gewuenscht is None else gewuenscht
    will = {b["metadata"]["schluessel"]: b for b in gewuenscht}

    if vorhanden is None:
        return {"aktion": "ersetzen", "ergaenzen": gewuenscht,
                "fehlend": sorted(will), "geaendert": [], "ueberzaehlig": [],
                "doppelt": []}

    hat: dict[str, str] = {}
    doppelt: list[str] = []
    for b in vorhanden:
        meta = dict(b.get("metadata") or {})
        s = meta.get("schluessel")
        if not s:
            # Ein Beispiel ohne Schlüssel lässt sich nicht zuordnen. Es als
            # überzählig zu behandeln ist ehrlicher als es zu ignorieren:
            # irgendwer hat von Hand etwas hineingelegt.
            doppelt.append("(ohne Schlüssel)")
            continue
        if s in hat:
            doppelt.append(s)
            continue
        hat[s] = meta.get("abdruck")

    fehlend = sorted(k for k in will if k not in hat)
    ueberzaehlig = sorted(k for k in hat if k not in will)
    geaendert = sorted(k for k in will
                       if k in hat
                       and hat[k] != will[k]["metadata"]["abdruck"])

    if not (fehlend or ueberzaehlig or geaendert or doppelt):
        aktion = "nichts"
    elif fehlend and not (ueberzaehlig or geaendert or doppelt):
        aktion = "ergaenzen"
    else:
        aktion = "ersetzen"
    return {"aktion": aktion,
            "ergaenzen": [will[k] for k in fehlend],
            "fehlend": fehlend, "geaendert": geaendert,
            "ueberzaehlig": ueberzaehlig, "doppelt": doppelt}


# --------------------------------------------------------------------------
# Phoenix — ab hier wird ein laufender Server gebraucht

def klient(base_url: str | None = None):
    """Der Phoenix-Client. Die Naht, an der Tests etwas anderes einhängen.

    Der Import steht in der Funktion: `import evals.dataset` soll kein
    laufendes Phoenix und kein installiertes `phoenix.client` voraussetzen.
    """
    from phoenix.client import Client

    from zettel.obs import labels

    return Client(base_url=base_url or labels.basis_url())


def vorhandene(client, name: str = NAME) -> list[dict] | None:
    """Die Beispiele, die Phoenix unter diesem Namen führt, oder `None`.

    `None` heisst „gibt es nicht" und nicht „ist leer" — der Unterschied
    entscheidet in `plan_fuer()`, ob angelegt oder ersetzt wird.
    """
    try:
        ds = client.datasets.get_dataset(dataset=name)
    except Exception as e:  # noqa: BLE001
        # Der Client wirft je nach Stand einen HTTP-Fehler oder einen
        # ValueError. Ein 404 ist hier kein Fehler, sondern die Auskunft
        # „noch nicht angelegt"; alles andere soll auffallen.
        if _ist_unbekannt(e):
            return None
        raise
    return [dict(b) for b in ds.examples]


def _ist_unbekannt(fehler: Exception) -> bool:
    antwort = getattr(fehler, "response", None)
    if antwort is not None and getattr(antwort, "status_code", None) == 404:
        return True
    text = str(fehler).casefold()
    return "not found" in text or "404" in text


def anlegen(client=None, *, name: str = NAME) -> dict:
    """Legt `zettel-anfragen` an — und zwar idempotent.

    Zweimal aufgerufen entsteht kein zweites Dataset und kein doppeltes
    Beispiel: der zweite Lauf findet den Bestand deckungsgleich vor und tut
    nichts. Das ist nicht dem Server überlassen, sondern hier entschieden
    (`plan_fuer`), denn `create_dataset` fällt auf älteren Phoenix-Ständen
    still von `action="update"` auf `action="create"` zurück — und dann
    hätte man doppelte Beispiele, ohne dass irgendetwas gemeldet worden
    wäre.

    Gibt einen Bericht zurück; wirft `DatasetFehler`, wenn der Bestand
    hinterher nicht stimmt.
    """
    client = klient() if client is None else client
    gewuenscht = als_beispiele()
    bestand = vorhandene(client, name)
    plan = plan_fuer(bestand, gewuenscht)

    if plan["aktion"] == "nichts":
        return {**plan, "name": name, "anzahl": len(bestand),
                "satz": f"{name}: unverändert, {len(bestand)} Beispiele."}

    if plan["aktion"] == "ergaenzen":
        client.datasets.add_examples_to_dataset(
            dataset=name, examples=plan["ergaenzen"])
        getan = f"{len(plan['ergaenzen'])} Beispiel(e) ergänzt"
    else:
        client.datasets.create_dataset(
            name=name, examples=gewuenscht,
            dataset_description=BESCHREIBUNG)
        getan = ("angelegt" if bestand is None
                 else f"ersetzt ({len(gewuenscht)} Beispiele)")

    # Nachsehen statt glauben. Wenn der Server angehängt statt ersetzt hat,
    # steht das Dataset jetzt doppelt da — und genau das soll auffallen,
    # bevor jemand ein Experiment darauf fährt.
    nachher = vorhandene(client, name) or []
    nachplan = plan_fuer(nachher, gewuenscht)
    if nachplan["aktion"] != "nichts":
        raise DatasetFehler(
            f"{name} stimmt nach dem Schreiben nicht: "
            f"fehlend={nachplan['fehlend']}, "
            f"geändert={nachplan['geaendert']}, "
            f"überzählig={nachplan['ueberzaehlig']}, "
            f"doppelt={nachplan['doppelt']}. "
            "Vermutlich hat der Server angehängt statt ersetzt.")
    return {**plan, "name": name, "anzahl": len(nachher),
            "satz": f"{name}: {getan}, jetzt {len(nachher)} Beispiele."}


# --------------------------------------------------------------------------
# Erreichbarkeit — misst die Eval den Agenten oder den Katalog?

def pruefe_katalog(con, *, limit: int = 20) -> list[dict]:
    """Kann der echte Katalog jede erwartete Kategorie überhaupt liefern?

    Für jede Zutat wird mit ihren eigenen Begriffen gesucht und nachgesehen,
    ob unter den ersten `limit` Kandidaten einer in einer der erwarteten
    Kategorien liegt. Zutaten ohne Begriffe (die Fallen) werden mit ihrem
    Namen gesucht.

    Das ist die Gegenprobe zur teuersten Art, eine Eval kaputtzumachen: eine
    Erwartung, die der Katalog gar nicht erfüllen kann, misst nicht den
    Agenten, sondern eine Lücke — und sie sieht dabei aus wie ein
    Modellfehler. Was hier rot ist, gehört korrigiert oder aus dem Dataset
    heraus.
    """
    from zettel.catalog import search

    raus = []
    for b in BEISPIELE:
        for z in b["zutaten"]:
            begriffe = z.get("proben") or z["begriffe"] or [z["name"]]
            treffer = []
            for begriff in begriffe:
                for p in search.search(con, begriff, limit=limit):
                    if irgendeine(pfad(p), z["kategorien"]):
                        treffer.append((begriff, p))
                        break
            raus.append({
                "schluessel": b["schluessel"], "zutat": z["name"],
                "kategorien": z["kategorien"],
                "erreichbar": bool(treffer),
                # Welche Begriffe ins Leere laufen, ist die eigentliche
                # Auskunft: „Klopapier" tut das mit Absicht, alles andere
                # wäre ein Fund.
                "leere_begriffe": [g for g in begriffe
                                   if g not in [t[0] for t in treffer]],
                "beispielprodukt": (treffer[0][1]["name"] if treffer
                                    else None),
            })
    return raus


# --------------------------------------------------------------------------
# Kommandozeile

def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Legt das Phoenix-Dataset 'zettel-anfragen' an "
                    "(idempotent).")
    p.add_argument("--pruefen", action="store_true",
                   help="Nur nachsehen, ob der echte Katalog jede erwartete "
                        "Kategorie liefern kann. Braucht kein Phoenix.")
    p.add_argument("--zeigen", action="store_true",
                   help="Die Beispiele ausgeben, ohne etwas zu schreiben.")
    p.add_argument("--db", default=None, help="Katalogdatei für --pruefen.")
    args = p.parse_args(argv)

    if args.zeigen:
        for b in als_beispiele():
            print(json.dumps(b, ensure_ascii=False, indent=2))
        return 0

    if args.pruefen:
        from zettel import db

        con = db.connect(args.db or db.DEFAULT_DB)
        zeilen = pruefe_katalog(con)
        fehlend = [z for z in zeilen if not z["erreichbar"]]
        for z in zeilen:
            marke = "ok  " if z["erreichbar"] else "FEHLT"
            leer = (f"   (ohne Treffer: {', '.join(z['leere_begriffe'])})"
                    if z["leere_begriffe"] else "")
            print(f"{marke} {z['schluessel']:32s} {z['zutat']:18s} "
                  f"-> {z['beispielprodukt'] or '—'}{leer}")
        print(f"\n{len(zeilen) - len(fehlend)}/{len(zeilen)} erwartete "
              "Zutaten sind im echten Katalog erreichbar.")
        return 1 if fehlend else 0

    bericht = anlegen()
    print(bericht["satz"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
