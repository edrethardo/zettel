"""Oberbegriffe auffächern: erst die Sorte, dann das Produkt (WB-368).

„Aufschnitt" in den Chat zu schreiben lieferte bis zu diesem Ticket sechs
Produkte, die zufällig das Wort im Namen tragen — Jagdwurst Aufschnitt,
Kochschinken Aufschnitt, Cervelatwurst Aufschnitt. Ein schmaler Ausschnitt,
keine Auswahl. Manche Wörter sind eben Oberbegriffe: wer sie tippt, will
zuerst eine SORTE wählen.

**Das ist nicht dasselbe wie die Alternativen aus WB-359.** Dort geht es um
andere *Produkte* zu einem Begriff („statt dieser Butter jene"); hier eine
Ebene höher um andere *Sorten* („Salami oder Kochschinken") — und erst danach
um das Produkt.

Drei Regeln tragen dieses Modul:

1. **Die Sorten kommen aus dem Katalog, nicht aus dem Modell.** Sie stehen im
   Kategoriebaum, mit echten Stückzahlen, und eine Sorte ohne Produkte kann
   dort gar nicht erst auftauchen (`catalog.categories.sorten`). Dasselbe mit
   Qwen erzeugt (gemessen 2026-08-28) lieferte „SCHWEINEBRUST" mit null
   Treffern, „Bananen" doppelt und ein verstümmeltes „Birn".
2. **Das Modell ordnet zu, es erfindet nicht.** Nicht jeder Oberbegriff ist
   ein Kategoriename — „Nudeln" steckt unter „Reis, Pasta & Getreide". Diese
   Übersetzung kann keine Zeichenkettenregel, also übernimmt sie das Modell —
   aber nur als Wahl aus einer vorgelegten Liste, und was nicht darin steht,
   wird verworfen (`plan._kategorie`).
3. **Ein Tipp auf eine Sorte führt in den normalen Ablauf.** Kandidaten,
   Auswahl durch Stufe 3, Ja/Nein, Alternativen — kein zweiter Mechanismus
   daneben. Was sich ändert, ist allein die Herkunft der Kandidaten: sie
   kommen aus der Kategorie statt aus der Volltextsuche
   (`catalog.search.in_sorte`), denn die Zahl neben der Sorte soll halten,
   was sie verspricht.

**Der billige Weg zuerst.** Ist das getippte Wort selbst ein Kategoriename
(„Aufschnitt", „Käse", „Obst"), braucht es dafür kein Modell und keine
Wartezeit — der Katalog weiss es. Erst wenn das nicht greift, geht die Frage
an Stufe 1 mit, die den Satz ohnehin liest (`plan.extract_plan`). Ein
aufgefächerter Zug kostet damit **null oder einen** Modellaufruf, wo derselbe
Satz vorher zwei kostete: Stufe 3 entfällt, weil es nichts zu wählen gibt.

Was hier NICHT steht, ist ein Vektorindex. Die Sorten stehen strukturiert in
der eigenen Datenbank; sie zu holen ist ein `GROUP BY` und keine
Ähnlichkeitssuche.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from zettel import db
from zettel.catalog import categories

#: Woher die Zuordnung „getipptes Wort -> Kategorie" kam. Steht im Trace
#: (`zettel.fanout_source`), weil sich sonst später nicht messen lässt, ob
#: der Modellaufruf überhaupt gebraucht wird.
KATALOG = "catalog"
MODELL = "model"

#: Bis zu wie vielen Wörtern ein Satz als Oberbegriff geprüft wird.
#:
#: **Die Auffächerung darf nicht überall anspringen.** Wer „alles für Spaghetti
#: Bolognese" schreibt, will kochen und keine Warengruppe durchblättern; wer
#: einen Oberbegriff meint, tippt ihn — „Aufschnitt", „Käse", „kalte Getränke".
#: Zwei Wörter sind die Grenze, an der beides noch stimmt.
#:
#: Die Zahl kostet ausserdem Token: nur für einen Satz innerhalb der Grenze
#: geht die Kategorienliste (1.036 Zeichen) in den Prompt von Stufe 1. Für
#: jeden längeren Satz ist der Aufruf Wort für Wort derselbe wie vor diesem
#: Ticket — auch im Zeitbedarf.
MAX_WOERTER = 2


@dataclass(frozen=True)
class Faecher:
    """Eine Auffächerung: das getippte Wort, die Kategorie, ihre Sorten."""
    wort: str
    kategorie: str
    sorten: list[dict] = field(default_factory=list)
    herkunft: str = KATALOG

    @property
    def anzahl(self) -> int:
        """Wie viele Produkte in allen angebotenen Sorten zusammen stehen."""
        return sum(int(s["anzahl"]) for s in self.sorten)


def moeglich(satz: str) -> bool:
    """Ist der Satz kurz genug, um ein Oberbegriff zu sein?"""
    return 0 < len((satz or "").split()) <= MAX_WOERTER


def namen(con: sqlite3.Connection) -> list[str]:
    """Die Kategorien, die dem Modell vorgelegt werden — und nur die.

    Genau diese Liste prüft `plan._kategorie` die Antwort gegen. Sie hier zu
    bilden und in `plan` nur zu vergleichen ist Absicht: die Vorlage und die
    Prüfung müssen aus derselben Quelle stammen, sonst könnte das Modell etwas
    nennen, was ihm vorlag, und es würde trotzdem verworfen.
    """
    return [k["name"] for k in categories.oberbegriffe(con)]


def aus_katalog(con: sqlite3.Connection, satz: str) -> Faecher | None:
    """Der Oberbegriff ohne Modell: das getippte Wort IST eine Kategorie.

    `None`, wenn es keine ist. Verglichen wird nachsichtig in der Schreibweise
    (Umlaute, Gross- und Kleinschreibung) und wörtlich im Bestand — „Käse"
    trifft „Käse", „Kaese" auch, „Wurst" nicht (das heisst im Katalog „Salami,
    Mettwurst & Co."). Für alles, was der Katalog so nicht kennt, ist das
    Modell zuständig.

    **Dieser Weg braucht die Box nicht.** Er läuft auch, wenn die vLLM-Maschine
    schläft — wie der Rezeptweg, und aus demselben Grund: es ist nichts zu
    raten.
    """
    if not moeglich(satz):
        return None
    return _zur_kategorie(con, satz, satz, KATALOG)


def aus_modell(con: sqlite3.Connection, wort: str,
               kategorie: str | None) -> Faecher | None:
    """Die Auffächerung zu der Kategorie, die Stufe 1 genannt hat.

    `kategorie` ist bereits gegen die Vorlage geprüft (`plan._kategorie`);
    hier wird nur noch nachgesehen, ob sie im Katalog auch Sorten hat. Beides
    getrennt zu halten ist kein Umweg: die eine Prüfung schützt vor einer
    erfundenen Kategorie, die andere vor einer leeren Auswahl.
    """
    if not kategorie:
        return None
    return _zur_kategorie(con, wort, kategorie, MODELL)


def _zur_kategorie(con: sqlite3.Connection, wort: str, kategorie: str,
                   herkunft: str) -> Faecher | None:
    """Kategorie -> Auffächerung, oder `None`, wenn zu wenige Sorten da sind."""
    gesucht = _form(kategorie)
    for k in categories.oberbegriffe(con):
        if _form(k["name"]) == gesucht:
            return Faecher(wort=" ".join((wort or "").split()),
                           kategorie=k["name"], sorten=list(k["sorten"]),
                           herkunft=herkunft)
    return None


def _form(text: str) -> str:
    """Die Vergleichsform: umlautfrei, klein, ohne doppelte Leerzeichen."""
    return " ".join(db.normalisiere(text or "").split()).casefold()


def meldung(faecher: Faecher) -> str:
    """Der Satz über der Sortenliste.

    Nennt die Zahlen, weil sie echt sind — und den Ausweg, weil ein
    Oberbegriff keine Sackgasse sein darf: wer nichts auswählt, sucht direkt
    nach dem, was er getippt hat.
    """
    n = len(faecher.sorten)
    return (f"„{faecher.wort}“ ist ein Oberbegriff — der Katalog führt dazu "
            f"{n} Sorten mit {faecher.anzahl} Produkten. Wähl aus, was du "
            "haben willst; mehrere gehen. Oder überspring das und such direkt "
            f"nach „{faecher.wort}“.")


# --------------------------------------------------------------------------
# Aufheben und Wiederfinden
#
# Die Sorten hängen an der ANTWORTZEILE des Chats (`chat_message`), genau wie
# die Vorschläge. Damit steht die Auffächerung im Verlauf da, wo sie
# entstanden ist, verschwindet mit der Bestellung und kann später wieder
# angetippt werden — wer erst Salami wählt und zwei Sätze später doch noch
# Kochschinken will, findet die Liste noch vor.
#
# Was hier NICHT passiert: ein Vorschlag anlegen. Eine Sorte ist keine
# Entscheidung über ein Produkt, und in `chat_suggestion` verfälschte sie das
# Eval-Label aus Spec 8.1 — die Trefferquote zählte dann Kästchen mit.


def merken(con: sqlite3.Connection, chat_message_id: int,
           faecher: Faecher) -> int:
    """Schreibt die angebotenen Sorten. Gibt zurück, wie viele es wurden."""
    n = 0
    for pos, s in enumerate(faecher.sorten):
        cur = con.execute(
            "INSERT OR IGNORE INTO chat_sorte"
            " (chat_message_id, wort, category_l1, name, anzahl, pos)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (chat_message_id, faecher.wort, faecher.kategorie, s["name"],
             int(s["anzahl"]), pos))
        n += cur.rowcount or 0
    con.commit()
    return n


def zu_nachricht(con: sqlite3.Connection,
                 chat_message_id: int) -> dict | None:
    """Die Auffächerung einer Chatzeile, oder `None`.

    `{"wort", "kategorie", "sorten": [{"name", "anzahl"}, …]}` — die
    Reihenfolge ist die des Anbietens, nicht die der Datenbank.

    **Die Stückzahl ist die von damals.** Sie wird nicht neu gezählt: die
    Nutzerin hat „Salami (34)" gesehen und soll dieselbe Zeile wiederfinden.
    Was der Katalog inzwischen hergibt, entscheidet sich beim Tippen auf die
    Sorte, nicht in dieser Liste.
    """
    rows = con.execute(
        "SELECT wort, category_l1, name, anzahl FROM chat_sorte"
        "  WHERE chat_message_id = ? ORDER BY pos, id",
        (chat_message_id,)).fetchall()
    if not rows:
        return None
    return {"wort": rows[0]["wort"], "kategorie": rows[0]["category_l1"],
            "sorten": [{"name": r["name"], "anzahl": int(r["anzahl"])}
                       for r in rows]}


def gewaehlte(con: sqlite3.Connection, chat_message_id: int,
              namen_gewaehlt: list[str]) -> list[str]:
    """Von den angekreuzten Sorten die, die auch angeboten wurden.

    **Dieselbe Zusicherung wie bei den Produkt-IDs in `plan.choose`, nur für
    die Oberfläche:** gewählt werden kann nur, was vorlag. Ein Name aus einem
    von Hand gebauten Formular fällt hier weg, statt eine Kategorieabfrage auf
    einen beliebigen Wert loszulassen. Die Reihenfolge ist die des Angebots,
    damit zwei Sorten immer in derselben Folge in den Satz gehen.
    """
    faecher = zu_nachricht(con, chat_message_id)
    if faecher is None:
        return []
    gewuenscht = {_form(n) for n in namen_gewaehlt}
    return [s["name"] for s in faecher["sorten"] if _form(s["name"]) in gewuenscht]
