"""Die Entscheidungen der Nutzerin als Annotationen in Phoenix (Spec 8.1).

**Das Eval-Label fällt aus dem Produkt heraus.** Sie bestätigt oder verwirft
jeden Vorschlag einzeln, weil sie das ohnehin tun muss, bevor sie bestellt.
Damit entsteht Ground Truth ohne einen einzigen Annotationsauftrag: echtes
menschliches Urteil, nicht ein LLM-Judge, der einen anderen LLM benotet.
Deshalb steht an jeder Annotation `annotator_kind = "HUMAN"` — das ist kein
Formfeld, sondern der ganze Wert dieser Daten. Wer in Phoenix nach
`annotator_kind = HUMAN` filtert, bekommt genau die Urteile, die ein Mensch
gefällt hat, weil er einkaufen wollte.

Geschrieben wird auf den `chat.turn`-Span des Zugs (`chat_message.span_id`,
gefüllt in WB-328). Zwei Sorten:

* `mapping_precision` — ein Score je Zug: behaltene / entschiedene Artikel.
* `suggestion` — eine Annotation je Vorschlag, Label `kept` oder `removed`,
  **mit dem Suchbegriff als Erklärung**. Damit steht im Trace direkt an der
  Zeile, welcher Begriff schlecht gemappt hat; zusammen mit dem
  `catalog.search`-Span desselben Begriffs (WB-328) ist die Frage „Modell
  oder Suche?" ohne Zusatzarbeit beantwortet.
* `recipe` — eine Annotation je Zug, aus dem ein Rezept geworden ist
  (WB-337). Sie beantwortet die Frage, die sonst niemand mehr beantworten
  kann: **warum nimmt dieses Gericht ab jetzt den schnellen Weg?** Am Zug
  steht `zettel.dish_draft` („daraus KANN ein Rezept werden"); ob es
  wirklich eines wurde, entscheidet sich erst beim Abschicken, und genau da
  fällt auch diese Annotation an.
* `correction` — eine Annotation je Korrektur (WB-359): **welcher Kandidat
  statt welchem**. Das macht aus einem negativen Label ein positives — nicht
  nur „das war falsch", sondern „das wäre richtig gewesen", und zwar mit einer
  ID, die in derselben Vorlage stand. Für die Evals aus WB-330 ist das der
  wertvollste Datenpunkt überhaupt, weil er die richtige Antwort benennt statt
  nur die falsche zu zählen.

**Ein zurückgenommener Tipp hinterlässt kein Label** (WB-361). Er kann keines
hinterlassen: geschrieben wird erst beim Abschicken, und wer zurückgenommen
hat, steht dann auf `offen` — und `offen` bekommt nichts (Punkt 2 unten).
Genau deshalb steht `withdrawn` in den Metadaten: an der einzelnen Annotation,
wie oft an dieser Zeile zurückgenommen wurde, und an `mapping_precision` die
Summe für den Zug. Ohne diese Zahl wäre der Fehltipp restlos unsichtbar, und
niemand könnte fragen, wie oft auf dem Handy danebengetippt wird. Ein Label
ist es trotzdem nicht: „zurückgenommen" ist kein Urteil über den Vorschlag.

Vier Entscheidungen, die die Zahlen ehrlich halten:

1. **Erst beim Abschicken** (`orders.abschicken`), nicht beim Tippen. Bis
   dahin kann sie ihre Meinung ändern, und ein `kept`, das zwei Minuten
   später ein `removed` wird, wäre sonst als Label schon draussen.

2. **`offen` zählt nicht mit.** Ein Vorschlag, über den nie entschieden
   wurde, steht weder im Zähler noch im Nenner — sonst zählte jeder Abbruch
   als Fehler des Modells. Die Rechnung macht `vorschlaege.quote()`, und zwar
   genau einmal im Projekt.

3. **Ein Zug mit ausschliesslich offenen Vorschlägen bekommt gar keinen
   Score.** Eine fehlende Zahl ist ehrlicher als eine erfundene: 0.0 hiesse
   „alles falsch", wo „noch nichts gesagt" richtig ist, und ein Mittelwert
   über solche Nullen wäre still kaputt.

4. **`identifier` verhindert Dubletten.** Phoenix schlüsselt eine Annotation
   über (Name, span_id, identifier); derselbe Schlüssel wird aktualisiert und
   nicht danebengelegt. Die Identifier hier kommen aus den Datenbank-IDs
   (`zettel-turn-<chat_message_id>`, `zettel-suggestion-<id>`), sind also
   für dieselbe Sache immer dieselben. Zweimal schreiben — durch einen
   wiederholten Aufruf oder ein wiederholtes Abschicken — ergibt denselben
   Bestand, nicht den doppelten.

**Der Nutzerweg wird davon nicht aufgehalten.** Das ist ein ANDERER Fehlerpfad
als in WB-328: Annotationen gehen über den HTTP-Client von Phoenix, nicht über
den Tracer — der `NichtBlockierend`-Prozessor aus `obs.otel` greift hier
nicht. Gemessen am 2026-08-28 gegen `arize-phoenix-client` 3.3.0:

    Port zu (Connection refused) ..............   0,001 s
    Host antwortet nicht (Paket verschwindet) .  10,0   s  (connect-Timeout)
    Phoenix nimmt an und antwortet nie ........  30,0   s  (read-Timeout)

Der erste Fall ist harmlos, die beiden anderen sind es nicht — und genau sie
treten auf, wenn Phoenix auf einer anderen Maschine läuft oder hängt statt zu
sterben. Ein „das scheitert doch sofort" wäre also dieselbe falsche Annahme
wie beim OTLP-Exporter, nur mit anderen Zahlen. Deshalb sammelt `schreiben()`
die Annotationen im aufrufenden Thread aus der Datenbank (Mikrosekunden, und
die sqlite-Verbindung bleibt, wo sie hingehört) und gibt sie einem
Hintergrund-Thread. **Die Bestellung geht durch, egal was Phoenix macht.**
"""
from __future__ import annotations

