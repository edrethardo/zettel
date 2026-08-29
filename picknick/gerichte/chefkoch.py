"""Der Chefkoch-Client: aus einem Gerichtsnamen wird ein Rezept (WB-338).

Reines HTTP gegen eine offene JSON-API, kein Browser, kein Schlüssel. Die
Schnittstelle ist gemessen (2026-08-28), nicht geraten:

    GET https://api.chefkoch.de/v2/recipes?query=<gericht>&limit=12
    -> 200, {"count": 85, "results": [{"recipe": {...}}, …]}
       je Rezept: id, title, rating {rating, numVotes}, difficulty,
       preparationTime, siteUrl — aber KEINE Zutaten

    GET https://api.chefkoch.de/v2/recipes/<id>
    -> 200, servings, preparationTime, cookingTime, restingTime, difficulty,
       instructions, siteUrl, ingredientGroups[].ingredients[]
       mit amount, unit, name, usageInfo

`/v2/recipes/<id>/ingredients` gibt es NICHT (404) — die Zutaten kommen im
Detail-Objekt.

**Warum eine Quelle und nicht das Modell.** Gemessen an „alles für Pho": das
Modell hängt sich an „Rind" und zählt zwanzig Fleischteile auf, von denen
KEINER im Katalog steht. Chefkoch kennt 85 Pho-Rezepte; das bestbewertete
liefert 23 Zutaten, davon 15 im Katalog. Bei einem Gericht ausserhalb seines
Wissens liefert das Modell nichts Brauchbares und merkt es nicht.

**Dieses Modul geht ins Netz — und seit WB-367 auch aus dem Request-Pfad
heraus.** Bis dahin galt Spec 3 wörtlich (wie für `picknick.scrapers.knuspr`):
der Web-Prozess trug nur einen Wunsch ein und liess einen eigenen Prozess
holen. Gemessen am 2026-08-28 kostet ein Gericht 90 bis 147 ms (Suche plus
Detail), der Modellweg daneben 35.600 ms — die Regel schützte einen Request,
der ohnehin eine halbe Minute auf die vLLM-Box wartet, vor einem Zehntel
Sekunde. Der Katalog bleibt davon unberührt: der wird nie live abgefragt.

**robots.txt erlaubt genau diese zwei Endpunkte** (gesperrt sind nur
`/v2/search/suggestions/` und die Kommentare zweier einzelner Rezepte). Das
ist nicht dasselbe wie die Nutzungsbedingungen — die sind NICHT gelesen. Der
Vorbehalt steht im README; hier steht die Höflichkeit: `PAUSE_S` zwischen
zwei Anfragen, ein ehrlicher User-Agent, zwei Anfragen je Gericht und
danach nie wieder (Zwischenspeicher).
"""
from __future__ import annotations

import re
import time

BASE = "https://api.chefkoch.de/v2"
SOURCE = "chefkoch"

#: Wie viele Treffer die Suche vorlegt. Zwölf: genug, dass ein
#: bestbewertetes Rezept nicht am Rand des Fensters liegt, wenig genug, dass
#: die Fixture lesbar bleibt.
LIMIT = 12

#: Wie viele davon zur WAHL gestellt werden (WB-387) — einschliesslich des
#: vorausgewählten. Zwölf sind zu viele für ein Telefon, drei zu wenig.
#: Gemessen an „Lasagne" (2026-08-29, 2.155 Rezepte bei Chefkoch, zwölf in
#: der Antwort), nach Gewicht sortiert:
#:
#:     4.837  Vegetarische Spinat-Gemüse-Lasagne   <- vorausgewählt
#:     4.723  Julies feine Gemüselasagne
#:     4.695  Lasagne                              <- die klassische, mit Hack
#:     4.686  Zucchini-Lasagne
#:     4.624  Spinatlasagne
#:     4.593  Béchamel-Hackfleisch-Lasagne
#:     ------------------------------------------ ab hier nicht angeboten
#:     4.591  Lasagne Bolognese
#:     …
#:
#: **Die Zahl entscheidet, ob überhaupt beide Lager vorkommen.** Wer
#: „Lasagne" tippt und Hackfleisch meint, findet bei drei Angeboten nur
#: Vegetarisches; bei sechs stehen die klassische Lasagne (Platz 3) und eine
#: Béchamel-Hackfleisch-Lasagne (Platz 6) mit darin. Nach oben ist die
#: Grenze das Telefon: sechs Zeilen à drei Zahlen stehen unter der
#: Rezeptkarte, ohne sie zu verdecken.
ANGEBOT = 6

