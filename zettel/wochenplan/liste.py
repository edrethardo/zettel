"""Die Einkaufsliste eines Plans — gerechnet, nicht generiert.

Alle Zutaten der Tage zusammenlegen, den erklärten Bestand abziehen, den
Rest in den bestehenden Korb. Kein Modell ist beteiligt; jede Zahl hier
kommt aus `mengen`, und wo `mengen` nichts rechnen kann, steht ein Grund und
keine Zahl.

## Woher die Zeilen eines Rezepts kommen — die Rangfolge

Der Entwurf rechnete mit `recipe_item.amount`. Am 06.09. nachgesehen: die
Tabelle ist leer, die 700 Mengen stehen in `recipe_ingredient`, und die wird
bei jedem Quellabruf neu geschrieben. Also, je Rezept:

1. **`recipe_item`** — die vom Menschen verknüpften Produkte (Spec 4), mit
   `amount` bei `servings` Portionen. Wo sie stehen, gelten sie; das ist
   dieselbe Quelle wie „alles in den Warenkorb" auf der Rezeptseite.
2. sonst **`recipe_zuordnung` + `herkunft.zuordnen`** — was das Modell schon
   einmal zu diesem Rezept ergeben hat (WB-408), mit den Mengen der Quelle
   daneben. Nichts davon wird kopiert; die Mengen entstehen beim Lesen, wie
   in `zuordnung.py` begründet.
3. sonst **`recipe_ingredient` als Freitext mit Menge** — kein Produkt, aber
   die Zeile geht nicht verloren. Phase 2 lässt das Modell die Zuordnung
   nachrechnen (`Chat.zuordnung_vorwaermen`); bis dahin steht die Zutat so da.

## Zusammenzählen und Abziehen

Schlüssel ist die Produkt-id, bei Freitext der gefaltete Name. Summiert wird
mit `mengen.summiere`; „4 Zehen" und „200 g" ergeben keine Summe, und dann
bleibt `bedarf = None` mit Grund. Der Bestand zieht nur ab, was sich rechnen
lässt: 500 g gegen 800 g ergibt 300 g, 6 Stück gegen 500 g ergibt nichts —
die Zeile bleibt und sagt, warum.

Die Packungszahl entsteht ERST im Korb (`korb.einlegen` addiert je Produkt
und rundet danach, WB-362). Der Preis hier ist eine Vorschau mit derselben
Rechnung (`mengen.rechne`) und heisst auch so.
"""
from __future__ import annotations

import sqlite3

from zettel import mengen, obs, orders
from zettel.assistant import herkunft, zuordnung
from zettel.bons import kaeufe
from zettel.gerichte import speicher as gerichtespeicher
from zettel.obs import labels
from zettel.orders import korb
from zettel.recipes import sammlung
from zettel.wochenplan import speicher
from zettel.wochenplan.speicher import WochenplanFehler

#: Herkunft einer Zeile — steht im Bericht und im Trace.
AUS_VERKNUEPFUNG = "verknuepft"
AUS_ZUORDNUNG = "zuordnung"
AUS_QUELLE = "quelle"

#: Ab wie vielen Punkten (`herkunft.punkte`) ein Bestandsname eine Zeile
#: trifft. Dieselbe Schwelle wie bei der Zuordnung Begriff -> Zutat: ein
#: Wortanfang reicht, und ein Gleichstand ist kein Treffer.
SCHWELLE = herkunft.SCHWELLE


# --------------------------------------------------------------------------
# Zeilen je Rezept

def _produktkopf(con: sqlite3.Connection, product_id) -> dict:
    row = con.execute(
        "SELECT id, name, unit_text, price_cents, active FROM product"
        " WHERE id = ?", (int(product_id),)).fetchone()
    return dict(row) if row else {}


