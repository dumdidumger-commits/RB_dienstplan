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

    # "Strompreis"-Karte (Startseite, 09.08.2026 ergaenzt) - eigener Attribut-Satz ohne
    # zeitraum/status, da diese Werte sich auf "jetzt" beziehen statt auf einen gewaehlten Tag.
    preis_attrs = {"quelle": "ZenWave-Kundenportal (Strompreis-Karte)"}
    _set_state(
        "sensor.zenwave_preis_aktuell",
        data.get("aktueller_preis_ct_kwh"),
        {**preis_attrs, "unit_of_measurement": "ct/kWh", "friendly_name": "ZenWave: aktueller Strompreis"},
    )
    _set_state(
        "sensor.zenwave_preis_durchschnitt",
        data.get("strompreis_durchschnitt_ct_kwh"),
        {**preis_attrs, "unit_of_measurement": "ct/kWh", "friendly_name": "ZenWave: Durchschnittspreis (Strompreis-Karte)"},
    )
    if data.get("boersenpreis_beschaffung_ct_kwh") is not None:
        _set_state(
            "sensor.zenwave_preis_boersenpreis_beschaffung",
            data.get("boersenpreis_beschaffung_ct_kwh"),
            {**preis_attrs, "unit_of_measurement": "ct/kWh", "friendly_name": "ZenWave: Preiskomponente Börsenpreis & Beschaffung"},
        )
        _set_state(
            "sensor.zenwave_preis_netzentgelte",
            data.get("netzentgelte_ct_kwh"),
            {**preis_attrs, "unit_of_measurement": "ct/kWh", "friendly_name": "ZenWave: Preiskomponente Netzentgelte"},
        )
        _set_state(
            "sensor.zenwave_preis_steuern_abgaben",
            data.get("steuern_abgaben_ct_kwh"),
            {**preis_attrs, "unit_of_measurement": "ct/kWh", "friendly_name": "ZenWave: Preiskomponente Steuern & Abgaben"},
        )
    else:
        _LOGGER.warning(
            "Preis-Komponenten (Börsenpreis/Netzentgelte/Steuern) nicht gefunden - "
            "Detailbox auf der Strompreis-Karte vermutlich nicht wie erwartet sichtbar/geparst"
        )

    _LOGGER.info("ZenWave-Sensoren aktualisiert fuer Zeitraum '%s' (Status: %s)", label, data.get("status"))
