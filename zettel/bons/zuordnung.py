"""Vom Bon-Text zum Katalogprodukt (WB-358).

Ein Kassenbon schreibt GROSS, ohne Umlaute und hart abgekürzt:
`SCHIN.-KAE. CRO.`, `HAEHN.BRUST PAP`, `SCHOTT. CHEDDAR`. Der Katalog schreibt
das Gegenteil. Dazwischen steht dieses Modul.

**Gemessen, nicht vermutet** (2026-08-28, echter Rewe-Bon, 10.361 Produkte,
`Qwen3.8-27B-Instruct`, 29 s für 18 Zeilen in einem Aufruf):

* Naiv über das erste Wort fanden 17 von 19 Zeilen irgendein Produkt — aber
  mehrere das falsche. `SCHIN.-KAE. CRO.` landete beim Schinken Spicker,
  `MAISST.SALT PEP.` bei Maisstärke.
* Mit diesem Modul fanden **18 von 18** Zeilen ein Produkt, und die vorher
  falschen sitzen: `SCHIN.-KAE. CRO.` -> „Schinken-Käse-Croissant" ->
  Schinken-Käse Croissant, `HAEHN.BRUST PAP` -> „Hähnchenbrust paniert" ->
  Bedford Nuggets paniert, `SCHOTT. CHEDDAR` -> „Schottischer Cheddar" ->
  Miil Cheddar.
* **Richtig sind davon 14, nicht 18.** Ehrlich benannt, weil „findet etwas"
  und „findet das Richtige" zwei verschiedene Zahlen sind:
  `OLD AMSTERDAM` -> Singha Bier, `GEFLUEGELROLLE` -> Prinzen Rolle,
  `KABANOS KAESE` -> Kabanos Klassik. Die ersten beiden sind Lücken im
  Katalog (weder Old Amsterdam noch eine Geflügelrolle stehen darin), der
  dritte auch (es gibt Kabanos nur als Klassik und Paprika). Genau dann greift
  der ALLGEMEINE letzte Begriff der Kette — „Bier", „Rolle" —, und der findet
  immer irgendetwas. Dasselbe Entgleisen ist an `plan.MAX_KETTE` beschrieben.
* Sichtbar ist es trotzdem: an der Zeile steht, MIT WELCHEM BEGRIFF gesucht
  wurde. „gesucht „Bier"" neben „OLD AMSTERDAM" ist die Warnung, die ein
  „Ja" verhindert. Deshalb steht sie dort und nicht nur im Trace.
* Ein einziger fehlender Umlaut kostet einen Treffer: in einem Lauf schrieb
  das Modell „Käsebrotchen" statt „Käsebrötchen" und fand nichts, obwohl der
  Katalog das Produkt führt. Der Prompt sagt es deshalb ausdrücklich — die
  Suche faltet ä auf „ae", und „Brotchen" ist davon zu weit weg.

**Das Modell sucht nicht, es übersetzt.** Es bekommt den Katalog nie zu sehen
und darf keine Produkt-id nennen — dieselbe Regel wie in `assistant.plan`, aus
demselben Grund: eine erfundene id sieht in der Datenbank aus wie eine echte.
Gesucht wird mit `catalog.search.suche_kette()`, also mit derselben Suche wie
im Chat. Eine zweite Suchlogik gibt es nicht.

**Und es wählt auch nicht.** Anders als im Chat (Spec 6, Stufe 3) folgt hier
kein zweiter Modellaufruf, der aus den Kandidaten auswählt. Genommen wird der
erste Kandidat der Kette, und die Wahl trifft anschliessend ein MENSCH — Zeile
für Zeile, direkt auf der Bon-Seite. Ein Modellaufruf, der eine Entscheidung
vorwegnimmt, die eine Sekunde später ohnehin von Hand fällt, kostet Wartezeit
und bringt nichts; die Kandidatenliste steht zur Korrektur ohnehin daneben.

**Nicht-Artikel ein zweites Mal.** `bons.zerlegen` wirft Pfand und Rabatt
schon über die Namensliste weg. Das Modell wird trotzdem gefragt, ob eine
Zeile ein Artikel ist: die Liste dort kennt nur, woran jemand gedacht hat, und
ein Laden, der seinen Rabatt anders nennt, rutscht durch. Was das Modell als
Nicht-Artikel meldet, bekommt keinen Produktvorschlag — verworfen wird es
damit nicht, das bleibt Sache der Nutzerin.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from zettel import obs
from zettel.assistant import plan
from zettel.bons import kaeufe
from zettel.catalog import search
from zettel.llm import wake
from zettel.llm.client import ModellNichtErreichbar

#: Wie viele Bon-Zeilen ein Aufruf höchstens mitnimmt. Ein Wocheneinkauf hat
#: 20 bis 40 Zeilen; 80 sind reichlich Luft. Darüber wird nicht stillschweigend
#: gekürzt, sondern in Blöcken gefragt (siehe `deuten`) — eine abgeschnittene
#: Liste hiesse, dass die letzten Käufe des Bons ohne Zuordnung dastehen und
#: niemand erführe warum.
BLOCK = 40

#: Wie viele Treffer je Suchbegriff und wie viele je Bon-Zeile insgesamt. Die
#: Begründung der Zahlen steht an `plan.KANDIDATEN_MODELL` und
#: `plan.MAX_KANDIDATEN_MODELL`; sie werden hier übernommen und nicht neu
#: erfunden, damit die Suche im Bon dieselbe ist wie im Chat.
KANDIDATEN = plan.KANDIDATEN_MODELL
OBERGRENZE = plan.MAX_KANDIDATEN_MODELL

#: Temperatur 0 wie in `assistant.plan`: derselbe Bon soll dieselbe Zuordnung
#: ergeben, sonst ist nichts vergleichbar.
TEMPERATUR = 0.0

#: Reichlich für einen Block: je Zeile Klartext plus zwei Suchbegriffe sind
#: grob 60 Token, mal 40 Zeilen plus Gerüst.
MAX_TOKENS = 3000

#: Denken aus, je Anfrage. Der Grund steht ausführlich an `plan.DENKEN` und ist
#: hier derselbe: Denk-Token zählen gegen `max_tokens`, und eine abgeschnittene
#: Antwort sieht aus wie ein Formatfehler, obwohl sie ein Budgetproblem ist.
DENKEN = False

SYSTEM_BON = """\
Du liest Zeilen von einem deutschen Kassenbon und machst daraus Suchbegriffe \
für einen Lebensmittel-Katalog.

