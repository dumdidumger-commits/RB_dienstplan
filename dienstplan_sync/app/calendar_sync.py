"""Event-ID/Event-Body-Hilfsfunktionen fuer Vivendi-Schichten.

13.08.2026: Die frueher hier lebende Google-Calendar-API-Anbindung (OAuth-Credentials,
Kalender finden/anlegen, Insert/Update/Delete-Sync) wurde komplett entfernt (Roland-Wunsch:
"das Ding brauchen wir nicht mehr, kannst Du es auch entfernen" - der Google-Cloud-Testzugang
gab nur 7 Tage gueltige Refresh-Tokens aus und lief staendig ab, siehe
project_vivendi_dienstplan_addon Memory). ICS-Kalenderabo (ics_export.py) ist seitdem der
einzige Weg, wie der Dienstplan in Google Calendar/Alexa landet.

_event_id() und _to_event_body() bleiben hier, weil sie nicht Google-spezifisch sind -
_event_id() liefert nur eine stabile ID zur Aenderungserkennung (main.py,
_notify_shift_changes) und ics_export.py nutzt beide fuer den ICS-Export.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from parser import Shift

# Alle Schichtzeiten aus dem Vivendi-Export sind lokale deutsche Zeit. Ohne explizite
# Zeitzone wuerde ein ICS-Consumer naive dateTime-Strings entweder ablehnen oder (schlimmer)
# stillschweigend als UTC interpretieren - das haette jede Schicht um 1-2 Stunden verschoben.
LOCAL_TZ = ZoneInfo("Europe/Berlin")


def _event_id(shift: Shift) -> str:
    """Deterministische, Google-konforme Event-ID ([a-v0-9]) aus Datum+Startzeit+Kuerzel.

    Ein Hex-Digest (0-9a-f) ist bereits vollstaendig [a-v0-9]-konform, keine weitere
    Zeichen-Ersetzung noetig.
    """
    raw = f"{shift.datum.isoformat()}|{shift.start or ''}|{shift.ist_code}"
    return "dp" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _to_event_body(shift: Shift) -> dict:
    title = shift.typ_label
    if shift.start and shift.ende:
        title = f"{shift.typ_label} {shift.start}–{shift.ende}"
    if shift.bereich:
        title += f" ({shift.bereich})"

    description = f"Pause: {shift.pause_min} Minuten" if shift.pause_min is not None else ""

    if shift.start and shift.ende:
        start_dt = datetime.combine(shift.datum, datetime.strptime(shift.start, "%H:%M").time(), tzinfo=LOCAL_TZ)
        end_dt = datetime.combine(shift.datum, datetime.strptime(shift.ende, "%H:%M").time(), tzinfo=LOCAL_TZ)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)  # Schicht ueber Mitternacht
        return {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }

    # Kein Zeitfenster in der Aufgabe-Spalte gefunden (z.B. reine Bewohnerbesprechung ohne
    # Zeitangabe) -> ganztaegiger Eintrag als Fallback, damit die Schicht trotzdem sichtbar ist.
    return {
        "summary": title,
        "description": description,
        "start": {"date": shift.datum.isoformat()},
        "end": {"date": (shift.datum + timedelta(days=1)).isoformat()},
    }

