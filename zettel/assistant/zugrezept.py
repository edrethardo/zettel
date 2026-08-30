"""Das Rezept, das an einem Chat-Zug hängt — und die Zahl, die entscheidet
(WB-383).

Bis zu diesem Ticket sagte der Chat einen SATZ ÜBER das Rezept:

    „Käse-Lauch-Suppe mit Hackfleisch" von Chefkoch — 12 Zutaten im Rezept,
    10 davon auf dem Zettel, 8 im Katalog gefunden.

Das Rezept selbst stand nirgends. Wer wissen wollte, was er da einkauft,
musste nach `/rezepte` wechseln — und dort steht es nur, wenn es gespeichert
wurde.

**Der Zweck ist eine Entscheidung, kein Nachschlagen.** „Damit man weiß worauf
man sich einlassen würde." Das bestimmt, was oben steht. Gemessen an den
echten Rezepten dieses Shops:

    Pho Bo                             6 Port.  4.84   90 + 480 min   23 Zut.
    Käse-Lauch-Suppe mit Hackfleisch   4 Port.  4.76    15 +  20 min   12 Zut.
    Amaretto-Mousse-Cheesecake        16 Port.  4.35    45 + 300 min   12 Zut.

Pho Bo sind neuneinhalb Stunden, die Käse-Lauch-Suppe fünfunddreissig
Minuten. Das ist der Unterschied zwischen „heute Abend" und „nächstes
Wochenende", und der Chat sagte dazu kein Wort: er nannte Zutatenzahl und
Bewertung, also genau die beiden Zahlen, die am wenigsten entscheiden.

Deshalb liefert dieses Modul die GESAMTZEIT als eine Zahl und nicht drei
Felder. „prep 90, cook 480" ist eine Datenbankzeile; „9½ Stunden — davon
8 Stunden Ruhezeit" ist die Auskunft. Die Ruhezeit steht dabei sichtbar
daneben, weil sie der Grund ist, warum aus 90 Minuten neuneinhalb Stunden
werden.

**Woher der Bezug kommt.** `chat_rezept` verknüpft die Antwortzeile mit dem
Rezept — geschrieben auf dem Quellenweg (das eben geholte Chefkoch-Rezept)
UND auf dem Rezeptweg (das gespeicherte, das der Satz getroffen hat). Der
Entwurf (`chat_entwurf.recipe_id`) reicht dafür nicht: den gibt es nur auf dem
Quellenweg, und dann sähe der schnelle Weg ärmer aus als der langsame.

**Nichts wird erfunden.** Fehlt eine Zeit, eine Bewertung oder eine
Zubereitung, steht dort nichts — kein Platzhalter, keine geschätzte Dauer.
Der Modellweg hat gar kein Rezept und bekommt deshalb auch keine Karte.

**Und seit WB-387 steht daneben, was die Karte NICHT geworden ist.** Chefkoch
liefert zwölf Rezepte je Suche; eines wurde vorgeschlagen, elf verschwanden.
Gemessen an „Lasagne" sind das keine Varianten desselben Gerichts, sondern
verschiedene Gerichte — und die Gewichtung wählt die vegetarische
Spinatlasagne (4,837 gegen 4,695 für die klassische). Sie ist nicht kaputt;
„am besten bewertet" ist nur nicht „was ich gemeint habe". `alternativen()`
stellt sie zur Wahl, mit denselben Zahlen wie die Karte darüber und ohne eine
einzige zusätzliche Anfrage.

**Und seit WB-384 steht die Portionszahl nicht nur da, sie ist einstellbar.**
Bis dahin bekam jeder, der „alles für Lasagne" schrieb, Chefkochs Portionszahl
und konnte sie nicht ändern — die Mengen aus WB-369 wurden für eine
Personenzahl gerechnet, die niemand gewählt hatte. Die Vorgabe kommt weiter
aus dem Rezept, die Entscheidung aus `chat_rezept.portionen`, und das Rezept
selbst bleibt unberührt (dieselbe Trennung wie in `recipes.in_den_korb`).
"""
from __future__ import annotations

import sqlite3

from zettel import mengen
from zettel.gerichte import chefkoch, speicher

