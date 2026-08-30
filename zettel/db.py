"""SQLite-Schema und Migrationen für Zettel.

Eine Datenbankdatei, ein Schema, keine ORM-Schicht. `migrate()` ist idempotent
und wird beim Start jedes Prozesses aufgerufen — Web, Crawler und Evals teilen
sich dieselbe Datei.

Abweichung von der Spec (Abschnitt 4), bewusst und hier dokumentiert:
Die Spec nennt die Bestelltabelle `order`. Das ist ein SQL-Schlüsselwort und
müsste in jeder Abfrage gequotet werden — eine Falle, die jedes Folgeticket
einmal stellen würde. Die Tabelle heisst deshalb `orders`. Alle Spalten sind
unverändert übernommen.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#: Die Datei behält ihren Namen von vor WB-401, und zwar mit Absicht: sie ist
#: gitignort, liegt auf genau einer Maschine, und ein `mv` würde einen
#: laufenden Shop von seinen Daten trennen, ohne dass irgendjemand etwas davon
#: hätte. Tabellen und Spalten heissen aus demselben Grund unverändert weiter.
DEFAULT_DB = "data/picknick.db"

#: Läden, aus denen ein Bestellposten geholt wird. Der Katalog ist ladenneutral
#: (Spec 5.1) — die Wahl hängt am Posten, nicht am Produkt.
STORES = ("rewe", "lidl", "egal")

#: Läden, aus denen ein KASSENBON stammt (WB-358). Bewusst nicht `STORES`:
#: dort heisst der dritte Wert `egal` und ist ein WUNSCH („hol es, wo du
#: willst"). Ein Bon kommt immer aus genau einem Laden; wenn er sich nicht
#: nennt, ist das `unbekannt` und keine Freiheit.
BON_STORES = ("rewe", "lidl", "unbekannt")

#: Zustände einer Bestellung. Mehr gibt es bewusst nicht: "unterwegs" fehlt,
#: weil der Einkaufende selbst sieht, dass er im Laden steht (Spec 4).
ORDER_STATES = ("draft", "offen", "erledigt")

#: Entscheidung über einen Chat-Vorschlag. `offen` heisst "nie entschieden" und
#: geht nicht in die Trefferquote ein (Spec 8.1).
DECISIONS = ("offen", "kept", "removed")


#: Deutsche Eigenheiten, die eine reine Volltextsuche leerlaufen lassen. Der
#: unicode61-Tokenizer von FTS5 entfernt zwar Diakritika ("Spülmittel" ->
#: "spulmittel"), aber niemand tippt "spulmittel" — getippt wird "spuelmittel".
#: Beide Schreibweisen werden deshalb auf DIESELBE ASCII-Form abgebildet, und
#: zwar an beiden Enden: beim Schreiben in den Index und auf dem Suchbegriff.
#: Nur so findet "spuelmittel" das "Spülmittel" und umgekehrt.
UMLAUTE = (
    ("Ä", "ae"), ("ä", "ae"),
    ("Ö", "oe"), ("ö", "oe"),
    ("Ü", "ue"), ("ü", "ue"),
    ("ẞ", "ss"), ("ß", "ss"),
)


def normalisiere(text: str) -> str:
    """Faltet Umlaute und ß auf ihre ASCII-Umschreibung.

    Das Gegenstück zu `_norm_sql()` unten. Die beiden MÜSSEN dasselbe tun —
    laufen sie auseinander, findet die Suche stillschweigend nichts mehr.
    Kleinschreibung übernimmt der Tokenizer, hier geht es nur um die
    Buchstabenersetzung.
    """
    for zeichen, ersatz in UMLAUTE:
        text = text.replace(zeichen, ersatz)
    return text


def _norm_sql(ausdruck: str) -> str:
    """Baut denselben Ersetzungslauf als SQL-Ausdruck (geschachtelte replace).

    SQLites eingebautes `lower()` kann nur ASCII, deshalb werden Gross- und
    Kleinbuchstaben einzeln aufgeführt statt vorher kleingeschrieben.
    """
    for zeichen, ersatz in UMLAUTE:
        ausdruck = f"replace({ausdruck}, '{zeichen}', '{ersatz}')"
    return ausdruck


#: Woraus die beiden normalisierten Suchspalten entstehen. Name und Marke
#: getrennt von den Kategorien, damit die Suche einen Namenstreffer höher
#: gewichten kann als einen blossen Kategorietreffer (siehe catalog/search.py).
NORM_NAME_SQL = _norm_sql(
    "coalesce(name, '') || ' ' || coalesce(brand, '')")
NORM_CAT_SQL = _norm_sql(
    "coalesce(category_l1, '') || ' ' || coalesce(category_l2, '')"
    " || ' ' || coalesce(category_l3, '')")

#: Generierte Spalten — SQLite rechnet sie bei jedem Lesen aus, sie belegen
#: keinen Platz und können nicht veralten. Deshalb VIRTUAL und nicht STORED:
#: `ALTER TABLE ADD COLUMN` erlaubt in SQLite ohnehin nur VIRTUAL.
NORM_SPALTEN = (
    ("norm_name", NORM_NAME_SQL),
    ("norm_cat", NORM_CAT_SQL),
)

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS product (
        id                   INTEGER PRIMARY KEY,
        source               TEXT    NOT NULL,
        external_id          TEXT    NOT NULL,
        name                 TEXT    NOT NULL,
        brand                TEXT,
        price_cents          INTEGER,
        price_per_unit_cents INTEGER,
        unit_text            TEXT,
        unit                 TEXT,
        image_path           TEXT,
        category_l1          TEXT,
        category_l2          TEXT,
        category_l3          TEXT,
        in_stock             INTEGER NOT NULL DEFAULT 1,
        last_seen_at         TEXT,
        active               INTEGER NOT NULL DEFAULT 1,
        UNIQUE (source, external_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id           INTEGER PRIMARY KEY,
        state        TEXT NOT NULL CHECK (state IN ('draft', 'offen', 'erledigt')),
        note         TEXT,
        created_at   TEXT NOT NULL,
        submitted_at TEXT,
        done_at      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_item (
        id         INTEGER PRIMARY KEY,
        order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        product_id INTEGER REFERENCES product(id),
        free_text  TEXT,
        -- Die PACKUNGSZAHL: was im Laden gegriffen wird. Sie ist seit WB-362
        -- ein ERGEBNIS und keine Eingabe mehr — gerechnet aus `need_amount`
        -- gegen die Packungsgrösse des Produkts, mindestens aber `hand_qty`.
        qty        INTEGER NOT NULL DEFAULT 1,
        -- Die benötigte MENGE, zusammengezählt über alle Rezepte, die auf
        -- dieses Produkt zeigen (WB-362). Der Grund, warum es diese Spalte
        -- gibt: ohne sie ist die Information, aus der man zusammenzählen
        -- müsste, beim Einlegen schon weggerundet. Zwei Rezepte mit je 40 g
        -- Knoblauch hinterliessen zweimal „1 Packung", und aus 1 + 1 lässt
        -- sich 80 g nicht mehr zurückgewinnen.
        --
        -- Nullable, und das ist kein Versäumnis: ein von Hand eingelegter
        -- Posten HAT keine benötigte Menge. Er sagt „eine Packung", nicht
        -- „80 Gramm", und eine 0 an dieser Stelle wäre die Behauptung, es
        -- werde nichts davon gebraucht.
        need_amount REAL,
        -- Die Grundeinheit dazu (`g`, `ml`, `Stk`, oder eine eigene wie
        -- `Bund`). Getrennt gespeichert, weil sich 200 g und 2 Bund nicht
        -- zusammenzählen lassen und der Unterschied sichtbar bleiben muss.
        need_unit  TEXT,
        -- Wie viele Packungen ausdrücklich VERLANGT wurden — der Griff ins
        -- Regal, das „+" an der Kachel, die von Hand gesetzte Menge. Sie ist
        -- die Untergrenze für `qty`: ein Handposten darf nicht verschwinden,
        -- nur weil ein Rezept rechnerisch mit weniger auskäme.
        hand_qty   INTEGER,
        store      TEXT NOT NULL DEFAULT 'egal'
                        CHECK (store IN ('rewe', 'lidl', 'egal')),
        picked_at  TEXT,
        -- Der dritte Stand eines Postens (WB-373): im Laden nicht bekommen.
        -- Eine eigene Spalte neben `picked_at` und nicht ein Wert darin, weil
        -- beide dasselbe sagen wollen wie `picked_at` schon sagt — WANN es
        -- passiert ist — und weil eine ältere Datenbank die Spalte per
        -- ADD COLUMN bekommt: NULL heisst dort richtigerweise „nicht
        -- vermisst".
        --
        -- Dass nie beide zugleich stehen, sichert `orders.setze_stand()` und
        -- ausdrücklich kein CHECK: ADD COLUMN kann keine Tabellenbedingung
        -- nachtragen, und eine Zusicherung, die nur in frisch angelegten
        -- Datenbanken gilt, ist schlimmer als keine.
        missing_at TEXT,
        -- Genau eines von beidem, nie beides und nie keines: das Freitext-
        -- Ventil ist gleichwertig, nicht ein Sonderfall (Spec 4).
        CHECK ((product_id IS NULL) <> (free_text IS NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipe (
        id       INTEGER PRIMARY KEY,
        name     TEXT NOT NULL,
        servings INTEGER,
        note     TEXT,
        -- Ab hier: was zum KOCHEN gebraucht wird und nicht zum Einkaufen
        -- (WB-338). Ein selbst angelegtes Rezept lässt alles davon leer —
        -- die Ansicht darf deswegen nicht kaputt aussehen, und `note` ist
        -- weiterhin die Stelle für eigene Notizen.
        --
        -- Die Zubereitung steht als EIN Text mit den Zeilenumbrüchen, die
        -- die Quelle gesetzt hat. Die Schritte daraus macht die Ansicht
        -- (`gerichte.chefkoch.schritte`): ein Absatz mit 3.924 Zeichen als
        -- eine Wand ist auf einem Telefon am Herd unbrauchbar, und die
        -- Struktur ist bereits im Text — sie darf nur nicht verlorengehen.
        instructions  TEXT,
        prep_minutes  INTEGER,
        cook_minutes  INTEGER,
        rest_minutes  INTEGER,
        difficulty    INTEGER,
        -- Die Herkunft, und zwar sichtbar und nicht bloss gespeichert: das
        -- ist fremde Arbeit. Wer das Rezept wirklich kocht, soll die
        -- Originalseite mit ihren Bildern und Kommentaren aufmachen können.
        source        TEXT,
        source_id     TEXT,
        source_url    TEXT,
        source_title  TEXT,
        source_rating REAL,
        source_votes  INTEGER,
        fetched_at    TEXT
    )
    """,
    # Die Zutaten, WIE DIE QUELLE SIE SCHREIBT — „500 ml Tomaten, passierte".
    #
    # **Ausdrücklich nicht `recipe_item`**, und der Unterschied ist der ganze
    # Zweck der Tabelle: `recipe_item` sind die PRODUKTE, die für das Rezept
    # gekauft werden (Katalogverweis oder Freitext, Spec 4). Hier steht die
    # Zutatenliste des Rezepts: Menge, Einheit, Name, Gruppe („Für die
    # Brühe"). Das eine ist ein Einkaufszettel, das andere ein Rezept, und
    # beides in eine Tabelle zu legen hiesse, jeder Zeile eine Menge in
    # Packungen UND eine in Litern zu geben.
    #
    # Solange ein aus Chefkoch geholtes Rezept keine verknüpften Produkte hat,
    # bleibt `recipe_item` leer — und genau daran erkennt `rezeptweg.erkenne`,
    # dass es den Chat-Zug NICHT abfangen soll (es hätte nichts vorzuschlagen).
    """
    CREATE TABLE IF NOT EXISTS recipe_ingredient (
        id         INTEGER PRIMARY KEY,
        recipe_id  INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
        pos        INTEGER NOT NULL,
        gruppe     TEXT,
        -- Wie es dasteht: „Zwiebel(n)". Bleibt unverändert, weil ein
        -- besserer Zerleger später einen zweiten Versuch bekommen soll.
        raw_name   TEXT NOT NULL,
        -- Das genaueste Glied der Begriffskette: „passierte Tomaten".
        name       TEXT NOT NULL,
        amount     REAL,
        unit       TEXT,
        usage_info TEXT
    )
    """,
    # Ein GEFRAGTES Gericht und was der Abruf ergeben hat (WB-338). Der
    # Zwischenspeicher, ohne den jeder Chat-Zug erneut ins Netz ginge.
    #
    # Getrennt von `recipe`, weil hier die FRAGE steht und dort die ANTWORT:
    # gefragt wird nach „Pho", geliefert wird „Pho Bo — Vietnamesische
    # Rindfleischsuppe". Ohne eigene Zeile liesse sich weder „danach wurde
    # schon einmal gefragt" noch „dazu kennt Chefkoch nichts" merken — und
    # gerade das Nichts muss gemerkt werden, sonst kostet jedes unbekannte
    # Gericht bei jedem Chat-Zug zwei Anfragen an eine fremde Seite.
    """
    CREATE TABLE IF NOT EXISTS dish (
        id           INTEGER PRIMARY KEY,
        -- Der gefaltete Suchschlüssel („gemueselasagne"), damit „Gemüse-
        -- lasagne" und „gemuselasagne" dieselbe Zeile treffen.
        name         TEXT NOT NULL UNIQUE,
        -- Wie das Gericht genannt wurde. Das geht an die Quelle und steht
        -- in der Meldung.
        query        TEXT NOT NULL,
        status       TEXT NOT NULL
                         CHECK (status IN ('offen', 'ok', 'leer', 'fehler')),
        source       TEXT,
        recipe_id    INTEGER REFERENCES recipe(id) ON DELETE SET NULL,
        error        TEXT,
        requested_at TEXT NOT NULL,
        -- Das Abrufdatum. Daran hängt, ob der Speicher noch gilt.
        fetched_at   TEXT
    )
    """,
    # Die ÜBRIGEN Treffer derselben Suche (WB-387).
    #
    # Chefkoch liefert je Suche zwölf Rezepte in EINER Antwort; bis zu diesem
    # Ticket nahm `chefkoch.bestes()` genau eines und die anderen elf
    # verschwanden, bevor ein Mensch sie sehen konnte. Gemessen an „Lasagne"
    # (2026-08-29) sind das keine Varianten desselben Rezepts, sondern
    # verschiedene Gerichte: klassisch mit Hack, vegetarisch mit Spinat,
    # Zucchini, Filoteig mit Ziegenkäse. Welches gemeint war, weiss nur der
    # Mensch — und ein besserer Sortierschlüssel löst das nicht.
    #
    # **Eine eigene Tabelle am GERICHT und nicht am Chat-Zug.** Die zwölf
    # sind die Antwort auf die Suche und gehören dorthin, wo auch die Frage
    # steht: an `dish`. Damit kann ein zweiter Zug zu demselben Gericht
    # dieselbe Auswahl anbieten, ohne noch einmal zu suchen — und genau das
    # ist die Zusage des Tickets (die Wahl kostet keinen zweiten Suchabruf).
    # An der Chatzeile (wie `chat_sorte`) lägen sie je Zug ein zweites Mal.
    #
    # Gespeichert wird, was die SUCHE hergibt, und keine Zeile mehr: Titel,
    # Bewertung mit Stimmen, Arbeitszeit, Schwierigkeit, `isPlus`. **Koch-
    # und Ruhezeit und die Zutatenzahl stehen NICHT darin** — die Suchantwort
    # trägt sie nicht, und sie nachzuholen kostete elf weitere Anfragen. Wo
    # ein Treffer bereits geholt wurde, stehen sie in `recipe` und werden von
    # dort dazugeholt (Verbund über `source_id`).
    """
    CREATE TABLE IF NOT EXISTS dish_treffer (
        id           INTEGER PRIMARY KEY,
        dish_id      INTEGER NOT NULL REFERENCES dish(id) ON DELETE CASCADE,
        -- Die Rezept-ID der Quelle. KEIN Fremdschlüssel auf `recipe`: die
        -- allermeisten dieser Treffer sind nie geholt worden, und genau das
        -- ist ihr Zweck — sie stehen zur Wahl, bevor jemand sie holt.
        source_id    TEXT NOT NULL,
        source_title TEXT NOT NULL,
        source_url   TEXT,
        rating       REAL,
        votes        INTEGER,
        -- Chefkochs `preparationTime`: die ARBEITSZEIT, nicht die
        -- Gesamtzeit. Pho Bo steht hier mit 90 Minuten und braucht in
        -- Wahrheit 9½ Stunden — die 480 Minuten Kochzeit stehen erst im
        -- Detail. Die Spalte heisst deshalb wie das Feld und nicht wie die
        -- Auskunft, die sie NICHT gibt.
        prep_minutes INTEGER,
        difficulty   INTEGER,
        -- Chefkoch-Plus. Diese Treffer tragen erfundene Stimmenzahlen
        -- (`numVotes: 255`, siehe `chefkoch.PLUS_UEBERGEHEN`) und werden
        -- nicht angeboten. Gespeichert werden sie trotzdem: sonst stünde
        -- hier eine Liste, die anders aussieht als die Antwort der Quelle.
        plus         INTEGER NOT NULL DEFAULT 0,
        -- Die gewichtete Note, nach der vorausgewählt wird. Mitgeschrieben,
        -- damit die Reihenfolge des Angebots dieselbe ist wie die der Wahl
        -- und nicht bei jedem Anzeigen neu gerechnet wird.
        gewicht      REAL,
        -- Die Stelle in der Antwort der Quelle.
        pos          INTEGER NOT NULL,
        -- Dasselbe Rezept zweimal zu einem Gericht wäre dieselbe Zeile
        -- zweimal auf dem Handy.
        UNIQUE (dish_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipe_item (
        id         INTEGER PRIMARY KEY,
        recipe_id  INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
        product_id INTEGER REFERENCES product(id),
        free_text  TEXT,
        qty        INTEGER NOT NULL DEFAULT 1,
        -- Wie viel von diesem Produkt das Rezept braucht — bei DER
        -- Portionszahl, die in `recipe.servings` steht (WB-362). Das ist die
        -- Grösse, die mit den Portionen wächst; `qty` ist es nicht.
        --
        -- Ausdrücklich HIER und nicht als Verweis auf `recipe_ingredient`:
        -- die Zutatenliste der Quelle wird bei jedem Abruf gelöscht und neu
        -- geschrieben (`gerichte.speicher.merken`), ein Fremdschlüssel darauf
        -- risse die von Hand verknüpften Produkte mit. Genau deren Erhalt ist
        -- dort ausdrücklich zugesagt.
        --
        -- Nullable: eine Zutat, die jemand als „1 Glas Pesto" verknüpft hat,
        -- hat keine Menge in Gramm, und eine erfundene wäre schlimmer als
        -- keine.
        amount     REAL,
        unit       TEXT,
        CHECK ((product_id IS NULL) <> (free_text IS NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_message (
        id         INTEGER PRIMARY KEY,
        order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        -- Span-ID des zugehörigen chat.turn (Spec 7.1). Die Annotationen aus
        -- Spec 8.1 hängen später genau hier dran.
        span_id    TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_suggestion (
        id              INTEGER PRIMARY KEY,
        chat_message_id INTEGER NOT NULL
                            REFERENCES chat_message(id) ON DELETE CASCADE,
        product_id      INTEGER REFERENCES product(id),
        free_text       TEXT,
        -- Die PACKUNGSZAHL, und auf diesem Weg lange die einzige Zahl: eine
        -- vom Modell GERATENE Menge. Sie bleibt es für alles ohne
        -- Mengenangabe („Klopapier"); wo `need_amount` steht, ist sie 1 und
        -- die Packungszahl entsteht im Korb aus der Rechnung (WB-369).
        qty             INTEGER NOT NULL DEFAULT 1,
        -- Die benötigte MENGE aus der Zutatenliste des Rezepts (WB-369),
        -- schon in der Grundeinheit (`g`, `ml`, `Stk`, oder eine eigene wie
        -- `bund`). Sie ist der Grund, warum die Rechnung aus WB-362 auf dem
        -- Chat-Weg überhaupt anspringt: beim „Ja" geht sie als
        -- `korb.einlegen(menge=…)` weiter, wird dort je Produkt
        -- zusammengezählt und erst danach gegen die Packungsgrösse
        -- aufgerundet.
        --
        -- Nullable, und das ist der Normalfall: ein Vorschlag aus
        -- „Klopapier" hat keine benötigte Menge, und eine 0 wäre die
        -- Behauptung, es werde nichts davon gebraucht. Dasselbe gilt für
        -- jede Zeile, die vor WB-369 entstanden ist.
        need_amount     REAL,
        need_unit       TEXT,
        search_term     TEXT,
        rank            REAL,
        decision        TEXT NOT NULL DEFAULT 'offen'
                            CHECK (decision IN ('offen', 'kept', 'removed')),
        decided_at      TEXT,
        -- Wann diese Zeile in den Korb gelegt wurde (WB-361). Das ist der
        -- Schutz gegen den Doppeltipp, und er hängt bewusst NICHT mehr an
        -- `decision`: seit eine Entscheidung zurücknehmbar ist, wäre
        -- „steht schon auf kept" kein Schutz mehr — „Ja, rückgängig, Ja"
        -- liefe zweimal durch `orders.einlegen()` und stockte die Menge im
        -- Korb auf. Gefragt ist nicht „ist sie gerade behalten", sondern
        -- „war sie schon einmal im Korb", und das steht nur hier.
        --
        -- Ein Zeitstempel und kein Ja/Nein: er kostet dasselbe und sagt
        -- zusätzlich, WANN — und beim Zurücknehmen eines „Ja" weiss die
        -- Oberfläche daran, dass sie auf die Korbzeile hinweisen muss, die
        -- absichtlich stehen bleibt.
        eingelegt_at    TEXT,
        -- Wie oft diese Entscheidung zurückgenommen wurde (WB-361). Ein
        -- Fehltipp hinterlässt sonst KEINE Spur: das Label wird erst beim
        -- Abschicken geschrieben (WB-329), und eine zurückgenommene
        -- Entscheidung schreibt gar keines — richtig so, aber dann sähe
        -- später niemand, wie oft auf dem Handy danebengetippt wird. Der
        -- Zähler wandert als Metadatum an die Annotation.
        --
        -- Nullable und nicht `NOT NULL DEFAULT 0`: dieselbe Spalte entsteht
        -- auch per ALTER TABLE an einer gewachsenen Datenbank
        -- (`NACHGETRAGENE_SPALTEN`), und die kann dort nur NULL sein. Zwei
        -- Vorgaben für dieselbe Spalte wären zwei Verhalten; gelesen wird
        -- sie an einer Stelle mit `or 0`.
        zurueckgenommen INTEGER,
        -- WELCHER Sammelvorgang diese Zeile entschieden hat (WB-397) — eine
        -- laufende Nummer je Zug, und NULL für jede einzeln getroffene
        -- Entscheidung. Sie ist die einzige Angabe, mit der „Doch nicht
        -- alles" die neun Zeilen des Sammeltipps von der Butter und dem
        -- Spinat unterscheiden kann, die die Nutzerin vorher selbst
        -- angetippt hat.
        --
        -- Warum keine Gruppierung über `decided_at`: `jetzt()` ist
        -- sekundengenau. Ein einzelnes „Ja" in derselben Sekunde fiele in
        -- die Gruppe, und ein Sammelvorgang, der über eine Sekundengrenze
        -- läuft (elf Zeilen gehen einzeln durch `orders.einlegen()`),
        -- zerfiele in zwei. Beide Fehler gehen in die Richtung, die das
        -- Ticket ausdrücklich verbietet.
        --
        -- Die Nummer wird beim Zurücknehmen wieder NULL — und ebenso, sobald
        -- die Zeile EINZELN angetippt wird. Ab da gehört die Entscheidung
        -- ihr, und der Sammelrückweg lässt sie stehen. Deshalb ist das
        -- grösste vergebene `sammel_nr` eines Zugs immer der jüngste noch
        -- rücknehmbare Sammelvorgang.
        --
        -- Nullable und ohne Nachtrag für gewachsene Datenbanken: was vor
        -- diesem Ticket gesammelt entschieden wurde, ist im Nachhinein nicht
        -- mehr von einzelnen Tipps zu unterscheiden. NULL heisst dort genau
        -- das Richtige — „kein Sammelvorgang, den man zurücknehmen könnte".
        sammel_nr       INTEGER,
        -- Diese Zeile ist die KORREKTUR eines anderen Vorschlags (WB-359):
        -- die Nutzerin hat „Nein" gesagt und aus den aufgehobenen Kandidaten
        -- etwas anderes gewählt. Ohne diesen Verweis stünden hinterher zwei
        -- lose Entscheidungen da — ein `removed` und ein `kept` —, und es
        -- wäre nicht mehr zu unterscheiden, ob sie korrigiert oder einfach
        -- etwas dazugelegt hat. Genau dieser Unterschied ist das wertvolle
        -- Eval-Signal: „das war falsch UND das wäre richtig gewesen".
        corrected_from  INTEGER REFERENCES chat_suggestion(id)
                            ON DELETE CASCADE,
        -- Gesetzt, wenn ALLE Kandidaten dieser Zutat nur über den
        -- ALLGEMEINSTEN Begriff der Kette hereinkamen (WB-359, Befund aus
        -- WB-358): „OLD AMSTERDAM" -> Kette endete auf „Bier" ->
        -- Singha Bier.
        -- Findet kein genauer Begriff etwas, greift der allgemeinste, und der
        -- findet immer irgendetwas. Ein solcher Vorschlag ist kein sicherer
        -- Treffer, und die Zeile soll das sagen statt es zu verschweigen.
        fallback_term   TEXT,
        CHECK ((product_id IS NULL) <> (free_text IS NULL))
    )
    """,
    # Die Kandidaten, die Stufe 2 zu einer Zutat vorgelegt hat (WB-359).
    #
    # **Eine eigene Tabelle und kein JSON in einer Spalte an
    # `chat_suggestion`.** Die Kandidaten sind eine Liste von Verweisen auf
    # `product`, und drei Dinge kann eine Spalte mit JSON nicht: ein
    # Fremdschlüssel darauf (ein Kandidat, den ein Crawl entfernt hat, fiele
    # sonst erst beim Anzeigen auf), ein JOIN auf `product` (Name, Preis und
    # Bild kämen sonst aus einer eingefrorenen Kopie von gestern) und eine
    # Auswertung über Züge hinweg („wie oft stand das Richtige in der Liste
    # und wurde nicht gewählt?"). Der Preis dafür ist eine Tabelle mehr; der
    # Preis für JSON wäre ein Parser von Hand in jedem Folgeticket.
    #
    # Aufgehoben wird die ANZEIGE-Liste (`plan.KANDIDATEN_ANZEIGE`), nicht die
    # kürzere Vorlage für Stufe 3: was der Mensch zu sehen bekommt, kostet
    # Datenbankzeilen und keine Token. Die Modellvorlage ist eine Teilmenge
    # davon (`catalog.search.kuerze_kette`).
    """
    CREATE TABLE IF NOT EXISTS chat_kandidat (
        id            INTEGER PRIMARY KEY,
        suggestion_id INTEGER NOT NULL
                          REFERENCES chat_suggestion(id) ON DELETE CASCADE,
        product_id    INTEGER NOT NULL REFERENCES product(id),
        -- Die Stelle in der Vorlage: die Reihenfolge der KETTE (WB-340) und
        -- der Wortstufe (WB-339), nicht der rohe bm25 über die Vereinigung
        -- hinweg. Sie wird beim Anzeigen genau so wiederhergestellt.
        pos           INTEGER NOT NULL,
        -- Der Begriff der Kette, der diesen Kandidaten gebracht hat (`via`).
        search_term   TEXT,
        rank          REAL,
        -- Derselbe Kandidat zweimal an derselben Zeile wäre dieselbe
        -- Alternative zweimal auf dem Handy.
        UNIQUE (suggestion_id, product_id)
    )
    """,
    # Die Sorten, die eine Auffächerung angeboten hat (WB-368).
    #
    # **Eine eigene Tabelle und ausdrücklich KEINE Zeile in
    # `chat_suggestion`.** Eine Sorte ist kein Vorschlag: sie hat kein
    # Produkt, sie kommt nicht in den Korb, und ein „Ja" dazu wäre keine
    # Entscheidung über einen Fehlgriff des Modells. In `chat_suggestion`
    # zählte sie in `decision` mit und verfälschte damit genau die
    # Trefferquote aus Spec 8.1, um die es im ganzen Projekt geht.
    #
    # Gespeichert wird, was ANGEBOTEN wurde — mit der Stückzahl von damals.
    # Sie wird beim Anzeigen nicht neu gezählt: die Nutzerin hat „Salami (34)"
    # gesehen und soll dieselbe Zeile wiederfinden, auch wenn der Nachtlauf
    # inzwischen zwei Salami ausgemustert hat. Was der Katalog HEUTE hergibt,
    # entscheidet sich beim Tippen auf die Sorte.
    """
    CREATE TABLE IF NOT EXISTS chat_sorte (
        id              INTEGER PRIMARY KEY,
        chat_message_id INTEGER NOT NULL
                            REFERENCES chat_message(id) ON DELETE CASCADE,
        -- Das getippte Wort („Aufschnitt"). Es steht an jeder Zeile, obwohl
        -- es sich innerhalb einer Auffächerung nicht ändert: ohne es wäre
        -- der Weg zurück zum Freitext („such doch direkt danach") auf die
        -- vorige Chatzeile angewiesen, und die kann anders lauten als das
        -- Wort, das die Kategorie getroffen hat.
        wort            TEXT NOT NULL,
        category_l1     TEXT NOT NULL,
        -- Die Sorte: eine `category_l2` des Katalogs. Kein Fremdschlüssel —
        -- der Kategoriebaum ist eine Ableitung aus `product` und keine
        -- Tabelle (siehe catalog/categories.py).
        name            TEXT NOT NULL,
        anzahl          INTEGER NOT NULL,
        pos             INTEGER NOT NULL,
        -- Dieselbe Sorte zweimal an derselben Zeile wäre dasselbe Kästchen
        -- zweimal auf dem Handy.
        UNIQUE (chat_message_id, name)
    )
    """,
    # Der Rezeptentwurf eines Zugs (WB-337).
    #
    # **Eine Zeile je Chatzeile und keine Spalten an `chat_message`.** Die
    # Nachricht ist der SATZ; der Entwurf ist eine Absicht, die daneben
    # entsteht, ihren eigenen Zustand hat (umbenannt, verworfen, gespeichert)
    # und die es zu 99 % der Nachrichten gar nicht gibt. Vier nullable
    # Spalten an `chat_message`, die nur an jeder zwanzigsten Zeile gefüllt
    # sind, wären dieselbe Tabelle mit mehr Fussnoten — dieselbe Begründung
    # wie bei `chat_sorte`.
    #
    # `dish` ist, wie die Nutzerin das Gericht genannt hat („Spaghetti
    # Bolognese"), `name` der Rezeptname — **vorbelegt mit `dish`, weil ein
    # Rezept nur dann wiedergefunden wird, wenn es heisst, wie man danach
    # fragt** (`rezeptweg.erkenne` vergleicht den Namen im Satz). Ein
    # geratener Name („Einkauf vom 28.08.") wäre hier wertlos.
    """
    CREATE TABLE IF NOT EXISTS chat_entwurf (
        chat_message_id INTEGER PRIMARY KEY
                            REFERENCES chat_message(id) ON DELETE CASCADE,
        dish            TEXT NOT NULL,
        name            TEXT NOT NULL,
        -- Das Rezept, in das dieser Entwurf gehört: vor dem Abschicken das
        -- von Chefkoch geholte (dort steht die Zubereitung), danach das,
        -- in das die Zutaten gewandert sind. Meistens dasselbe.
        --
        -- `ON DELETE SET NULL` und nicht CASCADE: wer das geholte Rezept
        -- wegwirft, soll nicht den halben Chatverlauf mitnehmen. Der
        -- Entwurf legt dann beim Abschicken ein neues an.
        recipe_id       INTEGER REFERENCES recipe(id) ON DELETE SET NULL,
        -- „Daraus soll kein Rezept werden." Das Gegenstück zum Regelfall,
        -- und es muss eines geben: das Rezept entsteht ungefragt beim
        -- Abschicken, also braucht es einen Knopf, der das abstellt.
        verworfen       INTEGER NOT NULL DEFAULT 0,
        -- Wann gespeichert wurde. Zugleich die Sperre gegen ein zweites
        -- Speichern desselben Entwurfs — dieselbe Rolle wie
        -- `chat_suggestion.eingelegt_at` beim Doppeltipp.
        saved_at        TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    # Welches Rezept ein Chat-Zug ZEIGT (WB-383).
    #
    # **Nicht dasselbe wie `chat_entwurf.recipe_id`, und deshalb eine eigene
    # Tabelle.** Der Entwurf sagt, wohin beim Abschicken gespeichert WIRD; er
    # entsteht nur auf dem Quellenweg und kann sein Ziel noch wechseln. Hier
    # steht, was in diesem Zug vorgeschlagen wurde — und das gibt es auch auf
    # dem Rezeptweg (`weg = recipe`), der gar keinen Entwurf hat, weil das
    # Rezept dort längst existiert. Ohne diese Zeile sähe genau der schnelle
    # Weg ärmer aus als der langsame.
    #
    # Mehrere Zeilen je Zug sind möglich und keine Ausnahme: „alles für
    # Bolognese und Kartoffelsalat" trifft zwei Rezepte. `pos` hält die
    # Reihenfolge, in der sie im Satz standen.
    #
    # ON DELETE CASCADE auf BEIDEN Seiten: Der Zug ist ohne seine Chatzeile
    # nichts, und ein gelöschtes Rezept hat nichts mehr zu zeigen — anders
    # als beim Entwurf, der ohne sein Rezept weiterlebt und beim Abschicken
    # ein neues anlegt.
    """
    CREATE TABLE IF NOT EXISTS chat_rezept (
        chat_message_id INTEGER NOT NULL
                            REFERENCES chat_message(id) ON DELETE CASCADE,
        recipe_id       INTEGER NOT NULL
                            REFERENCES recipe(id) ON DELETE CASCADE,
        pos             INTEGER NOT NULL,
        -- Welches GERICHT dieses Rezept in diesem Zug vertreten hat (WB-387).
        -- Daran hängen die Alternativen: die übrigen Treffer derselben Suche
        -- stehen in `dish_treffer` am Gericht, und ohne diesen Verweis fände
        -- die Karte sie nicht wieder. Über `dish.recipe_id` ginge es nicht —
        -- das zeigt auf das ZULETZT gewählte Rezept, und ein älterer Zug im
        -- Verlauf verlöre seine Liste genau dann, wenn jemand gewechselt
        -- hat.
        --
        -- Nullable, und das ist der Normalfall: der Rezeptweg hat gar kein
        -- Gericht, und jeder Zug aus der Zeit vor diesem Ticket hat keines
        -- mehr, das sich ohne Raten zuordnen liesse. ON DELETE SET NULL —
        -- wer ein Gericht aus dem Zwischenspeicher wirft, soll nicht die
        -- Rezeptkarte eines Chat-Zugs mitnehmen.
        dish_id         INTEGER REFERENCES dish(id) ON DELETE SET NULL,
        -- Für wie viele Portionen dieser ZUG rechnet (WB-384). Die Vorgabe
        -- kommt aus `recipe.servings`, die Entscheidung von hier — und das
        -- Rezept wird dabei nicht angefasst. Dieselbe Trennung wie beim
        -- Portionsfeld an `recipes.in_den_korb` (WB-362): „diesmal für acht"
        -- ist eine Aussage über diesen Einkauf und keine über das Rezept.
        --
        -- Nullable, und das ist der Normalfall: solange niemand am Feld war,
        -- gilt die Zahl der Quelle. Eine beim Anlegen mitgeschriebene Kopie
        -- von `servings` wäre schlechter — sie sagte nicht mehr, ob jemand
        -- gewählt hat, und ein später gewechseltes Rezept (WB-387) käme mit
        -- seiner eigenen Zahl nicht mehr durch.
        portionen       INTEGER,
        PRIMARY KEY (chat_message_id, recipe_id)
    )
    """,
    # ----------------------------------------------------------------------
    # Kassenbons (WB-358). Eigene Tabellen, ausdrücklich NICHT `orders`:
    #
    # Eine Bestellung ist ein PLAN — was gekauft werden soll, aus dem
    # Knuspr-Katalog, mit Ladenwunsch und einem Zustand, der von `draft` nach
    # `erledigt` läuft. Ein Kassenbon ist eine TATSACHE — was gekauft wurde,
    # in welchem Laden, an welchem Tag, zu welchem Preis. Beides in dieselbe
    # Tabelle zu legen hiesse: `orders`-Zeilen, die nie geplant wurden, ein
    # `state`, der für einen Bon nichts bedeutet, und eine Preisspalte an
    # `order_item`, die bei jeder anderen Zeile leer stünde.
    #
    # `receipt` trägt AUSSCHLIESSLICH Laden, Datum, Datei und Summe. Es gibt
    # hier keine Spalte für Kartennummer, VU-Nummer, Terminal-ID, Trace- oder
    # Belegnummer — nicht „die füllen wir nicht", sondern es gibt sie nicht.
    # Das ist die strukturelle Hälfte der Zusage aus WB-358; die andere ist
    # `bons.zerlegen`, das den Zahlungsteil des Bons gar nicht erst ansieht.
    """
    CREATE TABLE IF NOT EXISTS receipt (
        id          INTEGER PRIMARY KEY,
        store       TEXT NOT NULL
                        CHECK (store IN ('rewe', 'lidl', 'unbekannt')),
        -- Der Einkaufstag vom Bon (ISO), nicht der Tag des Einlesens.
        bought_on   TEXT,
        -- Der Dateiname unter data/bons/, damit ein zweites Einlesen
        -- derselben Datei nicht eine zweite Kaufhistorie erzeugt. Nur der
        -- NAME, nie der Inhalt.
        file_name   TEXT UNIQUE,
        total_cents INTEGER,
        created_at  TEXT NOT NULL
    )
    """,
    # `receipt_item` ist zugleich die Preisbeobachtung: Produkt, Laden, Datum
    # und bezahlter Betrag stehen nach dem Verbund mit `receipt` vollständig
    # da. Eine zweite Tabelle „echte Preise" wäre eine Kopie derselben Zeilen
    # und könnte auseinanderlaufen, sobald jemand eine Zuordnung korrigiert.
    #
    # `decision` ist dieselbe Spalte mit derselben Bedeutung wie an
    # `chat_suggestion`: die Zuordnung ist ein VORSCHLAG, bis ein Mensch sie
    # bestätigt. Nur `kept` zählt als echter Preis und als Kaufhistorie — ein
    # falsch zugeordneter Kauf verfälscht die Vorlieben dauerhaft.
    """
    CREATE TABLE IF NOT EXISTS receipt_item (
        id          INTEGER PRIMARY KEY,
        receipt_id  INTEGER NOT NULL REFERENCES receipt(id) ON DELETE CASCADE,
        line_no     INTEGER NOT NULL,
        -- Wie es auf dem Bon steht: „SCHIN.-KAE. CRO.". Bleibt unverändert,
        -- weil ein gewachsener Katalog später einen zweiten Versuch erlaubt.
        bon_text    TEXT NOT NULL,
        qty         INTEGER NOT NULL DEFAULT 1,
        unit_cents  INTEGER,
        -- Der Zeilenbetrag vom Bon. Das ist der echte bezahlte Preis.
        total_cents INTEGER NOT NULL,
        product_id  INTEGER REFERENCES product(id),
        -- Was das Modell aus der Abkürzung gemacht hat („Schinken-Käse-
        -- Croissant"). Erklärt die Zuordnung an der Zeile und überlebt einen
        -- Phoenix, der gerade nicht lief.
        note        TEXT,
        search_term TEXT,
        rank        REAL,
        decision    TEXT NOT NULL DEFAULT 'offen'
                        CHECK (decision IN ('offen', 'kept', 'removed')),
        decided_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scrape_run (
        id          INTEGER PRIMARY KEY,
        source      TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        status      TEXT,
        n_products  INTEGER,
        error       TEXT
    )
    """,
    # Genau EIN Warenkorb, gemeinsam für beide (Spec 4). Der Zwang steht
    # bewusst hier und nicht nur in `orders.warenkorb()`: eine Python-Funktion
    # kann zwei gleichzeitige Aufrufe nicht daran hindern, beide „kein draft
    # vorhanden" zu lesen und beide einen anzulegen. Ein partieller UNIQUE-Index
    # kann es — der zweite INSERT scheitert, egal aus welchem Prozess er kommt.
    # Nur `state = 'draft'` ist eingeschränkt; von `offen` und `erledigt` darf
    # es beliebig viele geben.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_ein_draft"
    " ON orders(state) WHERE state = 'draft'",
    "CREATE INDEX IF NOT EXISTS ix_order_item_order ON order_item(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_recipe_item_recipe ON recipe_item(recipe_id)",
    "CREATE INDEX IF NOT EXISTS ix_recipe_ingredient_recipe"
    " ON recipe_ingredient(recipe_id)",
    "CREATE INDEX IF NOT EXISTS ix_dish_status ON dish(status)",
    "CREATE INDEX IF NOT EXISTS ix_chat_message_order ON chat_message(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_sugg_message ON chat_suggestion(chat_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_active ON product(active)",
    "CREATE INDEX IF NOT EXISTS ix_receipt_item_receipt"
    " ON receipt_item(receipt_id)",
    # Die Frage, die dieser Index beantwortet: „was hat DIESES Produkt in
    # Wirklichkeit gekostet?" Sie wird je Produktkachel gestellt, nicht einmal
    # im Nachtlauf.
    "CREATE INDEX IF NOT EXISTS ix_receipt_item_product"
    " ON receipt_item(product_id) WHERE product_id IS NOT NULL",
]

# Volltextsuche über die Felder, nach denen im Katalog gesucht wird. `external
# content` heisst: der Index speichert den Text nicht doppelt, sondern verweist
# auf product.rowid. Dafür müssen die Trigger unten den Index von Hand
# nachziehen — vergisst man einen, findet die Suche stillschweigend zu wenig.
FTS_SCHEMA = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS product_fts USING fts5(
        name, brand, category_l1, category_l2, category_l3,
        norm_name, norm_cat,
        content='product', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_ai AFTER INSERT ON product BEGIN
        INSERT INTO product_fts (rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES (new.id, new.name, new.brand,
                new.category_l1, new.category_l2, new.category_l3,
                new.norm_name, new.norm_cat);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_ad AFTER DELETE ON product BEGIN
        INSERT INTO product_fts (product_fts, rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES ('delete', old.id, old.name, old.brand,
                old.category_l1, old.category_l2, old.category_l3,
                old.norm_name, old.norm_cat);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS product_fts_au AFTER UPDATE ON product BEGIN
        INSERT INTO product_fts (product_fts, rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES ('delete', old.id, old.name, old.brand,
                old.category_l1, old.category_l2, old.category_l3,
                old.norm_name, old.norm_cat);
        INSERT INTO product_fts (rowid, name, brand,
                                 category_l1, category_l2, category_l3,
                                 norm_name, norm_cat)
        VALUES (new.id, new.name, new.brand,
                new.category_l1, new.category_l2, new.category_l3,
                new.norm_name, new.norm_cat);
    END
    """,
]

#: Reihenfolge der Spalten in `product_fts`. bm25() bekommt seine Gewichte
#: positionsweise — steht hier etwas anderes als oben, gewichtet die Suche
#: stillschweigend die falsche Spalte.
FTS_SPALTEN = ("name", "brand", "category_l1", "category_l2", "category_l3",
               "norm_name", "norm_cat")

FTS_TRIGGER = ("product_fts_ai", "product_fts_ad", "product_fts_au")


#: Jede Tabelle, die `migrate()` anlegt. Der Test prüft gegen genau diese Liste,
#: damit eine vergessene Tabelle auffällt und nicht erst im Betrieb.
TABLES = (
    "product", "orders", "order_item", "recipe", "recipe_item",
    "recipe_ingredient", "dish", "dish_treffer",
    "chat_message", "chat_suggestion", "chat_kandidat", "chat_sorte",
    "chat_entwurf", "chat_rezept",
    "receipt", "receipt_item",
    "scrape_run", "product_fts",
)


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Öffnet die Datenbank und richtet sie ein.

    `:memory:` wird durchgereicht, damit Tests ohne Datei auskommen. Bei einem
    Dateipfad wird das übergeordnete Verzeichnis angelegt.
    """
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    # Ohne das setzt SQLite Fremdschlüssel nicht durch — und ON DELETE CASCADE
    # oben wäre stille Dekoration.
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def _spalten(con: sqlite3.Connection, tabelle: str,
             pragma: str = "table_info") -> list[str]:
    """Spaltennamen einer Tabelle.

    `table_xinfo` statt `table_info` für `product`: VIRTUAL generierte Spalten
    tauchen in `table_info` gar nicht auf — ohne das hielte die Migration sie
    für fehlend und liefe beim zweiten Aufruf in «duplicate column name».
    """
    return [r[1] for r in con.execute(f"PRAGMA {pragma}({tabelle})")]


def _norm_spalten_nachziehen(con: sqlite3.Connection) -> None:
    """Ergänzt `product` um die normalisierten Suchspalten, falls sie fehlen.

    Bewusst per ALTER statt im CREATE TABLE: so gibt es einen Codepfad für die
    frische und die gewachsene Datenbank, und `CREATE TABLE IF NOT EXISTS`
    kann eine bestehende Tabelle nicht stillschweigend übergehen.
    """
    vorhanden = _spalten(con, "product", "table_xinfo")
    for name, ausdruck in NORM_SPALTEN:
        if name not in vorhanden:
            con.execute(f"ALTER TABLE product ADD COLUMN {name} TEXT"
                        f" GENERATED ALWAYS AS ({ausdruck}) VIRTUAL")



#: Spalten, die zu einer bestehenden Tabelle nachgetragen werden müssen.
#: `CREATE TABLE IF NOT EXISTS` sieht eine vorhandene Tabelle gar nicht an —
#: eine Datenbank aus der Zeit vor dem Ticket bekäme die Spalte sonst nie und
#: fiele erst im Betrieb mit „no such column" auf.
NACHGETRAGENE_SPALTEN = (
    # WB-359: die Korrektur und der Hinweis, dass nur der allgemeinste
    # Kettenbegriff etwas gefunden hat.
    ("chat_suggestion", "corrected_from",
     "INTEGER REFERENCES chat_suggestion(id) ON DELETE CASCADE"),
    ("chat_suggestion", "fallback_term", "TEXT"),
    # WB-361: der Rückweg. `eingelegt_at` ist der Schutz gegen den
    # Doppeltipp, seit er nicht mehr an `decision` hängen darf.
    ("chat_suggestion", "eingelegt_at", "TEXT"),
    ("chat_suggestion", "zurueckgenommen", "INTEGER"),
    # WB-397: welcher Sammelvorgang diese Zeile entschieden hat. Ohne
    # Nachtrag — die Begründung steht am Schema oben.
    ("chat_suggestion", "sammel_nr", "INTEGER"),
    # WB-338: was zum Kochen gehört und woher das Rezept stammt. Eine
    # Datenbank aus der Zeit davor hat `recipe` bereits — `CREATE TABLE IF
    # NOT EXISTS` sähe sie gar nicht an, und die Rezeptansicht fiele mit
    # „no such column: instructions" um.
    ("recipe", "instructions", "TEXT"),
    ("recipe", "prep_minutes", "INTEGER"),
    ("recipe", "cook_minutes", "INTEGER"),
    ("recipe", "rest_minutes", "INTEGER"),
    ("recipe", "difficulty", "INTEGER"),
    ("recipe", "source", "TEXT"),
    ("recipe", "source_id", "TEXT"),
    ("recipe", "source_url", "TEXT"),
    ("recipe", "source_title", "TEXT"),
    ("recipe", "source_rating", "REAL"),
    ("recipe", "source_votes", "INTEGER"),
    ("recipe", "fetched_at", "TEXT"),
    # WB-362: die benötigte Menge neben der Packungszahl. Ohne diese drei
    # Spalten liesse sich im Nachhinein nicht zusammenzählen — die
    # Begründungen stehen am Schema oben.
    ("order_item", "need_amount", "REAL"),
    ("order_item", "need_unit", "TEXT"),
    ("order_item", "hand_qty", "INTEGER"),
    ("recipe_item", "amount", "REAL"),
    ("recipe_item", "unit", "TEXT"),
    # WB-369: die benötigte Menge auch am CHAT-Vorschlag. Ohne sie blieb der
    # produktive Weg (Chat -> Chefkoch -> Korb) bei der geratenen
    # Packungszahl, und die Rechnung aus WB-362 lief dort leer.
    #
    # Ohne Nachtrag für den Altbestand, und das ist die richtige Antwort:
    # eine Zeile aus der Zeit davor HAT keine benötigte Menge — ihre `qty`
    # war eine Vermutung des Modells und lässt sich nicht in Gramm
    # zurückrechnen. NULL heisst hier „unbekannt" und nicht „null Gramm".
    ("chat_suggestion", "need_amount", "REAL"),
    ("chat_suggestion", "need_unit", "TEXT"),
    # WB-337: gehört diese Zeile zum Gericht des Zugs?
    #
    # **Drei Zustände in einer Spalte, und alle drei werden gebraucht:**
    #
    #   NULL  die Zeile gehört zu keinem Gericht. „Klopapier" im Satz
    #         „alles für Spaghetti Bolognese, und Klopapier" — und jede
    #         Zeile jedes Zugs, der gar kein Gericht erkannt hat. Der
    #         Normalfall, und darum die richtige Vorgabe für den Altbestand:
    #         eine Zeile aus der Zeit vor diesem Ticket gehört zu keinem
    #         Rezeptentwurf, denn es gab keinen.
    #   1     die Zeile ist eine Zutat des Gerichts und geht ins Rezept.
    #   0     sie war es, wurde aber von Hand aus dem Entwurf genommen.
    #
    # Die 0 ist nicht dasselbe wie NULL, und deshalb steht hier keine
    # Ja/Nein-Spalte: der Weg zurück („doch wieder hinein") braucht das
    # Wissen, dass die Zeile einmal dazugehörte. Dieselbe Überlegung wie beim
    # Rückweg aus WB-361 — eine Entscheidung ist ein Tipp und kein Urteil.
    #
    # Ausdrücklich HIER und nicht am Entwurf: an dieser Zeile hängt seit
    # Spec 8.1 auch das Eval-Label. Rezept und Label müssen dieselbe Zeile
    # überleben, sonst ist nach einem „Nein" nicht mehr zu sagen, welche
    # Zutat gemeint war.
    ("chat_suggestion", "dish_item", "INTEGER"),
    # WB-373: „gab's nicht". Ohne Nachtrag für den Altbestand, und zwar
    # richtigerweise: ein Posten aus der Zeit davor wurde entweder abgehakt
    # oder er steht noch offen — vermisst wurde keiner, denn es gab den Weg
    # nicht. NULL ist hier die Wahrheit und keine Lücke.
    ("order_item", "missing_at", "TEXT"),
    # WB-387: welches Gericht das Rezept eines Zugs vertreten hat. Ohne
    # Nachtrag für den Altbestand, und richtigerweise: die 270 Züge, die die
    # Datenbank schon trägt, entstanden ohne gespeicherte Trefferliste — es
    # gäbe dort nichts anzubieten, und ein geratener Verweis behauptete eine
    # Herkunft. NULL heisst hier „dazu wurde nichts mitgeschrieben".
    ("chat_rezept", "dish_id",
     "INTEGER REFERENCES dish(id) ON DELETE SET NULL"),
    # WB-384: die Portionszahl, für die ein Chat-Zug rechnet. Ohne Nachtrag
    # für den Altbestand, und richtigerweise: an keinem der 270 Züge hat je
    # jemand eine Portionszahl gewählt — es gab kein Feld. NULL heisst „es
    # gilt die Zahl des Rezepts" und ist damit dieselbe Auskunft wie vor dem
    # Ticket.
    ("chat_rezept", "portionen", "INTEGER"),
)


def _spalten_nachziehen(con: sqlite3.Connection) -> set[tuple[str, str]]:
    """Trägt fehlende Spalten an bestehenden Tabellen nach.

    Nur ADD COLUMN, und nur mit `NULL` als Vorgabe: das ist die einzige
    Änderung, die SQLite ohne Tabellenkopie beherrscht, und die einzige, die
    ein älterer Prozess auf derselben Datei überlebt — der laufende Shop
    schreibt seine Spalten weiter namentlich und merkt von der neuen nichts.

    Gibt zurück, was tatsächlich neu entstanden ist. Eine Spalte, die nur mit
    NULL beginnen kann, braucht manchmal einen Anfangswert aus dem
    vorhandenen Bestand — und der darf genau einmal gesetzt werden, nicht bei
    jedem `migrate()`.
    """
    neu = set()
    for tabelle, name, typ in NACHGETRAGENE_SPALTEN:
        if name not in _spalten(con, tabelle):
            con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {typ}")
            neu.add((tabelle, name))
    return neu


def _eingelegt_nachtragen(con: sqlite3.Connection) -> None:
    """Füllt `eingelegt_at` für Zeilen, die vor WB-361 schon im Korb lagen.

    Ohne das wäre der Schutz gegen den Doppeltipp für genau die Zeilen
    ausgehebelt, die eine gewachsene Datenbank schon enthält: ein altes „Ja"
    hat `decision = 'kept'`, aber `eingelegt_at IS NULL` — zurücknehmen und
    noch einmal „Ja" legte ein zweites Mal ein. `decided_at` ist der richtige
    Wert dafür: es ist der Zeitpunkt, an dem eingelegt wurde, denn vor diesem
    Ticket fielen beide zusammen.
    """
    con.execute("UPDATE chat_suggestion SET eingelegt_at = decided_at"
                " WHERE decision = 'kept' AND eingelegt_at IS NULL")


def _hand_menge_nachtragen(con: sqlite3.Connection) -> None:
    """Füllt `hand_qty` für Posten, die vor WB-362 im Korb lagen.

    Vor diesem Ticket war `qty` eine EINGABE: jede Zahl im Korb stand dort,
    weil jemand sie gesetzt oder ein Rezept sie mitgebracht hat — nichts davon
    war aus einer Menge gerechnet. Genau das ist die Bedeutung von `hand_qty`,
    also ist `qty` der richtige Anfangswert.

    Bliebe die Spalte NULL, zählte der Altbestand als „keine Packung
    verlangt": ein Korb mit zwei Päckchen Butter fiele beim nächsten Rezept,
    das 50 g Butter braucht, auf eines zurück. Ein stillschweigend
    verschwundener Posten ist der schlimmste Ausgang einer Migration.
    """
    con.execute("UPDATE order_item SET hand_qty = qty WHERE hand_qty IS NULL")


def _tabellen(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _zug_rezepte_nachtragen(con: sqlite3.Connection) -> None:
    """Füllt `chat_rezept` für Züge, die vor WB-383 entstanden sind.

    Für den Quellenweg ist die Antwort schon da: `chat_entwurf.recipe_id`
    zeigt auf genau das Rezept, das dieser Zug vorgeschlagen hat. Ohne diesen
    Nachtrag stünde die neue Rezeptkarte an keinem einzigen der 270 Züge, die
    die Datenbank schon trägt — und das Ticket wäre für den vorhandenen
    Bestand wirkungslos.

    Für die Züge des REZEPTWEGES gibt es nichts nachzutragen: dort wurde nie
    festgehalten, welches Rezept getroffen wurde, und es aus dem Satz noch
    einmal zu erraten hiesse, eine Herkunft zu behaupten. Sie bleiben ohne
    Karte — das ist die Wahrheit über eine Zeit, in der niemand mitschrieb.
    """
    con.execute(
        "INSERT OR IGNORE INTO chat_rezept (chat_message_id, recipe_id, pos)"
        " SELECT chat_message_id, recipe_id, 0 FROM chat_entwurf"
        "  WHERE recipe_id IS NOT NULL")


def _fts_nachziehen(con: sqlite3.Connection) -> bool:
    """Wirft einen veralteten FTS-Index weg. Gibt zurück, ob neu gebaut wurde.

    `CREATE VIRTUAL TABLE IF NOT EXISTS` ändert eine bestehende Tabelle nicht,
    und ein Index ohne die norm-Spalten fände die Umlautschreibweisen nie.
    Ein Index ist reine Ableitung aus `product` — ihn wegzuwerfen und aus dem
    Inhalt neu aufzubauen kostet nichts ausser Zeit.
    """
    vorhanden = _spalten(con, "product_fts")
    if not vorhanden or list(vorhanden) == list(FTS_SPALTEN):
        return False
    for trigger in FTS_TRIGGER:
        # Die alten Trigger kennen die neuen Spalten nicht und würden nach
        # CREATE TRIGGER IF NOT EXISTS unverändert stehen bleiben.
        con.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    con.execute("DROP TABLE product_fts")
    return True


def migrate(con: sqlite3.Connection) -> None:
    """Legt das vollständige Schema an. Mehrfach aufrufbar."""
    # Vor dem Anlegen gefragt, sonst gibt es die Tabelle gleich und der
    # Nachtrag liefe entweder nie oder bei jedem Start.
    rezeptzuege_neu = "chat_rezept" not in _tabellen(con)
    for stmt in SCHEMA:
        con.execute(stmt)
    _norm_spalten_nachziehen(con)
    neue_spalten = _spalten_nachziehen(con)
    if ("chat_suggestion", "eingelegt_at") in neue_spalten:
        _eingelegt_nachtragen(con)
    if ("order_item", "hand_qty") in neue_spalten:
        _hand_menge_nachtragen(con)
    if rezeptzuege_neu:
        _zug_rezepte_nachtragen(con)
    neu_gebaut = _fts_nachziehen(con)
    for stmt in FTS_SCHEMA:
        con.execute(stmt)
    if neu_gebaut:
        con.execute("INSERT INTO product_fts (product_fts) VALUES ('rebuild')")
    con.commit()


def has_fts5() -> bool:
    """Ist FTS5 in dieses SQLite einkompiliert?

    Auf dieser Maschine ja (sqlite 3.37.2, in Spec-Anhang A verifiziert). Der
    Aufruf gibt einem Prozess auf einer fremden Maschine trotzdem eine
    verständliche Antwort statt eines rohen OperationalError.
    """
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()
