"""Rezepterkennung im freien Satz — die Abkürzung an Stufe 1 vorbei (Spec 6).

„Trifft die Anfrage ein gespeichertes Rezept, werden dessen bereits verknüpfte
Produkte direkt vorgeschlagen — ohne Modell und ohne Suche." Das ist nicht nur
schneller und billiger: es ist auch der einzige Weg, der genau die Produkte
vorschlägt, die die beiden selbst einmal ausgesucht haben. Ein Modell kann das
nicht besser wissen.

Erkannt wird über den Rezeptnamen im Satz, nicht über Ähnlichkeit. Der Grund
ist Vorhersagbarkeit: ein Rezept greift dann und nur dann, wenn sein Name
dasteht. Eine unscharfe Zuordnung („Nudelauflauf" trifft „Lasagne") wäre
gelegentlich hilfreich und gelegentlich falsch — und ein Vorschlag, dessen
Zustandekommen niemand erklären kann, ist in einem Projekt, das sich über
Nachvollziehbarkeit definiert, der falsche Handel.

**Was übrig bleibt, geht nicht verloren.** „alles für Spaghetti Bolognese, und
Klopapier" trifft das Rezept — und `rest` enthält danach „Klopapier". Der
Aufrufer macht daraus einen Freitext-Vorschlag. Stillschweigend fallen zu
lassen, was neben dem Rezept stand, wäre der schlimmste Ausgang: dann fehlt im
Laden etwas, ohne dass es jemand gemerkt hat.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from picknick import db, recipes

#: Wörter, die zwischen den Wünschen stehen und keiner sind. Bleibt nach dem
#: Rezeptnamen nur solches Füllwerk übrig, ist der Rest kein Wunsch, sondern
#: Satzbau — und darf verschwinden.
FUELLWOERTER = frozenset("""
alles fuer für und dazu auch noch bitte plus dann mach mache machen ich
wir mir uns mich dir du er sie es man mal doch halt eben heute morgen
moechte möchte moechten möchten will wollen haette hätte gern gerne
brauche brauchen kaufe kaufen besorg besorge besorgen ein eine einen
etwas das den die der dem des zutaten zutat rezept kochen koche essen
""".split())

#: Ab hier ist ein Rest ein eigener Wunsch und kein Wortstummel.
MIN_REST = 3


@dataclass(frozen=True)
class Rezeptweg:
    """Was die Rezepterkennung aus einem Satz gemacht hat."""
    rezepte: list[dict] = field(default_factory=list)
    rest: str | None = None

    def __bool__(self) -> bool:
        return bool(self.rezepte)


def falte(text: str) -> tuple[str, list[int]]:
    """Text -> (vergleichbare Form, Rückverweis auf die Ursprungsstellen).

    Kleinschreibung und dieselbe Umlautfaltung wie die Suche (`db.UMLAUTE`),
    damit „Grünkohl", „gruenkohl" und „GRUNKOHL" dasselbe Rezept treffen.

    Die Indexliste ist der Grund, warum das hier von Hand steht und nicht
    `db.normalisiere()` benutzt: „ä" wird zu zwei Zeichen, danach stimmen alle
    Positionen nicht mehr. Ohne Rückverweis liesse sich der Rest nicht aus dem
    ORIGINALSATZ herausschneiden — und der Freitext-Vorschlag hiesse dann
    „Kaese" statt „Käse".
    """
    ersatz = dict(db.UMLAUTE)
    gefaltet: list[str] = []
    stellen: list[int] = []
    for i, zeichen in enumerate(text):
        for aus in ersatz.get(zeichen, zeichen.lower()):
            gefaltet.append(aus)
            stellen.append(i)
    return "".join(gefaltet), stellen


def _stellen_im_satz(gefaltet: str, stellen: list[int], name: str):
    """Alle Vorkommen von `name` (bereits gefaltet), als Spannen im Original.

    Wortgrenzen sind Bedingung: sonst träfe ein Rezept „Ei" in „Eiscreme" und
    „Einkauf".
    """
    muster = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
    for treffer in muster.finditer(gefaltet):
        anfang = stellen[treffer.start()]
        ende = stellen[treffer.end() - 1] + 1
        yield anfang, ende


def _rest(satz: str, spannen: list[tuple[int, int]]) -> str | None:
    """Der Satz ohne die erkannten Rezeptnamen, von Füllwerk befreit.

    `None`, wenn nichts übrig bleibt, was ein eigener Wunsch sein könnte.
    """
    behalten = []
    letztes = 0
    for anfang, ende in sorted(spannen):
        if anfang > letztes:
            behalten.append(satz[letztes:anfang])
        letztes = max(letztes, ende)
    behalten.append(satz[letztes:])
    roh = " ".join(behalten)
    woerter = [w for w in re.findall(r"[\w\-]+", roh, re.UNICODE)
               if w.casefold() not in FUELLWOERTER]
    rest = " ".join(woerter).strip()
    return rest if len(rest) >= MIN_REST else None


def erkenne(con: sqlite3.Connection, satz: str) -> Rezeptweg:
    """Sucht gespeicherte Rezepte im Satz. Fragt kein Modell und keine Suche.

    Mehrere Rezepte dürfen treffen („Bolognese und Kartoffelsalat"). Geprüft
    wird von den langen Namen zu den kurzen, und eine bereits belegte Stelle
    wird nicht zweimal vergeben — sonst zöge „Salat" den halben
    „Kartoffelsalat" ein zweites Mal heran.

    Rezepte ohne Zutaten werden übergangen: sie würden den Modellweg
    abschneiden und nichts an seine Stelle setzen. **Das ist zugleich die
    Weiche zu WB-338:** ein aus Chefkoch geholtes Rezept hat noch keine
    verknüpften Produkte (`recipe_item` ist leer), fängt den Zug hier also
    nicht ab — es wird über die Gerichtequelle bedient, die aus seiner
    Zutatenliste erst Katalogprodukte sucht. Verknüpft jemand später von Hand
    Produkte, übernimmt wieder dieser Weg, und das ist richtig: dann stehen
    dort die Produkte, die ein Mensch ausgesucht hat.
    """
    text = (satz or "").strip()
    if not text:
        return Rezeptweg()
    kandidaten = [r for r in recipes.rezepte(con) if r["n_zutaten"] > 0]
    return erkenne_in(text, kandidaten)


def erkenne_in(satz: str, kandidaten, name_von=lambda e: e["name"]) -> Rezeptweg:
    """Derselbe Namensvergleich, aber gegen eine beliebige Liste (WB-338).

    Die Gerichtequelle braucht genau das, was der Rezeptweg hier tut: einen
    Namen im Satz finden, mit Wortgrenzen, gefaltet, und den Rest des Satzes
    sauber übrig behalten. Eine zweite Kopie dieser Logik liefe irgendwann
    auseinander — dann fände der eine Weg „Gemüselasagne" und der andere
    nicht, und niemand könnte erklären, warum.
    """
    text = (satz or "").strip()
    if not text:
        return Rezeptweg()
    gefaltet, stellen = falte(text)
    kandidaten = sorted(kandidaten, key=lambda r: len(name_von(r) or ""),
                        reverse=True)

    getroffen: list[dict] = []
    spannen: list[tuple[int, int]] = []
    for r in kandidaten:
        name, _ = falte((name_von(r) or "").strip())
        if not name:
            continue
        for anfang, ende in _stellen_im_satz(gefaltet, stellen, name):
            if any(anfang < b and a < ende for a, b in spannen):
                continue  # Die Stelle gehört schon einem längeren Namen.
            spannen.append((anfang, ende))
            getroffen.append(r)
            break
    if not getroffen:
        return Rezeptweg()
    # In der Reihenfolge, in der sie im Satz stehen — so liest sich die
    # Vorschlagsliste wie der Satz, den die Nutzerin geschrieben hat.
    reihenfolge = {id(r): a for r, (a, _) in zip(getroffen, spannen)}
    getroffen.sort(key=lambda r: reihenfolge[id(r)])
    return Rezeptweg(rezepte=getroffen, rest=_rest(text, spannen))


def zutaten(con: sqlite3.Connection, treffer: Rezeptweg) -> list[dict]:
    """Die Zutaten aller getroffenen Rezepte, jede mit ihrem Rezeptnamen.

    Ausgemusterte Produkte (`active = 0`, Spec 5.3) bleiben drin. Sie sind der
    Grund, warum `recipes.zutaten()` sie überhaupt mitliefert: eine Zutat, die
    aus dem Katalog gefallen ist, wird markiert und nicht verschwiegen.
    """
    liste = []
    for r in treffer.rezepte:
        for z in recipes.zutaten(con, r["id"]):
            liste.append({**z, "rezept": r["name"]})
    return liste
