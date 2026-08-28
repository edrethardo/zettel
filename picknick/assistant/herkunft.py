"""Welche Zutat steckt hinter einem Suchbegriff? (WB-369)

Die Suchbegriffe des Chat-Zugs entstehen aus einer bekannten Zutatenliste:
`plan.zutatenbegriffe` legt Chefkochs Zeilen vor („500 ml Tomaten, passierte")
und bekommt Begriffsketten zurück („passierte Tomaten", „Tomaten"). Bis WB-369
war danach nicht mehr zu sagen, aus WELCHER Zeile eine Kette entstanden ist —
und damit war die Menge weg, obwohl sie im selben Aufruf noch dastand.

**Diese Zuordnung braucht kein Modell.** Der Verdacht aus dem Ticket hat sich
bestätigt (dieselbe Sorte Befund wie in WB-368, wo eine vermeintliche
Modellaufgabe ein `GROUP BY` war): die Begriffe des Modells sind aus den
Zutatennamen gemacht, also stehen die Wörter noch darin. Gemessen an den drei
echten Chefkoch-Zügen der Datenbank (2026-08-28, Qwen3.8-27B, insgesamt 51
Zutaten und 38 Begriffe):

    Pho Bo            19 Begriffe -> 19 Zutaten zugeordnet, 0 falsch
    Käse-Lauch-Suppe   6 Begriffe ->  5 Zutaten zugeordnet, 0 falsch
    Ratatouille       13 Begriffe -> 13 Zutaten zugeordnet, 0 falsch

Die eine Lücke ist „Gemüsebrühwürfel" gegen den Begriff „Brühwürfel": das
Grundwort steht am ENDE des Kompositums, und diese Richtung erkennt der
Wortvergleich absichtlich nicht (siehe `_wort_punkte`). Gemessen wurde
ausserdem mit je EINEM Begriff je Zutat — im Betrieb liegt die ganze Kette
vor, und dort steht „Gemüsebrühwürfel" als genauestes Glied mit dabei.

**Wo die Zuordnung nicht sicher ist, gibt es keine Menge.** Kein Umbiegen auf
den ähnlichsten Namen, keine Zuordnung nach blosser Reihenfolge: eine falsche
Menge im Korb ist schlimmer als gar keine, weil sie aussieht wie eine
gerechnete. Dieselbe Zusicherung wie bei den Produkt-IDs in `plan.choose` —
nur dass hier nichts vom Modell kommt, was zu prüfen wäre.

Was die Reihenfolge NICHT leistet und deshalb nicht benutzt wird: das Modell
lässt Vorratszutaten weg, zieht dieselbe Zutat zu einer Zeile zusammen und
nennt Synonyme („Möhren" -> „Karotten"). Nach Position zugeordnet würde
spätestens ab der ersten weggelassenen Zeile alles um eins verrutschen — und
zwar unbemerkt, weil das Ergebnis weiter plausibel aussieht.
"""
from __future__ import annotations

from picknick import db, mengen

#: Ab dieser Länge zählt ein Wortanfang als Treffer. Kürzer wäre das
#: Präfix-Kürzen, das `chefkoch.zutat_kette` aus gutem Grund nicht macht:
#: „Ei" träfe „Eisberg", „Öl" träfe „Oliven".
MIN_WORT = 4

#: Punkte für einen Wortanfang statt eines ganzen Wortes. Bewusst kleiner als
#: 1, damit „gelbe Paprika" die gelbe Paprikaschote gewinnt und „Paprika" die
#: rote — der gemessene Fall aus dem Ratatouille-Zug.
PRAEFIX = 0.6

#: Darunter gilt eine Zutat als nicht zugeordnet. Genau ein Wortanfang reicht;
#: ein Begriff, von dem kein Wort passt, hat ohnehin null Punkte.
SCHWELLE = PRAEFIX


