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
"""
from __future__ import annotations

import sqlite3

from picknick.gerichte import chefkoch, speicher

#: Ab hier wird aus Minuten eine Stundenangabe. Darunter ist die Minutenzahl
#: die kürzere und die genauere Auskunft („35 Minuten"), darüber liest sich
#: „570 Minuten" wie ein Messwert und nicht wie eine Antwort.
STUNDE = 60


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
        "SELECT r.id, r.name, r.servings, r.prep_minutes, r.cook_minutes,"
        "       r.rest_minutes, r.difficulty, r.source, r.source_id,"
        "       r.source_url, r.source_title, r.source_rating, r.source_votes,"
        "       (r.instructions IS NOT NULL) AS hat_zubereitung,"
        "       r.instructions AS _text, z.dish_id, d.query AS gericht"
        "  FROM chat_rezept z JOIN recipe r ON r.id = z.recipe_id"
        "  LEFT JOIN dish d ON d.id = z.dish_id"
        " WHERE z.chat_message_id = ? ORDER BY z.pos, z.recipe_id",
        (chat_message_id,)).fetchall()
    karten = []
    for row in rows:
        k = dict(row)
        text = k.pop("_text", None)
        k["n_schritte"] = len(chefkoch.schritte(text)) if text else 0
        k["zutaten"] = speicher.zutaten(con, int(k["id"]))
        k["n_zutaten"] = len(k["zutaten"])
        k["gesamt_minuten"] = _gesamtzeit(k)
        k["zeitsatz"] = zeitsatz(k["gesamt_minuten"])
        k["ruhesatz"] = (zeitsatz(k["rest_minutes"])
                         if k["rest_minutes"] else None)
        k["n_zettel"] = k["n_ohne_produkt"] = k["n_fehlt"] = None
        k["alternativen"] = alternativen(con, k)
        if not _hat_inhalt(k):
            # Ein selbst angelegtes Rezept hat weder Zeiten noch Bewertung,
            # Zutatenliste oder Zubereitung — nur Produkte, und die stehen
            # gleich darunter als Vorschläge. Eine Karte mit einer einzigen
            # Portionszahl darin wäre Rahmen um nichts.
            continue
        karten.append(k)
    _deckung(karten, vorgeschlagen)
    return karten


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
        gesamt = _gesamtzeit({"prep_minutes": t["r_prep"],
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


def _hat_inhalt(k: dict) -> bool:
    """Trägt diese Karte irgendetwas, das nicht schon in der Liste steht?"""
    return bool(k["gesamt_minuten"] or k["source_rating"] or k["n_zutaten"]
                or k["n_schritte"] or k["source_url"])


def _gesamtzeit(k: dict) -> int | None:
    """Vorbereitung + Kochen + Ruhen. `None`, wenn keine Zeit dasteht.

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

    Zwei Wege führen zur Zuordnung, und beide stehen schon in den Daten:

    * **Quellenweg** — die Zeilen des Gerichts tragen `dish_item` (WB-337).
    * **Rezeptweg** — `_aus_rezept` schreibt den REZEPTNAMEN als
      `search_term` an jede Zeile. Dort gibt es keinen Entwurf, und ohne
      diesen Vergleich bliebe genau der schnelle Weg ohne Zahl.

    Bei mehreren Rezepten in einem Zug lässt sich der Quellenweg nicht
    aufteilen (der Entwurf kennt nur ein Gericht). Dann bleibt die Zahl weg,
    statt geraten zu werden.
    """
    zeilen = list(vorgeschlagen or [])
    if not zeilen:
        return
    korrekturen = {z["corrected_from"] for z in zeilen
                   if z.get("ist_korrektur")}
    zum_gericht = [z for z in zeilen if z.get("zum_gericht")]
    for k in karten:
        name = (k["name"] or "").casefold()
        if zum_gericht and len(karten) == 1:
            # Der Entwurf ist das genauere Signal, wo es ihn gibt — er steht
            # an der ZEILE und nicht an einem Namensvergleich. Bei zwei
            # Rezepten im Zug lässt er sich aber nicht aufteilen (er kennt
            # nur ein Gericht), dann gilt unten der Rezeptname.
            eigene = zum_gericht
        else:
            eigene = [z for z in zeilen
                      if (z.get("search_term") or "").casefold() == name]
        if not eigene:
            k["n_zettel"] = k["n_ohne_produkt"] = None
            continue
        # Eine korrigierte Zeile zählt nicht doppelt: die Korrektur ist
        # dieselbe Zutat mit einem anderen Produkt.
        eigene = [z for z in eigene if z["id"] not in korrekturen]
        k["n_zettel"] = len(eigene)
        k["n_ohne_produkt"] = sum(1 for z in eigene if z["ist_freitext"])
        # Nur wo die Quelle eine Zutatenliste mitgebracht hat, gibt es
        # überhaupt etwas zu decken. Ein selbst angelegtes Rezept hat keine
        # `recipe_ingredient`-Zeilen — dann ist der Zettel die Zutatenliste,
        # und „0 von 0 gedeckt" wäre eine Aussage über nichts.
        k["n_fehlt"] = (max(k["n_zutaten"] - k["n_zettel"], 0)
                        if k["n_zutaten"] else None)
