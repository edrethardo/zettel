"""Der Rezeptwechsel passiert an Ort und Stelle (WB-402).

Der Nutzer selbst hat es gemeldet: „Die Darstellung ist schrecklich. Wenn ich
einen anderen Vorschlag aussuche lädt es ewig und dann muss ich scrollen. Das
sollte inplace ersetzen." Nachgemessen am laufenden Shop (2026-08-30):

    anderes Rezept wählen                     24,29 s
    hx-target="#chat" hx-swap="outerHTML"     tauscht den GANZEN Verlauf
    Indikator #chat-laeuft                    201 Proben über 24 s, in KEINER
                                              im Bild (Dokument-y 8181,
                                              Fenster bei y 820)
    Seitenhöhe                                9.017 -> 23.496 px
    scrollY                                   820 -> 820, unverändert
    das gewählte Rezept lag                   bei y = 16.248

Alles andere in dieser Oberfläche liegt unter 0,25 s: Ja/Nein 0,23 s,
rückgängig 0,22 s, Portionen 0,22 s, „Alles übernehmen" 0,37 s, Korb ±
0,06 s. Der Rezeptwechsel war der einzige Ausreisser — um zwei
Grössenordnungen.

Die Kur hat zwei Hälften, und beide werden hier geprüft:

    1. das TAUSCHZIEL ist der Zug, nicht der Chat (die Kur aus WB-372,
       die den Rezeptwechsel übersehen hatte)
    2. die KARTE kostet kein Modell und kommt sofort; die Vorschläge
       laufen nach

**Kein Test geht ins Netz und keiner an ein Modell.** Fixture, Doppelgänger
und Shop kommen aus `test_alternativrezepte.py` — dieselbe aufgezeichnete
Pho-Suche, derselbe nachgebaute Alternativ-Doppelgänger.
"""
from __future__ import annotations

from pathlib import Path

from picknick import db

from test_alternativrezepte import (HTMX, PHO_BO, PHO_GA, _karte, _pho_geholt,
                                    _shop, _wechsel, _zug_id, _eine_zeile)
from test_alternativrezepte import datei  # noqa: F401  (Fixture)

STIL = Path(__file__).resolve().parents[1] / "picknick" / "web" / "static" \
    / "stil.css"


def bytes_(text: str) -> int:
    return len(text.encode("utf-8"))


def _zuege(pfad) -> int:
    con = db.connect(pfad)
    try:
        return con.execute("SELECT count(*) AS n FROM chat_message"
                           " WHERE role = 'assistant'").fetchone()["n"]
    finally:
        con.close()


# --------------------------------------------------------------------------
# 1. Das Tauschziel ist der Zug und nicht der Chat

def test_der_tipp_zielt_auf_den_zug_und_nicht_auf_den_chat(datei, tmp_path):
    """`hx-target="#chat"` war der Grund, warum die Seite sprang.

    Genau das hat WB-372 für Ja/Nein abgeschafft; der Rezeptwechsel ist dabei
    übersehen worden und stand als einziges Formular des Chats weiter auf dem
    ganzen Verlauf.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    karte = _karte(client.get("/chat").text)

    form = karte.split('<form class="andere"', 1)[1].split(">", 1)[0]
    assert f'hx-target="#zug-{mid}"' in form, form
    assert 'hx-target="#chat"' not in form, form
    # `afterend`: der alte Zug bleibt stehen. Das Band verspricht es, und
    # nähme dieser Tausch ihn für eine halbe Minute heraus, wäre der Satz in
    # genau der halben Minute falsch, in der jemand nachsehen wollte.
    assert 'hx-swap="afterend' in form, form


def test_die_antwort_traegt_den_verlauf_nicht_mehr_mit(datei, tmp_path):
    """Der Grössenvergleich des Tickets: der Tipp trug den ganzen Chat.

    Die Zahl ist dieselbe Art Zahl wie in WB-372 (207.000 -> 2.249 Bytes je
    „Ja"): was zurückkommt, ist so gross wie das, was sich ändert.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    # So gross war die Antwort vor diesem Ticket: der ganze Verlauf, also
    # genau das, was `GET /chat` als Bruchstück liefert.
    ganzer_chat = client.get("/chat", headers=HTMX).text
    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                        headers=HTMX).text

    assert '<section class="chat" id="chat">' in ganzer_chat
    assert 'id="chat"' not in erste, "die Antwort trägt den Verlauf mit"
    assert bytes_(erste) < bytes_(ganzer_chat)