#: Pause zwischen zwei Anfragen. Ein Gericht kostet zwei — Suche und Detail —
#: und danach nie wieder eine, weil das Ergebnis zwischengespeichert wird.
PAUSE_S = 1.5

#: Die Frist für den Lauf im Hintergrund (`--gericht`, `--alle`). Grosszügig:
#: dort wartet niemand, und ein langsamer Abruf ist besser als keiner.
TIMEOUT_S = 30.0

#: Die Frist für den Abruf IM REQUEST (WB-367). Zwei Sekunden je Anfrage, und
#: das ist gemessen reichlich — am 2026-08-28 gegen api.chefkoch.de:
#:
#:     Chili con Carne   Suche 131 ms + Detail 16 ms =  147 ms
#:     Kartoffelsalat    Suche  92 ms + Detail 17 ms =  109 ms
#:     Sushi             Suche  70 ms + Detail 20 ms =   90 ms
#:     Ratatouille       Suche  93 ms + Detail 20 ms =  114 ms
#:
#: Also rund das Vierzehnfache des langsamsten gemessenen Abrufs. Sie ist die
#: Frist JE ANFRAGE; ein Gericht kostet zwei, im schlimmsten Fall wartet der
#: Chat also vier Sekunden, bevor er auf den Modellweg zurückfällt — neben
#: einem Modelllauf von 20 bis 35 s fällt auch das nicht auf.
TIMEOUT_SYNC_S = 2.0

#: Ein ehrlicher User-Agent, wie beim Katalog-Crawler und aus demselben Grund:
#: wir holen ein paar öffentliche Rezepte in einem privaten Tempo, und wenn
#: das jemandem nicht passt, soll er es sehen und nicht raten müssen.
#: REIN ASCII — httpx kodiert Kopfzeilen als ASCII und wirft bei einem Umlaut
#: einen UnicodeEncodeError, noch bevor die erste Anfrage rausgeht.
USER_AGENT = ("picknick/1.0 (private household shopping list; "
              "2 req per dish, cached)")

# --------------------------------------------------------------------------
# Das beste Rezept, nicht das erste

#: Die bayes-artige Gewichtung: `(note * stimmen + PRIOR * PRIOR_STIMMEN) /
#: (stimmen + PRIOR_STIMMEN)`. Sie zieht jede Note zur Mitte, und zwar umso
#: stärker, je weniger Stimmen dahinterstehen.
#:
#: **Warum überhaupt gewichtet wird**, gemessen an „pho" (2026-08-28):
#:
#:     5,00 aus   2 Stimmen   „Pho Ga - Vietnamesische Nudelsuppe"
#:     4,84 aus  62 Stimmen   „Pho Bo - Vietnamesische Rindfleischsuppe"
#:
#: Roh nach `rating` gewinnt eine Fünf, die zwei Leute vergeben haben. Mit der
#: Gewichtung wird aus 5,00/2 eine 3,64 und aus 4,84/62 eine 4,51 — und die
#: Wahl fällt auf das Rezept, das 62 Leute gekocht haben.
PRIOR = 3.5
PRIOR_STIMMEN = 20

