"""Experiments über `zettel-anfragen` (Spec 8.3).

Dieselbe Pipeline, dasselbe Dataset, **je eine Stellschraube verändert**. Das
ist der ganze Punkt: „der Agent ist besser geworden" ist ohne einen zweiten
Lauf am selben Dataset eine Behauptung.

Was gemessen wird
-----------------

Zwei deterministische Bewertungen und ein LLM-Judge. Zwei deterministische
und nicht eine, weil sie verschiedene Fehler sehen und der teurere von beiden
sonst unsichtbar bleibt (siehe `evals/dataset.py`, „Zwei Zahlen"):

* `zutaten_vollstaendigkeit` — **Recall auf Kategorieebene.** Für jede
  erwartete Pflichtzutat: liegt IRGENDEIN vorgeschlagenes Produkt in einer
  ihrer Kategorien? Bewusst ohne Rücksicht darauf, unter welchem Begriff es
  gefunden wurde — gesucht wird die Vollständigkeit des Einkaufs, nicht die
  Sauberkeit der Zuordnung. Der Bolognese-Lauf aus WB-327 (Zwiebeln und
  Knoblauch vergessen) bekommt hier 0,6 und in jeder Präzisionsmetrik eine
  1,0. Genau diese Lücke schliesst der Score.
* `kategorie_praezision` — **liegt das gewählte Produkt in der erwarteten
  Kategorie?** Zugeordnet wird über den Suchbegriff des Vorschlags: der
  Begriff „Spaghetti" gehört zur erwarteten Zutat „Nudeln", also muss das
  gewählte Produkt in `Reis, Pasta & Getreide > Pasta` liegen. Der
  Spaghettilöffel aus `Haushaltsartikel > Küchenhelfer` — im echten Katalog
  der bestplatzierte Treffer für „Spaghetti" — ist damit ein Fehlgriff und
  keine Auslegungssache.
* `llm_urteil` — der Judge, und **nur für die Fälle, in denen „richtig"
  Ermessenssache ist** (`metadata.ermessen`). „Käse für die Nudeln" ist mit
  einem Blauschimmelkäse kategorial richtig und praktisch falsch; das kann
  keine Kategorieprüfung entscheiden. Bei allen anderen Beispielen gibt der
  Judge ausdrücklich KEINE Zahl ab, statt eine zu erfinden.

Was ausdrücklich NICHT gemessen wird
------------------------------------

**`zettel.weakest_rank`.** Der bm25-Rang steht am Span und ist verlockend,
aber er ist über Abfragen hinweg nicht geeicht: ein seltenes Wort bekommt
strukturell einen höheren Rang als ein häufiges (WB-328). Eine Schwelle
darauf misst den Katalog und nicht den Agenten — und sie würde jedes Mal
kippen, wenn der Crawler etwas Neues einsammelt.

Die dritte Variante ist getauscht
---------------------------------

Spec 8.3 nennt als dritte Stellschraube „lokales Qwen gegen ein grösseres
Modell". **Diese Variante ist nicht fahrbar**: auf der Box liegt genau ein
Modell (`/v1/models` führt `Qwen3.8-27B-Instruct` und sonst nichts), und ein
Zukauf über die Claude-API ist vom Nutzer ausdrücklich abgelehnt (Spec 16,
„Claude-API als LLM-Fallback"). Ein Vergleich, für den es keinen zweiten Lauf
gibt, wird hier nicht behauptet und schon gar nicht mit Zahlen ausgestattet.

An ihre Stelle tritt `ohne-guided`: **mit gegen ohne `guided_json`.** Das ist
eine Stellschraube, die es wirklich gibt, die bereits verdrahtet ist
(`Chat(guided=…)`) und die eine offene Frage des Projekts beantwortet. Der
Modul-Docstring von `plan.py` behauptet, Guided Decoding erzwinge die Form und
nicht die Wahrheit, und das nachsichtige Parsen müsse auch ohne Schema tragen
— hier steht die Behauptung zum ersten Mal auf dem Prüfstand.

Gemessen am 2026-08-28 gegen `Qwen3.8-27B-Instruct`: **alle zwölf Ausgaben
waren wortgleich**, mit und ohne Schema, bis auf die Produkt-ID. Bei
`temperature=0` und einem Modell, das sich ohnehin an das Format hält, ist die
Einschränkung nie bindend. Das ist ein Ergebnis und keine kaputte Variante —
und es sagt, dass `guided_json` hier eine Versicherung gegen ein anderes
Modell ist und keine Verbesserung dieses einen.

Handarbeit, kein Gate
---------------------

    .venv/bin/python evals/dataset.py --pruefen       # ohne Phoenix, ohne Box
    .venv/bin/python evals/dataset.py                 # braucht Phoenix
    .venv/bin/python evals/experiment.py --liste
    .venv/bin/python evals/experiment.py basis prompt-b

Die Läufe brauchen die vLLM-Box und ein laufendes Phoenix. `checks/smoke.py`
und `pytest` brauchen beides nicht — was hier rein rechnet, steht in Funktionen
ohne Netz und wird in `tests/test_evals.py` genau so geprüft.

Der Katalog wird nicht angefasst
--------------------------------

Jeder Lauf arbeitet auf einer **Kopie** von `data/picknick.db` (`VACUUM INTO`,
also ein in sich konsistenter Stand). Ein Chat-Zug schreibt Nachrichten und
Vorschläge in den Warenkorb — vierundzwanzig Züge über vier Varianten hätten
den echten Warenkorb der beiden mit Testrauschen gefüllt, und die Annotationen
aus Spec 8.1 gleich mit.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Siehe evals/dataset.py — damit `python evals/experiment.py` läuft.
_WURZEL = str(Path(__file__).resolve().parent.parent)
if _WURZEL not in sys.path:
    sys.path.insert(0, _WURZEL)

from evals import dataset  # noqa: E402
from zettel.assistant import plan  # noqa: E402

#: Das Projekt, in dem die Chat-Spans der Läufe landen. Dasselbe wie im
#: Betrieb (`obs.PROJEKT`), damit ein Experiment-Trace neben einem echten Zug
#: liegt und sich vergleichen lässt.
PROJEKT = "Zettel Agent"


# --------------------------------------------------------------------------
# Prompt-Variante B für plan.extract
#
# B unterscheidet sich von A (`plan.SYSTEM_EXTRACT`) in genau einem Punkt:
# der Regel für Gerichte. A sagt „die Zutaten, die man dafür kaufen muss"; B
# verlangt, das Rezept im Kopf durchzugehen, und stellt Vollständigkeit
# ausdrücklich über Kürze.
#
# Der Anlass ist gemessen und nicht ausgedacht: die Handprobe aus WB-327
# lieferte für „alles für Spaghetti Bolognese" die Begriffe
# `Spaghetti, Hackfleisch, passierte Tomaten, Klopapier` — Zwiebeln und
# Knoblauch fehlten. Die Regel ist trotzdem allgemein formuliert und nennt
# keine Zutat aus dem Dataset; ein Prompt, der „vergiss die Zwiebeln nicht"
# sagt, gewönne diesen Vergleich, ohne irgendetwas zu können.

SYSTEM_EXTRACT_B = """\
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
- Bei einem Gericht gehst du das Rezept im Kopf Schritt für Schritt durch und \
nennst JEDE Zutat, die man dafür einkaufen muss — auch die \
selbstverständlichen, die man beim Aufzählen leicht überspringt. \
Vollständigkeit geht vor Kürze: eine Zutat zu viel kostet einen Klick, eine \
fehlende einen zweiten Weg in den Laden.
- Was in jedem Haushalt steht (Salz, Pfeffer, Wasser, Öl, Gewürze), lässt du \
trotzdem weg.
- Die Menge ist die Anzahl Packungen, die gekauft werden soll. Im Zweifel 1.
- Nichts erfinden, was im Satz nicht vorkommt oder zum Gericht nicht gehört.

