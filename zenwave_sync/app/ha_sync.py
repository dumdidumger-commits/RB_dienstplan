"""Schreibt die von ZenWave gelesenen Werte als Sensoren nach Home Assistant.

Nutzt die REST-API direkt ueber SUPERVISOR_TOKEN (gleicher Zugriffsweg wie notify.py) statt
z.B. MQTT - fuer drei simple Werte reicht das, keine zusaetzliche Abhaengigkeit noetig.
"""

import logging
import os
import re
from datetime import datetime, timedelta

import requests

_LOGGER = logging.getLogger(__name__)
_SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
_BASE_URL = "http://supervisor/core/api"

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mär": 3, "Mrz": 3, "Apr": 4, "Mai": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Okt": 10, "Nov": 11, "Dez": 12,
}


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


def _get_state(entity_id: str) -> dict | None:
    if not _SUPERVISOR_TOKEN:
        return None
    resp = requests.get(
        f"{_BASE_URL}/states/{entity_id}",
        headers={"Authorization": f"Bearer {_SUPERVISOR_TOKEN}"},
        timeout=15,
    )
    # 12.08.2026 Fix: 404 ist der ganz normale Fall "Entity existiert noch nicht" (z.B. beim
    # allerersten Schreiben eines neuen Sensors) - kein echter Fehler, einfach None liefern
    # statt einer HTTPError-Exception.
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _epex_price_ct_kwh_at(zeitpunkt_label: str) -> float | None:
    """Sucht in sensor.epex_spot_data_market_price (custom_components/epex_spot) das
    15-Min-Intervall, das zeitpunkt_label ("9. Aug., 11:45", Jahr fehlt im Label) enthaelt,
    und gibt den Boersenpreis in ct/kWh zurueck (der HA-Sensor liefert €/kWh)."""
    m = re.match(r"(\d{1,2})\.\s*(\w{3})\.?,?\s*(\d{2}):(\d{2})", zeitpunkt_label or "")
    if not m:
        return None
    day, mon_abbr, hh, mm = m.groups()
    month = _MONTH_MAP.get(mon_abbr)
    if not month:
        return None

    epex = _get_state("sensor.epex_spot_data_market_price")
    data = (epex or {}).get("attributes", {}).get("data", [])
    if not data:
        return None

    # Jahr/Zeitzone aus dem ersten Datenpunkt uebernehmen, da das gescrapte Label selbst kein
    # Jahr enthaelt.
    ref = datetime.fromisoformat(data[0]["start_time"])
    target = datetime(ref.year, month, int(day), int(hh), int(mm), tzinfo=ref.tzinfo)
    # Jahreswechsel Dezember/Januar wuerde hier falsch landen (Label-Jahr = Referenz-Jahr
    # angenommen) - fuer dieses Add-on aktuell nicht relevant, bei Bedarf spaeter nachruesten.

    for entry in data:
        start = datetime.fromisoformat(entry["start_time"])
        end = datetime.fromisoformat(entry["end_time"])
        if start <= target < end:
            return entry["price_per_kwh"] * 100
    return None


