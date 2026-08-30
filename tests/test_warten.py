"""Der Shop fragt von selbst noch einmal — und der Zähler tickt (WB-414/415).

Der Nutzer: „Sorg dafür dass der Timer bis die Antwort kommt interaktiv ist
wenn das Modell aufgerufen wird, besonders wenn es schläft. Und dass man
danach nicht nochmal klicken muss." Und: „Wenn man die Menge ändert pass
direkt das Rezept bei Eingabe an und nicht erst bei Button klick."

Bis dahin endete ein Satz an eine schlafende Box in „Gleich noch einmal
‚Fragen' tippen" — bei gemessenen 96 Sekunden Weckzeit. Der Satz war schon
getippt und schon abgeschickt; ihn ein zweites Mal abzuschicken ist Arbeit,
die der Shop selbst tun kann.

Kein Test geht ins Netz und keiner an ein echtes Modell.
"""
from __future__ import annotations

from zettel import db
from zettel.llm import wake

from test_web_chat import HTMX, Box, _choose, _client, _extract


def _zuege(pfad) -> int:
    con = db.connect(pfad)
    try:
        return con.execute("SELECT count(*) AS n FROM chat_message"
                           " WHERE role = 'assistant'").fetchone()["n"]
    finally:
        con.close()


# --------------------------------------------------------------------------
# 1. Der Wartekasten

def test_ein_satz_an_die_schlafende_box_bekommt_den_wartekasten(db_datei,
                                                                tmp_path):
    client, _ = _client(db_datei, tmp_path,
                        box=Box(wake.WACHT_AUF, grund="Weckruf läuft."))

    antwort = client.post("/chat", data={"satz": "Landmilch"},
                          headers=HTMX).text

    assert 'id="chat-warten"' in antwort, "Kein Wartekasten."
    assert 'hx-post="/chat/warten"' in antwort
    assert "Landmilch" in antwort
    assert "data-warten-bis=" in antwort, "Kein tickender Zähler."


def test_der_zaehler_traegt_die_restsekunden_und_kein_role_status(db_datei,
                                                                  tmp_path):
    """Eine Zahl, die jede Sekunde wechselt, darf nicht vorgelesen werden.

    Derselbe Befund wie an der Quittung in WB-400: `role="status"` an etwas,
    das sich dauernd ändert, macht den Vorleser unbenutzbar. Angesagt wird
    der SATZ, einmal.
    """
    client, _ = _client(db_datei, tmp_path, box=Box(wake.WACHT_AUF))
    antwort = client.post("/chat", data={"satz": "Landmilch"},
                          headers=HTMX).text
    kasten = antwort.split('id="chat-warten"', 1)[1].split("</div>", 1)[0]

    zaehler = kasten.split('class="warten-zaehler"', 1)[1].split(">", 1)[0]
    assert 'aria-hidden="true"' in zaehler, zaehler
    assert "role=" not in zaehler, zaehler
    assert 'role="status"' in kasten, "Der Satz wird gar nicht angesagt."


def test_warten_schreibt_keinen_zug_solange_die_box_schlaeft(db_datei,
                                                             tmp_path):
    client, _ = _client(db_datei, tmp_path, box=Box(wake.WACHT_AUF))
    vorher = _zuege(db_datei)

    antwort = client.post("/chat/warten", data={"satz": "Landmilch"},
                          headers=HTMX)

    assert antwort.status_code == 200
    assert 'id="chat-warten"' in antwort.text
    assert "HX-Retarget" not in antwort.headers
    assert _zuege(db_datei) == vorher, "Es ist ein Zug entstanden."


def test_wacht_die_box_auf_laeuft_der_satz_ohne_zweiten_klick(db_datei,
                                                              tmp_path):
    """Der Kern des Tickets: kein zweiter Handgriff.

    Der Kasten fragt alle drei Sekunden dieselbe Adresse. Sobald die Box
    bedient, ist genau diese Frage der Zug — und die Antwort gehört an den
    ganzen Chat, nicht an den Kasten.
    """
    box = Box(wake.WACHT_AUF)
    client, _ = _client(db_datei, tmp_path,
                        _extract((("Landmilch", "Milch"), 1)),
                        _choose(),
                        box=box)
    client.post("/chat", data={"satz": "Landmilch"}, headers=HTMX)
    assert _zuege(db_datei) == 0

    box._zustand = wake.BEDIENT
    antwort = client.post("/chat/warten", data={"satz": "Landmilch"},
                          headers=HTMX)

    assert antwort.status_code == 200
    assert _zuege(db_datei) == 1, "Der Zug ist nicht von selbst gelaufen."
    assert antwort.headers.get("HX-Retarget") == "#chat"
    assert antwort.headers.get("HX-Reswap") == "outerHTML"


def test_ohne_javascript_bleibt_der_knopf_der_weg(db_datei, tmp_path):
    """Der Kasten ist ohne HTMX kein Kasten, sondern ein Formular."""
    client, _ = _client(db_datei, tmp_path, box=Box(wake.WACHT_AUF))

    antwort = client.post("/chat/warten", data={"satz": "Landmilch"}).text

    assert "<h1>Chat</h1>" in antwort, "Keine Vollseite."
    assert 'class="warten-jetzt"' in antwort
    assert 'value="Landmilch"' in antwort


def test_der_wartekasten_steht_nur_wenn_jemand_wartet(db_datei, tmp_path):
    client, _ = _client(db_datei, tmp_path)
    assert 'id="chat-warten"' not in client.get("/chat").text


# --------------------------------------------------------------------------
# 2. Die Mengen folgen der Eingabe

def test_das_portionsfeld_rechnet_bei_der_eingabe(db_datei, tmp_path):
    """Kein zweiter Handgriff, und die Tastatur bleibt stehen.

    Die feste `id` ist die Bedingung: der Tausch ersetzt den ganzen Zug samt
    Eingabefeld, und htmx stellt Fokus und Schreibmarke nur an einem Element
    mit derselben `id` wieder her.
    """
    from pathlib import Path
    vorlage = (Path(__file__).resolve().parents[1] / "zettel" / "web"
               / "templates" / "_zugrezept.html").read_text(encoding="utf-8")
    form = vorlage.split('<form class="zugportionen"', 1)[1].split(">", 1)[0]

    assert "input changed delay:" in form, form
    assert "submit" in form, "Ohne `submit` tut der Knopf nichts mehr."
    assert 'id="zugportionen-{{ m.id }}"' in vorlage, (
        "Ohne feste id verliert das Feld beim Tausch die Tastatur.")
