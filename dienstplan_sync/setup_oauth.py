#!/usr/bin/env python3
"""Einmaliges lokales Setup-Skript fuer den Google-Calendar-OAuth-Consent-Flow.

WICHTIG: Dieses Skript wird NICHT im Add-on-Container ausgefuehrt, sondern EINMALIG auf
deinem eigenen Computer (Laptop/PC) - und zwar aus genau diesem Grund: Der interaktive
OAuth-Consent-Flow fuer eine "Desktop-App" oeffnet einen Browser und wartet danach auf einen
Redirect auf http://localhost:<port>. Wuerde man das versuchen, DIREKT im Add-on-Container
(also auf dem HAOS-Rechner) laufen zu lassen, wuerde "localhost" dort aus Sicht deines
Browsers (der vermutlich auf einem ANDEREN Geraet laeuft, z.B. deinem Laptop oder Handy) ins
Leere zeigen - der Redirect kann den Container so nicht erreichen.

Deshalb: einmal lokal ausfuehren (wo Browser und Skript auf demselben Rechner laufen, das
Redirect-Problem existiert dann nicht), das Ergebnis (token.json) danach manuell in den
Home-Assistant-"share"-Ordner kopieren (z.B. per Samba-Add-on oder File-Editor-Add-on) nach:

    /share/dienstplan_sync/config/token.json

Der laufende Add-on-Container liest diese Datei nur noch und erneuert den Access-Token
danach selbststaendig ueber den darin enthaltenen Refresh-Token - dafuer ist kein Browser
mehr noetig.

Voraussetzungen auf DEINEM Rechner (nicht auf HAOS):
    pip install google-auth-oauthlib

Vorgehen:
    1. Google Cloud Console (siehe README.md, Abschnitt "Google OAuth Ersteinrichtung"):
       eigenes Projekt anlegen, Calendar API aktivieren, OAuth-Client vom Typ
       "Desktop-App" erstellen, JSON-Datei herunterladen.
    2. Die heruntergeladene Datei hier als "client_secret.json" neben dieses Skript legen.
    3. python3 setup_oauth.py
    4. Browser oeffnet sich, Consent bestaetigen.
    5. Die erzeugte "token.json" nach /share/dienstplan_sync/config/token.json auf dem
       HAOS-Rechner kopieren.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    # run_local_server funktioniert hier, weil Browser und Skript auf demselben Rechner
    # laufen (siehe Modul-Docstring oben) - port=0 laesst das Betriebssystem einen freien
    # Port waehlen.
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"\nFertig. {TOKEN_FILE} wurde erzeugt.")
    print(f"Bitte jetzt manuell nach /share/dienstplan_sync/config/{TOKEN_FILE} auf dem HAOS-Rechner kopieren.")


if __name__ == "__main__":
    main()
