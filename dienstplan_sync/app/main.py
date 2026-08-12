"""Einstiegspunkt: liest Add-on-Optionen, plant den taeglichen Sync-Lauf und fuehrt ihn aus."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta

import vivendi
import parser as dienstplan_parser
import calendar_sync
import ics_export
from notify import clear_error, notify_error, notify_info, notify_push

OPTIONS_PATH = "/data/options.json"
DIENSTPLAN_CACHE_PATH = "/data/last_dienstplan.xlsx"
DIENSTPLAN_CACHE_PATH_NEXT_MONTH = "/data/last_dienstplan_folgemonat.xlsx"
TOKEN_PATH = "/share/dienstplan_sync/config/token.json"
KUERZEL_MAPPING_PATH = "/share/dienstplan_sync/config/kuerzel_mapping.yaml"
# 12.08.2026 neu (Roland-Wunsch): merkt sich den Schicht-Stand des letzten Laufs, um
# Aenderungen (gestrichene/neue Dienste) per Push zu melden - siehe _notify_shift_changes().
SHIFT_SNAPSHOT_PATH = "/data/last_shifts_snapshot.json"

# Vom Nutzer bestaetigt (30.07.2026): Vivendi veroeffentlicht den Dienstplan fuer den
# Folgemonat jeweils am 15. oder 16. des Vormonats (mal der eine, mal der andere Tag). Ab dem
# 17. ist der Folgemonat also sicher verfuegbar und wird fuer die eigene Planung mit
# importiert.
NEXT_MONTH_AVAILABLE_FROM_DAY = 17

_LOGGER = logging.getLogger("dienstplan_sync")


def load_options() -> dict:
    with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _load_shift_snapshot() -> dict:
    try:
        with open(SHIFT_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_shift_snapshot(snapshot: dict) -> None:
    with open(SHIFT_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)


_MONATSNAMEN = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def _notify_shift_changes(shifts: list) -> None:
    """Vergleicht die frisch geparsten Schichten mit dem Stand des letzten Laufs und meldet
    das Ergebnis per Push (12.08.2026, Rolands Wunsch). Bewusst unabhaengig von der
    Google-API (rein lokaler Vergleich anhand von /data/last_shifts_snapshot.json) - passt
    zum neuen Fokus auf ICS als verlaesslichen Hauptweg (siehe
    project_vivendi_dienstplan_addon Memory).

    12.08.2026 erweitert (Rolands Wunsch, "fuer die erste Zeit zum Testen"): meldet sich
    jetzt bei JEDEM Lauf, nicht mehr nur bei echten Aenderungen - inkl. Bestaetigung "keine
    Veraenderung" und eigenem Hinweis, wenn der Folgemonat neu dazukommt. Kann spaeter wieder
    auf "nur bei echten Aenderungen" zurueckgestellt werden, sobald sich das eingespielt hat.

    Der Deckungszeitraum (bis zu welchem Datum ueberhaupt Schichten geladen wurden) waechst
    am 17. jedes Monats, wenn der Folgemonat dazukommt (siehe NEXT_MONTH_AVAILABLE_FROM_DAY) -
    ein voller "26 neue Dienste"-Alarm an genau diesem Tag waere Rauschen, kein echter
    Dienstplan-Wechsel. Deshalb: Neu/Gestrichen nur innerhalb der ÜBERLAPPUNG von altem und
    neuem Deckungszeitraum melden (Tage, die in BEIDEN Laeufen bereits sichtbar waren)."""
    new_by_id = {}
    for s in shifts:
        eid = calendar_sync._event_id(s)
        eintrag = {"datum": s.datum.isoformat(), "code": s.ist_code}
        if s.start and s.ende:
            eintrag["zeit"] = f"{s.start}–{s.ende}"
        new_by_id[eid] = eintrag
    new_coverage_end = max((s.datum for s in shifts), default=None)

    alt = _load_shift_snapshot()
    old_by_id = alt.get("shifts", {})
    old_coverage_end = alt.get("coverage_end")

    absatz = []

    if not old_by_id or not old_coverage_end:
        absatz.append("Erster Lauf, ich hab mir den aktuellen Stand als Vergleichsbasis gemerkt.")
    else:
        alter_monat = date.fromisoformat(old_coverage_end).replace(day=1)
        neuer_monat = new_coverage_end.replace(day=1) if new_coverage_end else alter_monat
        if neuer_monat > alter_monat:
            absatz.append(f"Der {_MONATSNAMEN[neuer_monat.month - 1]} ist jetzt auch verfügbar und wurde mit importiert.")

        overlap_end = min(old_coverage_end, new_coverage_end.isoformat()) if new_coverage_end else old_coverage_end
        old_overlap = {k: v for k, v in old_by_id.items() if v["datum"] <= overlap_end}
        new_overlap = {k: v for k, v in new_by_id.items() if v["datum"] <= overlap_end}
        hinzugekommen = [v for k, v in new_overlap.items() if k not in old_overlap]
        gestrichen = [v for k, v in old_overlap.items() if k not in new_overlap]

        if hinzugekommen or gestrichen:
            zeilen = []
            for v in gestrichen:
                d = date.fromisoformat(v["datum"]).strftime("%d.%m.")
                zeilen.append((v["datum"], f"{d}: {v['code']} gestrichen"))
            for v in hinzugekommen:
                d = date.fromisoformat(v["datum"]).strftime("%d.%m.")
                zeit = f" {v['zeit']}" if "zeit" in v else ""
                zeilen.append((v["datum"], f"{d}: {v['code']}{zeit} neu zugekommen"))
            zeilen.sort(key=lambda t: t[0])
            absatz.append("\n".join(text for _, text in zeilen))
        else:
            absatz.append("Sonst keine Veränderung zum letzten Abgleich.")

    message = f"{len(shifts)} Dienste abgeholt.\n\n" + "\n\n".join(absatz)
    _LOGGER.info("Dienstplan-Sync-Meldung: %s", message.replace("\n", " | "))
    notify_push(
        "Dienstplan abgeholt",
        message,
        notification_id="dienstplan_sync_aenderung",
    )

    _save_shift_snapshot({
        "coverage_end": new_coverage_end.isoformat() if new_coverage_end else None,
        "shifts": new_by_id,
    })


def sync_once(options: dict) -> None:
    _LOGGER.info("Starte Dienstplan-Sync")

    target_paths = [DIENSTPLAN_CACHE_PATH]
    if date.today().day >= NEXT_MONTH_AVAILABLE_FROM_DAY:
        target_paths.append(DIENSTPLAN_CACHE_PATH_NEXT_MONTH)
        _LOGGER.info(
            "Tag %d >= %d - Folgemonat ist bei Vivendi bereits verfuegbar, wird mit importiert",
            date.today().day, NEXT_MONTH_AVAILABLE_FROM_DAY,
        )

    xlsx_paths = vivendi.download_dienstplan(
        login_url=options["vivendi_login_url"],
        username=options["vivendi_username"],
        password=options["vivendi_password"],
        target_paths=target_paths,
    )
    _LOGGER.info("Dienstplan heruntergeladen: %s", ", ".join(xlsx_paths))

    shifts = []
    for xlsx_path in xlsx_paths:
        shifts.extend(dienstplan_parser.parse_dienstplan(xlsx_path, KUERZEL_MAPPING_PATH))
    _LOGGER.info("%d Schicht(en) aus %d Datei(en) geparst", len(shifts), len(xlsx_paths))

    # 12.08.2026 Fix: ICS-Backup bewusst VOR dem Google-API-Aufruf, in eigenem try/except -
    # vorher stand das NACH dem Google-Sync, wodurch es bei einem OAuth-Fehler (genau der
    # Fall, gegen den es urspruenglich als Absicherung gedacht war, siehe 30.07.2026-Kommentar
    # unten) NIE ausgefuehrt wurde und somit gar kein echtes unabhaengiges Backup war (siehe
    # project_vivendi_dienstplan_addon Memory). Ein Fehler hier darf den Haupt-Sync unten
    # trotzdem nie verhindern.
    try:
        filename, is_new = ics_export.write_ics(shifts)
        base_url = options.get("nabu_casa_base_url", "").rstrip("/")
        ics_url = f"{base_url}/local/{filename}"
        _LOGGER.info("ICS-Kalender-URL: %s", ics_url)
        if is_new:
            notify_info(
                "Dienstplan-Sync: ICS-Kalender-URL bereit",
                f"Einmalig in Google Kalender eintragen unter 'Weitere Kalender' -> "
                f"'Von URL': {ics_url}. WICHTIG: Diesen Kalender (heisst 'Dienstplan "
                f"(Backup, ICS-Abo)') danach in der Kalenderliste ausgeblendet lassen, sonst "
                f"siehst du jede Schicht doppelt (er laeuft parallel zum normalen "
                f"'Dienstplan'-Kalender). Nur bei Bedarf einblenden, z.B. falls der normale "
                f"Kalender mal nicht aktualisiert wird.",
                notification_id="dienstplan_sync_ics_url",
            )
    except Exception:  # noqa: BLE001 - darf den nachfolgenden Haupt-Sync nie verhindern
        _LOGGER.exception("ICS-Datei konnte nicht geschrieben werden (Haupt-Sync laeuft trotzdem weiter)")

    # Ebenfalls unabhaengig vom Google-API-Teil unten (rein lokaler Vergleich) - eigener
    # try/except, ein Fehler hier soll weder ICS noch Google-Sync verhindern.
    try:
        _notify_shift_changes(shifts)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Dienstplan-Aenderungserkennung fehlgeschlagen")

    # Kernsync (Download/Parsen/ICS) ist an dieser Stelle bereits durch - loescht eine evtl.
    # noch offene generische Fehlerbenachrichtigung eines fruehereren Laufs. Der Google-API-
    # Teil unten hat seine EIGENE Fehlerbenachrichtigung (dienstplan_sync_google_api_error).
    clear_error()

    # 12.08.2026 (Rolands Wunsch): Google-Kalender-API ist nur noch "nice to have", nicht
    # mehr der verlaessliche Hauptweg (ICS oben) - Grund: der Google-Cloud-Testzugang gibt
    # nur 7 Tage gueltige Refresh-Tokens aus, laeuft also periodisch ab (siehe
    # project_vivendi_dienstplan_addon Memory). Eigener try/except mit eigener, ruhigerer
    # Fehlermeldung statt des lauten generischen Fehlers - ICS oben lief ja bereits.
    try:
        service = calendar_sync.build_service(TOKEN_PATH)
        calendar_id = calendar_sync.get_or_create_calendar_id(service)

        months_ahead = int(options.get("sync_months_ahead", 2))
        window_start = date.today().replace(day=1)
        # naiver, aber robuster Monats-Vorschub ohne zusaetzliche Abhaengigkeit (z.B. dateutil)
        m = window_start.month - 1 + months_ahead
        window_end = window_start.replace(year=window_start.year + m // 12, month=m % 12 + 1)

        result = calendar_sync.sync_shifts(service, calendar_id, shifts, window_start, window_end)
        _LOGGER.info(
            "Sync fertig: %d neu, %d aktualisiert, %d geloescht, %d unveraendert",
            result.inserted, result.updated, result.deleted, result.unchanged,
        )
        clear_error(notification_id="dienstplan_sync_google_api_error")
    except calendar_sync.MissingTokenError as exc:
        _LOGGER.exception("Google-Token fehlt")
        notify_error(
            "Dienstplan-Sync: Google-Anmeldung fehlt",
            str(exc),
            notification_id="dienstplan_sync_google_api_error",
        )
    except Exception as exc:  # noqa: BLE001 - ICS oben ist der eigentliche Hauptweg
        _LOGGER.exception("Google-Kalender-API-Sync fehlgeschlagen (ICS oben lief unabhaengig davon)")
        notify_error(
            "Dienstplan-Sync: Google-Kalender-Kopie veraltet",
            f"Die zusaetzliche Google-Kalender-API-Synchronisation ist fehlgeschlagen: {exc}. "
            "Der Hauptweg (ICS-Kalender-Abo) lief unabhaengig davon normal weiter. Bei "
            "'invalid_grant'/Token-Fehlern: OAuth-Testzugang ist abgelaufen (7-Tage-Limit), "
            "siehe setup_oauth.py-Anleitung fuer die einmalige lokale Neuautorisierung.",
            notification_id="dienstplan_sync_google_api_error",
        )


def seconds_until_next_run(run_time: str) -> float:
    hh, mm = (int(x) for x in run_time.split(":"))
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def main() -> None:
    options = load_options()
    setup_logging(options.get("log_level", "info"))
    if options.get("debug_screenshots"):
        os.environ["DIENSTPLAN_SYNC_DEBUG"] = "1"
        _LOGGER.info("Debug-Screenshots aktiviert, werden unter /share/dienstplan_sync/debug/ gespeichert")

    _LOGGER.info("Dienstplan-Sync-Add-on gestartet, taeglicher Lauf um %s Uhr", options.get("run_time", "06:00"))

    def run_and_handle_errors() -> None:
        try:
            sync_once(options)
        except vivendi.VivendiLoginError as exc:
            _LOGGER.exception("Vivendi-Login fehlgeschlagen")
            notify_error("Dienstplan-Sync: Login fehlgeschlagen", str(exc))
        except vivendi.VivendiExportError as exc:
            _LOGGER.exception("Vivendi-Export fehlgeschlagen")
            notify_error("Dienstplan-Sync: Export fehlgeschlagen", str(exc))
        except calendar_sync.MissingTokenError as exc:
            _LOGGER.exception("Google-Token fehlt")
            notify_error("Dienstplan-Sync: Google-Anmeldung fehlt", str(exc))
        except Exception as exc:  # noqa: BLE001 - bewusst breit, damit der Loop nie stirbt
            _LOGGER.exception("Unerwarteter Fehler im Sync-Lauf")
            notify_error("Dienstplan-Sync: Unerwarteter Fehler", str(exc))

    # Einmal sofort beim (Neu-)Start laufen lassen, statt bis zu 24h auf die naechste
    # run_time zu warten - hilfreich sowohl fuer den ersten Testlauf als auch im normalen
    # Betrieb (z.B. nach einem Neustart des Add-ons gibt es sofort einen aktuellen Stand).
    run_and_handle_errors()

    while True:
        wait_s = seconds_until_next_run(options.get("run_time", "06:00"))
        _LOGGER.info("Naechster Sync-Lauf in %.0f Minuten", wait_s / 60)
        time.sleep(wait_s)
        run_and_handle_errors()


if __name__ == "__main__":
    main()