import logging
import os
import threading

from zettel import umgebung
from zettel.obs import otel

log = logging.getLogger(__name__)

#: Der Score je Zug. Englisch wie die `zettel.*`-Attribute (Spec 7.1): er
#: wird in Phoenix gefiltert und in Evals verglichen, nicht im Shop gelesen.
NAME_QUOTE = "mapping_precision"

#: Die Einzelentscheidung. Label ist wörtlich `chat_suggestion.decision`, also
#: `kept` oder `removed` — dieselben Wörter in Datenbank und Phoenix, damit
#: eine Auswertung nicht übersetzen muss.
NAME_ENTSCHEIDUNG = "suggestion"

#: Die Korrektur (WB-359) — und das ist die wertvollste Annotation, die dieses
#: Projekt erzeugt. `suggestion=removed` sagt „das war falsch". Diese hier sagt
#: **welcher Kandidat statt welchem**, und damit sagt sie, was richtig gewesen
#: wäre. Für eine Eval ist der Unterschied qualitativ: aus „Trefferquote 0,75"
#: wird „bei ‚Butter‘ wurde konsequent auf Weihenstephan korrigiert" — eine
#: Fehleranalyse mit der richtigen Antwort daneben, ohne einen einzigen
#: Annotationsauftrag.
#:
#: Ein EIGENER Name und nicht `suggestion` mit Label `kept`: unter demselben
#: Namen ginge die Korrektur in den Mittelwert der Mapping-Präzision ein und
#: hübe ausgerechnet die Zahl, die den Fehlgriff misst. Sie steht deshalb
#: daneben, und der Mittelwert über `suggestion` bleibt, was er war.
NAME_KORREKTUR = "correction"