def _zeile(*, product_id=None, free_text=None, name, bedarf, einheit,
           qty, recipe_id, zutat=None, woher, produkt=None) -> dict:
    return {"product_id": product_id, "free_text": free_text, "name": name,
            "bedarf": bedarf, "einheit": einheit, "qty": int(qty or 1),
            "recipe_id": recipe_id, "zutat": zutat, "woher": woher,
            "unit_text": (produkt or {}).get("unit_text"),
            "price_cents": (produkt or {}).get("price_cents"),
            "nicht_im_katalog": (bool(product_id)
                                 and (produkt or {}).get("active") != 1)}


def zeilen_je_rezept(con: sqlite3.Connection, recipe_id: int,
                     portionen=None) -> list[dict]:
    """Die Einkaufszeilen EINES Rezepts für `portionen`, nach der Rangfolge
    oben. Skaliert linear (`mengen.skaliere`), rundet nicht."""
    kopf = con.execute("SELECT id, name, servings FROM recipe WHERE id = ?",
                       (int(recipe_id),)).fetchone()
    if kopf is None:
        return []
    basis = kopf["servings"]
    ziel = portionen or basis

    verknuepft = sammlung.zutaten(con, int(recipe_id))
    if verknuepft:
        zeilen = []
        for z in verknuepft:
            produkt = _produktkopf(con, z["product_id"]) if z["product_id"] else None
            zeilen.append(_zeile(
                product_id=z["product_id"], free_text=z["free_text"],
                name=z["name"],
                bedarf=mengen.skaliere(z["amount"], basis, ziel),
                einheit=z["unit"], qty=z["qty"], recipe_id=int(recipe_id),
                woher=AUS_VERKNUEPFUNG, produkt=produkt))
        return zeilen

    quelle = gerichtespeicher.zutaten(con, int(recipe_id))
    gemerkt = zuordnung.lesen(con, int(recipe_id))
    if gemerkt is not None:
        begriffe = herkunft.zuordnen(quelle, zuordnung.begriffe_aus(gemerkt))
        zeilen = []
        for g, b in zip(gemerkt, begriffe):
            begriff = g["suchbegriffe"][0]
            bedarf = mengen.skaliere(b.get("bedarf"), basis, ziel)
            qty = g.get("wahl_menge") or g.get("menge") or 1
            if g["gewaehlt"] and g["product_id"]:
                produkt = _produktkopf(con, g["product_id"])
                zeilen.append(_zeile(
                    product_id=int(g["product_id"]),
                    name=produkt.get("name") or begriff,
                    bedarf=bedarf, einheit=b.get("einheit"), qty=qty,
                    recipe_id=int(recipe_id), zutat=b.get("zutat"),
                    woher=AUS_ZUORDNUNG, produkt=produkt))
            else:
                zeilen.append(_zeile(
                    free_text=begriff, name=begriff, bedarf=bedarf,
                    einheit=b.get("einheit"), qty=qty,
                    recipe_id=int(recipe_id), zutat=b.get("zutat"),
                    woher=AUS_ZUORDNUNG))
        return zeilen

    zeilen = []
    for z in quelle:
        name = z.get("name") or z.get("raw_name")
        if not name:
            continue
        menge = herkunft._menge(z)
        zeilen.append(_zeile(
            free_text=name, name=name,
            bedarf=mengen.skaliere(menge[0], basis, ziel) if menge else None,
            einheit=menge[1] if menge else None, qty=1,
            recipe_id=int(recipe_id), zutat=z.get("raw_name"),
            woher=AUS_QUELLE))
    return zeilen


# --------------------------------------------------------------------------
# Zusammenlegen

def _schluessel(z: dict):
    if z.get("product_id"):
        return ("p", int(z["product_id"]))
    return ("t", " ".join(herkunft._woerter(z["name"])))


