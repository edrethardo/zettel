"""Zugang zum lokalen Modell auf der vLLM-Box (Spec 6, „Modellzugang").

Die Box ist ein eigener Rechner im LAN — nicht dieser Laptop. Sie ist
OpenAI-kompatibel, deshalb spricht der Shop sie über das offizielle
`openai`-SDK an und nicht über selbstgebautes HTTP: Spec 7.2 hängt den
Phoenix-Tracer als `OpenAIInstrumentor` genau an dieses SDK. Ein eigener
httpx-Aufruf wäre kürzer und im Trace unsichtbar.

Zwei Dinge stehen hier bewusst NICHT im Code:

* **die Adresse als IP.** Die Box ist am 2026-08-16 von `192.168.2.219` auf
  `vllm-box.local` umgezogen. Jedes Werkzeug mit der alten IP scheiterte
  danach mit „No route to host" — was wie ein Netzwerkfehler aussieht und in
  Wahrheit veraltete Konfiguration ist.
* **das Modellkürzel.** Es hat auf dieser Box schon gewechselt
  (`Qwen3.6-35B-A3B-Instruct` -> `Qwen3.8-27B-Instruct`). Es wird über
  `/v1/models` erfragt, gemerkt, und lässt sich neu erfragen.

Dieses Modul baut beim Erzeugen keine Verbindung auf. Der Web-Prozess darf
beim Start nicht auf das Modell warten (Spec 11): ohne Modell bleibt alles
ausser dem Chat benutzbar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

import openai

ENV_ENDPUNKT = "PICKNICK_LLM_ENDPOINT"
ENV_SCHLUESSEL = "PICKNICK_LLM_API_KEY"

#: Vorgabe laut Spec 6. Ein Name, keine IP — siehe Modul-Docstring.
DEFAULT_ENDPUNKT = "http://vllm-box.local:8000/v1"

#: vLLM prüft den Schlüssel nicht; irgendeine nicht-leere Zeichenkette genügt.
#: Das SDK besteht aber auf einem, sonst sucht es `OPENAI_API_KEY`.
DEFAULT_SCHLUESSEL = "1"

#: Der Port, auf dem die vLLM-Box bedient — hier nur, um die Falle unten zu
#: erkennen.
VLLM_PORT = 8000

#: Die alte Adresse der Box. Steht sie noch in einer Konfiguration, ist das
#: kein Netzwerkfehler, sondern eine Altlast.
ALTE_IP = "192.168.2.219"

#: Verbindungsaufbau ins LAN: entweder es klappt sofort oder die Box schläft.
TIMEOUT_VERBINDUNG_S = 5.0
#: Ein 27B-Modell schreibt Sekunden lang, nicht Millisekunden.
TIMEOUT_ANTWORT_S = 180.0
#: `/v1/models` ist eine Tabellenabfrage. Dauert sie lang, lädt vLLM noch.
TIMEOUT_MODELLE_S = 10.0


class KonfigurationsFehler(RuntimeError):
    """Der Endpunkt kann so nicht stimmen — bevor irgendetwas versucht wird."""


class ModellNichtErreichbar(RuntimeError):
    """Die Box hat nicht geantwortet. Das heisst nicht, dass sie aus ist."""


def pruefe_endpunkt(url: str) -> str:
    """Gibt den Endpunkt zurück, wenn er benutzbar aussieht, sonst Fehler.

    Geprüft wird nichts, was ein Netzzugriff beantworten müsste — nur die
    zwei Schreibweisen, die hier schon Zeit gekostet haben (siehe die
    Konstanten oben). Alles andere wird durchgelassen: ob unter einer Adresse
    wirklich vLLM lauscht, beantwortet `wake.health()` und nicht ein Muster.
    """
    text = (url or "").strip().rstrip("/")
    if not text:
        raise KonfigurationsFehler(
            f"Leerer Modell-Endpunkt. Vorgabe ist {DEFAULT_ENDPUNKT}, "
            f"überschreibbar mit {ENV_ENDPUNKT}.")
    teile = urlsplit(text)
    if teile.scheme not in ("http", "https") or not teile.hostname:
        raise KonfigurationsFehler(
            f"{text!r} ist keine brauchbare Adresse. Erwartet wird etwas wie "
            f"{DEFAULT_ENDPUNKT}.")
    try:
        port = teile.port
    except ValueError:
        raise KonfigurationsFehler(f"{text!r} hat keinen gültigen Port.") from None

    if teile.hostname == ALTE_IP:
        raise KonfigurationsFehler(
            f"{ALTE_IP} war bis zum 2026-08-16 die Adresse der vLLM-Box und ist "
            "es nicht mehr. Ein Aufruf dorthin scheitert mit „No route to host“ "
            "und sieht dann aus wie ein Netzwerkfehler, obwohl nur die "
            f"Konfiguration alt ist. Richtig ist {DEFAULT_ENDPUNKT}.")

    if _ist_loopback(teile.hostname) and port == VLLM_PORT:
        # Die teuerste Falle dieses Aufbaus: die Box ist ein EIGENER Rechner
        # im LAN. Auf diesem Laptop lauscht auf 8000 nichts, jeder Aufruf
        # scheitert mit „Connection error" — was wie ein Modellfehler aussieht
        # und keiner ist. Der einzige lokale Forward, den es gibt, ist der
        # No-Think-Shim auf 8011, und der gehört einem anderen Werkzeug.
        raise KonfigurationsFehler(
            f"{text} zeigt auf diesen Rechner. Auf {teile.hostname}:{VLLM_PORT} "
            "lauscht hier nichts — die vLLM-Box ist ein eigener Rechner im LAN. "
            f"Gemeint ist vermutlich {DEFAULT_ENDPUNKT}.")
    return text


def _ist_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def endpunkt_aus_umgebung(umgebung=None) -> str:
    """Der Endpunkt aus `PICKNICK_LLM_ENDPOINT`, sonst die Vorgabe."""
    umgebung = os.environ if umgebung is None else umgebung
    return pruefe_endpunkt(umgebung.get(ENV_ENDPUNKT) or DEFAULT_ENDPUNKT)


@dataclass(frozen=True)
class Antwort:
    """Eine Modellantwort, Denken und Ergebnis getrennt.

    Auf der Box läuft `--reasoning-parser qwen3`. Das Denken landet dadurch
    nicht in `content`, sondern in einem eigenen Feld — je nach Stand
    `reasoning` oder `reasoning_content`, siehe `_als_antwort()`.
    Genau deshalb bleibt `content` sauber und lässt sich mit Guided Decoding
    (`json_schema`) kombinieren — das braucht der Agent in Spec 6.

    `reasoning_content` wird durchgereicht statt weggeworfen: der Agent
    braucht es nicht, aber wenn eine Zuordnung schiefgeht, steht die
    Begründung darin.
    """
    content: str
    reasoning_content: str | None
    modell: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class Modellzugang:
    """Der Weg zum Modell: Endpunkt, Modellkürzel, Chat-Aufruf.

    Erzeugen kostet nichts — kein Socket, kein Aufruf. Erst `modell()` oder
    `chat()` gehen ins Netz. Das ist die Bedingung dafür, dass der Web-Prozess
    beim Start nicht auf die Box wartet (Spec 11).
    """

    def __init__(self, endpunkt: str | None = None, *,
                 schluessel: str | None = None,
                 client=None, umgebung=None,
                 timeout_s: float = TIMEOUT_ANTWORT_S):
        umgebung = os.environ if umgebung is None else umgebung
        self.endpunkt = (pruefe_endpunkt(endpunkt) if endpunkt is not None
                         else endpunkt_aus_umgebung(umgebung))
        self.schluessel = (schluessel or umgebung.get(ENV_SCHLUESSEL)
                           or DEFAULT_SCHLUESSEL)
        self.timeout_s = timeout_s
        # Injizierbar, damit Tests einen HTTP-Doppelgänger unterschieben
        # können, ohne die Box zu wecken.
        self._client = client
        self._modell: str | None = None

    @property
    def client(self):
        """Der SDK-Client. Wird erst beim ersten Aufruf gebaut."""
        if self._client is None:
            self._client = openai.OpenAI(
                base_url=self.endpunkt,
                api_key=self.schluessel,
                timeout=openai.Timeout(
                    connect=TIMEOUT_VERBINDUNG_S, read=self.timeout_s,
                    write=TIMEOUT_VERBINDUNG_S, pool=TIMEOUT_VERBINDUNG_S),
                # Kein automatischer Neuversuch. Eine schlafende Box antwortet
                # auch beim dritten Mal nicht, und jeder Versuch verlängert nur
                # die Zeit, bis die Oberfläche „Modell wacht auf" sagen kann.
                # Das Wiederholen ist Sache von `wake.Wecker`.
                max_retries=0,
            )
        return self._client

    def modell(self, *, neu: bool = False) -> str:
        """Das Modellkürzel der Box, gemerkt. `neu=True` fragt wieder nach.

        Nie festgeschrieben: das Kürzel hat auf dieser Box schon gewechselt.
        Gemerkt wird es trotzdem, weil sonst jeder Chat-Zug eine zusätzliche
        Anfrage kostet — und weil ein Wechsel einen Neustart von vLLM bedeutet,
        also ohnehin einen Bruch.
        """
        if self._modell is None or neu:
            self._modell = self._erfrage_modell()
        return self._modell

    def _erfrage_modell(self) -> str:
        try:
            liste = self.client.models.list(timeout=TIMEOUT_MODELLE_S)
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ModellNichtErreichbar(_schlaeft_wohl(self.endpunkt, e)) from e
        except openai.APIError as e:
            raise ModellNichtErreichbar(
                f"{self.endpunkt}/models antwortete mit einem Fehler: {e}") from e
        namen = [m.id for m in (liste.data or []) if getattr(m, "id", None)]
        if not namen:
            raise ModellNichtErreichbar(
                f"{self.endpunkt}/models antwortet, führt aber kein Modell. "
                "vLLM lädt vermutlich noch.")
        # vLLM bedient genau ein Modell je Prozess; das erste ist das einzige.
        return namen[0]

    def chat(self, nachrichten, *, modell: str | None = None, **weitere) -> Antwort:
        """Ein Chat-Completion-Aufruf im OpenAI-Format.

        `weitere` geht unverändert ans SDK — `temperature`, `max_tokens`,
        `extra_body` mit `guided_json` und was der Agent sonst braucht. Hier
        wird nichts davon vorgegeben, damit die Prompt-Varianten aus Spec 8.3
        nicht gegen dieses Modul argumentieren müssen.
        """
        name = modell or self.modell()
        try:
            antwort = self.client.chat.completions.create(
                model=name, messages=list(nachrichten), **weitere)
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ModellNichtErreichbar(_schlaeft_wohl(self.endpunkt, e)) from e
        except openai.APIError as e:
            raise ModellNichtErreichbar(f"Das Modell antwortete mit einem Fehler: {e}") from e
        return _als_antwort(antwort, name)


def _als_antwort(roh, name: str) -> Antwort:
    """SDK-Objekt -> `Antwort`. Verträgt eine Antwort ohne `reasoning_content`."""
    wahl = (roh.choices or [None])[0]
    if wahl is None:
        raise ModellNichtErreichbar(
            "Das Modell lieferte eine Antwort ohne einzige Wahl (`choices` leer).")
    nachricht = wahl.message
    # Das Denken ist kein Feld des OpenAI-Schemas, sondern eine Zugabe des
    # Reasoning-Parsers, und es heisst nicht überall gleich: die Box liefert
    # es am 2026-08-28 gemessen als `reasoning`, ältere vLLM-Stände und andere
    # Server nennen es `reasoning_content`. Beide werden genommen — ein
    # einzelner geratener Feldname hätte das Denken stillschweigend
    # verschluckt. Was das SDK nicht kennt, hebt es in `model_extra` auf, wo
    # `getattr` es findet; fehlt beides, bleibt es None.
    denken = (getattr(nachricht, "reasoning_content", None)
              or getattr(nachricht, "reasoning", None))
    nutzung = getattr(roh, "usage", None)
    return Antwort(
        content=nachricht.content or "",
        reasoning_content=denken or None,
        modell=getattr(roh, "model", None) or name,
        finish_reason=getattr(wahl, "finish_reason", None),
        prompt_tokens=getattr(nutzung, "prompt_tokens", None),
        completion_tokens=getattr(nutzung, "completion_tokens", None),
    )


def _schlaeft_wohl(endpunkt: str, fehler: Exception) -> str:
    """Die Fehlermeldung, die nicht rät, die Box sei kaputt.

    Eine schlafende und eine tote Box sehen vom Client aus identisch aus:
    „Connection refused", „No route to host", gar keine Antwort. Die Box
    suspendiert nach etwa zwei Stunden Leerlauf, also ist Schlafen der weit
    häufigere Fall. Wer diese Meldung liest, soll `wake.Wecker` benutzen und
    nicht anfangen, das Netz zu debuggen.
    """
    return (f"{endpunkt} hat nicht geantwortet ({fehler.__class__.__name__}). "
            "Die vLLM-Box suspendiert nach etwa 120 Minuten Leerlauf — "
            "schlafend und aus sehen von hier aus gleich aus. Wecken statt "
            "diagnostizieren: siehe picknick.llm.wake.")
