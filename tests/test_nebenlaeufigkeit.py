"""Der Shop darf nicht stillstehen, während ein Modell antwortet (WB-409).

Der Nutzer: „Schau dass alles in unter 100ms geht."

Gemessen am laufenden Shop, 2026-08-30: `GET /chat` brauchte **14,17 s**
statt 4 ms, solange irgendwo ein Modellaufruf lief — für beide Nutzerinnen,
auf jeder Seite, auch der Pick-Liste im Laden. Die Ursache war ein `async
def`, das blockierend auf das Modell wartete und damit die Ereignisschleife
anhielt.

Diese drei Prüfungen sind Fallen und keine Messungen. Eine Zeitmessung im
Testlauf misst die Maschine, auf der sie läuft; was hier festgehalten wird,
sind die drei Entscheidungen, aus denen die gemessenen Zahlen folgen — und
jede einzelne lässt sich mit einem Handgriff versehentlich zurücknehmen.
"""
from __future__ import annotations

import inspect

from zettel import db
from zettel.llm import wake
from zettel.web import app as webapp

#: Die Eingänge, die auf ein Modell, auf Chefkoch oder auf den Weckruf warten
#: dürfen. Sie MÜSSEN im Threadpool laufen, also gewöhnliche `def` sein.
WARTENDE = ("chat_senden", "chat_sorten", "chat_rezept",
            "chat_rezept_vorschlaege", "chat_rezept_vorwaermen",
            "chat_zustand")


def _eingaenge(app) -> dict:
    return {r.name: r.endpoint for r in app.routes if hasattr(r, "endpoint")}


def test_wartende_eingaenge_laufen_im_threadpool(tmp_path):
    """`async def` + blockierender Aufruf = der ganze Shop steht.

    FastAPI führt ein gewöhnliches `def` im Threadpool aus und lässt die
    Ereignisschleife frei. Wer hier ein `async` davorschreibt, friert den
    Shop für die Dauer des Modellaufrufs ein — und sieht es nicht, weil der
    eigene Request ja antwortet.
    """
    app = webapp.create_app(db_path=tmp_path / "zettel.db",
                            image_dir=tmp_path / "bilder")
    eingaenge = _eingaenge(app)
    for name in WARTENDE:
        assert name in eingaenge, f"Eingang {name} gibt es nicht mehr."
        assert not inspect.iscoroutinefunction(eingaenge[name]), (
            f"{name} ist eine Koroutine und blockiert damit die "
            "Ereignisschleife, sobald sie auf das Modell wartet.")


def test_die_datenbank_syncht_nicht_bei_jedem_commit(tmp_path):
    """Dreissig `commit` je Zug, und jedes war eine Plattenumdrehung.

    Gemessen mit cProfile: 216 ms von 264 ms gingen für 30 fsyncs drauf.
    `synchronous = NORMAL` (1) ist die übliche Stellung zu WAL; `FULL` (2)
    macht daraus wieder eine Viertelsekunde je Zug.
    """
    con = db.connect(tmp_path / "zettel.db")
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 1, (
            "synchronous steht nicht auf NORMAL — jedes commit ist wieder "
            "ein fsync.")
    finally:
        con.close()


def test_der_weckruf_wartet_hoechstens_eine_sekunde():
    """Drei Sekunden waren das Tausendfache der Messung.

    `GET /v1/models` gegen die echte Box: Median 2 ms, langsamste von 15
    Proben 163 ms. Der Timeout wird voll bezahlt, sooft die Box NICHT
    bedient — und dann steht er in jedem `/chat/zustand`.
    """
    assert wake.HEALTH_TIMEOUT_S <= 1.0, wake.HEALTH_TIMEOUT_S
    assert wake.HEALTH_CACHE_S >= 1.0, wake.HEALTH_CACHE_S


def test_derselbe_befund_wird_nicht_zweimal_geholt():
    """Ein Zug fragt zwei- bis dreimal nach demselben Zustand.

    Ohne die Frist kostet jede dieser Fragen eine eigene Runde zur Box — und
    bei einer Box, die nicht bedient, den vollen Timeout, dreimal.
    """
    gefragt = []

    def gezaehlt(endpunkt=None, **_):
        gefragt.append(endpunkt)
        return wake.Befund(wake.BEDIENT, modell="fake")

    uhr = iter([100.0, 100.5, 101.0, 110.0, 110.0, 110.0])
    wecker = wake.Wecker(endpunkt="http://kein.host/v1", uhr=lambda: next(uhr))
    echt, wake.health = wake.health, gezaehlt
    try:
        assert wecker.zustand().bedient
        assert wecker.zustand().bedient
        assert wecker.zustand().bedient
        assert len(gefragt) == 1, "Der Befund wurde nicht gemerkt."
        assert wecker.zustand().bedient          # 10 s später
        assert len(gefragt) == 2, "Der Befund wurde nie erneuert."
    finally:
        wake.health = echt


def test_die_trefferzahl_liest_aus_der_fts_heraus(tmp_path):
    """`count()` darf nicht über den Katalog laufen (WB-410).

    Mit einem gewöhnlichen `JOIN` drehte SQLite die Reihenfolge um — Index
    `ix_product_active` zuerst, also alle aktiven Produkte, und je Zeile eine
    FTS-Anfrage. Gemessen am echten Katalog: „bio joghurt natur" 1.871 ms für
    die Zahl 43, während `search()` dieselben Treffer in 1,3 ms holte.

    Geprüft wird der PLAN und nicht die Zeit: eine Zeitmessung im Testlauf
    misst die Maschine, auf der sie läuft. Der Plan sagt dieselbe Sache
    schärfer — steht die virtuelle Tabelle nicht an erster Stelle, ist der
    Fehler wieder da.
    """
    from zettel.catalog import search

    con = db.connect(tmp_path / "zettel.db")
    db.migrate(con)
    try:
        con.execute(
            "INSERT INTO product (source, external_id, name, price_cents,"
            " unit_text, category_l1) VALUES ('knuspr', 'x1', 'Bio Joghurt"
            " natur', 99, '500 g', 'Molkerei')")
        con.commit()
        query = search.fts_query("bio joghurt natur")
        platz = ", ".join("?" for _ in search.AUSGESCHLOSSENE_KATEGORIEN)
        plan = [tuple(r)[3] for r in con.execute(
            "EXPLAIN QUERY PLAN SELECT count(*) AS n"
            "  FROM product_fts f CROSS JOIN product p ON p.id = f.rowid"
            " WHERE product_fts MATCH ? AND p.active = 1"
            f"   AND coalesce(p.category_l1, '') NOT IN ({platz})",
            (query, *search.AUSGESCHLOSSENE_KATEGORIEN))]
        assert plan and "VIRTUAL TABLE" in plan[0], plan
        assert search.count(con, "bio joghurt natur") == 1
    finally:
        con.close()