def test_die_karte_wird_ins_bild_geholt(datei, tmp_path):
    """Ohne `show:` ändert sich im sichtbaren Bereich nichts.

    Gemessen: scrollY blieb bei 820, die neue Karte lag bei y = 16.248 —
    zwanzig Bildschirme unter dem Fensterrand. Dasselbe Mittel wie bei der
    Katalog-Trefferliste (WB-376) und aus demselben Grund.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    karte = _karte(client.get("/chat").text)

    form = karte.split('<form class="andere"', 1)[1].split(">", 1)[0]
    assert f"show:#wechsel-{mid}:top" in form, form
    # Und das Sprungziel gibt es auch: es steht in der Antwort auf den Tipp.
    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                        headers=HTMX).text
    assert f'id="wechsel-{mid}"' in erste


def test_der_indikator_haengt_am_getauschten_zug(datei, tmp_path):
    """WB-378: der Indikator ist das Tauschziel.

    `#chat-laeuft` steht am Seitenfuss, in 201 Proben über 24 Sekunden kein
    einziges Mal im Bild. Blass wird jetzt der Zug, der gleich ersetzt wird —
    und `pointer-events: none` daran verhindert nebenbei den zweiten Tipp,
    während der erste läuft.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    karte = _karte(client.get("/chat").text)

    form = karte.split('<form class="andere"', 1)[1].split(">", 1)[0]
    assert f'hx-indicator="#zug-{mid}"' in form, form
    assert 'hx-indicator="#chat-laeuft"' not in form, form
    stil = STIL.read_text(encoding="utf-8")
    assert ".zug.htmx-request" in stil, "der Zug wird gar nicht blass"


# --------------------------------------------------------------------------
# 2. Die Karte kostet kein Modell und steht sofort

def test_die_karte_zeigt_das_neue_rezept_ohne_ein_modell_zu_fragen(datei,
                                                                   tmp_path):
    """Der grösste Hebel des Tickets (Punkt 3).

    Name, Zeiten, Zutatenliste, Bewertung stehen in `recipe` und
    `recipe_ingredient`; nur die Vorschlagsliste kostet die zwei
    Modellstufen. Also wechselt die Karte, bevor irgendetwas gerechnet wird.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    vorher = len(client.llm.aufrufe)
    zuege = _zuege(datei)

    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                        headers=HTMX).text

    assert "Pho Ga" in erste, "die Karte zeigt das neue Rezept nicht"
    assert "Pho Bo" not in erste, "die alte Karte steht noch da"
    assert len(client.llm.aufrufe) == vorher, \
        "die Karte hat ein Modell gefragt"
    assert _zuege(datei) == zuege, "es ist schon ein Zug entstanden"
    # Und die Zutaten der Quelle stehen darin — die Karte ist vollständig
    # und keine Überschrift mit Platzhalter.
    assert "Mie Nudeln" in erste


def test_die_karte_holt_sich_die_vorschlaege_selbst_nach(datei, tmp_path):
    """Das Nachlaufen ist Teil der Antwort und nicht Sache des Nutzers.

    Ohne den `load`-Auslöser bliebe die Karte stehen und die Vorschläge
    kämen nie — der Wechsel wäre halb.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                        headers=HTMX).text
    block = erste.split('<div class="zugwechsel"', 1)[1].split(">", 1)[0]
    assert f'hx-post="/chat/{mid}/rezept/vorschlaege?rezept={PHO_GA}"' \
        in block, block
    assert 'hx-trigger="load"' in block, block
    assert 'hx-target="this"' in block, block
    # Der Indikator zeigt auf den alten Zug: er ist blass und untippbar,
    # solange sein Nachfolger entsteht (WB-378 über beide Hälften).
    assert f'hx-indicator="#zug-{mid}"' in block, block


