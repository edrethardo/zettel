"""Die beiden Modellstufen des Agenten: `plan.extract` und `plan.choose`.

Die tragende Regel dieses Moduls (Spec 6): **das Modell erfindet niemals
Produkte.** Ein LLM, das Produkt-IDs frei ausgeben darf, halluziniert
Produkt-IDs — und eine halluzinierte ID sieht in der Datenbank aus wie eine
echte, bis jemand im Laden vor einem Regal steht.

Deshalb sind die zwei Aufrufe hier streng getrennt:

* `extract()` bekommt den Satz und darf **nur Suchbegriffe mit Mengen**
  zurückgeben — seit WB-340 mehrere je Zutat, vom genauesten zum
  allgemeinsten. Es sieht keinen einzigen Katalogeintrag, kann also keinen
  nennen.
* `choose()` bekommt die Kandidaten, die der Shop selbst gesucht hat, und darf
  **nur aus dieser Liste** wählen. Nennt es eine ID, die nicht vorgelegt
  wurde, wird der Vorschlag **verworfen und nicht repariert** — siehe
  `Auswahl.verworfen`.

**Guided Decoding erzwingt die Form, nicht die Wahrheit.** vLLM kann die
Antwort per `response_format` in ein Schema zwingen; damit ist sie sicher gültiges
JSON mit ganzzahliger `produkt_id`. Eine gültige Ganzzahl kann trotzdem eine
frei erfundene sein. Die Prüfung gegen die vorgelegten Kandidaten bleibt
deshalb im Code und wird nicht ans Schema abgetreten — sonst wanderte die
wichtigste Zusicherung des Projekts in eine Serveroption, die beim nächsten
vLLM-Update anders heissen kann.

Das Parsen ist absichtlich nachsichtig gegenüber der *Verpackung* (Codefence,
`{"begriffe": [...]}` statt blossem Array, `product_id` statt `produkt_id`)
und unnachgiebig gegenüber dem *Inhalt*. Verpackung ist Modelllaune; Inhalt
ist die Zusicherung.
"""
from __future__ import annotations

import concurrent.futures as cf
import contextvars

import json
import re
from dataclasses import dataclass, field

from zettel import db
from zettel.assistant import herkunft

#: Höchstzahl Begriffe, die eine Anfrage ergeben darf. Wer „alles fürs
#: Wochenende" schreibt, bekommt sonst eine Liste, die niemand mehr
#: zeilenweise bestätigt — und jeder Begriff kostet eine Suche.
MAX_BEGRIFFE = 20

#: Obergrenze je Menge. 99 Packungen Milch sind ein Vertipper des Modells,
#: keine Bestellung.
MAX_MENGE = 99

#: Wie viele Kandidaten je Begriff STUFE 3 VORGELEGT bekommt. Spec 8.3 will
#: genau diese Zahl später als Stellschraube gegen 20 vergleichen — sie
#: steht deshalb hier als Vorgabe und nicht als Literal im Code.
#:
#: **Das ist die MODELLGRENZE, und sie ist klein, weil sie Token kostet.**
#: Gemessen in WB-340 an „alles für Gemüselasagne" (echter Katalog): 63
#: Kandidaten waren 8.115 Zeichen Vorlage, rund 130 Zeichen je Kandidat. Sie
#: hat seit WB-359 einen sprechenden Namen, weil daneben eine zweite Grenze
#: steht, die etwas ganz anderes begrenzt (`KANDIDATEN_ANZEIGE`) — vorher
#: bediente diese eine Zahl beide Zwecke, und die Anzeige war deshalb bei
#: einbegriffigen Zutaten auf vier Alternativen beschränkt.
KANDIDATEN_MODELL = 5

#: Wie viele Suchbegriffe eine Zutat haben darf (WB-340). Drei, und das ist
#: gemessen: der Prompt bittet um zwei bis drei, und **der letzte Begriff einer
#: langen Kette entgleist**. Beobachtet wurden als dritter oder vierter Begriff
#: „Körnig" (ein Adjektiv, das den „MIIL Körnigen Frischkäse" zu einer
#: Mais-Zutat holt), „Papikra" und „Konzenzrat" (Tippfehler des Modells). Ein
#: solcher Begriff findet immer irgendetwas und vergiftet die Vereinigung.
#: Dagegen hilft dreierlei, und keines davon allein: eine kurze Kette, die
#: Reihenfolge (die Treffer des letzten Begriffs stehen hinten,
#: `catalog.search.suche_kette`) und die Obergrenze, die genau dort kürzt.
MAX_KETTE = 3

#: Kürzer als das ist kein Suchbegriff, sondern ein Bruchstück. Die Suche
#: sucht über Wortanfänge: „Ka" fände einen guten Teil des Katalogs, und in
#: einer Vereinigung wäre das nicht mehr zu erkennen. Ein echtes deutsches
#: Lebensmittel mit zwei Buchstaben gibt es nicht — „Ei" ist der Grenzfall und
#: würde hier wegfallen; er steht in jedem Rezept ohnehin als „Eier".
MIN_BEGRIFF = 3

#: Obergrenze der VEREINIGTEN Kandidaten je Zutat FÜR DAS MODELL (WB-340).
#:
#: Drei Begriffe à `KANDIDATEN_MODELL` Treffer sind 15 Kandidaten für eine
#: Zutat; neun Zutaten wären 135. Gemessen an der Handprobe „alles für
#: Gemüselasagne" (2026-08-28, echter Katalog): 9 Zutaten, 63 Kandidaten,
#: 8.115 Zeichen Vorlage für Stufe 3 — rund 130 Zeichen je Kandidat, also
#: gut 40 Token. In diesem Lauf hat die Grenze nicht gegriffen (die längste
#: Kette kam auf genau 10); ohne sie wüchse dieselbe Anfrage bei drei
#: ergiebigen Begriffen je Zutat auf gut das Anderthalbfache, und das für
#: eine Wahl, bei der das Modell je Zutat ohnehin nur eine Zeile ausgibt.
#:
#: 10 = 2 × `KANDIDATEN_MODELL` ist bewusst so gewählt: die Vereinigung darf
#: doppelt so lang werden wie die alte Einzelsuche, und die Treffer der
#: ERSTEN BEIDEN (genauesten) Begriffe passen immer vollständig hinein.
#: Damit kann die Obergrenze nie etwas wegnehmen, was der Agent vor WB-340
#: gesehen hätte — sie kürzt nur den Zugewinn, und zwar am allgemeinen Ende
#: der Kette, wo das Modell entgleist (siehe `MAX_KETTE`).
MAX_KANDIDATEN_MODELL = 2 * KANDIDATEN_MODELL

#: Wie viele Kandidaten je Begriff AUFGEHOBEN und der Nutzerin gezeigt werden
#: (WB-359).
#:
#: **Das ist die ANZEIGEGRENZE, und sie darf gross sein, weil sie keine Token
#: kostet.** Sie kostet Zeilen in `chat_kandidat` und sonst nichts; die
#: FTS-Abfrage holt intern ohnehin `max(limit × 5, 100)` Zeilen und sortiert
#: nach (`catalog.search.KANDIDATEN_FAKTOR`), 15 statt 5 ist dort dieselbe
#: Abfrage.
#:
#: 15 ist gemessen, nicht geraten (2026-08-28, 10.361 Produkte): mit `limit=15`
#: liefert der echte Katalog Butter 15, Schmand 15, Zucchini 15,
#: Lasagneplatten 12, Tomatenmark 11 — mit der Modellgrenze waren es je 5,
#: und bei einer EINBEGRIFFIGEN Kette blieben nach Abzug des gewählten
#: Produkts genau vier Alternativen. Ausgerechnet die einfachen Zutaten
#: (Butter, Schmand, Tomatenmark) sind einbegriffig, dort war die Auswahl
#: also am kleinsten und wird am ehesten gebraucht.
#:
#: Nach oben endet es nicht an der Technik, sondern am Daumen: die Liste wird
#: auf dem Handy durchgescrollt. Eine Katalog-Lücke schliesst auch eine
#: grössere Zahl nicht — „Sellerie" bleibt bei 2, und dann ist der
#: Freitext der richtige Ausgang und nicht ein längerer Vorrat an
#: Beinahe-Treffern.
KANDIDATEN_ANZEIGE = 15

#: Obergrenze der VEREINIGTEN aufgehobenen Kandidaten je Zutat (WB-359).
#: Dasselbe Verhältnis wie bei der Modellgrenze und aus demselben Grund: die
#: Treffer der beiden GENAUESTEN Begriffe passen immer vollständig hinein,
#: gekürzt wird nur am allgemeinen Ende der Kette, wo das Modell entgleist
#: (siehe `MAX_KETTE`). 30 Zeilen je Zutat sind für SQLite nichts.
MAX_KANDIDATEN_ANZEIGE = 2 * KANDIDATEN_ANZEIGE

#: Temperatur 0: derselbe Satz soll dieselben Begriffe ergeben. Ein Agent, der
#: bei jedem Aufruf etwas anderes tut, ist in Experiments (Spec 8.3) nicht
#: vergleichbar.
TEMPERATUR = 0.0

#: Gemessen, nicht geschätzt (2026-08-28, Qwen3.8-27B-Instruct, Denken aus):
#:
#:     alles für Pho                    677 Token, 20 Zutaten
#:     alles für Gemüselasagne          377 Token,  9 Zutaten
#:     alles für Spaghetti Bolognese    328 Token,  8 Zutaten
#:     alles für Sushi                  267 Token,  8 Zutaten
#:
#: Das sind rund **34 Token je Zutat**, und daran hängt diese Zahl. Sie stand
#: bei 800 und war damit zu klein: „alles für Pho" (20 Zutaten) brauchte 677
#: und riss gelegentlich trotzdem — die Box ist bei Temperatur 0 nicht
#: bitgenau deterministisch (WB-340), ein Gericht nahe der Grenze scheitert
#: deshalb SPORADISCH, was schwerer zu finden ist als immer.
#:
#: Zu klein geworden ist sie durch WB-340: davor gab Stufe 1 je Zutat EINEN
#: Begriff aus, seither mehrere, und die Ausgabe ist rund dreimal so lang.
#: Niemand hat die Zahl mitgezogen. Wer das Antwortformat wieder ändert, muss
#: hier nachrechnen — 34 Token je Zutat ist die Grösse, an der man es merkt.
#:
#: 1600 trägt gut 45 Zutaten. Der Kontext der Box ist 106.496 Token; knapp ist
#: hier nichts ausser dieser einen Zahl.
MAX_TOKENS = 1600

