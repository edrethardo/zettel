"""Katalogsuche über SQLite-FTS5.

Der Shop ruft nie eine fremde Seite auf (Spec 3) — gesucht wird ausschliesslich
im lokalen Index, den der Crawler füllt. Diese Suche ist zugleich Stufe 2 des
Agenten (Spec 6): das Modell liefert Begriffe, der Shop sucht selbst und legt
die Kandidaten vor. Deshalb gibt `search()` den FTS-Rang mit zurück; er wird als
Score in den RETRIEVER-Span geschrieben (Spec 7.1) und ist dort die Antwort auf
die Frage, ob ein Fehlgriff am Modell lag oder daran, dass die Suche das
richtige Produkt nie vorgelegt hat.
"""
from __future__ import annotations

import re
import sqlite3

from picknick import db

#: Wie stark ein Treffer im Produktnamen gegenüber einem blossen
#: Kategorietreffer zählt. „Milch" soll die Milchflasche vor den Joghurt aus der
#: Kategorie „Milch, Molkerei & Butter" schieben — aber der Joghurt soll
#: erscheinen, denn oft ist die Kategorie das Einzige, was passt.
GEWICHT_NAME = 10.0
GEWICHT_KATEGORIE = 1.0

#: Nur die norm-Spalten werden durchsucht (siehe `db.UMLAUTE`). Die
#: unveränderten Spalten bleiben im Index, weil andere Abfragen sie benutzen.
_SUCHSPALTEN = "{norm_name norm_cat}"

#: Wie viele Kandidaten die FTS-Abfrage holt, bevor in Python nachsortiert
#: wird — ein Vielfaches der gewünschten Trefferzahl, mindestens `_MIN_POOL`.
#: Nachsortieren kann nur umstellen, was vorliegt: „Zwiebeln Gelb" stand vor
#: WB-339 auf Platz 3, aber ein Wortreffer kann auch auf Platz 60 liegen, wenn
#: davor sechzig Komposita stehen. Gemessen (10.361 Produkte): die ersten drei
#: Treffer der sechs Problembegriffe ändern sich zwischen Faktor 3 und Faktor
#: 25 nicht mehr, 5 kostet ~2 ms je Abfrage. Tiefer zu graben brächte also
#: nichts als Laufzeit.
KANDIDATEN_FAKTOR = 5
_MIN_POOL = 100

#: Deutsche Endungen, die aus dem Suchbegriff dieselbe Sache in einer anderen
#: Form machen. „Zwiebel" soll „Zwiebeln" als vollen Wortreffer zählen dürfen —
#: sonst hilft die ganze Wortgrenze bei genau dem Fall nicht, der sie ausgelöst
#: hat. Die Gegenrichtung fehlt bewusst: die Kandidaten kommen aus einer
#: Präfixsuche, ihre Wörter FANGEN alle mit dem Begriff an — ein kürzeres Wort
#: als der Begriff kann also gar nicht dabei sein.
_ENDUNGEN = ("n", "en", "e", "s")

#: Die Stufen, nach denen vor dem Rang sortiert wird. Bewusst Zahlen mit
#: Abstand: der Kategoriezuschlag verschiebt innerhalb einer Namensstufe und
#: hebt nie über die nächste.
STUFE_WORT = 4          # Begriff steht als ganzes Wort im Produktnamen
STUFE_PRAEFIX = 2       # Begriff ist nur der Anfang eines längeren Wortes
STUFE_KATEGORIE = 1     # Zuschlag: er steht als ganzes Wort in der Kategorie

#: Satzzeichen an den Wortenden. INNEN bleibt alles stehen, und das ist der
#: Kern der Sache: „Schoko-Milch" und „Spaghetti-Eis" sind zusammengesetzte
#: Wörter und sollen genau so behandelt werden wie „Zwiebelbrot", nicht als
#: zwei Wörter. „Zwiebeln Gelb, Netz" verliert dagegen nur das Komma.
_RAND = re.compile(r"^\W+|\W+$", re.UNICODE)

#: Alles, was kein Wortzeichen ist, fliegt raus. Das ist die ganze Absicherung
#: gegen kaputte FTS5-Syntax: ein Anführungszeichen, ein `*`, ein `NEAR` in
#: Klammern — nichts davon überlebt diesen Filter, also kann nichts davon die
#: Abfrage sprengen. Weisse Liste statt Escapen, weil Escapen bei FTS5 von der
#: Position im Ausdruck abhängt und damit eine Fehlerquelle bleibt.
_WORT = re.compile(r"\w+", re.UNICODE)

#: Spalten, die ein Treffer mitbringt. Bewusst dieselben Namen wie in `product`.
_PRODUKT_SPALTEN = (
    "id", "source", "external_id", "name", "brand", "price_cents",
    "price_per_unit_cents", "unit_text", "unit", "image_path",
    "category_l1", "category_l2", "category_l3", "in_stock", "last_seen_at",
)