def test_der_fortschritt_steht_in_dem_stueck_das_getauscht_wird(datei,
                                                                tmp_path):
    """Punkt 4: sichtbarer Fortschritt für den Teil, der wirklich dauert.

    Der Satz steht IN dem Kasten, der gleich ersetzt wird — nicht am
    Seitenfuss, wo ihn 24 Sekunden lang niemand sah.

    **Und er steht ÜBER der Karte, nicht darunter.** Am Messstand gemessen:
    unter der Karte war er in 1 von 224 Proben im Bild, denn `show:` setzt den
    KASTEN an den oberen Rand und die Karte ist eine Bildschirmhöhe lang. Der
    Indikator ist das Tauschziel — aber er muss auch darin zu sehen sein.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                        headers=HTMX).text
    block = _eine_zeile(erste.split(f'id="wechsel-{mid}"', 1)[1])
    assert "die Vorschläge dazu werden gesucht" in block
    # Und die Zusage über den Korb steht schon hier — sie ist in dieser
    # Sekunde wahr, denn der Wechsel hat nur `dish` angefasst.
    assert "liegt weiter im Korb" in block
    assert block.index("werden gesucht") < block.index('class="zugrezept"'), \
        "der Fortschritt steht unter der Karte und damit ausserhalb des Bildes"


def test_die_vorschau_bietet_keine_zweite_wahl_und_kein_portionsfeld(datei,
                                                                     tmp_path):
    """Beide Formulare brauchen einen Zug, den es noch nicht gibt.

    Die Portionszahl steht in `chat_rezept`, die Alternativenliste postet an
    `/chat/<mid>/rezept` — mit `mid` des ALTEN Zugs wären beide falsch
    verdrahtet. Und ein zweiter Tipp mitten im Wechsel wäre ein Rennen.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    erste = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA},
                        headers=HTMX).text
    assert '<form class="andere"' not in erste
    assert '<form class="zugportionen"' not in erste
    # Der Ausgang, der immer wahr ist, steht trotzdem da.
    assert "Das ganze Rezept" in _eine_zeile(erste)


# --------------------------------------------------------------------------
# 3. Die zweite Hälfte setzt zusammen, was in der Datenbank steht

def test_die_zweite_haelfte_traegt_nur_das_neue(datei, tmp_path):
    """Der alte Zug steht schon im Dokument — er wird nicht mitgeschickt.

    `afterend` setzt den Wartekasten HINTER den Zug statt an seine Stelle;
    damit bleibt der alte Zug stehen, und diese Antwort wird um seine ganze
    Grösse kleiner (am Messstand 64.733 -> 34.855 Bytes).
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    _erste, zweite = _wechsel(client, mid, PHO_GA)
    neu = _zug_id(datei)
    assert neu > mid
    assert f'id="zug-{neu}"' in zweite.text, "der neue Zug fehlt"
    assert f'id="zug-{mid}"' not in zweite.text, "der alte Zug reist mit"
    assert 'id="chat"' not in zweite.text, "die Antwort trägt den Verlauf mit"


def test_die_zweite_haelfte_stellt_den_blick_auf_das_band(datei, tmp_path):
    """Der alte Zug rückt wieder ein — die Seite wächst an dieser Stelle.

    Ohne `show:` schöbe sich alles darunter um seine ganze Höhe nach unten,
    und der Nutzer suchte die Karte wieder, auf die er eben eine halbe Minute
    geschaut hat. Das Sprungziel ist das Band: seine `id` steht schon fest,
    wenn der Tipp abgeschickt wird — die Nummer des neuen Zugs gibt es erst,
    wenn er geschrieben ist.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    erste, zweite = _wechsel(client, mid, PHO_GA)
    block = erste.text.split('<div class="zugwechsel"', 1)[1].split(">", 1)[0]
    assert f"show:#gewechselt-{mid}:top" in block, block
    assert f'id="gewechselt-{mid}"' in zweite.text, "das Sprungziel fehlt"