#: Denkt das Modell vor der Antwort? Hier nicht — und das ist gemessen, nicht
#: gemeint. Die Box läuft mit eingeschaltetem Denken; der erste Lauf von
#: `scripts/chat_probe.py` gegen die echte Box (2026-08-28) brach deshalb mit
#: „kein JSON" ab, und zwar an dieser Antwort:
#:
#:     '{\n  "begriffe": [\n    {\n      "begriff": "Spaghetti",\n      "menge": 1'
#:
#: Das JSON war nicht falsch, es war ABGESCHNITTEN: Denk-Token zählen gegen
#: `max_tokens`, und Qwens Vorrede frisst davon rund tausend. Der Ausweg ist
#: nicht ein grösseres Budget (das verlängert nur die Wartezeit im
#: Request-Pfad um ein Vielfaches), sondern das Denken je Anfrage
#: abzuschalten. `chat_template_kwargs` geht mit der ANFRAGE mit — der Server
#: bleibt unverändert, alles andere auf der Box denkt weiter.
#:
#: Für Stufe 1 und 3 ist das kein Verlust: beide sollen keine Überlegung
#: ausgeben, sondern eine Liste. Wer das Gegenteil messen will, setzt
#: `denken=True` — Spec 8.3 nennt genau solche Stellschrauben.
DENKEN = False


class PlanFehler(RuntimeError):
    """Die Modellantwort war nicht zu gebrauchen.

    Ausdrücklich kein Fehler des Shops: der Aufrufer fängt das ab und zeigt es
    im Chat an. Alles ausser dem Chat läuft weiter (Spec 11).
    """


# --------------------------------------------------------------------------
# Stufe 1 — plan.extract
#
# Der Prompt trägt eine Eigenheit dieses Shops mit, die keine Modellschwäche
# ist: **die Katalogsuche kennt nur Wortanfänge, keine Infixe** (WB-322 misst
# das). „Hackfleisch" findet „Rinderhackfleisch" nicht über den Namen. Das ist
# nicht in Stufe 1 zu lösen — aber ein Modell, das kurze, allgemeine Begriffe
# liefert, läuft seltener hinein als eines, das „500 g gemischtes Hackfleisch
# vom Rind" schreibt. Was das messbar bringt, steht in `scripts/chat_probe.py`.

SYSTEM_EXTRACT = """\
Du hilfst beim Einkaufen. Du bekommst einen Satz und machst daraus eine Liste \
von Zutaten. Zu jeder Zutat gibst du MEHRERE Suchbegriffe für einen \
Lebensmittel-Katalog an.

Regeln für die Suchbegriffe einer Zutat (zwei bis drei, vom genauesten zum \
allgemeinsten):
- Der erste Begriff ist der genaueste. Gehört die Form zur Zutat, gehört sie \
dazu: „passierte Tomaten" ist etwas anderes als „Tomaten".
- Danach wirst du allgemeiner. Der Katalog sucht über Wortanfänge, kurze \
Begriffe finden mehr.
- Zusammengesetzte Wörter nennst du zusätzlich als Grundwort: \
„Knoblauchzehen" auch als „Knoblauch", „Lasagneplatten" auch als „Lasagne".
- Gebräuchliche Synonyme nimmst du auf: „Möhren" auch als „Karotten", \
„geriebener Käse" auch als „Reibekäse".
- Ist unklar, wie das Produkt im Laden heisst, nennst du Einzahl UND Mehrzahl: \
„Auberginen" und „Aubergine".
- Jeder Begriff muss die Zutat für sich allein benennen. Kein Adjektiv ohne \
sein Hauptwort („körnig" ist keine Zutat), keine Abkürzung, kein halbes Wort. \
Fällt dir nur ein Begriff ein, nennst du nur einen.

Regeln für die Liste:
- Nur Suchbegriffe und Mengen. KEINE Produktnamen, KEINE Marken, KEINE Nummern.
- Bei einem Gericht: die Zutaten, die man dafür kaufen muss. Was in jedem \
Haushalt steht (Salz, Pfeffer, Wasser, Öl, Gewürze), lässt du weg.
- Die Menge ist die Anzahl Packungen, die gekauft werden soll. Im Zweifel 1.
- Nichts erfinden, was im Satz nicht vorkommt oder zum Gericht nicht gehört.

Nennt der Satz ein GERICHT — einen Rezeptnamen wie „Spaghetti Bolognese", \
„Gemüselasagne", „Pho" —, schreibst du dessen Namen zusätzlich in das Feld \
"gericht". Nur den Namen des Gerichts, ohne „alles für" und ohne die anderen \
Wünsche des Satzes. Nennt der Satz kein Gericht, sondern einzelne Waren, \
setzt du "gericht" auf null.

Antworte ausschliesslich als JSON:
{"gericht": null, "begriffe": [{"suchbegriffe": ["Rinderhackfleisch", \
"Hackfleisch"], "menge": 1}, {"suchbegriffe": ["passierte Tomaten", \
"Tomaten"], "menge": 2}]}"""

#: Der ZUSATZ an Stufe 1 für kurze Sätze (WB-368): ist das ein Oberbegriff,
#: und wenn ja, welche Kategorie des Katalogs ist gemeint?
#:
#: **Ein Aufruf, zwei Auskünfte** — dieselbe Bauart wie beim Gerichtsnamen
#: (WB-338). Eine eigene Modellstufe davorzuhängen hiesse, dass JEDER kurze
#: Satz eine Wartezeit mehr kostet, auch „Milch" und „Tomatenmark"; das Modell
#: liest den Satz aber ohnehin gerade. Ein Zug, der auffächert, kostet damit
#: sogar WENIGER als vorher: Stufe 3 entfällt, weil nichts zu wählen ist.
#:
#: **Das Modell erfindet hier nichts, es ordnet zu.** Die Kategorien stehen in
#: der Vorlage; was nicht darin steht, wird verworfen (siehe `_kategorie`) —
#: dieselbe Zusicherung wie bei den Produkt-IDs in `choose`. Nötig ist das,
#: weil nicht jeder Oberbegriff auch ein Kategoriename ist: „Nudeln" steckt im
#: Katalog unter „Reis, Pasta & Getreide", und diese Übersetzung kann keine
#: Zeichenkettenregel.
#:
#: **Die Gegenbeispiele im Prompt sind gemessen und nicht Zierrat**
#: (2026-08-28, echter Katalog, Qwen3.8-27B). Ohne sie fächerte das Modell
#: fast jedes einzelne Wort auf — „Gouda" zu „Käse", „Klopapier" zu „Papier- &
#: Hygieneartikel", „Spaghetti" zu „Reis, Pasta & Getreide", „Bierschinken" zu
#: „Aufschnitt". Wer eine WARE tippt, bekäme dann statt des Produkts ein Menü.
#: Mit der Unterscheidung Warengruppe/Ware und sieben Gegenbeispielen trafen
#: dieselben 14 Wörter: Aufschnitt, Käse, Nudeln, Wurst, Obst -> Kategorie;
#: Tomatenmark, Bierschinken, Landmilch, Gouda, Klopapier, Spaghetti, Butter
#: -> Suchbegriffe. „Getränke" und „Milch" nannten eine Kategorie, die es
#: nicht gibt — sie wird verworfen, und der Satz läuft als Suche weiter.
SYSTEM_KATEGORIE = """

Zusätzlich: der Satz besteht nur aus ein bis zwei Wörtern. Prüfe, ob er eine \
WARENGRUPPE nennt statt einer Ware.

Eine Warengruppe ist ein Sammelname, unter dem im Laden ganz verschiedene \
Waren liegen und aus dem man erst eine Sorte aussuchen muss: „Aufschnitt", \
„Käse", „Nudeln", „Getränke", „Obst".

Eine WARE ist alles, was man so in den Wagen legen kann — auch wenn es zu \
einer Warengruppe gehört: „Gouda", „Bierschinken", „Spaghetti", \
„Tomatenmark", „Landmilch", „Klopapier", „Butter". Dafür setzt du \
"kategorie" auf null und gibst wie sonst Suchbegriffe an.

Ist es eine Warengruppe, schreibst du in "kategorie" die passende Kategorie \
aus der folgenden Liste — WÖRTLICH so, wie sie dort steht — und lässt \
"begriffe" leer. Steht keine passende in der Liste, ist "kategorie" null.

Kategorien: {kategorien}"""


def system_mit_kategorien(system: str, kategorien: list[str]) -> str:
    """Hängt die Kategorienliste an den System-Prompt von Stufe 1."""
    return system + SYSTEM_KATEGORIE.format(kategorien=", ".join(kategorien))


SCHEMA_EXTRACT = {
    "type": "object",
    "properties": {
        # Der Gerichtsname, falls der Satz einen nennt (WB-338). NICHT
        # `required`: ein Satz über Milch und Klopapier nennt kein Gericht,
        # und ein Modell, das eines nennen MUSS, erfindet eines.
        #
        # `["string", "null"]` und nicht bloss `"string"`: mit Guided
        # Decoding kann das Modell ein Feld, das im Schema steht, nur mit
        # einem passenden Wert füllen — ohne `null` bliebe ihm nur, sich
        # etwas auszudenken oder das Feld ganz wegzulassen.
        "gericht": {"type": ["string", "null"]},
        "begriffe": {
            "type": "array",
            "maxItems": MAX_BEGRIFFE,
            "items": {
                "type": "object",
                "properties": {
                    # Die Kette, nicht ein Begriff (WB-340). `minItems: 1`,
                    # weil eine Zutat ohne Begriff keine halbe Zutat ist,
                    # sondern nichts.
                    "suchbegriffe": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_KETTE,
                        "items": {"type": "string"},
                    },
                    "menge": {"type": "integer", "minimum": 1,
                              "maximum": MAX_MENGE},
                },
                "required": ["suchbegriffe", "menge"],
                "additionalProperties": False,
            },
        },
    },
    # `gericht` steht MIT in `required`, und das ist gemessen und nicht
    # Geschmack: ohne diesen Eintrag liess das Modell das Feld bei „alles für
    # Pho" schlicht weg (2026-08-28, Qwen3.8-27B) — ausgerechnet bei dem
    # Gericht, für das die Quelle gebaut wurde. Mit `required` erzwingt
    # Guided Decoding das Feld; `null` bleibt eine gültige Antwort, also wird
    # dadurch kein Gericht erfunden. Es steht ausserdem VOR `begriffe`: das
    # Modell nennt es damit, bevor es sich in eine lange Zutatenliste
    # verrennt (WB-363).
    "required": ["gericht", "begriffe"],
    "additionalProperties": False,
}