#: Ab hier wird aus Minuten eine Stundenangabe. Darunter ist die Minutenzahl
#: die kürzere und die genauere Auskunft („35 Minuten"), darüber liest sich
#: „570 Minuten" wie ein Messwert und nicht wie eine Antwort.
STUNDE = 60


class ZugrezeptFehler(RuntimeError):
    """An der Rezeptkarte eines Zugs stimmt etwas nicht."""


def merken(con: sqlite3.Connection, chat_message_id: int,
           recipe_ids, dish_ids=None) -> None:
    """Hält fest, welche Rezepte dieser Zug vorgeschlagen hat.

    `INSERT OR IGNORE`: derselbe Zug wird nicht zweimal geschrieben, und ein
    Satz, der dasselbe Rezept zweimal nennt, ergibt eine Zeile.

    Ein Rezept, das zwischen dem Vorschlagen und hier verschwunden ist, kostet
    seine Zeile und nicht den Zug — der Fremdschlüssel würde sonst den ganzen
    `_schreiben`-Durchgang mitreissen, und der trägt die Bestellung.

    `dish_ids` ist die GLEICH LANGE Liste der Gerichte, für die diese Rezepte
    standen (WB-387) — auf dem Quellenweg gefüllt, auf dem Rezeptweg nicht.
    An ihr hängen die Alternativen: die übrigen Treffer derselben Suche
    stehen am Gericht, und ohne diesen Verweis fände die Karte sie nicht
    wieder. Fehlt sie oder ist sie kürzer, bleibt die Spalte `NULL` — das ist
    kein Sonderfall, sondern der Rezeptweg.
    """
    gerichte_ids = list(dish_ids or [])
    for pos, recipe_id in enumerate(recipe_ids or []):
        if not recipe_id:
            continue
        dish_id = (gerichte_ids[pos] if pos < len(gerichte_ids) else None)
        try:
            con.execute(
                "INSERT OR IGNORE INTO chat_rezept"
                " (chat_message_id, recipe_id, pos, dish_id)"
                " VALUES (?, ?, ?, ?)",
                (chat_message_id, int(recipe_id), pos,
                 int(dish_id) if dish_id else None))
        except sqlite3.IntegrityError:
            continue
    con.commit()


def zum_zug(con: sqlite3.Connection, chat_message_id: int,
            vorgeschlagen: list[dict] | None = None) -> list[dict]:
    """Die Rezeptkarten einer Antwortzeile — meist eine, oft keine.

    `vorgeschlagen` ist die bereits geholte Vorschlagsliste dieses Zugs.
    Dieselbe Übergabe wie beim Entwurf (`entwurf.zu_nachricht`) und aus
    demselben Grund: die Oberfläche hat die Liste ohnehin, und sie ein zweites
    Mal zu holen wäre je Zug eine weitere Abfrage.

    **Die Zubereitung ist NICHT dabei, nur ihre Schrittzahl.** Pho Bos
    Zubereitung sind 3.924 Zeichen; drei Rezeptzüge im Verlauf hätten die
    Ersparnis aus WB-372 wieder aufgefressen. Sie wird beim Aufklappen
    nachgeladen (`/rezepte/<id>/zubereitung`) — die ZEIT dagegen steht immer
    da, denn sie ist der Grund, warum jemand „nein" sagt.
    """
    rows = con.execute(
        f"SELECT {_FELDER}, z.dish_id, z.portionen AS _gewaehlt,"
        "       d.query AS gericht"
        "  FROM chat_rezept z JOIN recipe r ON r.id = z.recipe_id"
        "  LEFT JOIN dish d ON d.id = z.dish_id"
        " WHERE z.chat_message_id = ? ORDER BY z.pos, z.recipe_id",
        (chat_message_id,)).fetchall()
    karten = []
    for row in rows:
        k = _karte(con, row)
        if not _hat_inhalt(k):
            # Ein selbst angelegtes Rezept hat weder Zeiten noch Bewertung,
            # Zutatenliste oder Zubereitung — nur Produkte, und die stehen
            # gleich darunter als Vorschläge. Eine Karte mit einer einzigen
            # Portionszahl darin wäre Rahmen um nichts.
            continue
        karten.append(k)
    _deckung(karten, vorgeschlagen)
    return karten