#: Chefkoch-Plus-Rezepte werden bei der Wahl übergangen, und das ist keine
#: Geschmacksfrage: **ihre Stimmenzahl ist keine.** Gemessen an „pho" tragen
#: die beiden `isPlus`-Treffer BEIDE exakt `numVotes: 255` (4,71 und 4,44),
#: und der Detail-Endpunkt liefert zu genau diesen beiden `rating: null`.
#: 255 ist der grösste Wert eines Bytes; das ist ein Platzhalter, kein
#: Abstimmungsergebnis. Mit ihm gewinnt „Wildenten-Pho mit rosa gebratener
#: Wildente" (Gewicht 4,62) gegen „Pho Bo" (4,51) — und zwar auf Grundlage
#: einer Zahl, die nichts bedeutet.
#:
#: Übergangen werden sie nur, SOLANGE es andere gibt: ein Gericht, zu dem
#: Chefkoch ausschliesslich Plus-Rezepte kennt, bekommt eines davon. Ihre
#: Zutaten und ihre Zubereitung liefert die API vollständig.
PLUS_UEBERGEHEN = True


def such_url(gericht: str, limit: int = LIMIT) -> str:
    from urllib.parse import urlencode
    q = urlencode({"query": (gericht or "").strip(), "limit": int(limit)})
    return f"{BASE}/recipes?{q}"


def detail_url(rezept_id: str) -> str:
    return f"{BASE}/recipes/{rezept_id}"


def _zahl(wert, vorgabe=None):
    if wert in (None, ""):
        return vorgabe
    try:
        return float(wert)
    except (TypeError, ValueError):
        return vorgabe


def _ganz(wert):
    zahl = _zahl(wert)
    return int(zahl) if zahl is not None and zahl > 0 else None


def parse_treffer(payload: dict) -> list[dict]:
    """`results[].recipe` -> die Felder, nach denen gewählt wird.

    Alles andere aus der Antwort (Vorschaubilder, Autorin, Kampagnen) bleibt
    liegen: gewählt wird nach Bewertung, und was nicht gebraucht wird, muss
    auch nicht mitwandern.
    """
    treffer = []
    for eintrag in ((payload or {}).get("results") or []):
        rezept = eintrag.get("recipe") if isinstance(eintrag, dict) else None
        if not isinstance(rezept, dict):
            # Manche Antworten legen das Rezept flach in die Liste.
            rezept = eintrag if isinstance(eintrag, dict) else None
        if not isinstance(rezept, dict) or not rezept.get("id"):
            continue
        bewertung = rezept.get("rating") or {}
        treffer.append({
            "rezept_id": str(rezept["id"]),
            "titel": (rezept.get("title") or "").strip(),
            "rating": _zahl(bewertung.get("rating"), 0.0) or 0.0,
            "votes": int(_zahl(bewertung.get("numVotes"), 0) or 0),
            "site_url": rezept.get("siteUrl"),
            "difficulty": _ganz(rezept.get("difficulty")),
            "prep_minutes": _ganz(rezept.get("preparationTime")),
            "plus": bool(rezept.get("isPlus")),
        })
    return treffer


def gewicht(treffer: dict) -> float:
    """Die gewichtete Note eines Treffers. Grösser ist besser."""
    note = float(treffer.get("rating") or 0.0)
    stimmen = int(treffer.get("votes") or 0)
    return ((note * stimmen + PRIOR * PRIOR_STIMMEN)
            / (stimmen + PRIOR_STIMMEN))


def bestes(treffer: list[dict]) -> dict | None:
    """Das bestbewertete Rezept — nicht das erste der Liste.

    Bei Gleichstand entscheidet die Stimmenzahl, danach die Reihenfolge, in
    der Chefkoch geliefert hat. `None`, wenn nichts vorlag.
    """
    if not treffer:
        return None
    return (zur_wahl(treffer) or [None])[0]


def zur_wahl(treffer: list[dict], grenze: int | None = None) -> list[dict]:
    """Die Treffer in der Reihenfolge, in der sie zur Wahl stehen (WB-387).

    Bestgewichtet zuerst — der erste ist damit genau der, den `bestes()`
    nimmt; das ist keine zweite Rangfolge, sondern dieselbe, nur nicht mehr
    auf einen Eintrag zusammengestrichen.

    Plus-Rezepte fallen heraus, solange es andere gibt (siehe
    `PLUS_UEBERGEHEN`): ihre Stimmenzahl ist keine, und was nicht gewählt
    werden darf, soll auch nicht zur Wahl stehen.
    """
    auswahl = list(treffer or [])
    if PLUS_UEBERGEHEN:
        ohne_plus = [t for t in auswahl if not t.get("plus")]
        auswahl = ohne_plus or auswahl
    auswahl.sort(key=lambda t: (gewicht(t), t.get("votes") or 0),
                 reverse=True)
    return auswahl[:grenze] if grenze else auswahl