def zusammenlegen(zeilen: list[dict]) -> list[dict]:
    """Gleiche Sachen über alle Tage zu EINER Zeile. Reihenfolge: erstes
    Vorkommen."""
    gebuendelt: dict = {}
    reihenfolge = []
    for z in zeilen:
        k = _schluessel(z)
        if k not in gebuendelt:
            reihenfolge.append(k)
            gebuendelt[k] = {**z, "tage": [], "rezepte": [], "grund": None,
                             "qty": 0, "teile": []}
        b = gebuendelt[k]
        if z.get("tag_pos") is not None and z["tag_pos"] not in b["tage"]:
            b["tage"].append(z["tag_pos"])
        if z.get("rezept_name") and z["rezept_name"] not in b["rezepte"]:
            b["rezepte"].append(z["rezept_name"])
        b["qty"] += int(z.get("qty") or 1)
        b["teile"].append((z.get("bedarf"), z.get("einheit")))
    fertig = []
    for k in reihenfolge:
        b = gebuendelt[k]
        summe = None
        gescheitert = False
        for menge, einheit in b.pop("teile"):
            if menge is None:
                continue
            summe = mengen.summiere(*(summe or (None, None)), menge, einheit)
            if summe is None:
                gescheitert = True
                break
        if gescheitert:
            b["bedarf"], b["einheit"] = None, None
            b["grund"] = ("die Mengen lassen sich nicht zusammenzählen "
                          "(verschiedene Einheiten)")
        elif summe is not None:
            b["bedarf"], b["einheit"] = summe
        else:
            b["bedarf"], b["einheit"] = None, None
        b["qty"] = max(1, b["qty"]) if b["bedarf"] is None else 1
        fertig.append(b)
    return fertig


# --------------------------------------------------------------------------
# Bestand abziehen

def _trifft(bestand: dict, zeile: dict) -> bool:
    """Trifft eine Bestandszeile eine Einkaufszeile?

    Dieselbe Produkt-id ist ein Treffer. Verschiedene ids sind noch kein
    Nicht-Treffer: der Bon nennt „Berliner Eisbären Eier", das Rezept hat
    „Frische Eier aus Bodenhaltung" gewählt — beides sind Eier, und die
    Herkunftszutat der Zeile („Ei(er)") trifft den Bestandsnamen. Verglichen
    wird in beide Richtungen mit `herkunft.punkte`, weil einmal die eine
    und einmal die andere Seite der kürzere Name ist (gemessen am Trockenlauf
    vom 06.09.: 10 Stück Eier vom Bon deckten 6 Stück Bedarf nicht, weil nur
    die ids verglichen wurden).
    """
    if bestand.get("product_id") and zeile.get("product_id") \
            and int(bestand["product_id"]) == int(zeile["product_id"]):
        return True
    namen = [zeile.get("zutat"), zeile.get("free_text"), zeile.get("name")]
    for n in namen:
        if not n:
            continue
        if (herkunft.punkte(n, bestand["name"]) >= SCHWELLE
                or herkunft.punkte(bestand["name"], n) >= SCHWELLE):
            return True
    return False