def schema_mit_kategorie(schema: dict = SCHEMA_EXTRACT) -> dict:
    """Dasselbe Schema, um das Feld `kategorie` ergänzt (WB-368).

    Aus dem vorhandenen abgeleitet und nicht danebengeschrieben: zwei
    getrennte Schemata liefen beim nächsten Ticket auseinander, und die
    Abweichung fiele erst auf, wenn Guided Decoding etwas anderes erzwingt,
    als der Prompt verlangt.

    `kategorie` steht MIT in `required` und darf `null` sein — genau wie
    `gericht`: ohne `required` liess das Modell das Feld gemessen einfach weg,
    und ohne `null` bliebe ihm nur, sich eine Kategorie auszudenken.
    """
    return {**schema,
            "properties": {"kategorie": {"type": ["string", "null"]},
                           **schema["properties"]},
            "required": [*schema["required"], "kategorie"]}


@dataclass(frozen=True)
class Plan:
    """Was Stufe 1 aus dem Satz gemacht hat: Zutaten und — vielleicht — ein
    Gericht.

    Das Gericht ist seit WB-338 dabei und ändert an den Zutaten nichts: sie
    bleiben die Antwort des Modells und damit der Weg, der immer funktioniert.
    Der Gerichtsname ist die ZUSÄTZLICHE Auskunft, mit der der Chat-Zug bei
    einer Quelle nachschlagen (und damit das Raten ersetzen) kann.
    """
    zutaten: list[dict] = field(default_factory=list)
    gericht: str | None = None
    #: Die Katalogkategorie, wenn der Satz einen OBERBEGRIFF nennt (WB-368).
    #: Immer eine aus der VORGELEGTEN Liste — was das Modell sonst nennt,
    #: steht in `kategorie_verworfen` und wird nicht benutzt.
    kategorie: str | None = None
    #: Was das Modell als Kategorie nannte, obwohl es sie nicht gibt. Die
    #: Schwester von `Auswahl.verworfen`: nicht Protokollrest, sondern die
    #: Zahl, an der sich messen lässt, ob das Modell erfindet.
    kategorie_verworfen: str | None = None


#: Grenzen für einen Gerichtsnamen aus dem Modell. Zwei Zeichen sind kein
#: Gericht, achtzig sind ein Satz — und beides wäre eine sinnlose Anfrage an
#: eine fremde Seite.
MIN_GERICHT = 3
MAX_GERICHT = 80


def _gericht(text: str) -> str | None:
    """Der Gerichtsname aus der Antwort von Stufe 1, oder `None`.

    Nachsichtig wie der Rest des Moduls: fehlt das Feld, ist es `null`, steht
    Unsinn darin oder war die Antwort abgeschnitten — dann eben kein Gericht.
    Ein fehlender Gerichtsname kostet nur die Abkürzung über die Quelle; ein
    falscher kostet eine Anfrage an eine fremde Seite und ein Rezept, das
    niemand wollte.
    """
    def sauber(roh):
        gekuerzt = " ".join(roh.split()).strip(" .,:;-–—\"'„“")
        return gekuerzt if MIN_GERICHT <= len(gekuerzt) <= MAX_GERICHT else None

    try:
        wert = _json_wert(text)
    except PlanFehler:
        # Abgeschnitten (WB-363, und das trifft ausgerechnet Pho): der Anfang
        # der Antwort steht trotzdem da, und `gericht` steht im Schema VOR
        # `begriffe`. Es hier von Hand herauszuschneiden ist die einzige
        # Stelle im Modul, an der ein regulärer Ausdruck auf Modellausgabe
        # losgelassen wird — und sie ist es wert: ohne sie verliert genau der
        # Fall den Gerichtsnamen, für den die Quelle gebaut wurde.
        treffer = _GERICHT_ROH.search(text)
        return sauber(treffer.group(1)) if treffer else None
    if not isinstance(wert, dict):
        return None
    for name in ("gericht", "dish", "rezept", "gerichtsname"):
        roh = wert.get(name)
        if isinstance(roh, str):
            gefunden = sauber(roh)
            if gefunden:
                return gefunden
    return None


#: `"gericht": "Pho"` aus einer angeschnittenen Antwort. Absichtlich streng:
#: kein Escape, keine Verschachtelung — was komplizierter ist, soll der
#: JSON-Weg oben holen.
_GERICHT_ROH = re.compile(r'"gericht"\s*:\s*"([^"\\]{1,80})"')


def extract(zugang, satz: str, *, guided: bool = True,
            system: str = SYSTEM_EXTRACT,
            temperatur: float = TEMPERATUR,
            max_tokens: int = MAX_TOKENS,
            denken: bool = DENKEN) -> list[dict]:
    """Satz -> `[{"suchbegriffe": […], "menge": …}, …]`. Nie Produkt-IDs.

    Je Zutat eine ganze KETTE von Suchbegriffen, vom genauesten zum
    allgemeinsten (WB-340). Der Shop sucht anschliessend über alle und
    vereinigt die Treffer (`catalog.search.suche_kette`) — ein einzelner
    Begriff ist gleichzeitig zu streng („Lasagneplatten" findet nichts) und zu
    grosszügig („Zwiebel" findet Zwiebelbrot), und keine Zeichenkettenregel
    fängt das auf. Ein Sprachmodell ist hier der bessere Zerleger: es bietet
    „Reibekäse" und „Karotten" ungefragt an.

    Wirft `PlanFehler`, wenn nichts Brauchbares zurückkam — kein JSON, ein
    leeres Array, ein falscher Typ. Der Aufrufer macht daraus eine Meldung im
    Chat; er baut daraus NICHTS zusammen, was das Modell nicht gesagt hat.

    Die Kurzform von `extract_plan()`, für alles, was den Gerichtsnamen
    nicht braucht (Evals, Proben, die Tests dieses Moduls).
    """
    return extract_plan(zugang, satz, guided=guided, system=system,
                        temperatur=temperatur, max_tokens=max_tokens,
                        denken=denken).zutaten


def extract_plan(zugang, satz: str, *, guided: bool = True,
                 system: str = SYSTEM_EXTRACT,
                 temperatur: float = TEMPERATUR,
                 max_tokens: int = MAX_TOKENS,
                 denken: bool = DENKEN,
                 kategorien: list[str] | None = None) -> Plan:
    """Wie `extract()`, gibt aber auch den Gerichtsnamen zurück (WB-338).

    **Ein Aufruf, zwei Auskünfte.** Den Gerichtsnamen in einer eigenen Stufe
    zu erfragen wäre ein zweiter Modellaufruf je Satz — und das Modell hat
    den Satz ohnehin gerade gelesen. Kostet der Gerichtsname nichts extra,
    darf er auch dann dastehen, wenn ihn niemand braucht.

    `kategorien` macht daraus drei Auskünfte (WB-368): die Liste der
    Katalogkategorien geht mit in den Prompt, und das Modell darf eine davon
    als Oberbegriff nennen. **Nur eine davon** — was nicht in der Liste steht,
    landet in `Plan.kategorie_verworfen` und wird nicht benutzt. Ohne
    `kategorien` ist der Aufruf Wort für Wort derselbe wie vorher, also auch
    dasselbe Prompt-Budget: die 1.036 Zeichen Kategorienliste zahlt nur, wer
    einen kurzen Satz schreibt (siehe `assistant.oberbegriffe.MAX_WOERTER`).
    """
    text = (satz or "").strip()
    if not text:
        raise PlanFehler("Leere Anfrage — dazu gibt es nichts zu suchen.")
    schema = SCHEMA_EXTRACT
    if kategorien:
        system = system_mit_kategorien(system, kategorien)
        schema = schema_mit_kategorie(schema)
    antwort = _frage(zugang, system, text, schema, "begriffe",
                     guided, temperatur, max_tokens, denken)
    roh = _eintraege(antwort, ("begriffe", "zutaten", "items", "liste"))
    zutaten = _zutaten_aus(roh)
    gewaehlt, verworfen = _kategorie(antwort, kategorien or [])
    if not zutaten and gewaehlt is None and verworfen is None:
        # Ein Oberbegriff ist die AUSNAHME von „ohne Begriffe geht nichts":
        # wer „Aufschnitt" tippt, bekommt die Sorten des Katalogs und keine
        # Suche, und dafür braucht es keine einzige Zutat. Ohne diesen Zweig
        # scheiterte ausgerechnet die knappste Antwort des Modells.
        #
        # Dasselbe gilt für eine VERWORFENE Kategorie: gemessen antwortete das
        # Modell auf „Getränke" mit `kategorie: "Alkoholfreie Alternatives"`
        # (so nicht im Katalog) und einer leeren Begriffsliste. Das ist keine
        # kaputte Antwort, sondern eine, die auf die falsche Frage passt — der
        # Aufrufer sucht dann nach dem, was getippt wurde (`chat._aus_modell`).
        raise PlanFehler(
            "Das Modell hat aus dem Satz keine Suchbegriffe gemacht.")
    return Plan(zutaten=zutaten, gericht=_gericht(antwort),
                kategorie=gewaehlt, kategorie_verworfen=verworfen)


def _kategorie(text: str, erlaubt: list[str]) -> tuple[str | None, str | None]:
    """Die Kategorie aus der Antwort — geprüft gegen die VORLAGE (WB-368).

    Gibt `(gewaehlt, verworfen)` zurück. **Hier endet der Halluzinationsweg,
    genau wie bei den Produkt-IDs in `choose`:** nennt das Modell eine
    Kategorie, die ihm nicht vorgelegt wurde, wird sie verworfen und NICHT auf
    die ähnlichste gebogen. Eine erfundene Kategorie führte sonst in eine
    Auffächerung ohne Sorten — eine leere Auswahl, die aussieht wie eine
    echte.

    Verglichen wird nachsichtig in der Schreibweise (Umlaute, Gross- und
    Kleinschreibung, Leerzeichen) und unnachgiebig im Bestand: zurückgegeben
    wird immer der Name, wie er im Katalog steht, denn mit ihm wird gleich
    abgefragt.
    """
    if not erlaubt:
        return None, None
    try:
        wert = _json_wert(text)
    except PlanFehler:
        return None, None
    if not isinstance(wert, dict):
        return None, None
    roh = _text(wert, ("kategorie", "category", "oberbegriff", "warengruppe"))
    if not roh:
        return None, None
    nach_form = {_form(name): name for name in erlaubt}
    treffer = nach_form.get(_form(roh))
    return (treffer, None) if treffer else (None, roh)


def _form(text: str) -> str:
    """Die Vergleichsform eines Kategorienamens: umlautfrei, klein, entrümpelt."""
    return " ".join(db.normalisiere(text or "").split()).casefold()


def _zutaten_aus(roh: list[dict]) -> list[dict]:
    """Die rohen Einträge des Modells -> geprüfte Zutaten mit Begriffskette."""
    zutaten: list[dict] = []
    gesehen: set[str] = set()
    for eintrag in roh:
        kette = _kette(eintrag)
        if not kette:
            # Eine Zeile ohne Begriff ist keine halbe Zutat, sondern Rauschen.
            continue
        schluessel = kette[0].casefold()
        if schluessel in gesehen:
            # Zweimal dieselbe Zutat hiesse zweimal dieselbe Suche und zwei
            # gleiche Vorschlagszeilen. Die Menge steht in der ersten.
            continue
        gesehen.add(schluessel)
        zutaten.append({"suchbegriffe": kette,
                        "menge": _menge(eintrag, ("menge", "anzahl", "qty",
                                                  "quantity"))})
        if len(zutaten) >= MAX_BEGRIFFE:
            break
    return zutaten


