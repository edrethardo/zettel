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
   (`picknick-turn-<chat_message_id>`, `picknick-suggestion-<id>`), sind also
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

from picknick.obs import otel

log = logging.getLogger(__name__)

#: Der Score je Zug. Englisch wie die `picknick.*`-Attribute (Spec 7.1): er
#: wird in Phoenix gefiltert und in Evals verglichen, nicht im Shop gelesen.
NAME_QUOTE = "mapping_precision"

#: Die Einzelentscheidung. Label ist wörtlich `chat_suggestion.decision`, also
#: `kept` oder `removed` — dieselben Wörter in Datenbank und Phoenix, damit
#: eine Auswertung nicht übersetzen muss.
NAME_ENTSCHEIDUNG = "suggestion"

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
    (`PICKNICK_TRACING=0`): die Entscheidungen stehen trotzdem in der
    Datenbank, sie haben bloss keinen Trace, an den sie gehören.
    """
    # Der Import steht hier und nicht oben: `assistant.vorschlaege` importiert
    # `picknick.orders`, und `orders.korb` importiert dieses Modul, um beim
    # Abschicken zu schreiben. Auf Modulebene wäre das ein Ringschluss beim
    # Import — hier ist es keiner, weil beide Pakete längst geladen sind, wenn
    # jemand annotiert.
    from picknick.assistant import vorschlaege

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
        if q["quote"] is not None:
            # Kein Label, nur ein Score: „0.75" ist die Aussage, und eine
            # danebengestellte Textmarke („gut"/„schlecht") wäre eine
            # Schwelle, die niemand festgelegt hat.
            raus.append(_anno(
                span_id, NAME_QUOTE,
                score=float(q["quote"]),
                explanation=_quote_satz(q),
                identifier=f"picknick-turn-{msg_id}",
                metadata={"order_id": order_id, "chat_message_id": msg_id,
                          "suggested": q["vorgeschlagen"],
                          "kept": q["behalten"], "removed": q["verworfen"],
                          "open": q["offen"]}))

        for v in vorschlaege.liste(con, msg_id):
            if v["decision"] == vorschlaege.OFFEN:
                # Nie entschieden heisst nie beurteilt. Eine Annotation dafür
                # wäre eine Behauptung über etwas, das die Nutzerin gar nicht
                # angesehen hat.
                continue
            behalten = v["decision"] == vorschlaege.BEHALTEN
            raus.append(_anno(
                span_id, NAME_ENTSCHEIDUNG,
                label=v["decision"],
                # 1/0 neben dem Label, damit Phoenix mitteln kann: der
                # Mittelwert über alle `suggestion`-Annotationen IST die
                # Mapping-Präzision über alle Züge hinweg.
                score=1.0 if behalten else 0.0,
                explanation=_begriff(v),
                identifier=f"picknick-suggestion-{v['id']}",
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
                          "free_text": bool(v["ist_freitext"])}))
    return raus


def _quote_satz(q: dict) -> str:
    satz = (f"{q['behalten']} von {q['behalten'] + q['verworfen']} "
            "entschiedenen Vorschlägen behalten")
    if q["offen"]:
        satz += f"; {q['offen']} offen und nicht gewertet"
    return satz + "."


def _begriff(v: dict) -> str:
    """Die Erklärung an der Einzelannotation: der Suchbegriff.

    Er ist der Grund, warum diese Annotation etwas erklärt und nicht nur
    zählt. Fehlt er (alte Zeile, Rezeptweg ohne Begriff), steht der Name da —
    eine leere Erklärung würde der Phoenix-Client stillschweigend weglassen,
    und dann wäre im Trace nicht zu sehen, worum es ging.
    """
    return v["search_term"] or f"ohne Suchbegriff: {v['name']}"


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
    ziel = os.environ.get(otel.ENV_ENDPUNKT) or otel.ENDPUNKT
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
            # (conftest setzt PICKNICK_TRACING=0) redet nie ungefragt mit
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
                         name="picknick-labels", daemon=True)
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