def bestand_abziehen(zeilen: list[dict], bestand: list[dict]) -> list[dict]:
    """Zieht bestätigten Bestand ab, wo sich das rechnen lässt.

    Je Zeile danach: `gedeckt` (True: nichts mehr zu kaufen), `bedarf` (der
    Rest), `bestand_hinweis` (ein Satz, wenn Bestand da war — auch wenn er
    sich nicht abziehen liess). Ein Bestand ohne Menge deckt nichts, steht
    aber als Hinweis an der Zeile: „Nudeln sind da" ist eine Auskunft, keine
    Rechnung.
    """
    behalten = [b for b in bestand if b.get("decision") == "kept"]
    fertig = []
    for z in zeilen:
        z = dict(z)
        z.setdefault("gedeckt", False)
        z.setdefault("bestand_hinweis", None)
        treffer = [b for b in behalten if _trifft(b, z)]
        if not treffer:
            fertig.append(z)
            continue
        vorrat = None
        unrechenbar = []
        ohne_menge = []
        for b in treffer:
            teil = mengen.in_grundeinheit(b.get("menge"), b.get("einheit"))
            if teil is None:
                ohne_menge.append(b)
                continue
            vorrat = mengen.summiere(*(vorrat or (None, None)), b["menge"],
                                     b.get("einheit"))
            if vorrat is None:
                unrechenbar.append(b)
                break
        bedarf = mengen.in_grundeinheit(z.get("bedarf"), z.get("einheit"))
        if vorrat is not None and bedarf is not None:
            passt = mengen.vergleichbar(vorrat[1], bedarf[1])
            if passt is None:
                z["bestand_hinweis"] = (
                    f"{mengen.schreibe(*vorrat)} da — lässt sich nicht gegen "
                    f"{mengen.schreibe(*bedarf)} rechnen")
            else:
                rest = bedarf[0] - vorrat[0] * passt[0]
                if rest <= 0:
                    z["gedeckt"] = True
                    z["bedarf"], z["einheit"] = 0.0, bedarf[1]
                    z["bestand_hinweis"] = f"{mengen.schreibe(*vorrat)} da — gedeckt"
                else:
                    z["bedarf"], z["einheit"] = mengen._rund(rest), bedarf[1]
                    z["bestand_hinweis"] = (
                        f"{mengen.schreibe(*vorrat)} da, noch "
                        f"{mengen.schreibe(z['bedarf'], z['einheit'])} zu kaufen")
        elif vorrat is not None:
            z["bestand_hinweis"] = (f"{mengen.schreibe(*vorrat)} da — der Bedarf "
                                    "hat keine Menge, nichts abgezogen")
        elif ohne_menge or unrechenbar:
            z["bestand_hinweis"] = (", ".join(b["name"] for b in treffer)
                                    + " ist da — ohne Menge, nichts abgezogen")
        fertig.append(z)
    return fertig


# --------------------------------------------------------------------------
# Die Liste

def _preis(con: sqlite3.Connection, z: dict) -> tuple[int | None, int | None]:
    """Preisvorschau einer Zeile: (Packungen, Cent) — oder (None, None)."""
    if not z.get("product_id"):
        return None, None
    rechnung = mengen.rechne(z.get("bedarf"), z.get("einheit"), z.get("unit_text"))
    packungen = rechnung.packungen if rechnung.ausrechenbar else z.get("qty") or 1
    preis = z.get("price_cents")
    echt = kaeufe.letzter_preis(con, int(z["product_id"]))
    if echt and echt.get("stueck_cents") is not None:
        preis = echt["stueck_cents"]
    if preis is None:
        return packungen, None
    return packungen, int(packungen) * int(preis)


def rest(zeilen: list[dict]) -> list[dict]:
    """Zutaten, die nur an EINEM Tag vorkommen — der zählbare „Rest"."""
    return [z for z in zeilen if len(z.get("tage") or []) == 1]


def einkaufsliste(con: sqlite3.Connection, plan: dict) -> dict:
    """Die Liste zum Plan, wie sie auf der Seite steht — ohne den Korb zu
    berühren."""
    roh = []
    for t in plan["tage_liste"]:
        if not t["zaehlt"]:
            continue
        for z in zeilen_je_rezept(con, t["recipe_id"], t["portionen"]):
            roh.append({**z, "tag_pos": t["pos"],
                        "rezept_name": (t["rezept"] or {}).get("name")})
    zeilen = bestand_abziehen(zusammenlegen(roh), plan["bestand"])
    summe = 0
    ohne_preis = 0
    for z in zeilen:
        z["mengensatz"] = (mengen.schreibe(z["bedarf"], z["einheit"])
                           if z.get("bedarf") is not None and not z["gedeckt"]
                           else None)
        if z["gedeckt"]:
            z["packungen"], z["preis_cents"] = None, None
            continue
        z["packungen"], z["preis_cents"] = _preis(con, z)
        if z["preis_cents"] is None:
            ohne_preis += 1
        else:
            summe += z["preis_cents"]
    budget = plan.get("budget_cents")
    zu_kaufen = [z for z in zeilen if not z["gedeckt"]]
    return {
        "zeilen": zeilen,
        "zu_kaufen": len(zu_kaufen),
        "gedeckt": sum(1 for z in zeilen if z["gedeckt"]),
        "ohne_produkt": sum(1 for z in zu_kaufen if not z.get("product_id")),
        "rest": rest(zeilen),
        "n_rest": len(rest(zeilen)),
        "preis_cents": summe if zu_kaufen else 0,
        "ohne_preis": ohne_preis,
        "budget_cents": budget,
        "budget_ueber": (summe - budget) if budget and summe > budget else None,
    }


