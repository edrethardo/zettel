"""Der Chat-Agent: freier Text -> Vorschläge aus dem echten Katalog (Spec 6).

Drei Module, entlang der einen Regel geschnitten, die alles trägt — **das
Modell erfindet niemals Produkte**:

* `plan.py` — die beiden Modellstufen. Stufe 1 sieht keinen Katalog und kann
  deshalb keinen nennen; Stufe 3 darf nur wählen, was der Shop vorgelegt hat,
  und eine nicht vorgelegte ID wird verworfen statt repariert.
* `rezeptweg.py` — die Abkürzung: trifft der Satz ein gespeichertes Rezept,
  entfallen Modell und Suche.
* `vorschlaege.py` — die Vorschlagsliste und die Entscheidung je Zeile. Diese
  Entscheidung ist das Eval-Label (Spec 8.1), deshalb steht sie von Anfang an
  sauber in `chat_suggestion.decision`.

`chat.Chat.turn()` setzt die drei zusammen und ist der einzige Einstieg, den
die Oberfläche braucht.
"""
from picknick.assistant.chat import (  # noqa: F401
    WEG_LLM, WEG_REZEPT, Chat, ChatFehler, ChatNichtVerfuegbar, Ergebnis)
from picknick.assistant.plan import (  # noqa: F401
    KANDIDATEN, Auswahl, PlanFehler, choose, extract)
from picknick.assistant.rezeptweg import Rezeptweg, erkenne  # noqa: F401
from picknick.assistant.vorschlaege import (  # noqa: F401
    BEHALTEN, OFFEN, VERWORFEN, VorschlagFehler, alle_entscheiden, entscheiden,
    quote, span_setzen, verlauf)

__all__ = [
    "Auswahl", "BEHALTEN", "Chat", "ChatFehler", "ChatNichtVerfuegbar",
    "Ergebnis", "KANDIDATEN", "OFFEN", "PlanFehler", "Rezeptweg", "VERWORFEN",
    "VorschlagFehler", "WEG_LLM", "WEG_REZEPT", "alle_entscheiden", "choose",
    "entscheiden", "erkenne", "extract", "quote", "span_setzen", "verlauf",
]