Ein Kassenbon schreibt in GROSSBUCHSTABEN, ohne Umlaute und stark abgekürzt. \
Deine Aufgabe ist, daraus wieder zu machen, wie das Produkt im Laden heisst.

Für jede Zeile gibst du an:
- "bon": die Zeile unverändert, genau wie du sie bekommen hast.
- "artikel": true, wenn es ein gekauftes Lebensmittel oder Produkt ist. \
false bei Pfand, Leergut, Rabatt, Coupon, Treuepunkten, Summen, Gebühren.
- "klartext": der ausgeschriebene Name mit Umlauten und ohne Abkürzungen. \
"SCHIN.-KAE. CRO." wird "Schinken-Käse-Croissant", "HAEHN.BRUST PAP" wird \
"Hähnchenbrust paniert", "SCHOTT. CHEDDAR" wird "Schottischer Cheddar". \
Achte auf die Umlaute: "KAESEBROETCHEN" wird "Käsebrötchen" und nicht \
"Käsebrotchen" — ein fehlender Umlaut findet im Katalog nichts.
- "suchbegriffe": ein bis drei Begriffe, vom genauesten zum allgemeinsten. \
Der erste ist der Klartext oder sein Kern, der letzte ein kurzes Grundwort \
("Croissant", "Cheddar"). Der Katalog sucht über Wortanfänge, kurze Begriffe \
finden mehr.

Regeln:
- Keine Marken erfinden. Steht keine auf dem Bon, nennst du auch keine.
- Keine Produktnummern, keine Preise, keine IDs.
- Bei "artikel": false lässt du "suchbegriffe" leer.
- Bist du dir nicht sicher, was gemeint ist, gibst du den Bon-Text so \
allgemein wieder, wie du ihn verstehst — raten ist besser als schweigen, \
weil ein Mensch die Zuordnung anschliessend bestätigt.
- Jede Zeile, die du bekommst, kommt genau einmal zurück.

