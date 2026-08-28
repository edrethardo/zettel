"""Die beiden Modellstufen des Agenten: `plan.extract` und `plan.choose`.

Die tragende Regel dieses Moduls (Spec 6): **das Modell erfindet niemals
Produkte.** Ein LLM, das Produkt-IDs frei ausgeben darf, halluziniert
Produkt-IDs — und eine halluzinierte ID sieht in der Datenbank aus wie eine
echte, bis jemand im Laden vor einem Regal steht.

Deshalb sind die zwei Aufrufe hier streng getrennt:

* `extract()` bekommt den Satz und darf **nur Suchbegriffe mit Mengen**
  zurückgeben. Es sieht keinen einzigen Katalogeintrag, kann also keinen
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

#: Wie viele Kandidaten je Begriff vorgelegt werden. Spec 8.3 will genau diese
#: Zahl später als Stellschraube gegen 20 vergleichen — sie steht deshalb hier
#: als Vorgabe und nicht als Literal im Code.
KANDIDATEN = 5

#: Temperatur 0: derselbe Satz soll dieselben Begriffe ergeben. Ein Agent, der
#: bei jedem Aufruf etwas anderes tut, ist in Experiments (Spec 8.3) nicht
#: vergleichbar.
TEMPERATUR = 0.0

#: Reichlich Luft für zwanzig Begriffe, aber nicht unbegrenzt: läuft eine
#: Antwort in die Länge, ist sie ohnehin kaputt, und das Warten kostet die
#: Nutzerin Zeit.
MAX_TOKENS = 800

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
von Suchbegriffen für einen Lebensmittel-Katalog.

Regeln:
- Nur Suchbegriffe und Mengen. KEINE Produktnamen, KEINE Marken, KEINE Nummern.
- Kurze, allgemeine Begriffe. Ein bis zwei Wörter. Der Katalog sucht über \
Wortanfänge: „Tomaten" findet mehr als „passierte Tomaten aus der Dose".
- Bei einem Gericht: die Zutaten, die man dafür kaufen muss. Was in jedem \
Haushalt steht (Salz, Pfeffer, Wasser, Öl), lässt du weg.
- Die Menge ist die Anzahl Packungen, die gekauft werden soll. Im Zweifel 1.
- Nichts erfinden, was im Satz nicht vorkommt oder zum Gericht nicht gehört.

Antworte ausschliesslich als JSON:
{"begriffe": [{"begriff": "Hackfleisch", "menge": 1}, \
{"begriff": "passierte Tomaten", "menge": 2}]}"""

SCHEMA_EXTRACT = {
    "type": "object",
    "properties": {
        "begriffe": {
            "type": "array",
            "maxItems": MAX_BEGRIFFE,
            "items": {
                "type": "object",
                "properties": {
                    "begriff": {"type": "string"},
                    "menge": {"type": "integer", "minimum": 1,
                              "maximum": MAX_MENGE},
                },
                "required": ["begriff", "menge"],
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
    """Satz -> `[{"begriff": …, "menge": …}, …]`. Nie Produkt-IDs.

    Wirft `PlanFehler`, wenn nichts Brauchbares zurückkam — kein JSON, ein
    leeres Array, ein falscher Typ. Der Aufrufer macht daraus eine Meldung im
    Chat; er baut daraus NICHTS zusammen, was das Modell nicht gesagt hat.
    """
    text = (satz or "").strip()
    if not text:
        raise PlanFehler("Leere Anfrage — dazu gibt es nichts zu suchen.")
    antwort = _frage(zugang, system, text, SCHEMA_EXTRACT, "begriffe",
                     guided, temperatur, max_tokens, denken)
    roh = _eintraege(antwort, ("begriffe", "suchbegriffe", "items", "liste"))
    begriffe: list[dict] = []
    gesehen: set[str] = set()
    for eintrag in roh:
        begriff = _text(eintrag, ("begriff", "suchbegriff", "term", "name",
                                  "query"))
        if not begriff:
            # Eine Zeile ohne Begriff ist keine halbe Zutat, sondern Rauschen.
            continue
        schluessel = begriff.casefold()
        if schluessel in gesehen:
            # Zweimal derselbe Begriff hiesse zweimal dieselbe Suche und zwei
            # gleiche Vorschlagszeilen. Die Menge steht in der ersten.
            continue
        gesehen.add(schluessel)
        begriffe.append({"begriff": begriff,
                         "menge": _menge(eintrag, ("menge", "anzahl", "qty",
                                                   "quantity"))})
        if len(begriffe) >= MAX_BEGRIFFE:
            break
    if not begriffe:
        raise PlanFehler(
            "Das Modell hat aus dem Satz keine Suchbegriffe gemacht.")
    return begriffe


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
    """
    return {"id": int(p["id"]), "name": p.get("name") or "",
            "gebinde": p.get("unit_text") or "",
            "preis_cent": p.get("price_cents")}


def choose(zugang, satz: str, aufgaben: list[dict], *, guided: bool = True,
           system: str = SYSTEM_CHOOSE, temperatur: float = TEMPERATUR,
           max_tokens: int = MAX_TOKENS, denken: bool = DENKEN) -> Auswahl:
    """Wählt je Begriff höchstens ein vorgelegtes Produkt.

    `aufgaben` ist `[{"begriff", "menge", "kandidaten": [Produkt, …]}, …]` —
    genau das, was `catalog.search` zurückgegeben hat.

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
        # ein Budgetproblem — meist Denk-Token, die gegen `max_tokens` zählen
        # (siehe DENKEN). Wer diese Meldung liest, sucht nicht am Prompt.
        raise PlanFehler(
            f"Die Antwort wurde nach {max_tokens} Token abgeschnitten "
            "(finish_reason=length). Das Format ist damit nicht kaputt, "
            "sondern unvollständig — denkt das Modell mit? Denk-Token zählen "
            "gegen dasselbe Budget.")
    return inhalt


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
    wert = _json_wert(text)
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