#: Die Felder einer Rezeptkarte, wie sie aus `recipe` kommen. Eine Zeichenkette
#: und zwei Abfragen, damit die Karte des Zugs und die Karte der frischen Wahl
#: (`zur_wahl`, WB-402) dieselbe bleiben — zwei Kopien liefen auseinander, und
#: dann zeigte die Vorschau etwas anderes als der Zug eine halbe Minute später.
_FELDER = (
    "r.id, r.name, r.servings, r.prep_minutes, r.cook_minutes,"
    " r.rest_minutes, r.difficulty, r.source, r.source_id,"
    " r.source_url, r.source_title, r.source_rating, r.source_votes,"
    " (r.instructions IS NOT NULL) AS hat_zubereitung,"
    " r.instructions AS _text"
)


def _karte(con: sqlite3.Connection, row) -> dict:
    """Aus einer Rezeptzeile die Karte, die die Vorlage zeigt."""
    k = dict(row)
    text = k.pop("_text", None)
    k["n_schritte"] = len(chefkoch.schritte(text)) if text else 0
    k["zutaten"] = speicher.zutaten(con, int(k["id"]))
    k["n_zutaten"] = len(k["zutaten"])
    k["gesamt_minuten"] = gesamtzeit(k)
    k["zeitsatz"] = zeitsatz(k["gesamt_minuten"])
    k["ruhesatz"] = (zeitsatz(k["rest_minutes"])
                     if k["rest_minutes"] else None)
    k["n_zettel"] = k["n_ohne_produkt"] = k["n_fehlt"] = None
    k["alternativen"] = alternativen(con, k)
    _portionen_an(k)
    return k


def zur_wahl(con: sqlite3.Connection, dish_id: int) -> dict | None:
    """Die Karte des Rezepts, auf das ein Gericht GERADE zeigt (WB-402).

    **Der Grund, warum es sie gibt: sie kostet kein Modell.** Wer im Chat
    eine Alternative antippt, wartete bis zu diesem Ticket
    vierundzwanzig Sekunden auf einen kompletten Zug — zwei Modellstufen —,
    bevor überhaupt zu sehen war, dass seine Wahl angekommen ist. Alles, was
    die Karte zeigt (Name, Zeiten, Zutatenliste der Quelle, Bewertung,
    Herkunft), steht zu diesem Zeitpunkt längst in `recipe` und
    `recipe_ingredient`; nur die Vorschlagsliste darunter braucht das Modell.

    `None`, wenn das Gericht auf kein Rezept zeigt. Es ist DIESELBE Karte wie
    die des Zugs, nur ohne dessen zwei Zahlen: die Deckung („8 davon nicht auf
    dem Zettel") und die gewählte Portionszahl hängen an einer
    Vorschlagsliste, die es noch nicht gibt. Sie bleiben leer statt geraten —
    eine halbe Minute später stehen sie da.
    """
    row = con.execute(
        f"SELECT {_FELDER}, d.id AS dish_id, NULL AS _gewaehlt,"
        "       d.query AS gericht"
        "  FROM dish d JOIN recipe r ON r.id = d.recipe_id"
        " WHERE d.id = ?", (dish_id,)).fetchone()
    if row is None:
        return None
    k = _karte(con, row)
    return k if _hat_inhalt(k) else None


