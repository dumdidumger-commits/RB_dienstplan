"""Eigenstaendiger Test: prueft NUR die Google-Calendar-Anbindung (Token laden, Kalender
"Dienstplan" suchen/anlegen) - unabhaengig vom (noch nicht live verifizierten) Vivendi-Teil.

Nuetzlich, um das Google-OAuth-Setup fuer sich zu verifizieren, bevor/waehrend der
Vivendi-Export-Teil noch getestet wird. Im Add-on-Container ausfuehren, z.B. ueber die
Home-Assistant-Weboberflaeche des Add-ons (falls vorhanden) oder per SSH in den laufenden
Container:

    python3 test_calendar.py

Voraussetzung: /share/dienstplan_sync/config/token.json muss bereits existieren (siehe
setup_oauth.py / README, Abschnitt "Google-OAuth-Ersteinrichtung").
"""

from __future__ import annotations

import logging
import sys

import calendar_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
_LOGGER = logging.getLogger("test_calendar")

TOKEN_PATH = "/share/dienstplan_sync/config/token.json"


def main() -> int:
    try:
        service = calendar_sync.build_service(TOKEN_PATH)
    except calendar_sync.MissingTokenError as exc:
        _LOGGER.error("%s", exc)
        return 1

    _LOGGER.info("Google-Token erfolgreich geladen/erneuert.")

    calendar_id = calendar_sync.get_or_create_calendar_id(service)
    _LOGGER.info("Kalender 'Dienstplan' vorhanden, ID: %s", calendar_id)
    _LOGGER.info("Erfolg - der Kalender sollte jetzt in deinem Google-Kalender-Konto sichtbar sein.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