def _woerter(text) -> list[str]:
    """Vergleichsform: umlautfrei, klein, am Bindestrich getrennt.

    Dieselbe Faltung wie die Suche (`db.normalisiere`) — „Käse" und „Kaese"
    sind dasselbe Wort, sonst hinge die Zuordnung an der Schreibweise der
    Rezeptseite.
    """
    roh = db.normalisiere((text or "").casefold())
    return [w for w in roh.replace("-", " ").replace("/", " ").split() if w]


def _wort_punkte(begriffswort: str, zutatenwort: str) -> float:
    """Zwei Wörter vergleichen: 1.0 gleich, `PRAEFIX` am Wortanfang, sonst 0.

    **Nur der Wortanfang, nicht das Wortende.** „Zwiebel" trifft damit
    „Zwiebeln" (Mehrzahl) und nicht „Frühlingszwiebel" — und das ist die
    wichtigere Hälfte: eine Frühlingszwiebel ist keine Zwiebel, und die Menge
    der einen an der anderen wäre ein Fehler, den niemand mehr sieht.
    """
    if begriffswort == zutatenwort:
        return 1.0
    if len(begriffswort) >= MIN_WORT and zutatenwort.startswith(begriffswort):
        return PRAEFIX
    if len(zutatenwort) >= MIN_WORT and begriffswort.startswith(zutatenwort):
        return PRAEFIX
    return 0.0


def punkte(begriff: str, zutat: str) -> float:
    """Wie gut passt ein Suchbegriff zu einem Zutatennamen? 0.0 heisst „gar nicht".

    **Jedes Wort des Begriffs muss vorkommen.** „gelbe Paprika" passt deshalb
    nicht zur roten Paprikaschote, obwohl „Paprika" es täte: ein Begriff, von
    dem die Hälfte fehlt, benennt etwas anderes. Umgekehrt darf die Zutat mehr
    Wörter haben als der Begriff — „Tomaten" passt zu „Tomaten, passierte",
    und genau so entstehen die Begriffe ja.
    """
    bw, zw = _woerter(begriff), _woerter(zutat)
    if not bw or not zw:
        return 0.0
    summe = 0.0
    for wort in bw:
        bestes = max((_wort_punkte(wort, z) for z in zw), default=0.0)
        if bestes == 0.0:
            return 0.0
        summe += bestes
    return summe / len(bw)


def _kette_punkte(kette: list[str], zutat: str) -> float:
    """Die beste Übereinstimmung über die ganze Begriffskette.

    Gewertet wird der beste Begriff und nicht der erste: die Kette geht vom
    genauesten zum allgemeinsten, und welches Glied den Namen der Zutat trifft,
    ist nicht vorherzusagen („Knoblauchzehe(n)" trifft das zweite Glied
    „Knoblauch", „passierte Tomaten" das erste).
    """
    return max((punkte(b, zutat) for b in kette), default=0.0)


def _namen(zutat: dict) -> list[str]:
    """Die Namen, unter denen eine Zutat wiedererkannt werden darf.

    Chefkochs Rohform („Tomaten, passierte") und die gedrehte Form
    („passierte Tomaten"), die `chefkoch.parse_zutaten` schon als `name`
    danebenlegt. Beide, weil das Modell mal die eine und mal die andere
    Schreibweise aufgreift.
    """
    return [n for n in ((zutat.get("name") or ""),
                        (zutat.get("raw_name") or "")) if n]


def _bester_treffer(zutat: dict, ketten: list[list[str]]) -> int | None:
    """Zu welcher Begriffskette gehört diese Zutat? `None` heisst „unklar".

    Zugeordnet wird von der ZUTAT aus und nicht vom Begriff aus, und das ist
    der Grund, warum „Zwiebel(n)" zweimal im Pho-Rezept steht und trotzdem
    genau eine Zeile auf dem Zettel ergibt: beide Zutaten zeigen auf dieselbe
    Kette, und ihre Mengen werden dort zusammengezählt.

    Ein Gleichstand zwischen zwei Ketten ergibt `None`. Raten wäre hier
    besonders teuer: die Menge landete an der falschen Zeile und sähe dort
    aus wie eine gerechnete.
    """
    werte = [max(_kette_punkte(kette, name) for name in _namen(zutat))
             for kette in ketten] if _namen(zutat) else []
    if not werte:
        return None
    bestes = max(werte)
    if bestes < SCHWELLE:
        return None
    if werte.count(bestes) > 1:
        return None
    return werte.index(bestes)


