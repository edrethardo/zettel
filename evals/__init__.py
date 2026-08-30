"""Datasets und Experiments für Zettel (Spec 8.2 und 8.3).

**Dieses Paket ist die Handarbeit, nicht das Gate.** Es gehört ausdrücklich
nicht in `checks/smoke.py` und nicht in die Testsuite: `dataset.py` redet mit
Phoenix, `experiment.py` zusätzlich mit der vLLM-Box. Beides ist genau die
Sorte Abhängigkeit, die Spec 13 aus dem Gate heraushalten will — ein Gate, das
eine schlafende Maschine im LAN braucht, ist rot, sobald jemand zwei Stunden
nicht gearbeitet hat.

Die Trennung ist im Code sichtbar und nicht bloss eine Verabredung:

* **Rein rechnende Teile** — die Beispiele selbst (`dataset.BEISPIELE`), der
  Kategorievergleich (`dataset.passt_kategorie`), beide deterministischen
  Bewertungen (`experiment.kategorie_praezision`,
  `experiment.zutaten_vollstaendigkeit`) und der Urteilsteil des LLM-Judge
  (`experiment.urteil_aus_antwort`) — hängen an keinem Netz. Sie werden in
  `tests/test_evals.py` geprüft, und dieser Test läuft ohne Phoenix, ohne
  Modell und ohne Katalogdatei.
* **Alles, was ein Netz braucht** — `phoenix.client`, `Modellzugang`,
  `db.connect` auf die echte Datei — wird erst INNERHALB der Funktionen
  importiert bzw. gebaut, die von der Kommandozeile aus aufgerufen werden.
  Ein `import evals.experiment` weckt deshalb nichts und verlangt kein
  laufendes Phoenix.

Gefahren, die hier absichtlich gemieden werden:

* **Erwartet wird auf Kategorieebene, nie auf Produkt-ID.** Ein Dataset, das
  „Produkt 64" verlangt, ist nach dem nächsten Crawl Schrott. Siehe
  `dataset.passt_kategorie`.
* **`zettel.weakest_rank` ist kein Bewertungskriterium.** Der bm25-Rang ist
  über Abfragen hinweg nicht geeicht — ein seltenes Wort bekommt strukturell
  einen höheren Rang. Eine Schwelle darauf misst den Katalog und nicht den
  Agenten (WB-328). In diesem Paket kommt der Rang deshalb nirgends in einem
  Score vor; er steht nur im Protokoll, damit man nachlesen kann, was die
  Suche vorgelegt hat.
"""