Antworte ausschliesslich als JSON:
{"begriffe": [{"suchbegriffe": ["Rinderhackfleisch", "Hackfleisch"], \
"menge": 1}, {"suchbegriffe": ["passierte Tomaten", "Tomaten"], "menge": 2}]}"""


@dataclass(frozen=True)
class Variante:
    """Eine Stellschraube, um genau einen Wert gegen die Basis verschoben.

    Mehr als eine auf einmal zu verändern wäre kein Experiment, sondern ein
    neuer Agent: man wüsste hinterher nicht, welche Änderung die Zahl bewegt
    hat.
    """
    name: str
    beschreibung: str
    system_extract: str = plan.SYSTEM_EXTRACT
    kandidaten: int = plan.KANDIDATEN_MODELL
    guided: bool = True

    def als_metadaten(self) -> dict:
        return {"variante": self.name,
                "prompt_extract": ("B" if self.system_extract
                                   == SYSTEM_EXTRACT_B else "A"),
                "kandidaten": self.kandidaten,
                "guided_json": self.guided}


VARIANTEN = {
    v.name: v for v in [
        Variante("basis",
                 "Prompt A, 5 Kandidaten, guided_json an — der Betriebsstand."),
        Variante("prompt-b",
                 "Prompt B für plan.extract: Vollständigkeit vor Kürze.",
                 system_extract=SYSTEM_EXTRACT_B),
        Variante("kandidaten-20",
                 "20 statt 5 vorgelegte Kandidaten je Begriff für plan.choose.",
                 kandidaten=20),
        Variante("ohne-guided",
                 "Ohne guided_json — trägt der Prompt allein? "
                 "(Ersatz für die nicht fahrbare Modellvariante, siehe "
                 "Modul-Docstring.)",
                 guided=False),
    ]
}

BASIS = "basis"


# --------------------------------------------------------------------------
# Die Aufgabe: ein Chat-Zug je Beispiel

def kategorien(con, ids) -> dict[int, str]:
    """Kategoriepfade zu Produkt-IDs, in einer Abfrage.

    Die Vorschlagsliste des Chats führt die Kategoriespalten nicht mit — sie
    braucht sie in der Oberfläche nicht. Für die Bewertung sind sie das
    Einzige, worauf es ankommt, also werden sie hier nachgeschlagen, solange
    die Verbindung noch offen ist. `vorschlaege.liste()` dafür umzubauen wäre
    ein Eingriff in den Nutzerweg für eine Auswertung — der falsche Ort.
    """
    schluessel = sorted({int(i) for i in ids if i is not None})
    if not schluessel:
        return {}
    platzhalter = ", ".join("?" * len(schluessel))
    rows = con.execute(
        "SELECT id, category_l1, category_l2, category_l3 FROM product"
        f" WHERE id IN ({platzhalter})", schluessel).fetchall()
    return {int(r["id"]): dataset.pfad(dict(r)) for r in rows}


def als_ausgabe(ergebnis, kategorie_je_id: dict[int, str] | None = None) -> dict:
    """Ein `chat.Ergebnis` in die Form, die in Phoenix landet.

    Die Kategorie steht als Pfad an jedem Vorschlag — **das ist der Grund,
    warum die Bewertung ohne einen zweiten Blick in die Datenbank auskommt.**
    Eine Ausgabe, die nur Produkt-IDs enthielte, wäre in Phoenix nicht mehr zu
    lesen, sobald der Katalog sich gedreht hat.
    """
    je_id = kategorie_je_id or {}
    return {
        "weg": ergebnis.weg,
        "meldung": ergebnis.meldung,
        # Seit WB-340 die ganze Begriffskette je Zutat, nicht ein Begriff:
        # welcher davon den Treffer brachte, steht am Vorschlag als
        # `search_term`.
        "begriffe": [{"suchbegriffe": list(b["suchbegriffe"]),
                      "menge": b["menge"]}
                     for b in ergebnis.begriffe],
        "vorschlaege": [
            {"product_id": v["product_id"],
             "name": v["name"],
             "kategorie": je_id.get(v["product_id"], ""),
             "search_term": v["search_term"],
             "menge": v["qty"],
             "freitext": bool(v["ist_freitext"])}
            for v in ergebnis.vorschlaege],
        # Wie oft das Modell eine ID nannte, die ihm nie vorgelegt wurde
        # (Spec 6). Kein Score, aber die Zahl, die man beim Vergleich zweier
        # Varianten als Erstes sehen will.
        "verworfen": len(ergebnis.verworfen),
    }


class Lauf:
    """Ein Variantenlauf: eine Katalogkopie, ein `Chat`, eine Aufgabe.

    Die Kopie entsteht einmal je Lauf und wird am Ende weggeräumt. Sie ist
    nicht bloss Hygiene: ohne sie schriebe jeder Experimentzug in den echten
    Warenkorb, und die Annotationen aus Spec 8.1 stünden anschliessend neben
    Vorschlägen, über die nie ein Mensch entschieden hat.
    """

    def __init__(self, variante: Variante, *, zugang=None,
                 db_pfad: str | None = None, wecker=None):
        self.variante = variante
        self._zugang = zugang
        self._wecker = wecker
        self._quelle = db_pfad
        self._ordner: str | None = None
        self._kopie: str | None = None

    def __enter__(self) -> "Lauf":
        from zettel import db

        quelle = self._quelle or db.DEFAULT_DB
        self._ordner = tempfile.mkdtemp(prefix="zettel-eval-")
        self._kopie = str(Path(self._ordner) / "katalog.db")
        con = db.connect(quelle)
        try:
            # `VACUUM INTO` statt `shutil.copy`: die Datei läuft im
            # WAL-Modus, eine rohe Kopie ohne das -wal daneben wäre ein
            # älterer Stand — und zwar stillschweigend.
            con.execute("VACUUM INTO ?", (self._kopie,))
        finally:
            con.close()
        return self

    def __exit__(self, *_) -> None:
        if self._ordner:
            shutil.rmtree(self._ordner, ignore_errors=True)
            self._ordner = self._kopie = None

    @property
    def chat(self):
        from zettel.assistant.chat import Chat

        from zettel import gerichte

        if not hasattr(self, "_chat"):
            self._chat = Chat(
                self._zugang, wecker=self._wecker,
                kandidaten=self.variante.kandidaten,
                guided=self.variante.guided,
                system_extract=self.variante.system_extract,
                # **Der Lauf holt keine Gerichte nach** (WB-338, WB-367):
                # der Zwischenspeicher der Kopie wird gelesen, aber nicht
                # gefüllt. Sonst holte der erste Satz eines Datasets ein
                # Rezept, und der zweite Lauf derselben Variante liefe gegen
                # eine andere Grundlage als der erste — zwei Varianten wären
                # dann nicht mehr vergleichbar. Seit WB-367 wiegt das
                # schwerer: der Abruf läuft im Zug selbst, ein Lauf über
                # dreissig Sätze fasste also dreissigmal eine fremde Seite
                # an. Wer die Quelle messen will, füllt den Speicher VOR dem
                # Lauf (`python -m zettel.gerichte.lauf --gericht …`).
                quelle=gerichte.Quelle(holer=gerichte.nicht_holen))
        return self._chat

    def einmal(self, satz: str) -> dict:
        """Ein Chat-Zug auf der Kopie. Genau der Weg aus dem Betrieb.

        Kein `try`: fällt die Box aus, wirft `turn()` `ChatNichtVerfuegbar`,
        und der Lauf soll daran laut scheitern. Ein aufgefangener Ausfall
        stünde als 0,0 in den Scores und sähe aus wie ein Agent, der nichts
        kann.
        """
        from zettel import db

        con = db.connect(self._kopie)
        try:
            ergebnis = self.chat.turn(con, satz)
            return als_ausgabe(
                ergebnis,
                kategorien(con, [v["product_id"]
                                 for v in ergebnis.vorschlaege]))
        finally:
            con.close()

    def aufgabe(self):
        """Die Funktion, die `run_experiment(task=…)` bekommt.

        Sie heisst `zettel_chat_turn`, weil Phoenix den Namen als
        Wurzel-Span des Laufs führt (`Task: …`) — ein `<lambda>` dort wäre in
        der Oberfläche nicht wiederzuerkennen.
        """
        def zettel_chat_turn(input: dict) -> dict:  # noqa: A002
            return self.einmal(input["satz"])

        return zettel_chat_turn


# --------------------------------------------------------------------------
# Bewertung — rein rechnend, ohne Phoenix und ohne Modell

@dataclass(frozen=True)
class Auswertung:
    """Was ein Vergleich von Ausgabe und Erwartung ergeben hat.

    `praezision` und `vollstaendigkeit` sind `None`, wenn es nichts zu
    bewerten gab — eine fehlende Zahl ist ehrlicher als eine 0,0, die
    „alles falsch" behauptet, wo „nichts gefragt" richtig wäre. Denselben
    Grundsatz benutzt schon `zettel.obs.labels` für Züge ohne Entscheidung.
    """
    vollstaendigkeit: float | None
    praezision: float | None
    getroffen: list[str] = field(default_factory=list)
    fehlend: list[str] = field(default_factory=list)
    richtig: list[dict] = field(default_factory=list)
    fehlgriffe: list[dict] = field(default_factory=list)
    beifang: list[dict] = field(default_factory=list)
    rezeptpfad_falsch: bool = False


def zuordnung(zutaten: list[dict], produkte: list[dict]) -> dict[int, int]:
    """Ordnet Produkte den Zutaten zu — **jedes Produkt höchstens einer**.

    Ohne diese Bedingung misst der Recall zu gut, und zwar genau dort, wo es
    weh tut: `Zwiebeln` und `Knoblauch` liegen im echten Katalog beide in
    `Gemüse > Zwiebeln & Knoblauch`. Ein einziges Netz Zwiebeln würde ohne
    Zuordnung BEIDE Zutaten als geliefert zählen — und der Fehler aus WB-327
    (Knoblauch vergessen) wäre wieder unsichtbar, diesmal durch die Metrik
    selbst.

    Gesucht wird deshalb eine maximale Paarung im zweiseitigen Graphen
    (Zutaten links, Produkte rechts, Kante = Kategorie passt). Der Algorithmus
    ist Kuhns Erweiterungspfad — exakt, und bei einer Handvoll Knoten billiger
    als das Nachdenken darüber, ob eine gierige Zuteilung im Einzelfall
    danebenliegt. Sie täte es: „nimm der Reihe nach" gäbe das Zwiebelnetz der
    Zutat Zwiebeln und liesse Knoblauch leer stehen — richtig; bei umgekehrter
    Reihenfolge der Zutaten aber andersherum, und mit einem dritten
    Gemüseprodukt kippte das Ergebnis vollends.
    """
    kanten = [
        [j for j, p in enumerate(produkte)
         if dataset.irgendeine(p.get("kategorie") or "",
                               z.get("kategorien") or [])]
        for z in zutaten]
    zu_zutat: dict[int, int] = {}          # Produkt -> Zutat

    def erweitere(i: int, besucht: set[int]) -> bool:
        for j in kanten[i]:
            if j in besucht:
                continue
            besucht.add(j)
            if j not in zu_zutat or erweitere(zu_zutat[j], besucht):
                zu_zutat[j] = i
                return True
        return False

    for i in range(len(zutaten)):
        erweitere(i, set())
    return {i: j for j, i in zu_zutat.items()}


def bewerte(ausgabe: dict, erwartung: dict) -> Auswertung:
    """Vergleicht einen Lauf mit der Erwartung. Der Kern dieses Moduls.

    Zwei getrennte Fragen an dieselben Daten:

    **Vollständigkeit (Recall).** Für jede Pflichtzutat: gibt es ein
    vorgeschlagenes PRODUKT in einer ihrer Kategorien, das nicht schon einer
    anderen Zutat gehört (siehe `zuordnung`)? Freitext-Vorschläge zählen
    ausdrücklich nicht — „Zwiebeln" als Zeile, die die Nutzerin selbst suchen
    muss, ist kein gelieferter Einkauf. Ein `product_id`-Vergleich stünde hier
    nie: die Kategorie überlebt den nächsten Crawl, die ID nicht.

    **Präzision.** Jeder Produktvorschlag wird über seinen Suchbegriff einer
    erwarteten Zutat zugeordnet (`dataset.zutat_zu_begriff`) und muss dann in
    DEREN Kategorien liegen. Was sich keiner Zutat zuordnen lässt, ist
    entweder erlaubter Beifang (`zusatz_ok`, etwa Parmesan zur Bolognese) und
    bleibt ungezählt — oder es wird, bei einer abgezählten Liste, gegen alle
    erwarteten Kategorien geprüft. Ohne diese Unterscheidung wäre entweder
    jede sinnvolle Zugabe ein Fehler oder jeder Fehlgriff eine Zugabe.
    """
    zutaten = list(erwartung.get("zutaten") or [])
    produkte = [v for v in (ausgabe.get("vorschlaege") or [])
                if not v.get("freitext")]
    alle = [k for z in zutaten for k in z.get("kategorien", ())]

    pflichtige = [z for z in zutaten if z.get("pflicht", True)]
    gedeckt = zuordnung(pflichtige, produkte)
    getroffen = [z["name"] for i, z in enumerate(pflichtige) if i in gedeckt]
    fehlend = [z["name"] for i, z in enumerate(pflichtige) if i not in gedeckt]
    pflicht = len(pflichtige)
    vollstaendigkeit = len(getroffen) / pflicht if pflicht else None

    richtig, fehlgriffe, beifang = [], [], []
    for v in produkte:
        z = dataset.zutat_zu_begriff(zutaten, v.get("search_term") or "")
        if z is not None:
            ziel, wozu = z.get("kategorien") or [], z["name"]
        elif erwartung.get("zusatz_ok"):
            beifang.append({"name": v.get("name"),
                            "kategorie": v.get("kategorie"),
                            "begriff": v.get("search_term")})
            continue
        else:
            ziel, wozu = alle, "(nicht bestellt)"
        eintrag = {"name": v.get("name"), "kategorie": v.get("kategorie"),
                   "begriff": v.get("search_term"), "erwartet": wozu}
        (richtig if dataset.irgendeine(v.get("kategorie") or "", ziel)
         else fehlgriffe).append(eintrag)
    bewertet = len(richtig) + len(fehlgriffe)
    praezision = len(richtig) / bewertet if bewertet else None

    return Auswertung(
        vollstaendigkeit=vollstaendigkeit, praezision=praezision,
        getroffen=getroffen, fehlend=fehlend, richtig=richtig,
        fehlgriffe=fehlgriffe, beifang=beifang,
        rezeptpfad_falsch=bool(erwartung.get("kein_rezeptpfad"))
        and ausgabe.get("weg") != "llm")


def zutaten_vollstaendigkeit(output: dict, expected: dict) -> tuple:
    """Recall: wie viele der erwarteten Pflichtzutaten kamen wirklich?

    Der Score, den WB-327 gebraucht hätte. Die Erklärung nennt die fehlenden
    Zutaten beim Namen — eine Zahl allein sagt nicht, WAS fehlt, und genau
    das ist die Auskunft, für die man sonst den Trace lesen muss.
    """
    a = bewerte(output or {}, expected or {})
    if a.vollstaendigkeit is None:
        return (None, "keine Erwartung",
                "Für dieses Beispiel ist keine Pflichtzutat hinterlegt.")
    satz = (f"{len(a.getroffen)} von "
            f"{len(a.getroffen) + len(a.fehlend)} erwarteten Zutaten "
            "geliefert")
    if a.fehlend:
        satz += ". Es fehlt: " + ", ".join(a.fehlend)
    return (a.vollstaendigkeit,
            "vollstaendig" if not a.fehlend else "unvollstaendig",
            satz + ".")


def kategorie_praezision(output: dict, expected: dict) -> tuple:
    """Liegt das gewählte Produkt in der erwarteten Kategorie?

    Der deterministische Evaluator aus Spec 8.3 — und der Grund, warum das
    Dataset keine Produkt-IDs führt: derselbe Satz darf beim nächsten Crawl
    ein anderes Produkt liefern und trotzdem richtig sein.

    Ein falsch genommener Rezeptweg ist hier ein hartes 0,0 und keine
    Teilwertung: „Klopapier und Spülmittel" aus der Rezeptsammlung zu
    beantworten wäre auch dann falsch, wenn zufällig passende Produkte dabei
    herauskämen.
    """
    a = bewerte(output or {}, expected or {})
    if a.rezeptpfad_falsch:
        return (0.0, "rezeptpfad",
                "Der Rezeptweg wurde genommen, obwohl der Satz kein Rezept "
                "nennt. Was dabei herauskommt, ist nicht geprüft — der Weg "
                "ist schon falsch.")
    if a.praezision is None:
        return (None, "nichts geliefert",
                "Kein einziger Produktvorschlag, der sich bewerten liesse "
                f"({len(output.get('vorschlaege') or [])} Zeilen, alle "
                "Freitext).")
    satz = (f"{len(a.richtig)} von {len(a.richtig) + len(a.fehlgriffe)} "
            "bewerteten Produkten in der erwarteten Kategorie")
    if a.fehlgriffe:
        satz += ". Daneben: " + "; ".join(
            f"„{f['begriff']}“ -> {f['name']} [{f['kategorie']}], erwartet "
            f"{f['erwartet']}" for f in a.fehlgriffe)
    if a.beifang:
        satz += (f". Zusätzlich ungezählt: "
                 + ", ".join(f["name"] for f in a.beifang))
    return (a.praezision,
            "sauber" if not a.fehlgriffe else "fehlgriff", satz + ".")


# --------------------------------------------------------------------------
# Der LLM-Judge

#: Was der Judge sagen darf, und was es als Zahl bedeutet. Drei Stufen und
#: nicht zwei: „die Kategorie stimmt, die Wahl ist trotzdem schlecht" ist
#: genau der Fall, für den es diesen Evaluator gibt, und er wäre in einem
#: Ja/Nein nicht abbildbar.
URTEILE = {"passt": 1.0, "teilweise": 0.5, "passt_nicht": 0.0}

SYSTEM_JUDGE = """\
Du beurteilst, ob ein Einkaufs-Assistent eine Anfrage sinnvoll beantwortet \
hat.

