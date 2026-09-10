"""Der Zug `plan.woche`: das Modell belegt die offenen Tage (Phase 2).

Ein `CHAIN`-Span je Zug, wie `chat.turn` — darunter der LLM-Span der Stufe
und, wo ein neu belegtes Rezept noch keine gemerkte Zuordnung hat, die
`recipe.zuordnung`-Läufe mit ihren `catalog.search`-Retrievern. Damit steht
im Trace der ganze Weg von „Woche planen" bis zur Einkaufsliste, und die
Frage „lag es am Modell oder an der Suche" ist an derselben Stelle zu
beantworten wie im Chat.

**Der Planer fasst den Chat-Zug nicht an: er benutzt ihn.** Modellzugang,
Weckzustand, Guided-Schalter und das Vorwärmen der Zuordnung kommen aus
`Chat`; hier steht nur die Zuordnung Gericht -> Tag und ihre Prüfung.

Was verworfen wird, steht in `plan.woche()`; hier wird es gezählt
(`zettel.plan.rejected`) und nicht repariert.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from zettel import obs
from zettel.assistant import chat as chatmodul
from zettel.assistant import plan as stufen
from zettel.llm import wake
from zettel.llm.client import ModellNichtErreichbar
from zettel.wochenplan import bestand, liste, naehrwert, speicher, vorlage

#: `zettel.path` dieses Zugs — neben `llm`, `recipe`, `chefkoch`, `fanout`.
WEG_PLAN = "plan"

WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _tagname(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WOCHENTAGE[d.weekday()]} {d.day:02d}.{d.month:02d}."


def tage_fuer_modell(plan: dict) -> list[dict]:
    """Die Tage, wie Stufe 4 sie sieht: offen, oder festgelegt mit Gericht.

    Offen ist ein Tag ohne Rezept und einer mit `removed` — „Nein" heisst
    „nicht das", nicht „nichts". `kept` und auswärts sind Festlegungen.
    """
    fertig = []
    for t in plan["tage_liste"]:
        offen = (not t["auswaerts"]
                 and (t["recipe_id"] is None or t["decision"] == "removed"))
        fertig.append({
            "tag": int(t["pos"]) + 1,
            "tag_id": int(t["id"]),
            "name": _tagname(t["datum"]),
            "offen": offen,
            "festgelegt": ((t["rezept"] or {}).get("name")
                           if not offen and t["recipe_id"] else None),
            "festgelegt_id": (int(t["recipe_id"])
                              if not offen and t["recipe_id"] else None),
            # Was an diesem Tag abgelehnt wurde, kommt nicht wieder — auch
            # nicht an einem anderen Tag dieses Zugs.
            "abgelehnt_id": (int(t["recipe_id"])
                             if offen and t["recipe_id"] else None),
        })
    return fertig


class Planer:
    """Der Zug. Eine Instanz je Prozess, gebaut um den `Chat` des Shops."""

    def __init__(self, chat: chatmodul.Chat):
        self.chat = chat

    def rahmen_lesen(self, satz: str) -> stufen.Rahmenlesung:
        """Stufe 5: ein Satz -> geprüfte Felder, mit eigenem Span.

        `zettel.rahmen.rejected` zählt, was das Modell nannte, ohne dass es
        im Satz stand. Wirft `ChatNichtVerfuegbar`, wenn die Box nicht
        bedient, und `PlanFehler`, wenn die Antwort kein Objekt ist — der
        Aufrufer zeigt dann das Formular, nicht einen halben Plan.
        """
        zustand = self.chat.zustand()
        if not zustand.bedient:
            raise chatmodul.ChatNichtVerfuegbar(zustand)
        with obs.chain("plan.rahmen", eingabe={"satz": satz}) as span:
            obs.setze(span, {obs.PFAD: WEG_PLAN})
            try:
                with obs.stufe("plan.rahmen"):
                    lesung = stufen.rahmen_lesen(
                        self.chat.zugang, satz, guided=self.chat.guided,
                        denken=self.chat.denken)
            except ModellNichtErreichbar as e:
                raise chatmodul.ChatNichtVerfuegbar(
                    wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e
            obs.setze(span, {
                "zettel.rahmen.rejected": len(lesung.verworfen),
                "zettel.rahmen.fields": len([k for k, v in lesung.werte.items()
                                             if k != "bestand" and v is not None]),
                "zettel.rahmen.stock": len(lesung.werte.get("bestand") or [])})
            obs.setze_ausgabe(span, lesung.werte)
            return lesung

    def planen(self, con: sqlite3.Connection, plan_id: int, *,
               vorwaermen: bool = True) -> dict:
        """Belegt die offenen Tage. Gibt einen Bericht zurück, wirft nur, wenn
        die Box nicht bedient (`ChatNichtVerfuegbar`) — wie der Chat.

        Ohne offenen Tag oder ohne ein einziges Gericht zur Wahl wird das
        Modell nicht gefragt und kein Span geöffnet: ein Zug, der nichts
        fragen kann, ist kein Zug.
        """
        plan = speicher.laden(con, plan_id)
        rahmen = speicher.rahmen_von(plan)
        tage = tage_fuer_modell(plan)
        offen = [t for t in tage if t["offen"]]
        abgelehnt = {t["abgelehnt_id"] for t in tage if t["abgelehnt_id"]}
        gerichte = [g for g in vorlage.gerichte(con, rahmen)
                    if int(g["id"]) not in abgelehnt]
        bestand_namen = [b["produkt_name"] or b["name"]
                         for b in plan["bestand"] if b["decision"] == "kept"]
        bericht = {"plan_id": int(plan_id), "offen": len(offen),
                   "vorgelegt": len(gerichte), "belegt": 0, "verworfen": 0,
                   "vorgewaermt": 0, "bestand_vorgeschlagen": 0,
                   "fehler": None, "gewaehlt": []}
        if not offen:
            bericht["meldung"] = "kein_offener_tag"
            return bericht
        if not gerichte:
            bericht["meldung"] = "nichts_zur_wahl"
            return bericht

        zustand = self.chat.zustand()
        if not zustand.bedient:
            raise chatmodul.ChatNichtVerfuegbar(zustand)

        eingabe = {"plan_id": int(plan_id), "personen": plan["personen"],
                   "max_minuten": plan["max_minuten"],
                   "bestand": bestand_namen,
                   "tage": [{"tag": t["tag"], "name": t["name"],
                             "offen": t["offen"], "festgelegt": t["festgelegt"]}
                            for t in tage]}
        with obs.chain("plan.woche", eingabe=eingabe) as span:
            obs.setze(span, {obs.PFAD: WEG_PLAN,
                             "session.id": f"plan-{int(plan_id)}",
                             "zettel.plan_id": int(plan_id),
                             "zettel.plan.days": len(offen),
                             "zettel.plan.fixed": len(tage) - len(offen),
                             "zettel.plan.presented": len(gerichte)})
            try:
                with obs.stufe("plan.woche"):
                    wahl = stufen.woche(
                        self.chat.zugang, tage, gerichte,
                        personen=plan["personen"],
                        max_minuten=plan["max_minuten"],
                        bestand=bestand_namen,
                        vorlieben=plan.get("vorlieben"),
                        guided=self.chat.guided, denken=self.chat.denken)
            except stufen.PlanFehler as e:
                # Wie im Chat: eine kaputte Antwort ist eine Meldung, kein
                # Absturz. Der Plan bleibt, wie er war.
                bericht["fehler"] = str(e)
                bericht["meldung"] = "modell_kaputt"
                obs.setze(span, {"zettel.plan.error": str(e)})
                speicher.status_setzen(con, plan_id, plan["status"],
                                       span_id=obs.span_id(span))
                return bericht
            except ModellNichtErreichbar as e:
                raise chatmodul.ChatNichtVerfuegbar(
                    wake.Zustand(wake.NICHT_ERREICHBAR, grund=str(e))) from e

            nach_tag = {t["tag"]: t["tag_id"] for t in offen}
            for w in wahl.gewaehlt:
                speicher.tag_setzen(con, nach_tag[w["tag"]], w["recipe_id"],
                                    grund=w["grund"])
            bericht["gewaehlt"] = list(wahl.gewaehlt)
            bericht["belegt"] = len(wahl.gewaehlt)
            bericht["verworfen"] = len(wahl.verworfen)

            if vorwaermen:
                bericht["vorgewaermt"] = self._vorwaermen(
                    con, {w["recipe_id"] for w in wahl.gewaehlt})

            # Der Bon schlägt vor (Phase 3) — jetzt, wo die Liste steht, gegen
            # die sich ein Kauf prüfen lässt. Kein Modell, wirft nicht.
            bericht["bestand_vorgeschlagen"] = bestand.vorschlagen(con, plan_id)

            neu = naehrwert.anreichern(con, speicher.laden(con, plan_id))
            einkauf = liste.einkaufsliste(con, neu)
            obs.setze(span, {
                "zettel.plan.assigned": len(wahl.gewaehlt),
                # Wie oft das Modell ein Gericht nannte, das ihm nie vorgelegt
                # wurde, oder einen Tag, der nicht offen war — dieselbe Zahl
                # mit derselben Bedeutung wie `zettel.rejected` im Chat.
                "zettel.plan.rejected": len(wahl.verworfen),
                "zettel.plan.rejected_reasons":
                    ", ".join(v["grund"] for v in wahl.verworfen) or None,
                # Zutaten, die nur an EINEM Tag vorkommen — der „Rest",
                # gezählt und nicht geschätzt.
                "zettel.plan.rest": einkauf["n_rest"],
                "zettel.plan.lines": einkauf["zu_kaufen"],
                "zettel.plan.covered": einkauf["gedeckt"],
                "zettel.plan.free_text": einkauf["ohne_produkt"],
                "zettel.plan.prewarmed": bericht["vorgewaermt"] or None,
                "zettel.plan.stock_suggested":
                    bericht["bestand_vorgeschlagen"] or None,
                "zettel.plan.price_cents": einkauf["preis_cents"],
                # Gerechnet aus den Packungen — Tage ohne rechenbare Zutat
                # zählen nicht (siehe `naehrwert`).
                "zettel.plan.kcal_per_serving":
                    neu["zusammenfassung"].get("kcal_je_portion"),
                "zettel.plan.protein_per_serving":
                    neu["zusammenfassung"].get("protein_je_portion"),
                "zettel.plan.kcal_days": neu["zusammenfassung"].get("naehrwert_tage") or None,
                "zettel.plan.kcal_target": neu.get("kcal_ziel"),
                "zettel.plan.over_budget_cents": einkauf["budget_ueber"],
            })
            obs.setze_ausgabe(span, [
                {"tag": w["tag"], "recipe_id": w["recipe_id"],
                 "name": w.get("name"), "grund": w.get("grund")}
                for w in wahl.gewaehlt])
            speicher.status_setzen(con, plan_id, neu["status"],
                                   span_id=obs.span_id(span))
        bericht["meldung"] = "ok"
        return bericht

    def _vorwaermen(self, con, recipe_ids: set[int]) -> int:
        """Die Zuordnung der neu belegten Rezepte — damit die Einkaufsliste
        Produkte trägt und nicht nur Zutatennamen.

        Nur wo nichts gemerkt ist und niemand Produkte verknüpft hat; ein
        Fehlschlag kostet die Zeile ihr Produkt (sie bleibt Freitext) und
        nicht den Zug. `zuordnung_vorwaermen` wirft nicht.
        """
        n = 0
        for rid in sorted(recipe_ids):
            hat_verknuepft = con.execute(
                "SELECT 1 FROM recipe_item WHERE recipe_id = ? LIMIT 1",
                (int(rid),)).fetchone()
            if hat_verknuepft:
                continue
            if self.chat.zuordnung_vorwaermen(con, int(rid)) == "ok":
                n += 1
        return n
