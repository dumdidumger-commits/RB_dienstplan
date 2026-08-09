"""Home-Assistant-Benachrichtigungen ueber die Supervisor-API.

Identisch zum gleichnamigen Modul im Nachbar-Add-on "Vivendi Dienstplan Sync" (bewusst
dupliziert statt geteilt, da beide Add-ons unabhaengige Container/Images sind).
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


def notify_error(title: str, message: str, notification_id: str = "zenwave_sync_error") -> None:
    """Erzeugt/aktualisiert eine persistent_notification in Home Assistant."""
    _create_notification(title, message, notification_id)


def notify_info(title: str, message: str, notification_id: str) -> None:
    """Wie notify_error, aber semantisch fuer nicht-fehlerhafte Hinweise."""
    _create_notification(title, message, notification_id)


def clear_error(notification_id: str = "zenwave_sync_error") -> None:
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