# --------------------------------------------------------------------------
# Das Rezept

#: Ein Klammerzusatz am Zutatennamen: „Zwiebel(n)", „Ei(er)", „Öl (z. B.
#: Sesamöl)". Für die Suche ist beides Beiwerk — der Plural sowieso, und der
#: Hinweis gehört in die Zubereitung, nicht in einen Suchbegriff.
_KLAMMER = re.compile(r"\s*\([^)]*\)")

#: Mehrfache Leerzeichen und Zeilenumbrüche im Zutatennamen.
_LEER = re.compile(r"\s+")


def zutat_kette(name: str) -> list[str]:
    """Chefkochs Schreibweise -> eine Begriffskette, genau zuerst (WB-340).

        Zwiebel(n)          -> ["Zwiebel"]
        Tomaten, passierte  -> ["passierte Tomaten", "Tomaten"]
        Käse, geriebener    -> ["geriebener Käse", "Käse"]
        Rinderfilet         -> ["Rinderfilet"]

    **Die Komma-Form wird umgedreht und nicht abgeschnitten.** Das ist der
    teuerste Befund der Vorprobe: „Tomaten, passierte" nur auf „Tomaten" zu
    kürzen legte frische Tomaten und Ketchup vor, während das Rezept 500 ml
    passierte Tomaten will. Wo die Form zur Zutat gehört (passiert, gerieben,
    tiefgekühlt), darf sie nicht wegfallen — deshalb steht sie im ERSTEN
    Kettenglied, und das allgemeine Wort steht als zweites daneben. Genau
    dafür gibt es die Kette: erst „passierte Tomaten", dann „Tomaten".

    Umgekehrt wird nur die einfache Form „Hauptwort, Beiwort": bei mehreren
    Kommata wäre nicht mehr zu sagen, was Adjektiv und was Zusatz ist
    („Tomaten, passiert, aus der Dose"). Dann bleibt es beim Hauptwort plus
    dem vollen Original als genauestem Glied.

    **Kein Präfix-Kürzen.** „Staudensellerie" -> „Staud" fand Staud's
    Apfelmus, „Paprikapulver" -> „Paprikap" die Thüringer Paprikapastete. Ein
    falscher Treffer sieht aus wie ein richtiger; Komposita zerlegt das
    Modell (`plan.zutatenbegriffe`), nicht diese Funktion.
    """
    roh = _LEER.sub(" ", (name or "").replace("\n", " ")).strip()
    ohne_klammer = _LEER.sub(" ", _KLAMMER.sub("", roh)).strip(" ,;")
    if not ohne_klammer:
        return []
    teile = [t.strip() for t in ohne_klammer.split(",")]
    teile = [t for t in teile if t]
    kette: list[str] = []
    if len(teile) == 2 and " " not in teile[1]:
        kette.append(f"{teile[1]} {teile[0]}")
        kette.append(teile[0])
    elif len(teile) > 1:
        kette.append(ohne_klammer)
        kette.append(teile[0])
    else:
        kette.append(ohne_klammer)
    # Entdoppeln, Reihenfolge behalten.
    gesehen, sauber = set(), []
    for begriff in kette:
        schluessel = begriff.casefold()
        if begriff and schluessel not in gesehen:
            gesehen.add(schluessel)
            sauber.append(begriff)
    return sauber


#: Was in jedem Haushalt steht. Wird NUR im Rückfall gebraucht, wenn das
#: Modell die Zutatenliste nicht zerlegen konnte — im Normalfall lässt der
#: Prompt von `plan.zutatenbegriffe` solche Zeilen weg. Bewusst kurz: eine
#: lange Liste würde irgendwann etwas wegwerfen, das jemand kaufen wollte.
VORRAT = frozenset((
    "salz", "pfeffer", "salz und pfeffer", "wasser", "zucker", "essig",
    "oel", "olivenoel", "sonnenblumenoel", "rapsoel", "mehl", "eiswuerfel",
))


