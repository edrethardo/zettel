"""Die Begriffsliste für den nächtlichen Vollcrawl (Spec 12).

Knuspr hat keinen Endpunkt „gib mir den ganzen Katalog". Es gibt nur die
Suche (Spec-Anhang A), und die verlangt einen Begriff. Der Katalog dieses
Shops ist damit genau so breit wie diese Liste — sie ist keine Beigabe zum
Crawler, sie IST der Umfang des Sortiments.

Die Liste stand bisher in `scripts/chat_probe.py` und war dort ein Ausschnitt
für eine Handprobe: zwölf Begriffe rund um Spaghetti Bolognese. Für einen
Haushalt, in dem zwei Menschen wöchentlich einkaufen, reicht das nicht — was
nicht gecrawlt wurde, findet die Suche nicht, und der Chat-Agent kann es dann
auch nicht vorschlagen. Deshalb liegt sie jetzt hier im Paket.

**Gewählt nach Einkaufszettel, nicht nach Warenkunde.** Aufgenommen ist, was
tatsächlich auf einem Zettel steht („hackfleisch", „klopapier"), nicht die
Kategorienamen des Händlers. Ein Begriff bringt über `totalHits` alles mit,
was der Händler unter ihm führt — „nudeln" liefert auch Penne und Fusilli,
ohne dass beide hier stehen müssten.

**Kosten, ehrlich geschätzt und nicht gemessen:** 170 Begriffe, je nach
Begriff eine bis fünf Seiten à 200 Produkte, dazwischen `PAUSE_S` von 1,5 s.
Das sind grob 400–800 Anfragen, also etwa 15–25 Minuten, plus die Bilder, die
beim ersten Lauf einzeln geladen werden — der erste Lauf dauert deutlich
länger als jeder folgende, weil `hole_bild()` vorhandene Dateien überspringt.
Für einen nächtlichen Lauf ist das gleichgültig; für einen Handstart am Tag
ist es der Grund, `--begriff` zu benutzen.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_BEGRIFFE = "PICKNICK_BEGRIFFE"

#: Der Vollcrawl. Gruppiert wie ein Einkaufszettel, damit beim Ergänzen
#: auffällt, welche Ecke des Haushalts noch fehlt.
BEGRIFFE: tuple[str, ...] = (
    # Milchprodukte und Kühlregal
    "milch", "haltbare milch", "butter", "margarine", "sahne", "schmand",
    "creme fraiche", "joghurt", "quark", "skyr", "frischkaese", "kaese",
    "gouda", "mozzarella", "parmesan", "feta", "huettenkaese", "eier",
    "pudding", "hefeteig",
    # Pflanzliche Alternativen
    "haferdrink", "sojadrink", "mandeldrink", "tofu",
    # Brot und Backwaren
    "brot", "vollkornbrot", "toast", "broetchen", "knaeckebrot", "zwieback",
    # Obst
    "aepfel", "bananen", "birnen", "orangen", "zitronen", "trauben",
    "erdbeeren", "heidelbeeren", "avocado", "melone",
    # Gemüse
    "tomaten", "gurken", "paprika", "zwiebeln", "knoblauch", "kartoffeln",
    "moehren", "zucchini", "aubergine", "brokkoli", "blumenkohl", "spinat",
    "salat", "champignons", "lauch", "sellerie", "ingwer", "kuerbis",
    # Fleisch, Wurst, Fisch
    "hackfleisch", "haehnchenbrust", "haehnchenschenkel", "schweinefilet",
    "rindersteak", "gulasch", "schinken", "salami", "wurst", "speck",
    "bratwurst", "lachs", "thunfisch", "fischstaebchen", "garnelen",
    # Grundnahrungsmittel
    "nudeln", "spaghetti", "reis", "kartoffelpueree", "couscous",
    "linsen", "kichererbsen", "bohnen", "mehl", "zucker", "salz", "hefe",
    "haferflocken", "muesli", "cornflakes", "paniermehl",
    # Konserven, Glas, Vorrat
    "passierte tomaten", "tomatenmark", "mais", "erbsen", "oliven",
    "kokosmilch", "bruehe", "suppe", "pesto", "ketchup", "senf",
    "mayonnaise", "essig", "olivenoel", "sonnenblumenoel", "sojasosse",
    "honig", "marmelade", "nutella", "erdnussbutter",
    # Gewürze und Backzutaten
    "pfeffer", "paprikapulver", "oregano", "basilikum", "curry", "zimt",
    "backpulver", "vanillezucker", "schokolade zum backen",
    # Getränke
    "kaffee", "espresso", "tee", "mineralwasser", "apfelsaft",
    "orangensaft", "cola", "limonade", "bier", "wein",
    # Süßes und Snacks
    "schokolade", "kekse", "gummibaerchen", "chips", "salzstangen",
    "nuesse", "studentenfutter", "eis",
    # Tiefkühl und Fertiges
    "tiefkuehlpizza", "pommes", "tiefkuehlgemuese", "blaetterteig",
    # Haushalt und Reinigung
    "spuelmittel", "spuelmaschinentabs", "waschmittel", "weichspueler",
    "allzweckreiniger", "badreiniger", "muellbeutel", "gefrierbeutel",
    "alufolie", "frischhaltefolie", "backpapier", "kuechenrolle",
    "schwaemme", "batterien", "kerzen",
    # Hygiene
    "toilettenpapier", "taschentuecher", "zahnpasta", "zahnbuerste",
    "duschgel", "shampoo", "seife", "deodorant", "rasierer",
    "damenbinden", "tampons", "sonnencreme", "pflaster",
    # Tier
    "katzenfutter", "hundefutter",
)


def begriffe_aus_umgebung(umgebung=None) -> list[str]:
    """Die Liste für diesen Lauf: `PICKNICK_BEGRIFFE`, sonst `BEGRIFFE`.

    Der Wert darf ein Dateipfad sein (ein Begriff je Zeile, `#` leitet einen
    Kommentar ein) oder eine kommagetrennte Aufzählung. Der Dateiweg ist der
    eigentliche Zweck: die Liste soll sich ändern lassen, ohne dass jemand
    dafür eine systemd-Unit anfasst oder das Paket neu ausrollt.
    """
    umgebung = os.environ if umgebung is None else umgebung
    roh = (umgebung.get(ENV_BEGRIFFE) or "").strip()
    if not roh:
        return list(BEGRIFFE)
    pfad = Path(roh)
    if pfad.is_file():
        return aus_datei(pfad)
    liste = [b.strip() for b in roh.split(",") if b.strip()]
    if not liste:
        # Gesetzt, aber leer: das ist ein Konfigurationsfehler. Stillschweigend
        # auf die Vorgabe zurückzufallen hiesse, einen Vollcrawl zu starten,
        # den niemand angefordert hat.
        raise ValueError(f"{ENV_BEGRIFFE} ist gesetzt, enthält aber keinen Begriff.")
    return liste


def aus_datei(pfad: str | Path) -> list[str]:
    """Begriffe aus einer Datei, ein Begriff je Zeile."""
    zeilen = Path(pfad).read_text(encoding="utf-8").splitlines()
    liste = []
    for zeile in zeilen:
        begriff = zeile.split("#", 1)[0].strip()
        if begriff:
            liste.append(begriff)
    if not liste:
        raise ValueError(f"{pfad} enthält keinen Begriff.")
    return liste
