"""Home-Assistant-Benachrichtigungen ueber die Supervisor-API.

Nutzt denselben Weg wie jeder andere HA-Add-on-Container: SUPERVISOR_TOKEN wird von HA
automatisch als Umgebungsvariable injiziert, http://supervisor/core/api/... ist das interne
Gateway zur Core-REST-API (kein eigener Netzwerkzugriff/Port noetig).
"""

import logging
import os

import requests

_LOGGER = logging.getLogger(__name__)

_SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
_BASE_URL = "http://supervisor/core/api"


def _create_notification(title: str, message: str, notification_id: str) -> None:
    if not _SUPERVISOR_TOKEN:
        _LOGGER.error("Kein SUPERVISOR_TOKEN vorhanden, kann keine HA-Benachrichtigung senden: %s - %s", title, message)
        return
    try:
        resp = requests.post(
            f"{_BASE_URL}/services/persistent_notification/create",
            headers={"Authorization": f"Bearer {_SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
            json={"title": title, "message": message, "notification_id": notification_id},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        _LOGGER.exception("Konnte persistent_notification nicht an Home Assistant senden")


def notify_error(title: str, message: str, notification_id: str = "dienstplan_sync_error") -> None:
    """Erzeugt/aktualisiert eine persistent_notification in Home Assistant."""
    _create_notification(title, message, notification_id)


def notify_info(title: str, message: str, notification_id: str) -> None:
    """Wie notify_error, aber semantisch fuer nicht-fehlerhafte Hinweise (z.B. einmalige
    ICS-URL-Mitteilung) - technisch identisch, eigener Name nur fuer Lesbarkeit im Aufrufcode."""
    _create_notification(title, message, notification_id)


def clear_error(notification_id: str = "dienstplan_sync_error") -> None:
    """Entfernt eine zuvor gesetzte Fehlerbenachrichtigung (z.B. nach erfolgreichem Lauf)."""
    if not _SUPERVISOR_TOKEN:
        return
    try:
        resp = requests.post(
            f"{_BASE_URL}/services/persistent_notification/dismiss",
            headers={"Authorization": f"Bearer {_SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
            json={"notification_id": notification_id},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        _LOGGER.exception("Konnte persistent_notification nicht zuruecksetzen")