#: Die beiden Ausgänge einer Korrektur. `corrected` heisst „aus der Vorlage
#: hätte das Modell das Richtige nehmen können" — ein Modellfehler. `free_text`
#: heisst „in der Vorlage stand es gar nicht" — eine Katalog-Lücke, und die ist
#: keinem Modell anzulasten. Die beiden auseinanderzuhalten ist der ganze Sinn
#: des Labels; als eine Sorte wäre die Lücke von einem Fehlgriff nicht mehr zu
#: unterscheiden.
LABEL_KORRIGIERT = "corrected"
LABEL_FREITEXT = "free_text"

#: Das Rezept, das aus einem Zug entstanden ist (WB-337). Ein eigener Name
#: und kein Score: „ein Rezept ist entstanden" ist keine Bewertung des
#: Vorschlags und hat in keinem Mittelwert etwas zu suchen. Sie steht
#: trotzdem hier und nicht bloss am Span, weil sie zum selben Zeitpunkt
#: entsteht wie die Labels — beim Abschicken — und weil sie erst dann wahr
#: ist.
NAME_REZEPT = "recipe"

#: Die drei Ausgänge eines Entwurfs. `dropped` heisst „sie wollte kein
#: Rezept", `empty` „es blieb keine bestätigte Zutat übrig" — der
#: Unterschied ist die interessante Hälfte: das eine ist eine Entscheidung,
#: das andere ein Zug, der nichts Brauchbares gefunden hat.
LABEL_GESPEICHERT = "saved"
LABEL_VERWORFEN = "dropped"
LABEL_LEER = "empty"

#: Kein LLM-Judge. Siehe Modul-Docstring.
MENSCH = "HUMAN"

#: Wie lange ein Hintergrund-Thread höchstens auf sich warten lässt, wenn
#: jemand `abwarten()` ruft (Skripte und Tests, nicht der Web-Prozess).
FRIST_S = 40.0

_threads: list[threading.Thread] = []
_sperre = threading.Lock()


# --------------------------------------------------------------------------
# Was geschrieben werden soll — ohne Netz, ohne Phoenix

