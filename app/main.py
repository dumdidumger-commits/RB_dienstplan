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
from notify import clear_error, notify_error

OPTIONS_PATH = "/data/options.json"
DIENSTPLAN_CACHE_PATH = "/data/last_dienstplan.xlsx"
DIENSTPLAN_CACHE_PATH_NEXT_MONTH = "/data/last_dienstplan_folgemonat.xlsx"
TOKEN_PATH = "/share/dienstplan_sync/config/token.json"
KUERZEL_MAPPING_PATH = "/share/dienstplan_sync/config/kuerzel_mapping.yaml"

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
    clear_error()


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
