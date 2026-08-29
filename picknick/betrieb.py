"""Betrieb: nächtliche Sicherung und Statusbericht (Spec 12, Spec 11).

Zwei Dinge, die nichts mit dem Einkaufen zu tun haben und trotzdem darüber
entscheiden, ob der Shop in einem Monat noch brauchbar ist:

1. **Sicherung.** Eine SQLite-Datei ist eine Datei — und genau deshalb ist sie
   verloren, wenn sie einmal verloren ist. Gesichert wird mit `VACUUM INTO`
   und ausdrücklich nicht mit `cp`: SQLite schreibt im WAL-Modus (siehe
   `db.connect`), eine Kopie mitten in einem Schreibvorgang kann inkonsistent
   sein und fällt erst beim Zurückspielen auf. `VACUUM INTO` läuft dagegen
   über eine Lesetransaktion und erzeugt eine in sich geschlossene Datei.

2. **Statusbericht.** Der Crawler läuft nachts, wenn niemand zusieht. Ein
   Lauf, der still scheitert, ist schlimmer als einer, der laut scheitert:
   der Katalog altert dann einfach weiter, und das Hinweisband in Spec 11
   sagt nur „Preise sind N Tage alt", nicht warum. Deshalb wird jeder
   verworfene Lauf mit seiner Begründung ausgewiesen.

   **Derselbe Satz gilt für den Tracer** (WB-377), und dort wog er lange
   schwerer: dieses Projekt ist ein Vorführstück für Observability, und seine
   Statusseite hat bis WB-377 kein Wort darüber verloren, ob die Beobachtung
   überhaupt ankommt. Gemessen am 2026-08-29 in der echten Datenbank: 13 von
   35 Nutzerzügen trugen keine `span_id` — sie sind nie in Phoenix gelandet,
   und niemand konnte es sehen. Ein Beobachtbarkeits-Vorführstück, dessen
   eigene Statusseite dazu schweigt, widerlegt sich selbst. Deshalb stehen
   hier neben dem Katalog auch `trace_luecke()`, `zuordnungen()` und
   `modellstand()`.

**Der Statusbericht fragt nichts nach draussen.** Kein Phoenix, kein Modell,
kein Netz — alles kommt aus der Datenbank oder aus Zählern des laufenden
Prozesses. Der Grund ist konkret: `chat.zustand()` weckt die vLLM-Box per
Wake-on-LAN. Eine Statusseite, die einen Rechner im Nebenzimmer aufweckt, weil
jemand nachsehen wollte, ob alles läuft, wäre ein Fehler mit Stromrechnung.
Was die Box gerade tut, fragt genau eine Stelle: der Chat im Warenkorb, und
nur dann, wenn ihn jemand vor sich hat.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from picknick import db, obs

#: Wie viele Stände aufgehoben werden (Spec 12). Sieben, weil ein Fehler, der
#: den Katalog kaputtmacht, spätestens am nächsten Wochenende auffällt — und
#: weil die Datei klein ist, aber nicht so klein, dass beliebig viele Stände
#: gratis wären.
STAENDE = 7

#: Wohin gesichert wird, wenn niemand etwas anderes sagt. Nur der Name des
#: Verzeichnisses — WO es liegt, entscheidet `sicherung_dir_fuer()`.
SICHERUNG_DIR_NAME = "sicherungen"

PRAEFIX = "picknick-"
ENDUNG = ".db"

#: Erkennt genau die Dateien, die `sichern()` anlegt. Bewusst streng: im
#: Sicherungsverzeichnis wird gelöscht, und dabei soll nichts abgeräumt
#: werden, was jemand von Hand dort abgelegt hat.
STAND_MUSTER = re.compile(
    r"^" + re.escape(PRAEFIX) + r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}"
    r"(-\d+)?" + re.escape(ENDUNG) + r"$")


# --------------------------------------------------------------------------
# Sicherung

def sicherung_dir_fuer(db_path: str | Path) -> Path:
    """Das Sicherungsverzeichnis NEBEN der Datenbank, nicht neben dem cwd.

    Ein fester relativer Pfad wie `data/sicherungen` sichert dorthin, wo der
    Prozess gerade steht — beim Probestart der systemd-Unit landete dadurch
    eine Sicherung der Testdatenbank im Projektverzeichnis (2026-08-28). Wer
    die Datenbank verlegt, verlegt damit auch ihre Stände.
    """
    return Path(db_path).resolve().parent / SICHERUNG_DIR_NAME


def staende(ziel_dir: str | Path) -> list[Path]:
    """Alle vorhandenen Stände, ältester zuerst.

    Sortiert wird über den Dateinamen und nicht über die mtime: der Name trägt
    den Zeitstempel des Laufs, die mtime überlebt ein Kopieren des Verzeich-
    nisses nicht. Der ISO-Stempel sortiert lexikografisch wie chronologisch.
    """
    ziel = Path(ziel_dir)
    if not ziel.is_dir():
        return []
    return sorted((p for p in ziel.iterdir() if STAND_MUSTER.match(p.name)),
                  key=lambda p: p.name)


def abraeumen(ziel_dir: str | Path, behalten: int = STAENDE) -> list[Path]:
    """Löscht die ältesten Stände, bis nur noch `behalten` übrig sind.

    Gibt die gelöschten Pfade zurück, damit der Lauf im Log sagen kann, was er
    weggeräumt hat — eine Rotation, die schweigt, ist eine Rotation, der man
    beim ersten Zweifel nicht glaubt.
    """
    if behalten < 0:
        raise ValueError(f"behalten muss >= 0 sein, war {behalten}")
    vorhanden = staende(ziel_dir)
    weg = vorhanden[:max(0, len(vorhanden) - behalten)]
    for pfad in weg:
        pfad.unlink()
    return weg


def _freier_pfad(ziel_dir: Path, stempel: str) -> Path:
    """Ein noch nicht vergebener Dateiname für diesen Zeitstempel.

    `VACUUM INTO` weigert sich, eine bestehende Datei zu überschreiben. Zwei
    Läufe in derselben Sekunde sind unwahrscheinlich, aber ein Handstart neben
    dem Timer erzeugt genau das — und ein Abbruch mit „output file already
    exists" wäre dann ein Fehler ohne Ursache.
    """
    kandidat = ziel_dir / f"{PRAEFIX}{stempel}{ENDUNG}"
    n = 2
    while kandidat.exists():
        kandidat = ziel_dir / f"{PRAEFIX}{stempel}-{n}{ENDUNG}"
        n += 1
    return kandidat


def sichern(db_path: str | Path,
            ziel_dir: str | Path | None = None,
            *, behalten: int = STAENDE,
            stempel: str | None = None) -> tuple[Path, list[Path]]:
    """Schreibt einen Stand per `VACUUM INTO` und räumt die alten ab.

    Gibt `(neuer Stand, gelöschte Stände)` zurück. Die Verbindung wird hier
    geöffnet und wieder geschlossen: die Sicherung ist ein abgeschlossener
    Vorgang und soll nicht an einer Verbindung hängen, in der jemand anders
    gerade eine Transaktion offen hat — `VACUUM` scheitert darin.
    """
    quelle = Path(db_path)
    if not quelle.is_file():
        raise FileNotFoundError(
            f"Keine Datenbank unter {quelle} — es gibt nichts zu sichern.")
    ziel_dir = Path(ziel_dir) if ziel_dir else sicherung_dir_fuer(quelle)
    ziel_dir.mkdir(parents=True, exist_ok=True)
    ziel = _freier_pfad(ziel_dir, stempel or time.strftime("%Y-%m-%dT%H-%M-%S"))

    con = sqlite3.connect(str(quelle))
    try:
        # Der Pfad geht als Parameter hinein und nicht in den SQL-Text: ein
        # Verzeichnisname mit Apostroph würde die Anweisung sonst zerlegen.
        con.execute("VACUUM INTO ?", (str(ziel),))
    finally:
        con.close()
    return ziel, abraeumen(ziel_dir, behalten)


# --------------------------------------------------------------------------
# Statusbericht

#: Läufe mit diesem Status haben den Katalog NICHT angefasst (siehe
#: `knuspr.crawl`) — sie sind der interessante Teil der Statusseite.
VERWORFEN = ("rejected", "error")

_LAUF_SPALTEN = ("id, source, started_at, finished_at, status, n_products,"
                 " error")


def _zeilen(con: sqlite3.Connection, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def laeufe(con: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Die letzten Läufe, neuester zuerst."""
    return _zeilen(con, f"SELECT {_LAUF_SPALTEN} FROM scrape_run"
                        " ORDER BY id DESC LIMIT ?", (limit,))