def alternativen(con: sqlite3.Connection, karte: dict) -> list[dict]:
    """Die anderen Rezepte, die dieselbe Suche geliefert hat (WB-387).

    **Keine neue Suche und kein Netz** — dieselbe Zusage wie bei den
    Alternativen einer verworfenen Zeile (WB-359): sie stehen seit dem Abruf
    in `dish_treffer`. Chefkoch liefert zwölf Rezepte in einer Antwort;
    `chefkoch.bestes()` nahm eines, und die übrigen elf verschwanden, bevor
    jemand sie sehen konnte.

    Leer, wo es nichts zu wählen gibt: der Rezeptweg hat kein Gericht, und
    ein Zug aus der Zeit vor diesem Ticket hat keine mitgeschriebene
    Trefferliste. Leer auch dann, wenn nur EIN Treffer übrig ist — eine Wahl
    zwischen einer Sache ist keine.

    Jeder Eintrag trägt die Zahlen, die die Wahl tragen, und **nur die, die
    es wirklich gibt**:

    * `zeitsatz` — die Gesamtzeit, wo das Rezept schon geholt wurde; sonst
      die ARBEITSZEIT aus der Suchantwort (`arbeitszeit = True`). Chefkochs
      `preparationTime` ist nicht die Gesamtzeit: Pho Bo steht dort mit 90
      Minuten und braucht 9½ Stunden. Beides „Zeit" zu nennen wäre die
      bequemere und falsche Auskunft.
    * `n_zutaten` — nur, wo die Zutatenliste vorliegt. Die Suchantwort trägt
      sie nicht, und sie für elf Alternativen nachzuholen wären elf weitere
      Anfragen an eine fremde Seite.
    * `rating`/`votes` — immer, denn danach wurde vorausgewählt.
    """
    if not karte.get("dish_id"):
        return []
    liste = speicher.treffer(con, int(karte["dish_id"]))
    if len(liste) < 2:
        return []
    gewaehlt = str(karte.get("source_id") or "")
    fertig = []
    for t in liste:
        gesamt = gesamtzeit({"prep_minutes": t["r_prep"],
                              "cook_minutes": t["r_cook"],
                              "rest_minutes": t["r_rest"]})
        fertig.append({
            "source_id": t["rezept_id"],
            "titel": t["titel"],
            "rating": t["rating"],
            "votes": t["votes"] or 0,
            "n_zutaten": t["n_zutaten"] or None,
            "zeitsatz": zeitsatz(gesamt or t["prep_minutes"]),
            # Sagt der Oberfläche, welche der beiden Zeiten sie da vor sich
            # hat. Ohne diese Unterscheidung stünde „25 Minuten" neben einem
            # Rezept, das vier Stunden im Ofen steht.
            "arbeitszeit": gesamt is None,
            # Das vorausgewählte steht MIT in der Liste, markiert. Sonst
            # wäre nicht zu sehen, wogegen man wählt — und der Weg zurück
            # gäbe es auch nicht.
            "gewaehlt": t["rezept_id"] == gewaehlt,
        })
    return fertig


def _portionen_an(karte: dict) -> None:
    """Die Portionszahl an die Karte — und die Mengen, die daran hängen.

    **Vier Felder, und jedes beantwortet eine andere Frage** (WB-384):

        portionen_rezept   wofür die QUELLE rechnet (`recipe.servings`)
        portionen          wofür DIESER ZUG rechnet — das Feld an der Karte
        faktor             das eine, was daraus folgt
        umgerechnet        ob überhaupt etwas anderes dasteht als die Quelle

    Der Faktor kommt aus `mengen.faktor` und nirgendwo sonst: die Reihenfolge
    aus WB-362 (skalieren, zusammenzählen, aufrunden) gilt unverändert, und
    eine zweite Multiplikation an dieser Stelle wäre der Anfang einer zweiten
    Mengenrechnung.

    **Die Zutatenliste wird nur zum ANZEIGEN hochgerechnet, nicht
    gespeichert.** `recipe_ingredient` ist die Liste der Quelle — fremde
    Arbeit, verlinkt und zitiert —, und sie gehört nicht diesem Zug. Was
    dieser Zug ändern darf, ist seine eigene Vorschlagsliste
    (`chat_suggestion.need_amount`, siehe `portionen_setzen`).

    Fehlt `servings`, gibt es keinen Faktor und die Mengen bleiben, wie sie
    sind (Regel 4 des Tickets). Geraten wird nichts — die Karte sagt es dann
    selbst, statt eine 4 hinzuschreiben, die niemand behauptet hat.
    """
    basis = karte.get("servings")
    gewaehlt = karte.pop("_gewaehlt", None)
    karte["portionen_rezept"] = basis
    karte["portionen"] = gewaehlt or basis
    karte["faktor"] = mengen.faktor(basis, karte["portionen"])
    karte["umgerechnet"] = bool(basis and karte["portionen"] != basis)
    for z in karte["zutaten"]:
        z["gerechnet"] = mengen.skaliere(z.get("amount"), basis,
                                         karte["portionen"])


