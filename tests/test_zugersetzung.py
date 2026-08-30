"""Der Rezeptwechsel ersetzt den Zug an Ort und Stelle (WB-403).

**Der Nutzer, zum zweiten Mal:** „Das ist auch katastrophal. Sorg dafür dass
das inplace passiert anstatt dass gescrollt wird."

WB-402 hat die Rückmeldung repariert — die Karte des neuen Rezepts steht nach
0,84 s statt nie — und den eigentlichen Wunsch nicht erfüllt: der Tausch war
`afterend`, also ein ANGEHÄNGTER Zug plus ein Bildlauf dorthin. Nach drei
Wechseln standen vier fast gleiche Lasagne-Züge untereinander.

**Warum bisher überhaupt ein neuer Zug angelegt wurde**, und warum das
Argument nicht trägt: `chat.turn` schreibt je Wechsel eine neue
`chat_message`, und WB-387 begründete das mit den Eval-Labels — jeder Zug
trägt seine `mapping_precision`. Nach Spec 8.1 zählen **offene** Vorschläge
aber nirgends, weder im Zähler noch im Nenner; ein Zug, dessen Vorschläge
alle offen sind, trägt `quote = None` und liefert gar kein Label. Genau das
ist die Lage beim Wechsel: wer ein anderes Rezept wählt, hat zum alten noch
nichts entschieden.

Also gilt:

    alle Vorschläge des alten Zugs offen   ->  nichts geht verloren, ersetzen
    einzelne schon entschieden             ->  die liegen im Korb und tragen
                                               ein Label; sie MÜSSEN bleiben

**Gelöscht wird trotzdem nichts.** Die beiden Wege des Tickets waren „die
alte Chatzeile samt offener Vorschläge löschen" und „sie als ersetzt
markieren". Der erste ist der kürzere und der falsche: an der Zeile hängen
Vorschläge, `ON DELETE CASCADE` nähme sie mit, und darunter wären die
entschiedenen — die mit Label und Korbwirkung. Eine Löschung, die im
Normalfall gutgeht und im interessanten Fall Entscheidungen tilgt, ist keine
Vereinfachung. Dieses Projekt hat mehrfach anders entschieden
(`zurueckgenommen` in WB-361, `corrected_from` in WB-359); hier kostet es
eine Spalte, `chat_message.ersetzt`.

**Kein Test geht ins Netz und keiner an ein Modell.** Fixture, Doppelgänger
und Shop kommen aus `test_alternativrezepte.py`.
"""
from __future__ import annotations

from zettel import db
from zettel.assistant import vorschlaege as vorschlagsliste
from zettel.obs import labels

from test_alternativrezepte import (HTMX, PHO_BO, PHO_GA, _antworten,
                                    _eine_zeile, _pho_geholt, _shop,
                                    _wechsel, _zug_id)
from test_alternativrezepte import datei  # noqa: F401  (Fixture)


def _zuege_im_dokument(text: str) -> int:
    """Wie viele Zug-Kästen in einer Antwort stehen."""
    return text.count('id="zug-')


def _zeilen(pfad, mid: int) -> list[tuple]:
    con = db.connect(pfad)
    try:
        return [tuple(r) for r in con.execute(
            "SELECT id, decision FROM chat_suggestion"
            " WHERE chat_message_id = ? ORDER BY id", (mid,))]
    finally:
        con.close()


def _posten(pfad) -> list[tuple]:
    con = db.connect(pfad)
    try:
        return [tuple(r) for r in con.execute(
            "SELECT product_id, qty FROM order_item ORDER BY id")]
    finally:
        con.close()


def _ein_zug(pfad, tmp_path, *, wechsel: int = 0):
    """Ein Chefkoch-Zug und `wechsel` Wechsel darauf — zurück der Client."""
    _pho_geholt(pfad)
    client, _, _ = _shop(pfad, tmp_path,
                         antworten=_antworten(pfad, 1 + wechsel))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    return client


# --------------------------------------------------------------------------
# 1. Kein Zuwachs: nach drei Wechseln steht EIN Zug da

