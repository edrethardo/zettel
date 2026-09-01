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

from zettel import db

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

#: Oberkategorien, die im Katalog gar nicht erscheinen (WB-343). Der Haushalt
#: hat keine Haustiere — 295 der 10.361 Produkte sind Tierfutter, und sie
#: verdrängten Lebensmittel systematisch: Tierfutter nennt den Rohstoff als
#: GANZES WORT („Huhn", „Ente", „Lachs"), Lebensmittel für Menschen fast immer
#: als Kompositum („Entenbrust", „Lachsfilet"). Die Wortgrenze aus WB-339 ist
#: damit ausgerechnet dort am stärksten, wo sie am wenigsten helfen soll —
#: gemessen lieferten 5 von 8 Rohstoff-Begriffen Tierfutter auf Platz 1.
#:
#: Gefiltert wird über die Kategorie und NICHT über einen Textabgleich auf
#: „tier": „Pflanzenbasierter Vorratschrank > Alternativen für tierische
#: Produkte" heisst so und würde mitfliegen — ausgerechnet die veganen
#: Produkte. Die Liste ist die Pflegestelle: benennt Knuspr die Oberkategorie
#: um, taucht das Tierfutter wieder auf. Das ist der Preis für einen Filter,
#: der ohne Modell und ohne Rateregel auskommt.
AUSGESCHLOSSENE_KATEGORIEN = ("Katzen", "Hunde")

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
    platzhalter = ", ".join("?" for _ in AUSGESCHLOSSENE_KATEGORIEN)
    rows = con.execute(
        f"SELECT {spalten}, -{_bm25()} AS rang"
        "  FROM product_fts f JOIN product p ON p.id = f.rowid"
        " WHERE product_fts MATCH ? AND p.active = 1"
        f"   AND coalesce(p.category_l1, '') NOT IN ({platzhalter})"
        # Der Name als zweites Kriterium: bei gleichem Rang sonst die Reihen-
        # folge der rowids, und die ändert sich mit jedem Crawl.
        " ORDER BY rang DESC, p.name ASC"
        " LIMIT ?",
        (query, *AUSGESCHLOSSENE_KATEGORIEN,
         max(limit * KANDIDATEN_FAKTOR, _MIN_POOL))).fetchall()

    treffer = [dict(r) for r in rows]
    for p in treffer:
        p["wortstufe"] = wortstufe(suchworte, p)
    treffer.sort(key=_reihenfolge)
    return treffer[:limit]


def count(con: sqlite3.Connection, begriff: str) -> int:
    """Wie viele aktive Produkte der Begriff überhaupt trifft.

    Dieselbe Bedingung wie in `search()`, nur ohne Sortieren und ohne Grenze —
    das Nachsortieren stellt die Reihenfolge um, es wirft nichts weg, also ist
    diese Zahl auch die Zahl der Treffer, die `search()` bei unbegrenztem
    `limit` lieferte.

    Gebraucht wird sie für einen einzigen Satz in der Oberfläche (WB-375):
    „Milch" hat 657 Treffer, gezeigt werden 60. Dass geschnitten wird, ist
    Absicht — auf dem Telefon scrollt niemand durch 657 Kacheln. Dass die
    Liste darüber schweigt, ist der Fehler: wer sein Produkt nicht sieht, hält
    den Katalog für lückenhaft statt die Suche für zu weit.

    **`CROSS JOIN`, und das ist die ganze Abfrage** (WB-410). Mit einem
    gewöhnlichen `JOIN` drehte SQLite die Reihenfolge um: es lief über den
    Index `ix_product_active` — also über alle zehntausend aktiven Produkte —
    und stellte je Zeile eine FTS-Anfrage. `search()` daneben lief andersherum
    und war tausendmal schneller, weil ein `ORDER BY` mit `bm25` den Planer
    zwingt, aus der FTS heraus zu lesen.

    Gemessen am echten Katalog (10.361 Produkte, 2026-08-30):

        „bio joghurt natur"   count 1.871 ms   search 1,3 ms   -> 43 Treffer
        „milch"                     448 ms          5,1 ms        657
        „passierte tomaten"         397 ms          0,6 ms          7
        die reine FTS-Abfrage         0 ms

        SEARCH p USING INDEX ix_product_active (active=?)   <- der Fehler
        SCAN f VIRTUAL TABLE INDEX 0:=M7

    `CROSS JOIN` ist in SQLite kein anderer Join, sondern die Anweisung an den
    Planer, die Reihenfolge NICHT zu vertauschen. Genau das ist hier gewollt:
    die FTS liefert dreiundvierzig Zeilen, `product` hat zehntausend, und
    welche der beiden zuerst gelesen wird, entscheidet über drei
    Grössenordnungen.
    """
    query = fts_query(begriff)
    if query is None:
        return 0
    platzhalter = ", ".join("?" for _ in AUSGESCHLOSSENE_KATEGORIEN)
    return int(con.execute(
        "SELECT count(*) AS n"
        "  FROM product_fts f CROSS JOIN product p ON p.id = f.rowid"
        " WHERE product_fts MATCH ? AND p.active = 1"
        f"   AND coalesce(p.category_l1, '') NOT IN ({platzhalter})",
        (query, *AUSGESCHLOSSENE_KATEGORIEN)).fetchone()["n"])


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