def zeilen_zur_karte(karte: dict, zeilen, n_karten: int = 1) -> list[dict]:
    """Welche Vorschlagszeilen zu dieser Rezeptkarte gehören.

    **Eine Regel für zwei Fragen**: wie viele Zutaten auf dem Zettel stehen
    (`_deckung`, WB-383) und welche Mengen eine geänderte Portionszahl
    betrifft (`portionen_setzen`, WB-384). Zwei Kopien liefen auseinander,
    und dann zählte die Karte etwas anderes, als sie umrechnet.

    Zwei Wege führen zur Zuordnung, und beide stehen schon in den Daten:

    * **Quellenweg** — die Zeilen des Gerichts tragen `dish_item` (WB-337).
    * **Rezeptweg** — `_aus_rezept` schreibt den REZEPTNAMEN als
      `search_term` an jede Zeile. Dort gibt es keinen Entwurf, und ohne
      diesen Vergleich bliebe genau der schnelle Weg ohne Zuordnung.

    Bei mehreren Rezepten in EINEM Zug lässt sich der Quellenweg nicht
    aufteilen (der Entwurf kennt nur ein Gericht) — dann gilt der Rezeptname.

    Eine korrigierte Zeile fällt heraus, die Korrektur bleibt drin (WB-359):
    beide meinen dieselbe Zutat, und sie doppelt zu zählen wäre ebenso falsch
    wie sie doppelt umzurechnen.
    """
    zeilen = list(zeilen or [])
    if not zeilen:
        return []
    zum_gericht = [z for z in zeilen if z.get("zum_gericht")]
    name = (karte.get("name") or "").casefold()
    if zum_gericht and n_karten == 1:
        # Der Entwurf ist das genauere Signal, wo es ihn gibt — er steht an
        # der ZEILE und nicht an einem Namensvergleich.
        eigene = zum_gericht
    else:
        eigene = [z for z in zeilen
                  if (z.get("search_term") or "").casefold() == name]
    korrekturen = {z["corrected_from"] for z in zeilen
                   if z.get("ist_korrektur")}
    return [z for z in eigene if z["id"] not in korrekturen]