def test_drei_wechsel_hinterlassen_einen_zug(datei, tmp_path):
    """Punkt 5 des Tickets, gemessen an der Seite selbst.

    Vor diesem Ticket wuchs der Verlauf bei jedem Wechsel um einen ganzen
    Zug — Karte, Alternativen, Zutatenliste, Vorschläge —, und das Neuladen
    brachte sie alle wieder.
    """
    client = _ein_zug(datei, tmp_path, wechsel=3)
    ziele = [PHO_GA, PHO_BO, PHO_GA]
    seiten = []
    for ziel in ziele:
        _wechsel(client, _zug_id(datei), ziel)
        seiten.append(client.get("/chat").text)

    for i, seite in enumerate(seiten):
        # Zwei Kästen: die Frage und die Antwort darauf. Nicht mehr.
        assert _zuege_im_dokument(seite) == 2, f"nach Wechsel {i + 1}"
    # Und die Seite ist nach dem dritten Wechsel so gross wie nach dem
    # ersten — beide zeigen Pho Ga, also denselben Zug.
    assert abs(len(seiten[2]) - len(seiten[0])) < 200, \
        (len(seiten[0]), len(seiten[2]))


def test_die_datenbank_zieht_mit(datei, tmp_path):
    """Was im Dokument steht, steht auch in der Datenbank — sonst holt ein
    Neuladen die alten Züge zurück.

    Die Zeilen selbst bleiben (nichts wird gelöscht); sie tragen nur eine
    Nachfolgerin, und `verlauf()` zeigt von einer Kette das letzte Glied.
    """
    client = _ein_zug(datei, tmp_path, wechsel=3)
    for ziel in (PHO_GA, PHO_BO, PHO_GA):
        _wechsel(client, _zug_id(datei), ziel)

    con = db.connect(datei)
    try:
        order_id = con.execute("SELECT id FROM orders").fetchone()["id"]
        alle = con.execute("SELECT count(*) AS n FROM chat_message"
                           ).fetchone()["n"]
        gezeigt = vorschlagsliste.verlauf(con, order_id)
    finally:
        con.close()
    assert alle == 8, "vier Züge à zwei Zeilen sollten in der Datenbank stehen"
    assert [z["role"] for z in gezeigt] == ["user", "assistant"]
    # Die Wurzel ist beide Male die des ERSTEN Zugs: die Kette hängt nicht
    # aneinander, sondern an ihrem Anfang.
    assert [z["ersetzt"] for z in gezeigt] == [1, 2]


def test_ein_neuladen_zeigt_dasselbe_wie_das_bruchstueck(datei, tmp_path):
    """Die Abnahme des Tickets: kein wiederauferstandener Zug.

    Der Vergleich ist die Nummer des Zugs im Bruchstück gegen die auf der
    frisch geladenen Seite — dieselbe, und sonst keine.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    _erste, zweite = _wechsel(client, mid, PHO_GA)
    neu = _zug_id(datei)

    seite = client.get("/chat").text
    assert f'id="zug-{neu}"' in zweite.text and f'id="zug-{neu}"' in seite
    assert f'id="zug-{mid}"' not in seite, "der alte Zug ist wieder da"
    # Genau EINE Rezeptkarte, und sie zeigt das gewählte Rezept. („Pho Bo"
    # steht weiter auf der Seite — als eine der sechs Alternativen.)
    assert seite.count('<section class="zugrezept">') == 1
    assert "<h3>Pho Ga</h3>" in seite
    assert "<h3>Pho Bo - Vietnamesische" not in seite


# --------------------------------------------------------------------------
# 2. Entschiedenes bleibt — Zeile, Label und Korbwirkung

def test_eine_entschiedene_zeile_ueberlebt_den_wechsel(datei, tmp_path):
    """Punkt 2 des Tickets. **Das ist der Fall, für den nicht gelöscht wird.**

    Ein „Ja" liegt im Korb und trägt beim Abschicken ein Eval-Label (Spec
    8.1, WB-329). Die Zeile mit `ON DELETE CASCADE` wegzuräumen nähme beides
    mit — und zwar still.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    zeilen = _zeilen(datei, mid)
    assert len(zeilen) >= 2, zeilen
    client.post(f"/chat/vorschlag/{zeilen[0][0]}/entscheiden?decision=kept",
                headers=HTMX)
    client.post(f"/chat/vorschlag/{zeilen[1][0]}/entscheiden?decision=removed",
                headers=HTMX)
    korb = _posten(datei)
    assert korb, "nichts im Korb — der Test prüft dann nichts"

    _erste, zweite = _wechsel(client, mid, PHO_GA)

    # 1. Die Zeilen stehen unverändert in der Datenbank.
    assert _zeilen(datei, mid) == [(zeilen[0][0], "kept"),
                                   (zeilen[1][0], "removed")] + zeilen[2:]
    # 2. Der Korb ist unberührt.
    assert _posten(datei) == korb
    # 3. Und sie sind zu SEHEN — im Bruchstück wie nach dem Neuladen.
    for text in (zweite.text, client.get("/chat").text):
        assert f'id="zug-{mid}"' in text, "der Rest des alten Zugs fehlt"
        assert 'class="zug ersetzt"' in text
        assert f'id="vorschlag-{zeilen[0][0]}"' in text
        assert f'id="vorschlag-{zeilen[1][0]}"' in text
        assert "Vom vorigen Rezept bleibt, was du entschieden hattest" \
            in _eine_zeile(text)


