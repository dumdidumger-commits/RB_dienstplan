"""Google-Calendar-Anbindung: Credentials laden, Kalender finden/anlegen, Schichten
synchronisieren (Insert/Update/Delete, keine Duplikate).

Voraussetzung: token.json existiert bereits unter TOKEN_PATH (siehe setup_oauth.py im
Repo-Root - das lokale, einmalige Setup-Skript). Dieses Modul selbst startet NIE einen
interaktiven Browser-Flow, sondern laedt/erneuert ausschliesslich den vorhandenen Token.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from parser import Shift

_LOGGER = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_NAME = "Dienstplan"


class MissingTokenError(Exception):
    """token.json fehlt - einmaliges Setup (setup_oauth.py) wurde noch nicht durchgefuehrt."""


def load_credentials(token_path: str) -> Credentials:
    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    except FileNotFoundError as exc:
        raise MissingTokenError(
            f"{token_path} nicht gefunden. Bitte einmalig setup_oauth.py lokal ausfuehren "
            "und die erzeugte token.json dorthin kopieren (siehe README)."
        ) from exc

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


def get_or_create_calendar_id(service, calendar_name: str = CALENDAR_NAME) -> str:
    page_token = None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for entry in result.get("items", []):
            if entry.get("summary") == calendar_name:
                return entry["id"]
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    _LOGGER.info("Kalender %r nicht gefunden, lege ihn neu an", calendar_name)
    created = service.calendars().insert(body={"summary": calendar_name}).execute()
    return created["id"]


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
        start_dt = datetime.combine(shift.datum, datetime.strptime(shift.start, "%H:%M").time())
        end_dt = datetime.combine(shift.datum, datetime.strptime(shift.ende, "%H:%M").time())
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


@dataclass
class SyncResult:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0


def sync_shifts(service, calendar_id: str, shifts: list[Shift], sync_window_start: date, sync_window_end: date) -> SyncResult:
    """Gleicht die uebergebenen Schichten mit dem Kalender ab. Alle Events im Kalender
    innerhalb [sync_window_start, sync_window_end), deren Event-ID nicht mehr im aktuellen
    Datensatz vorkommt, werden geloescht (deckt verschobene/entfallene Schichten ab).
    """
    result = SyncResult()

    desired = {}
    for shift in shifts:
        if not (sync_window_start <= shift.datum < sync_window_end):
            continue
        desired[_event_id(shift)] = shift

    existing_ids: set[str] = set()
    page_token = None
    time_min = datetime.combine(sync_window_start, datetime.min.time()).isoformat() + "Z"
    time_max = datetime.combine(sync_window_end, datetime.min.time()).isoformat() + "Z"
    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            pageToken=page_token,
            singleEvents=True,
            maxResults=2500,
        ).execute()
        for event in resp.get("items", []):
            event_id = event.get("id", "")
            if event_id.startswith("dp"):  # nur von diesem Add-on verwaltete Events anfassen
                existing_ids.add(event_id)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    for event_id, shift in desired.items():
        body = _to_event_body(shift)
        if event_id in existing_ids:
            current = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            if (current.get("summary") == body["summary"]
                    and current.get("description", "") == body["description"]
                    and current.get("start") == body["start"]
                    and current.get("end") == body["end"]):
                result.unchanged += 1
                continue
            service.events().update(calendarId=calendar_id, eventId=event_id, body=body).execute()
            result.updated += 1
        else:
            body["id"] = event_id
            service.events().insert(calendarId=calendar_id, body=body).execute()
            result.inserted += 1

    for event_id in existing_ids - desired.keys():
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        result.deleted += 1

    return result


def build_service(token_path: str):
    creds = load_credentials(token_path)
    return build("calendar", "v3", credentials=creds)