def annotationen(con, order_id: int) -> list[dict]:
    """Die Annotationen zu einer Bestellung. Rein rechnend, schreibt nichts.

    Getrennt vom Senden, weil das hier die interessante Hälfte ist: welche
    Zahl aus welchen Entscheidungen entsteht, lässt sich so ohne laufendes
    Phoenix prüfen (`tests/test_labels.py`).

    Züge ohne `span_id` fallen weg — ohne Span gibt es nichts zu annotieren.
    Das ist der Normalfall, wenn der Zug lief, während Phoenix aus war
    (`ZETTEL_TRACING=0`): die Entscheidungen stehen trotzdem in der
    Datenbank, sie haben bloss keinen Trace, an den sie gehören.
    """
    # Der Import steht hier und nicht oben: `assistant.vorschlaege` importiert
    # `zettel.orders`, und `orders.korb` importiert dieses Modul, um beim
    # Abschicken zu schreiben. Auf Modulebene wäre das ein Ringschluss beim
    # Import — hier ist es keiner, weil beide Pakete längst geladen sind, wenn
    # jemand annotiert.
    from zettel.assistant import entwurf, vorschlaege

    raus: list[dict] = []
    zuege = con.execute(
        "SELECT DISTINCT m.id AS id, m.span_id AS span_id"
        "  FROM chat_message m"
        "  JOIN chat_suggestion s ON s.chat_message_id = m.id"
        " WHERE m.order_id = ? AND m.span_id IS NOT NULL"
        " ORDER BY m.id", (order_id,)).fetchall()

    for zug in zuege:
        span_id, msg_id = zug["span_id"], int(zug["id"])
        q = vorschlaege.quote(con, msg_id)
        zeilen = vorschlaege.liste(con, msg_id)
        # Wie oft in diesem Zug eine Entscheidung zurückgenommen wurde
        # (WB-361). Korrekturzeilen zählen mit: eine zurückgenommene Korrektur
        # ist genauso ein Fehltipp. Diese Zahl ist die EINZIGE Spur, die ein
        # zurückgenommener Tipp hinterlässt — ein Label bekommt er bewusst
        # keines (siehe unten), und ohne sie sähe niemand, wie oft auf dem
        # Handy danebengetippt wird.
        zurueck = sum(v["zurueckgenommen"] for v in zeilen)
        if q["quote"] is not None:
            # Kein Label, nur ein Score: „0.75" ist die Aussage, und eine
            # danebengestellte Textmarke („gut"/„schlecht") wäre eine
            # Schwelle, die niemand festgelegt hat.
            raus.append(_anno(
                span_id, NAME_QUOTE,
                score=float(q["quote"]),
                explanation=_quote_satz(q, zurueck),
                identifier=f"zettel-turn-{msg_id}",
                metadata={"order_id": order_id, "chat_message_id": msg_id,
                          "suggested": q["vorgeschlagen"],
                          "kept": q["behalten"], "removed": q["verworfen"],
                          "open": q["offen"], "withdrawn": zurueck}))

        rezept = _rezept_anno(entwurf.zu_nachricht(con, msg_id, zeilen),
                              span_id, order_id, msg_id)
        if rezept is not None:
            raus.append(rezept)

        for v in zeilen:
            if v["ist_korrektur"]:
                # Eine Korrekturzeile ist kein Vorschlag des Modells, sondern
                # die Handbewegung danach (WB-359). Sie bekommt ihre eigene
                # Annotation — unter `suggestion` mitgezählt hübe sie die
                # Mapping-Präzision genau dann, wenn die Nutzerin einen
                # Fehlgriff geradezieht.
                #
                # Eine zurückgenommene Korrektur (sie hat auch bei der
                # Alternative „Nein" gesagt) sagt nichts darüber, was richtig
                # gewesen wäre — sie bekommt gar keine Annotation, statt eine
                # Behauptung zu schreiben, die die Nutzerin widerrufen hat.
                if v["decision"] == vorschlaege.BEHALTEN:
                    raus.append(_korrektur_anno(v, span_id, order_id, msg_id))
                continue
            if v["decision"] == vorschlaege.OFFEN:
                # Nie entschieden heisst nie beurteilt. Eine Annotation dafür
                # wäre eine Behauptung über etwas, das die Nutzerin gar nicht
                # angesehen hat.
                continue
            behalten = v["decision"] == vorschlaege.BEHALTEN
            # Nur eine Korrektur, die auch im Korb liegt, sagt etwas darüber,
            # was richtig gewesen wäre. Eine zurückgenommene sagt nichts.
            k = v["korrektur"] if (v["korrektur"]
                                   and v["korrektur"]["behalten"]) else None
            raus.append(_anno(
                span_id, NAME_ENTSCHEIDUNG,
                label=v["decision"],
                # 1/0 neben dem Label, damit Phoenix mitteln kann: der
                # Mittelwert über alle `suggestion`-Annotationen IST die
                # Mapping-Präzision über alle Züge hinweg.
                score=1.0 if behalten else 0.0,
                explanation=_begriff(v),
                identifier=f"zettel-suggestion-{v['id']}",
                metadata={"order_id": order_id, "chat_message_id": msg_id,
                          "suggestion_id": v["id"],
                          "search_term": v["search_term"],
                          "product_id": v["product_id"],
                          "product": v["name"],
                          # Der FTS-Rang der Suche, die diesen Vorschlag
                          # hervorgebracht hat. Damit lässt sich später
                          # fragen, ob verworfene Vorschläge systematisch aus
                          # schwachen Suchen kommen — die Frage aus WB-328,
                          # jetzt mit menschlichem Urteil daneben.
                          "rank": v["rang"],
                          # Nur über den allgemeinsten Kettenbegriff gefunden
                          # (WB-359)? Dann ist ein Fehlgriff kein Rätsel,
                          # sondern eine Katalog-Lücke mit einem Auffangbegriff
                          # dahinter — und er gehört an die Annotation, nicht
                          # in eine spätere Vermutung.
                          "fallback_term": v["fallback_term"],
                          # Und wenn korrigiert wurde: WAS statt dessen. Damit
                          # trägt schon das negative Label die richtige
                          # Antwort bei sich, ohne dass eine Auswertung zwei
                          # Annotationen zusammensuchen muss.
                          "corrected_to": _name(k),
                          "corrected_to_product_id": (
                              k["product_id"] if k else None),
                          "corrected_to_suggestion_id": k["id"] if k else None,
                          # Wie oft an DIESER Zeile zurückgenommen wurde
                          # (WB-361). Ein `kept` mit `withdrawn = 1` ist ein
                          # anderer Datenpunkt als ein `kept` beim ersten
                          # Hinsehen — dort hat jemand gezögert oder
                          # danebengetippt, und ein Label, das das
                          # verschweigt, sieht sicherer aus, als es ist.
                          "withdrawn": v["zurueckgenommen"],
                          "free_text": bool(v["ist_freitext"])}))
    return raus


