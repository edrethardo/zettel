"""Der Chat-Zug: aus einem Satz wird eine Vorschlagsliste (Spec 6).

Genau drei Wege, und welcher genommen wurde, wird festgehalten (`weg`, in
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
   man erst im Laden.
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

from picknick import gerichte, obs, orders
from picknick.assistant import plan, rezeptweg, vorschlaege
from picknick.catalog import search
from picknick.llm import wake
from picknick.llm.client import ModellNichtErreichbar

#: Die drei Wege. Englisch, weil sie so in Spec 7.1 als Span-Attribut
#: stehen und ein zweiter Name für dieselbe Sache eine Auswertung kostet.
WEG_REZEPT = "recipe"
WEG_LLM = "llm"
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
    #: Dieser Zug hat einen Abruf angestossen. Der nächste Zug mit demselben
    #: Gericht nimmt die Quelle.
    angefordert: bool = False

    @property
    def n_produkte(self) -> int:
        return sum(1 for v in self.vorschlaege if not v["ist_freitext"])

    @property
    def n_freitext(self) -> int:
        return sum(1 for v in self.vorschlaege if v["ist_freitext"])


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
                 system_extract: str = plan.SYSTEM_EXTRACT,
                 system_choose: str = plan.SYSTEM_CHOOSE):
        self._zugang = zugang
        self._wecker = wecker
        # Die Gerichtequelle (WB-338). Wie `zugang` und `wecker`: hier nur
        # gehalten, nichts gebaut und nichts gefragt. `Quelle()` öffnet keine
        # Verbindung, geht nie selbst ins Netz und startet erst dann einen
        # eigenen Prozess, wenn ein Zug ein unbekanntes Gericht nennt.
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
        self.system_extract = system_extract
        self.system_choose = system_choose

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

    # -- Der Zug ----------------------------------------------------------

    def turn(self, con: sqlite3.Connection, satz: str,
             span_id: str | None = None) -> Ergebnis:
        """Ein Satz -> eine Vorschlagsliste. Legt nichts in den Warenkorb.

        Wirft `ChatFehler`, wenn der Satz leer ist, und `ChatNichtVerfuegbar`,
        wenn der Modellweg nötig wäre und die Box nicht bedient. Eine kaputte
        Modellantwort wirft NICHT: sie wird zu einer ehrlichen Meldung und,
        wo möglich, zu Freitext-Vorschlägen. Der Request darf daran nicht
        zerbrechen.
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
            # 3. Das Modell bleibt für alles zuständig, was kein Gericht ist —
            #    und für den ersten Zug zu einem Gericht, das noch niemand
            #    geholt hat.
            treffer = rezeptweg.erkenne(con, text)
            gerichtsweg = None
            if not treffer:
                gerichtsweg = self._gericht_im_satz(con, text)

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
            elif aus_quelle is not None:
                (plan_zeilen, meldung, begriffe, verworfen, aufgaben,
                 zusatz) = aus_quelle
                weg = WEG_QUELLE
            else:
                (plan_zeilen, meldung, begriffe, verworfen, aufgaben,
                 zusatz) = self._aus_modell(con, text)
                weg = WEG_LLM

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
            "picknick.dish_requested": ergebnis.angefordert or None,
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
            zeilen.append({"product_id": z["product_id"],
                           "free_text": z["free_text"],
                           "qty": z["qty"], "search_term": z["rezept"],
                           "rang": None})
        namen = ", ".join(r["name"] for r in treffer.rezepte)
        teile = [f"„{namen}“ — {len(zeilen)} Zutaten aus dem Rezept, "
                 "ohne Modell."]
        if treffer.rest:
            zeilen.append({"product_id": None, "free_text": treffer.rest,
                           "qty": 1, "search_term": treffer.rest, "rang": None})
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
                    rest=gefunden.rest, guided=self.guided,
                    denken=self.denken)
            notbehelf = None
        except plan.PlanFehler as e:
            # **Hier wird nicht abgebrochen, und das ist der Unterschied zum
            # Modellweg.** Dort gibt es ohne Stufe 1 nichts; hier liegt die
            # Zutatenliste bereits vor, und aus ihr lässt sich ohne Modell
            # eine Begriffskette bauen (`chefkoch.zutat_kette`). Sie ist
            # schlechter — gemessen 10 von 14 statt 12 von 12 — aber sie ist
            # da, und eine Liste, in der zwei Zeilen als Freitext stehen, ist
            # besser als eine Fehlermeldung.
            begriffe = self._ketten_ohne_modell(zutaten, gefunden.rest)
            notbehelf = str(e)
            if not begriffe:
                raise
        except ModellNichtErreichbar as e:
            raise ChatNichtVerfuegbar(
                wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

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
        if notbehelf:
            teile.append(f"Das Modell hat die Zutatenliste nicht zerlegt "
                         f"({notbehelf}) — die Begriffe kommen roh aus dem "
                         "Rezept.")
        teile.extend(self._meldung_auswahl(auswahl, choose_kaputt))
        teile.append("Die Zubereitung steht unter Rezepte.")
        zusatz = {"gericht": gefunden.rezepte[0]["query"],
                  "quelle_name": titel,
                  "quelle_url": erstes.get("source_url"),
                  "quelle_recipe_id": int(erstes["id"])}
        return (zeilen, " ".join(teile), begriffe, auswahl.verworfen, aufgaben,
                zusatz)

    def _ketten_ohne_modell(self, zutaten, rest=None) -> list[dict]:
        """Begriffsketten direkt aus der Zutatenliste — der Notbehelf.

        Nur für den Fall, dass Stufe 1 die Liste nicht zerlegen konnte. Was
        in jedem Haushalt steht, fällt weg; alles andere kommt so, wie die
        Quelle es schreibt (mit umgedrehter Komma-Form, siehe
        `chefkoch.zutat_kette`).
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
        if rest:
            begriffe.append({"suchbegriffe": [rest], "menge": 1})
        return begriffe

    # -- Weg 3: Modell ----------------------------------------------------

    def _aus_modell(self, con, text: str):
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

        try:
            # `stufe()` benennt den Span, den der OpenAI-Instrumentor um
            # diesen Aufruf öffnet, in `plan.extract` um (Spec 7.1). Ein
            # eigener LLM-Span daneben würde die Tokenzahlen verdoppeln.
            with obs.stufe("plan.extract"):
                erst = plan.extract_plan(self.zugang, text, guided=self.guided,
                                         denken=self.denken,
                                         system=self.system_extract)
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

        # **Hier wird das Gericht angefordert, nicht abgewartet** (WB-338).
        # Der Zug läuft mit den geratenen Begriffen zu Ende — der Abruf
        # läuft daneben in einem eigenen Prozess, und der NÄCHSTE Satz mit
        # demselben Gericht nimmt die Quelle. Zu warten hiesse, einen
        # Request an eine fremde Seite zu hängen (Spec 3).
        angefordert = False
        if erst.gericht:
            try:
                angefordert = self.quelle.anfordern(con, erst.gericht)
            except Exception:                    # noqa: BLE001 — bewusst breit
                # Eine Quelle, die nicht will, kostet die Abkürzung und
                # sonst nichts. Der Zug steht bereits.
                angefordert = False

        aufgaben = self._suchen(con, begriffe)
        auswahl, choose_kaputt = self._waehlen(text, aufgaben)
        zeilen, freitext = self._zeilen(aufgaben, auswahl)

        meldung = self._meldung_modell(begriffe, freitext, auswahl,
                                       choose_kaputt, erst.gericht,
                                       angefordert)
        return (zeilen, meldung, begriffe, auswahl.verworfen, aufgaben,
                {"gericht": erst.gericht, "angefordert": angefordert})

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

    def _waehlen(self, text: str, aufgaben: list[dict]):
        """Stufe 3: das Modell wählt aus den VORGELEGTEN Kandidaten."""
        try:
            with obs.stufe("plan.choose"):
                auswahl = plan.choose(self.zugang, text, aufgaben,
                                      guided=self.guided, denken=self.denken,
                                      system=self.system_choose)
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
        """Aus Aufgaben und Wahl die Vorschlagszeilen. Kein Begriff fällt weg."""
        gewaehlt = {w["begriff"]: w for w in auswahl.gewaehlt}
        zeilen, freitext = [], []
        for b in aufgaben:
            wahl = gewaehlt.get(b["begriff"])
            if wahl is not None:
                zeilen.append({"product_id": wahl["produkt"]["id"],
                               "free_text": None, "qty": wahl["menge"],
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
                               "rang": wahl["produkt"].get("rang")})
                continue
            # Kein Treffer, keine Wahl oder eine verworfene ID — in allen drei
            # Fällen bleibt der Begriff stehen, als Freitext.
            zeilen.append({"product_id": None, "free_text": b["begriff"],
                           "qty": b["menge"], "search_term": b["begriff"],
                           "rang": None,
                           # Auch an einer Freitextzeile: hat die Suche etwas
                           # vorgelegt und das Modell nur nichts gewählt, ist
                           # die Liste da und einen Blick wert. Fand die Suche
                           # nichts (die echte Katalog-Lücke), ist sie
                           # leer — und die Zeile steht als Freitext da.
                           "kandidaten": b["aufgehoben"],
                           "fallback": None})
            freitext.append(b["begriff"])
        return zeilen, freitext

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
                        gericht=None, angefordert=False):
        """Der Satz über der Liste. Nennt beim Namen, was nicht geklappt hat."""
        n = len(begriffe)
        teile = [f"{n} Begriff{'e' if n != 1 else ''} aus dem Satz, "
                 f"{n - len(freitext)} davon im Katalog gefunden."]
        if freitext:
            teile.append("Ohne Katalogtreffer und deshalb als Freitext: "
                         + ", ".join(f"„{f}“" for f in freitext) + ".")
        teile.extend(self._meldung_auswahl(auswahl, choose_kaputt))
        if angefordert:
            # Ehrlich benennen, was gerade passiert und was es bringt. Ohne
            # diesen Satz sähe die Nutzerin beim ersten „alles für Pho" eine
            # geratene Liste und beim zweiten eine ganz andere, ohne zu
            # wissen, warum.
            teile.append(f"Die Zutaten hier hat das Modell aus dem Gedächtnis "
                         f"genannt. „{gericht}“ wird gerade bei Chefkoch "
                         "geholt — frag gleich noch einmal, dann kommen sie "
                         "aus einem echten Rezept.")
        return " ".join(teile)

    # -- Schreiben --------------------------------------------------------

    def _schreiben(self, con, order_id: int, text: str, weg: str,
                   zeilen: list[dict], meldung: str, span_id: str | None,
                   *, begriffe, verworfen, rezepte, gericht=None,
                   quelle_name=None, quelle_url=None, quelle_recipe_id=None,
                   angefordert=False) -> Ergebnis:
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
        gesehen = set()
        for z in zeilen:
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
                    fallback_term=z.get("fallback"))
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
        return Ergebnis(
            weg=weg, order_id=order_id, satz=text, chat_message_id=antwort_id,
            vorschlaege=vorschlaege.liste(con, antwort_id), begriffe=begriffe,
            verworfen=verworfen, rezepte=rezepte, meldung=meldung,
            gericht=gericht, quelle_name=quelle_name, quelle_url=quelle_url,
            quelle_recipe_id=quelle_recipe_id, angefordert=angefordert)