def ist_vorrat(name: str) -> bool:
    """Steht das in jedem Haushalt? Dann gehört es nicht auf den Zettel.

    Verglichen wird gegen die genaueste Form der Kette, damit „Öl" fliegt
    und „Sesamöl" bleibt: Letzteres kauft man, Ersteres steht da.
    """
    from picknick.db import normalisiere

    kette = zutat_kette(name)
    if not kette:
        return True
    schluessel = normalisiere(kette[0].strip().casefold())
    return schluessel in VORRAT


def parse_zutaten(payload: dict) -> list[dict]:
    """`ingredientGroups[].ingredients[]` -> flache Liste mit Menge und Einheit.

    Die Gruppe („Für die Brühe:") wandert an jede Zutat mit: sie ist beim
    Kochen die halbe Ordnung des Rezepts, und ohne sie stünde eine Liste aus
    35 Zeilen ohne Gliederung da.
    """
    zutaten: list[dict] = []
    for gruppe in ((payload or {}).get("ingredientGroups") or []):
        if not isinstance(gruppe, dict):
            continue
        kopf = (gruppe.get("header") or "").strip().strip(":").strip() or None
        for z in (gruppe.get("ingredients") or []):
            if not isinstance(z, dict):
                continue
            roh = _LEER.sub(" ", (z.get("name") or "").strip())
            if not roh:
                continue
            kette = zutat_kette(roh)
            zutaten.append({
                "gruppe": kopf,
                "raw_name": roh,
                "name": kette[0] if kette else roh,
                "amount": _zahl(z.get("amount")),
                "unit": (z.get("unit") or "").strip() or None,
                "usage_info": (z.get("usageInfo") or "").strip(" ,;") or None,
            })
    return zutaten


def schritte(instructions: str) -> list[str]:
    """Chefkochs Zubereitungstext -> die einzelnen Schritte.

    Getrennt wird an den Zeilenumbrüchen, die Chefkoch selbst setzt — daraus
    werden nummerierte Schritte in der Ansicht. Ein Absatz mit 3.924 Zeichen
    als eine Wand ist auf einem Telefon am Herd unbrauchbar, und die Struktur
    ist bereits im Text; sie muss nur nicht zusammengezogen werden.
    """
    text = (instructions or "").replace("\r\n", "\n").replace("\r", "\n")
    return [z.strip() for z in text.split("\n") if z.strip()]


def parse_rezept(payload: dict) -> dict:
    """Die Detailantwort -> das, was gespeichert und gekocht wird.

    Zeiten und Schwierigkeit gehören dazu und nicht bloss die Zutaten: „480
    Minuten Kochzeit" ist beim Planen wichtiger als die Zutatenliste — wer
    abends um sieben Pho anfängt, sollte das vorher wissen.
    """
    d = payload or {}
    bewertung = d.get("rating") or {}
    return {
        "rezept_id": str(d.get("id") or ""),
        "titel": (d.get("title") or "").strip(),
        "site_url": d.get("siteUrl"),
        "servings": _ganz(d.get("servings")),
        "prep_minutes": _ganz(d.get("preparationTime")),
        "cook_minutes": _ganz(d.get("cookingTime")),
        "rest_minutes": _ganz(d.get("restingTime")),
        "difficulty": _ganz(d.get("difficulty")),
        "rating": _zahl(bewertung.get("rating")),
        "votes": int(_zahl(bewertung.get("numVotes"), 0) or 0),
        "instructions": (d.get("instructions") or "").strip() or None,
        "schritte": schritte(d.get("instructions")),
        "zutaten": parse_zutaten(d),
    }


# --------------------------------------------------------------------------
# Der Abruf


class ChefkochFehler(RuntimeError):
    """Die Quelle hat nicht geliefert. Bricht nur diese eine Funktion."""


