"""Handprobe: was ein Blick in Korb und Chat und was ein „Ja" kostet (WB-372).

KEIN Test — die Zusicherung, dass ein Tipp nicht mehr den ganzen Verlauf
überträgt, steht in `tests/test_web_chat.py` und wird dort bei jedem Lauf
geprüft. Hier geht es um die ZAHL: wie viele Bytes es vorher waren und wie
viele danach, an einem Verlauf in der Grössenordnung des gemeldeten Standes.

    .venv/bin/python scripts/groesse_probe.py
    .venv/bin/python scripts/groesse_probe.py --grenze alle   # ohne Kürzung

Kein Netz, kein Modell, keine Box: der Verlauf wird direkt in eine frische
Datenbank geschrieben und der Shop über `fastapi.testclient` befragt. Die
Probe ist damit wiederholbar — die Zahlen im Ticket sind gegen den laufenden
Shop gemessen und schwanken mit seinem Inhalt, diese hier nicht.

Gemessen werden drei Dinge GETRENNT, weil verschiedene Hebel daran hängen:

    Chat    GET /chat               — hängt an der Verlaufsgrenze (Punkt 1)
    Korb    GET /warenkorb          — seit WB-382 ohne den Verlauf
    Tipp    POST .../entscheiden    — hängt am Tauschziel (Punkt 2)

`--grenze alle` schaltet die Kürzung ab und lässt nur das Tauschziel wirken;
so ist zu sehen, welcher Hebel wie viel bringt.

`--rezept` hängt einen echten Chefkoch-Zug hinten an (WB-383): Pho Bo, 23
Zutaten, 90 + 480 Minuten, 3.924 Zeichen Zubereitung. Er kostet die
Rezeptkarte — samt der Auswahl aus sechs Rezepten, die seit WB-387 darin
steht —, und genau die soll messbar sein. **Ohne die Schalter misst die
Probe unverändert das, was WB-372 gemessen hat** — sonst wären die Zahlen von
damals nicht mehr vergleichbar. Das Rezept kommt aus derselben aufgezeichneten
Antwort wie in der Testsuite; auch diese Probe geht nicht ins Netz.

**Die Zeile „Seite" von WB-372 ist seit WB-382 zwei Zeilen.** Damals trug eine
Seite beides; die Zahl 45 KB von damals ist mit der CHATSEITE zu vergleichen,
denn dort steht jetzt der Verlauf. Der Korb ist der Rest — und der ist klein.
Der Tipp wurde beim Umzug nebenbei billiger: er trug den ganzen Korb mit, weil
der danebenstand, und trägt jetzt nur noch die Korbbrücke.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from zettel import db, orders  # noqa: E402
from zettel.assistant import chat as chatmodul  # noqa: E402
from zettel.assistant import vorschlaege as vorschlagsliste  # noqa: E402
from zettel.assistant import zugrezept  # noqa: E402
from zettel.gerichte import lauf  # noqa: E402
from zettel.llm import wake  # noqa: E402
from zettel.scrapers import knuspr  # noqa: E402
from zettel.web import app as webapp  # noqa: E402

HTMX = {"HX-Request": "true"}
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

#: Die Grössenordnung des gemeldeten Standes: 34 Chatzeilen, 153 Vorschläge.
ZUEGE = 17
JE_ZUG = 9

#: So viele Kandidaten hebt die Suche je Vorschlag auf (WB-359). Sie sind der
#: Grund, warum eine verworfene Zeile so viel schwerer ist als eine offene.
KANDIDATEN = 6


class StummesLLM:
    """Fragt nie und darf nie gefragt werden — diese Probe chattet nicht."""

    def modell(self, **_):
        return "probe"

    def chat(self, *_a, **_k):
        raise AssertionError("Die Grössenprobe fragt kein Modell.")


class BedienteBox:
    def zustand(self):
        return wake.Zustand(wake.BEDIENT, modell="probe")


class VorlagenHTTP:
    """Der aufgezeichnete Katalog, derselbe wie in der Testsuite."""

    def __init__(self, seiten):
        self.seiten = list(seiten)

    def get(self, _url):
        return _Antwort(self.seiten.pop(0) if self.seiten else {"data": {}})


class _Antwort:
    def __init__(self, payload):
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


class ChefkochVorlage:
    """Die aufgezeichnete Chefkoch-Antwort — dieselbe wie in der Testsuite."""

    #: Das Rezept, das die Gewichtung aus der aufgezeichneten Suche wählt.
    PHO_BO = "3595991540759513"

    def __init__(self):
        self.seiten = {
            "/v2/recipes?": _fixture("chefkoch_pho_suche.json"),
            f"/v2/recipes/{self.PHO_BO}": _fixture("chefkoch_pho_rezept.json"),
        }

    def get(self, url):
        for teil, payload in self.seiten.items():
            if teil in url:
                return _Antwort(payload)
        raise AssertionError(f"Unerwartete URL: {url}")


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rezeptzug(pfad: Path) -> None:
    """Hängt EINEN Chefkoch-Zug hinten an — mit Rezeptkarte (WB-383).

    Kein Modell und keine Suche: die Vorschlagszeilen werden von Hand
    geschrieben, weil hier nicht der ZUG gemessen wird, sondern die ANSICHT.
    Was zählt, ist die Verknüpfung `chat_rezept` — an ihr hängt die Karte.
    """
    con = db.connect(pfad)
    lauf.hole_eines(con, ChefkochVorlage(), "Pho", pause_s=0,
                    schreib=lambda _: None)
    recipe_id = con.execute(
        "SELECT id FROM recipe ORDER BY id DESC LIMIT 1").fetchone()["id"]
    # Das Gericht dazu (WB-387): an ihm hängen die Alternativen, und ohne
    # diesen Verweis misst die Probe eine Karte ohne sie — also nicht die,
    # die der Shop zeigt.
    dish_id = con.execute("SELECT id FROM dish ORDER BY id DESC"
                          " LIMIT 1").fetchone()["id"]
    korb = orders.warenkorb(con)
    produkte = [r["id"] for r in
                con.execute("SELECT id FROM product ORDER BY id").fetchall()]
    vorschlagsliste.nachricht(con, korb, vorschlagsliste.ROLLE_NUTZERIN,
                              "alles für Pho")
    mid = vorschlagsliste.nachricht(
        con, korb, vorschlagsliste.ROLLE_AGENT,
        "„Pho Bo“ von Chefkoch — 23 Zutaten im Rezept.")
    for i in range(JE_ZUG):
        vorschlagsliste.vorschlag(con, mid, product_id=produkte[i],
                                  qty=1, search_term=f"Zutat {i}",
                                  rang=-1.5, dish_item=1)
    zugrezept.merken(con, mid, [recipe_id], [dish_id])
    con.commit()
    con.close()


def baue(pfad: Path) -> list[int]:
    """Legt Katalog, Verlauf und Korb an. Gibt die offenen Vorschläge zurück."""
    con = db.connect(pfad)
    db.migrate(con)
    payload = json.loads(
        (FIXTURES / "knuspr_milch.json").read_text(encoding="utf-8"))
    knuspr.crawl(con, VorlagenHTTP([payload]), ["milch"], pause_s=0)
    produkte = [r["id"] for r in
                con.execute("SELECT id FROM product ORDER BY id").fetchall()]
    korb = orders.warenkorb(con)
    offene = []
    for zug in range(ZUEGE):
        vorschlagsliste.nachricht(con, korb, vorschlagsliste.ROLLE_NUTZERIN,
                                  f"Zug {zug}: was brauchen wir noch?")
        mid = vorschlagsliste.nachricht(
            con, korb, vorschlagsliste.ROLLE_AGENT,
            f"Zu Zug {zug} habe ich das hier gefunden.")
        for i in range(JE_ZUG):
            pid = produkte[(zug * JE_ZUG + i) % len(produkte)]
            sid = vorschlagsliste.vorschlag(
                con, mid, product_id=pid, qty=1 + i % 3,
                search_term=f"Begriff {zug}-{i}", rang=-1.5 - i / 10)
            for pos, kandidat in enumerate(produkte[:KANDIDATEN]):
                con.execute(
                    "INSERT INTO chat_kandidat (suggestion_id, product_id,"
                    "  search_term, rank, pos) VALUES (?, ?, ?, ?, ?)",
                    (sid, kandidat, f"Begriff {zug}-{i}", -1.0 - pos / 10, pos))
            offene.append(sid)
    for pid in produkte[:3]:
        orders.einlegen(con, product_id=pid, qty=2)
    con.commit()
    con.close()
    return offene


def messe(pfad: Path, offene: list[int], bilder: Path):
    app = webapp.create_app(db_path=pfad, image_dir=bilder,
                            chat=chatmodul.Chat(StummesLLM(),
                                                wecker=BedienteBox()))
    client = TestClient(app)
    chatseite = client.get("/chat").text
    korbseite = client.get("/warenkorb").text
    # Ein „Ja" auf eine Zeile MITTEN im Verlauf — genau der Tipp, um den es
    # geht. Am Ende des Verlaufs sähe eine Kürzung besser aus, als sie ist.
    sid = offene[len(offene) // 2]
    tipp = client.post(f"/chat/vorschlag/{sid}/entscheiden?decision=kept",
                       headers=HTMX).text
    # Die mittlere Tauschgrösse: ein ganzer ZUG. Sie trägt seit WB-383 auch
    # die Rezeptkarte — deshalb steht sie hier und nicht nur der Tipp.
    mid = _letzter_zug(pfad)
    zug = client.post(f"/chat/{mid}/alle?decision=kept", headers=HTMX).text
    return chatseite, korbseite, tipp, zug


def _letzter_zug(pfad: Path) -> int:
    con = db.connect(pfad)
    try:
        return con.execute(
            "SELECT id FROM chat_message WHERE role = ?"
            " ORDER BY id DESC LIMIT 1",
            (vorschlagsliste.ROLLE_AGENT,)).fetchone()["id"]
    finally:
        con.close()


def bytes_(text: str) -> int:
    return len(text.encode("utf-8"))


def main() -> None:
    grenze = "vorgabe"
    if "--grenze" in sys.argv:
        wert = sys.argv[sys.argv.index("--grenze") + 1]
        grenze = None if wert == "alle" else int(wert)
        webapp.VERLAUF_ZUEGE = grenze
    mit_rezept = "--rezept" in sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pfad = tmp / "zettel.db"
        offene = baue(pfad)
        if mit_rezept:
            rezeptzug(pfad)
        chatseite, korbseite, tipp, zug = messe(pfad, offene, tmp / "bilder")
    chat = chatseite[chatseite.find('<section class="chat"'):]
    print(f"Verlaufsgrenze:     {grenze}")
    print(f"Rezeptzug dabei:    {'ja' if mit_rezept else 'nein'}")
    print(f"Seite /chat:        {bytes_(chatseite):>9,} Bytes")
    print(f"  davon #chat:      {bytes_(chat):>9,} Bytes")
    print(f"Seite /warenkorb:   {bytes_(korbseite):>9,} Bytes")
    print(f"beide zusammen:     {bytes_(chatseite) + bytes_(korbseite):>9,} Bytes")
    print(f"Tipp  Ja/Nein:      {bytes_(tipp):>9,} Bytes")
    print(f"Zug   (Sammelknopf):{bytes_(zug):>9,} Bytes")
    print(f"Formulare /chat:    {chatseite.count('<form'):>9,}")
    print(f"Formulare /korb:    {korbseite.count('<form'):>9,}")
    print(f"Formulare Tipp:     {tipp.count('<form'):>9,}")


if __name__ == "__main__":
    main()