#: Alltagswörter, die der Katalog anders schreibt.
#:
#: „Klopapier" ist der Beleg, und er kam aus dem Video: der Satz „alles für
#: Lasagne, und Klopapier" liess das Klopapier als Freitext liegen — WEIL das
#: Wort in keinem Produktnamen vorkommt. Im Katalog stehen elf Packungen, alle
#: als „Toilettenpapier". Das ist keine Lücke im Sortiment, sondern eine
#: zwischen zwei Vokabularen.
#:
#: **Die Aufnahmeregel ist eng, und sie ist prüfbar:** ein Paar kommt nur
#: hinein, wenn das Alltagswort im Katalog NULL Treffer hat und das Ladenwort
#: welche. Alles andere wäre geraten — „Zahnpasta", „Sprudel" und „Pommes"
#: finden längst etwas und stehen deshalb NICHT hier, obwohl sie sich als
#: Synonyme anböten. `tests/test_alltagswort.py` prüft die Regel gegen den
#: echten Katalog, damit ein Eintrag auffällt, sobald er überflüssig wird.
#:
#: Geprüft am 2026-09-01 gegen 10.361 aktive Produkte.
ALLTAGSWORT = {
    "klopapier": "Toilettenpapier",
    "wc-papier": "Toilettenpapier",
    "spüli": "Spülmittel",
    "tempos": "Taschentücher",
    "kloreiniger": "WC-Reiniger",
}