def test_von_einem_ersetzten_zug_bleibt_nur_das_entschiedene(datei, tmp_path):
    """Die offenen Zeilen fragen nach einem abgewählten Rezept.

    Sie bleiben in der Datenbank stehen — gelöscht wird nichts —, aber im
    Verlauf hätten sie nichts mehr zu suchen: „Ja" oder „Nein" zu einer
    Zutat, die zu einem anderen Rezept gehört, wäre eine Frage ohne Sinn.
    Nach Spec 8.1 zählt eine offene Zeile ohnehin nirgends.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    zeilen = _zeilen(datei, mid)
    client.post(f"/chat/vorschlag/{zeilen[0][0]}/entscheiden?decision=kept",
                headers=HTMX)
    offen = [z for z in _zeilen(datei, mid) if z[1] == "offen"]
    assert offen, "kein offener Vorschlag — der Test prüft dann nichts"

    _wechsel(client, mid, PHO_GA)
    seite = client.get("/chat").text

    for sid, _ in offen:
        assert f'id="vorschlag-{sid}"' not in seite, sid
    # In der Datenbank stehen sie weiter.
    assert len(_zeilen(datei, mid)) == len(zeilen)
    # Und die Karte des abgewählten Rezepts steht nicht mehr da.
    assert seite.count('<section class="zugrezept">') == 1
    assert "<h3>Pho Bo - Vietnamesische" not in seite


def test_eine_zurueckgenommene_zeile_bleibt_auch_stehen(datei, tmp_path):
    """WB-361 endet sonst in einer Sackgasse.

    Ein zurückgenommenes „Ja" lässt den Korbposten absichtlich liegen — die
    Oberfläche sagt es an dem Knopf, der es zurücknimmt. Verschwände die
    Zeile beim nächsten Rezeptwechsel trotzdem, stünde ein Posten im Korb,
    zu dem es keine Zeile mehr gibt, und der Weg zurück wäre zu.

    Eine zurückgenommene ABLEHNUNG fällt nicht darunter: sie ist wieder
    offen und hat nirgends eine Spur hinterlassen.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    zeilen = _zeilen(datei, mid)
    ja, nein = zeilen[0][0], zeilen[1][0]
    for sid, weg in ((ja, "kept"), (nein, "removed")):
        client.post(f"/chat/vorschlag/{sid}/entscheiden?decision={weg}",
                    headers=HTMX)
        client.post(f"/chat/vorschlag/{sid}/entscheiden?decision=offen",
                    headers=HTMX)
    assert _posten(datei), "der Korbposten sollte liegen bleiben (WB-361)"

    _wechsel(client, mid, PHO_GA)
    seite = client.get("/chat").text

    assert f'id="vorschlag-{ja}"' in seite, "die Zeile zum Korbposten fehlt"
    assert f'id="vorschlag-{nein}"' not in seite, \
        "eine zurückgenommene Ablehnung ist wieder offen und bleibt nicht"


