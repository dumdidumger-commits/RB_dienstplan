"""Schreibt die von ZenWave gelesenen Werte als Sensoren nach Home Assistant.

Nutzt die REST-API direkt ueber SUPERVISOR_TOKEN (gleicher Zugriffsweg wie notify.py) statt
z.B. MQTT - fuer drei simple Werte reicht das, keine zusaetzliche Abhaengigkeit noetig.
"""

import logging
import os

import requests

_LOGGER = logging.getLogger(__name__)
_SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
_BASE_URL = "http://supervisor/core/api"


def _set_state(entity_id: str, state, attributes: dict) -> None:
    if not _SUPERVISOR_TOKEN:
        _LOGGER.error("Kein SUPERVISOR_TOKEN vorhanden, kann %s nicht setzen", entity_id)
        return
    resp = requests.post(
        f"{_BASE_URL}/states/{entity_id}",
        headers={"Authorization": f"Bearer {_SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
        json={"state": state, "attributes": attributes},
        timeout=15,
    )
    resp.raise_for_status()


def publish_intervalldaten(data: dict) -> None:
    """Schreibt sensor.zenwave_real_verbrauch / _kosten / _durchschnittspreis.

    `data` ist das Rueckgabe-Dict von zenwave.fetch_intervalldaten(). `zeitraum`/`status` landen
    als Attribute, damit spaeter (siehe project_energieprognose_tag_offset_summary Memory) die
    Tag-Offset-Sensoren pruefen koennen, fuer welchen Tag der Wert gilt und ob er schon final ist,
    bevor sie ihn statt der Shelly/Poweropti-Schaetzung anzeigen.
    """
    label = data.get("zeitraum_label") or "unbekannt"
    common_attrs = {
        "zeitraum": label,
        "status": data.get("status"),
        "quelle": "ZenWave-Kundenportal (Intervalldaten)",
    }
    _set_state(
        "sensor.zenwave_real_verbrauch",
        data.get("verbrauch_kwh"),
        {**common_attrs, "unit_of_measurement": "kWh", "friendly_name": "ZenWave: realer Verbrauch"},
    )
    _set_state(
        "sensor.zenwave_real_kosten",
        data.get("kosten_eur"),
        {**common_attrs, "unit_of_measurement": "€", "friendly_name": "ZenWave: reale variable Kosten"},
    )
    _set_state(
        "sensor.zenwave_real_durchschnittspreis",
        data.get("durchschnittspreis_ct_kwh"),
        {**common_attrs, "unit_of_measurement": "ct/kWh", "friendly_name": "ZenWave: realer Durchschnittspreis"},
    )
    _LOGGER.info("ZenWave-Sensoren aktualisiert fuer Zeitraum '%s' (Status: %s)", label, data.get("status"))
