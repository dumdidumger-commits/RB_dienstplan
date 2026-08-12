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
from datetime import date, datetime, timedelta

import requests

import ha_sync
import zenwave
from notify import clear_error, notify_error

OPTIONS_PATH = "/data/options.json"
# 12.08.2026: Merkt sich das Datum des letzten erfolgreichen Sync-Laufs in /data (ueberlebt
# Add-on-Neustarts, anders als eine In-Memory-Variable). Verhindert, dass ein Add-on-Neustart
# (z.B. ausgeloest durch einen unabhaengigen HA-Core-Neustart) eine zusaetzliche, ungeplante
# Zweit-Abfrage am selben Tag ausloest - das fuehrte dazu, dass "gestrige" Intervalldaten bei
# mehreren Laeufen am selben Tag mit unterschiedlichen (von ZenWave zwischenzeitlich noch
# nachkorrigierten) Werten ueberschrieben wurden, siehe project_zenwave_sync_planning Memory.
LAST_SYNC_MARKER_PATH = "/data/last_sync_date.txt"

_LOGGER = logging.getLogger("zenwave_sync")


def load_options() -> dict:
    with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _already_synced_today() -> bool:
    try:
        with open(LAST_SYNC_MARKER_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() == datetime.now().date().isoformat()
    except FileNotFoundError:
        return False


def _mark_synced_today() -> None:
    with open(LAST_SYNC_MARKER_PATH, "w", encoding="utf-8") as f:
        f.write(datetime.now().date().isoformat())


def _clear_backfill_option() -> None:
    """Setzt die Add-on-Option backfill_dates ueber die Supervisor-API wieder auf "" zurueck,
    damit ein einmal angefragter Nachtrag nicht bei jedem folgenden Neustart wiederholt wird."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return
    try:
        resp = requests.post(
            "http://supervisor/addons/self/options",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"options": {"backfill_dates": ""}},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        _LOGGER.exception("Konnte backfill_dates-Option nicht zuruecksetzen")


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _parse_backfill_dates(raw: str) -> list[date]:
    result = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(date.fromisoformat(part))
        except ValueError:
            _LOGGER.warning("Ungueltiges Datum in backfill_dates ignoriert: %r", part)
    return result


def sync_once(options: dict) -> None:
    """Voller taeglicher Sync: Intervalldaten (Verbrauch/Kosten) + Strompreis-Karte. Bewusst
    NUR 1x taeglich (siehe LAST_SYNC_MARKER_PATH oben) - die Intervalldaten-Karte zeigt kein
    festes Kalendertag-Fenster, mehrfache Laeufe am selben Tag wuerden wieder das Rolling-
    Fenster-Problem verursachen (project_shelly_vs_poweropti_miscalibration bzw.
    project_zenwave_sync_planning Memory)."""
    _LOGGER.info("Starte ZenWave-Sync (voll)")
    requested_dates = _parse_backfill_dates(options.get("backfill_dates", ""))
    if requested_dates:
        _LOGGER.info("Zusaetzlich angefragte Nachtrags-Tage: %s", requested_dates)
    data = zenwave.fetch_intervalldaten(
        login_url=options["zenwave_login_url"],
        username=options["zenwave_username"],
        password=options["zenwave_password"],
        requested_dates=requested_dates,
    )
    _LOGGER.info(
        "ZenWave-Daten gelesen: %s",
        {k: v for k, v in data.items() if k != "raw_card_text"},
    )
    if requested_dates:
        ha_sync.publish_specific_days(data.get("specific_days", {}))
        _clear_backfill_option()
    ha_sync.publish_intervalldaten(data)
    ha_sync.publish_preisaufschlag(data)
    ha_sync.publish_preis_snapshot(data)
    ha_sync.publish_kalibrierungsvergleich(data)
    clear_error()
    _mark_synced_today()


def price_sync_once(options: dict) -> None:
    """Leichtgewichtiger Preis-Nur-Sync (12.08.2026 neu, Roland-Wunsch): laeuft mehrmals
    taeglich (siehe price_sync_interval_hours), liest NUR die Strompreis-Karte (ohne
    Verbrauch-Tab) und speichert einen echten Preis-Schnappschuss mit Zeitstempel. Aktualisiert
    dabei auch gleich den Aufschlag fuer die 24h-Prognosekurve, damit die sich zwischen den
    taeglichen vollen Laeufen selbst nachkorrigiert."""
    _LOGGER.info("Starte ZenWave-Sync (nur Preis)")
    data = zenwave.fetch_strompreis_only(
        login_url=options["zenwave_login_url"],
        username=options["zenwave_username"],
        password=options["zenwave_password"],
    )
    _LOGGER.info(
        "ZenWave-Preis-Snapshot gelesen: %s",
        {k: v for k, v in data.items() if k != "raw_strompreis_text"},
    )
    ha_sync.publish_preis_snapshot(data)
    ha_sync.publish_preisaufschlag(data)
    ha_sync.publish_kalibrierungsvergleich(data)
    clear_error(notification_id="zenwave_sync_price_error")


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

    price_interval_h = float(options.get("price_sync_interval_hours", 5) or 5)
    _LOGGER.info(
        "ZenWave-Sync-Add-on gestartet - voller Lauf taeglich um %s Uhr, Preis-Nur-Lauf alle %.1fh",
        options.get("run_time", "23:59"), price_interval_h,
    )

    def run_full_and_handle_errors() -> None:
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

    def run_price_and_handle_errors() -> None:
        try:
            price_sync_once(options)
        except zenwave.ZenwaveLoginError as exc:
            _LOGGER.exception("ZenWave-Login fehlgeschlagen (Preis-Sync)")
            notify_error("ZenWave-Sync: Login fehlgeschlagen (Preis)", str(exc), notification_id="zenwave_sync_price_error")
        except zenwave.ZenwaveScrapeError as exc:
            _LOGGER.exception("ZenWave-Preis konnte nicht gelesen werden")
            notify_error("ZenWave-Sync: Preis konnte nicht gelesen werden", str(exc), notification_id="zenwave_sync_price_error")
        except Exception as exc:  # noqa: BLE001 - Loop soll nie sterben
            _LOGGER.exception("Unerwarteter Fehler im ZenWave-Preis-Sync-Lauf")
            notify_error("ZenWave-Sync: Unerwarteter Fehler (Preis)", str(exc), notification_id="zenwave_sync_price_error")

    # Einmal sofort beim (Neu-)Start laufen lassen, nicht bis zu 24h auf run_time warten -
    # ABER nur, wenn heute noch kein erfolgreicher Lauf stattgefunden hat (12.08.2026 Fix,
    # siehe LAST_SYNC_MARKER_PATH oben) ODER ein Nachtrag (backfill_dates) angefragt wurde.
    # Verhindert unnoetige Zusatz-Abfragen bei Add-on-Neustarts, die nichts mit dem
    # eigentlichen Sync zu tun haben.
    if _already_synced_today() and not options.get("backfill_dates"):
        _LOGGER.info("Heute bereits erfolgreich synchronisiert, ueberspringe Sofort-Lauf beim Start")
    else:
        run_full_and_handle_errors()

    # Preis-Nur-Sync IMMER sofort beim Start auch einmal ausfuehren (unabhaengig vom
    # Tages-Merker oben) - das ist die leichtgewichtige, mehrmals-taegliche Preis-Abfrage,
    # fuer die es bewusst keine "schon heute gelaufen"-Sperre gibt.
    run_price_and_handle_errors()

    price_interval_s = price_interval_h * 3600
    verbleibend_voll = seconds_until_next_run(options.get("run_time", "23:59"))
    verbleibend_preis = price_interval_s

    while True:
        wait_s = min(verbleibend_voll, verbleibend_preis)
        _LOGGER.info(
            "Naechster ZenWave-Lauf in %.0f Minuten (voll in %.0f Min, Preis in %.0f Min)",
            wait_s / 60, verbleibend_voll / 60, verbleibend_preis / 60,
        )
        time.sleep(wait_s)
        verbleibend_voll -= wait_s
        verbleibend_preis -= wait_s
        if verbleibend_voll <= 1:
            run_full_and_handle_errors()
            verbleibend_voll = seconds_until_next_run(options.get("run_time", "23:59"))
        if verbleibend_preis <= 1:
            run_price_and_handle_errors()
            verbleibend_preis = price_interval_s


if __name__ == "__main__":
    main()