def portionen_setzen(con: sqlite3.Connection, chat_message_id: int,
                     recipe_id: int, portionen, zeilen) -> dict:
    """Rechnet die Mengen eines Chat-Zugs auf eine andere Portionszahl um.

    **Das Feld, das WB-369 offengelassen hat.** Dort wurde bewusst nicht aus
    dem Satz geraten — „in 35 echten Nutzersätzen kommt keine einzige Ziffer
    vor" —, und das war richtig. Ein Feld ist kein Raten: hier steht die Zahl,
    die ein Mensch getippt hat.

    Drei Dinge, die dieser Funktion ihre Form geben:

    1. **Gerechnet wird mit `mengen.skaliere` und sonst nirgends.** Es bleibt
       bei der einen Mengenrechnung aus WB-362; hier steht nur der erste ihrer
       drei Schritte. Zusammengezählt wird weiterhin in `korb.einlegen`, und
       aufgerundet erst danach.
    2. **Was schon im Korb liegt, bleibt liegen** (`im_korb`, WB-361 und
       WB-387). Die Korbzeile ist beim „Ja" entstanden und kann längst eine
       sein, die die Nutzerin selbst aufgestockt hat — sie nachzurechnen
       hiesse, fremde Mengen zu überschreiben. Die neue Zahl wirkt auf das
       Nächste, und der Bericht sagt es.
    3. **Umgerechnet wird von der zuletzt gewählten Zahl**, nicht von der des
       Rezepts: nach „für 8" und dann „für 6" stünde sonst das Doppelte des
       Rezepts mal drei Viertel im Korb. Die Zahl steht in `chat_rezept`,
       eben damit es die zuletzt gewählte gibt.

    Ohne Portionszahl am Rezept gibt es keinen Faktor (Regel 4): dann wird
    nichts geändert und `grund` sagt, warum. Dasselbe bei einer Zahl, die
    keine ist — getippt wird auf einem Telefon, und ein Vertipper darf keine
    Mengen verstellen.
    """
    row = con.execute(
        "SELECT z.portionen, r.servings, r.name"
        "  FROM chat_rezept z JOIN recipe r ON r.id = z.recipe_id"
        " WHERE z.chat_message_id = ? AND z.recipe_id = ?",
        (chat_message_id, recipe_id)).fetchone()
    if row is None:
        raise ZugrezeptFehler("Zu diesem Zug steht dieses Rezept nicht "
                              "(mehr) da.")
    basis = row["servings"]
    vorher = row["portionen"] or basis
    gewuenscht = _zahl(portionen)
    if gewuenscht is None:
        grund = "Das ist keine Portionszahl."
        if vorher:
            grund = f"Das ist keine Portionszahl — es bleibt bei {vorher}."
        return {"name": row["name"], "geaendert": 0, "im_korb": 0,
                "von": vorher, "auf": vorher, "grund": grund}
    if not basis:
        return {"name": row["name"], "geaendert": 0, "im_korb": 0,
                "von": None, "auf": None,
                "grund": "Am Rezept steht keine Portionszahl — dann gibt es "
                         "keinen Faktor, und die Mengen bleiben, wie sie "
                         "sind."}
    con.execute("UPDATE chat_rezept SET portionen = ?"
                " WHERE chat_message_id = ? AND recipe_id = ?",
                (gewuenscht, chat_message_id, recipe_id))
    geaendert = im_korb = 0
    for z in zeilen:
        if z.get("need_amount") is None:
            continue
        if z.get("im_korb"):
            # Nicht rückwirkend: was bestätigt wurde, gehört dem Korb.
            im_korb += 1
            continue
        con.execute("UPDATE chat_suggestion SET need_amount = ? WHERE id = ?",
                    (mengen.skaliere(z["need_amount"], vorher, gewuenscht),
                     z["id"]))
        geaendert += 1
    con.commit()
    return {"name": row["name"], "geaendert": geaendert, "im_korb": im_korb,
            "von": vorher, "auf": gewuenscht, "grund": None}


def gewaehlte_portionen(con: sqlite3.Connection, chat_message_id: int):
    """Für wie viele Portionen dieser Zug rechnet — oder `None`.

    Die gewählte Zahl, sonst die des Rezepts, sonst nichts. Sie geht beim
    „Ja" an `korb.einlegen(portionen=…)` und landet als `zettel.servings`
    im Trace — dasselbe Attribut, das der Rezeptweg seit WB-362 setzt.

    **Bis WB-384 blieb es auf dem Chat-Weg leer**, und das war richtig: es gab
    dort keine Portionszahl, die jemand gewählt hätte, und aus dem Satz zu
    raten wäre falsch gewesen (WB-369, Regel 5). Jetzt gibt es das Feld, und
    die Zahl ist keine Vermutung mehr.

    Der erste Zug seiner Karten und nicht die Summe: Züge mit mehreren
    Rezepten sind der Ausnahmefall, und eine erfundene gemeinsame Zahl wäre
    schlechter als die des ersten.
    """
    row = con.execute(
        "SELECT z.portionen, r.servings FROM chat_rezept z"
        "  JOIN recipe r ON r.id = z.recipe_id"
        " WHERE z.chat_message_id = ? ORDER BY z.pos, z.recipe_id LIMIT 1",
        (chat_message_id,)).fetchone()
    if row is None:
        return None
    return row["portionen"] or row["servings"]


def _zahl(wert):
    """`'8'` -> 8, leer oder Unsinn -> `None`, alles unter 1 auch.

    Dieselbe Nachsicht wie `recipes.uebernahme._portionen`: die Zahl kommt
    aus einem Formularfeld auf einem Telefon. Der Unterschied ist der
    Rückfall — dort auf die Zahl des Rezepts, hier auf gar nichts, weil hier
    nichts gerechnet werden MUSS.
    """
    try:
        zahl = int(str(wert).strip())
    except (TypeError, ValueError):
        return None
    return zahl if zahl > 0 else None