def begriffe(text: str) -> list[str]:
    """Freitext -> normalisierte Suchwörter. Kann leer sein."""
    return _WORT.findall(db.normalisiere(text or ""))


def fts_query(text: str) -> str | None:
    """Baut den FTS5-Ausdruck. `None`, wenn nichts Suchbares übrig bleibt.

    Jedes Wort wird als Präfix gesucht (`"milch"*`), weil im Deutschen das
    gesuchte Wort oft hinten am Kompositum klebt und der Nutzer beim Tippen
    ohnehin selten fertig ist. Mehrere Wörter werden UND-verknüpft — wer
    „passierte tomaten" tippt, will nicht jede Tomate.
    """
    woerter = begriffe(text)
    if not woerter:
        return None
    # Anführungszeichen um jedes Wort, damit ein Wort wie „and" oder „near"
    # nicht als Operator gelesen wird. Enthalten kann es keine, siehe _WORT.
    ausdruck = " ".join(f'"{w}"*' for w in woerter)
    return f"{_SUCHSPALTEN} : ({ausdruck})"


def _namensworte(text: str) -> list[str]:
    """Text -> normalisierte Wörter, an Leerzeichen getrennt.

    NICHT `_WORT.findall()`: das zerlegt auch an Bindestrichen, und dann wäre
    „Schoko-Milch" zwei Wörter und der Begriff „Milch" ein voller Wortreffer
    darin. Ein Bindestrich-Kompositum ist aber genau das — ein Kompositum, wie
    „Zwiebelbrot", nur mit Strich. Getrennt wird deshalb nur an Leerzeichen,
    und Satzzeichen fallen nur an den Wortenden weg („Netz," -> „Netz").
    """
    worte = []
    for roh in db.normalisiere(text or "").casefold().split():
        wort = _RAND.sub("", roh)
        if wort:
            worte.append(wort)
    return worte


def _ohne_marke(name: str, brand: str | None) -> list[str]:
    """Die Wörter des Namens ohne den Markennamen, der vorne dranklebt.

    Der Katalog schreibt die Marke oft in den Namen: „Hemme Milch Schoko-Milch"
    mit der Marke „Hemme Milch Uckermark". Ohne diesen Schritt trägt jedes
    Hemme-Produkt das Wort „Milch" im Namen, und der Wortreffer unterscheidet
    dann nichts mehr — die Schoko-Milch stünde weiter vor der Milch. Abgezogen
    wird nur das gemeinsame VORDERE Stück, denn dort steht die Marke; und wenn
    davon nichts übrig bliebe (der Name IST die Marke), gilt wieder der ganze
    Name, sonst hätte das Produkt gar keine Wörter mehr.
    """
    worte = _namensworte(name)
    marke = _namensworte(brand or "")
    i = 0
    while i < len(worte) and i < len(marke) and worte[i] == marke[i]:
        i += 1
    return worte[i:] or worte


def _ist_wort(begriff: str, worte: list[str]) -> bool:
    """Steht `begriff` als ganzes Wort in `worte` — Mehrzahl eingeschlossen?"""
    if begriff in worte:
        return True
    return any(wort == begriff + endung
               for endung in _ENDUNGEN for wort in worte)


def wortstufe(suchworte: list[str], produkt: dict) -> int:
    """Wie gut ein Produkt den Begriff trägt — 0 bis 5, grösser ist besser.

    Das ist das Signal, das bm25 strukturell fehlt (WB-339): die Präfixsuche
    `zwiebel*` trifft „Zwiebeln" und „Zwiebelbrot" gleich gut, und der
    bm25-Abstand zwischen beiden lag gemessen bei 0,01 bis 0,17 — Rauschen.
    Die Frage, die entscheidet, ist eine andere: steht der Begriff als GANZES
    WORT im Namen, oder nur als Anfang eines längeren Wortes?

        „Zwiebeln Gelb, Netz"    Wort (Mehrzahl)          -> STUFE_WORT
        „Exner Zwiebelbrot"      nur Präfix               -> STUFE_PRAEFIX
        „Maison Les Alexandrins" nur Präfix (franz. Wein) -> STUFE_PRAEFIX

    Der Zuschlag `STUFE_KATEGORIE` kommt oben drauf, wenn der Begriff auch als
    ganzes Wort in der Kategorie steht. Er sortiert innerhalb einer Namensstufe
    das Lebensmittel vor die Süssigkeit, die zufällig so heisst: „Milch" trifft
    „Lindt LINDOR Beutel Milch" als Wort genauso wie „Weihenstephan Frische
    Milch" — aber nur eins davon liegt in der Kategorie „Milch". Er hebt nie
    über die nächste Namensstufe, denn der Name bleibt das stärkere Signal
    (siehe `GEWICHT_NAME`).

    Bei mehreren Suchwörtern zählt das SCHWÄCHSTE. Die FTS-Abfrage verknüpft
    sie mit UND; ein Produkt ist nur so gut, wie sein schlechtestes Wort passt.
    """
    ohne_marke = _ohne_marke(produkt.get("name") or "", produkt.get("brand"))
    im_namen = _namensworte(produkt.get("name") or "") \
        + _namensworte(produkt.get("brand") or "")
    kategorie = []
    for spalte in ("category_l1", "category_l2", "category_l3"):
        kategorie += _namensworte(produkt.get(spalte) or "")

    stufen = []
    for wort in suchworte:
        if _ist_wort(wort, ohne_marke):
            stufe = STUFE_WORT
        elif any(k.startswith(wort) for k in im_namen):
            stufe = STUFE_PRAEFIX
        else:
            # Nur über die Kategorie hereingekommen — der Name sagt nichts.
            stufe = 0
        if _ist_wort(wort, kategorie):
            stufe += STUFE_KATEGORIE
        stufen.append(stufe)
    return min(stufen) if stufen else 0