Antworte ausschliesslich als JSON:
{"zeilen": [{"bon": "SCHIN.-KAE. CRO.", "artikel": true, "klartext": \
"Schinken-Käse-Croissant", "suchbegriffe": ["Schinken-Käse Croissant", \
"Croissant"]}]}"""

SCHEMA_BON = {
    "type": "object",
    "properties": {
        "zeilen": {
            "type": "array",
            "maxItems": BLOCK,
            "items": {
                "type": "object",
                "properties": {
                    "bon": {"type": "string"},
                    "artikel": {"type": "boolean"},
                    "klartext": {"type": "string"},
                    "suchbegriffe": {
                        "type": "array",
                        "maxItems": plan.MAX_KETTE,
                        "items": {"type": "string"},
                    },
                },
                "required": ["bon", "artikel"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["zeilen"],
    "additionalProperties": False,
}


class ZuordnungFehler(RuntimeError):
    """Die Zuordnung ging schief. Die Käufe stehen trotzdem in der Datenbank."""


class ZuordnungNichtVerfuegbar(ZuordnungFehler):
    """Die Box bedient gerade nicht. Trägt den Weckzustand für die Anzeige."""

    def __init__(self, zustand: wake.Zustand):
        super().__init__(zustand.grund or "Das Modell bedient gerade nicht.")
        self.zustand = zustand


@dataclass(frozen=True)
class Ergebnis:
    """Was ein Zuordnungslauf ergeben hat — für Oberfläche und Bericht."""
    receipt_id: int
    zeilen: int = 0
    zugeordnet: int = 0
    kein_artikel: int = 0
    ohne_treffer: int = 0
    meldung: str = ""
    unbeantwortet: list[str] = field(default_factory=list)


def deuten(zugang, bon_texte: list[str], *, guided: bool = True,
           system: str = SYSTEM_BON, temperatur: float = TEMPERATUR,
           max_tokens: int = MAX_TOKENS, denken: bool = DENKEN,
           block: int = BLOCK) -> dict[str, dict]:
    """Bon-Zeilen -> `{bon_text: {"artikel", "klartext", "suchbegriffe"}}`.

    **Zugeordnet wird über den TEXT, nicht über die Position.** Das ist die
    einzige Stelle, an der dieses Modul streng sein muss: lässt das Modell eine
    Zeile aus oder schiebt eine dazu, verschöbe eine Zuordnung nach Index
    sämtliche folgenden Zeilen um eins — und jede davon sähe plausibel aus.
    Eine Zeile, die nicht zurückkam, bleibt deshalb lieber ohne Deutung.

    Lange Bons werden in Blöcken gefragt. Ein Block, dessen Antwort unbrauchbar
    ist, kostet nur seine eigenen Zeilen; der Rest des Bons wird trotzdem
    zugeordnet.
    """
    gedeutet: dict[str, dict] = {}
    texte = [t for t in bon_texte if t and t.strip()]
    for i in range(0, len(texte), max(1, block)):
        teil = texte[i:i + max(1, block)]
        try:
            antwort = plan._frage(  # noqa: SLF001 — s.u.
                zugang, system, _prompt(teil), SCHEMA_BON, "zeilen", guided,
                temperatur, max_tokens, denken)
            eintraege = plan._eintraege(antwort, ("zeilen", "posten", "items"))
        except plan.PlanFehler:
            # Ein kaputter Block ist kein kaputter Bon. Die Zeilen bleiben
            # ungedeutet und stehen anschliessend ohne Vorschlag da — sichtbar
            # und von Hand zuzuordnen. Das Auspacken gehört mit in den Versuch:
            # eine Antwort, die kein JSON ist, scheitert erst dort.
            continue
        for eintrag in eintraege:
            text = _passende_zeile(eintrag, teil)
            if text is None:
                continue
            gedeutet[text] = {
                "artikel": eintrag.get("artikel") is not False,
                "klartext": plan._text(eintrag, ("klartext", "name", "text")),
                "suchbegriffe": plan._kette(eintrag),
            }
    return gedeutet


# `plan._frage`, `_eintraege`, `_text` und `_kette` sind bewusst
# WIEDERVERWENDET und nicht nachgebaut. Sie sind der Teil von `assistant.plan`,
# der nichts mit Einkaufszetteln zu tun hat, sondern mit dem Umgang mit
# Modellantworten: Denken abschalten, das Schema als `response_format` mitgeben, einen
# Codefence abziehen, `finish_reason=length` als Budgetproblem benennen. Eine
# zweite Kopie davon hier hiesse, dieselben Fallen ein zweites Mal zu stellen —
# der Unterstrich sagt „nicht Teil der öffentlichen Oberfläche", nicht
# „woanders verboten". Wandert das später in ein eigenes Modul, ist dieser
# Absatz die Begründung dafür.


def _prompt(texte: list[str]) -> str:
    return ("Zeilen von einem Kassenbon:\n"
            + json.dumps(texte, ensure_ascii=False, indent=1))


def _passende_zeile(eintrag: dict, texte: list[str]) -> str | None:
    """Welche vorgelegte Zeile meint dieser Eintrag? `None`, wenn keine.

    Verglichen wird nachsichtig — Leerraum zusammengezogen, Gross- und
    Kleinschreibung egal —, denn ein Modell schreibt `SCHIN.-KAE. CRO.` gerne
    mit anderem Abstand zurück. Nicht nachsichtig ist der Rest: eine Zeile, die
    das Modell frei erfunden hat, findet hier keine Entsprechung und fällt weg.
    """
    roh = plan._text(eintrag, ("bon", "zeile", "original", "text"))
    if not roh:
        return None
    schluessel = " ".join(roh.split()).casefold()
    for text in texte:
        if " ".join(text.split()).casefold() == schluessel:
            return text
    return None


class Zuordner:
    """Der Zuordnungslauf, mit allem Injizierbaren an einer Stelle.

    Dieselbe Bauart wie `assistant.chat.Chat`: `zugang` und `wecker` hängen am
    Objekt, damit ein Test einen Fake-LLM unterschieben kann, ohne die Box zu
    wecken. Gebaut wird im Konstruktor nichts — der Web-Prozess soll ohne
    Modell hochkommen (Spec 11).
    """

    def __init__(self, zugang=None, *, wecker=None,
                 kandidaten: int = KANDIDATEN,
                 obergrenze: int = OBERGRENZE, guided: bool = True,
                 denken: bool = DENKEN, system: str = SYSTEM_BON):
        self._zugang = zugang
        self._wecker = wecker
        self.kandidaten = kandidaten
        self.obergrenze = obergrenze
        self.guided = guided
        self.denken = denken
        self.system = system

    @property
    def zugang(self):
        if self._zugang is None:
            from zettel.llm.client import Modellzugang
            self._zugang = Modellzugang()
        return self._zugang

    def zustand(self) -> wake.Zustand:
        """Bedient die Box? Weckt sie, wenn nicht (nicht blockierend)."""
        if self._wecker is not None:
            return self._wecker.zustand()
        return wake.zustand()

    def zuordnen(self, con: sqlite3.Connection, receipt_id: int) -> Ergebnis:
        """Schlägt für jede OFFENE Zeile eines Belegs ein Produkt vor.

        Geschrieben wird nur `product_id`, `note`, `search_term` und `rank` —
        `decision` bleibt `offen`. Nichts an diesem Lauf bestätigt etwas: das
        tut ein Mensch auf der Bon-Seite, und nur was er bestätigt, wird
        Kaufhistorie und echter Preis.

        Wirft `ZuordnungNichtVerfuegbar`, wenn die Box nicht bedient. Der Beleg
        ist dann trotzdem vollständig eingelesen — Datum, Artikel und Preise
        stehen da, nur die Verbindung zum Katalog fehlt und lässt sich später
        nachholen.
        """
        zeilen = [z for z in kaeufe.posten(con, receipt_id) if z["offen"]]
        if not zeilen:
            return Ergebnis(receipt_id=receipt_id,
                            meldung="Auf diesem Beleg ist nichts mehr offen.")

        zustand = self.zustand()
        if not zustand.bedient:
            raise ZuordnungNichtVerfuegbar(zustand)

        with obs.chain("bons.zuordnen",
                       eingabe=f"Beleg {receipt_id}, {len(zeilen)} Zeilen"):
            try:
                with obs.stufe("bons.deuten"):
                    gedeutet = deuten(self.zugang,
                                      [z["bon_text"] for z in zeilen],
                                      guided=self.guided, denken=self.denken,
                                      system=self.system)
            except ModellNichtErreichbar as e:
                # Zwischen `zustand()` und dem Aufruf können Sekunden liegen,
                # und die Box suspendiert nach Leerlauf. Das ist kein Fehler
                # des Belegs.
                raise ZuordnungNichtVerfuegbar(
                    wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e
            return self._schreiben(con, receipt_id, zeilen, gedeutet)

    def _schreiben(self, con, receipt_id: int, zeilen: list[dict],
                   gedeutet: dict[str, dict]) -> Ergebnis:
        """Sucht je Zeile und trägt den Vorschlag ein."""
        zugeordnet = kein_artikel = ohne_treffer = 0
        unbeantwortet: list[str] = []

        for z in zeilen:
            deutung = gedeutet.get(z["bon_text"])
            if deutung is None:
                # Das Modell hat diese Zeile nicht zurückgegeben. Sie bleibt
                # ohne Vorschlag stehen, statt einer fremden Deutung zu
                # folgen.
                unbeantwortet.append(z["bon_text"])
                continue
            if not deutung["artikel"]:
                kein_artikel += 1
                kaeufe.zuordnung_setzen(
                    con, z["id"], product_id=None,
                    note=(deutung["klartext"] or "kein Artikel")
                         + " — laut Modell kein gekaufter Artikel")
                continue

            kette = deutung["suchbegriffe"] or (
                [deutung["klartext"]] if deutung["klartext"] else [])
            if not kette:
                ohne_treffer += 1
                kaeufe.zuordnung_setzen(con, z["id"], product_id=None,
                                        note=deutung["klartext"] or None)
                continue

            with obs.retriever("catalog.search", suchbegriffe=kette) as such:
                kandidaten = search.suche_kette(
                    con, kette, limit=self.kandidaten,
                    obergrenze=self.obergrenze)
                obs.dokumente(such, kandidaten)

            if not kandidaten:
                # Eine echte Katalog-Lücke — `OLD AMSTERDAM` gibt es bei
                # Knuspr nicht. Der Klartext bleibt an der Zeile stehen: er
                # ist das, wonach beim nächsten Versuch gesucht wird.
                ohne_treffer += 1
                kaeufe.zuordnung_setzen(con, z["id"], product_id=None,
                                        note=deutung["klartext"] or None,
                                        search_term=kette[0])
                continue

            bester = kandidaten[0]
            zugeordnet += 1
            kaeufe.zuordnung_setzen(
                con, z["id"], product_id=int(bester["id"]),
                note=deutung["klartext"] or None,
                # NICHT der erste Begriff der Kette, sondern der, der DIESEN
                # Kandidaten gebracht hat (WB-340). Sonst behauptet die Zeile
                # eine Herkunft, die nicht stimmt.
                search_term=bester.get("via") or kette[0],
                rang=bester.get("rang"))

        return Ergebnis(
            receipt_id=receipt_id, zeilen=len(zeilen), zugeordnet=zugeordnet,
            kein_artikel=kein_artikel, ohne_treffer=ohne_treffer,
            unbeantwortet=unbeantwortet,
            meldung=_meldung(len(zeilen), zugeordnet, kein_artikel,
                             ohne_treffer, unbeantwortet))


def _meldung(gesamt: int, zugeordnet: int, kein_artikel: int,
             ohne_treffer: int, unbeantwortet: list[str]) -> str:
    """Der Satz über der Liste. Nennt beim Namen, was nicht geklappt hat."""
    teile = [f"{gesamt} Zeile{'n' if gesamt != 1 else ''} gelesen, "
             f"{zugeordnet} einem Katalogprodukt zugeordnet."]
    if kein_artikel:
        teile.append(f"{kein_artikel} davon sind laut Modell kein Artikel "
                     "(Pfand, Rabatt und Ähnliches).")
    if ohne_treffer:
        teile.append(f"{ohne_treffer} ohne Treffer im Katalog — das ist eine "
                     "Lücke im Katalog und kein Fehler des Bons.")
    if unbeantwortet:
        teile.append("Das Modell hat nichts gesagt zu: "
                     + ", ".join(f"„{t}“" for t in unbeantwortet[:5])
                     + ("…" if len(unbeantwortet) > 5 else "") + ".")
    teile.append("Nichts davon ist bestätigt — das entscheidest du Zeile für "
                 "Zeile.")
    return " ".join(teile)