def test_das_band_wird_nach_dem_ersten_tipp_nicht_falsch(datei, tmp_path):
    """WB-400 Runde 4, Punkt 2: kein Satz, der über seinem Gegenbeispiel steht.

    „Seine unberührten Zeilen sind weg" stimmt nur, solange in der Quittung
    nichts offen ist. Ein „rückgängig" — genau wofür die Quittung da ist —
    öffnet die Zeile darunter wieder, mit lebendem Ja/Nein, und das übersteht
    das Neuladen (`eingelegt_at` hält sie sichtbar). Der Halbsatz fällt dann
    weg — die Rücknahme ist der Zweck der Quittung, eine eingefrorene Marke
    zeigte einen Zustand, den die Datenbank nicht mehr hat — und kommt mit
    der nächsten Entscheidung zurück.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    ja = _zeilen(datei, mid)[0][0]
    client.post(f"/chat/vorschlag/{ja}/entscheiden?decision=kept",
                headers=HTMX)
    _wechsel(client, mid, PHO_GA)

    # Frisch gewechselt ist alles in der Quittung entschieden — der Satz
    # steht ganz.
    seite = _eine_zeile(client.get("/chat").text)
    assert "seine unberührten Zeilen sind weg" in seite

    # Die Rücknahme IN der Quittung tauscht nur die Zeile; das Band drüber
    # muss deshalb out-of-band mitkommen — ohne den Halbsatz, denn unter ihm
    # steht jetzt eine offene Zeile mit lebendem Ja/Nein.
    antwort = client.post(
        f"/chat/vorschlag/{ja}/entscheiden?decision=offen", headers=HTMX)
    assert f'id="quittungsband-{mid}"' in antwort.text
    assert "hx-swap-oob" in antwort.text
    flach = _eine_zeile(antwort.text)
    assert "Der Vorschlag selbst ist ersetzt." in flach
    assert "unberührten Zeilen" not in flach

    # Das Neuladen sagt dasselbe — die offene Zeile steht ja noch da.
    seite = _eine_zeile(client.get("/chat").text)
    assert "Vom vorigen Rezept bleibt" in seite
    assert "unberührten Zeilen" not in seite

    # Wieder entschieden: nichts mehr offen, der Halbsatz kommt zurück.
    client.post(f"/chat/vorschlag/{ja}/entscheiden?decision=kept",
                headers=HTMX)
    seite = _eine_zeile(client.get("/chat").text)
    assert "seine unberührten Zeilen sind weg" in seite


def test_an_einem_lebenden_zug_reist_kein_band_mit(datei, tmp_path):
    """Die Kehrseite des OOB-Nachtrags: er kostet nur, wo es ihn gibt.

    Ein Tipp an einem gewöhnlichen Zug hat kein Quittungsband über sich —
    die Antwort darf keins mitschicken, sonst zahlt jede der 150 Zeilen
    einer Liste für einen Kasten, den es nicht gibt (WB-372).
    """
    client = _ein_zug(datei, tmp_path)
    mid = _zug_id(datei)
    ja = _zeilen(datei, mid)[0][0]
    antwort = client.post(
        f"/chat/vorschlag/{ja}/entscheiden?decision=kept", headers=HTMX)
    assert "quittungsband" not in antwort.text


def test_die_karte_des_alten_rezepts_ist_weg_der_rest_bleibt(datei, tmp_path):
    """Der Rest ist eine Quittung und kein Vorschlag.

    Keine Rezeptkarte (sie zeigte das abgewählte Rezept), keine
    Sammelknöpfe (sie entschieden über die offenen Zeilen), keine Sorten
    (sie führten in eine neue Suche zu ihm).
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    zeilen = _zeilen(datei, mid)
    client.post(f"/chat/vorschlag/{zeilen[0][0]}/entscheiden?decision=kept",
                headers=HTMX)
    _wechsel(client, mid, PHO_GA)

    seite = client.get("/chat").text
    rest = seite.split('<div class="zug ersetzt" id="zug-%d">' % mid, 1)[1]
    rest = rest.split('id="zug-', 1)[0]
    assert '<section class="zugrezept">' not in rest, rest[:400]
    assert 'id="sammel-%d"' % mid not in rest
    assert '<form class="andere"' not in rest