def _kette(eintrag: dict) -> list[str]:
    """Die Suchbegriffe einer Zutat, geordnet und entdoppelt.

    Nachsichtig gegenüber der Verpackung wie der Rest dieses Moduls: eine
    Liste unter `suchbegriffe`, eine unter `begriffe`, oder ein einzelner
    `begriff` als Zeichenkette — alles wird zur Kette. Ein Modell, das nur
    einen Begriff nennt, ist damit kein Fehlerfall, sondern eine Kette der
    Länge eins.
    """
    roh = None
    for name in ("suchbegriffe", "begriffe", "terms", "queries"):
        wert = eintrag.get(name)
        if isinstance(wert, (list, tuple)):
            roh = wert
            break
    if roh is None:
        einer = _text(eintrag, ("begriff", "suchbegriff", "term", "name",
                                "query"))
        roh = [einer] if einer else []
    kette: list[str] = []
    gesehen: set[str] = set()
    for wert in roh:
        if not isinstance(wert, str):
            continue
        begriff = " ".join(wert.split())
        if len(begriff) < MIN_BEGRIFF or begriff.casefold() in gesehen:
            # Derselbe Begriff zweimal wäre dieselbe Abfrage zweimal — und in
            # der Vereinigung keine einzige zusätzliche Zeile. Ein Bruchstück
            # („Ka") wäre schlimmer: es findet über die Präfixsuche halb so
            # viel wie der Katalog hergibt (siehe MIN_BEGRIFF).
            continue
        gesehen.add(begriff.casefold())
        kette.append(begriff)
        if len(kette) >= MAX_KETTE:
            break
    return kette


# --------------------------------------------------------------------------
# Stufe 1b — plan.zutatenbegriffe: aus einer fremden Zutatenliste Suchbegriffe
#
# Dieselbe Aufgabe wie `extract`, nur mit einer besseren Eingabe: nicht der
# Satz der Nutzerin, sondern die Zutatenliste eines echten Rezepts (WB-338).
# Das Modell RÄT hier nichts mehr — es übersetzt. Und Übersetzen kann es,
# gemessen gegen den echten Katalog (10.361 Produkte):
#
#     Chefkochs Schreibweise roh                8 von 14 Zutaten gefunden
#     trivial normalisiert                     10 von 14
#     mit dem Modell und Begriffsketten        12 von 12
#
# Es liefert ungefragt Synonyme („Reibekäse", „Karotten") und kürzt Komposita
# richtig („Knoblauchzehe" -> „Knoblauch"). Eine Heuristik kann das nicht: das
# naive Präfix-Kürzen macht aus „Staudensellerie" ein „Staud" und findet
# Staud's Apfelmus — ein falscher Treffer, der aussieht wie ein richtiger.

SYSTEM_ZUTATEN = """\
Du hilfst beim Einkaufen. Du bekommst die Zutatenliste eines Rezepts, so wie \
sie auf einer Rezeptseite steht, und machst daraus Suchbegriffe für einen \
Lebensmittel-Katalog.

Regeln für die Suchbegriffe einer Zutat (zwei bis drei, vom genauesten zum \
allgemeinsten):
- Der erste Begriff ist der genaueste. Gehört die Form zur Zutat, gehört sie \
dazu: „passierte Tomaten" ist etwas anderes als „Tomaten".
- Danach wirst du allgemeiner. Der Katalog sucht über Wortanfänge, kurze \
Begriffe finden mehr.
- Zusammengesetzte Wörter nennst du zusätzlich als Grundwort: \
„Knoblauchzehen" auch als „Knoblauch", „Lasagneplatten" auch als „Lasagne", \
„Staudensellerie" auch als „Sellerie".
- Gebräuchliche Synonyme nimmst du auf: „Möhren" auch als „Karotten", \
„geriebener Käse" auch als „Reibekäse".
- Klammern und Mehrzahlformen der Rezeptseite lässt du weg: aus „Zwiebel(n)" \
wird „Zwiebel".
- Jeder Begriff muss die Zutat für sich allein benennen. Kein Adjektiv ohne \
sein Hauptwort, keine Abkürzung, kein halbes Wort.

Regeln für die Liste:
- Eine Zeile je Zutat des Rezepts, in derselben Reihenfolge.
- Was in jedem Haushalt steht (Salz, Pfeffer, Wasser, Zucker, Öl, Essig, \
Gewürze), lässt du weg.
- Kommt dieselbe Zutat mehrfach vor, fasst du sie zu einer Zeile zusammen.
- Die Menge ist die Anzahl PACKUNGEN, die gekauft werden soll — nicht die \
Menge aus dem Rezept. Im Zweifel 1.
- Die Zutatenliste ist die Wahrheit. Erfinde nichts dazu, was nicht dasteht.
- Nur Suchbegriffe und Mengen. KEINE Produktnamen, KEINE Marken, KEINE Nummern.

Antworte ausschliesslich als JSON:
{"begriffe": [{"suchbegriffe": ["passierte Tomaten", "Tomaten"], "menge": 1}, \
{"suchbegriffe": ["Knoblauch"], "menge": 1}]}"""


def zutatenliste(zutaten: list[dict], *, gericht: str | None = None,
                 servings=None) -> str:
    """Die Zutaten der Quelle als Prompt — so, wie sie auf der Seite stehen.

    Menge und Einheit gehen mit, obwohl gekauft wird und nicht gekocht: „500
    ml passierte Tomaten" ist eine Packung, „3 Liter Wasser" ist keine, und
    ohne die Menge kann das Modell den Unterschied nicht sehen.

    **Was neben dem Gericht im Satz stand, steht hier NICHT** (WB-370). Bis
    dahin ging es als Zeile „Ausserdem gewünscht, nicht aus dem Rezept:
    Klopapier" mit, in der Hoffnung auf die Übersetzung „Klopapier" ->
    „Toilettenpapier", die die Präfixsuche nie findet. Gemessen am
    2026-08-28 gegen die echte Box (Qwen3.8-27B) an 35 Chefkoch-Gerichten
    mit je einem Rest im Satz:

        der Rest kam als Begriff zurück      3 von 35
        der Rest kam ÜBERSETZT zurück        0 von 35
        der Rest kam gar nicht zurück       32 von 35

    Die Zeile brachte die Übersetzung also nicht — und sie kostete den
    stillen Verlust, um den es in WB-370 geht. Der Rest wird seither im Code
    angehängt (`chat._rest_sichern`), wo das Modell ihn nicht übergehen
    kann.
    """
    zeilen = []
    if gericht:
        kopf = f"Rezept: {gericht}"
        if servings:
            kopf += f" (für {servings} Portionen)"
        zeilen.append(kopf)
    zeilen.append("Zutaten:")
    for z in zutaten:
        menge = z.get("amount")
        if menge is not None:
            menge = int(menge) if float(menge).is_integer() else menge
        teile = [str(menge) if menge is not None else "",
                 (z.get("unit") or "").strip(),
                 (z.get("raw_name") or z.get("name") or "").strip()]
        zeilen.append("- " + " ".join(t for t in teile if t))
    return "\n".join(zeilen)


def zutatenbegriffe(zugang, zutaten: list[dict], *, gericht: str | None = None,
                    servings=None,
                    guided: bool = True, system: str = SYSTEM_ZUTATEN,
                    temperatur: float = TEMPERATUR,
                    max_tokens: int = MAX_TOKENS,
                    denken: bool = DENKEN) -> list[dict]:
    """Zutatenliste einer Quelle -> Begriffsketten MIT ihrer Herkunftszutat.

    Fast dieselbe Form wie `extract()`, damit Stufe 2 und Stufe 3 unverändert
    weiterlaufen: was sich ändert, ist die HERKUNFT der Zutaten, nicht der
    Weg durch den Shop. Dazu kommen seit WB-369 `bedarf`, `einheit` und
    `zutat` je Zeile — die Menge, die im Rezept steht.

    **Diese Zuordnung kostet keinen Modellaufruf und keine Prompt-Zeile.**
    Sie entsteht im Code aus den Namen (`assistant.herkunft`), weil der
    Begriff aus einer bekannten Zutat gebildet wurde und deren Wörter noch
    darin stehen. Das Modell wird hier nicht gefragt — und darf deshalb auch
    keine Menge erfinden: eine Zahl, die nicht aus Chefkochs Zutatenliste
    stammt, gibt es an dieser Stelle gar nicht.

    Wirft `PlanFehler` wie `extract()`. Der Aufrufer hat dann immer noch die
    Zutaten der Quelle und kann sich daraus selbst Ketten bauen
    (`gerichte.chefkoch.zutat_kette`) — schlechter als das Modell, aber
    besser als nichts.

    **Was neben dem Gericht im Satz stand, geht hier nicht mit** (WB-370,
    siehe `zutatenliste`). Diese Stufe bekommt eine Zutatenliste und macht
    Suchbegriffe daraus; der Rest des Satzes ist keine Zutat, und ihn
    trotzdem beizulegen hiess, ihn dem Ermessen des Modells zu überlassen.
    """
    if not zutaten:
        raise PlanFehler("Die Quelle hat keine Zutaten geliefert.")
    antwort = _frage(zugang, system,
                     zutatenliste(zutaten, gericht=gericht, servings=servings),
                     SCHEMA_EXTRACT, "begriffe", guided, temperatur,
                     max_tokens, denken)
    begriffe = _zutaten_aus(
        _eintraege(antwort, ("begriffe", "zutaten", "items", "liste")))
    if not begriffe:
        raise PlanFehler(
            "Das Modell hat aus der Zutatenliste keine Suchbegriffe gemacht.")
    return herkunft.zuordnen(zutaten, begriffe)


# --------------------------------------------------------------------------
# Stufe 3 — plan.choose

SYSTEM_CHOOSE = """\
Du wählst für eine Einkaufsliste aus vorgelegten Katalogprodukten aus.

Regeln:
- Du darfst AUSSCHLIESSLICH IDs verwenden, die unten in der Liste stehen. \
Eine ID, die dort nicht steht, wird verworfen.
- Je Suchbegriff höchstens ein Produkt: das, was am ehesten gemeint ist.
- Passt zu einem Begriff nichts davon, lässt du ihn weg. Rate nicht.
- Die Menge übernimmst du aus dem Begriff.

Antworte ausschliesslich als JSON:
{"auswahl": [{"begriff": "Hackfleisch", "produkt_id": 123, "menge": 1}]}"""