def publish_preisaufschlag(data: dict) -> None:
    """Berechnet den festen Preisaufschlag (Netzentgelte + Steuern & Abgaben + die im
    "Börsenpreis & Beschaffung"-Wert enthaltene Beschaffungsmarge oberhalb des reinen
    EPEX-Rohpreises) und schreibt ihn als sensor.zenwave_preis_aufschlag_fix.

    Ersetzt die bisherige Schätzung `input_number.zenwave_preisaufschlag` in
    sensor.zenwave_gesamtpreis (template.yaml) - Nutzerwunsch 09.08.2026: echte statt
    geschätzte Werte, nachdem sich Netzentgelte/Steuern/Beschaffungsmarge an zwei Messungen
    desselben Tages als praktisch konstant erwiesen (siehe project_zenwave_sync_planning
    Memory).
    """
    netzentgelte = data.get("netzentgelte_ct_kwh")
    steuern = data.get("steuern_abgaben_ct_kwh")
    boersenpreis_beschaffung = data.get("boersenpreis_beschaffung_ct_kwh")
    zeitpunkt = data.get("intervall_zeitpunkt")
    if netzentgelte is None or steuern is None or boersenpreis_beschaffung is None or not zeitpunkt:
        _LOGGER.warning("Preiskomponenten unvollstaendig, kann Fix-Aufschlag nicht berechnen: %s", data)
        return

    epex_ct_kwh = _epex_price_ct_kwh_at(zeitpunkt)
    if epex_ct_kwh is None:
        _LOGGER.warning(
            "Konnte keinen passenden EPEX-Preis fuer Zeitpunkt '%s' finden - "
            "Fix-Aufschlag nicht berechnet", zeitpunkt,
        )
        return

    beschaffungsmarge = boersenpreis_beschaffung - epex_ct_kwh
    aufschlag_fix = netzentgelte + steuern + beschaffungsmarge

    _set_state(
        "sensor.zenwave_preis_aufschlag_fix",
        round(aufschlag_fix, 4),
        {
            "unit_of_measurement": "ct/kWh",
            "friendly_name": "ZenWave: fester Preisaufschlag (Netzentgelte+Steuern+Beschaffungsmarge)",
            "netzentgelte_ct_kwh": netzentgelte,
            "steuern_abgaben_ct_kwh": steuern,
            "beschaffungsmarge_ct_kwh": round(beschaffungsmarge, 4),
            "berechnet_fuer_intervall": zeitpunkt,
            "epex_preis_zu_dem_zeitpunkt_ct_kwh": round(epex_ct_kwh, 4),
            "quelle": "ZenWave-Kundenportal + sensor.epex_spot_data_market_price",
        },
    )
    _LOGGER.info(
        "Fixer Preisaufschlag aktualisiert: %.4f ct/kWh (Netzentgelte %.2f + Steuern %.2f + Beschaffungsmarge %.4f)",
        aufschlag_fix, netzentgelte, steuern, beschaffungsmarge,
    )


def _parse_zeitraum_label_to_iso(label: str) -> str | None:
    """Parst ein Zeitraum-Label wie "11 Aug." zu einem ISO-Datum ("2026-08-11").

    Das Label enthaelt kein Jahr. Da die Intervalldaten-Karte immer einen kuerzlich
    vergangenen Tag zeigt, wird das aktuelle Jahr angenommen - ausser das ergaebe ein Datum in
    der Zukunft (Jahreswechsel-Fall), dann wird das Vorjahr genommen.
    """
    if not label:
        return None
    m = re.match(r"(\d{1,2})\.?\s+(\w{3})\.?", label.strip())
    if not m:
        return None
    day, mon_abbr = m.groups()
    month = _MONTH_MAP.get(mon_abbr)
    if not month:
        return None
    today = datetime.now().date()
    candidate = datetime(today.year, month, int(day)).date()
    if candidate > today:
        candidate = datetime(today.year - 1, month, int(day)).date()
    return candidate.isoformat()