def in_den_korb(con: sqlite3.Connection, plan_id: int) -> dict:
    """Legt die Einkaufsliste in den gemeinsamen Warenkorb.

    Über `korb.einlegen(menge=…)`, Zeile für Zeile — dort wird je Produkt
    addiert, auch mit dem, was schon im Korb liegt, und erst danach gegen die
    Packung gerundet. Gedeckte Zeilen bleiben draussen. Ein Plan ohne
    einzige Zeile wird abgelehnt statt stumm nichts zu tun (dieselbe
    Begründung wie `LeeresRezept`).
    """
    plan = speicher.laden(con, plan_id)
    liste = einkaufsliste(con, plan)
    zu_kaufen = [z for z in liste["zeilen"] if not z["gedeckt"]]
    if not zu_kaufen:
        raise WochenplanFehler(
            "Auf dem Plan steht nichts zu kaufen — kein Tag mit Rezept, "
            "oder alles ist gedeckt.")
    eingelegt, gescheitert = [], []
    # EIN Span für die Übergabe: die `korb.menge`-Spuren je Zeile hängen
    # darunter statt als zwanzig Wurzelzüge in der Trace-Liste zu stehen —
    # dort verdeckten sie den `plan.woche`-Zug, um den es geht (Trockenlauf
    # 06.09.: der oberste Wurzelzug war eine Mengenrechnung).
    with obs.chain("plan.korb", eingabe={"plan_id": int(plan_id),
                                         "zeilen": len(zu_kaufen)}) as span:
        obs.setze(span, {obs.PFAD: "plan", "zettel.plan_id": int(plan_id)})
        for z in zu_kaufen:
            try:
                item_id = korb.einlegen(
                    con, product_id=z.get("product_id"),
                    free_text=z.get("free_text"),
                    qty=z.get("qty") or 1, menge=z.get("bedarf"),
                    einheit=z.get("einheit"), begriff=z.get("name"))
            except orders.UngueltigerPosten as e:
                gescheitert.append({**z, "grund": str(e)})
                continue
            eingelegt.append({**z, "item_id": item_id})
        obs.setze(span, {"zettel.plan.lines": len(eingelegt),
                         "zettel.plan.covered": liste["gedeckt"],
                         "zettel.plan.free_text": liste["ohne_produkt"]})
        obs.setze_ausgabe(span, [{"product_id": z.get("product_id"),
                                  "name": z.get("name"), "bedarf": z.get("bedarf"),
                                  "einheit": z.get("einheit")} for z in eingelegt])
    order_id = orders.warenkorb(con)
    speicher.status_setzen(con, plan_id, speicher.IM_KORB, order_id=order_id)
    # Die Labels aus der Nutzung (Abschnitt 7): jetzt stehen die
    # Entscheidungen fest. Wirft nie — die Liste geht durch, egal was
    # Phoenix macht.
    labels.plan_schreiben(con, plan_id)
    return {"plan_id": int(plan_id), "order_id": order_id,
            "eingelegt": eingelegt, "gescheitert": gescheitert,
            "gedeckt": liste["gedeckt"], "ohne_produkt": liste["ohne_produkt"],
            "n_eingelegt": len(eingelegt)}
