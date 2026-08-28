"""Der Chat-Zug: aus einem Satz wird eine Vorschlagsliste (Spec 6).

Genau vier Wege, und welcher genommen wurde, wird festgehalten (`weg`, in
Spec 7.1 `picknick.path`), weil sich sonst später keine Auswertung mehr
trennen lässt:

* **`recipe`** — der Satz nennt ein gespeichertes Rezept. Dessen verknüpfte
  Produkte werden direkt vorgeschlagen: kein Modell, keine Suche, keine
  Wartezeit. Das ist auch der Weg, der noch funktioniert, wenn die vLLM-Box
  schläft.
* **`chefkoch`** — der Satz nennt ein GERICHT, dessen Rezept schon geholt
  wurde (WB-338). Die Zutaten kommen dann aus dem Rezept statt aus dem
  Gedächtnis des Modells; das Modell macht daraus nur noch Suchbegriffe.
  Gemessen an „alles für Pho" (2026-08-28, echte Box, echter Katalog): aus
  dem Gedächtnis drei Begriffe — Rinderhack, Rinderknochen, Rinderbrust —,
  von denen keiner in eine Pho gehört; aus der Quelle 19 Begriffe mit 11
  Katalogtreffern und acht ehrlichen Freitexten.
* **`fanout`** — der Satz nennt einen OBERBEGRIFF (WB-368). „Aufschnitt" ist
  kein Produktwunsch, sondern ein Regal: statt sechs Produkten, die zufällig
  das Wort im Namen tragen, kommen die SORTEN des Katalogs mit ihren echten
  Stückzahlen — Rohschinken & Bacon (58), Kochschinken (42), Salami (34) …
  Der Zug legt keinen Vorschlag an; er stellt eine Frage. Ein Tipp auf eine
  Sorte führt zurück in den gewöhnlichen Ablauf (`_aus_sorten`), und wer
  nichts ankreuzt, sucht direkt nach dem getippten Wort. Ist das Wort selbst
  ein Kategoriename, kostet dieser Weg **keinen Modellaufruf** und läuft auch
  bei schlafender Box.
* **`llm`** — die drei Stufen aus Spec 6: `plan.extract` (nur Begriffe),
  `catalog.search` (der SHOP sucht), `plan.choose` (Wahl aus den vorgelegten
  Kandidaten). Nennt der Satz ein Gericht, das noch niemand geholt hat, wird
  der Abruf hier ANGESTOSSEN und nicht abgewartet — der Zug läuft mit den
  geratenen Begriffen zu Ende und sagt das auch. Ein Request wartet nie auf
  eine fremde Seite (Spec 3).

Drei Regeln halten diesen Ablauf zusammen:

1. **Das Modell erfindet niemals Produkte.** Es sieht in Stufe 1 keinen
   Katalog und darf in Stufe 3 nur nehmen, was ihm vorgelegt wurde. Eine
   nicht vorgelegte ID wird verworfen (siehe `plan.choose`).
2. **Kein Begriff verschwindet still.** Findet die Suche nichts, wählt das
   Modell nichts oder erfindet es etwas — der Begriff wird zum
   **Freitext-Vorschlag**. Der Katalog hat Lücken (die Suche kennt nur
   Wortanfänge, WB-322), und eine stillschweigend fallengelassene Zutat merkt
   man erst im Laden. **Auch das, was neben einem Gericht im Satz stand**
   („… und Klopapier"), fällt darunter: es kommt seit WB-370 im Code auf den
   Zettel (`_rest_sichern`) und nicht über eine Bitte an das Modell — die
   schlug in 32 von 35 gemessenen Zügen fehl.
3. **Nichts landet ungefragt im Warenkorb.** Ergebnis ist eine Liste in
   `chat_suggestion`, die zeilenweise bestätigt oder verworfen wird
   (`vorschlaege.entscheiden`). Seit WB-359 werden dabei auch die übrigen
   Kandidaten aufgehoben (`chat_kandidat`): ein „Nein" klappt sie auf, statt
   sie wegzuwerfen — gesucht waren sie ohnehin längst.

Fällt das Modell aus, ist nur der Chat betroffen (Spec 11): `turn()` wirft
`ChatNichtVerfuegbar` mit dem Zustand des Weckers, und der Rest des Shops
merkt davon nichts. Geschrieben wird erst am Ende — ein abgebrochener Zug
hinterlässt keine halbe Unterhaltung in der Datenbank.

**Der Zug ist zugleich der Span-Baum aus Spec 7.1** (WB-328):

```
CHAIN       chat.turn        input: der Satz der Nutzerin
 ├ LLM      plan.extract     output: [{suchbegriffe, menge}, …]
 ├ RETRIEVER catalog.search  input: die Begriffskette einer Zutat
 ├ RETRIEVER catalog.search  (ein Span je ZUTAT) -> n Kandidaten mit Score
 ├ LLM      plan.choose      Kandidaten -> gewählte product_ids
 └ output: die Vorschlagsliste
```

Der Rezeptweg erzeugt nur den CHAIN-Span, mit `picknick.path = "recipe"`.
Der Quellenweg hat DENSELBEN Baum wie der Modellweg — auch seine Stufe 1
heisst `plan.extract`, obwohl sie `plan.zutatenbegriffe` aufruft. Das ist
Absicht: zwei Namen für dieselbe Stelle im Baum machten jede Auswertung über
Stufe 1 zu einer Fallunterscheidung, und WAS das Modell zu lesen bekam, steht
ohnehin am Span (`input.value`) und am `picknick.path` daneben.
Fällt Phoenix aus, ändert sich an diesem Ablauf nichts: die Span-Aufrufe
sind dann No-Ops und der Export läuft ohnehin in einem anderen Thread
(`picknick.obs.otel`).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from picknick import gerichte, mengen, obs, orders
from picknick.assistant import (entwurf, herkunft, oberbegriffe, plan,
                                rezeptweg, vorschlaege)
from picknick.catalog import search
from picknick.llm import wake
from picknick.llm.client import ModellNichtErreichbar

#: Die vier Wege. Englisch, weil sie so in Spec 7.1 als Span-Attribut
#: stehen und ein zweiter Name für dieselbe Sache eine Auswertung kostet.
WEG_REZEPT = "recipe"
WEG_LLM = "llm"
#: Seit WB-368: der Satz nannte einen OBERBEGRIFF, und statt einer
#: Vorschlagsliste sind die Sorten des Katalogs herausgekommen. Ein eigener
#: Wert und kein Nebensatz, weil sich sonst nicht messen lässt, ob der Umweg
#: über die Sorte hilft: ein `fanout`-Zug hat null Vorschläge, und ohne den
#: Weg daneben sähe er aus wie ein Zug, der nichts gefunden hat.
WEG_FAECHER = "fanout"
#: Seit WB-338: die Zutaten kamen aus einer Quelle (Chefkoch) statt aus dem
#: Gedächtnis des Modells. **Der Wert existiert, damit sich messen lässt, ob
#: die Quelle wirklich besser ist als das Raten** — ohne ihn wären beide
#: Wege im Trace `llm` und die interessanteste Frage der Evals wäre nicht
#: mehr zu stellen.
WEG_QUELLE = gerichte.SOURCE


class ChatFehler(RuntimeError):
    """Der Chat konnte nicht antworten. Der übrige Shop läuft weiter."""


class ChatNichtVerfuegbar(ChatFehler):
    """Die Box bedient gerade nicht. Trägt den Weckzustand für die Anzeige."""

    def __init__(self, zustand: wake.Zustand):
        super().__init__(zustand.grund or "Das Modell bedient gerade nicht.")
        self.zustand = zustand


@dataclass(frozen=True)
class Ergebnis:
    """Was ein Chat-Zug ergeben hat — für Oberfläche, Span und Bericht."""
    weg: str
    order_id: int
    satz: str
    chat_message_id: int | None = None
    vorschlaege: list[dict] = field(default_factory=list)
    begriffe: list[dict] = field(default_factory=list)
    verworfen: list[dict] = field(default_factory=list)
    rezepte: list[str] = field(default_factory=list)
    meldung: str = ""
    #: Das erkannte Gericht (WB-338) — auf JEDEM Weg gesetzt, sobald der Zug
    #: eines erkannt hat, auch wenn die Quelle es (noch) nicht liefern
    #: konnte. Genau daran hängt der Vergleich „Quelle gegen Raten": zu einem
    #: `llm`-Zug mit gesetztem Gericht gibt es später einen `chefkoch`-Zug
    #: mit demselben, und die beiden lassen sich nebeneinanderlegen.
    gericht: str | None = None
    #: Woher die Zutatenliste stammt: Rezeptname und `siteUrl` der Quelle.
    quelle_name: str | None = None
    quelle_url: str | None = None
    #: Das Rezept, das aus der Quelle in die Sammlung gewandert ist — damit
    #: die Meldung darauf zeigen kann („steht jetzt unter Rezepte").
    quelle_recipe_id: int | None = None
    #: Was der Abruf bei Chefkoch in DIESEM Zug ergeben hat (WB-367):
    #: `ok`, `leer` (kennt das Gericht nicht), `fehler` (Störung oder
    #: Zeitüberschreitung) — oder `None`, wenn gar nicht abgerufen wurde,
    #: weil der Zwischenspeicher schon etwas Frisches hatte oder gar kein
    #: Gericht im Satz stand. Auf `weg = llm` mit gesetztem `gericht` steht
    #: hier der Grund, warum trotzdem geraten wurde.
    abruf: str | None = None
    #: Die Auffächerung dieses Zugs (WB-368): die Kategorie, unter der die
    #: Sorten stehen, und die Sorten selbst. Auf einem `fanout`-Zug gefüllt;
    #: auf dem FOLGEZUG steht `kategorie` weiter da, damit im Trace beide
    #: Hälften desselben Umwegs zusammenfinden.
    kategorie: str | None = None
    sorten: list[dict] = field(default_factory=list)
    #: Kam die Kategorie aus dem Katalog (`catalog`) oder aus dem Modell
    #: (`model`)? Die Zahl, an der sich später ablesen lässt, ob der
    #: Modellaufruf überhaupt gebraucht wird.
    sorten_herkunft: str | None = None
    #: Was das Modell als Kategorie nannte, obwohl es sie nicht gibt. Die
    #: Schwester von `verworfen` — und der Beleg dafür, dass eine erfundene
    #: Kategorie nicht durchkommt.
    sorten_verworfen: str | None = None
    #: Welche Sorten die Nutzerin angekreuzt hat. Nur auf dem Folgezug.
    gewaehlte_sorten: list[str] = field(default_factory=list)
    #: Der Rezeptentwurf dieses Zugs (WB-337): der vorgeschlagene Name und
    #: wie viele Vorschlagszeilen als Gerichtszutat markiert sind. Nur auf
    #: dem Quellenweg gefüllt — die Begründung steht in `assistant.entwurf`.
    entwurf: str | None = None
    entwurf_zutaten: int = 0
    #: Was neben dem Gericht im Satz stand (WB-370): „alles für Spaghetti
    #: Bolognese, und Klopapier" -> „Klopapier". `rest_angehaengt` sagt, ob
    #: dieser Zug eine eigene Zeile dafür angelegt hat (`True`) oder ob der
    #: Rest schon auf dem Zettel stand (`False`). Ohne Rest bleibt beides
    #: leer. Auf dem Rezept- wie auf dem Chefkoch-Weg gefüllt — auf beiden
    #: kann ein Artikel neben dem Gericht stehen.
    rest: str | None = None
    rest_angehaengt: bool | None = None

    @property
    def n_produkte(self) -> int:
        return sum(1 for v in self.vorschlaege if not v["ist_freitext"])

    @property
    def n_freitext(self) -> int:
        return sum(1 for v in self.vorschlaege if v["ist_freitext"])


def _zusammengefasst(zeilen: list[dict]) -> list[dict]:
    """Zwei Begriffe auf DASSELBE Produkt — eine Zeile, aber beide Mengen.

    Der Sonderfall aus WB-369 zu einer Regel, die es seit WB-327 gibt: zwei
    Suchbegriffe können auf dasselbe Produkt zeigen („Nudeln" und
    „Spaghetti"), und daraus wird eine Vorschlagszeile, weil zwei gleiche
    Zeilen zweimal dieselbe Entscheidung wären. Seit die Zeile eine Menge
    trägt, ist das Wegwerfen der zweiten aber ein verlorener Bedarf — 250 g
    und 250 g wären danach 250 g.

    Zusammengezählt wird mit `mengen.summiere`, also genau wie im Korb: was
    sich nicht zusammenzählen lässt (Stück und Gramm), bleibt bei der ersten
    Menge, und die zweite Zeile verschwindet wie bisher. Eine addierte Zahl
    aus zwei Einheiten wäre schlimmer als eine fehlende.
    """
    zusammen: list[dict] = []
    nach_schluessel: dict[tuple, dict] = {}
    for z in zeilen:
        schluessel = (z["product_id"], (z.get("free_text") or "").casefold())
        erste = nach_schluessel.get(schluessel)
        if erste is None:
            kopie = dict(z)
            nach_schluessel[schluessel] = kopie
            zusammen.append(kopie)
            continue
        # Die Zugehörigkeit zum Gericht wandert mit (WB-337): trifft eine
        # Gerichtszutat auf dasselbe Produkt wie ein Wunsch daneben, bleibt
        # die Zeile eine Gerichtszutat. Andersherum verlöre das Rezept eine
        # Zutat, weil im selben Satz zufällig noch etwas anderes stand.
        if z.get("zum_gericht"):
            erste["zum_gericht"] = True
        if z.get("bedarf") is None:
            continue
        summe = mengen.summiere(erste.get("bedarf"), erste.get("einheit"),
                                z["bedarf"], z["einheit"])
        if summe is not None:
            erste["bedarf"], erste["einheit"] = summe
    return zusammen


def _nur_allgemein(kette: list[str], kandidaten: list[dict]) -> str | None:
    """Kam ALLES nur über den allgemeinsten Begriff der Kette? Dann dieser.

    Der Querschnittsbefund aus WB-358, hierher übernommen: beim Zuordnen der
    Kassenbons entgleiste die Kette genau dort, wo der Katalog eine Lücke hat.

        OLD AMSTERDAM   -> Kette endete auf „Bier"  -> Singha Bier
        GEFLUEGELROLLE  -> Kette endete auf „Rolle" -> Prinzen Rolle Choco

    Findet kein genauer Begriff etwas, greift der allgemeinste — und der
    findet **immer irgendetwas**. Ein Vorschlag, dessen sämtliche Kandidaten
    nur von dort kommen, ist deshalb kein sicherer Treffer, sondern der Fall,
    in dem die Alternativenliste am meisten wert ist. Er wird an der Zeile
    benannt (`chat_suggestion.fallback_term`) statt verschwiegen.

    `None` heisst „unauffällig": eine Kette der Länge eins hat gar keinen
    allgemeineren Begriff, auf den sie hätte ausweichen können, und eine
    Zutat, zu der auch ein genauer Begriff Treffer brachte, ist nicht
    ausgewichen.
    """
    if len(kette) < 2 or not kandidaten:
        return None
    allgemeinster = kette[-1]
    if all(p.get("via") == allgemeinster for p in kandidaten):
        return allgemeinster
    return None


class Chat:
    """Ein Chat-Zug, mit allem Injizierbaren an einer Stelle.

    `zugang`, `wecker` und die Stellschrauben aus Spec 8.3 (`kandidaten`,
    `guided`, die beiden System-Prompts) hängen am Objekt und nicht an
    globalen Aufrufen — so kann ein Test einen Fake-LLM unterschieben, ohne
    die Box zu wecken, und ein Experiment dieselbe Pipeline mit anderer
    Kandidatenzahl oder einer anderen Prompt-Variante fahren.

    Dass die Prompts hier durchgereicht werden und nicht in `plan` ersetzt,
    ist Absicht: ein Experiment, das `plan.SYSTEM_EXTRACT` überschreibt,
    ändert das Modul für alles, was im selben Prozess noch läuft — und zwei
    Varianten nacheinander wären dann nicht mehr auseinanderzuhalten.

    Gebaut wird nichts davon im Konstruktor: `Modellzugang()` und `wecker()`
    entstehen erst beim ersten Modellweg. Der Web-Prozess soll ohne Modell
    hochkommen (Spec 11), und der Rezeptweg braucht beides nie.
    """

    def __init__(self, zugang=None, *, wecker=None, quelle=None,
                 kandidaten: int = plan.KANDIDATEN_MODELL,
                 obergrenze: int = plan.MAX_KANDIDATEN_MODELL,
                 anzeige: int = plan.KANDIDATEN_ANZEIGE,
                 anzeige_obergrenze: int = plan.MAX_KANDIDATEN_ANZEIGE,
                 guided: bool = True,
                 denken: bool = plan.DENKEN,
                 auffaechern: bool = True,
                 system_extract: str = plan.SYSTEM_EXTRACT,
                 system_choose: str = plan.SYSTEM_CHOOSE,
                 system_choose_sorte: str = plan.SYSTEM_CHOOSE_SORTE):
        self._zugang = zugang
        self._wecker = wecker
        # Die Gerichtequelle (WB-338, WB-367). Wie `zugang` und `wecker`:
        # hier nur gehalten, nichts gebaut und nichts gefragt. `Quelle()`
        # öffnet keine Verbindung; sie ruft Chefkoch erst dann ab, wenn ein
        # Zug ein Gericht nennt, das der Zwischenspeicher nicht kennt.
        # `Quelle(holer=gerichte.nicht_holen)` schaltet genau das ab.
        self._quelle = quelle
        # `kandidaten` gilt je BEGRIFF, `obergrenze` je ZUTAT: die Vereinigung
        # über eine Begriffskette (WB-340) wäre sonst so lang, dass drei
        # Begriffe mal fünf Treffer mal acht Zutaten den Prompt von Stufe 3
        # füllen. Die Begründung steht an `plan.MAX_KANDIDATEN_MODELL`.
        self.kandidaten = kandidaten
        self.obergrenze = obergrenze
        # Und daneben die ZWEITE Grenze (WB-359): wie viele Kandidaten
        # aufgehoben und der Nutzerin beim „Nein" gezeigt werden. Sie ist
        # grösser, weil sie etwas anderes kostet — Datenbankzeilen statt
        # Token (Begründung an `plan.KANDIDATEN_ANZEIGE`). Vorher bediente EINE
        # Zahl beide Zwecke, und deshalb gab es bei einer einbegriffigen Zutat
        # wie „Butter" genau vier Alternativen.
        #
        # `max(...)`: wer die Modellgrenze hochdreht (Spec 8.3 vergleicht 5
        # gegen 20), soll nicht versehentlich weniger aufheben, als das Modell
        # gesehen hat — die Modellvorlage MUSS eine Teilmenge des
        # Aufgehobenen bleiben, sonst zeigt „Nein" nicht mehr, woraus
        # gewählt wurde.
        self.anzeige = max(anzeige, kandidaten)
        self.anzeige_obergrenze = max(anzeige_obergrenze, obergrenze)
        self.guided = guided
        self.denken = denken
        # Die Auffächerung als Stellschraube (Spec 8.3): ein Experiment soll
        # „mit Sorten" gegen „ohne Sorten" fahren können, ohne dass jemand
        # den Chat umbaut — und die Evals sollen den Weg abschalten können,
        # der eine Rückfrage stellt statt eine Liste zu liefern.
        self.auffaechern = auffaechern
        self.system_extract = system_extract
        self.system_choose = system_choose
        self.system_choose_sorte = system_choose_sorte

    # -- Zugang -----------------------------------------------------------

    @property
    def zugang(self):
        if self._zugang is None:
            from picknick.llm.client import Modellzugang
            self._zugang = Modellzugang()
        return self._zugang

    @property
    def quelle(self):
        if self._quelle is None:
            self._quelle = gerichte.Quelle()
        return self._quelle

    def zustand(self) -> wake.Zustand:
        """Bedient die Box? Weckt sie, wenn nicht (nicht blockierend).

        Das ist der Aufruf aus Spec 6 („vor jedem Chat-Request wird
        `/v1/models` geprüft"). Er steht bewusst NICHT im Konstruktor: der
        Chat-Kasten im Warenkorb fragt ihn beim Anzeigen, und ein Katalogklick
        soll keine Maschine im LAN aufwecken.
        """
        if self._wecker is not None:
            return self._wecker.zustand()
        return wake.zustand()

    def _faechern(self, fuer_diesen_zug: bool | None) -> bool:
        """Wird in diesem Zug aufgefächert? Der Zug schlägt die Stellschraube.

        Zwei Schalter für dieselbe Sache, und beide werden gebraucht: der am
        Objekt gilt für ein Experiment oder einen Eval-Lauf, der am Zug für
        den einen Fall, in dem die Frage schon gestellt wurde — nach einem
        „Überspringen" darf derselbe Satz nicht wieder in der Rückfrage
        landen.
        """
        if fuer_diesen_zug is None:
            return self.auffaechern
        return bool(fuer_diesen_zug) and self.auffaechern

    # -- Der Zug ----------------------------------------------------------

    def turn(self, con: sqlite3.Connection, satz: str,
             span_id: str | None = None, *,
             auffaechern: bool | None = None,
             aus_sorten: tuple[str, list[str]] | None = None) -> Ergebnis:
        """Ein Satz -> eine Vorschlagsliste. Legt nichts in den Warenkorb.

        Wirft `ChatFehler`, wenn der Satz leer ist, und `ChatNichtVerfuegbar`,
        wenn der Modellweg nötig wäre und die Box nicht bedient. Eine kaputte
        Modellantwort wirft NICHT: sie wird zu einer ehrlichen Meldung und,
        wo möglich, zu Freitext-Vorschlägen. Der Request darf daran nicht
        zerbrechen.

        `aus_sorten` ist `(Kategorie, [Sorten])` und heisst: die Nutzerin hat
        aus einer Auffächerung gewählt (WB-368). Die Kandidaten kommen dann
        aus dem Kategoriebaum statt aus der Volltextsuche; **alles danach ist
        der normale Ablauf** — Stufe 3 wählt, die Kandidaten werden
        aufgehoben, Ja/Nein und Alternativen sind dieselben.

        `auffaechern=False` schaltet die Auffächerung für DIESEN Zug ab. Das
        ist der Weg zurück in den Freitext: wer den Oberbegriff überspringt,
        soll nach „Aufschnitt" suchen können, ohne dass ihm dieselbe Frage
        noch einmal gestellt wird.
        """
        text = " ".join((satz or "").split())
        if not text:
            # Vor dem Span: ein leerer Satz ist kein Zug des Agenten, sondern
            # ein nicht ausgefülltes Formular. Als CHAIN-Span mit Fehler
            # verzerrte er jede Auswertung über Fehlerquoten.
            raise ChatFehler("Schreib hin, was du brauchst — leer geht nicht.")

        # Ab hier läuft der Baum aus Spec 7.1. Der CHAIN-Span umschliesst den
        # ganzen Zug; die LLM- und RETRIEVER-Spans darunter entstehen in
        # `_aus_modell`. Ohne eingerichteten Tracer ist das ein No-Op.
        with obs.chain("chat.turn", eingabe=text) as span:
            # Der Chat gehört in den Warenkorb (Spec 9), also hängt er an
            # dessen Bestellung. Beim Abschicken wandert er mit, und die
            # Entscheidungen bleiben bei dem Einkauf, zu dem sie gehören.
            order_id = orders.warenkorb(con)
            # Derselbe Warenkorb ist auch die Sitzung: mehrere Sätze zu einem
            # Einkauf gehören in Phoenix zusammen, sonst steht jeder Zug für
            # sich und „sie hat nachgebessert" ist nicht mehr zu sehen.
            obs.setze(span, {"session.id": f"korb-{order_id}",
                             "picknick.order_id": order_id})

            # Die Reihenfolge der drei Wege ist eine Rangfolge, keine
            # Willkür — vom Verlässlichsten zum Geratensten:
            #
            # 1. Ein GESPEICHERTES Rezept schlägt alles. Es enthält die
            #    Produkte, die die beiden selbst einmal ausgesucht haben; das
            #    kann keine fremde Seite und kein Modell besser wissen.
            # 2. Ein GEHOLTES Gericht schlägt das Modell. Seine Zutatenliste
            #    stammt von Menschen, die das Gericht gekocht haben.
            # 3. Das Modell bleibt für alles zuständig, was kein Gericht ist
            #    — und für ein Gericht, das Chefkoch nicht kennt oder gerade
            #    nicht herausrückt. Seit WB-367 ist das die Ausnahme und
            #    nicht mehr der erste Zug: ein unbekanntes Gericht wird im
            #    Request geholt (`_aus_modell`), nicht angefordert.
            faecher = None
            if aus_sorten is None:
                treffer = rezeptweg.erkenne(con, text)
                gerichtsweg = None
                if not treffer:
                    gerichtsweg = self._gericht_im_satz(con, text)
                if not treffer and gerichtsweg is None and self._faechern(
                        auffaechern):
                    # **Der billige Weg zuerst** (WB-368): ist das getippte
                    # Wort selbst ein Kategoriename, weiss der Katalog das
                    # ohne Modell und ohne Wartezeit. Diese Abfrage kostet
                    # ein GROUP BY und läuft auch, wenn die Box schläft.
                    faecher = oberbegriffe.aus_katalog(con, text)
            else:
                # Eine gewählte Sorte ist weder ein Rezept noch ein Gericht,
                # und sie darf auch nicht ein zweites Mal aufgefächert
                # werden: „Salami" führt in die Kandidaten, nicht in die
                # nächste Rückfrage.
                treffer = rezeptweg.Rezeptweg()
                gerichtsweg = None

            zusatz: dict = {}
            aus_quelle = None
            if not treffer and gerichtsweg is not None:
                # `None` heisst: zwischen dem Nachschlagen und hier ist das
                # Rezept verschwunden (gelöscht, abgelaufen). Dann gilt der
                # Modellweg — und zwar auch als `weg`, damit im Trace nicht
                # `chefkoch` steht, wo das Modell geraten hat.
                aus_quelle = self._aus_quelle(con, text, gerichtsweg)

            if treffer:
                plan_zeilen, meldung = self._aus_rezept(con, treffer)
                weg, begriffe, verworfen, aufgaben = WEG_REZEPT, [], [], []
                # Der Rezeptweg hängt den Rest schon immer im Code an und
                # sagt es auch. Seit WB-370 steht dasselbe im Span, damit
                # sich die beiden Wege in Phoenix vergleichen lassen: `rest`
                # heisst auf beiden dasselbe.
                zusatz = {"rest": treffer.rest,
                          "rest_angehaengt": True if treffer.rest else None}
            elif faecher is not None:
                # Ein Oberbegriff, aus dem Katalog erkannt: keine Suche, kein
                # Modell, keine Vorschläge — die Sorten und die Frage, welche
                # es sein sollen.
                plan_zeilen, meldung = [], oberbegriffe.meldung(faecher)
                weg, begriffe, verworfen, aufgaben = WEG_FAECHER, [], [], []
                zusatz = {"faecher": faecher}
            elif aus_sorten is not None:
                (plan_zeilen, meldung, begriffe, verworfen, aufgaben,
                 zusatz) = self._aus_sorten(con, text, *aus_sorten)
                weg = WEG_LLM
            elif aus_quelle is not None:
                (plan_zeilen, meldung, begriffe, verworfen, aufgaben,
                 zusatz) = aus_quelle
                weg = WEG_QUELLE
            else:
                (plan_zeilen, meldung, begriffe, verworfen, aufgaben,
                 zusatz) = self._aus_modell(
                     con, text, auffaechern=self._faechern(auffaechern))
                # Der Modellweg kann UNTERWEGS zum Quellenweg werden
                # (WB-367): Stufe 1 nennt ein Gericht, das noch niemand
                # geholt hat, es wird geholt, und die Zutaten kommen dann
                # doch aus dem Rezept. Dann steht der Weg im Zusatz — und
                # zwar als `chefkoch`, damit im Trace nicht `llm` steht, wo
                # nicht geraten wurde.
                weg = zusatz.pop("weg", WEG_LLM)

            ergebnis = self._schreiben(
                con, order_id, text, weg, plan_zeilen, meldung,
                # Die echte Span-ID, ausser ein Aufrufer gibt eine vor. Damit
                # findet eine Annotation aus Spec 8.1 später genau diesen Zug.
                span_id if span_id is not None else obs.span_id(span),
                begriffe=begriffe, verworfen=verworfen,
                rezepte=[r["name"] for r in treffer.rezepte], **zusatz)
            self._span_abschluss(span, ergebnis, aufgaben)
            return ergebnis

    # -- Gerichte aus der Quelle (WB-338) ---------------------------------

    def _gericht_im_satz(self, con, text: str) -> rezeptweg.Rezeptweg | None:
        """Nennt der Satz ein Gericht, das schon geholt wurde?

        Genau derselbe Namensvergleich wie beim Rezeptweg (`rezeptweg`), und
        aus demselben Grund: **er kostet kein Modell und keine Suche.** Wäre
        hier eine Modellstufe nötig, um überhaupt nachzusehen, müsste jeder
        Satz erst durch Stufe 1 — und die Abkürzung wäre keine.

        Ein Ausfall dieses Nachschlagens darf den Zug nicht kosten: was hier
        schiefgeht, führt zum Modellweg zurück und nicht zu einer
        Fehlerseite.
        """
        try:
            bekannt = self.quelle.bereit(con)
        except Exception:                        # noqa: BLE001 — bewusst breit
            return None
        if not bekannt:
            return None
        gefunden = rezeptweg.erkenne_in(text, bekannt, lambda g: g["query"])
        return gefunden or None

    def _span_abschluss(self, span, ergebnis: Ergebnis,
                        aufgaben: list[dict]) -> None:
        """Das Ergebnis auf den CHAIN-Span (Spec 7.1).

        `weakest_term`/`weakest_rank` sind die Abkürzung zur Frage des ganzen
        Tickets: wessen Suche hat am schlechtesten vorgelegt? Im Butter-Fall
        aus WB-327 stünde hier „Butter" und 4,01 — und damit die Auskunft,
        dass ein Fehlgriff bei diesem Begriff eher der Suche als dem Modell
        anzulasten ist, ohne dass man einen einzigen Ast aufklappt.
        """
        schwach, rang = obs.schwaechste_suche(aufgaben)
        obs.setze(span, {
            obs.PFAD: ergebnis.weg,
            "picknick.chat_message_id": ergebnis.chat_message_id,
            "picknick.terms": len(ergebnis.begriffe),
            "picknick.products": ergebnis.n_produkte,
            "picknick.free_text": ergebnis.n_freitext,
            # Wie oft das Modell eine ID nannte, die ihm nie vorgelegt wurde.
            # Die interessanteste Zahl von Stufe 3 (Spec 6).
            "picknick.rejected": len(ergebnis.verworfen),
            "picknick.recipes": ", ".join(ergebnis.rezepte) or None,
            "picknick.weakest_term": schwach,
            "picknick.weakest_rank": rang,
            # Die Herkunft der Zutaten (WB-338). `dish` steht auf JEDEM Weg,
            # der ein Gericht erkannt hat — auch auf dem `llm`-Weg, der es
            # noch geraten hat. Genau dieses Paar macht die Frage messbar, um
            # die es im Ticket geht: derselbe Gerichtsname einmal mit
            # `path = llm` und einmal mit `path = chefkoch`, und daneben
            # `products` und `free_text`.
            "picknick.dish": ergebnis.gericht,
            "picknick.dish_recipe": ergebnis.quelle_name,
            "picknick.dish_url": ergebnis.quelle_url,
            # Seit WB-367: `dish_requested` heisst „dieser Zug hat das
            # Gericht selbst geholt" (statt „hat einen Lauf angestossen"),
            # und `dish_fetch` sagt, was dabei herauskam. Ein `llm`-Zug mit
            # gesetztem `dish` ist damit erklärbar statt bloss auffällig.
            "picknick.dish_requested": bool(ergebnis.abruf) or None,
            "picknick.dish_fetch": ergebnis.abruf,
            # Die Auffächerung (WB-368). `fanout_category` steht auf BEIDEN
            # Hälften des Umwegs — auf dem Zug, der die Sorten angeboten hat,
            # und auf dem, der eine davon gewählt hat. Nur so lassen sich die
            # beiden nebeneinanderlegen und die Frage beantworten, um die es
            # geht: hilft der Umweg über die Sorte, oder kostet er nur einen
            # Tipp mehr?
            "picknick.fanout_category": ergebnis.kategorie,
            "picknick.fanout_varieties": len(ergebnis.sorten) or None,
            "picknick.fanout_source": ergebnis.sorten_herkunft,
            # Wie `rejected` bei den Produkt-IDs: wie oft das Modell eine
            # Kategorie nannte, die ihm nie vorgelegt wurde.
            "picknick.fanout_rejected": ergebnis.sorten_verworfen,
            "picknick.varieties_chosen":
                ", ".join(ergebnis.gewaehlte_sorten) or None,
            # Der Rezeptentwurf (WB-337). **Dasselbe Vokabular wie oben**:
            # `dish` sagt, WELCHES Gericht erkannt wurde, `dish_draft` sagt,
            # dass aus diesem Zug ein Rezept werden kann, und `dish_items`,
            # aus wie vielen Zeilen. Ohne die beiden ist später nicht mehr zu
            # sehen, warum dasselbe Gericht ab dem nächsten Satz plötzlich
            # `path = recipe` nimmt und gar kein Modell mehr kostet.
            "picknick.dish_draft": ergebnis.entwurf,
            "picknick.dish_items": ergebnis.entwurf_zutaten or None,
            # Was NEBEN dem Gericht im Satz stand (WB-370) — dasselbe
            # Vokabular wie `dish`, nur die andere Hälfte des Satzes.
            # `rest_added` sagt, ob dieser Zug eine Zeile dafür angelegt hat;
            # `False` heisst „stand schon auf dem Zettel". Ohne die beiden
            # ist der Fall des Tickets im Trace nicht wiederzufinden: ein
            # Artikel, der still verschwindet, hinterlässt sonst nichts.
            "picknick.rest": ergebnis.rest,
            "picknick.rest_added": ergebnis.rest_angehaengt,
        })
        obs.setze_ausgabe(span, [
            {"product_id": v["product_id"], "name": v["name"],
             "menge": v["qty"], "begriff": v["search_term"],
             "rang": v["rang"], "freitext": v["ist_freitext"]}
            for v in ergebnis.vorschlaege])

    # -- Weg 1: Rezept ----------------------------------------------------

    def _aus_rezept(self, con, treffer: rezeptweg.Rezeptweg):
        """Die verknüpften Produkte des Rezepts, ohne Modell und ohne Suche.

        Der Rest des Satzes („… und Klopapier") wird ein Freitext-Vorschlag.
        Ihn hier durch die Suche zu schicken, wäre ein dritter Auswahlweg
        neben Rezept und Modell — und ein Vorschlag, den weder ein Rezept noch
        ein Modell verantwortet. Als Freitext steht er sichtbar da und die
        Nutzerin entscheidet selbst.
        """
        zeilen = []
        for z in rezeptweg.zutaten(con, treffer):
            # Die Menge des gespeicherten Rezepts geht mit (WB-369). Sie steht
            # dort seit WB-362 (`recipe_item.amount`) und wurde auf diesem Weg
            # bisher weggeworfen — „alles in den Warenkorb" rechnete damit,
            # derselbe Weg über den Chat nicht.
            #
            # `qty` bleibt dabei die des Rezepts und wird NICHT auf 1
            # gesetzt: sie ist keine geratene Zahl wie beim Modell, sondern
            # von Hand eingetragen, und sie ist die Rückfallebene, wo sich
            # nichts ausrechnen lässt. Genau so hält es auch
            # `recipes.in_den_korb` (WB-362).
            zeilen.append({"product_id": z["product_id"],
                           "free_text": z["free_text"], "qty": z["qty"],
                           "bedarf": z.get("amount"), "einheit": z.get("unit"),
                           "search_term": z["rezept"],
                           "rang": None})
        namen = ", ".join(r["name"] for r in treffer.rezepte)
        teile = [f"„{namen}“ — {len(zeilen)} Zutaten aus dem Rezept, "
                 "ohne Modell."]
        if treffer.rest:
            zeilen.append({"product_id": None, "free_text": treffer.rest,
                           "qty": 1, "bedarf": None, "einheit": None,
                           "search_term": treffer.rest, "rang": None})
            teile.append(f"„{treffer.rest}“ steht nicht im Rezept und liegt "
                         "als Freitext dabei.")
        return zeilen, " ".join(teile)

    # -- Weg 2: die Quelle (WB-338) ---------------------------------------

    def _aus_quelle(self, con, text: str, gefunden):
        """Die Zutaten kommen aus dem geholten Rezept, nicht aus dem Modell.

        Gibt `None` zurück, wenn dieser Weg doch nicht gangbar ist; der
        Aufrufer nimmt dann den Modellweg.

        Was sich gegenüber dem Modellweg ändert, ist genau EINE Stufe: statt
        `plan.extract` (das Modell rät die Zutaten eines Gerichts aus dem
        Gedächtnis) läuft `plan.zutatenbegriffe` (das Modell übersetzt die
        Zutatenliste des Rezepts in Suchbegriffe). Stufe 2 und Stufe 3 sind
        dieselben — die Suche sucht im selben Katalog, und Stufe 3 wählt aus
        denselben vorgelegten Kandidaten.

        **Das Modell bleibt der Zerleger, es ist nur nicht mehr die Quelle.**
        Es kürzt „Knoblauchzehe(n)" zu „Knoblauch", bietet „Karotten" für
        „Möhren" an und lässt Salz und Pfeffer weg. Was es nicht mehr tut,
        ist sich die Zutatenliste ausdenken.
        """
        zustand = self.zustand()
        if not zustand.bedient:
            raise ChatNichtVerfuegbar(zustand)

        gerichte_daten = []
        for eintrag in gefunden.rezepte:
            voll = self.quelle.gericht(con, eintrag["query"])
            if voll is not None and voll["zutaten"]:
                gerichte_daten.append(voll)
        if not gerichte_daten:
            # Zwischen dem Nachschlagen und hier ist das Rezept verschwunden
            # (gelöscht, abgelaufen). Kein Grund zu scheitern: `turn()` nimmt
            # dann den Modellweg — und beschriftet ihn auch als solchen.
            return None

        zutaten, namen, quellen = [], [], []
        for g in gerichte_daten:
            zutaten.extend(g["zutaten"])
            namen.append(g["rezept"]["name"])
            quellen.append(g["rezept"])
        titel = ", ".join(namen)

        try:
            with obs.stufe("plan.extract"):
                begriffe = plan.zutatenbegriffe(
                    self.zugang, zutaten, gericht=titel,
                    servings=quellen[0].get("servings"),
                    guided=self.guided, denken=self.denken)
            notbehelf = None
        except plan.PlanFehler as e:
            # **Hier wird nicht abgebrochen, und das ist der Unterschied zum
            # Modellweg.** Dort gibt es ohne Stufe 1 nichts; hier liegt die
            # Zutatenliste bereits vor, und aus ihr lässt sich ohne Modell
            # eine Begriffskette bauen (`chefkoch.zutat_kette`). Sie ist
            # schlechter — gemessen 10 von 14 statt 12 von 12 — aber sie ist
            # da, und eine Liste, in der zwei Zeilen als Freitext stehen, ist
            # besser als eine Fehlermeldung.
            begriffe = self._ketten_ohne_modell(zutaten)
            notbehelf = str(e)
            if not begriffe:
                raise
        except ModellNichtErreichbar as e:
            raise ChatNichtVerfuegbar(
                wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

        # **Hier entsteht die Trennung, um die es in WB-337 geht** — ohne
        # Modell und ohne ein Feld im Prompt. Sie steht vor der Suche, weil
        # sie nur die Begriffe und die Zutatenliste braucht. Davor der Rest
        # des Satzes, der seit WB-370 im Code angehängt wird und nicht mehr
        # im Prompt steht.
        begriffe, rest_angehaengt = self._rest_sichern(begriffe,
                                                       gefunden.rest)
        begriffe = self._zum_gericht(begriffe, gerichte_daten)

        aufgaben = self._suchen(con, begriffe)
        auswahl, choose_kaputt = self._waehlen(text, aufgaben)
        zeilen, freitext = self._zeilen(aufgaben, auswahl)

        erstes = quellen[0]
        teile = [f"„{titel}“ von Chefkoch — {len(zutaten)} Zutaten im Rezept, "
                 f"{len(begriffe)} davon auf dem Zettel, "
                 f"{len(begriffe) - len(freitext)} im Katalog gefunden."]
        if erstes.get("source_rating"):
            teile.append(f"Bestbewertetes Rezept zum Gericht "
                         f"({erstes['source_rating']:.2f} aus "
                         f"{erstes.get('source_votes') or 0} Stimmen).")
        if freitext:
            teile.append("Ohne Katalogtreffer und deshalb als Freitext: "
                         + ", ".join(f"„{f}“" for f in freitext) + ".")
        if gefunden.rest:
            teile.append(self._meldung_rest(gefunden.rest, rest_angehaengt))
        if notbehelf:
            teile.append(f"Das Modell hat die Zutatenliste nicht zerlegt "
                         f"({notbehelf}) — die Begriffe kommen roh aus dem "
                         "Rezept.")
        teile.extend(self._meldung_auswahl(auswahl, choose_kaputt))
        teile.append("Die Zubereitung steht unter Rezepte.")
        gericht = gefunden.rezepte[0]["query"]
        n_zutaten = sum(1 for z in zeilen if z.get("zum_gericht"))
        teile.append(self._meldung_entwurf(gericht, n_zutaten, namen))
        zusatz = {"gericht": gericht,
                  "quelle_name": titel,
                  "quelle_url": erstes.get("source_url"),
                  "quelle_recipe_id": int(erstes["id"]),
                  # Der Entwurf hängt am ERSTEN Gericht (WB-337) und an dessen
                  # Rezept — dort steht die Zubereitung, dort kommen die
                  # Produkte dazu.
                  "entwurf_name": gericht if n_zutaten else None,
                  # Was neben dem Gericht im Satz stand, und ob dieser Zug
                  # eine Zeile dafür angelegt hat (WB-370).
                  "rest": gefunden.rest,
                  "rest_angehaengt": rest_angehaengt}
        return (zeilen, " ".join(teile), begriffe, auswahl.verworfen, aufgaben,
                zusatz)

    def _meldung_rest(self, rest: str, angehaengt: bool) -> str:
        """Was mit dem Rest des Satzes geschah — in der ANTWORT (WB-370).

        Der Rest kommt seit WB-370 aus dem Code und nicht mehr aus dem
        Modell. Damit ist er nie mehr still weg — aber er ist auch nicht mehr
        übersetzt, und „Klopapier" findet im Katalog nichts. Diese eine Zeile
        ist der Preis, den die Nutzerin sehen muss: sie steht neben der
        Freitext-Zeile und sagt, warum sie da ist.
        """
        if angehaengt:
            return (f"„{rest}“ stand daneben im Satz und liegt als eigene "
                    "Zeile dabei — nicht im Rezept.")
        return (f"„{rest}“ stand daneben im Satz und steht schon auf dem "
                "Zettel.")

    def _rest_sichern(self, begriffe: list[dict],
                      rest: str | None) -> tuple[list[dict], bool | None]:
        """Der Rest des Satzes kommt im CODE auf den Zettel (WB-370).

        Gibt `(begriffe, angehaengt)` zurück; `angehaengt` ist `None`, wenn
        es gar keinen Rest gab.

        Was neben dem Gericht stand („… und Klopapier"), ging bis WB-370 als
        Zeile in den Prompt von Stufe 1 — und das Modell durfte sie
        übergehen. WB-337 hängte den Rest deshalb an, wenn KEIN einziger
        Begriff ohne Herkunftszutat zurückkam. Das Netz hatte einen blinden
        Fleck: nannte das Modell irgendetwas, das zu keiner Zutat gehört,
        galt der Rest als aufgegriffen.

        **Gemessen am 2026-08-28** gegen die echte Box (Qwen3.8-27B) und 35
        Chefkoch-Gerichte, je einmal mit einem Rest im Satz:

            mindestens ein Begriff ohne Herkunftszutat   11 von 35 (31 %)
            der Rest kam als Begriff zurück               3 von 35
            der Rest kam ÜBERSETZT zurück                 0 von 35
            der Rest ging dadurch still verloren         10 von 35 (29 %)

        Beide Hälften der Rechtfertigung von WB-337 fielen damit: das Netz
        greift in jedem dritten Zug daneben, und die Übersetzung
        („Klopapier" -> „Toilettenpapier"), für die die Prompt-Zeile dastand,
        liefert die Box kein einziges Mal. Der Rest steht seither nicht mehr
        im Prompt und wird hier angehängt — deterministisch, ohne Ermessen.

        **Der Preis steht in der Meldung.** Ohne Prompt-Zeile gibt es keine
        Übersetzung mehr: „Klopapier" findet im Katalog nichts,
        „Toilettenpapier" schon. Die Zeile liegt trotzdem im Korb, als
        Freitext, und der Zug sagt es (`_aus_quelle`). Ein Artikel, der
        sichtbar als Freitext dasteht, ist besser als einer, der still
        verschwindet.

        Verdoppelt wird nichts: steht der Rest schon auf dem Zettel, kommt er
        nicht ein zweites Mal. Das ist keine Vermutung über das Modell,
        sondern ein Wortvergleich mit denselben Regeln wie die
        Herkunftszuordnung (`herkunft.punkte`) — und er greift auch dort, wo
        der Rest mit einer Zutat des Rezepts zusammenfällt („alles für
        Lasagne und Tomaten").
        """
        if not rest:
            return begriffe, None
        if any(herkunft.punkte(b, rest) >= herkunft.SCHWELLE
               for eintrag in begriffe
               for b in (eintrag.get("suchbegriffe") or [])):
            return begriffe, False
        return [*begriffe, {"suchbegriffe": [rest], "menge": 1}], True

    def _zum_gericht(self, begriffe: list[dict],
                     gerichte_daten: list[dict]) -> list[dict]:
        """Markiert die Begriffe, die eine Zutat des Gerichts benennen.

        **Kein Modellfeld, keine zusätzliche Prompt-Zeile** (WB-337): die
        Begriffe sind aus einer bekannten Zutatenliste gemacht, also stehen
        deren Wörter noch darin, und `herkunft.zuordnen` findet sie ohne
        Modell wieder. Was eine Zutat gefunden hat, gehört zum Gericht — was
        keine gefunden hat, stand daneben im Satz. Das ist „Klopapier".

        Bei MEHREREN Gerichten in einem Satz zählt nur das erste, und die
        Zuordnung wird deshalb gegen dessen Zutatenliste allein noch einmal
        gerechnet: sonst wanderten die Zwiebeln des zweiten Gerichts in das
        Rezept des ersten. Mehrere Rezepte aus einem Satz sind ausdrücklich
        nicht Teil des Tickets, und die Zeilen tragen dann die Menge über
        BEIDE Gerichte — im Rezept steht also eine Menge, die für zwei
        Essen reicht. Der Fall setzt zwei bereits geholte Gerichte in einem
        Satz voraus; wo er auftritt, sagt es die Meldung.

        Die Vorsicht dieser Zuordnung ist gewollt und geht in die richtige
        Richtung: ein Begriff, der seiner Zutat nicht sicher zugeordnet
        werden kann, gehört nicht ins Rezept. Gemessen wurde die Lücke in
        WB-369 (eine von 38 Zuordnungen, „Brühwürfel" gegen
        „Gemüsebrühwürfel"); die Zutat fehlt dann im Rezept und lässt sich
        dort nachtragen — eine falsche Zutat liesse sich nicht mehr
        erkennen.
        """
        if len(gerichte_daten) > 1:
            erste = herkunft.zuordnen(gerichte_daten[0]["zutaten"], begriffe)
            return [{**b, "zum_gericht": bool(e.get("zutat"))}
                    for b, e in zip(begriffe, erste)]
        return [{**b, "zum_gericht": bool(b.get("zutat"))} for b in begriffe]

    def _meldung_entwurf(self, gericht: str, n_zutaten: int,
                         namen: list[str]) -> str:
        """Der Satz zum Rezeptentwurf. Sagt auch, wenn es keinen gibt.

        Ein Rezept entsteht hier ungefragt (beim Abschicken), also muss der
        Zug es sagen — sonst steht später etwas in der Sammlung, das niemand
        angelegt zu haben meint.
        """
        if not n_zutaten:
            return ("Ein Rezept wird daraus nicht: keine der Zeilen liess "
                    "sich einer Zutat des Rezepts zuordnen.")
        satz = (f"Daraus kann ein Rezept „{gericht}“ werden — {n_zutaten} "
                "Zutaten, gespeichert erst beim Abschicken.")
        if len(namen) > 1:
            satz += (f" Der Satz nennt mehrere Gerichte; der Entwurf ist der "
                     f"für „{gericht}“.")
        return satz

    def _ketten_ohne_modell(self, zutaten) -> list[dict]:
        """Begriffsketten direkt aus der Zutatenliste — der Notbehelf.

        Nur für den Fall, dass Stufe 1 die Liste nicht zerlegen konnte. Was
        in jedem Haushalt steht, fällt weg; alles andere kommt so, wie die
        Quelle es schreibt (mit umgedrehter Komma-Form, siehe
        `chefkoch.zutat_kette`).

        Der Rest des Satzes gehört seit WB-370 nicht mehr hierher: er wird an
        genau EINER Stelle angehängt (`_rest_sichern`), gleich ob Stufe 1
        geantwortet hat oder nicht. Zwei Stellen, die dasselbe anhängen,
        laufen irgendwann auseinander — und die eine legte dann eine Zeile
        doppelt hin.
        """
        begriffe, gesehen = [], set()
        for z in zutaten:
            roh = z.get("raw_name") or z.get("name") or ""
            if gerichte.chefkoch.ist_vorrat(roh):
                continue
            kette = gerichte.zutat_kette(roh)
            if not kette or kette[0].casefold() in gesehen:
                continue
            gesehen.add(kette[0].casefold())
            begriffe.append({"suchbegriffe": kette[:plan.MAX_KETTE],
                             "menge": 1})
            if len(begriffe) >= plan.MAX_BEGRIFFE:
                break
        # Auch der Notbehelf trägt die Mengen (WB-369). Er hat es sogar
        # leichter als der Modellweg: die Kette ist hier BUCHSTÄBLICH aus dem
        # Zutatennamen gebaut, die Zuordnung kann also gar nicht danebenliegen
        # — und sie läuft trotzdem durch dieselbe Funktion, damit es nicht
        # zwei Zuordnungen im Projekt gibt, die auseinanderlaufen können.
        return herkunft.zuordnen(zutaten, begriffe)

    # -- Die gewählten Sorten (WB-368) ------------------------------------

    def _aus_sorten(self, con, text: str, kategorie: str,
                    sorten: list[str]):
        """Eine gewählte Sorte -> der normale Kandidatenablauf.

        **Nur Stufe 2 ist eine andere.** Statt der Volltextsuche liefert der
        Kategoriebaum die Kandidaten (`catalog.search.in_sorte`); Stufe 3
        wählt daraus wie immer, die Kandidaten werden wie immer aufgehoben,
        und ein „Nein" zeigt sie als Alternativen (WB-359). Ein zweiter
        Auswahlmechanismus neben dem bestehenden entsteht dadurch nicht.

        Warum nicht doch gesucht wird, steht an `in_sorte()`: die Zahl neben
        der Sorte („Rohschinken & Bacon (58)") ist die Zahl der Produkte in
        genau dieser Kategorie, und eine FTS-Abfrage auf denselben Namen fände
        etwas anderes. Die Zusage wäre gebrochen, bevor der erste Kandidat
        dasteht.

        Stufe 1 entfällt ersatzlos: die Begriffe stehen bereits fest, sie sind
        die Sorten. Ein Modellaufruf, der „Salami" in „Salami" übersetzt, wäre
        Wartezeit ohne Ertrag.
        """
        zustand = self.zustand()
        if not zustand.bedient:
            raise ChatNichtVerfuegbar(zustand)

        aufgaben = self._sorten_suchen(con, kategorie, sorten)
        auswahl, choose_kaputt = self._waehlen(text, aufgaben,
                                               self.system_choose_sorte)
        zeilen, freitext = self._zeilen(aufgaben, auswahl)

        n = len(sorten)
        teile = [f"{n} Sorte{'n' if n != 1 else ''} aus „{kategorie}“: "
                 + ", ".join(f"„{s}“" for s in sorten) + ". "
                 f"{n - len(freitext)} davon mit einem Vorschlag."]
        if freitext:
            # **Nicht „im Katalog nicht gefunden".** Die Sorte steht im
            # Katalog, ihre Zahl war echt und ihre Kandidaten liegen an der
            # Zeile — das Modell hat sich nur nicht entschieden. Ein „Nein"
            # klappt sie auf.
            teile.append("Ohne Wahl des Modells und deshalb als Freitext: "
                         + ", ".join(f"„{f}“" for f in freitext)
                         + " — die Kandidaten stehen trotzdem an der Zeile.")
        teile.extend(self._meldung_auswahl(auswahl, choose_kaputt))
        begriffe = [{"suchbegriffe": [s], "menge": 1} for s in sorten]
        return (zeilen, " ".join(teile), begriffe, auswahl.verworfen, aufgaben,
                {"kategorie": kategorie, "gewaehlte_sorten": list(sorten)})

    def _sorten_suchen(self, con, kategorie: str,
                       sorten: list[str]) -> list[dict]:
        """Stufe 2 aus dem Kategoriebaum — ein RETRIEVER-Span je SORTE.

        Derselbe Span-Name und dieselbe Form wie bei der Suche (Spec 7.1):
        eine Sorte ist hier das, was sonst eine Zutat ist, und zwei Namen für
        dieselbe Stelle im Baum machten jede Auswertung über Stufe 2 zu einer
        Fallunterscheidung. Dass die Kandidaten aus der Kategorie kommen,
        steht als Attribut daneben und nicht im Spannamen.
        """
        aufgaben = []
        for sorte in sorten:
            with obs.retriever("catalog.search", suchbegriffe=[sorte]) as such:
                aufgehoben = search.in_sorte(con, kategorie, sorte,
                                             limit=self.anzeige_obergrenze)
                kandidaten = search.kuerze_kette(
                    aufgehoben, limit=self.kandidaten,
                    obergrenze=self.obergrenze)
                obs.dokumente(such, kandidaten)
                obs.setze(such, {"picknick.qty": 1,
                                 "picknick.candidates_kept": len(aufgehoben),
                                 # Woher die Kandidaten kamen. Ohne das sähe
                                 # der Span aus wie eine Suche, die zufällig
                                 # keinen Rang hat.
                                 "picknick.category": f"{kategorie} > {sorte}"})
            aufgaben.append({"suchbegriffe": [sorte], "menge": 1,
                             "begriff": sorte, "kandidaten": kandidaten,
                             "aufgehoben": aufgehoben,
                             # Es gibt keinen allgemeineren Begriff, auf den
                             # diese Zutat hätte ausweichen können — eine
                             # Sorte ist keine Kette.
                             "nur_allgemein": None})
        return aufgaben

    # -- Weg 3: Modell ----------------------------------------------------

    def _aus_modell(self, con, text: str, *, auffaechern: bool = True):
        """Die drei Stufen aus Spec 6.

        Reihenfolge mit Absicht: erst der Weckzustand (billig, und ohne
        bedienende Box hat der Rest keinen Sinn), dann Stufe 1, dann die
        Suche, dann Stufe 3.

        Gibt `(zeilen, meldung, begriffe, verworfen, aufgaben, zusatz)`
        zurück. `aufgaben` sind die Begriffe samt ihren Kandidaten — sie gehen
        nicht nur in Stufe 3, sondern auch in die Zusammenfassung auf dem
        CHAIN-Span (`_span_abschluss`).
        """
        zustand = self.zustand()
        if not zustand.bedient:
            raise ChatNichtVerfuegbar(zustand)

        # Die Kategorienliste geht NUR bei einem kurzen Satz mit (WB-368).
        # Damit ist der Aufruf für „alles für Pho" Wort für Wort derselbe wie
        # vor dem Ticket — dieselben Token, dieselbe Wartezeit —, und die
        # Frage nach dem Oberbegriff kostet keinen eigenen Modellaufruf,
        # sondern reist in dem mit, der den Satz ohnehin liest.
        kategorien = (oberbegriffe.namen(con)
                      if auffaechern and oberbegriffe.moeglich(text) else None)
        try:
            # `stufe()` benennt den Span, den der OpenAI-Instrumentor um
            # diesen Aufruf öffnet, in `plan.extract` um (Spec 7.1). Ein
            # eigener LLM-Span daneben würde die Tokenzahlen verdoppeln.
            with obs.stufe("plan.extract"):
                erst = plan.extract_plan(self.zugang, text, guided=self.guided,
                                         denken=self.denken,
                                         system=self.system_extract,
                                         kategorien=kategorien)
            begriffe = erst.zutaten
        except plan.PlanFehler as e:
            # Kein JSON, leeres Array, falscher Typ: daraus lässt sich nichts
            # bauen, ohne zu raten. Der Request bleibt heil, die Nutzerin
            # bekommt einen Satz statt einer Fehlerseite.
            return [], (f"Das Modell hat den Satz nicht in Suchbegriffe "
                        f"zerlegt ({e}). Schreib es anders — oder leg es "
                        "direkt aus dem Katalog ein."), [], [], [], {}
        except ModellNichtErreichbar as e:
            raise ChatNichtVerfuegbar(
                wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

        # Ein Oberbegriff, den der Katalog nicht wörtlich kennt (WB-368):
        # „Nudeln" steht dort unter „Reis, Pasta & Getreide", und diese
        # Übersetzung kann keine Zeichenkettenregel. Das Modell hat sie
        # gerade nebenbei geliefert — geprüft gegen die vorgelegte Liste, was
        # nicht darin stand, steht in `kategorie_verworfen` und wird NICHT
        # benutzt.
        #
        # Ein GERICHT schlägt den Oberbegriff: „Pho" ist beides kurz und
        # etwas, wofür ein Rezept vorliegt, und ein Rezept ist die bessere
        # Antwort als eine Rückfrage.
        faecher = None
        if kategorien and not erst.gericht:
            faecher = oberbegriffe.aus_modell(con, text, erst.kategorie)
        if faecher is not None:
            # Was Stufe 1 an Begriffen geraten hat, wird weggeworfen — wie
            # beim Wechsel auf den Quellenweg (WB-367). Es war der Preis
            # dafür, den Oberbegriff überhaupt zu erkennen, und eine
            # Vorschlagsliste neben einer Rückfrage wäre die schlechteste
            # aller Antworten.
            return ([], oberbegriffe.meldung(faecher), [], [], [],
                    {"weg": WEG_FAECHER, "faecher": faecher,
                     "sorten_verworfen": erst.kategorie_verworfen})
        if not begriffe:
            # Stufe 1 hat NUR eine Kategorie genannt, und die trägt nicht:
            # sie stand nicht in der Vorlage (gemessen: „Getränke" ->
            # „Alkoholfreie Alternatives", „Milch" -> „Milch") oder der
            # Katalog hat sie zwischen Vorlage und Nachschlagen verloren.
            #
            # **Dann wird nach dem gesucht, was DIE NUTZERIN getippt hat.**
            # Das ist kein Raten: das Wort kommt nicht aus dem Modell, es
            # steht im Satz. Eine Fehlermeldung wäre hier das schlechtere
            # Ende — vor diesem Ticket hätte derselbe Satz eine ganz normale
            # Suche ausgelöst, und genau die bekommt er auch jetzt.
            begriffe = [{"suchbegriffe": [text], "menge": 1}]

        # **Hier wird das Gericht GEHOLT, nicht angefordert** (WB-367).
        # Stufe 1 hat gerade 20 bis 35 s gebraucht; der Abruf daneben kostet
        # gemessen 90 bis 147 ms. Zwischen WB-338 und WB-367 wurde
        # stattdessen ein eigener Prozess angestossen und dieser Zug mit den
        # GERATENEN Begriffen zu Ende geführt — der erste Satz zu einem
        # neuen Gericht bekam also genau das, was WB-338 abschaffen wollte.
        abruf = None
        gefunden = None
        if erst.gericht:
            abruf = self._gericht_holen(con, erst.gericht)
            if abruf in (None, gerichte.OK):
                # `None` heisst „nicht abgerufen", und das schliesst den
                # Fall ein, dass schon ein frisches Rezept dasteht — eines,
                # das der Namensvergleich am Anfang des Zugs nicht gefunden
                # hat, weil das Gericht im Satz anders heisst als in `dish`.
                # Ohne diesen Zweig würde daneben geraten, obwohl das
                # Rezept vorliegt.
                gefunden = self._nach_abruf(con, text, erst.gericht)

        if gefunden is not None:
            # Der Zug wechselt den Weg. Was Stufe 1 geraten hat, wird
            # weggeworfen — es war der Preis dafür, den Gerichtsnamen
            # überhaupt zu kennen, und eine geratene Zutatenliste neben
            # einer echten stehen zu lassen wäre der schlechteste Ausgang.
            aus_quelle = self._aus_quelle(con, text, gefunden)
            if aus_quelle is not None:
                (zeilen, meldung, begriffe, verworfen, aufgaben,
                 zusatz) = aus_quelle
                return (zeilen, meldung, begriffe, verworfen, aufgaben,
                        {**zusatz, "abruf": abruf, "weg": WEG_QUELLE})

        aufgaben = self._suchen(con, begriffe)
        auswahl, choose_kaputt = self._waehlen(text, aufgaben)
        zeilen, freitext = self._zeilen(aufgaben, auswahl)

        meldung = self._meldung_modell(begriffe, freitext, auswahl,
                                       choose_kaputt, erst.gericht, abruf)
        return (zeilen, meldung, begriffe, auswahl.verworfen, aufgaben,
                {"gericht": erst.gericht, "abruf": abruf,
                 # Auch OHNE Auffächerung mitgeführt: eine erfundene Kategorie
                 # ist genau dann interessant, wenn sie nicht durchkam.
                 "sorten_verworfen": erst.kategorie_verworfen})

    def _gericht_holen(self, con, name: str) -> str | None:
        """Den Abruf anstossen und auf ihn warten — kurz (WB-367).

        Gibt zurück, was dabei herauskam (`ok`, `leer`, `fehler`) oder
        `None`, wenn nicht abgerufen wurde: der Speicher hatte schon etwas
        Frisches, oder ein anderer Zug ruft dasselbe Gericht gerade ab.

        **Was hier schiefgeht, kostet die Abkürzung und nicht den Zug.** Die
        geratenen Begriffe von Stufe 1 stehen bereits; ein Ausfall der
        Quelle führt zurück auf sie, nicht auf eine Fehlerseite.
        """
        try:
            return self.quelle.holen(con, name)
        except Exception:                        # noqa: BLE001 — bewusst breit
            return None

    def _nach_abruf(self, con, text: str, name: str):
        """Das eben geholte Gericht als Gerichtsweg — oder `None`.

        Zuerst derselbe Namensvergleich wie sonst (`_gericht_im_satz`): er
        schneidet den Gerichtsnamen sauber aus dem Satz und lässt „und
        Klopapier" als `rest` stehen. Steht der Name nicht wörtlich im Satz,
        weil Stufe 1 ihn herausgelesen hat („für ne Bolo" -> „Bolognese"),
        greift der Wortvergleich aus `rezeptweg.rest_ohne`. Beides ist
        besser als der dritte Ausgang: das Rezept liegt vor, und es wird
        trotzdem geraten.
        """
        gefunden = self._gericht_im_satz(con, text)
        if gefunden is not None:
            return gefunden
        try:
            zeile = self.quelle.zeile(con, name)
        except Exception:                        # noqa: BLE001 — bewusst breit
            return None
        if zeile is None:
            return None
        return rezeptweg.Rezeptweg(rezepte=[dict(zeile)],
                                   rest=rezeptweg.rest_ohne(text,
                                                            zeile["query"]))

    # -- Stufe 2 und 3, für beide Modellwege dieselben --------------------

    def _suchen(self, con, begriffe: list[dict]) -> list[dict]:
        """Stufe 2: der SHOP sucht, je Zutat ein RETRIEVER-Span (Spec 7.1).

        Ein Span je ZUTAT (WB-340) — nicht einer für alle Suchen zusammen und
        auch nicht einer je Begriff. Die Frage lautet „hat die Suche für
        DIESE Zutat etwas Brauchbares vorgelegt", und die vorgelegte Liste
        ist die VEREINIGUNG über die ganze Begriffskette; an einem Sammel-Span
        wäre die Frage nicht mehr zu stellen, an einem Span je Begriff die
        Vorlage nicht mehr zu sehen.
        """
        aufgaben = []
        for b in begriffe:
            kette = b["suchbegriffe"]
            with obs.retriever("catalog.search", suchbegriffe=kette) as such:
                # EINE Suche, zwei Listen (WB-359). Gesucht wird mit der
                # Anzeigegrenze, weil das die grössere ist; die Vorlage für
                # Stufe 3 wird daraus gekürzt, statt dieselben Begriffe ein
                # zweites Mal durch die Suche zu schicken. Damit ist die
                # Modellvorlage garantiert eine Teilmenge dessen, was
                # aufgehoben wird — „Nein" zeigt genau die Liste, aus der
                # gewählt wurde, und nicht eine zweite, neu gesuchte.
                aufgehoben = search.suche_kette(
                    con, kette, limit=self.anzeige,
                    obergrenze=self.anzeige_obergrenze)
                kandidaten = search.kuerze_kette(
                    aufgehoben, limit=self.kandidaten,
                    obergrenze=self.obergrenze)
                # In den Span gehen die Kandidaten des MODELLS: der
                # RETRIEVER-Span beantwortet die Frage „woraus hat Stufe 3
                # gewählt", und eine dreimal so lange Dokumentliste je Zutat
                # machte aus jedem Trace eine Wand. Wie viele aufgehoben
                # wurden, steht als Zahl daneben.
                obs.dokumente(such, kandidaten)
                obs.setze(such, {"picknick.qty": b["menge"],
                                 "picknick.candidates_kept": len(aufgehoben)})
            # `begriff` ist der genaueste Begriff der Kette und steht für die
            # Zutat: unter ihm wählt Stufe 3, und als Freitext steht er da,
            # wenn nichts gefunden wurde.
            aufgaben.append({**b, "begriff": kette[0],
                             "kandidaten": kandidaten,
                             "aufgehoben": aufgehoben,
                             "nur_allgemein": _nur_allgemein(kette,
                                                             aufgehoben)})
        return aufgaben

    def _waehlen(self, text: str, aufgaben: list[dict], system=None):
        """Stufe 3: das Modell wählt aus den VORGELEGTEN Kandidaten.

        `system` überschreibt den Prompt für DIESEN Aufruf — gebraucht wird
        das genau einmal (WB-368): bei einer gewählten Sorte ist der Begriff
        ein Regalname und kein Suchbegriff, und der gewöhnliche Prompt liess
        das Modell deshalb gar nichts wählen (siehe
        `plan.SYSTEM_CHOOSE_SORTE`). Die Stufe bleibt dieselbe, samt Span und
        Prüfung gegen die Vorlage.
        """
        try:
            with obs.stufe("plan.choose"):
                auswahl = plan.choose(self.zugang, text, aufgaben,
                                      guided=self.guided, denken=self.denken,
                                      system=system or self.system_choose)
            return auswahl, None
        except plan.PlanFehler as e:
            # Auch das kostet keinen Begriff: ohne Wahl wird JEDER Begriff zu
            # einem Freitext-Vorschlag. Lieber eine Liste, in der die Nutzerin
            # selbst sucht, als eine leere.
            return plan.Auswahl(), str(e)
        except ModellNichtErreichbar as e:
            raise ChatNichtVerfuegbar(
                wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

    def _zeilen(self, aufgaben: list[dict], auswahl):
        """Aus Aufgaben und Wahl die Vorschlagszeilen. Kein Begriff fällt weg.

        **Seit WB-369 trägt jede Zeile die benötigte Menge, wo es eine gibt**
        (`bedarf`, `einheit`) — sie kommt aus der Zutatenliste der Quelle und
        wandert beim „Ja" in `korb.einlegen(menge=…)`. Damit greift die
        Rechnung aus WB-362 auch auf dem Chat-Weg, und zwar erst NACH dem
        Zusammenzählen über alle Rezepte.

        `_qty` sagt, was daneben mit der geratenen Packungszahl des Modells
        geschieht.
        """
        gewaehlt = {w["begriff"]: w for w in auswahl.gewaehlt}
        zeilen, freitext = [], []
        for b in aufgaben:
            wahl = gewaehlt.get(b["begriff"])
            if wahl is not None:
                zeilen.append({"product_id": wahl["produkt"]["id"],
                               "free_text": None,
                               "qty": self._qty(b, wahl["menge"]),
                               "bedarf": b.get("bedarf"),
                               "einheit": b.get("einheit"),
                               # Die aufgehobenen Kandidaten gehen mit an die
                               # Zeile (WB-359) — sie sind das, was „Nein"
                               # zeigt. Und `fallback`: kam ALLES nur über den
                               # allgemeinsten Kettenbegriff, ist dieser
                               # Vorschlag kein sicherer Treffer.
                               "kandidaten": b["aufgehoben"],
                               "fallback": b["nur_allgemein"],
                               # NICHT die Zutat, sondern der Begriff der
                               # Kette, der DIESEN Kandidaten gebracht hat
                               # (WB-340). „Möhren" und „Karotten" führen zu
                               # verschiedenen Produkten; welcher der beiden
                               # es war, ist die Erklärung an der
                               # Eval-Annotation (WB-329) und wäre sonst
                               # geraten.
                               "search_term": (wahl["produkt"].get("via")
                                               or b["begriff"]),
                               "rang": wahl["produkt"].get("rang"),
                               # Gehört diese Zeile zum Gericht (WB-337)?
                               # Gesetzt hat das `_zum_gericht`; hier wird es
                               # nur weitergereicht, damit die Vorschlagszeile
                               # es trägt und nicht der Zug.
                               "zum_gericht": b.get("zum_gericht")})
                continue
            # Kein Treffer, keine Wahl oder eine verworfene ID — in allen drei
            # Fällen bleibt der Begriff stehen, als Freitext.
            zeilen.append({"product_id": None, "free_text": b["begriff"],
                           "qty": self._qty(b, b["menge"]),
                           "bedarf": b.get("bedarf"),
                           "einheit": b.get("einheit"),
                           "search_term": b["begriff"],
                           "rang": None,
                           "zum_gericht": b.get("zum_gericht"),
                           # Auch an einer Freitextzeile: hat die Suche etwas
                           # vorgelegt und das Modell nur nichts gewählt, ist
                           # die Liste da und einen Blick wert. Fand die Suche
                           # nichts (die echte Katalog-Lücke), ist sie
                           # leer — und die Zeile steht als Freitext da.
                           "kandidaten": b["aufgehoben"],
                           "fallback": None})
            freitext.append(b["begriff"])
        return zeilen, freitext

    @staticmethod
    def _qty(aufgabe: dict, geraten: int) -> int:
        """Die Packungszahl an der Vorschlagszeile — Regel 4 aus WB-369.

        **Wo eine echte Menge dasteht, wird die geratene Zahl nicht benutzt.**
        Das Modell kennt die Packungsgrösse nicht; seine „2" ist eine
        Vermutung darüber, wie viel in eine Packung passt, und daneben steht
        eine gemessene Zahl aus dem Rezept. Die Packungszahl entsteht dann in
        `korb.einlegen` aus der Summe über alle Rezepte.

        Die 1 ist dabei keine zweite Vermutung, sondern die Rückfallebene für
        den Fall, dass sich nichts ausrechnen lässt („4 Zehen" gegen
        „100 g"): dann liegt genau eine Packung im Korb, und die Zeile sagt
        warum (`mengen.Rechnung.grund`).

        Ohne Menge bleibt die geratene Zahl — sie ist dann das Einzige, was
        dasteht, und das ist seit WB-327 so.
        """
        return 1 if aufgabe.get("bedarf") is not None else geraten

    def _meldung_auswahl(self, auswahl, choose_kaputt) -> list[str]:
        """Was an Stufe 3 schiefging — auf beiden Modellwegen derselbe Satz."""
        teile = []
        if auswahl.verworfen:
            # Das ist die Zahl, um die es in Spec 6 geht. Sie wird angezeigt
            # und nicht bloss protokolliert: ein Modell, das erfindet, soll
            # man sehen können.
            teile.append(f"{len(auswahl.verworfen)} Antwort(en) des Modells "
                         "verworfen, weil sie kein vorgelegtes Produkt "
                         "nannten.")
        if choose_kaputt:
            teile.append(f"Die Auswahl des Modells war unbrauchbar "
                         f"({choose_kaputt}) — alles steht als Freitext da.")
        return teile

    def _meldung_modell(self, begriffe, freitext, auswahl, choose_kaputt,
                        gericht=None, abruf=None):
        """Der Satz über der Liste. Nennt beim Namen, was nicht geklappt hat."""
        n = len(begriffe)
        teile = [f"{n} Begriff{'e' if n != 1 else ''} aus dem Satz, "
                 f"{n - len(freitext)} davon im Katalog gefunden."]
        if freitext:
            teile.append("Ohne Katalogtreffer und deshalb als Freitext: "
                         + ", ".join(f"„{f}“" for f in freitext) + ".")
        teile.extend(self._meldung_auswahl(auswahl, choose_kaputt))
        if gericht:
            # **Ein geratenes Gericht wird benannt** (WB-338), und seit
            # WB-367 auch, WARUM geraten wurde. Vorher stand hier „frag
            # gleich noch einmal" — das war die Aufforderung, den Fehler
            # selbst auszubügeln. Jetzt ist der Abruf schon gelaufen, und
            # was übrig bleibt, ist eine Auskunft: Chefkoch kennt das
            # Gericht nicht, oder war gerade nicht zu erreichen.
            teile.append(self._meldung_abruf(gericht, abruf))
        return " ".join(teile)

    #: Warum die Zutaten trotz erkanntem Gericht geraten sind (WB-367). Die
    #: Fristen daneben sind die aus `speicher` — sie stehen im Satz, damit
    #: niemand fünf Minuten später dasselbe erwartet und etwas anderes
    #: bekommt.
    _ABRUF_GRUND = {
        gerichte.LEER: "Chefkoch kennt „{gericht}“ nicht (eine Woche gemerkt)",
        gerichte.FEHLER: "Chefkoch war für „{gericht}“ nicht zu erreichen "
                         "(eine Stunde gemerkt, danach wird es neu versucht)",
        # Geholt, aber trotzdem nicht benutzt: das Rezept ist zwischen Abruf
        # und Verwendung verschwunden oder kam ohne Zutaten. Selten — und
        # eine Meldung, die das verschweigt, wäre eine Lüge über die Herkunft.
        gerichte.OK: "„{gericht}“ liegt jetzt bei den Rezepten, war für "
                     "diesen Zug aber nicht verwertbar",
        None: "Zu „{gericht}“ liegt gerade kein Rezept von Chefkoch vor",
    }

    def _meldung_abruf(self, gericht: str, abruf: str | None) -> str:
        grund = self._ABRUF_GRUND.get(abruf, self._ABRUF_GRUND[None])
        return (grund.format(gericht=gericht)
                + " — die Zutaten hier hat das Modell aus dem Gedächtnis "
                  "genannt.")

    # -- Schreiben --------------------------------------------------------

    def _schreiben(self, con, order_id: int, text: str, weg: str,
                   zeilen: list[dict], meldung: str, span_id: str | None,
                   *, begriffe, verworfen, rezepte, gericht=None,
                   quelle_name=None, quelle_url=None, quelle_recipe_id=None,
                   abruf=None, faecher=None, kategorie=None,
                   gewaehlte_sorten=None, sorten_verworfen=None,
                   entwurf_name=None, rest=None,
                   rest_angehaengt=None) -> Ergebnis:
        """Nachrichten und Vorschläge in einem Zug — erst wenn alles steht.

        Die Vorschläge hängen an der Antwortzeile und nicht an der Frage: sie
        sind die Antwort. Beide Zeilen tragen dieselbe Span-ID, damit eine
        Annotation den Zug findet, egal von welcher Seite sie kommt.
        """
        vorschlaege.nachricht(con, order_id, vorschlaege.ROLLE_NUTZERIN, text,
                              span_id)
        antwort_id = vorschlaege.nachricht(con, order_id,
                                           vorschlaege.ROLLE_AGENT, meldung,
                                           span_id)
        if faecher is not None:
            # Die angebotenen Sorten hängen an derselben Antwortzeile wie
            # sonst die Vorschläge — und ausdrücklich NICHT in
            # `chat_suggestion`: eine Sorte ist keine Entscheidung über ein
            # Produkt und hätte in der Trefferquote aus Spec 8.1 nichts zu
            # suchen (siehe `db.chat_sorte`).
            oberbegriffe.merken(con, antwort_id, faecher)
        gesehen: set[tuple] = set()
        for z in _zusammengefasst(zeilen):
            schluessel = (z["product_id"], (z["free_text"] or "").casefold())
            if schluessel in gesehen:
                # Zwei Begriffe, dasselbe Produkt („Nudeln" und „Spaghetti").
                # Zwei gleiche Zeilen wären zweimal dieselbe Entscheidung.
                continue
            gesehen.add(schluessel)
            try:
                sid = vorschlaege.vorschlag(
                    con, antwort_id, product_id=z["product_id"],
                    free_text=z["free_text"], qty=z["qty"],
                    search_term=z["search_term"], rang=z["rang"],
                    fallback_term=z.get("fallback"),
                    # Die benötigte Menge (WB-369) — von hier an trägt sie
                    # die Zeile, bis das „Ja" sie an `korb.einlegen` gibt.
                    menge=z.get("bedarf"), einheit=z.get("einheit"),
                    # Und die Zugehörigkeit zum Gericht (WB-337). Sie steht
                    # an der Zeile und nicht am Zug, weil an dieser Zeile
                    # auch das Eval-Label hängt: beide müssen dasselbe
                    # „Nein" überleben.
                    dish_item=(entwurf.ZUTAT if z.get("zum_gericht")
                               else None))
                # Und hier werden die Kandidaten aufgehoben statt weggeworfen
                # (WB-359). Bis zu diesem Ticket endeten sie im Prompt von
                # Stufe 3 und im RETRIEVER-Span — die Nutzerin bekam sie nie
                # zu sehen, obwohl der Shop sie längst gesucht hatte.
                vorschlaege.kandidaten_merken(con, sid,
                                              z.get("kandidaten") or [])
            except (vorschlaege.VorschlagFehler, orders.UngueltigerPosten):
                # Ein Produkt, das zwischen Suche und Schreiben verschwunden
                # ist, oder ein leerer Begriff. Kostet eine Zeile, nicht den
                # ganzen Zug.
                continue
        # Der Rezeptentwurf (WB-337) — nach den Zeilen, weil er ohne sie
        # keiner wäre, und nur, wenn wirklich Gerichtszutaten dabei sind.
        # Kein Gericht im Satz heisst kein Entwurf, und dann läuft alles wie
        # vor diesem Ticket.
        zutaten_im_entwurf = 0
        if entwurf_name:
            entwurf.merken(con, antwort_id, dish=entwurf_name,
                           recipe_id=quelle_recipe_id)
            zutaten_im_entwurf = sum(
                1 for v in vorschlaege.liste(con, antwort_id)
                if v["zum_gericht"])
        return Ergebnis(
            weg=weg, order_id=order_id, satz=text, chat_message_id=antwort_id,
            entwurf=entwurf_name, entwurf_zutaten=zutaten_im_entwurf,
            vorschlaege=vorschlaege.liste(con, antwort_id), begriffe=begriffe,
            verworfen=verworfen, rezepte=rezepte, meldung=meldung,
            gericht=gericht, quelle_name=quelle_name, quelle_url=quelle_url,
            quelle_recipe_id=quelle_recipe_id, abruf=abruf,
            kategorie=(faecher.kategorie if faecher is not None else kategorie),
            sorten=(list(faecher.sorten) if faecher is not None else []),
            sorten_herkunft=(faecher.herkunft if faecher is not None else None),
            sorten_verworfen=sorten_verworfen,
            gewaehlte_sorten=list(gewaehlte_sorten or []),
            rest=rest, rest_angehaengt=rest_angehaengt)