def ladenwort(begriff: str) -> str | None:
    """Das Wort, unter dem der Laden führt, was der Satz anders nennt."""
    return ALLTAGSWORT.get(begriff.strip().casefold())


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
    Mit der Vorgabe (`plan.MAX_KANDIDATEN_MODELL` = 2 ×
    `plan.KANDIDATEN_MODELL`) passen die ersten beiden Begriffe immer
    vollständig hinein; gekürzt wird nur am allgemeinen Ende. Das ist auch das
    Ende, an dem das Modell entgleist — gemessen wurden als letzte Begriffe
    „Körnig", „Papikra", „Konzenzrat". Ein solcher Begriff findet irgendetwas,
    und was er findet, soll als Letztes stehen und als Erstes wegfallen.

    Gesucht wird trotzdem jeder Begriff: was die Kette gebracht hat, steht
    dadurch vollständig im RETRIEVER-Span, auch wenn die Obergrenze das Ende
    abschneidet.
    """
    gewaehlt: list[dict] = []
    gesehen: set[int] = set()
    for begriff in suchbegriffe:
        for platz, p in enumerate(search(con, begriff, limit=limit)):
            pid = int(p["id"])
            if pid in gesehen:
                # Schon über einen genaueren Begriff vorgelegt. Die Herkunft
                # bleibt beim ersten — er beschreibt die Zutat besser.
                continue
            gesehen.add(pid)
            if obergrenze is not None and len(gewaehlt) >= obergrenze:
                continue
            # `via_platz` ist der Platz INNERHALB der Trefferliste des eigenen
            # Begriffs — vor der Entdopplung, also unabhängig davon, was ein
            # früherer Begriff schon weggenommen hat. Nur damit lässt sich die
            # Liste hinterher auf eine kleinere Grenze kürzen und dasselbe
            # herausbekommen, als hätte man gleich mit ihr gesucht
            # (`kuerze_kette`, WB-359).
            gewaehlt.append({**p, "via": begriff, "via_platz": platz})
    if not gewaehlt:
        # Erst wenn die ganze Kette leer ausgeht. Vorher zu übersetzen wäre
        # falsch: solange irgendein Begriff trägt, ist die Kette des Modells
        # die genauere Auskunft, und ein Ladenwort daneben zöge nur breitere
        # Kandidaten herein.
        for begriff in suchbegriffe:
            laden = ladenwort(begriff)
            if not laden:
                continue
            for platz, p in enumerate(search(con, laden, limit=limit)):
                pid = int(p["id"])
                if pid in gesehen:
                    continue
                gesehen.add(pid)
                if obergrenze is not None and len(gewaehlt) >= obergrenze:
                    continue
                # `via` ist das LADENWORT, nicht das getippte: die Zeile soll
                # sagen, worüber das Produkt gefunden wurde. „Klopapier" stünde
                # dort sonst neben einem Produkt, in dem es nicht vorkommt.
                gewaehlt.append({**p, "via": laden, "via_platz": platz})
    return gewaehlt


def kuerze_kette(kandidaten: list[dict], *, limit: int,
                 obergrenze: int | None = None) -> list[dict]:
    """Kürzt eine bereits gesuchte Kettenvorlage auf eine kleinere Grenze.

    Seit WB-359 gibt es zwei Grenzen mit verschiedenen Zwecken: was AUFGEHOBEN
    und der Nutzerin gezeigt wird (`plan.KANDIDATEN_ANZEIGE`, gross — es
    kostet Datenbankzeilen) und was STUFE 3 im Prompt sieht
    (`plan.KANDIDATEN_MODELL`, klein — es kostet Token). Gesucht wird trotzdem
    **genau einmal**: die Modellvorlage entsteht hier aus der Anzeigeliste,
    statt dieselben Begriffe ein zweites Mal durch die Suche zu schicken.

    Damit ist die Modellvorlage eine **Teilmenge** der aufgehobenen Liste, und
    das ist die Zusicherung, auf der WB-359 steht: was das Modell zur Auswahl
    hatte, bekommt die Nutzerin beim „Nein" auch zu sehen — es kann gar
    nicht auseinanderlaufen, weil es dieselben Zeilen sind.

    Gekürzt wird nach `via_platz` — dem Platz eines Kandidaten in der
    Trefferliste SEINES Begriffs — und dann auf `obergrenze` je Zutat. Nicht
    nach „die ersten `limit` je Begriff, die übrig geblieben sind": das wäre
    grosszügiger als eine echte Suche mit `limit`, weil ein von einem
    genaueren Begriff schon vorgelegtes Produkt keinen Platz mehr verbrauchte.
    Gemessen im Rauchtest (Kette [Salzbutter, Butter], fünf Butter im Katalog)
    machte das aus fünf Kandidaten sechs — die Modellgrenze wäre stillschwei-
    gend gewachsen, obwohl sie ihren Wert behalten soll.

    **Ein Rest bleibt ehrlicherweise:** ein Produkt, das beim genauesten
    Begriff auf Platz 8 steht und beim zweiten auf Platz 2, kommt hier über
    den ERSTEN Begriff herein und fällt mit ihm weg — bei einer eigenen Suche
    mit `limit=5` hätte der zweite Begriff es gebracht. Im Einzelfall kann
    also ein Kandidat fehlen, den es vor WB-359 gegeben hätte; er steht dann
    in der AUFGEHOBENEN Liste, die die Nutzerin sieht. Die Reihenfolge nach
    Kette und Wortstufe bleibt unberührt.
    """
    gewaehlt: list[dict] = []
    for p in kandidaten:
        if obergrenze is not None and len(gewaehlt) >= obergrenze:
            break
        if p.get("via_platz", 0) >= limit:
            continue
        gewaehlt.append(p)
    return gewaehlt


def in_sorte(con: sqlite3.Connection, category_l1: str, category_l2: str, *,
             limit: int = 20) -> list[dict]:
    """Die Produkte EINER Sorte — aus dem Kategoriebaum, ohne Volltextsuche.

    Stufe 2 des Agenten, wenn die Nutzerin eine Sorte aus einer Auffächerung
    gewählt hat (WB-368). Der Rest des Wegs ist unverändert: die Kandidaten
    gehen durch `plan.choose`, werden aufgehoben (`chat_kandidat`) und stehen
    beim „Nein" als Alternativen da.

    **Warum hier nicht gesucht wird.** Der Sortenname kommt aus dem Katalog,
    und die Zahl daneben („Rohschinken & Bacon (58)") ist die Zahl der
    Produkte in genau dieser Kategorie. Wer darauf tippt, hat 58 Produkte
    angeboten bekommen; eine FTS-Abfrage auf denselben Namen fände etwas
    anderes und im Zweifel weniger — „Rohschinken & Bacon" wird zu
    `"rohschinken"* AND "bacon"*`, und ein Produkt, das nur eines von beiden
    im Namen trägt, fiele heraus. Die Zusage der Auffächerung wäre damit
    gebrochen, bevor der erste Kandidat dasteht.

    Sortiert wird nach der **Wortstufe des Sortennamens** (WB-339): ein
    Produkt, das ein Wort der Sorte im Namen trägt („Salami"), steht vor
    einem, das nur in ihrer Kategorie liegt. Anders als bei der Suche zählt
    hier das BESTE Wort und nicht das schwächste — „Rohschinken & Bacon" sind
    zwei Sorten in einem Namen, und ein Bacon soll nicht dafür bestraft
    werden, dass er kein Rohschinken ist.

    `rang` ist `None` und nicht 0.0: es gab keine bm25-Abfrage, und eine
    erfundene Null stünde später als Score im Trace und in
    `chat_kandidat.rank`, als hätte die Suche schlecht abgeschnitten.
    """
    spalten = ", ".join(_PRODUKT_SPALTEN)
    rows = con.execute(
        f"SELECT {spalten} FROM product"
        "  WHERE active = 1 AND category_l1 = ? AND category_l2 = ?",
        (category_l1, category_l2)).fetchall()
    suchworte = [w.casefold() for w in begriffe(category_l2)]

    treffer = []
    for r in rows:
        p = dict(r)
        p["wortstufe"] = max((wortstufe([w], p) for w in suchworte),
                             default=0)
        p["rang"] = None
        # `via` ist überall im Chat „der Begriff, der diesen Kandidaten
        # gebracht hat" — hier ist das die Sorte. Damit steht an der
        # Vorschlagszeile und an jeder Alternative, woher sie kam.
        p["via"] = category_l2
        treffer.append(p)
    treffer.sort(key=lambda p: (-p["wortstufe"], len(p["name"] or ""),
                                p["name"] or ""))
    # `via_platz` wie bei der Kette (WB-359): damit `kuerze_kette()` aus
    # dieser Anzeigeliste dieselbe, nur kürzere Modellvorlage schneiden kann.
    for platz, p in enumerate(treffer):
        p["via_platz"] = platz
    return treffer[:limit]
