#!/bin/bash
# Wartet, bis diese Maschine eine Adresse im Tailnet hat — höchstens `FRIST`
# Sekunden, und danach wird trotzdem gestartet.
#
# **Warum es das gibt.** Der Shop sucht seine Bindeadressen EINMAL, beim
# Start (`web.app.hosts_aus_umgebung`): die eigene Tailnet-Adresse plus
# loopback. Ist tailscaled noch nicht oben, findet er keine und lauscht nur
# auf 127.0.0.1 — der Prozess läuft, `systemctl status` ist grün, und das
# Telefon erreicht nichts. Ein Fehler, der wie Betrieb aussieht.
#
# Solange die User-Dienste erst mit der Anmeldung starten (`Linger=no`), tritt
# der Fall nicht auf. Mit `loginctl enable-linger` schon — und das ist der
# Befehl, den man braucht, damit der nächtliche Katalog-Lauf ohne offene
# Sitzung läuft. Dieses Skript macht die beiden Wünsche verträglich.
#
# **Es scheitert nie.** Ein `ExecStartPre`, das mit einem Fehler endet, hält
# den Dienst an. Ohne Tailscale soll der Shop auf loopback starten, nicht gar
# nicht: dann ist er vom Rechner selbst erreichbar, und das ist mehr als
# nichts.
FRIST=${TAILNET_FRIST:-60}
for _ in $(seq "$FRIST"); do
  # Dieselbe Frage wie im Shop: nicht die Interfaces absuchen, sondern die
  # Routing-Tabelle fragen. Das UDP-`connect` sendet nichts.
  if ip route get 100.100.100.100 2>/dev/null | grep -qE 'src 100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.'; then
    echo "Tailnet-Adresse da."
    exit 0
  fi
  sleep 1
done
echo "Nach ${FRIST}s keine Tailnet-Adresse — der Shop startet auf loopback."
exit 0