def publish_intervalldaten(data: dict) -> None:
    """Schreibt sensor.zenwave_real_verbrauch / _kosten / _durchschnittspreis.

    `data` ist das Rueckgabe-Dict von zenwave.fetch_intervalldaten(). `zeitraum`/`status` landen
    als Attribute, damit spaeter (siehe project_energieprognose_tag_offset_summary Memory) die
    Tag-Offset-Sensoren pruefen koennen, fuer welchen Tag der Wert gilt und ob er schon final ist,
    bevor sie ihn statt der Shelly/Poweropti-Schaetzung anzeigen.
    """
    label = data.get("zeitraum_label") or "unbekannt"
    # ISO-Datum aus dem tatsaechlich gescrapten Label ("11 Aug.") ableiten - NICHT mehr blind
    # "heute minus 1 Tag" annehmen (Bug, 12.08.2026 gefunden: fuehrte bei mehrfachen Sync-
    # Laeufen am selben Tag, z.B. durch HA-Core-Neustarts ausgeloest, zu falsch beschrifteten
    # Werten, sobald die Intervalldaten-Karte mal nicht exakt den Vortag zeigte). Das Label
    # selbst enthaelt kein Jahr - wird ueber "naeher an heute" (aktuelles vs. voriges Jahr)
    # inferiert, gleiches Muster wie _epex_price_ct_kwh_at() oben.
    datum_iso = _parse_zeitraum_label_to_iso(label)
    if datum_iso is None:
        _LOGGER.warning(
            "Konnte Zeitraum-Label '%s' nicht parsen, falle auf 'heute minus 1 Tag' zurueck",
            label,
        )
        datum_iso = (datetime.now().date() - timedelta(days=1)).isoformat()
    common_attrs = {
        "zeitraum": label,
        "datum": datum_iso,
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


def publish_specific_days(specific_days: dict) -> None:
    """Schreibt gezielt nachgetragene/korrigierte Einzeltage (ueber den Kalender im Zeitraum-
    Dropdown ausgelesen, siehe zenwave.py fetch_intervalldaten(requested_dates=...)) nach
    sensor.zenwave_manuelle_korrekturen (12.08.2026 neu).

    Anders als sensor.zenwave_real_verbrauch/_kosten (immer nur EIN Tag, der zuletzt gescrapte)
    haelt dieser Sensor eine wachsende Sammlung {"YYYY-MM-DD": {...}} als Attribut "tage" -
    damit lassen sich beliebig viele einzelne Tage nachtragen, ohne aeltere zu verlieren.
    app.html prueft diesen Sensor VOR dem Poweropti/Shelly-Schaetzwert-Fallback (siehe
    renderVbTagSummary).
    """
    if not specific_days:
        return
    bestehend = _get_state("sensor.zenwave_manuelle_korrekturen") or {}
    tage = dict((bestehend.get("attributes") or {}).get("tage") or {})
    tage.update(specific_days)
    _set_state(
        "sensor.zenwave_manuelle_korrekturen",
        len(tage),
        {
            "unit_of_measurement": "Tage",
            "friendly_name": "ZenWave: manuell nachgetragene/korrigierte Tage",
            "quelle": "ZenWave-Kundenportal (gezielte Kalenderauswahl)",
            "tage": tage,
        },
    )
    _LOGGER.info("Manuelle Korrekturtage aktualisiert: %s", list(specific_days.keys()))


def publish_preis_snapshot(data: dict) -> None:
    """Speichert einen echten 'Aktuell'-Preis-Schnappschuss mit Zeitstempel in
    sensor.zenwave_preis_historie_real (12.08.2026 neu, Roland-Wunsch: haeufigere echte
    Preis-Werte statt nur der einmal taeglichen Hochrechnung, um Kosten der Vergangenheit
    moeglichst genau zu berechnen - siehe project_zenwave_sync_planning Memory). Waechst
    ueber die Zeit, Eintraege aelter als 30 Tage werden automatisch entfernt."""
    preis = data.get("aktueller_preis_ct_kwh")
    if preis is None:
        _LOGGER.warning("Kein aktueller Preis zum Speichern in der Preis-Historie vorhanden")
        return
    jetzt_iso = datetime.now().astimezone().isoformat(timespec="minutes")
    bestehend = _get_state("sensor.zenwave_preis_historie_real") or {}
    punkte = dict((bestehend.get("attributes") or {}).get("punkte") or {})
    punkte[jetzt_iso] = preis
    grenze = datetime.now().astimezone() - timedelta(days=30)
    punkte = {
        k: v for k, v in punkte.items()
        if datetime.fromisoformat(k) >= grenze
    }
    _set_state(
        "sensor.zenwave_preis_historie_real",
        preis,
        {
            "unit_of_measurement": "ct/kWh",
            "friendly_name": "ZenWave: echte Preis-Historie (Schnappschüsse)",
            "quelle": "ZenWave-Kundenportal (Strompreis-Karte, mehrmals täglich)",
            "anzahl_punkte": len(punkte),
            "punkte": punkte,
        },
    )
    _LOGGER.info("Preis-Schnappschuss gespeichert: %s ct/kWh um %s (insgesamt %d Punkte)", preis, jetzt_iso, len(punkte))


def publish_kalibrierungsvergleich(data: dict) -> None:
    """Vergleicht den echten 'Aktuell'-Preis (von der ZenWave-Seite) mit dem, was unsere
    eigene Hochrechnung (sensor.zenwave_preiskurve_real = EPEX-Rohpreis + fester Aufschlag,
    siehe template.yaml) fuer denselben Moment liefert, und speichert die Abweichung mit
    Zeitstempel in sensor.zenwave_kalibrierung_delta (12.08.2026 neu, Roland-Idee: statt
    alle 15 Min echt einzuloggen lieber bei den bestehenden 5-stuendigen Laeufen bleiben und
    ueber die Zeit pruefen, ob die Hochrechnung systematisch daneben liegt - Muster wie bei
    der Solarprognose-Kalibrierung, siehe solar_forecast_calibration Memory). Wird bewusst
    NUR geloggt/gesammelt, keine automatische Korrektur - erst wenn genug Datenpunkte da
    sind, entscheiden wir anhand des Durchschnitts, ob/wie stark der Aufschlag nachjustiert
    werden sollte."""
    real = data.get("aktueller_preis_ct_kwh")
    if real is None:
        return
    hochrechnung_state = _get_state("sensor.zenwave_preiskurve_real")
    if hochrechnung_state is None or hochrechnung_state.get("state") in (None, "unknown", "unavailable"):
        _LOGGER.warning("sensor.zenwave_preiskurve_real noch nicht verfuegbar, ueberspringe Kalibrierungsvergleich")
        return
    try:
        hochrechnung = float(hochrechnung_state["state"])
    except (TypeError, ValueError):
        return
    delta = round(real - hochrechnung, 3)
    jetzt_iso = datetime.now().astimezone().isoformat(timespec="minutes")
    bestehend = _get_state("sensor.zenwave_kalibrierung_delta") or {}
    punkte = dict((bestehend.get("attributes") or {}).get("punkte") or {})
    punkte[jetzt_iso] = delta
    grenze = datetime.now().astimezone() - timedelta(days=30)
    punkte = {
        k: v for k, v in punkte.items()
        if datetime.fromisoformat(k) >= grenze
    }
    durchschnitt = round(sum(punkte.values()) / len(punkte), 3) if punkte else None
    _set_state(
        "sensor.zenwave_kalibrierung_delta",
        delta,
        {
            "unit_of_measurement": "ct/kWh",
            "friendly_name": "ZenWave: Hochrechnung-Abweichung (echt minus berechnet)",
            "quelle": "Vergleich ZenWave-Kundenportal 'Aktuell' vs. sensor.zenwave_preiskurve_real",
            "echter_preis_ct_kwh": real,
            "hochgerechneter_preis_ct_kwh": hochrechnung,
            "durchschnitt_ct_kwh": durchschnitt,
            "anzahl_punkte": len(punkte),
            "punkte": punkte,
        },
    )
    _LOGGER.info(
        "Kalibrierungsvergleich: echt=%s ct/kWh, hochgerechnet=%s ct/kWh, Abweichung=%s ct/kWh "
        "(Durchschnitt ueber %d Punkte: %s)",
        real, hochrechnung, delta, len(punkte), durchschnitt,
    )