def _bm25() -> str:
    gewichte = [0.0] * len(db.FTS_SPALTEN)
    gewichte[db.FTS_SPALTEN.index("norm_name")] = GEWICHT_NAME
    gewichte[db.FTS_SPALTEN.index("norm_cat")] = GEWICHT_KATEGORIE
    return "bm25(product_fts, " + ", ".join(str(g) for g in gewichte) + ")"


def search(con: sqlite3.Connection, begriff: str,
           limit: int = 20) -> list[dict]:
    """Sucht aktive Produkte zu einem Freitextbegriff.

    Gibt Wörterbücher mit allen Produktspalten plus `rang` und `wortstufe`
    zurück. Sortiert wird **erst nach `wortstufe`, dann nach `rang`**.

    `rang` ist das negierte bm25 und damit positiv: je grösser, desto besser.
    bm25 selbst ist negativ und „kleiner ist besser" — als Score an Phoenix
    übergeben wäre das genau falsch herum lesbar, und ein Score, den man
    rückwärts lesen muss, wird irgendwann rückwärts gelesen. Seine Bedeutung
    ist seit WB-339 unverändert; er ist nur nicht mehr das ERSTE Kriterium,
    sondern das Feinsortiermittel INNERHALB einer Stufe. Über die Liste hinweg
    fällt er darum nicht mehr monoton.

    Warum überhaupt eine Stufe davor: bm25 kann „Zwiebeln Gelb, Netz" nicht von
    „Exner Zwiebelbrot" unterscheiden, weil die Präfixsuche `zwiebel*` auf
    beides gleich gut passt — gemessen lagen 0,01 zwischen ihnen, und das
    Zwiebelbrot gewann. Siehe `wortstufe()`.

    Nachsortiert wird in Python, nicht in SQL: die Wortgrenze braucht die
    deutsche Mehrzahl und den markenfreien Namensteil, und beides in
    verschachtelten `replace()`-Ausdrücken zu bauen wäre eine Abfrage, die
    niemand mehr liest. Dafür muss die FTS-Abfrage mehr Kandidaten holen, als
    zurückgegeben werden (`KANDIDATEN_FAKTOR`) — sonst könnte das Nachsortieren
    einen Wortreffer, der weit hinten liegt, gar nicht erst sehen.
    """
    # `begriffe()` normalisiert schon (Umlaute); klein muss noch sein, weil
    # der Vergleich gegen den Produktnamen gleich casegefaltet läuft.
    suchworte = [w.casefold() for w in begriffe(begriff)]
    query = fts_query(begriff)
    if query is None:
        return []
    spalten = ", ".join(f"p.{s}" for s in _PRODUKT_SPALTEN)
    rows = con.execute(
        f"SELECT {spalten}, -{_bm25()} AS rang"
        "  FROM product_fts f JOIN product p ON p.id = f.rowid"
        " WHERE product_fts MATCH ? AND p.active = 1"
        # Der Name als zweites Kriterium: bei gleichem Rang sonst die Reihen-
        # folge der rowids, und die ändert sich mit jedem Crawl.
        " ORDER BY rang DESC, p.name ASC"
        " LIMIT ?",
        (query, max(limit * KANDIDATEN_FAKTOR, _MIN_POOL))).fetchall()

    treffer = [dict(r) for r in rows]
    for p in treffer:
        p["wortstufe"] = wortstufe(suchworte, p)
    treffer.sort(key=_reihenfolge)
    return treffer[:limit]