def _rezept_anno(e: dict | None, span_id: str, order_id: int,
                 msg_id: int) -> dict | None:
    """Was aus dem Rezeptentwurf dieses Zugs geworden ist (WB-337).

    `None`, wenn es gar keinen gab — der Normalfall. Ein Entwurf, der beim
    Abschicken noch offen wäre, kommt hier nicht vor: `speichern()` läuft
    vorher, und danach ist jeder Entwurf entschieden.
    """
    if e is None:
        return None
    if e["saved_at"]:
        label = LABEL_GESPEICHERT
        satz = (f"aus diesem Zug wurde das Rezept „{e['name']}“ mit "
                f"{e['n_drin']} Zutaten — ab jetzt nimmt „{e['dish']}“ den "
                "Rezeptweg")
    elif e["verworfen"]:
        label = LABEL_VERWORFEN
        satz = f"kein Rezept aus diesem Zug — „{e['name']}“ wurde verworfen"
    else:
        label = LABEL_LEER
        satz = (f"kein Rezept aus diesem Zug — zu „{e['name']}“ blieb keine "
                "bestätigte Zutat übrig")
    return _anno(
        span_id, NAME_REZEPT,
        label=label,
        explanation=satz,
        identifier=f"zettel-recipe-{msg_id}",
        metadata={"order_id": order_id, "chat_message_id": msg_id,
                  "dish": e["dish"], "recipe_name": e["name"],
                  "recipe_id": e["recipe_id"] if e["saved_at"] else None,
                  "items": e["n_drin"]})


def _korrektur_anno(v: dict, span_id: str, order_id: int,
                    msg_id: int) -> dict:
    """Die Annotation zu einer Korrektur — „X statt Y", nicht nur „nicht Y".

    Sie steht auf demselben `chat.turn`-Span wie alles andere und trägt beide
    Seiten: das verworfene Produkt und das gewählte, mit dem Suchbegriff
    dazwischen. Das ist der Datenpunkt, für den sonst jemand annotieren
    müsste.
    """
    freitext = bool(v["ist_freitext"])
    label = LABEL_FREITEXT if freitext else LABEL_KORRIGIERT
    verworfen = v["statt_name"] or "der Vorschlag"
    if freitext:
        satz = (f"„{verworfen}“ war falsch; nichts aus der Vorlage passte — "
                f"von Hand: „{v['name']}“")
    else:
        satz = (f"„{v['search_term'] or v['name']}“: statt „{verworfen}“ -> "
                f"„{v['name']}“")
    return _anno(
        span_id, NAME_KORREKTUR,
        label=label,
        explanation=satz,
        identifier=f"zettel-correction-{v['id']}",
        metadata={"order_id": order_id, "chat_message_id": msg_id,
                  "suggestion_id": v["id"],
                  # Die Zeile, die falsch war. Über sie hängt die Korrektur an
                  # ihrer `suggestion`-Annotation (`removed`).
                  "corrected_suggestion_id": v["corrected_from"],
                  "rejected_product": v["statt_name"],
                  "search_term": v["search_term"],
                  "product_id": v["product_id"],
                  "product": None if freitext else v["name"],
                  "free_text": v["free_text"],
                  "rank": v["rang"]})