Du bekommst die Anfrage, eine Beschreibung dessen, was erwartet wird, und die \
Liste der Artikel, die der Assistent vorgeschlagen hat.

Beurteile NUR, ob die vorgeschlagenen Artikel die Anfrage im Sinne der \
Beschreibung erfüllen. Beurteile nicht Preis, Marke oder Verpackungsgrösse.

Drei mögliche Urteile:
- "passt": die Vorschläge erfüllen die Anfrage.
- "teilweise": im Kern richtig, aber etwas Wesentliches fehlt oder ein \
Vorschlag geht an der Absicht vorbei.
- "passt_nicht": die Vorschläge erfüllen die Anfrage nicht.

Antworte ausschliesslich als JSON:
{"urteil": "teilweise", "begruendung": "ein kurzer Satz"}"""

SCHEMA_JUDGE = {
    "type": "object",
    "properties": {
        "urteil": {"type": "string", "enum": sorted(URTEILE)},
        "begruendung": {"type": "string"},
    },
    "required": ["urteil", "begruendung"],
    "additionalProperties": False,
}


def judge_prompt(satz: str, beschreibung: str, ausgabe: dict) -> str:
    """Der Benutzerteil für den Judge.

    Er sieht die erwarteten KATEGORIEN nicht, sondern nur die Beschreibung in
    Prosa. Das ist Absicht: bekäme er die Kategorien, prüfte er dasselbe wie
    `kategorie_praezision`, nur teurer und unzuverlässiger. Er soll das
    beurteilen, was dort nicht hineinpasst.
    """
    zeilen = []
    for v in ausgabe.get("vorschlaege") or []:
        if v.get("freitext"):
            zeilen.append(f"- (kein Produkt gefunden) Suchbegriff: "
                          f"{v.get('search_term')}")
        else:
            zeilen.append(f"- {v.get('name')} [{v.get('kategorie')}] "
                          f"(gesucht als: {v.get('search_term')})")
    liste = "\n".join(zeilen) or "(keine Vorschläge)"
    return (f"Anfrage: {satz}\n\n"
            f"Erwartet wird: {beschreibung}\n\n"
            f"Vorgeschlagen wurde:\n{liste}")


def urteil_aus_antwort(text: str) -> tuple:
    """Modellantwort -> `(score, label, erklärung)`.

    Rein rechnend und deshalb ohne Box prüfbar. Eine unlesbare oder unbekannte
    Antwort ergibt **keinen** Score, sondern das Label `unlesbar`: eine 0,0
    stünde in Phoenix neben den echten Nullen und sähe aus wie ein Agent, der
    versagt hat, obwohl der Richter gestottert hat.

    Ausgepackt wird mit `plan._json_wert` — dieselbe nachsichtige Stelle, die
    schon weiss, dass Modelle Codefences und Vorreden schreiben. Ein zweiter,
    eigener Parser hier liefe irgendwann auseinander.
    """
    try:
        wert = plan._json_wert(text or "")
    except plan.PlanFehler as e:
        return (None, "unlesbar", f"Der Judge antwortete kein JSON: {e}")
    if not isinstance(wert, dict):
        return (None, "unlesbar",
                f"Der Judge antwortete kein Objekt, sondern "
                f"{type(wert).__name__}.")
    urteil = str(wert.get("urteil") or "").strip().casefold()
    begruendung = str(wert.get("begruendung") or "").strip()
    if urteil not in URTEILE:
        return (None, "unlesbar",
                f"Unbekanntes Urteil {urteil!r}. Erlaubt: "
                + ", ".join(sorted(URTEILE)) + ".")
    return (URTEILE[urteil], urteil,
            begruendung or "(der Judge hat nichts begründet)")


def llm_urteil(zugang, satz: str, beschreibung: str, ausgabe: dict, *,
               ermessen: bool = True, guided: bool = True,
               temperatur: float = 0.0, max_tokens: int = 400) -> tuple:
    """Fragt den Judge — aber nur, wenn „richtig" Ermessenssache ist.

    Bei `ermessen=False` wird das Modell **gar nicht erst gefragt**. Das ist
    keine Sparmassnahme: für „Klopapier und Spülmittel" ist die
    Kategorieprüfung vollständig, und eine zweite Meinung dazu wäre eine
    Zahl, die nur Streuung hinzufügt.
    """
    if not ermessen:
        return (None, "nicht_bewertet",
                "Hier entscheidet die Kategorie; ein Ermessensurteil würde "
                "nur Streuung hinzufügen.")
    weitere = {"temperature": temperatur, "max_tokens": max_tokens}
    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    if guided:
        extra["guided_json"] = SCHEMA_JUDGE
    weitere["extra_body"] = extra
    antwort = zugang.chat(
        [{"role": "system", "content": SYSTEM_JUDGE},
         {"role": "user", "content": judge_prompt(satz, beschreibung,
                                                  ausgabe)}],
        **weitere)
    return urteil_aus_antwort(antwort.content or "")


# --------------------------------------------------------------------------
# Phoenix — ab hier braucht es einen Server, und der Judge die Box

def evaluatoren_code():
    """Die beiden deterministischen Evaluatoren, für `run_experiment`.

    Der Import von `create_evaluator` steht in der Funktion, damit
    `import evals.experiment` ohne installiertes `phoenix.client` gelingt —
    die Bewertungslogik darüber ist davon nicht abhängig und wird ohne
    Phoenix getestet.
    """
    from phoenix.client.experiments import create_evaluator

    @create_evaluator(kind="CODE", name="kategorie_praezision")
    def praezision(output, expected):
        return kategorie_praezision(output, expected)

    @create_evaluator(kind="CODE", name="zutaten_vollstaendigkeit")
    def vollstaendigkeit(output, expected):
        return zutaten_vollstaendigkeit(output, expected)

    return [praezision, vollstaendigkeit]


def evaluator_judge(zugang):
    """Der LLM-Judge als Phoenix-Evaluator. Braucht die Box."""
    from phoenix.client.experiments import create_evaluator

    @create_evaluator(kind="LLM", name="llm_urteil")
    def urteil(input, output, expected, metadata):  # noqa: A002
        return llm_urteil(zugang,
                          (input or {}).get("satz") or "",
                          (expected or {}).get("beschreibung") or "",
                          output or {},
                          ermessen=bool((metadata or {}).get("ermessen")))

    return urteil


def fahre(name: str, *, zugang=None, client=None, db_pfad: str | None = None,
          judge: bool = True, dry_run: bool = False) -> dict:
    """Fährt eine Variante über das Dataset und bewertet sie.

    Zwei Schritte mit Absicht getrennt: `run_experiment` mit den beiden
    deterministischen Evaluatoren (die brauchen nichts als Rechenzeit), danach
    `evaluate_experiment` mit dem Judge (der braucht die Box). Wer die Zahlen
    ohne Modellurteil will, lässt `judge=False` — die deterministischen Scores
    stehen dann trotzdem vollständig in Phoenix.
    """
    from phoenix.client.experiments import evaluate_experiment, run_experiment

    from zettel import obs
    from zettel.llm.client import Modellzugang

    variante = VARIANTEN[name]
    client = dataset.klient() if client is None else client
    zugang = Modellzugang() if zugang is None else zugang
    daten = client.datasets.get_dataset(dataset=dataset.NAME)

    # Die Chat-Spans der Läufe gehören ins Betriebsprojekt: dort liegen sie
    # neben echten Zügen und lassen sich mit ihnen vergleichen.
    obs.einrichten(projekt=PROJEKT)

    stempel = datetime.now().strftime("%Y-%m-%d %H:%M")
    with Lauf(variante, zugang=zugang, db_pfad=db_pfad) as lauf:
        gefahren = run_experiment(
            dataset=daten,
            task=lauf.aufgabe(),
            evaluators=evaluatoren_code(),
            experiment_name=f"{variante.name} {stempel}",
            experiment_description=variante.beschreibung,
            experiment_metadata={**variante.als_metadaten(),
                                 "modell": zugang.modell(),
                                 "projekt": PROJEKT},
            dry_run=dry_run,
            print_summary=False)
        if judge:
            gefahren = evaluate_experiment(
                experiment=gefahren, evaluators=[evaluator_judge(zugang)],
                dry_run=dry_run, print_summary=False)

    obs.flush()
    return {"variante": variante.name, "experiment": gefahren,
            "url": (None if dry_run else client.experiments.get_experiment_url(
                dataset_id=daten.id,
                experiment_id=gefahren["experiment_id"])),
            "scores": mittelwerte(gefahren)}


def mittelwerte(gefahren) -> dict:
    """Je Evaluator: Mittelwert, Zahl der gewerteten und der ungewerteten Läufe.

    Ungewertet gezählt und nicht als 0 mitgemittelt — sonst zöge jedes „nicht
    bewertet" des Judge den Schnitt nach unten und behauptete einen Fehler,
    den niemand gefunden hat.
    """
    raus: dict[str, dict] = {}
    for run in gefahren.get("evaluation_runs") or []:
        name = run.name
        eintrag = raus.setdefault(name, {"summe": 0.0, "n": 0, "ohne": 0,
                                         "fehler": 0, "labels": {}})
        if run.error is not None:
            eintrag["fehler"] += 1
            continue
        ergebnis = run.result or {}
        if isinstance(ergebnis, list):
            ergebnis = ergebnis[0] if ergebnis else {}
        label = ergebnis.get("label")
        if label:
            eintrag["labels"][label] = eintrag["labels"].get(label, 0) + 1
        score = ergebnis.get("score")
        if score is None:
            eintrag["ohne"] += 1
        else:
            eintrag["summe"] += float(score)
            eintrag["n"] += 1
    for eintrag in raus.values():
        eintrag["mittel"] = (eintrag["summe"] / eintrag["n"]
                             if eintrag["n"] else None)
    return raus


# --------------------------------------------------------------------------
# Kommandozeile

def _zeige(bericht: dict) -> None:
    print(f"\n=== {bericht['variante']} ===")
    if bericht["url"]:
        print(f"    {bericht['url']}")
    for name, s in sorted(bericht["scores"].items()):
        mittel = "—" if s["mittel"] is None else f"{s['mittel']:.3f}"
        print(f"    {name:26s} {mittel:>6s}  (n={s['n']}, "
              f"ohne Score={s['ohne']}, Fehler={s['fehler']})  "
              + ", ".join(f"{k}={v}" for k, v in sorted(s["labels"].items())))


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Fährt Varianten des Agenten über 'zettel-anfragen'. "
                    "Braucht Phoenix und die vLLM-Box — das Gate nicht.")
    p.add_argument("varianten", nargs="*", default=None,
                   help="Namen der Varianten. Ohne Angabe: nur die Basis.")
    p.add_argument("--liste", action="store_true",
                   help="Die Varianten aufzählen und aufhören.")
    p.add_argument("--ohne-judge", action="store_true",
                   help="Nur die deterministischen Evaluatoren.")
    p.add_argument("--dry-run", action="store_true",
                   help="Ein einzelnes Beispiel, nichts wird in Phoenix "
                        "gespeichert.")
    p.add_argument("--db", default=None, help="Katalogdatei.")
    args = p.parse_args(argv)

    if args.liste:
        for v in VARIANTEN.values():
            print(f"{v.name:16s} {v.beschreibung}")
            print(f"{'':16s} Prompt {v.als_metadaten()['prompt_extract']}, "
                  f"{v.kandidaten} Kandidaten, guided_json "
                  f"{'an' if v.guided else 'aus'}")
        return 0

    namen = args.varianten or [BASIS]
    unbekannt = [n for n in namen if n not in VARIANTEN]
    if unbekannt:
        p.error(f"Unbekannte Variante(n): {', '.join(unbekannt)}. "
                f"Bekannt: {', '.join(VARIANTEN)}.")

    berichte = []
    for n in namen:
        print(f"\n--- {n}: {VARIANTEN[n].beschreibung}")
        bericht = fahre(n, db_pfad=args.db, judge=not args.ohne_judge,
                        dry_run=args.dry_run)
        berichte.append(bericht)
        _zeige(bericht)

    if len(berichte) > 1:
        _vergleich(berichte)
    return 0


def _vergleich(berichte: list[dict]) -> None:
    """Die Tabelle, wegen der es dieses Ticket gibt: Variante gegen Variante.

    Gleiche Zahlen sind ein Ergebnis und werden nicht weginterpretiert. Wer
    hier zweimal denselben Wert sieht, hat gemessen, dass die Stellschraube
    nichts bewegt — das ist die Auskunft.
    """
    print("\n=== Vergleich ===")
    scores = sorted({s for b in berichte for s in b["scores"]})
    breite = max([len(s) for s in scores] + [10])
    print(" " * breite + "".join(f"{b['variante']:>16s}" for b in berichte))
    for s in scores:
        zeile = ""
        for b in berichte:
            mittel = b["scores"].get(s, {}).get("mittel")
            zeile += f"{('—' if mittel is None else f'{mittel:.3f}'):>16s}"
        print(f"{s:{breite}s}{zeile}")


if __name__ == "__main__":
    raise SystemExit(main())