def _reihenfolge(p: dict) -> tuple:
    """Der Sortierschlüssel: Stufe, Rang, Namenslänge, Name.

    Die Namenslänge greift erst bei GLEICHEM Rang — sie ersetzt das frühere
    `p.name ASC` und ist wie dieses nur dazu da, die Reihenfolge von der
    rowid-Folge zu lösen, die sich mit jedem Crawl ändert. Kürzer zuerst, weil
    das Grundprodukt schlichter heisst als das Fertiggericht daraus („Baby
    Mais, Schale" vor „Prignitzer Maishähnchenkeule, ohne Haut, ohne
    Knochen"). Mehr Gewicht bekommt die Länge bewusst nicht: sie ist ein
    Bauchgefühl, kein gemessenes Signal, und „Weihenstephan Frische Milch 1,5%
    Fett" ist länger als „Milka Tender Milch" und trotzdem das Richtige.
    """
    return (-p["wortstufe"], -p["rang"], len(p["name"] or ""), p["name"] or "")


def suche_kette(con: sqlite3.Connection, suchbegriffe, *, limit: int = 20,
                obergrenze: int | None = None) -> list[dict]:
    """Sucht eine ganze Begriffskette und VEREINIGT die Treffer (WB-340).

    Stufe 1 liefert je Zutat mehrere Begriffe, vom genauesten zum
    allgemeinsten („Auberginen", „Aubergine"). Hier wird jeder davon gesucht;
    die Treffer werden **nach Produkt-ID entdoppelt** und als eine
    Kandidatenliste zurückgegeben. Jeder Treffer trägt zusätzlich `via`: den
    Begriff, der ihn gebracht hat. Ohne dieses Feld wäre nach der Vereinigung
    nicht mehr zu sagen, warum ein Produkt vorlag — und genau das will der
    Span-Vertrag (Spec 7.1) beantworten können.

    **Nicht „der erste Begriff, der etwas findet, gewinnt".** Das ist gemessen
    und schlechter: „Auberginen" findet das Fertiggericht
    „Gemüse-Auberginen-Masala" mit Rang 16,55, die echte Aubergine kommt erst
    über den Singular und mit niedrigerem Rang (13,08). Wer nach dem ersten
    Fund abbricht, zurrt das Fertiggericht fest. Die Vereinigung legt beides
    vor und lässt Stufe 3 entscheiden.

    **Die Reihenfolge ist die der KETTE, nicht die des Rangs.** Der genaueste
    Begriff zuerst, innerhalb eines Begriffs so, wie `search()` sortiert (seit
    WB-339: Wortstufe, dann Rang). Global nach
    `rang` zu sortieren wäre falsch, und zwar gemessen: bm25 ist über Abfragen
    hinweg nicht geeicht (siehe `obs.spans.schwaechste_suche`), ein seltenes
    Wort bekommt strukturell einen höheren Rang als ein häufiges. Für die Kette
    [Mais, Süßmais, Körnig] steht dann der „MIIL Körniger Frischkäse" mit 14,01
    ÜBER der Maishähnchenkeule mit 9,70 — nicht weil er besser passt, sondern
    weil „Körnig" seltener ist als „Mais". Nach Rang sortiert bekäme Stufe 3
    drei Frischkäse zuerst vorgelegt. Die Kettenreihenfolge ist die einzige
    Rangfolge, die hier etwas bedeutet: sie kommt vom Modell und ist nach
    Genauigkeit geordnet.

    `obergrenze` begrenzt die Zahl der Kandidaten je Zutat, und das Budget wird
    **vom genauen Ende der Kette her ausgegeben**: der erste Begriff bekommt
    seine Treffer ganz, dann der zweite, und der letzte bekommt, was übrig ist.
    Mit der Vorgabe (`plan.MAX_KANDIDATEN` = 2 × `plan.KANDIDATEN`) passen die
    ersten beiden Begriffe immer vollständig hinein; gekürzt wird nur am
    allgemeinen Ende. Das ist auch das Ende, an dem das Modell entgleist —
    gemessen wurden als letzte Begriffe „Körnig", „Papikra", „Konzenzrat". Ein
    solcher Begriff findet irgendetwas, und was er findet, soll als Letztes
    stehen und als Erstes wegfallen.

    Gesucht wird trotzdem jeder Begriff: was die Kette gebracht hat, steht
    dadurch vollständig im RETRIEVER-Span, auch wenn die Obergrenze das Ende
    abschneidet.
    """
    gewaehlt: list[dict] = []
    gesehen: set[int] = set()
    for begriff in suchbegriffe:
        for p in search(con, begriff, limit=limit):
            pid = int(p["id"])
            if pid in gesehen:
                # Schon über einen genaueren Begriff vorgelegt. Die Herkunft
                # bleibt beim ersten — er beschreibt die Zutat besser.
                continue
            gesehen.add(pid)
            if obergrenze is not None and len(gewaehlt) >= obergrenze:
                continue
            gewaehlt.append({**p, "via": begriff})
    return gewaehlt
