"""Der Span-Vertrag aus Spec 7.1 — und die Frage, die er beantworten soll.

Ein Chat-Zug ergibt genau diesen Baum:

```
CHAIN       chat.turn        input: der Satz der Nutzerin
 ├ LLM      plan.extract     output: [{suchbegriffe, menge}, …]
 ├ RETRIEVER catalog.search  input: die Begriffskette einer Zutat
 ├ RETRIEVER catalog.search  (ein Span je ZUTAT, WB-340) -> n Kandidaten
 ├ LLM      plan.choose      Kandidaten -> gewählte product_ids
 └ output: die Vorschlagsliste
```

**`RETRIEVER` statt `TOOL` ist der Zweck des Ganzen.** Die Katalogsuche *ist*
Retrieval, und mit der Retriever-Semantik von OpenInference
(`retrieval.documents` mit `document.id`, `document.content`,
`document.score`) rendert Phoenix die Kandidaten als Dokumentenliste mit Score
statt als JSON-Klumpen. Damit ist die eine Frage, die sich ohne Tracing nicht
beantworten lässt, auf einen Blick beantwortet:

> **Lag ein Fehlgriff am Modell oder an der Suche?**
>
> Beim Bauen von WB-327 kam der Fall heraus, an dem sich das entscheidet: Auf
> „…dazu brauche ich noch Zahnpasta und Butter" wählte das Modell eine
> handgemachte BIO-Salzbutter für 4,69 €. Ein Fehlgriff — aber alle FÜNF
> vorgelegten Kandidaten waren ButterBoyz-Spezialbutter. Normale Butter stand
> nie zur Wahl. Der Fehler lag an der SUCHE, nicht am Modell, und der Rang
> sagte es vorher: 4,01 bei „Butter" gegen 9,00 bei „Zwiebeln".
>
> Im Trace: den `catalog.search`-Span für „Butter" aufklappen, die fünf
> Dokumente lesen. Steht das Richtige nicht darunter, konnte das Modell es
> nicht wählen — dann ist der Retriever schuld, egal wie die Wahl aussieht.
> Steht es darunter und das Modell nahm ein anderes, ist das Modell schuld.

Damit man dafür nicht erst jeden Ast öffnen muss, trägt der `chat.turn`-Span
die Zusammenfassung: `picknick.weakest_term` und `picknick.weakest_rank` — der
Begriff, dessen bester Treffer am schwächsten war, und dessen Rang. Das ist
die Zahl, die im Butter-Fall 4,01 gewesen wäre. Wer eine Liste von Zügen nach
`picknick.weakest_rank` sortiert, sieht die Retrieval-Probleme zuerst.

**Die Attributnamen unter `picknick.` sind englisch.** Der Code dieses
Projekts ist deutsch, die Spans sind es nicht: sie werden in Phoenix gelesen,
gefiltert und in Evals verglichen, neben `llm.token_count.prompt` und
`retrieval.documents`. Ein Filterausdruck, der auf halbem Weg die Sprache
wechselt, wird falsch getippt. Spec 7.1 gibt mit `picknick.path` denselben
Weg vor.

Hier werden **keine Tokenzahlen** gesetzt. Sie kommen vom `OpenAIInstrumentor`
auf den LLM-Spans, der Eingabe-, Ausgabe- und Cache-Token getrennt hält;
Phoenix rechnet daraus die Kosten (Spec 7.3). Von Hand nachgesetzte Zahlen
wären eine zweite Wahrheit daneben.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from openinference.semconv.trace import (
    DocumentAttributes,
    OpenInferenceMimeTypeValues,
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry import trace as trace_api

from picknick.obs.otel import tracer

KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
CHAIN = OpenInferenceSpanKindValues.CHAIN.value
RETRIEVER = OpenInferenceSpanKindValues.RETRIEVER.value

DOKUMENTE = SpanAttributes.RETRIEVAL_DOCUMENTS
JSON_TYP = OpenInferenceMimeTypeValues.JSON.value
TEXT_TYP = OpenInferenceMimeTypeValues.TEXT.value

#: Spec 7.1. Der einzige englische Name, den die Spec selbst festlegt — die
#: übrigen `picknick.*` folgen ihm.
PFAD = "picknick.path"


# --------------------------------------------------------------------------
# Spans öffnen

@contextmanager
def chain(name: str, *, eingabe: str | None = None):
    """Der `CHAIN`-Span eines Chat-Zugs. Immer benutzbar, auch ohne Phoenix.

    Ohne eingerichteten Tracer ist der Span nicht aufzeichnend: alle Aufrufe
    darauf sind No-Ops, `span_id()` gibt `None`. Der Aufrufer braucht deshalb
    keine Fallunterscheidung.
    """
    with tracer().start_as_current_span(name) as span:
        span.set_attribute(KIND, CHAIN)
        if eingabe is not None:
            setze_eingabe(span, eingabe)
        yield span


@contextmanager
def retriever(name: str, *, suchbegriffe: list[str]):
    """Ein `RETRIEVER`-Span je ZUTAT, mit ihrer ganzen Begriffskette (WB-340).

    Ein Span je Zutat und nicht einer für alle Suchen: die Frage lautet „hat
    die Suche für DIESE Zutat etwas Brauchbares vorgelegt", und die ist an
    einem Sammel-Span nicht mehr zu stellen. Aber auch nicht einer je BEGRIFF:
    seit die Kandidaten mehrerer Begriffe zu einer Liste vereinigt werden, gäbe
    es die vorgelegte Liste an keinem einzelnen Begriffs-Span mehr zu sehen —
    der Baum zerfaserte, und die Vorlage, aus der Stufe 3 wirklich gewählt hat,
    stünde nirgends.

    Die Kette steht deshalb als `input.value` (JSON, in der Reihenfolge vom
    genauesten zum allgemeinsten) und zusätzlich als `picknick.search_terms`
    lesbar am Span; welcher Begriff einen einzelnen Kandidaten gebracht hat,
    steht an dessen Dokument (`dokumente`).
    """
    kette = list(suchbegriffe)
    with tracer().start_as_current_span(name) as span:
        span.set_attribute(KIND, RETRIEVER)
        setze_eingabe(span, kette)
        setze(span, {
            # Der genaueste Begriff steht für die Zutat. `weakest_term` und
            # jede Ablesung „welche Zutat lief schlecht" hängen daran.
            "picknick.term": kette[0] if kette else None,
            "picknick.search_terms": ", ".join(kette) or None,
        })
        yield span


# --------------------------------------------------------------------------
# Attribute

def setze_eingabe(span, wert) -> None:
    """`input.value` — was hineinging."""
    if isinstance(wert, str):
        span.set_attribute(SpanAttributes.INPUT_VALUE, wert)
        span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, TEXT_TYP)
    else:
        span.set_attribute(SpanAttributes.INPUT_VALUE, _json(wert))
        span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, JSON_TYP)


def setze_ausgabe(span, wert) -> None:
    """`output.value` — was herauskam.

    Ein Span ohne Ein- und Ausgabe sieht in der Oberfläche genauso gut aus wie
    ein voller und misst nichts. Deshalb prüft `scripts/trace_probe.py` genau
    diese beiden Felder gegen das echte Phoenix.
    """
    if isinstance(wert, str):
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, wert)
        span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, TEXT_TYP)
    else:
        span.set_attribute(SpanAttributes.OUTPUT_VALUE, _json(wert))
        span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, JSON_TYP)


def setze(span, attribute: dict) -> None:
    """Setzt Attribute und lässt `None` weg.

    OpenTelemetry lehnt `None` als Attributwert ab und protokolliert das —
    ein Feld, das es diesmal nicht gibt (`weakest_rank` auf dem Rezeptweg),
    soll fehlen und nicht als Warnung auffallen.
    """
    for name, wert in attribute.items():
        if wert is None:
            continue
        span.set_attribute(name, wert)


def dokumente(span, treffer: list[dict]) -> None:
    """Schreibt die Kandidaten als `retrieval.documents` mit Score.

    Der `rang` aus `catalog.search` IST der Score: negiertes bm25, also
    positiv und „grösser ist besser". bm25 selbst ist negativ und „kleiner ist
    besser" — als Score übergeben wäre das genau rückwärts lesbar, und ein
    Score, den man rückwärts lesen muss, wird irgendwann rückwärts gelesen.

    Die Dokumente stehen in der Reihenfolge, in der sie dem Modell vorlagen —
    seit WB-340 also nach der Begriffskette und nicht global nach Score. Der
    Score gilt innerhalb eines Begriffs; über Begriffe hinweg sind bm25-Ränge
    nicht vergleichbar (siehe `schwaechste_suche`).

    `document.content` ist das, was das Modell tatsächlich zu sehen bekam
    (vgl. `plan.kandidat_kurz`: Name, Gebinde, Preis) — nicht die ganze
    Produktzeile. Sonst zeigte der Trace eine Vorlage, die es nie gab, und
    ein Fehlgriff sähe unerklärlicher aus, als er ist. Was das Modell NICHT
    sah, aber der Mensch beim Nachsehen braucht, steht in
    `document.metadata` — seit WB-340 auch `via`: der Begriff der Kette, der
    diesen Kandidaten gebracht hat. Ohne ihn ist an einer vereinigten Liste
    nicht mehr abzulesen, WARUM ein Produkt vorlag, und genau das soll der
    Span beantworten.
    """
    for i, p in enumerate(treffer):
        praefix = f"{DOKUMENTE}.{i}."
        rang = p.get("rang")
        setze(span, {
            praefix + DocumentAttributes.DOCUMENT_ID: str(p.get("id")),
            praefix + DocumentAttributes.DOCUMENT_CONTENT: inhalt(p),
            praefix + DocumentAttributes.DOCUMENT_SCORE:
                None if rang is None else float(rang),
            praefix + DocumentAttributes.DOCUMENT_METADATA: _json({
                "via": p.get("via"),
                "marke": p.get("brand"),
                "kategorie": kategorie(p),
                "preis_cent": p.get("price_cents"),
                "vorraetig": bool(p.get("in_stock")),
            }),
        })
    setze(span, {
        "picknick.candidates": len(treffer),
        # Der beste Rang dieser Suche. Ist er niedrig, hat die Suche nichts
        # Passendes gehabt — unabhängig davon, was das Modell daraus macht.
        "picknick.rank_top": rang_top(treffer),
    })
    setze_ausgabe(span, [{"id": p.get("id"), "name": p.get("name"),
                          "rang": p.get("rang"), "via": p.get("via")}
                         for p in treffer])


def inhalt(p: dict) -> str:
    """Ein Produkt als eine Zeile — so, wie es dem Modell vorlag."""
    teile = [str(p.get("name") or "").strip()]
    if p.get("unit_text"):
        teile.append(str(p["unit_text"]).strip())
    cent = p.get("price_cents")
    if cent is not None:
        teile.append(f"{int(cent) / 100:.2f} €".replace(".", ","))
    return " · ".join(t for t in teile if t)


def kategorie(p: dict) -> str:
    return " > ".join(str(p[s]) for s in
                      ("category_l1", "category_l2", "category_l3")
                      if p.get(s))


def rang_top(treffer: list[dict]) -> float | None:
    """Der beste Rang einer Trefferliste, oder `None` bei keinem Treffer."""
    raenge = [float(p["rang"]) for p in treffer if p.get("rang") is not None]
    return max(raenge) if raenge else None


def schwaechste_suche(aufgaben: list[dict]) -> tuple[str | None, float | None]:
    """Der Begriff, dessen bester Treffer am schwächsten war.

    Die Zusammenfassung des Butter-Falls in zwei Attributen (siehe
    Modul-Docstring). Ein Begriff ganz ohne Treffer ist der schwächste
    überhaupt und zählt als Rang 0.0 — er wird ohnehin zum Freitext-Vorschlag,
    aber im Trace soll er nicht besser dastehen als ein schlechter Treffer.

    **Diese Zahl ist ein Hinweis, kein Urteil.** bm25 ist nicht über Abfragen
    hinweg geeicht: ein seltenes Wort bekommt strukturell einen höheren Rang
    als ein häufiges, weil der Rang unter anderem misst, wie ungewöhnlich der
    Begriff im Index ist. „Butter 4,01 gegen passierte Tomaten 16,66" heisst
    also nicht „viermal schlechter", sondern „hier stehen viele ähnlich
    benannte Produkte, die Suche konnte kaum unterscheiden". Für die Frage
    „wo lohnt sich das Nachsehen zuerst" reicht das; als absolutes
    Qualitätsmass taugt es nicht, und eine Eval, die daraus eine Schwelle
    macht, misst den Katalog und nicht den Agenten.
    """
    schlechteste, begriff = None, None
    for a in aufgaben:
        rang = rang_top(a.get("kandidaten") or [])
        rang = 0.0 if rang is None else rang
        if schlechteste is None or rang < schlechteste:
            schlechteste, begriff = rang, a.get("begriff")
    return begriff, schlechteste


# --------------------------------------------------------------------------
# Span-ID

def span_id(span) -> str | None:
    """Die Span-ID als Hex — oder `None`, wenn nicht aufgezeichnet wird.

    Der Haken, an dem `chat_message.span_id` und damit WB-329 hängt: die
    Annotationen aus Spec 8.1 müssen den `chat.turn`-Span dieses Zugs treffen.
    Phoenix erwartet dieselbe Schreibweise, die auch in der Oberfläche steht —
    16 Hexzeichen ohne `0x`.

    Ohne Tracer gibt es keinen Span, und dann soll die Spalte `NULL` bleiben.
    Eine Null-ID (`"0000000000000000"`) wäre schlimmer als nichts: sie sieht
    aus wie ein Verweis und zeigt ins Leere.
    """
    if span is None:
        return None
    kontext = span.get_span_context()
    if not kontext.is_valid:
        return None
    return trace_api.format_span_id(kontext.span_id)


def _json(wert) -> str:
    return json.dumps(wert, ensure_ascii=False, default=str)
