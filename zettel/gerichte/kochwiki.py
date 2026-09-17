"""Der Koch-Wiki-Client: aus Wikitext wird ein Rezept (WB-606).

Dieselbe Rolle wie `zettel.gerichte.chefkoch`, dieselben Schlüssel, eine
andere Quelle — und der Grund, warum es sie gibt, ist die Lizenz. Die
Nutzungsbedingungen von chefkoch.de untersagen in §5.1 das automatische
Auslesen; das Koch-Wiki stellt **9.210 Rezepte unter CC BY-SA 3.0**, mit
Namensnennung und Weitergabe unter gleichen Bedingungen. Was hier geholt
wird, darf auch weitergegeben werden — solange die Herkunft mitwandert.
Deshalb trägt jedes geparste Rezept `quelle`, `site_url` und `lizenz`.

**Warum überhaupt ein Wiki-Parser und nicht Freitext-NLP.** Im Koch-Wiki ist
jede Zutat verlinkt:

    * 2 mittelgroße [[Zutat:Karotte|Karotten]]

Das sind Menge (2), Beiwerk (mittelgroß), kanonischer Name (`Karotte`) und
Anzeigeform (`Karotten`) — getrennt, in der Quelle, ohne Raten. Genau die
Felder, die `chefkoch.parse_zutaten` aus `ingredientGroups[].ingredients[]`
zieht. Der kanonische Name ist dabei mehr, als Chefkoch liefert: er ist über
alle 9.210 Rezepte derselbe und damit der bessere Schlüssel für den Katalog.

**Gemessen an 120 Rezepten** (Stichprobe `random.seed(42)` aus allen 9.210,
2026-09-17, eingecheckt als `tests/fixtures/kochwiki_stichprobe.json`; die
Zahlen stehen in `EVALS.md` und werden von `test_kochwiki.py` als
Mindestwerte erzwungen). Vier Stellen sind teuer bezahlt und stehen deshalb
unten bei den Funktionen, die sie treffen: die Abschnittsgrenze
(`_abschnitt`), der Plural in der Zeiterkennung (`_ZEITSTUECK`), die
Varianten (`parse_zutaten`) und die mehrteilige Zeitangabe (`zeit`).

**robots.txt** sperrt `Spezial:`, `Special:` und `index.php?` — die API
unter `/w/api.php` ist frei (gelesen am 2026-09-17). Die Höflichkeit ist
dieselbe wie bei Chefkoch: `PAUSE_S` zwischen zwei Anfragen, ein
User-Agent, der das Projekt benennt, und ein Zwischenspeicher, damit
dasselbe Gericht nur einmal kostet.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode

BASE = "https://www.kochwiki.org/w/api.php"
ARTIKEL = "https://www.kochwiki.org/wiki/"
SOURCE = "kochwiki"

#: Die Lizenz wandert mit, an jedem einzelnen Rezept. Sie ist keine Fussnote:
#: Share-Alike ist der Preis dieser Quelle, und ihn zu verschweigen wäre
#: dasselbe Versäumnis, das WB-604 gerade aufgeräumt hat.
LIZENZ = "CC BY-SA 3.0"
LIZENZ_URL = "https://creativecommons.org/licenses/by-sa/3.0/"

#: Wie viele Treffer die Suche vorlegt — dieselbe Zahl wie bei Chefkoch,
#: damit eine spätere Wahl zwischen den Quellen nicht auch noch eine Wahl
#: zwischen zwei Listenlängen ist.
LIMIT = 12

#: Pause zwischen zwei Anfragen, wie bei Chefkoch und aus demselben Grund.
PAUSE_S = 1.5

#: Ein ehrlicher User-Agent: wir holen ein paar öffentliche Rezepte in einem
#: privaten Tempo, und wenn das jemandem nicht passt, soll er es sehen.
#: REIN ASCII — httpx kodiert Kopfzeilen als ASCII.
USER_AGENT = ("zettel/1.0 (private household shopping list; "
              "2 req per dish, cached)")


def such_url(gericht: str, limit: int = LIMIT) -> str:
    """Volltextsuche im Artikelnamensraum. Eine Anfrage, JSON zurück."""
    q = urlencode({"action": "query", "list": "search",
                   "srsearch": (gericht or "").strip(), "srnamespace": 0,
                   "srlimit": int(limit), "format": "json",
                   "formatversion": 2})
    return f"{BASE}?{q}"


def detail_url(titel: str) -> str:
    """Der Wikitext EINES Artikels — `rvslots=main`, sonst kommt er leer."""
    q = urlencode({"action": "query", "prop": "revisions", "rvprop": "content",
                   "rvslots": "main", "titles": (titel or "").strip(),
                   "format": "json", "formatversion": 2})
    return f"{BASE}?{q}"


def artikel_url(titel: str) -> str:
    """Die Seite für Menschen — sie steht als Herkunft an jedem Rezept."""
    return ARTIKEL + quote((titel or "").strip().replace(" ", "_"))


def parse_detail(payload: dict) -> tuple[str, str] | None:
    """Die API-Antwort -> `(titel, wikitext)`, oder `None`.

    `None` heisst „diesen Artikel gibt es nicht" (die API antwortet dann mit
    `missing: true` und trotzdem 200) — das ist kein Fehler, sondern eine
    Auskunft, und der Aufrufer muss beides unterscheiden können.
    """
    seiten = ((payload or {}).get("query") or {}).get("pages") or []
    if isinstance(seiten, dict):              # formatversion=1: Dict nach ID
        seiten = list(seiten.values())
    for seite in seiten:
        if not isinstance(seite, dict) or seite.get("missing"):
            continue
        for rev in (seite.get("revisions") or []):
            slot = (rev.get("slots") or {}).get("main") or {}
            inhalt = (slot.get("content") or rev.get("content")
                      or rev.get("*"))
            if inhalt:
                return str(seite.get("title") or ""), str(inhalt)
    return None


# --------------------------------------------------------------------------
# Abschnitte

#: **Falle 1, und die teuerste von allen.** Ein Abschnitt endet nicht am
#: nächsten `==`, sondern am nächsten `==`, dem kein weiteres `=` folgt —
#: `(?=^==[^=]|\Z)`. Mit dem naiven `(?=^==|\Z)` bricht jeder Abschnitt an
#: seiner ersten Untergruppe ab, und Untergruppen sind hier die Regel:
#: gemessen an 120 Rezepten **837 statt 1.288 Zutatenzeilen** (ein Drittel
#: lautlos weg) und bei 32 von 120 Rezepten **null** Zubereitungsschritte
#: statt 119 von 120. Eine Klammer, 65 % gegen 99 %.
_ABSCHNITT = re.compile(r"^==\s*(?P<titel>[^=].*?)\s*==\s*$"
                        r"(?P<inhalt>.*?)(?=^==[^=]|\Z)", re.M | re.S)

#: Eine Untergruppe: drei Gleichheitszeichen oder mehr. Vier kommen wirklich
#: vor (`==== Variante (1) ====`), deshalb wird die Tiefe gezählt und nicht
#: geraten.
_UNTERTITEL = re.compile(
    r"^(?P<tiefe>={3,})\s*(?P<titel>.+?)\s*(?P=tiefe)\s*$", re.M)


def abschnitt(wikitext: str, *namen: str) -> str:
    """Den Inhalt des ersten Abschnitts liefern, dessen Titel passt.

    Verglichen wird am Anfang und ohne Gross-/Kleinschreibung: die Quelle
    schreibt „Zutaten", aber auch „Zubereitung" und „Zubereitung des Teigs".
    """
    for m in _ABSCHNITT.finditer(wikitext or ""):
        titel = m.group("titel").strip().casefold()
        if any(titel.startswith(n.casefold()) for n in namen):
            return m.group("inhalt")
    return ""


def _bloecke(inhalt: str) -> list[tuple[str | None, int, str]]:
    """Ein Abschnitt -> `[(untertitel, tiefe, text), …]`, Reihenfolge erhalten.

    Der erste Block trägt `None`: was vor der ersten Untergruppe steht,
    gehört zu keiner.
    """
    bloecke: list[tuple[str | None, int, str]] = []
    pos, titel, tiefe = 0, None, 0
    for m in _UNTERTITEL.finditer(inhalt or ""):
        bloecke.append((titel, tiefe, (inhalt or "")[pos:m.start()]))
        titel = saeubere(m.group("titel"))
        tiefe, pos = len(m.group("tiefe")), m.end()
    bloecke.append((titel, tiefe, (inhalt or "")[pos:]))
    return [b for b in bloecke if b[2].strip() or b[0]]


# --------------------------------------------------------------------------
# Wikitext-Kleinkram

#: Die Bruchvorlage `{{B|1|2}}` = ½ (3,7 % der Zutatenzeilen) und die
#: Unicode-Brüche, die daneben vorkommen (5 von 1.288).
_BRUCH_VORLAGE = re.compile(r"\{\{\s*B\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\}\}")
_BRUCH_ZEICHEN = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
                  "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}

#: Ein Link: `[[Ziel|Text]]`, `[[Ziel]]`, `[[:Kategorie:Ziel|Text]]`.
_LINK = re.compile(
    r"\[\[\s*(?P<ziel>[^|\]]+?)\s*(?:\|\s*(?P<text>[^\]]*?)\s*)?\]\]")

#: Kursives trägt Beiwerk („(Typ Dijon)", „(für die Auflaufform)") — mit
#: einer Ausnahme: `''oder''` ist kein Beiwerk, sondern das Bindewort
#: zwischen zwei Zutaten. Wer es nach `usage_info` wegräumt, klebt zwei
#: Zutaten zu einer zusammen.
_KURSIV = re.compile(r"''+(?P<inhalt>.*?)''+")
_BINDEWORT = {"oder", "bzw.", "bzw", "und", "o."}

#: Eine Klammer trägt dasselbe Beiwerk, nur ohne Kursivschrift.
_KLAMMER = re.compile(r"\(([^()]*)\)")

#: Alles andere in doppelten geschweiften Klammern ist Auszeichnung, keine
#: Zutat: `{{Alkohol}}` markiert 22 Zeilen der Stichprobe als alkoholhaltig.
_VORLAGE = re.compile(r"\{\{[^{}]*\}\}")

#: Was von einem Namen am Rand abfällt. Kein `.` — „Msp." und „Pr." sind
#: Einheiten, und ein Name endet hier nie auf einem Punkt.
_RAND = " \t,;:*#-–—'\"·"


def _text_von_links(text: str) -> str:
    """Links durch ihren Anzeigetext ersetzen, sonst nichts anfassen."""
    return _LINK.sub(lambda m: (m.group("text") or m.group("ziel")).strip(),
                     text or "")


def saeubere(text: str) -> str:
    """Wikitext -> Klartext. Links werden zu ihrem Anzeigetext.

    Für die Zubereitungsschritte und für jedes Feld, das ein Mensch liest:
    was übrig bleibt, darf keine eckige, geschweifte oder Hochkomma-Klammer
    mehr enthalten — das ist eine Zusicherung und wird geprüft.
    """
    roh = _text_von_links(text or "")
    roh = _BRUCH_VORLAGE.sub(
        lambda m: _bruch_text(m.group(1), m.group(2)), roh)
    roh = _VORLAGE.sub(" ", roh)
    roh = _KURSIV.sub(lambda m: m.group("inhalt"), roh)
    roh = roh.replace("'''", "").replace("''", "")
    roh = re.sub(r"<br\s*/?>", " ", roh, flags=re.I)
    roh = re.sub(r"<[^>]+>", " ", roh)
    return re.sub(r"\s+", " ", roh).strip()


def _bruch_text(zaehler, nenner) -> str:
    for zeichen, wert in _BRUCH_ZEICHEN.items():
        if abs(wert - int(zaehler) / max(int(nenner), 1)) < 1e-9:
            return zeichen
    return f"{zaehler}/{nenner}"


# --------------------------------------------------------------------------
# Menge und Einheit

#: Die Einheiten der Quelle, gefaltet -> so, wie sie geschrieben werden.
#: Die rechte Seite ist so gewählt, dass `zettel.mengen.falte` sie auf das
#: faltet, was dort in `UMRECHNUNG`/`SCHREIBWEISE` steht: „Pr." und „Bd"
#: wären sonst eigene Einheiten neben „Prise" und „Bund" und liessen sich
#: mit ihnen nicht zusammenzählen.
EINHEITEN = {
    "g": "g", "gr": "g", "gramm": "g", "kg": "kg", "mg": "mg",
    "l": "l", "ltr": "l", "liter": "l", "ml": "ml", "cl": "cl", "dl": "dl",
    "el": "EL", "essloeffel": "EL", "tl": "TL", "teeloeffel": "TL",
    "msp": "Msp.", "messerspitze": "Msp.",
    "prise": "Prise", "prisen": "Prise", "pr": "Prise",
    "pck": "Pck.", "pkt": "Pck.", "packung": "Pck.", "paeckchen": "Pck.",
    "stk": "Stk", "stueck": "Stk", "st": "Stk", "stck": "Stk",
    "bund": "Bund", "bd": "Bund", "buendel": "Bund",
    "zehe": "Zehe", "zehen": "Zehe",
    "scheibe": "Scheibe", "scheiben": "Scheibe",
    "stange": "Stange", "stangen": "Stange",
    "staengel": "Stängel",
    "zweig": "Zweig", "zweige": "Zweig",
    "blatt": "Blatt", "blaetter": "Blatt",
    "tasse": "Tasse", "tassen": "Tasse",
    "glas": "Glas", "glaeser": "Glas",
    "dose": "Dose", "dosen": "Dose",
    "becher": "Becher", "kopf": "Kopf", "koepfe": "Kopf",
    "handvoll": "Handvoll", "schuss": "Schuss", "spritzer": "Spritzer",
    "portion": "Portion", "portionen": "Portion",
    "becherchen": "Becher", "kugel": "Kugel", "kugeln": "Kugel",
    "tropfen": "Tropfen", "flasche": "Flasche", "flaschen": "Flasche",
}

#: Eine Zahl am Zeilenanfang: `2`, `0,2`, `1{{B|1|2}}`, `1 ½`, `2–3`.
#: **Der Bereich wird auf seine UNTERE Grenze gelesen** (4,2 % der Zeilen):
#: wer „2–3 Zwiebeln" braucht, kauft zwei und hat nicht zu wenig — die
#: dritte ist die Zugabe, nicht der Bedarf. Die Obergrenze geht nicht
#: verloren, sie steht als Beiwerk in `usage_info`.
_ZAHL = r"\d+(?:[.,]\d+)?"
_BRUCH = r"(?:\{\{\s*B\s*\|\d+\|\d+\s*\}\}|[½¼¾⅓⅔⅛⅜⅝⅞])"
_MENGE = re.compile(
    rf"^\s*(?P<roh>(?:{_ZAHL}\s*)?{_BRUCH}|{_ZAHL})"
    rf"(?P<bereich>\s*(?:[–—-]|bis)\s*(?:(?:{_ZAHL}\s*)?{_BRUCH}|{_ZAHL}))?"
    r"(?=\s|$)")


def _zahl_von(text: str) -> float | None:
    """`„1{{B|1|2}}"` -> 1.5, `„0,2"` -> 0.2, `„½"` -> 0.5."""
    wert = 0.0
    gefunden = False
    m = re.match(rf"\s*({_ZAHL})", text or "")
    if m:
        wert += float(m.group(1).replace(",", "."))
        gefunden = True
    for br in _BRUCH_VORLAGE.finditer(text or ""):
        wert += int(br.group(1)) / max(int(br.group(2)), 1)
        gefunden = True
    for zeichen, teil in _BRUCH_ZEICHEN.items():
        if zeichen in (text or ""):
            wert += teil
            gefunden = True
    return round(wert, 4) if gefunden else None


def _einheit_von(wort: str) -> str | None:
    """Ein Wort -> die geschriebene Einheit, oder `None`.

    Eine Whitelist und keine Heuristik „das Wort nach der Zahl ist die
    Einheit": in der Stichprobe steht dort 72-mal ein Adjektiv („12 große",
    „11 frische", „8 mittelgroße"). „große" ist keine Einheit, sondern die
    Beschreibung einer Zwiebel.
    """
    from zettel.mengen import falte

    return EINHEITEN.get(falte(wort)) if wort else None


# --------------------------------------------------------------------------
# Zutaten

def parse_zutaten(wikitext: str) -> list[dict]:
    """Der Zutatenabschnitt -> flache Liste mit Menge, Einheit und Herkunft.

    Je Zeile: `gruppe`, `raw_name`, `name`, `amount`, `unit`, `usage_info` —
    dieselben sechs Schlüssel wie bei `chefkoch.parse_zutaten`, damit beide
    Quellen dasselbe liefern und nicht zweierlei. Dazu zwei, die Chefkoch
    nicht hat und die der eigentliche Gewinn dieser Quelle sind:

        kanonisch      das Linkziel `[[Zutat:Karotte|…]]` -> „Karotte",
                       über alle 9.210 Rezepte derselbe Name. `None`, wenn
                       die Zeile auf einen Artikel oder eine Kategorie zeigt.
        warengruppe    `[[:Kategorie:Pflanzliche Öle|Speiseöl]]` -> die
                       Kategorie. Das ist eine Warengruppe und kein Produkt
                       (4,1 % der Zeilen); ohne dieses Feld sähe sie aus wie
                       eine Zutat ohne kanonischen Namen.
        alternativen   was hinter „ oder " stand (12,3 % der Zeilen), als
                       eigene Liste. Nicht verloren, aber auch keine zweite
                       Einkaufszeile: gekauft wird das Erste.

    **Falle 3: `=== Variante (n) ===` sind keine Gruppen.** 27 von 120
    Rezepten haben echte Untergruppen („Teig", „Füllung", „Garnitur") — die
    gehören ins `gruppe`-Feld wie Chefkochs `header`. Zwei von 120 haben
    `Variante (1)` bis `Variante (5)`: das sind **alternative Rezepte im
    selben Artikel**. Wer sie zusammenwirft, legt fünf Varianten desselben
    Bratapfels als eine Liste mit fünffachem Zucker vor. Genommen wird die
    erste jedes Laufs — „erste jedes Laufs" und nicht „die erste
    überhaupt", weil derselbe Artikel unter einer späteren Gruppe
    („Vanillesauce") noch einmal Varianten auffächern kann, und die gehören
    zu einem anderen Teil des Gerichts.
    """
    zutaten: list[dict] = []
    vorheriger: tuple[str | None, int] = (None, 0)
    eltern: dict[int, str] = {}
    for titel, tiefe, text in _bloecke(abschnitt(wikitext, "Zutaten")):
        if _ist_variante(titel):
            if _ist_variante(vorheriger[0]) and vorheriger[1] == tiefe:
                vorheriger = (titel, tiefe)
                continue                      # zweite Variante desselben Laufs
            gruppe = _elternteil(eltern, tiefe)
        else:
            gruppe = titel
            if titel:
                eltern = {t: g for t, g in eltern.items() if t < tiefe}
                eltern[tiefe] = titel
        vorheriger = (titel, tiefe)
        fett: str | None = None
        for zeile in text.splitlines():
            zeile = zeile.strip()
            if not zeile.startswith(("*", "#")):
                continue
            rumpf = zeile.lstrip("*#").strip()
            kopf = re.fullmatch(r"'''(?P<t>[^']+)'''", rumpf)
            if kopf:                          # `* '''Marinade'''` ist ein Kopf
                fett = kopf.group("t").strip()
                continue
            eintrag = _zutat(rumpf)
            if eintrag:
                eintrag["gruppe"] = fett or gruppe or None
                zutaten.append(eintrag)
    return zutaten


def _ist_variante(titel) -> bool:
    return bool(re.match(r"\s*Variante", str(titel or ""), re.I))


def _elternteil(eltern: dict[int, str], tiefe: int) -> str | None:
    """Die Gruppe, in der eine Variante steht — nicht die Variante selbst.

    Die erste Variante EINES Gerichts ist keine Untergruppe, sondern das
    Rezept; sie trägt deshalb die Überschrift, unter der sie steht
    („Vanillesauce") oder gar keine. Gesucht wird die nächste flachere
    Überschrift, die selbst keine Variante ist.
    """
    hoeher = [t for t in eltern if t < tiefe]
    return eltern[max(hoeher)] if hoeher else None


def _zutat(rumpf: str) -> dict | None:
    """Eine Zutatenzeile -> ein Eintrag, oder `None` (keine Zutat)."""
    beiwerk: list[str] = []

    def merken(m):
        inhalt = saeubere(m.group("inhalt") if "inhalt" in m.groupdict()
                          else m.group(1))
        # `''oder''` bindet zwei Zutaten, es schmückt keine.
        if inhalt.casefold() in _BINDEWORT:
            return f" {inhalt} "
        inhalt = inhalt.strip("()").strip()   # `''(Typ Dijon)''` ist gerahmt
        if inhalt:
            beiwerk.append(inhalt)
        return " "

    text = _KURSIV.sub(merken, rumpf)
    text = _KLAMMER.sub(merken, text)
    text = _VORLAGE.sub(lambda m: m.group(0) if _BRUCH_VORLAGE.fullmatch(
        m.group(0).strip()) else " ", text)

    menge = _MENGE.match(text)
    amount = _zahl_von(menge.group("roh")) if menge else None
    if menge and menge.group("bereich"):
        beiwerk.insert(0, saeubere(menge.group(0)).strip())
    text = text[menge.end():] if menge else text

    unit = None
    wort = re.match(r"\s*([A-Za-zÄÖÜäöüß.]+)", text)
    if wort:
        unit = _einheit_von(wort.group(1))
        if unit:
            text = text[wort.end():]

    haupt, weitere = _nach_oder(text)
    name, kanonisch, warengruppe, rest = _benennen(haupt)
    if not name:
        return None
    beiwerk = [b for b in ([rest] if rest else []) + beiwerk if b]
    alternativen = []
    for teil in weitere:
        alt_name, alt_kanonisch, alt_gruppe, alt_rest = _benennen(teil)
        if alt_name:
            alternativen.append({"name": alt_name, "kanonisch": alt_kanonisch,
                                 "warengruppe": alt_gruppe})
        elif alt_rest:
            beiwerk.append(f"oder {alt_rest}")
    return {
        "gruppe": None,
        "raw_name": name,
        "name": name,
        "amount": amount,
        "unit": unit,
        "usage_info": ", ".join(dict.fromkeys(beiwerk)) or None,
        "kanonisch": kanonisch,
        "warengruppe": warengruppe,
        "alternativen": alternativen,
    }


def _nach_oder(text: str) -> tuple[str, list[str]]:
    """`„A oder B"` -> `(„A", [„B"])` — aber nur, wo „oder" Zutaten trennt.

    12,3 % der Zeilen bieten eine Alternative an („[[Rinderbrühe]] oder
    [[Zutat:Brühwürfel|Würfelbrühe]]"). Genauso oft steht „oder" aber
    zwischen zwei EIGENSCHAFTEN derselben Zutat: „frisch gemahlener, weißer
    oder schwarzer [[Zutat:Pfeffer|Pfeffer]]", „1 400 g-Dose geschälte oder
    500 g frische [[Zutat:Tomate|Tomaten]]". Wer dort trennt, legt eine
    Zutat namens „frisch gemahlener, weißer" an und schiebt den Pfeffer in
    die Alternativen.

    Die Unterscheidung braucht kein Wörterbuch: ein Stück, das keinen Link
    trägt, ist keine Zutat — es wird an das nächste angehängt.
    """
    stuecke, offen = [], ""
    for teil in re.split(r"\s+oder\s+", text or ""):
        offen = f"{offen} oder {teil}" if offen else teil
        if _LINK.search(offen):
            stuecke.append(offen)
            offen = ""
    if offen:
        stuecke.append(offen)
    return (stuecke[0] if stuecke else (text or "")), stuecke[1:]


def _benennen(text: str) -> tuple[str | None, str | None, str | None, str]:
    """Ein Zeilenstück -> `(name, kanonisch, warengruppe, beiwerk)`.

    Der ERSTE Link ist die Zutat; was davor und dahinter steht, ist Beiwerk
    („100 g ganze [[Zutat:Haselnuss|Haselnüsse]]" — „ganze" beschreibt die
    Nuss und gehört nicht in ihren Namen). Ohne Link bleibt der Klartext als
    Name stehen: 5,4 % der Zeilen verlinken nichts, und „Salz" ist auch ohne
    Link Salz.
    """
    m = _LINK.search(text or "")
    if not m:
        klartext = saeubere(text).strip(_RAND)
        return (klartext or None), None, None, ""
    ziel = m.group("ziel").strip()
    name = saeubere(m.group("text") or ziel).strip(_RAND)
    kanonisch = warengruppe = None
    if ziel.startswith("Zutat:"):
        kanonisch = ziel[len("Zutat:"):].split("#")[0].strip() or None
    elif ziel.lstrip(":").startswith("Kategorie:"):
        warengruppe = ziel.lstrip(":")[len("Kategorie:"):].strip() or None
    beiwerk = " ".join(p for p in (saeubere(text[:m.start()]),
                                   saeubere(text[m.end():])) if p)
    return (name or None), kanonisch, warengruppe, beiwerk.strip(_RAND)


# --------------------------------------------------------------------------
# Zubereitung

def schritte(wikitext: str) -> list[str]:
    """Der Zubereitungsabschnitt -> die einzelnen Schritte.

    Aufzählungszeilen, in der Reihenfolge der Quelle und ohne Auszeichnung.
    Die Zwischenüberschriften („Vorbereitung", „Fertigstellung") fallen
    weg — ein Schritt ist, was man tut; die Gliederung steht in der Quelle
    und hilft beim Lesen, nicht beim Kochen.
    """
    raus = []
    for zeile in abschnitt(wikitext, "Zubereitung", "Zubereitungsweise",
                           "Herstellung").splitlines():
        zeile = zeile.strip()
        if not zeile.startswith(("*", "#")):
            continue
        satz = saeubere(zeile.lstrip("*#").strip())
        if satz:
            raus.append(satz)
    return raus


# --------------------------------------------------------------------------
# Der Vorlagenkopf: Menge, Zeit, Schwierigkeit

def kopf(wikitext: str) -> dict:
    """Die Felder der `{{Rezept}}`-Vorlage. In allen 120 Rezepten vorhanden."""
    ende = (wikitext or "").find("|}}")
    block = (wikitext or "")[:ende if ende > 0 else 4000]
    felder = {}
    for m in re.finditer(r"^\s*\|\s*([A-Za-zÄÖÜäöüß]+)\s*=\s*(.*?)\s*$",
                         block, re.M):
        felder.setdefault(m.group(1).strip(), m.group(2).strip())
    return felder


#: Vier Stufen im Koch-Wiki, drei bei Chefkoch — und die Karte sagt
#: „Schwierigkeit 2 von 3". „leicht bis mittel" wird deshalb AUFGERUNDET:
#: wer zu viel gewarnt wird, verliert einen Abend weniger als der, der zu
#: wenig gewarnt wird.
SCHWIERIGKEIT = {"leicht": 1, "leicht bis mittel": 2, "mittel": 2,
                 "mittel bis schwierig": 3, "schwierig": 3}


def schwierigkeit(text) -> int | None:
    return SCHWIERIGKEIT.get(" ".join(str(text or "").split()).casefold())


#: Was in der `Menge` steht, ist nicht immer eine Portionszahl: „1 Liter",
#: „6 Gläser zu je 200 ml", „Für ein 1 l Einmachglas". Eine Ausbeute ist
#: keine Portion — steht dort ein Mass, bleibt `servings` leer, statt eine
#: Eins zu erfinden, mit der später hochgerechnet würde.
_MASS = re.compile(r"\b(l|ltr|liter|ml|cl|dl|g|gr|gramm|kg)\b", re.I)


def portionen(text) -> int | None:
    """`„4 Personen"` -> 4, `„4–6 Personen"` -> 4, `„1 Liter"` -> `None`."""
    roh = saeubere(str(text or ""))
    if not roh or _MASS.search(roh):
        return None
    m = re.search(rf"{_ZAHL}", roh)
    if not m:
        return None
    zahl = _zahl_von(m.group(0))
    return int(zahl) if zahl and zahl >= 1 else None


#: **Falle 2.** `(Minute|Std)\b` findet „Minuten" NICHT — die Wortgrenze
#: scheitert am Plural-n, und die Zeiterkennung lag damit bei 15,8 % statt
#: 96,7 %. Deshalb `Minuten?`, und deshalb steht diese Zeile hier und nicht
#: eingebaut in eine grössere.
_ZEITSTUECK = re.compile(
    rf"(?P<zahl>(?:{_ZAHL}\s*)?{_BRUCH}|{_ZAHL})\s*"
    r"(?P<einheit>Minuten?|Min\.?|Stunden?|Std\.?|h\b|Tage?|Nächte?|Wochen?|"
    r"Monate?)", re.I)

_IN_MINUTEN = {"min": 1, "minute": 1, "minuten": 1, "std": 60, "stunde": 60,
               "stunden": 60, "h": 60, "tag": 1440, "tage": 1440,
               "nacht": 1440, "nächte": 1440, "woche": 10080,
               "wochen": 10080, "monat": 43200, "monate": 43200}

#: Wie ein Teilstück der Zeitangabe heisst -> in welches Feld es gehört.
#: Gesucht wird der Name IM Teilstück, nicht davor: die Quelle schreibt
#: beides („Zubereitung: 15 Minuten" und „50 Minuten Vorbereitung").
_ZEITWORT = (
    ("prep", ("zubereitung", "vorbereitung", "arbeitszeit", "vorbereiten",
              "zubereiten", "fertigstellung", "filtern")),
    ("cook", ("kochzeit", "backzeit", "garzeit", "bratzeit", "schmorzeit",
              "grillzeit", "braten", "backen", "kochen", "garen", "schmoren",
              "dünstzeit", "dämpfzeit")),
    ("rest", ("ruhezeit", "rastzeit", "gehzeit", "abkühl", "kühl", "quellzeit",
              "einweichzeit", "einweichen", "einlegezeit", "einlegen",
              "einsalzzeit", "marinierzeit", "marinieren", "marinade",
              "mazerationszeit", "durchziehzeit", "durchziehen", "trocknen",
              "trockenzeit", "wartezeit", "über nacht", "tiefkühlung",
              "gefrierzeit", "reifezeit", "ziehzeit")),
)

#: Woran ein Teilstück endet. `<br>` gehört dazu (die Quelle trennt auch so),
#: das Komma nur zwischen Buchstaben — „1,5 Stunden" ist ein Teilstück.
_ZEITTRENNER = re.compile(
    r"\s*(?:\+|;|<br\s*/?>|(?<=[A-Za-zÄÖÜäöüß.])\s*,)\s*", re.I)


def zeit(text) -> dict:
    """Die Zeitangabe -> `{"prep": …, "cook": …, "rest": …}` in Minuten.

    **Falle 4, und eine Entscheidung, die begründet werden muss.** 59 von
    120 Zeitangaben sind Prosa aus mehreren Teilen:

        „Sauce: 20 Minuten + Abkühlzeit: 2 Stunden + Zubereitung: 5 Minuten
         + Backzeit: 20 Minuten"

    Die erste Zahl zu nehmen ist falsch — hier wären das 20 Minuten Sauce,
    und in „Einsalzzeit 3 Stunden; Zubereitung 30 Minuten" wären es drei
    Stunden Warten, die als Arbeitszeit in die Wochenplanung gingen. Die
    Summe zu nehmen ist genauso falsch: aus 25 Minuten Arbeit würden mit
    „+ 2 Monate Einlegezeit" 86.425 Minuten.

    **Genommen wird deshalb nur, was benannt ist**, und zwar in das Feld,
    das der Name nennt: „Zubereitung" und „Vorbereitung" sind Arbeitszeit,
    „Backzeit"/„Kochzeit" sind Kochzeit, „Abkühlzeit"/„Einlegezeit" sind
    Ruhezeit — dieselben drei Felder, die Chefkoch mitliefert. Ein
    unbenanntes Teilstück in einer mehrteiligen Angabe wird **weggelassen**:
    es ist nicht zu entscheiden, ob dort gearbeitet oder gewartet wird, und
    ein fehlender Wert kostet weniger als ein falscher. Steht die Angabe
    dagegen für sich allein („45 Minuten"), ist sie die Arbeitszeit — so
    liest sie auch jeder Mensch.

    Gemessen an der Stichprobe: 108 von 120 bekommen so eine Arbeitszeit
    (90,0 %), 33 eine Kochzeit, 30 eine Ruhezeit. Eine Angabe ohne Zahl
    („ca. Minuten", „keine Angaben", „bis Minuten" — es gibt sie) liefert
    überall `None` und wirft nicht.
    """
    roh = str(text or "")
    stuecke = [s for s in _ZEITTRENNER.split(roh) if s and s.strip()]
    raus: dict[str, int | None] = {"prep": None, "cook": None, "rest": None}
    for stueck in stuecke:
        minuten = _minuten(stueck)
        if minuten is None:
            continue
        feld = _zeitfeld(stueck)
        if feld is None:
            if len(stuecke) > 1:
                continue                      # unbenannt und mehrteilig: weg
            feld = "prep"
        raus[feld] = (raus[feld] or 0) + minuten
    return raus


def _minuten(stueck: str) -> int | None:
    m = _ZEITSTUECK.search(stueck)
    if not m:
        return None
    from zettel.mengen import falte

    zahl = _zahl_von(m.group("zahl"))
    faktor = _IN_MINUTEN.get(falte(m.group("einheit")).casefold())
    if zahl is None or not faktor:
        return None
    return int(round(zahl * faktor))


def _zeitfeld(stueck: str) -> str | None:
    klein = stueck.casefold()
    treffer = []
    for feld, woerter in _ZEITWORT:
        for wort in woerter:
            pos = klein.find(wort)
            if pos >= 0:
                treffer.append((pos, feld))
    return min(treffer)[1] if treffer else None


# --------------------------------------------------------------------------
# Das Rezept

def parse_rezept(titel: str, wikitext: str) -> dict:
    """Wikitext -> dieselbe Form, die `chefkoch.parse_rezept` liefert.

    Dieselben dreizehn Schlüssel, damit die Quelle austauschbar ist und
    nicht ein zweites Format neben dem ersten steht. Zwei Felder kann das
    Koch-Wiki nicht füllen: es hat keine Bewertungen. Sie stehen deshalb auf
    `None` und `0` — eine erfundene Vier hätte die Wahl zwischen zwei
    Rezepten auf eine Zahl gestützt, die niemand vergeben hat.

    `rezept_id` ist der Artikeltitel: in einem Wiki ist er der Schlüssel,
    er steht in der URL, und er überlebt eine Umbenennung als Weiterleitung.

    Dazu drei Felder, die Chefkoch nicht braucht und diese Quelle schon:
    `quelle`, `lizenz` und `lizenz_url`. CC BY-SA verlangt Namensnennung und
    Weitergabe unter gleichen Bedingungen — eine Rezeptkarte, die das nicht
    zeigt, verletzt die Lizenz, unter der sie das Rezept überhaupt hat.
    """
    felder = kopf(wikitext)
    zeiten = zeit(felder.get("Zeit"))
    schrittliste = schritte(wikitext)
    return {
        "rezept_id": (titel or "").strip(),
        "titel": (titel or "").strip(),
        "site_url": artikel_url(titel),
        "servings": portionen(felder.get("Menge")),
        "prep_minutes": zeiten["prep"],
        "cook_minutes": zeiten["cook"],
        "rest_minutes": zeiten["rest"],
        "difficulty": schwierigkeit(felder.get("Schwierigkeit")),
        "rating": None,
        "votes": 0,
        "instructions": "\n".join(schrittliste) or None,
        "schritte": schrittliste,
        "zutaten": parse_zutaten(wikitext),
        "quelle": SOURCE,
        "lizenz": LIZENZ,
        "lizenz_url": LIZENZ_URL,
    }