def _name(v: dict | None) -> str | None:
    return None if v is None else v["name"]


def _quote_satz(q: dict, zurueckgenommen: int = 0) -> str:
    satz = (f"{q['behalten']} von {q['behalten'] + q['verworfen']} "
            "entschiedenen Vorschlägen behalten")
    if q["offen"]:
        satz += f"; {q['offen']} offen und nicht gewertet"
    if zurueckgenommen:
        # Steht in der Erklärung und nicht nur in den Metadaten: wer eine
        # Liste von Zügen durchsieht, soll den zurückgenommenen Fehltipp
        # sehen, ohne eine einzelne Annotation aufzuklappen.
        satz += (f"; {zurueckgenommen} Entscheidung"
                 f"{'en' if zurueckgenommen != 1 else ''} zurückgenommen")
    return satz + "."


def _begriff(v: dict) -> str:
    """Die Erklärung an der Einzelannotation: der Suchbegriff.

    Er ist der Grund, warum diese Annotation etwas erklärt und nicht nur
    zählt. Fehlt er (alte Zeile, Rezeptweg ohne Begriff), steht der Name da —
    eine leere Erklärung würde der Phoenix-Client stillschweigend weglassen,
    und dann wäre im Trace nicht zu sehen, worum es ging.

    Seit WB-359 stehen zwei Zusätze mit dran, wenn es sie gibt: worauf
    korrigiert wurde, und ob überhaupt nur der allgemeinste Kettenbegriff
    etwas gefunden hat. Beides gehört in die Erklärung und nicht nur in die
    Metadaten — in der Spanliste von Phoenix sieht man zuerst den Satz.
    """
    satz = v["search_term"] or f"ohne Suchbegriff: {v['name']}"
    if v.get("fallback_term"):
        satz += (f" — nur über den allgemeinen Begriff "
                 f"„{v['fallback_term']}“ gefunden")
    k = v.get("korrektur")
    if k is not None and k["behalten"]:
        satz += f" — stattdessen: „{k['name']}“"
    return satz


def _anno(span_id: str, name: str, *, label: str | None = None,
          score: float | None = None, explanation: str | None = None,
          identifier: str, metadata: dict | None = None) -> dict:
    """Eine Annotation in der Form, die `log_span_annotations` erwartet.

    Genau das Schema der Phoenix-API (`v1/span_annotations`): `result` bündelt
    Label, Score und Erklärung, alles andere steht daneben. `None`-Werte
    fallen weg, weil der Server sie sonst als gesetzte Leere speichert.
    """
    ergebnis: dict = {}
    if label is not None:
        ergebnis["label"] = label
    if score is not None:
        ergebnis["score"] = float(score)
    if explanation:
        ergebnis["explanation"] = explanation
    anno = {"span_id": span_id, "name": name, "annotator_kind": MENSCH,
            "result": ergebnis, "identifier": identifier}
    if metadata:
        anno["metadata"] = {k: w for k, w in metadata.items() if w is not None}
    return anno


# --------------------------------------------------------------------------
# Senden — im Hintergrund, und niemals auf Kosten der Bestellung