def hole(http, gericht: str, *, limit: int = LIMIT, pause_s: float = PAUSE_S,
         schlafen=time.sleep) -> dict | None:
    """Gericht -> das bestbewertete Rezept samt Zutaten, oder `None`.

    `http` muss ein `.get(url)` anbieten, dessen Ergebnis `.json()` kennt —
    httpx erfüllt das, Tests reichen einen Doppelgänger herein (kein Test
    geht ins Netz, Spec 13).

    `None` heisst „Chefkoch kennt dieses Gericht nicht" und ist kein Fehler:
    der Aufrufer merkt sich das und fällt auf den Modellweg zurück. Ein
    kaputter Abruf dagegen wirft `ChefkochFehler` — die beiden dürfen nicht
    dasselbe sein, sonst wird aus einer Störung stillschweigend ein „gibt es
    nicht", das tagelang zwischengespeichert bleibt.

    Genau ZWEI Anfragen, mit einer Pause dazwischen.
    """
    name = " ".join((gericht or "").split())
    if not name:
        raise ChefkochFehler("Ohne Gerichtsnamen gibt es nichts zu suchen.")
    try:
        payload = http.get(such_url(name, limit=limit)).json()
    except Exception as e:                       # noqa: BLE001 — bewusst breit
        raise ChefkochFehler(f"Suche fehlgeschlagen: "
                             f"{type(e).__name__}: {e}") from e
    treffer = parse_treffer(payload)
    wahl = bestes(treffer)
    if wahl is None:
        return None

    if pause_s:
        schlafen(pause_s)
    rezept = hole_detail(http, wahl)
    # **Die übrigen elf reisen mit** (WB-387). Sie stehen in derselben
    # Antwort, sie kosten nichts, und bis zu diesem Ticket wurden sie hier
    # weggeworfen. Wer sie speichert, kann später eine Wahl anbieten, ohne
    # noch einmal zu suchen.
    rezept["treffer"] = treffer
    return rezept


def hole_detail(http, wahl: dict) -> dict:
    """Das Detail zu EINEM bereits vorliegenden Treffer. Genau eine Anfrage.

    `wahl` ist ein Eintrag aus `parse_treffer` — aus der Suche von eben oder
    aus dem Zwischenspeicher (`dish_treffer`). Damit kostet die Wahl einer
    Alternative (WB-387) keinen zweiten Suchabruf: die zwölf Treffer liegen
    schon, geholt werden muss nur, was die Suche nicht mitliefert — Zutaten,
    Zubereitung, Koch- und Ruhezeit.
    """
    rezept_id = str(wahl.get("rezept_id") or "")
    if not rezept_id:
        raise ChefkochFehler("Ohne Rezept-ID gibt es nichts zu holen.")
    try:
        roh = http.get(detail_url(rezept_id)).json()
    except Exception as e:                       # noqa: BLE001
        raise ChefkochFehler(f"Rezept {rezept_id} nicht geladen: "
                             f"{type(e).__name__}: {e}") from e
    rezept = parse_rezept(roh)
    if not rezept["zutaten"]:
        raise ChefkochFehler(
            f"Rezept {rezept_id} kam ohne Zutaten zurück — Format geändert?")

    # Was die Suche besser weiss als das Detail: die Bewertung. Der
    # Detail-Endpunkt liefert sie für manche Rezepte gar nicht (`null`), und
    # sie ist der Grund, warum GENAU DIESES Rezept gewählt wurde — sie gehört
    # deshalb an das gespeicherte Rezept.
    rezept["rezept_id"] = rezept["rezept_id"] or rezept_id
    rezept["titel"] = rezept["titel"] or wahl.get("titel") or ""
    rezept["site_url"] = rezept["site_url"] or wahl.get("site_url")
    if rezept["rating"] is None:
        rezept["rating"] = wahl.get("rating")
        rezept["votes"] = wahl.get("votes") or 0
    rezept["gewicht"] = gewicht(wahl)
    rezept["quelle"] = SOURCE
    return rezept