def verworfene_laeufe(con: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Läufe, die den Katalog nicht verändert haben — mit Begründung.

    Drei Sorten, und die dritte ist die gemeinste: `status IS NULL` heisst,
    dass der Prozess zwischen dem ersten und dem letzten Schreiben in
    `scrape_run` verschwunden ist — abgeschossen, ausgeschaltet, zugeklappt.
    Ohne diese Zeile sähe die Statusseite einfach gar nichts, und genau so
    scheitert ein Crawler still.
    """
    zeilen = _zeilen(
        con,
        f"SELECT {_LAUF_SPALTEN} FROM scrape_run"
        " WHERE status IN (?, ?) OR status IS NULL"
        " ORDER BY id DESC LIMIT ?", (*VERWORFEN, limit))
    for z in zeilen:
        z["grund"] = z["error"] or (
            "Kein Abschluss vermerkt — der Lauf wurde abgebrochen, oder er "
            "läuft in diesem Moment noch."
            if z["status"] is None else "ohne Begründung eingetragen")
    return zeilen


# --------------------------------------------------------------------------
# Der Agent: Trace, Zuordnungen, Modell (WB-377)

#: Die Rolle der Nutzerin in `chat_message`. Denselben Wert schreibt
#: `assistant.vorschlaege.ROLLE_NUTZERIN`; von dort importiert zöge dieses
#: Modul über `picknick.assistant` das ganze Chat-Paket samt `openai` herein,
#: und der nächtliche Sicherungslauf soll nichts davon laden.
ROLLE_NUTZERIN = "user"

OFFEN, BEHALTEN, VERWORFEN_ENTSCHEIDUNG = db.DECISIONS


def trace_luecke(con: sqlite3.Connection) -> dict:
    """Wie viele Chat-Züge nie in Phoenix angekommen sind (WB-377).

    Ein Zug ohne `span_id` hat keinen Trace: entweder war Tracing aus, oder
    der Provider liess sich nicht einrichten. Die Zeile ist nachträglich nicht
    mehr aufzulösen — welcher der beiden Gründe es war, steht nirgends —, und
    genau deshalb wird sie hier gezählt und nicht geraten.

    Gezählt werden die Zeilen der NUTZERIN. Jeder Zug schreibt zwei Zeilen mit
    derselben `span_id`; über beide gezählt stünde auf der Seite die doppelte
    Zahl, und niemand könnte sie mit „35 Züge" in Übereinstimmung bringen.
    """
    row = con.execute(
        "SELECT count(*) AS zuege,"
        "       sum(span_id IS NOT NULL) AS mit,"
        "       max(CASE WHEN span_id IS NOT NULL THEN created_at END)"
        "            AS letzter_mit,"
        "       max(CASE WHEN span_id IS NULL THEN created_at END)"
        "            AS letzter_ohne"
        " FROM chat_message WHERE role = ?", (ROLLE_NUTZERIN,)).fetchone()
    zuege = int(row["zuege"] or 0)
    mit = int(row["mit"] or 0)
    return {
        "zuege": zuege,
        "mit_trace": mit,
        "ohne_trace": zuege - mit,
        "letzter_mit": row["letzter_mit"],
        "letzter_ohne": row["letzter_ohne"],
        # Kein Anteil ohne Züge: 0 % gelesen als „nichts kommt an" wäre eine
        # Aussage über einen Shop, in dem noch niemand etwas getippt hat.
        "anteil": (mit / zuege) if zuege else None,
    }


def zuordnungen(con: sqlite3.Connection) -> dict:
    """kept / removed / offen über alle Vorschläge — die Eval-Grundlage.

    Dieselbe Rechnung wie `assistant.vorschlaege.quote()`, nur über den ganzen
    Bestand statt über eine Chatzeile: **offene Vorschläge zählen weder im
    Zähler noch im Nenner**, und Korrekturzeilen zählen gar nicht mit. Beides
    aus demselben Grund wie dort — ein Abbruch ist kein Fehlgriff des Modells,
    und wer von Hand nachbessert, soll damit nicht die Quote heben, die genau
    diesen Fehlgriff misst.

    Ohne eine einzige Entscheidung bleibt `quote` `None`. Eine 0.0 hiesse
    „alles falsch", wo „noch nichts gesagt" richtig ist.
    """
    gezaehlt = {z["decision"]: int(z["n"]) for z in _zeilen(
        con, "SELECT decision, count(*) AS n FROM chat_suggestion"
             " WHERE corrected_from IS NULL GROUP BY decision")}
    behalten = gezaehlt.get(BEHALTEN, 0)
    verworfen = gezaehlt.get(VERWORFEN_ENTSCHEIDUNG, 0)
    offen = gezaehlt.get(OFFEN, 0)
    entschieden = behalten + verworfen
    return {"vorgeschlagen": entschieden + offen, "behalten": behalten,
            "verworfen": verworfen, "offen": offen,
            "quote": (behalten / entschieden) if entschieden else None}


def modellstand(con: sqlite3.Connection) -> dict:
    """Was über das Modell bekannt ist, OHNE die Box zu fragen (WB-377).

    Der wichtigste Teil dieser Funktion ist das, was sie nicht tut: sie ruft
    `chat.zustand()` nicht auf. Dieser Aufruf schickt ein Magic Packet und
    **weckt einen Rechner im LAN** — für eine Seite, die jemand aufmacht, um
    nachzusehen, ist das der falsche Preis. Deshalb steht hier der zuletzt
    belegte Stand mit Zeitpunkt und nicht der aktuelle.

    Was zurückkommt, ist entsprechend bescheiden und ehrlich:

    * `letzter_zug` — wann zuletzt überhaupt ein Chat-Zug lief. Ob dabei das
      Modell gefragt wurde, steht nicht in der Datenbank: der Rezeptweg
      (Spec 6) schreibt dieselbe Zeile ohne einen einzigen Modellaufruf. Die
      Seite behauptet deshalb nicht, die Box habe zu diesem Zeitpunkt bedient.
    * `endpunkt` — wohin gefragt WÜRDE. Reine Konfiguration.
    * `fehler` — die Konfiguration ist so nicht benutzbar. Das ist die einzige
      Aussage über das Modell, die sich ohne Netzaufruf sicher treffen lässt,
      und es ist ausgerechnet die, die hier schon Zeit gekostet hat (die alte
      IP der Box, siehe `llm.client.pruefe_endpunkt`).
    """
    row = con.execute("SELECT max(created_at) AS letzter FROM chat_message"
                      ).fetchone()
    stand = {"letzter_zug": row["letzter"] if row else None,
             "endpunkt": None, "fehler": None}
    try:
        # Erst hier importiert: `llm.client` zieht `openai` herein, und der
        # nächtliche Sicherungslauf hat damit nichts zu tun.
        from picknick.llm.client import endpunkt_aus_umgebung
        stand["endpunkt"] = endpunkt_aus_umgebung()
    except Exception as e:  # noqa: BLE001 — Konfiguration oder fehlendes Paket
        stand["fehler"] = str(e)
    return stand


def statusbericht(con: sqlite3.Connection, *, limit: int = 20) -> dict:
    """Alles, was die Statusseite zeigt (Spec 11, WB-377).

    Bewusst eine Funktion und keine Abfrage in der Vorlage: so ist der Bericht
    ohne Web-Prozess prüfbar, und die Seite kann nichts anderes anzeigen als
    das, was hier steht.

    **Kein Aufruf hier geht ins Netz.** Siehe den Modul-Docstring: die Seite
    darf die vLLM-Box nicht wecken, nur weil jemand nachsehen wollte.
    """
    letzter = laeufe(con, 1)
    letzter_ok = _zeilen(
        con, f"SELECT {_LAUF_SPALTEN} FROM scrape_run WHERE status = 'ok'"
             " ORDER BY id DESC LIMIT 1")
    produkte = con.execute(
        "SELECT count(*) AS n FROM product WHERE active = 1").fetchone()["n"]
    return {
        "letzter": letzter[0] if letzter else None,
        "letzter_ok": letzter_ok[0] if letzter_ok else None,
        "produkte": int(produkte),
        "laeufe": laeufe(con, limit),
        "verworfen": verworfene_laeufe(con, limit),
        "tracer": obs.tracerstand(),
        "trace_luecke": trace_luecke(con),
        "zuordnungen": zuordnungen(con),
        "modell": modellstand(con),
    }