#: Dieselbe Stufe, andere Frage: aus einer SORTE ein Produkt (WB-368).
#:
#: **Gemessen, und deshalb überhaupt vorhanden** (2026-08-28, echter Katalog,
#: Qwen3.8-27B): mit `SYSTEM_CHOOSE` wählte das Modell zu „Rohschinken &
#: Bacon" NICHTS — in drei Formulierungen der Anfrage dreimal nichts. Kein
#: Wunder: dort steht „das, was am ehesten gemeint ist … passt nichts davon,
#: lässt du ihn weg", und kein einziges Produkt heisst „Rohschinken & Bacon".
#: Der Begriff ist hier eben kein Suchbegriff, sondern ein REGALNAME, und die
#: Kandidaten stehen alle wirklich darin. Mit diesem Prompt wählte dasselbe
#: Modell in allen fünf Aufschnitt-Sorten ein Produkt, in 1,3 bis 1,8 s.
#:
#: Ein eigener Prompt und kein Zusatz am alten: die Regel „passt nichts, lass
#: es weg" ist bei einer Suche richtig (der Katalog hat Lücken) und bei einer
#: Sorte falsch (die Nutzerin hat das Regal selbst angetippt, und die Zahl
#: daneben war echt).
SYSTEM_CHOOSE_SORTE = """\
Du wählst für eine Einkaufsliste aus vorgelegten Katalogprodukten aus.

Der „begriff" ist der NAME EINER SORTE aus dem Kategoriebaum des Ladens \
(zum Beispiel „Rohschinken & Bacon"), nicht der Name eines Produkts. Alle \
vorgelegten Kandidaten stehen wirklich in dieser Sorte; die Nutzerin hat sie \
selbst ausgewählt.

Regeln:
- Du darfst AUSSCHLIESSLICH IDs verwenden, die unten in der Liste stehen. \
Eine ID, die dort nicht steht, wird verworfen.
- Je Sorte GENAU EIN Produkt: das gewöhnlichste, das jemand meint, der diese \
Sorte anklickt. Im Zweifel das erste.
- Nur wenn die Liste zu einer Sorte leer ist, lässt du sie weg.
- Die Menge ist 1, ausser die Anfrage nennt eine andere.

Antworte ausschliesslich als JSON:
{"auswahl": [{"begriff": "Salami", "produkt_id": 123, "menge": 1}]}"""

