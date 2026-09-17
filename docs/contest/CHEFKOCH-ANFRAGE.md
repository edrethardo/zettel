Betreff: Zwei API-Abrufe je Gericht — Bitte um Zustimmung

An: info@chefkoch.de

Sehr geehrte Damen und Herren,

Ziffer 5.1 Ihrer Nutzungsbedingungen untersagt das automatische Auslesen der
Inhalte von chefkoch.de. Mein Projekt tut das in kleinem Umfang — deshalb
frage ich, statt es weiter stillschweigend zu tun.

„Zettel" ist eine private Einkaufsliste für einen Haushalt: Open Source unter
MIT-Lizenz, nicht kommerziell, ohne Werbung, ohne fremde Nutzer, Quelltext
unter github.com/edrethardo/zettel. Nennt jemand im Chat ein Gericht, holt
das Programm dazu ein Rezept und schlägt die Zutaten als Einkaufsliste vor.

Abgerufen werden genau zwei Adressen je Gericht:

    GET api.chefkoch.de/v2/recipes?query=<gericht>&limit=12
    GET api.chefkoch.de/v2/recipes/<id>

Dazwischen 1,5 Sekunden Pause; der User-Agent nennt das Projekt
(„zettel/1.0 (private household shopping list; 2 req per dish, cached)").
Das Ergebnis wird 90 Tage zwischengespeichert — dasselbe Gericht kostet Sie
danach keine weitere Anfrage. Gemessen am 28.08.2026 dauerten beide Anfragen
zusammen 90 bis 147 ms. Kein Browser, kein Zugangsschlüssel, keine Bilder,
kein flächiger Crawl.

Was nicht passiert: keine Kopie Ihres Bestands, keine Weitergabe der
Rezeptdaten (die Testdaten im öffentlichen Repository sind erfunden), keine
Werbung, keine Monetarisierung, keine eigene Rezeptsuche für Dritte.
Umgekehrt steht Chefkoch als Quelle an jedem geholten Rezept, und jede
Rezeptkarte verlinkt die Rezeptadresse (siteUrl) auf Ihre Seite; wünschen
Sie eine bestimmte Form der Nennung, nehme ich diese.

Meine Bitte, zwei Punkte:

1. Stimmen Sie diesem Abrufmuster zu — zwei Anfragen je Gericht, mit Pause
   und Zwischenspeicher, in dem beschriebenen Umfang?
2. Stimmen Sie zu, dass ich „Chefkoch" als Quelle namentlich nenne und das
   Rezept verlinke?

Ein Logo brauche ich nicht. Und ein Nein ist mir ebenso recht wie ein Ja:
dann stelle ich auf eine Quelle mit freier Lizenz um, ohne Nachfrage — eine
Absage macht mir nichts kaputt. Ich frage jetzt, weil das Projekt rund um
die GTC Berlin (20.–22.10.2026) öffentlich gezeigt wird; eine Antwort bis
dahin wäre schön, schon damit ich rechtzeitig umstellen kann, falls sie
negativ ausfällt. Vielen Dank für Ihre Zeit.

Mit freundlichen Grüßen
Ed Rethardo

---

## Worauf sich das stützt

**Empfänger**, dem Impressum entnommen am 17.09.2026
(`https://www.chefkoch.de/impressum`): Chefkoch GmbH, Rheinwerk 3,
Joseph-Schumpeter-Allee 33, 53227 Bonn, `info@chefkoch.de`; Amtsgericht
Bonn HRB 18761.

**Die drei Ziffern der Nutzungsbedingungen** (`https://www.chefkoch.de/agb/`,
eingesehen am 17.09.2026), im Wortlaut:

> **5.1** … „Das automatische Auslesen der auf unserer Seite befindlichen
> Daten sowie der Aufbau eigener Suchsysteme, Dienste und Verzeichnisse
> unter Zuhilfenahme der auf chefkoch.de abrufbaren Inhalte …"

> **6.9** „Du erkennst an, dass sämtliche Marken-, Kennzeichen- und
> sonstigen Schutzrechte der Chefkoch-Plattform – abseits deiner
> Nutzer-Inhalte – ausschließlich uns zustehen und ohne unsere vorherige
> schriftliche Zustimmung nicht genutzt werden dürfen."

> **6.10** „Die Nutzung sämtlicher Inhalte dieses Angebots (insbesondere
> Texte, Bilder, Grafiken, Videos, Audioinhalte und sonstiger begleitender
> Daten) für kommerzielles Text und Data Mining im Sinne des § 44b UrhG ist
> nicht statthaft."

5.1 und 6.9 sind die beiden Punkte, die eine schriftliche Zustimmung
gegenstandslos machen würde. 6.10 steht nicht in der Bitte, weil das Projekt
nicht kommerziell ist und deshalb nicht darunter fällt — genannt wird es
hier, damit die Prüfung vollständig ist.

**Die Zahlen im Brief** stehen so in `zettel/gerichte/chefkoch.py`:
`LIMIT = 12`, `PAUSE_S = 1.5`, `USER_AGENT`, die vier gemessenen Abrufe vom
28.08.2026 im Kopf von `TIMEOUT_SYNC_S` (90, 109, 114 und 147 ms) und
`ALTER_OK_S = 90 * 24 * 3600` in `zettel/gerichte/speicher.py`.

**Mit robots.txt wird nicht argumentiert**, und das mit Absicht:
`api.chefkoch.de` ist ein anderer Host als `www.chefkoch.de`, und die
robots.txt von `www` sperrt inzwischen rund sechzig KI-Crawler vollständig.
Eine falsch zitierte robots.txt würde das Gespräch beenden, bevor es anfängt.

**Stand: Entwurf, noch nicht abgeschickt.** Abgeschickt wird er von Hand.