def test_die_neue_frage_steht_ueber_ihrer_antwort(datei, tmp_path):
    """`chat.turn` schreibt Frage UND Antwort — beide gehören in die Antwort.

    Ohne die Frage stünde eine Vorschlagsliste ohne den Satz da, aus dem sie
    entstanden ist — dieselbe Überlegung wie bei der Verlaufsgrenze, die
    deshalb immer auf einer Zeile der Nutzerin liegt.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    _erste, zweite = _wechsel(client, mid, PHO_GA)
    # Genau die Zeilen, die auch `/chat` an dieser Stelle zeigt.
    con = db.connect(datei)
    try:
        neue = [r["id"] for r in con.execute(
            "SELECT id FROM chat_message WHERE id > ? ORDER BY id", (mid,))]
    finally:
        con.close()
    assert len(neue) == 2, neue
    for i in neue:
        assert f'id="zug-{i}"' in zweite.text, i
    assert zweite.text.index(f'id="zug-{neue[0]}"') \
        < zweite.text.index(f'id="zug-{neue[1]}"')


def test_ohne_javascript_bleibt_es_bei_einem_request(datei, tmp_path):
    """Kein Bruchstück ohne HTMX: dann läuft der ganze Wechsel auf einmal.

    Sonst bekäme jemand ohne JavaScript eine Karte, die auf ein Nachladen
    wartet, das nie kommt — und der Shop soll ohne JavaScript bedienbar
    bleiben.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)
    zuege = _zuege(datei)

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_GA})
    assert antwort.status_code == 200
    assert _zuege(datei) == zuege + 1, "ohne HTMX ist kein Zug entstanden"
    assert '<section class="chat" id="chat">' in antwort.text
    assert "Das Rezept ist jetzt „Pho Ga“" in antwort.text


def test_eine_meldung_landet_dort_wo_die_karte_stuende(datei, tmp_path):
    """Auch der Fehlerweg darf weder den Verlauf noch einen Zug liefern.

    Das Tauschziel ist `#zug-N` mit `afterend` — ein ganzer Zug als Antwort
    stünde danach zweimal im Dokument, eine ganze Chatseite ergäbe einen
    zweiten `#chat` im ersten. Die Meldung kommt deshalb in der Hülle des
    Wartekastens und wird von demselben `show:` ins Bild geholt.
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": "4711"},
                          headers=HTMX)
    assert "nicht (mehr) zur Wahl" in antwort.text
    assert 'id="chat"' not in antwort.text
    assert f'id="zug-{mid}"' not in antwort.text
    assert f'id="wechsel-{mid}"' in antwort.text
    # Und kein Nachladen: hier läuft nichts.
    assert "hx-trigger" not in antwort.text


def test_das_schon_gewaehlte_meldet_sich_an_derselben_stelle(datei, tmp_path):
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    mid = _zug_id(datei)

    antwort = client.post(f"/chat/{mid}/rezept", data={"rezept": PHO_BO},
                          headers=HTMX)
    assert "bereits das vorgeschlagene Rezept" in antwort.text
    assert 'id="chat"' not in antwort.text
    assert f'id="wechsel-{mid}"' in antwort.text


# --------------------------------------------------------------------------
# 4. Die Darstellung der Alternativen (Punkt 5)

def test_die_vorgewaehlte_alternative_traegt_die_handlungsfarbe(datei,
                                                                tmp_path):
    """„Man erkennt nicht, welche gewählt ist."

    Bis WB-402 war es genau andersherum: die fünf wählbaren waren gefüllte
    Kästen, und die vorgewählte stand durchsichtig mit gestricheltem Rand
    daneben — sie sah aus wie die schwächste der sechs.
    """
    stil = STIL.read_text(encoding="utf-8")
    assert ".andereliste .ist .wahl" in stil
    block = stil.split(".andereliste .ist .wahl", 1)[1].split("}", 1)[0]
    assert "var(--akzent" in block, block
    assert "border-style: dashed" not in block, block


def test_die_zeit_hebt_sich_von_den_anderen_zahlen_ab(datei, tmp_path):
    """„Die Zahl, nach der man entscheidet, ist die Zeit."

    Sechs Zeilen mit derselben Schrift und derselben Zahlenreihe waren eine
    Wand. Die Zeit steht jetzt in Tinte und fett, Bewertung und Zutatenzahl
    bleiben gedämpft — eine Spalte, die sich überfliegen lässt.
    """
    stil = STIL.read_text(encoding="utf-8")
    assert ".andereliste .dauer" in stil
    block = stil.split(".andereliste .dauer", 1)[1].split("}", 1)[0]
    assert "var(--tinte)" in block, block

    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path)
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    karte = _karte(client.get("/chat").text)
    assert '<span class="dauer">9½ Stunden</span>' in karte
    assert '<span class="dauer">45 Minuten Arbeitszeit</span>' in karte