def basis_url() -> str:
    """Wo Phoenix steht — abgeleitet vom OTLP-Endpunkt aus `obs.otel`.

    Eine zweite Umgebungsvariable für dieselbe Maschine liefe irgendwann
    auseinander: Spans gingen dann woandershin als die Annotationen, und die
    Annotation fände ihren Span nicht.
    """
    ziel = umgebung.wert(otel.ENV_ENDPUNKT) or otel.ENDPUNKT
    return ziel.split("/v1/traces")[0].rstrip("/")


def klient():
    """Der Phoenix-Client. Die Naht, an der Tests etwas anderes einhängen."""
    from phoenix.client import Client

    return Client(base_url=basis_url())


def schreiben(con, order_id: int, *, client=None) -> list[dict]:
    """Schreibt die Entscheidungen einer Bestellung nach Phoenix.

    Gibt zurück, was übergeben wurde — leer, wenn es nichts zu schreiben gibt
    oder Tracing aus ist. **Wirft nie**: der Aufrufer ist
    `orders.abschicken()`, und eine Bestellung darf an einem Observability-
    Werkzeug nicht scheitern.

    Das Sammeln aus der Datenbank passiert hier, im Thread des Aufrufers — die
    sqlite-Verbindung gehört ihm und darf den Thread nicht wechseln. Nur der
    HTTP-Aufruf wandert in den Hintergrund, mit den fertigen Daten im Gepäck.
    """
    try:
        if client is None and not otel.an():
            # Tracing aus heisst: es gibt keine Spans, auf die diese Labels
            # passen würden. Dann ist Schweigen richtig — und die Testsuite
            # (conftest setzt ZETTEL_TRACING=0) redet nie ungefragt mit
            # einem Phoenix, das auf diesem Rechner tatsächlich läuft.
            return []
        annos = annotationen(con, order_id)
    except Exception as e:  # noqa: BLE001 — siehe Docstring
        log.warning("Annotationen nicht ermittelt (%s: %s).",
                    e.__class__.__name__, e)
        return []
    if not annos:
        return []
    _im_hintergrund(annos, client)
    return annos


def senden(annos: list[dict], client=None) -> bool:
    """Ein HTTP-Aufruf für alle Annotationen. Wirft nie, meldet nur.

    Ein Aufruf und nicht einer je Annotation: die Bündelung ist der Grund,
    warum `log_span_annotations` existiert, und bei einem Zug mit fünf
    Vorschlägen sind das sechs Runden gegen eine.
    """
    if not annos:
        return True
    try:
        c = klient() if client is None else client
        c.spans.log_span_annotations(span_annotations=annos, sync=False)
        log.info("%d Annotationen an Phoenix übergeben.", len(annos))
        return True
    except Exception as e:  # noqa: BLE001
        # Genau der Fall, für den dieser Thread da ist. Phoenix ist weg, die
        # Entscheidungen stehen weiter in `chat_suggestion` — nachtragen kann
        # man sie, die Bestellung wiederholen nicht.
        log.warning("Annotationen nicht geschrieben (%s: %s). Die "
                    "Entscheidungen stehen in der Datenbank.",
                    e.__class__.__name__, e)
        return False


def _im_hintergrund(annos: list[dict], client) -> None:
    t = threading.Thread(target=senden, args=(annos, client),
                         name="zettel-labels", daemon=True)
    with _sperre:
        # Fertige Threads wegräumen, sonst wächst die Liste mit jeder
        # Bestellung — ein kleines, aber echtes Leck in einem Prozess, der
        # monatelang läuft.
        _threads[:] = [x for x in _threads if x.is_alive()]
        _threads.append(t)
    t.start()


def abwarten(frist_s: float = FRIST_S) -> bool:
    """Wartet, bis die Hintergrund-Threads durch sind. Für Skripte und Tests.

    Im Web-Prozess hat das nichts zu suchen: dort ist der Sinn der Übung, dass
    niemand wartet.
    """
    with _sperre:
        laufend = list(_threads)
    for t in laufend:
        t.join(frist_s)
    return not any(t.is_alive() for t in laufend)
