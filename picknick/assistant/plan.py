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
Antwort per `guided_json` in ein Schema zwingen; damit ist sie sicher gültiges
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

import json
import re
from dataclasses import dataclass, field

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

Antworte ausschliesslich als JSON:
{"begriffe": [{"suchbegriffe": ["Rinderhackfleisch", "Hackfleisch"], \
"menge": 1}, {"suchbegriffe": ["passierte Tomaten", "Tomaten"], "menge": 2}]}"""

SCHEMA_EXTRACT = {
    "type": "object",
    "properties": {
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
    "required": ["begriffe"],
    "additionalProperties": False,
}


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
    """
    text = (satz or "").strip()
    if not text:
        raise PlanFehler("Leere Anfrage — dazu gibt es nichts zu suchen.")
    antwort = _frage(zugang, system, text, SCHEMA_EXTRACT, "begriffe",
                     guided, temperatur, max_tokens, denken)
    roh = _eintraege(antwort, ("begriffe", "zutaten", "items", "liste"))
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
    if not zutaten:
        raise PlanFehler(
            "Das Modell hat aus dem Satz keine Suchbegriffe gemacht.")
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


def choose(zugang, satz: str, aufgaben: list[dict], *, guided: bool = True,
           system: str = SYSTEM_CHOOSE, temperatur: float = TEMPERATUR,
           max_tokens: int = MAX_TOKENS, denken: bool = DENKEN) -> Auswahl:
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

    erlaubt: dict[int, dict] = {}
    for aufgabe in mit_kandidaten:
        for p in aufgabe["kandidaten"]:
            erlaubt.setdefault(int(p["id"]),
                               {"begriff": aufgabe["begriff"], "produkt": p,
                                "menge": aufgabe.get("menge", 1)})

    antwort = _frage(zugang, system, _choose_prompt(satz, mit_kandidaten),
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


def _choose_prompt(satz: str, aufgaben: list[dict]) -> str:
    """Der Benutzerteil von Stufe 3: Satz, Begriffe, Kandidaten als JSON.

    Der ursprüngliche Satz steht mit dabei, weil „Milch" allein nicht
    entscheidbar ist und „Milch für den Kaffee" schon.
    """
    liste = [{"begriff": a["begriff"], "menge": a.get("menge", 1),
              "kandidaten": [kandidat_kurz(p) for p in a["kandidaten"]]}
             for a in aufgaben]
    return (f"Anfrage: {satz.strip()}\n\n"
            "Vorgelegte Kandidaten:\n"
            + json.dumps(liste, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------
# Modellaufruf und Auspacken

def _frage(zugang, system: str, benutzer: str, schema: dict, wurzel: str,
           guided: bool, temperatur: float, max_tokens: int,
           denken: bool = DENKEN) -> str:
    """Ein Aufruf ans Modell. Gibt `content` zurück, roh.

    `guided_json` und `chat_template_kwargs` gehen über `extra_body` — vLLM
    erwartet sie dort, und der Client reicht `weitere` unverändert ans SDK
    durch. Ist Guided Decoding abgeschaltet, bleibt nur der Prompt und das
    nachsichtige Parsen unten; beides muss auch allein tragen, denn ein
    anderer Server (Spec 8.3: „lokales Qwen gegen ein grösseres Modell")
    kennt `guided_json` möglicherweise nicht.
    """
    weitere = {"temperature": temperatur, "max_tokens": max_tokens}
    extra = {}
    if guided:
        extra["guided_json"] = schema
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