def zuordnen(zutaten: list[dict], begriffe: list[dict]) -> list[dict]:
    """Hängt jedem Begriff seine Herkunftszutat samt Menge an (WB-369).

    Aus `[{"suchbegriffe": [...], "menge": 1}]` wird dieselbe Liste mit drei
    Feldern mehr:

    * `bedarf` / `einheit` — die benötigte Menge, wie Chefkoch sie schreibt,
      über alle zugeordneten Zutaten zusammengezählt. `None`, wenn es keine
      gibt oder keine sichere Zuordnung.
    * `zutat` — der Name der Herkunftszutat (mehrere durch Komma), damit im
      Trace und in der Meldung steht, WORAUS die Menge stammt.
    * `grund` — warum keine Menge dasteht, wenn eine hätte dastehen können.

    `menge` (die geratene Packungszahl des Modells) bleibt unangetastet: sie
    ist die Rückfallebene für alles ohne Mengenangabe, und was der Aufrufer
    damit macht, entscheidet er (siehe `chat._zeilen`).

    Zusammengezählt wird mit `mengen.summiere` und nicht mit `+`: „4 Zehen"
    und „200 g" ergeben keine Summe, und aus zweien, die nicht zusammenpassen,
    wird lieber gar keine Menge als eine falsche.
    """
    angereichert = [{**b, "bedarf": None, "einheit": None, "zutat": None,
                     "grund": None} for b in begriffe]
    if not angereichert:
        return angereichert
    ketten = [b.get("suchbegriffe") or [] for b in begriffe]
    treffer: dict[int, list[dict]] = {}
    for zutat in zutaten or []:
        i = _bester_treffer(zutat, ketten)
        if i is not None:
            treffer.setdefault(i, []).append(zutat)

    for i, eintrag in enumerate(angereichert):
        zugeordnet = treffer.get(i) or []
        if not zugeordnet:
            eintrag["grund"] = "keine Zutat des Rezepts zuzuordnen"
            continue
        eintrag["zutat"] = ", ".join(
            dict.fromkeys(z.get("raw_name") or z.get("name") or ""
                          for z in zugeordnet)).strip(", ")
        summe = None
        gescheitert = False
        for z in zugeordnet:
            teil = _menge(z)
            if teil is None:
                continue
            # Auch der erste Beitrag geht durch `summiere`: die Funktion
            # rechnet dabei in die Grundeinheit („1 kg" -> 1000 g), und eine
            # Menge, die mal in kg und mal in g an der Zeile stünde, wäre im
            # Trace nicht mehr vergleichbar.
            summe = mengen.summiere(*(summe or (None, None)), teil[0], teil[1])
            if summe is None:
                gescheitert = True
                break
        if gescheitert:
            eintrag["grund"] = ("die Mengen derselben Zutat lassen sich nicht "
                                "zusammenzählen")
        elif summe is None:
            eintrag["grund"] = "das Rezept nennt dazu keine Menge"
        else:
            eintrag["bedarf"], eintrag["einheit"] = summe
    return angereichert


def _menge(zutat: dict) -> tuple[float, str | None] | None:
    """Die Menge einer Zutat als `(Zahl, Einheit)` — oder `None`.

    **Chefkochs Null ist keine Menge.** Bei „Salz und Pfeffer n. B." oder
    „Fischsauce, ca. 5 - 8 EL" steht `amount: 0.0` in der API-Antwort; das
    heisst „nach Belieben" und nicht „null Gramm". Eine 0 durchzureichen
    ergäbe einen Bedarf von null, und `mengen.rechne` machte daraus
    aufgerundet eine Packung — gekauft, weil nichts gebraucht wird.
    """
    try:
        zahl = float(zutat.get("amount"))
    except (TypeError, ValueError):
        return None
    if zahl <= 0:
        return None
    return (zahl, zutat.get("unit"))