def _hat_inhalt(k: dict) -> bool:
    """Trägt diese Karte irgendetwas, das nicht schon in der Liste steht?"""
    return bool(k["gesamt_minuten"] or k["source_rating"] or k["n_zutaten"]
                or k["n_schritte"] or k["source_url"])


def gesamtzeit(k: dict) -> int | None:
    """Vorbereitung + Kochen + Ruhen. `None`, wenn keine Zeit dasteht.

    Öffentlich seit WB-400 Runde 4: die Rezeptseite zeigt dieselbe Leitzahl
    wie die Karte im Chat, und eine zweite Summe an anderer Stelle wäre der
    Anfang einer zweiten Zeitrechnung.

    Eine fehlende Angabe ist nicht null Minuten: ein selbst angelegtes Rezept
    hat gar keine Zeiten, und „0 Minuten" wäre eine Behauptung, die niemand
    aufgestellt hat. Stehen aber zwei von drei Feldern, ist ihre Summe die
    beste Auskunft, die es gibt — sie ist eher zu klein als falsch.
    """
    teile = [k.get("prep_minutes"), k.get("cook_minutes"),
             k.get("rest_minutes")]
    vorhanden = [int(t) for t in teile if t]
    return sum(vorhanden) if vorhanden else None


def zeitsatz(minuten) -> str | None:
    """Minuten -> „35 Minuten", „1 Stunde 15 Minuten", „9½ Stunden".

    Die halbe Stunde bekommt ihr Zeichen, weil „9 Stunden 30 Minuten" beim
    Überfliegen länger dauert als die Zahl, die es ausdrückt. Alles andere
    wird ausgeschrieben statt gerundet: eine gerundete Kochzeit wäre bequemer
    zu lesen und in der Küche falsch.
    """
    if not minuten:
        return None
    minuten = int(minuten)
    if minuten < STUNDE:
        return f"{minuten} Minuten"
    stunden, rest = divmod(minuten, STUNDE)
    wort = "Stunde" if stunden == 1 else "Stunden"
    if rest == 0:
        return f"{stunden} {wort}"
    if rest == 30:
        # Immer Plural: „1½ Stunde" gibt es nicht.
        return f"{stunden}½ Stunden"
    return f"{stunden} {wort} {rest} Minuten"


def _deckung(karten: list[dict], vorgeschlagen: list[dict] | None) -> None:
    """Wie viele Zutaten auf dem Zettel stehen — und wie viele nicht.

    **Die Zahl, die vor dem „Ja" sagt, wie viel Nachlaufen es wird.** „23
    Zutaten, 8 davon nicht auf dem Zettel" ist eine Auskunft über den
    Einkauf; die Bewertung ist keine.

    Gerechnet und nicht gespeichert, weil sie sich noch ändert: eine
    Korrektur (WB-359) legt eine NEUE Vorschlagszeile an, und eine
    festgeschriebene Zahl stünde danach falsch da.

    Welche Zeilen zu welcher Karte gehören, entscheidet `zeilen_zur_karte`
    und nicht diese Funktion — seit WB-384 hängt an derselben Zuordnung auch,
    welche Mengen eine geänderte Portionszahl betrifft, und zwei Kopien der
    Regel liefen auseinander.
    """
    zeilen = list(vorgeschlagen or [])
    if not zeilen:
        return
    for k in karten:
        eigene = zeilen_zur_karte(k, zeilen, len(karten))
        if not eigene:
            k["n_zettel"] = k["n_ohne_produkt"] = None
            continue
        k["n_zettel"] = len(eigene)
        k["n_ohne_produkt"] = sum(1 for z in eigene if z["ist_freitext"])
        # Nur wo die Quelle eine Zutatenliste mitgebracht hat, gibt es
        # überhaupt etwas zu decken. Ein selbst angelegtes Rezept hat keine
        # `recipe_ingredient`-Zeilen — dann ist der Zettel die Zutatenliste,
        # und „0 von 0 gedeckt" wäre eine Aussage über nichts.
        k["n_fehlt"] = (max(k["n_zutaten"] - k["n_zettel"], 0)
                        if k["n_zutaten"] else None)