# --------------------------------------------------------------------------
# 3. Kein Label verfälschen

def test_ein_wechsel_ohne_entscheidung_ruehrt_die_quote_nicht_an(datei,
                                                                 tmp_path):
    """Die ausdrückliche Nebenbedingung des Tickets.

    Ein ersetzter Zug ohne Entscheidungen darf die Trefferquote nicht
    verändern — und er tut es nicht, weil er nach Spec 8.1 gar kein Label
    trägt: alle seine Zeilen sind offen, `quote` ist `None`, und
    `labels.annotationen` überspringt ihn.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    con = db.connect(datei)
    try:
        # Ohne Span gäbe es nichts zu annotieren; im echten Lauf trägt ihn
        # `chat.turn` ein, hier wird er von Hand gesetzt.
        vorschlagsliste.span_setzen(con, mid, "span-alt")
        order_id = con.execute("SELECT id FROM orders").fetchone()["id"]
        vorher = labels.annotationen(con, order_id)
    finally:
        con.close()

    _wechsel(client, mid, PHO_GA)

    con = db.connect(datei)
    try:
        vorschlagsliste.span_setzen(con, _zug_id(datei), "span-neu")
        nachher = labels.annotationen(con, order_id)
        alt = vorschlagsliste.quote(con, mid)
    finally:
        con.close()
    assert alt["quote"] is None and alt["offen"] == alt["vorgeschlagen"]
    # Vorher trug er GENAU EINE Annotation, und keine Quote: den Ausgang
    # seines Rezeptentwurfs (WB-337). Nachher trägt er gar keine mehr — aus
    # ihm ist kein Rezept geworden, weil die Nutzerin ein anderes gewählt
    # hat, und nicht, weil keine Zutat bestätigt wurde. Ein `empty` dafür
    # wäre ein Fehlschlag, wo eine Wahl stattfand.
    assert [(a["span_id"], a["name"]) for a in vorher] \
        == [("span-alt", "recipe")], vorher
    assert [a["span_id"] for a in nachher] == ["span-neu"], nachher


def test_die_entscheidungen_des_alten_zugs_bleiben_im_label(datei, tmp_path):
    """Und andersherum: was entschieden wurde, zählt weiter.

    Der ersetzte Zug behält seine eigene Quote — sie ist die Aussage über
    SEINE Vorschläge und über niemanden sonst. Der Wechsel ist kein Urteil
    darüber, ob das Modell damals richtig zugeordnet hat.
    """
    client = _ein_zug(datei, tmp_path, wechsel=1)
    mid = _zug_id(datei)
    zeilen = _zeilen(datei, mid)
    client.post(f"/chat/vorschlag/{zeilen[0][0]}/entscheiden?decision=kept",
                headers=HTMX)
    con = db.connect(datei)
    try:
        vorher = vorschlagsliste.quote(con, mid)
    finally:
        con.close()

    _wechsel(client, mid, PHO_GA)

    con = db.connect(datei)
    try:
        assert vorschlagsliste.quote(con, mid) == vorher
        assert vorher["quote"] == 1.0
    finally:
        con.close()


# --------------------------------------------------------------------------
# 4. Der ersetzte Zug behält seinen Platz im Verlauf

def test_ein_gewechselter_zug_bleibt_an_seiner_stelle(datei, tmp_path):
    """Sonst stünde er im Dokument mittendrin und nach dem Neuladen unten.

    Genau dieser Unterschied ist es, den das Ticket abschafft: `ersetzt` ist
    nicht nur die Marke, sondern auch die Sortiergrösse
    (`ORDER BY coalesce(ersetzt, id), id`).
    """
    _pho_geholt(datei)
    client, _, _ = _shop(datei, tmp_path, antworten=_antworten(datei, 3))
    client.post("/chat", data={"satz": "alles für Pho"}, headers=HTMX)
    erster = _zug_id(datei)
    client.post("/chat", data={"satz": "alles für Pho, und Klopapier"},
                headers=HTMX)
    zweiter = _zug_id(datei)
    assert zweiter > erster

    _wechsel(client, erster, PHO_GA)

    con = db.connect(datei)
    try:
        order_id = con.execute("SELECT id FROM orders").fetchone()["id"]
        gezeigt = vorschlagsliste.verlauf(con, order_id)
    finally:
        con.close()
    saetze = [z["content"] for z in gezeigt if z["role"] == "user"]
    assert saetze == ["alles für Pho", "alles für Pho, und Klopapier"]
    # Der neue Zug hat die höchste id und steht trotzdem vorne.
    assert gezeigt[1]["id"] > gezeigt[3]["id"]


def test_der_verlaufsschnitt_zaehlt_nur_sichtbare_zuege(datei, tmp_path):
    """Die Anzeigegrenze aus WB-372 zählt ZÜGE, keine Zeilen.

    Zählte sie die überholten mit, schöbe sich die Grenze bei jedem Wechsel
    um einen Zug weiter — und der sichtbare Verlauf schrumpfte bei jedem
    Tipp auf eine Alternative.
    """
    client = _ein_zug(datei, tmp_path, wechsel=2)
    for ziel in (PHO_GA, PHO_BO):
        _wechsel(client, _zug_id(datei), ziel)

    con = db.connect(datei)
    try:
        order_id = con.execute("SELECT id FROM orders").fetchone()["id"]
        # Ein einziger sichtbarer Zug, und die Grenze für drei Züge liegt
        # damit auf `None`: es gibt nichts abzuschneiden.
        assert vorschlagsliste.verlauf_ab(con, order_id, 3) is None
        assert vorschlagsliste.umfang(con, order_id)["zuege"] == 1
    finally:
        con.close()


def test_die_seite_haelt_den_getauschten_zug_an_seiner_stelle(datei, tmp_path):
    """Der Anker gegen den Bildlauf des Browsers.

    Am Messstand gemessen: ohne ihn landete der Blick nach dem zweiten und
    dritten Wechsel am Seitenende, 3.839 und 4.455 Pixel unter dem Tipp. Der
    Grund ist Firefox' „scroll anchoring": zwischen den beiden Hälften des
    Wechsels schrumpft die Seite um mehrere tausend Pixel (die
    Vorschlagsliste ist weg) und wächst danach wieder — und der Browser hält
    sich dabei an einen Knoten seiner Wahl.
    """
    client = _ein_zug(datei, tmp_path)
    seite = client.get("/chat").text
    assert "htmx:beforeSwap" in seite and "htmx:afterSettle" in seite
    assert "getBoundingClientRect" in seite
    # Und kein `show:` im Chat: der Wechsel scrollt nicht, er ersetzt.
    assert "show:#wechsel-" not in seite
    assert "show:#gewechselt-" not in seite


# --------------------------------------------------------------------------
# 5. Der Altbestand

def test_eine_alte_datenbank_bekommt_die_spalte(leere_db_datei):
    """`chat_message.ersetzt` kommt per ALTER TABLE nach.

    Ohne Nachtrag für den Altbestand, und richtigerweise: vor diesem Ticket
    hat kein Wechsel je einen Zug ersetzt — er hängte einen neuen an. NULL
    heisst „diese Zeile löst nichts ab", und das ist für jede vorhandene
    Zeile wahr.
    """
    con = db.connect(leere_db_datei)
    try:
        con.execute("ALTER TABLE chat_message DROP COLUMN ersetzt")
        con.commit()
    finally:
        con.close()

    con = db.connect(leere_db_datei)
    try:
        db.migrate(con)
        db.migrate(con)          # idempotent
        spalten = [r[1] for r in con.execute("PRAGMA table_info(chat_message)")]
        assert "ersetzt" in spalten
        assert con.execute("SELECT count(*) AS n FROM chat_message"
                           " WHERE ersetzt IS NOT NULL").fetchone()["n"] == 0
    finally:
        con.close()
