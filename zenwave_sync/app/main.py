"""Einstiegspunkt: liest Add-on-Optionen, plant den taeglichen ZenWave-Sync und fuehrt ihn aus.

Struktur bewusst identisch zum Nachbar-Add-on "Vivendi Dienstplan Sync" (main.py dort), damit
beide Add-ons demselben, bereits bewaehrten Muster folgen.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import ha_sync
import zenwave
from notify import clear_error, notify_error

OPTIONS_PATH = "/data/options.json"

_LOGGER = logging.getLogger("zenwave_sync")


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
    _LOGGER.info("Starte ZenWave-Sync")
    data = zenwave.fetch_intervalldaten(
        login_url=options["zenwave_login_url"],
        username=options["zenwave_username"],
        password=options["zenwave_password"],
    )
    _LOGGER.info(
        "ZenWave-Daten gelesen: %s",
        {k: v for k, v in data.items() if k != "raw_card_text"},
    )
    ha_sync.publish_intervalldaten(data)
    ha_sync.publish_preisaufschlag(data)
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
        os.environ["ZENWAVE_SYNC_DEBUG"] = "1"
        _LOGGER.info("Debug-Screenshots aktiviert, werden unter /share/zenwave_sync/debug/ gespeichert")

    _LOGGER.info("ZenWave-Sync-Add-on gestartet, taeglicher Lauf um %s Uhr", options.get("run_time", "07:00"))

    def run_and_handle_errors() -> None:
        try:
            sync_once(options)
        except zenwave.ZenwaveLoginError as exc:
            _LOGGER.exception("ZenWave-Login fehlgeschlagen")
            notify_error("ZenWave-Sync: Login fehlgeschlagen", str(exc))
        except zenwave.ZenwaveScrapeError as exc:
            _LOGGER.exception("ZenWave-Daten konnten nicht gelesen werden")
            notify_error("ZenWave-Sync: Daten konnten nicht gelesen werden", str(exc))
        except Exception as exc:  # noqa: BLE001 - Loop soll nie sterben
            _LOGGER.exception("Unerwarteter Fehler im ZenWave-Sync-Lauf")
            notify_error("ZenWave-Sync: Unerwarteter Fehler", str(exc))

    # Einmal sofort beim (Neu-)Start laufen lassen, nicht bis zu 24h auf run_time warten -
    # wichtig gerade jetzt fuer den ersten echten Testlauf.
    run_and_handle_errors()

    while True:
        wait_s = seconds_until_next_run(options.get("run_time", "07:00"))
        _LOGGER.info("Naechster Sync-Lauf in %.0f Minuten", wait_s / 60)
        time.sleep(wait_s)
        run_and_handle_errors()


if __name__ == "__main__":
    main()
