"""Katalog: Volltextsuche (FTS5) und Kategoriebaum.

Beides liest ausschliesslich aus der lokalen SQLite-Datei — dieser Teil des
Shops geht nie ins Netz (Spec 3).

Bewusst ohne Re-Exports: `from zettel.catalog import search` würde sonst die
Funktion `search` liefern statt des Moduls, und `search.search(...)` wäre ein
AttributeError. Aufrufer importieren die Module.
"""
