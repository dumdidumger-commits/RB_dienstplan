"""Erzeugt eine iCalendar-Datei (.ics) aus den geparsten Schichten.

Zusaetzliche, von der Google-API-Synchronisation unabhaengige Absicherung (30.07.2026,
Nutzerwunsch): Google Kalender kann eine .ics-URL direkt abonnieren ("Weitere Kalender" ->
"Von URL"), dafuer ist kein OAuth noetig - falls der aktuelle Google-Cloud-Testzugang nach
7 Tagen ablaeuft (siehe README, noch nicht abschliessend geklaert), bleibt dieser Weg trotzdem
funktionsfaehig. Laeuft PARALLEL zur bestehenden Google-API-Synchronisation, ersetzt sie nicht
(Nutzerentscheidung 30.07.2026).

Die Datei wird unter /homeassistant/www/ abgelegt (zusaetzlicher homeassistant_config:rw-
Zugriff, siehe config.yaml) - das entspricht dem HA-eigenen "/local/"-Pfad, der bei diesem
Nutzer bereits unauthentifiziert extern ueber die Nabu-Casa-Cloud-URL erreichbar ist (fuer das
bestehende app.html-Dashboard, laut Nutzer "funktioniert tadellos"). Google kann beim Abrufen
keine Zugangsdaten mitschicken, daher zwingend ohne Login - abgesichert einzig durch einen
langen, nicht erratbaren Zufallsnamen (RFC bietet dafuer kein Auth-Feld).

WICHTIG: Der einmal erzeugte Zufallsname/Dateiname darf sich nach der ersten Google-Abo-
Einrichtung nie wieder aendern, sonst zeigt Googles Abo ins Leere - deshalb persistiert in
TOKEN_PATH und bei jedem Lauf wiederverwendet statt neu erzeugt.

Google aktualisiert abonnierte URLs nach eigenem Zeitplan (typischerweise alle 12-24h, nicht
erzwingbar) - da der eigene Sync ohnehin nur 1x taeglich laeuft, in der Praxis meist
unproblematisch.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone

from calendar_sync import _event_id, _to_event_body
from parser import Shift

_LOGGER = logging.getLogger(__name__)

TOKEN_PATH = "/share/dienstplan_sync/config/ics_token.txt"
WWW_DIR = "/homeassistant/www"


def _get_or_create_token() -> tuple[str, bool]:
    """Gibt (token, ist_neu_erzeugt) zurueck."""
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token, False
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(token)
    _LOGGER.info("Neuer ICS-Zufallsname erzeugt (einmalig, bleibt ab jetzt dauerhaft stabil)")
    return token, True


def _escape(text: str) -> str:
    """RFC 5545: Backslash, Komma, Semikolon und Newline muessen escaped werden."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 verlangt Zeilenumbrueche (mit fuehrendem Leerzeichen) nach spaetestens 75
    Oktetten - bei den kurzen Titeln/Beschreibungen hier meist nicht relevant, aber
    spezifikationskonform und schadet nicht. Schneidet an Zeichen- statt Byte-Grenzen, damit
    Mehrbyte-UTF8-Zeichen (Umlaute) nicht mittendrin zerrissen werden."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts = []
    rest = line
    while len(rest.encode("utf-8")) > 75:
        cut = 75
        while len(rest[:cut].encode("utf-8")) > 75:
            cut -= 1
        parts.append(rest[:cut])
        rest = " " + rest[cut:]
    parts.append(rest)
    return "\r\n".join(parts)


def _event_to_vevent(shift: Shift) -> list[str]:
    body = _to_event_body(shift)
    uid = f"{_event_id(shift)}@dienstplan-sync"
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now_utc}"]

    if "dateTime" in body["start"]:
        start_dt = datetime.fromisoformat(body["start"]["dateTime"])
        end_dt = datetime.fromisoformat(body["end"]["dateTime"])
        lines.append(f"DTSTART;TZID=Europe/Berlin:{start_dt.strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"DTEND;TZID=Europe/Berlin:{end_dt.strftime('%Y%m%dT%H%M%S')}")
    else:
        start_d = body["start"]["date"].replace("-", "")
        end_d = body["end"]["date"].replace("-", "")
        lines.append(f"DTSTART;VALUE=DATE:{start_d}")
        lines.append(f"DTEND;VALUE=DATE:{end_d}")

    lines.append(_fold(f"SUMMARY:{_escape(body['summary'])}"))
    if body.get("description"):
        lines.append(_fold(f"DESCRIPTION:{_escape(body['description'])}"))

    lines.append("END:VEVENT")
    return lines


def write_ics(shifts: list[Shift]) -> tuple[str, bool]:
    """Schreibt eine .ics-Datei mit allen uebergebenen Schichten unter /homeassistant/www/.

    Gibt (dateiname, ist_neu_erzeugt) zurueck - ist_neu_erzeugt ist True nur beim allerersten
    Lauf (neuer Zufallsname), damit main.py den Nutzer genau einmal per Benachrichtigung
    informieren kann, statt bei jedem taeglichen Lauf erneut.
    """
    token, is_new = _get_or_create_token()
    filename = f"dienstplan_{token}.ics"
    path = os.path.join(WWW_DIR, filename)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dienstplan_sync//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        # 12.08.2026 umbenannt (Roland-Wunsch): ICS ist inzwischen der verlaessliche Hauptweg,
        # nicht mehr nur "Backup" (siehe project_vivendi_dienstplan_addon Memory) - u.a. weil
        # eine dritte Person diesen Feed direkt in Alexa abonniert hat und dort bisher
        # "... Backup..." als Kalendername sah. Ehemals bewusst "Dienstplan (Backup, ICS-Abo)"
        # (siehe Git-Historie) um Namenskollision mit dem Google-API-Kalender "Dienstplan" zu
        # vermeiden - diese Sorge ist nachrangig, seit der Google-API-Weg selbst nachrangig ist.
        "X-WR-CALNAME:Rolands Dienstplan",
    ]
    for shift in shifts:
        lines.extend(_event_to_vevent(shift))
    lines.append("END:VCALENDAR")

    os.makedirs(WWW_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\r\n".join(lines) + "\r\n")

    _LOGGER.info("ICS-Datei geschrieben: %s (%d Termine)", path, len(shifts))
    return filename, is_new