SCHEMA_CHOOSE = {
    "type": "object",
    "properties": {
        "auswahl": {
            "type": "array",
            "maxItems": MAX_BEGRIFFE,
            "items": {
                "type": "object",
                "properties": {
                    "begriff": {"type": "string"},
                    "produkt_id": {"type": "integer"},
                    "menge": {"type": "integer", "minimum": 1,
                              "maximum": MAX_MENGE},
                },
                "required": ["begriff", "produkt_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["auswahl"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Auswahl:
    """Was Stufe 3 ergeben hat — samt dem, was verworfen wurde.

    `verworfen` ist kein Protokollrest, sondern die interessanteste Zahl der
    Stufe: sie sagt, wie oft das Modell eine ID genannt hat, die ihm nie
    vorgelegt wurde. Sie wandert in den Bericht und später in den Span
    (WB-328).
    """
    gewaehlt: list[dict] = field(default_factory=list)
    verworfen: list[dict] = field(default_factory=list)
    roh: str = ""


def kandidat_kurz(p: dict) -> dict:
    """Ein Produkt so, wie es dem Modell vorgelegt wird.

    Bewusst wenig: id, Name, Gebinde, Preis. Kategorien und Marke sind für die
    Wahl selten entscheidend und blähen den Prompt so weit auf, dass bei
    zwanzig Kandidaten je Begriff (Spec 8.3) das Kontextfenster zum Thema
    wird.

    Auch `via` — der Begriff, über den der Kandidat kam (WB-340) — bleibt
    draussen. Es ist eine Auskunft für den Trace und für die Vorschlagszeile,
    keine Entscheidungshilfe: das Modell soll das Produkt wählen, das gemeint
    ist, und nicht das, dessen Suchbegriff am genauesten klang.
    """
    return {"id": int(p["id"]), "name": p.get("name") or "",
            "gebinde": p.get("unit_text") or "",
            "preis_cent": p.get("price_cents")}


#: In so viele gleichzeitige Anfragen wird Stufe 3 zerlegt (WB-412).
#:
#: **Gemessen an einem echten Rezept** (Pho Bo, 17 Begriffe mit Kandidaten,
#: echte Box, echter Katalog, 2026-08-30):
#:
#:     1 Spur    16,7 s   11 gewählt
#:     2 Spuren  11,1 s   11 gewählt   1,51×
#:     4 Spuren   9,4 s   11 gewählt   1,78×
#:     6 Spuren   8,7 s   12 gewählt   1,92×
#:
#: Vier, weil danach kaum noch etwas kommt: von vier auf sechs sind es 0,7 s.
#: Die Box bedient vier Anfragen nebeneinander mit 74,8 tok/s gegen 24,9
#: einzeln — mehr Spuren teilen dieselbe Bandbreite auf mehr Prompts auf, und
#: jeder Prompt trägt den Systemteil noch einmal.
SPUREN = 4

#: Darunter wird nicht zerlegt. Vier Anfragen mit je zwei Begriffen zahlen
#: viermal den Systemprompt für eine Antwort, die ohnehin kurz ist.
SPUREN_AB = 8


def choose(zugang, satz: str, aufgaben: list[dict], *, guided: bool = True,
           system: str = SYSTEM_CHOOSE, temperatur: float = TEMPERATUR,
           max_tokens: int = MAX_TOKENS, denken: bool = DENKEN,
           gericht: str | None = None, spuren: int = SPUREN) -> Auswahl:
    """Wählt je Begriff höchstens ein vorgelegtes Produkt.

    `aufgaben` ist `[{"begriff", "menge", "kandidaten": [Produkt, …]}, …]` —
    genau das, was `catalog.search` zurückgegeben hat. `begriff` ist seit
    WB-340 der genaueste Begriff der Kette und steht für die ganze Zutat; die
    Kandidaten sind die VEREINIGUNG über alle Begriffe der Kette und tragen
    ihre Herkunft in `via`.

    Die Prüfung dahinter ist der Kern des Tickets: gewählt werden kann nur,
    was in `kandidaten` steht. Ein Treffer wird dem Begriff zugeordnet, unter
    dem er VORGELEGT wurde, nicht dem, den das Modell dazuschreibt — so ist
    `search_term` an der Vorschlagszeile immer wahr, auch wenn das Modell die
    Begriffe durcheinanderbringt.
    """
    mit_kandidaten = [a for a in aufgaben if a.get("kandidaten")]
    if not mit_kandidaten:
        # Ohne Kandidaten gäbe es nichts zu wählen. Das Modell zu fragen wäre
        # eine Einladung zum Erfinden — und würde Zeit kosten für nichts.
        return Auswahl()

    if spuren > 1 and len(mit_kandidaten) >= SPUREN_AB:
        return _choose_gleichzeitig(
            zugang, satz, mit_kandidaten, spuren, guided=guided,
            system=system, temperatur=temperatur, max_tokens=max_tokens,
            denken=denken, gericht=gericht)
    return _choose_einmal(zugang, satz, mit_kandidaten, guided=guided,
                          system=system, temperatur=temperatur,
                          max_tokens=max_tokens, denken=denken,
                          gericht=gericht)


def _choose_gleichzeitig(zugang, satz, mit_kandidaten, spuren, **rest) -> Auswahl:
    """Stufe 3 in mehreren Anfragen nebeneinander (WB-412).

    **Jede Spur bekommt NUR ihre eigenen Kandidaten**, und damit wird die
    Zusicherung aus `choose()` sogar schärfer: eine Antwort kann kein Produkt
    aus einer anderen Spur nennen. Sie wäre dort ohnehin verworfen worden —
    hier kommt sie gar nicht erst in Frage.

    Reihum verteilt (`[i::spuren]`) und nicht in Blöcken: die Begriffe stehen
    in der Reihenfolge der Zutatenliste, und ein Block wäre „alles für die
    Sauce" — vier Spuren mit sehr verschieden langen Kandidatenlisten. Wer
    reihum verteilt, gibt jeder Spur einen Querschnitt.

    `copy_context()` je Spur, damit der Span-Kontext mitreist: sonst hingen
    die Modellaufrufe nicht unter `chat.turn` und hiessen `ChatCompletion`
    statt `plan.choose` (siehe `obs.stufe(..., mehrfach=True)`).

    **Die Reihenfolge der Antwort bleibt die der Aufgaben.** Sie ist die
    Reihenfolge der Vorschlagsliste, und sie soll nicht davon abhängen,
    welche Spur zuerst fertig war.
    """
    teile = [t for t in ([a for a in mit_kandidaten[i::spuren]]
                         for i in range(spuren)) if t]
    if len(teile) < 2:
        return _choose_einmal(zugang, satz, mit_kandidaten, **rest)
    with cf.ThreadPoolExecutor(max_workers=len(teile)) as pool:
        laeufe = [pool.submit(contextvars.copy_context().run,
                              _choose_einmal, zugang, satz, teil, **rest)
                  for teil in teile]
        ergebnisse = [f.result() for f in laeufe]
    reihenfolge = {a["begriff"]: i for i, a in enumerate(mit_kandidaten)}
    gewaehlt = sorted((w for e in ergebnisse for w in e.gewaehlt),
                      key=lambda w: reihenfolge.get(w["begriff"], 0))
    return Auswahl(
        gewaehlt=gewaehlt,
        verworfen=[v for e in ergebnisse for v in e.verworfen],
        roh="\n".join(e.roh for e in ergebnisse if e.roh))


def _choose_einmal(zugang, satz: str, mit_kandidaten: list[dict], *,
                   guided: bool = True, system: str = SYSTEM_CHOOSE,
                   temperatur: float = TEMPERATUR,
                   max_tokens: int = MAX_TOKENS, denken: bool = DENKEN,
                   gericht: str | None = None) -> Auswahl:
    """Eine Anfrage an Stufe 3 — der Rumpf, den `choose()` einmal oder
    mehrfach ausführt."""
    erlaubt: dict[int, dict] = {}
    for aufgabe in mit_kandidaten:
        for p in aufgabe["kandidaten"]:
            erlaubt.setdefault(int(p["id"]),
                               {"begriff": aufgabe["begriff"], "produkt": p,
                                "menge": aufgabe.get("menge", 1)})

    antwort = _frage(zugang, system,
                     _choose_prompt(satz, mit_kandidaten, gericht),
                     SCHEMA_CHOOSE, "auswahl", guided, temperatur, max_tokens,
                     denken)
    roh = _eintraege(antwort, ("auswahl", "produkte", "items", "liste"))

    gewaehlt: list[dict] = []
    verworfen: list[dict] = []
    belegt: set[str] = set()
    for eintrag in roh:
        pid = _id(eintrag, ("produkt_id", "product_id", "id"))
        if pid is None or pid not in erlaubt:
            # HIER endet der Halluzinationsweg. Nicht reparieren, nicht die
            # nächstbeste ID nehmen — verwerfen und benennen. Der Begriff geht
            # trotzdem nicht verloren: der Aufrufer macht daraus einen
            # Freitext-Vorschlag.
            verworfen.append({
                "produkt_id": pid,
                "begriff": _text(eintrag, ("begriff", "suchbegriff", "term")),
                "grund": ("nicht vorgelegt" if pid is not None
                          else "keine brauchbare Produkt-id")})
            continue
        quelle = erlaubt[pid]
        if quelle["begriff"] in belegt:
            # Zwei Produkte für denselben Begriff: die Vorlage sagt „höchstens
            # eines". Das zweite ist keine Wahl mehr, sondern Beifang.
            verworfen.append({"produkt_id": pid, "begriff": quelle["begriff"],
                              "grund": "zweites Produkt für denselben Begriff"})
            continue
        belegt.add(quelle["begriff"])
        gewaehlt.append({
            "begriff": quelle["begriff"],
            "produkt": quelle["produkt"],
            "menge": _menge(eintrag, ("menge", "anzahl", "qty"),
                            vorgabe=quelle["menge"]),
        })
    return Auswahl(gewaehlt=gewaehlt, verworfen=verworfen, roh=antwort)


def _choose_prompt(satz: str, aufgaben: list[dict],
                   gericht: str | None = None) -> str:
    """Der Benutzerteil von Stufe 3: Satz, Gericht, Begriffe, Kandidaten.

    Der ursprüngliche Satz steht mit dabei, weil „Milch" allein nicht
    entscheidbar ist und „Milch für den Kaffee" schon.

    **Ein leerer `satz` lässt die Anfragezeile ganz weg** (WB-386), und das
    ist keine Bequemlichkeit, sondern das Ergebnis: mit dem Satz im Prompt
    wählte das Modell zu „Kartoffelpürree" 0 von 10 Begriffen, ohne ihn 10
    von 10 — dieselben Kandidaten. Der Rezeptweg nutzt es, der Modellweg
    nicht (siehe `chat.Chat._aus_quelle`).

    `gericht` — der Name des Rezepts, aus dem die Begriffe stammen — ist eine
    MESSSTELLE und kein Weg des Shops: `scripts/satz_probe.py` fährt damit
    die Varianten „Rezeptname statt Satz" und „Rezeptname zusätzlich".
    Gemessen am 2026-08-29 hat beides nicht getragen (90 bzw. 83 gewählte
    Begriffe gegen 94 ohne jeden Kopf), deshalb ruft der Shop ohne auf. Die
    Zeile steht hier, damit sich das nachfahren lässt, statt beim nächsten
    Verdacht neu erfunden zu werden.
    """
    liste = [{"begriff": a["begriff"], "menge": a.get("menge", 1),
              "kandidaten": [kandidat_kurz(p) for p in a["kandidaten"]]}
             for a in aufgaben]
    kopf = []
    if gericht and gericht.strip():
        kopf.append(f"Rezept: {gericht.strip()}")
    if satz and satz.strip():
        kopf.append(f"Anfrage: {satz.strip()}")
    return ("\n".join(kopf) + ("\n\n" if kopf else "")
            + "Vorgelegte Kandidaten:\n"
            + json.dumps(liste, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------
# Modellaufruf und Auspacken

# --------------------------------------------------------------------------
# Stufe 4 — plan.woche (2026-09-06-wochenplan-design.md, Abschnitt 4)
#
# Dieselbe tragende Regel wie in Stufe 3, nur eine Ebene höher: das Modell
# ORDNET Gerichte Tagen zu, und zwar nur Gerichte, die ihm vorgelegt wurden —
# die eigene Rezeptsammlung und die schon geholten Gerichte des Haushalts
# (`wochenplan.vorlage`). Keinen Katalog, keine Preise, keine Nährwerte.
#
# **Was es nicht macht: rechnen.** Portionen, Mengen, Packungen, Preis und
# Kochzeit summiert der Code (`wochenplan.liste`, `mengen`). Was hier
# zurückkommt, ist je Tag eine id und EIN Satz Begründung — Text, keine Zahl.
# Eine nicht vorgelegte id wird verworfen und gezählt, nie repariert.

SYSTEM_WOCHE = """\
Du planst die Abendessen eines Haushalts für einige Tage.

Du bekommst die Tage und eine Liste von Gerichten mit ID, Zeit, Portionen \
und Zutaten. Manche Tage sind schon festgelegt; du belegst nur die offenen.

Regeln:
- Du darfst AUSSCHLIESSLICH IDs verwenden, die in der Liste stehen. Eine \
ID, die dort nicht steht, wird verworfen.
- Belege so viele offene Tage wie möglich — je Tag genau ein Gericht, und \
kein Gericht zweimal in der Woche. Ein Tag bleibt nur leer, wenn kein \
Gericht der Liste mehr übrig ist, das an ihn passt.
- Bevorzuge Gerichte, die Zutaten teilen (weniger Reste), und solche, die \
den genannten Bestand aufbrauchen. Sorge für Abwechslung.
- Nenne keine ID, die nicht in der Liste steht. Rate nicht.
- „grund" ist EIN kurzer Satz, warum dieses Gericht an diesen Tag passt.

Antworte ausschliesslich als JSON:
{"tage": [{"tag": 1, "gericht_id": 12, "grund": "nutzt die Kartoffeln und teilt Zwiebeln mit Tag 3"}]}"""

#: **Gemessen am 06.09.** (Qwen3.8-27B, echte Datenbank, `scripts/plan_probe.py`):
#: mit „passt zu einem Tag nichts, lässt du ihn weg" belegte das Modell bei
#: drei vorgelegten Gerichten EINEN von drei Tagen und liess die anderen
#: leer — es las die Regel als Erlaubnis, sparsam zu sein. Seither steht die
#: Regel andersherum: so viele Tage wie möglich, leer nur, wenn nichts mehr
#: übrig ist. Die Zusicherung (keine fremde ID) hängt nicht daran; sie steht
#: im Code.

#: Länger als das ist keine Begründung, sondern ein Aufsatz — und auf einem
#: Telefon eine Zeile, die die Tagesliste sprengt.
MAX_GRUND = 160

#: Höchstens so viele Tage plant ein Zug (`wochenplan.rahmen.MAX_TAGE`).
MAX_TAGE = 14

#: Ein Tag ist eine Zeile von rund 30 Token; 14 Tage plus Verpackung passen
#: vielfach hinein. Deutlich kleiner als `MAX_TOKENS`, weil die Antwort hier
#: nicht mit den Zutaten wächst.
MAX_TOKENS_WOCHE = 800

SCHEMA_WOCHE = {
    "type": "object",
    "properties": {
        "tage": {
            "type": "array",
            "maxItems": MAX_TAGE,
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "integer", "minimum": 1,
                            "maximum": MAX_TAGE},
                    "gericht_id": {"type": "integer"},
                    "grund": {"type": "string", "maxLength": MAX_GRUND},
                },
                "required": ["tag", "gericht_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tage"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Wochenwahl:
    """Was Stufe 4 ergeben hat — samt dem, was verworfen wurde.

    `verworfen` ist hier dieselbe Zahl mit derselben Bedeutung wie in
    `Auswahl`: wie oft das Modell etwas nannte, das ihm nie vorgelegt wurde,
    oder einen Tag belegen wollte, der nicht offen war.
    """
    gewaehlt: list[dict] = field(default_factory=list)
    verworfen: list[dict] = field(default_factory=list)
    roh: str = ""


def _zeitwort(minuten) -> str:
    if not minuten:
        return "Zeit unbekannt"
    return f"{int(minuten)} Min."


def wochenvorlage(tage: list[dict], gerichte: list[dict], *,
                  personen=None, max_minuten=None,
                  bestand: list[str] | None = None,
                  vorlieben: str | None = None) -> str:
    """Der Benutzerteil von Stufe 4 — deterministisch, ohne Modell prüfbar.

    `tage` ist `[{"tag": 1, "name": "Mo 07.09.", "offen": True,
    "festgelegt": "Lasagne" | None}]`, `gerichte` die Vorlage aus
    `wochenplan.vorlage.gerichte` (id, name, minuten, servings, zutaten).
    """
    zeilen = []
    rahmen = []
    if personen:
        rahmen.append(f"{personen} Personen")
    if max_minuten:
        rahmen.append(f"höchstens {max_minuten} Minuten am Herd je Tag")
    if vorlieben:
        # Wörtlich aus dem Satz des Haushalts (Stufe 5), nicht erfunden.
        rahmen.append(f"Vorliebe: {vorlieben}")
    if rahmen:
        zeilen.append("Rahmen: " + ", ".join(rahmen))
    if bestand:
        zeilen.append("Noch da (soll aufgebraucht werden): " + ", ".join(bestand))
    zeilen.append("Tage:")
    for t in tage:
        if t.get("offen"):
            zeilen.append(f"- Tag {t['tag']} ({t['name']}): offen")
        else:
            was = t.get("festgelegt") or "auswärts, nichts kochen"
            zeilen.append(f"- Tag {t['tag']} ({t['name']}): festgelegt — {was}")
    zeilen.append("Gerichte zur Wahl:")
    for g in gerichte:
        zutaten = ", ".join(str(z) for z in (g.get("zutaten") or [])[:20])
        kopf = (f"- ID {int(g['id'])}: {g['name']} — {_zeitwort(g.get('minuten'))}"
                + (f", für {g['servings']} Portionen" if g.get("servings") else ""))
        zeilen.append(kopf + (f"; Zutaten: {zutaten}" if zutaten else ""))
    return "\n".join(zeilen)


def woche(zugang, tage: list[dict], gerichte: list[dict], *,
          personen=None, max_minuten=None, bestand: list[str] | None = None,
          vorlieben: str | None = None,
          guided: bool = True, system: str = SYSTEM_WOCHE,
          temperatur: float = TEMPERATUR,
          max_tokens: int = MAX_TOKENS_WOCHE,
          denken: bool = DENKEN) -> Wochenwahl:
    """Ordnet offenen Tagen je ein vorgelegtes Gericht zu.

    Die Prüfung dahinter ist der Kern: gewählt werden kann nur, was in
    `gerichte` steht, belegt nur ein Tag, der `offen` ist, und jedes Gericht
    nur einmal. Alles andere landet in `verworfen` mit Grund — und wird nicht
    durch das nächstbeste ersetzt.

    Ohne offenen Tag oder ohne Gericht wird gar nicht gefragt: das Modell zu
    fragen wäre eine Einladung zum Erfinden.
    """
    offen = {int(t["tag"]) for t in tage if t.get("offen")}
    erlaubt = {int(g["id"]): g for g in gerichte}
    if not offen or not erlaubt:
        return Wochenwahl()
    schon = {int(t["festgelegt_id"]) for t in tage
             if t.get("festgelegt_id") is not None}

    antwort = _frage(zugang, system,
                     wochenvorlage(tage, gerichte, personen=personen,
                                   max_minuten=max_minuten, bestand=bestand,
                                   vorlieben=vorlieben),
                     SCHEMA_WOCHE, "wochenplan", guided, temperatur,
                     max_tokens, denken)
    roh = _eintraege(antwort, ("tage", "plan", "wochenplan", "days"))

    gewaehlt: list[dict] = []
    verworfen: list[dict] = []
    belegt: set[int] = set()
    benutzt: set[int] = set(schon)
    for eintrag in roh:
        tag = _id(eintrag, ("tag", "day", "pos"))
        rid = _id(eintrag, ("gericht_id", "recipe_id", "rezept_id", "id"))
        grund = _text(eintrag, ("grund", "reason", "warum"))[:MAX_GRUND]
        if rid is None or rid not in erlaubt:
            # HIER endet der Halluzinationsweg — wie in `choose()`.
            verworfen.append({"tag": tag, "recipe_id": rid,
                              "grund": ("nicht vorgelegt" if rid is not None
                                        else "keine brauchbare Gericht-id")})
            continue
        if tag is None or tag not in offen:
            verworfen.append({"tag": tag, "recipe_id": rid,
                              "grund": "Tag nicht offen"})
            continue
        if tag in belegt:
            verworfen.append({"tag": tag, "recipe_id": rid,
                              "grund": "zweites Gericht für denselben Tag"})
            continue
        if rid in benutzt:
            verworfen.append({"tag": tag, "recipe_id": rid,
                              "grund": "Gericht steht schon im Plan"})
            continue
        belegt.add(tag)
        benutzt.add(rid)
        gewaehlt.append({"tag": tag, "recipe_id": rid, "grund": grund or None,
                         "name": erlaubt[rid].get("name")})
    gewaehlt.sort(key=lambda w: w["tag"])
    return Wochenwahl(gewaehlt=gewaehlt, verworfen=verworfen, roh=antwort)


# --------------------------------------------------------------------------
# Stufe 5 — plan.rahmen (2026-09-10): der Rahmen aus EINEM Satz
#
# Aaron: „eine Mahlzeit pro Tag, 700 Kalorien, viel Protein, Kartoffeln, Eier
# und Nudeln sind da" — getippt in ein Chatfeld, und die Maske darunter füllt
# sich. `wochenplan.rahmen` hat lange begründet, warum Zahlen aus Feldern
# kommen und nicht aus einem Satz: eine Zahl, die ein Modell aus einem Satz
# liest, kann es erfunden haben. Der Einwand bleibt — und wird hier
# beantwortet wie in Stufe 3 die erfundene Produkt-id: **jede Zahl, die das
# Modell liefert, muss wörtlich im Satz stehen**, jeder Bestandsname ein
# Stück des Satzes sein, jede Vorliebe ein Wort daraus. Was nicht belegt ist,
# wird verworfen und gezählt (`zettel.rahmen.rejected`), nie übernommen.
#
# Das Modell darf hier also LESEN, nicht wissen. „700 Kalorien" -> kcal 700
# ist Lesen; „viel Protein" -> kcal 2000 wäre Wissen, und das wird verworfen.

SYSTEM_RAHMEN = """\
Du liest einen Satz eines Haushalts über die kommende Woche und trägst ein, \
was DARIN STEHT — in Felder. Nichts ergänzen, nichts schätzen.

Felder (null, wenn der Satz nichts dazu sagt):
- tage: Zahl der Tage
- personen: Zahl der Personen
- max_minuten: höchstens Minuten am Herd je Tag
- budget_euro: Budget für die Woche in Euro
- kcal: Kalorien je Person und Tag
- mahlzeiten_pro_tag: Mahlzeiten je Tag
- vorlieben: EIN Wort oder kurzer Ausdruck aus dem Satz (z. B. „viel Protein", \
„vegetarisch"), sonst null
- bestand: was noch da ist, als Liste — jeder Eintrag WÖRTLICH so, wie er im \
Satz steht, mit Menge, wenn eine dasteht („6 Eier", „Kartoffeln")

Jede Zahl muss im Satz vorkommen. Steht keine Zahl da, bleibt das Feld null.

Antworte ausschliesslich als JSON:
{"tage": null, "personen": null, "max_minuten": null, "budget_euro": null, \
"kcal": 700, "mahlzeiten_pro_tag": 1, "vorlieben": "viel Protein", \
"bestand": ["Kartoffeln", "Eier", "Nudeln"]}"""

SCHEMA_RAHMEN = {
    "type": "object",
    "properties": {
        "tage": {"type": ["integer", "null"]},
        "personen": {"type": ["integer", "null"]},
        "max_minuten": {"type": ["integer", "null"]},
        "budget_euro": {"type": ["number", "null"]},
        "kcal": {"type": ["integer", "null"]},
        "mahlzeiten_pro_tag": {"type": ["integer", "null"]},
        "vorlieben": {"type": ["string", "null"], "maxLength": 60},
        "bestand": {"type": "array", "maxItems": 30,
                    "items": {"type": "string", "maxLength": 60}},
    },
    "required": ["tage", "personen", "max_minuten", "budget_euro", "kcal",
                 "mahlzeiten_pro_tag", "vorlieben", "bestand"],
    "additionalProperties": False,
}

MAX_TOKENS_RAHMEN = 300

#: Zahlwörter, die als Beleg für eine Zahl gelten — „eine Mahlzeit" belegt
#: die 1, „zwei Personen" die 2. Deutsch und Englisch, bis vierzehn: mehr
#: Tage plant der Planer ohnehin nicht.
ZAHLWOERTER = {
    1: ("ein", "eine", "einen", "einem", "einer", "eins", "one", "a"),
    2: ("zwei", "two"), 3: ("drei", "three"), 4: ("vier", "four"),
    5: ("fünf", "fuenf", "five"), 6: ("sechs", "six"), 7: ("sieben", "seven"),
    8: ("acht", "eight"), 9: ("neun", "nine"), 10: ("zehn", "ten"),
    11: ("elf", "eleven"), 12: ("zwölf", "zwoelf", "twelve"),
    13: ("dreizehn", "thirteen"), 14: ("vierzehn", "fourteen"),
}

#: Eine Vorliebe gilt als belegt, wenn ein Wort ihrer Gruppe im Satz steht —
#: „eiweissreich" ist belegt durch „Protein", nicht nur durch „Eiweiss".
VORLIEBEN_GRUPPEN = (
    ("protein", "eiweiss", "eiweiß"),
    ("vegetar",), ("vegan",), ("gluten",), ("laktos", "lactose"),
    ("fisch", "fish"), ("fleisch", "meat"), ("scharf", "spicy"),
    ("günstig", "guenstig", "billig", "cheap"), ("schnell", "quick", "fast"),
    ("leicht", "light"), ("kohlenhydrat", "carb"), ("zucker", "sugar"),
)


@dataclass
class Rahmenlesung:
    """Was Stufe 5 aus dem Satz las — und was davon verworfen wurde."""
    werte: dict = field(default_factory=dict)
    verworfen: list[dict] = field(default_factory=list)
    roh: str = ""


def _zahl_im_satz(zahl, satz_klein: str) -> bool:
    """Steht diese Zahl im Satz — als Ziffern oder als Zahlwort?"""
    try:
        wert = float(zahl)
    except (TypeError, ValueError):
        return False
    woerter = re.findall(r"[a-zäöüß]+|\d+(?:[.,]\d+)?", satz_klein)
    if wert == int(wert):
        ziffern = str(int(wert))
        if ziffern in woerter:
            return True
        for w in ZAHLWOERTER.get(int(wert), ()):
            if w in woerter:
                return True
        return False
    return any(w.replace(",", ".") == f"{wert:g}" for w in woerter)


def _vorliebe_im_satz(vorliebe: str, satz_klein: str) -> bool:
    v = vorliebe.lower()
    for gruppe in VORLIEBEN_GRUPPEN:
        if any(g in v for g in gruppe) and any(g in satz_klein for g in gruppe):
            return True
    # Sonst muss ein tragendes Wort der Vorliebe selbst im Satz stehen.
    return any(w in satz_klein for w in re.findall(r"[a-zäöüß]{5,}", v))


def rahmen_lesen(zugang, satz: str, *, guided: bool = True,
                 system: str = SYSTEM_RAHMEN, temperatur: float = TEMPERATUR,
                 max_tokens: int = MAX_TOKENS_RAHMEN,
                 denken: bool = DENKEN) -> Rahmenlesung:
    """Ein Satz -> geprüfte Felder. Das Modell liest, der Code belegt.

    Jede Zahl muss im Satz stehen (Ziffern oder Zahlwort), jeder
    Bestandseintrag ein Stück des Satzes sein, jede Vorliebe ein Wort
    daraus. Unbelegtes landet in `verworfen` mit Grund — und wird nicht durch
    eine Vorgabe ersetzt; die Vorgaben setzt `wochenplan.rahmen.aus_lesung`,
    und die sind dann sichtbar die des Formulars, nicht das Wissen des
    Modells.
    """
    satz = " ".join(str(satz or "").split())
    if not satz:
        return Rahmenlesung()
    antwort = _frage(zugang, system, satz, SCHEMA_RAHMEN, "rahmen", guided,
                     temperatur, max_tokens, denken)
    roh = _json_wert(antwort)
    if not isinstance(roh, dict):
        raise PlanFehler("Die Antwort auf den Satz ist kein Objekt.")
    klein = satz.lower()
    werte: dict = {}
    verworfen: list[dict] = []
    for feld in ("tage", "personen", "max_minuten", "budget_euro", "kcal",
                 "mahlzeiten_pro_tag"):
        wert = roh.get(feld)
        if wert is None or wert == "":
            continue
        if _zahl_im_satz(wert, klein):
            werte[feld] = wert
        else:
            # HIER endet der Halluzinationsweg für Zahlen: eine Zahl, die
            # nicht im Satz steht, hat das Modell gewusst, nicht gelesen.
            verworfen.append({"feld": feld, "wert": wert,
                              "grund": "Zahl steht nicht im Satz"})
    bestand = []
    for eintrag in roh.get("bestand") or []:
        text = " ".join(str(eintrag or "").split())
        if not text:
            continue
        if text.lower() in klein:
            bestand.append(text)
        else:
            verworfen.append({"feld": "bestand", "wert": text,
                              "grund": "steht nicht im Satz"})
    werte["bestand"] = bestand
    vorliebe = roh.get("vorlieben")
    if vorliebe:
        vorliebe = " ".join(str(vorliebe).split())
        if _vorliebe_im_satz(vorliebe, klein):
            werte["vorlieben"] = vorliebe
        else:
            verworfen.append({"feld": "vorlieben", "wert": vorliebe,
                              "grund": "kein Wort davon im Satz"})
    return Rahmenlesung(werte=werte, verworfen=verworfen, roh=antwort)


def _frage(zugang, system: str, benutzer: str, schema: dict, wurzel: str,
           guided: bool, temperatur: float, max_tokens: int,
           denken: bool = DENKEN) -> str:
    """Ein Aufruf ans Modell. Gibt `content` zurück, roh.

    Das Schema geht als `response_format` mit — `{"type": "json_schema",
    "json_schema": {"name": wurzel, "schema": …}}`, das OpenAI-Wire-Format,
    das vLLM seit 0.6 versteht. **Nicht mehr als `guided_json` in
    `extra_body`:** gemessen am 2026-09-05 gegen vLLM 0.27.1 wird das dort
    stillschweigend ignoriert — HTTP 200, Prosa statt JSON, keine Warnung.
    Die Box lief seit dem 01.09. auf 0.27.1; Stufe 1 und 3 waren dort also
    vier Tage lang unbeschränkt, und keine Messung hat es gezeigt, weil das
    Modell bei Temperatur 0 das Schema ohnehin trifft (EVALS.md: „turning
    guided_json off moved none of the three scores"). Genau deshalb steht die
    Prüfung gegen die Kandidaten im Code und nicht in der Serveroption.

    `chat_template_kwargs` geht weiter über `extra_body` — dafür gibt es kein
    Standardfeld. Ist Guided Decoding abgeschaltet, bleibt nur der Prompt und
    das nachsichtige Parsen unten; beides muss auch allein tragen, denn ein
    anderer Server (Spec 8.3: „lokales Qwen gegen ein grösseres Modell")
    kennt auch `response_format` möglicherweise nicht.
    """
    weitere = {"temperature": temperatur, "max_tokens": max_tokens}
    extra = {}
    if guided:
        weitere["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": wurzel, "schema": schema}}
    if not denken:
        extra["chat_template_kwargs"] = {"enable_thinking": False}
    if extra:
        weitere["extra_body"] = extra
    antwort = zugang.chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": benutzer}], **weitere)
    inhalt = (antwort.content or "").strip()
    if not inhalt:
        raise PlanFehler(
            "Das Modell hat nichts geantwortet"
            + (f" (finish_reason={antwort.finish_reason})."
               if antwort.finish_reason else "."))
    if antwort.finish_reason == "length":
        # Die teuerste Verwechslung dieses Moduls, einmal bezahlt: eine
        # abgeschnittene Antwort ist syntaktisch kein JSON und sieht deshalb
        # aus wie ein Modell, das sich nicht an das Format hält. Sie ist aber
        # ein Budgetproblem.
        #
        # ZWEI Ursachen, und die Meldung nennt beide. Früher stand hier nur
        # „denkt das Modell mit?" — das war die Ursache in WB-327 und schickte
        # bei WB-363 (Pho, 20 Zutaten) den Leser an die falsche Stelle: das
        # Denken ist längst je Anfrage abgeschaltet, es waren schlicht viele
        # Zutaten. Eine Fehlermeldung, die nur eine von zwei Ursachen nennt,
        # kostet mehr Zeit als eine, die keine nennt.
        gerettet = _vollstaendige_eintraege(inhalt)
        if gerettet:
            # Ein Abbruch nach 18 von 20 Zutaten ist ein weiches Problem; alles
            # wegzuwerfen wäre eine harte Reaktion darauf. Was vollständig
            # dasteht, wird verwendet — und der Aufrufer erfährt davon, damit
            # aus „abgeschnitten" nicht stillschweigend „weniger Zutaten" wird.
            return _AbgeschnittenerText(json.dumps(gerettet, ensure_ascii=False),
                                        len(gerettet), max_tokens)
        raise PlanFehler(
            f"Die Antwort wurde nach {max_tokens} Token abgeschnitten "
            "(finish_reason=length) und es stand noch kein vollständiger "
            "Eintrag darin. Das Format ist nicht kaputt, sondern das Budget zu "
            "klein — entweder hat das Gericht sehr viele Zutaten (siehe "
            "MAX_TOKENS, rund 34 Token je Zutat), oder das Modell denkt mit "
            "und die Denk-Token zählen gegen dasselbe Budget (siehe DENKEN).")
    return inhalt


class _AbgeschnittenerText(str):
    """Der gerettete Teil einer abgeschnittenen Antwort.

    Ein `str`, damit alle Aufrufer unverändert weiterlesen können — mit zwei
    Feldern daneben, damit der Abbruch nicht unsichtbar wird. Ein stiller
    Verlust wäre schlimmer als der Abbruch: die Nutzerin bekäme eine kürzere
    Zutatenliste und keinen Hinweis, dass etwas fehlt.
    """

    abgeschnitten = True

    def __new__(cls, text: str, gerettet: int, budget: int):
        selbst = super().__new__(cls, text)
        selbst.gerettet = gerettet
        selbst.budget = budget
        return selbst


def _vollstaendige_eintraege(text: str) -> list:
    """Aus einer abgeschnittenen Antwort die Einträge, die noch ganz sind.

    Zeichenweise über die Klammertiefe statt mit einem regulären Ausdruck: ein
    Produktname darf geschweifte Klammern und Anführungszeichen enthalten, und
    eine Zeichenkette mit `\\"` darin bringt jede naive Suche durcheinander.
    """
    sauber = _FENCE.sub("", text).strip()
    anfang = sauber.find("[")
    if anfang == -1:
        return []
    eintraege, tiefe, start = [], 0, None
    in_text, maskiert = False, False
    for i, z in enumerate(sauber[anfang:], anfang):
        if maskiert:
            maskiert = False
            continue
        if z == "\\":
            maskiert = True
            continue
        if z == '"':
            in_text = not in_text
            continue
        if in_text:
            continue
        if z == "{":
            if tiefe == 0:
                start = i
            tiefe += 1
        elif z == "}":
            tiefe -= 1
            if tiefe == 0 and start is not None:
                try:
                    eintraege.append(json.loads(sauber[start:i + 1]))
                except ValueError:
                    pass
                start = None
    return eintraege


#: Ein Codefence um die Antwort. Modelle schreiben ihn auch dann, wenn im
#: Prompt „ausschliesslich JSON" steht.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _json_wert(text: str):
    """Freitext -> JSON-Wert. Wirft `PlanFehler`, wenn keiner drinsteckt."""
    sauber = _FENCE.sub("", text).strip()
    try:
        return json.loads(sauber)
    except ValueError:
        pass
    # Zweiter Versuch: das erste vollständige Objekt oder Array herausschneiden.
    # Modelle stellen der Antwort gern einen Satz voran („Hier ist die Liste:").
    for auf, zu in (("{", "}"), ("[", "]")):
        anfang = sauber.find(auf)
        ende = sauber.rfind(zu)
        if anfang != -1 and ende > anfang:
            try:
                return json.loads(sauber[anfang:ende + 1])
            except ValueError:
                continue
    raise PlanFehler(f"Die Antwort des Modells ist kein JSON: {text[:200]!r}")


def _eintraege(text: str, schluessel: tuple[str, ...]) -> list[dict]:
    """Die Liste aus der Antwort, egal ob blank oder in ein Objekt gewickelt.

    Ein leeres Array ist ausdrücklich erlaubt und kein Fehler: „zu diesem
    Begriff passt nichts" ist eine gültige Aussage. Ob daraus eine leere
    Vorschlagsliste werden darf, entscheidet der Aufrufer.
    """
    try:
        wert = _json_wert(text)
    except PlanFehler:
        # WB-363: Eine unvollständige Antwort ist kein Formatfehler, sondern
        # ein Abbruch — und der hat ZWEI Ursachen, die von aussen gleich
        # aussehen:
        #
        #   * das Budget riss (finish_reason=length),
        #   * oder das Modell hörte mitten in der Struktur von selbst auf
        #     (finish_reason=stop!). Gemessen an „alles für Pho": das Modell
        #     geriet in eine Schleife und erzeugte immer obskurere
        #     Rindfleischteile bis zum „Rinderzungenkotelett", 1342 Token,
        #     dann Ende mitten im Wort. Guided Decoding verhindert das NICHT.
        #
        # Deshalb wird hier gerettet und nicht erst bei `length`: was
        # vollständig dasteht, ist brauchbar, egal warum der Rest fehlt.
        # Achtzehn von zwanzig Zutaten sind besser als eine Fehlermeldung.
        gerettet = _vollstaendige_eintraege(text)
        if not gerettet:
            raise
        return [e for e in gerettet if isinstance(e, dict)]
    if isinstance(wert, dict):
        for k in schluessel:
            if k in wert:
                wert = wert[k]
                break
        else:
            # Ein einzelnes Objekt ohne Hülle — kommt vor, wenn nur eine Zutat
            # gefunden wurde.
            wert = [wert]
    if not isinstance(wert, list):
        raise PlanFehler(
            f"Erwartet wurde eine Liste, bekommen: {type(wert).__name__}.")
    return [e for e in wert if isinstance(e, dict)]


def _text(eintrag: dict, namen: tuple[str, ...]) -> str:
    for n in namen:
        wert = eintrag.get(n)
        if isinstance(wert, str) and wert.strip():
            return " ".join(wert.split())
    return ""


def _menge(eintrag: dict, namen: tuple[str, ...], vorgabe: int = 1) -> int:
    """Menge aus dem Eintrag, notfalls die Vorgabe. Immer 1..MAX_MENGE.

    Wirft nie: eine Menge, die das Modell als `"zwei"` schreibt, darf keinen
    Einkauf verhindern.
    """
    for n in namen:
        wert = eintrag.get(n)
        try:
            zahl = int(wert)
        except (TypeError, ValueError):
            continue
        return max(1, min(MAX_MENGE, zahl))
    return max(1, min(MAX_MENGE, vorgabe))


def _id(eintrag: dict, namen: tuple[str, ...]) -> int | None:
    """Produkt-id aus dem Eintrag, oder `None`.

    `True` ist in Python eine 1 — das wäre hier eine gültige Produkt-id aus
    einem Booleschen Wert, und damit genau die Sorte stiller Fehlgriff, die
    dieses Modul verhindern soll.
    """
    for n in namen:
        wert = eintrag.get(n)
        if isinstance(wert, bool):
            continue
        try:
            return int(wert)
        except (TypeError, ValueError):
            continue
    return None
