"""Der Bon schlägt vor, der Mensch bestätigt (Phase 3, Entwurf Abschnitt 3c).

„Dienstag 1 kg Kartoffeln gekauft — noch da?" Aus den BESTÄTIGTEN Käufen
der letzten Tage (`receipt_item.decision = 'kept'`, dieselbe Spalte, aus der
`kaeufe.echte_preise` liest) werden Bestandszeilen vorgeschlagen: `herkunft
= 'aus_bon'`, `decision = 'offen'`. Sie gelten erst mit einem Ja, und nur
für diesen Plan.

Kein Modell. Zwei Regeln, die die Vorschläge ehrlich halten:

* **Nur, was der Plan braucht.** Vorgeschlagen wird ein Kauf, dessen Produkt
  eine Zeile der Einkaufsliste trifft — über die Produkt-id, sonst über den
  Namen (`herkunft.punkte`, dieselbe Schwelle wie überall). Der ganze Bon
  wäre 30 Fragen; die Frage des Plans ist eine je Zeile.
* **Die Menge ist die gekaufte.** „1 kg" steht auf dem Bon; wie viel davon
  übrig ist, weiss nur der Mensch. Gezeigt wird, was gekauft wurde, und
  gewartet wird auf das Ja. Wer weniger hat, sagt Nein und schreibt den
  Rest als erklärten Bestand hin.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from zettel import mengen
from zettel.assistant import herkunft
from zettel.wochenplan import liste, speicher

#: Wie weit zurück ein Kauf noch ein Hinweis ist. Länger ist er eine
#: Vermutung, und Vermutungen sind das, was der Entwurf ausschliesst.
TAGE_ZURUECK = 7

SCHWELLE = herkunft.SCHWELLE

_SQL = (
    "SELECT i.id, i.product_id, i.qty, i.bon_text, r.bought_on, r.store,"
    "       p.name AS produkt_name, p.unit_text"
    "  FROM receipt_item i"
    "  JOIN receipt r ON r.id = i.receipt_id"
    "  JOIN product p ON p.id = i.product_id"
    " WHERE i.decision = 'kept' AND i.product_id IS NOT NULL"
    "   AND r.bought_on IS NOT NULL AND r.bought_on >= ? AND r.bought_on <= ?"
    " ORDER BY r.bought_on DESC, i.id DESC")


def kaeufe_der_letzten_tage(con: sqlite3.Connection, bis: date | str,
                            tage: int = TAGE_ZURUECK) -> list[dict]:
    """Bestätigte Käufe mit Produkt aus den letzten `tage` Tagen vor `bis`."""
    if isinstance(bis, str):
        bis = date.fromisoformat(bis[:10])
    von = bis - timedelta(days=tage)
    return [dict(r) for r in con.execute(
        _SQL, (von.isoformat(), bis.isoformat())).fetchall()]


def _trifft(kauf: dict, zeile: dict) -> bool:
    if zeile.get("product_id") and int(zeile["product_id"]) == int(kauf["product_id"]):
        return True
    namen = [zeile.get("name"), zeile.get("zutat"), zeile.get("free_text")]
    return any(herkunft.punkte(n, kauf["produkt_name"]) >= SCHWELLE
               for n in namen if n)


def passende(kaeufe: list[dict], zeilen: list[dict]) -> list[dict]:
    """Die Käufe, die eine Zeile der Einkaufsliste treffen — je Kauf einmal."""
    return [k for k in kaeufe if any(_trifft(k, z) for z in zeilen)]


def gekaufte_menge(kauf: dict) -> tuple[float | None, str | None]:
    """`qty × Packungsgrösse` in der Grundeinheit — oder `qty` Stück, wenn
    die Packung nicht lesbar ist. Nie geraten: „2 × 1 kg" sind 2000 g, „2 ×
    unlesbar" sind 2 Stück."""
    packung = mengen.packungsgroesse(kauf.get("unit_text"))
    qty = max(1, int(kauf.get("qty") or 1))
    if packung is None:
        return (float(qty), mengen.STUECK)
    return (mengen._rund(packung[0] * qty), packung[1])


def vorschlagen(con: sqlite3.Connection, plan_id: int, *,
                heute: date | None = None) -> int:
    """Legt Bestandsvorschläge aus den Bons an. Gibt die Zahl der NEUEN zurück.

    Idempotent: ein Kauf, der an diesem Plan schon vorgeschlagen wurde
    (`receipt_item_id`), wird nicht ein zweites Mal vorgelegt — egal, wie er
    entschieden wurde.
    """
    plan = speicher.laden(con, plan_id)
    einkauf = liste.einkaufsliste(con, plan)
    if not einkauf["zeilen"]:
        return 0
    bis = heute or date.today()
    kaeufe = kaeufe_der_letzten_tage(con, bis)
    schon = {b["receipt_item_id"] for b in plan["bestand"]
             if b.get("receipt_item_id")}
    neu = 0
    for k in passende(kaeufe, einkauf["zeilen"]):
        if k["id"] in schon:
            continue
        menge, einheit = gekaufte_menge(k)
        speicher.bestand_hinzufuegen(
            con, plan_id, k["produkt_name"], menge, einheit,
            product_id=int(k["product_id"]), herkunft=speicher.AUS_BON,
            receipt_item_id=int(k["id"]))
        schon.add(k["id"])
        neu += 1
    return neu
