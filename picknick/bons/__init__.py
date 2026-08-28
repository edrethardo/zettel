"""Kassenbons: ablegen, auslesen, den Käufen zuordnen (WB-344 und WB-358).

Fünf Teile, jeder mit einer Aufgabe:

* `ablage`   — hochladen, ablegen, auflisten, löschen. Kennt nur Bytes.
* `lesen`    — Datei -> Text, über `pdftotext` beziehungsweise `tesseract`.
* `zerlegen` — Text -> Posten. **Hier endet der Bon bei der Summe**; der
  Zahlungsteil wird gar nicht erst angesehen.
* `kaeufe`   — Posten -> Datenbank, und der echte Preis mit Laden und Datum.
* `zuordnung`— Bon-Text -> Katalogprodukt, über das Modell und `suche_kette()`.
* `lauf`     — das Ganze im Hintergrund, damit kein Request darauf wartet.

Gebündelt wird hier, damit Aufrufer weiter schlicht `from picknick import bons`
schreiben können — genauso wie bei `picknick.orders`.

**Die eine Zusage, die dieses Paket trägt:** in die Datenbank gehen
ausschliesslich Laden, Datum, Artikelname, Menge und Preis. Die maskierte
Kartennummer, die VU-Nummer, die Terminal-ID, die Trace- und die Belegnummer
stehen auf dem Bon und bleiben dort. Zwei Dinge halten das:

1. `zerlegen.artikelzeilen()` schneidet bei der Summenzeile ab. Was danach
   kommt, wird nie zu einem Posten.
2. Das Schema in `picknick/db.py` hat für nichts davon eine Spalte.

Beides ist zusammen geprüft (`tests/test_bons_kaeufe.py`): der Test liest die
ganze Datenbank als Text zurück und sucht die Kartennummer darin.
"""
from picknick.bons.ablage import (  # noqa: F401
    ERLAUBT, MAX_BYTES, SIGNATUREN, BonFehler, erkenne_typ, groesse_text,
    liste, loeschen, pfad_im_verzeichnis, sicherer_name, speichern)
from picknick.bons.lauf import (  # noqa: F401
    FEHLER, FERTIG, LAEUFT, NACHFRAGE_S, Laeufe, Stand, sofort)
from picknick.bons.lesen import (  # noqa: F401
    OCR_FEHLT_TEXT, LeseFehler, WerkzeugFehlt, bild_text, ocr_da, pdf_text,
    text_aus_datei, werkzeug_da)
from picknick.bons.zerlegen import (  # noqa: F401
    LADEN_TITEL, LIDL, REWE, UNBEKANNT, Bon, BonFormatFehler, Posten,
    artikelzeilen, cents, erkenne_datum, erkenne_laden, zerlege)
from picknick.bons.kaeufe import (  # noqa: F401
    BESTAETIGT, OFFEN, VERWORFEN, KaufFehler, anlegen, beleg, beleg_zu_datei,
    belege, bilanz, echte_preise, entscheiden, korrigieren, letzter_preis,
    posten, posten_zeile, zuordnung_setzen)
from picknick.bons.zuordnung import (  # noqa: F401
    Zuordner, ZuordnungFehler, ZuordnungNichtVerfuegbar, deuten)

__all__ = [
    "BESTAETIGT", "ERLAUBT", "FEHLER", "FERTIG", "LADEN_TITEL", "LAEUFT",
    "LIDL", "MAX_BYTES", "NACHFRAGE_S", "OCR_FEHLT_TEXT", "OFFEN", "REWE",
    "SIGNATUREN", "UNBEKANNT", "VERWORFEN",
    "Bon", "BonFehler", "BonFormatFehler", "KaufFehler", "Laeufe",
    "LeseFehler", "Posten", "Stand", "WerkzeugFehlt", "Zuordner",
    "ZuordnungFehler", "ZuordnungNichtVerfuegbar",
    "anlegen", "artikelzeilen", "beleg", "beleg_zu_datei", "belege", "bilanz",
    "bild_text", "cents", "deuten", "echte_preise", "entscheiden",
    "erkenne_datum", "erkenne_laden", "erkenne_typ", "groesse_text",
    "korrigieren", "letzter_preis", "liste", "loeschen", "ocr_da", "pdf_text",
    "pfad_im_verzeichnis", "posten", "posten_zeile", "sicherer_name",
    "sofort", "speichern", "text_aus_datei", "werkzeug_da", "zerlege",
    "zuordnung_setzen",
]
