"""Der Chat-Zug: aus einem Satz wird eine Vorschlagsliste (Spec 6).

Genau zwei Wege, und welcher genommen wurde, wird festgehalten (`weg`, in
Spec 7.1 `picknick.path`), weil sich sonst später keine Auswertung mehr
trennen lässt:

* **`recipe`** — der Satz nennt ein gespeichertes Rezept. Dessen verknüpfte
  Produkte werden direkt vorgeschlagen: kein Modell, keine Suche, keine
  Wartezeit. Das ist auch der Weg, der noch funktioniert, wenn die vLLM-Box
  schläft.
* **`llm`** — die drei Stufen aus Spec 6: `plan.extract` (nur Begriffe),
  `catalog.search` (der SHOP sucht), `plan.choose` (Wahl aus den vorgelegten
  Kandidaten).

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
   (`vorschlaege.entscheiden`).

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
Fällt Phoenix aus, ändert sich an diesem Ablauf nichts: die Span-Aufrufe
sind dann No-Ops und der Export läuft ohnehin in einem anderen Thread
(`picknick.obs.otel`).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from picknick import obs, orders
from picknick.assistant import plan, rezeptweg, vorschlaege
from picknick.catalog import search
from picknick.llm import wake
from picknick.llm.client import ModellNichtErreichbar

#: Die beiden Wege. Englisch, weil sie so in Spec 7.1 als Span-Attribut
#: stehen und ein zweiter Name für dieselbe Sache eine Auswertung kostet.
WEG_REZEPT = "recipe"
WEG_LLM = "llm"


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

    @property
    def n_produkte(self) -> int:
        return sum(1 for v in self.vorschlaege if not v["ist_freitext"])

    @property
    def n_freitext(self) -> int:
        return sum(1 for v in self.vorschlaege if v["ist_freitext"])


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

    def __init__(self, zugang=None, *, wecker=None,
                 kandidaten: int = plan.KANDIDATEN,
                 obergrenze: int = plan.MAX_KANDIDATEN, guided: bool = True,
                 denken: bool = plan.DENKEN,
                 system_extract: str = plan.SYSTEM_EXTRACT,
                 system_choose: str = plan.SYSTEM_CHOOSE):
        self._zugang = zugang
        self._wecker = wecker
        # `kandidaten` gilt je BEGRIFF, `obergrenze` je ZUTAT: die Vereinigung
        # über eine Begriffskette (WB-340) wäre sonst so lang, dass drei
        # Begriffe mal fünf Treffer mal acht Zutaten den Prompt von Stufe 3
        # füllen. Die Begründung der Zahl steht an `plan.MAX_KANDIDATEN`.
        self.kandidaten = kandidaten
        self.obergrenze = obergrenze
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

            treffer = rezeptweg.erkenne(con, text)
            if treffer:
                plan_zeilen, meldung = self._aus_rezept(con, treffer)
                weg, begriffe, verworfen, aufgaben = WEG_REZEPT, [], [], []
            else:
                (plan_zeilen, meldung, begriffe, verworfen,
                 aufgaben) = self._aus_modell(con, text)
                weg = WEG_LLM

            ergebnis = self._schreiben(
                con, order_id, text, weg, plan_zeilen, meldung,
                # Die echte Span-ID, ausser ein Aufrufer gibt eine vor. Damit
                # findet eine Annotation aus Spec 8.1 später genau diesen Zug.
                span_id if span_id is not None else obs.span_id(span),
                begriffe=begriffe, verworfen=verworfen,
                rezepte=[r["name"] for r in treffer.rezepte])
            self._span_abschluss(span, ergebnis, aufgaben)
            return ergebnis

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

    # -- Weg 2: Modell ----------------------------------------------------

    def _aus_modell(self, con, text: str):
        """Die drei Stufen aus Spec 6.

        Reihenfolge mit Absicht: erst der Weckzustand (billig, und ohne
        bedienende Box hat der Rest keinen Sinn), dann Stufe 1, dann die
        Suche, dann Stufe 3.

        Gibt `(zeilen, meldung, begriffe, verworfen, aufgaben)` zurück.
        `aufgaben` sind die Begriffe samt ihren Kandidaten — sie gehen nicht
        nur in Stufe 3, sondern auch in die Zusammenfassung auf dem
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
                begriffe = plan.extract(self.zugang, text, guided=self.guided,
                                        denken=self.denken,
                                        system=self.system_extract)
        except plan.PlanFehler as e:
            # Kein JSON, leeres Array, falscher Typ: daraus lässt sich nichts
            # bauen, ohne zu raten. Der Request bleibt heil, die Nutzerin
            # bekommt einen Satz statt einer Fehlerseite.
            return [], (f"Das Modell hat den Satz nicht in Suchbegriffe "
                        f"zerlegt ({e}). Schreib es anders — oder leg es "
                        "direkt aus dem Katalog ein."), [], [], []
        except ModellNichtErreichbar as e:
            raise ChatNichtVerfuegbar(
                wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

        # Ein RETRIEVER-Span je ZUTAT (Spec 7.1, WB-340) — nicht einer für alle
        # Suchen zusammen und auch nicht einer je Begriff. Die Frage lautet
        # „hat die Suche für DIESE Zutat etwas Brauchbares vorgelegt", und die
        # vorgelegte Liste ist die VEREINIGUNG über die ganze Begriffskette;
        # an einem Sammel-Span wäre die Frage nicht mehr zu stellen, an einem
        # Span je Begriff die Vorlage nicht mehr zu sehen.
        aufgaben = []
        for b in begriffe:
            kette = b["suchbegriffe"]
            with obs.retriever("catalog.search", suchbegriffe=kette) as such:
                kandidaten = search.suche_kette(
                    con, kette, limit=self.kandidaten,
                    obergrenze=self.obergrenze)
                obs.dokumente(such, kandidaten)
                obs.setze(such, {"picknick.qty": b["menge"]})
            # `begriff` ist der genaueste Begriff der Kette und steht für die
            # Zutat: unter ihm wählt Stufe 3, und als Freitext steht er da,
            # wenn nichts gefunden wurde.
            aufgaben.append({**b, "begriff": kette[0],
                             "kandidaten": kandidaten})

        try:
            with obs.stufe("plan.choose"):
                auswahl = plan.choose(self.zugang, text, aufgaben,
                                      guided=self.guided, denken=self.denken,
                                      system=self.system_choose)
            choose_kaputt = None
        except plan.PlanFehler as e:
            # Auch das kostet keinen Begriff: ohne Wahl wird JEDER Begriff zu
            # einem Freitext-Vorschlag. Lieber eine Liste, in der die Nutzerin
            # selbst sucht, als eine leere.
            auswahl, choose_kaputt = plan.Auswahl(), str(e)
        except ModellNichtErreichbar as e:
            raise ChatNichtVerfuegbar(
                wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

        gewaehlt = {w["begriff"]: w for w in auswahl.gewaehlt}
        zeilen, freitext = [], []
        for b in aufgaben:
            wahl = gewaehlt.get(b["begriff"])
            if wahl is not None:
                zeilen.append({"product_id": wahl["produkt"]["id"],
                               "free_text": None, "qty": wahl["menge"],
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
                           "rang": None})
            freitext.append(b["begriff"])

        meldung = self._meldung_modell(begriffe, freitext, auswahl,
                                       choose_kaputt)
        return zeilen, meldung, begriffe, auswahl.verworfen, aufgaben

    def _meldung_modell(self, begriffe, freitext, auswahl, choose_kaputt):
        """Der Satz über der Liste. Nennt beim Namen, was nicht geklappt hat."""
        n = len(begriffe)
        teile = [f"{n} Begriff{'e' if n != 1 else ''} aus dem Satz, "
                 f"{n - len(freitext)} davon im Katalog gefunden."]
        if freitext:
            teile.append("Ohne Katalogtreffer und deshalb als Freitext: "
                         + ", ".join(f"„{f}“" for f in freitext) + ".")
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
        return " ".join(teile)

    # -- Schreiben --------------------------------------------------------

    def _schreiben(self, con, order_id: int, text: str, weg: str,
                   zeilen: list[dict], meldung: str, span_id: str | None,
                   *, begriffe, verworfen, rezepte) -> Ergebnis:
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
                vorschlaege.vorschlag(
                    con, antwort_id, product_id=z["product_id"],
                    free_text=z["free_text"], qty=z["qty"],
                    search_term=z["search_term"], rang=z["rang"])
            except (vorschlaege.VorschlagFehler, orders.UngueltigerPosten):
                # Ein Produkt, das zwischen Suche und Schreiben verschwunden
                # ist, oder ein leerer Begriff. Kostet eine Zeile, nicht den
                # ganzen Zug.
                continue
        return Ergebnis(
            weg=weg, order_id=order_id, satz=text, chat_message_id=antwort_id,
            vorschlaege=vorschlaege.liste(con, antwort_id), begriffe=begriffe,
            verworfen=verworfen, rezepte=rezepte, meldung=meldung)
