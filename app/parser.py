"""Parst die Vivendi-Dienstplan-Exceldatei (Sheet "Dienstliste") in eine Liste von Schichten.

Regeln (siehe README/Projekt-Spec):
- Datum-Vererbung: leere Datum-Zellen gehoeren zur letzten Zeile mit gesetztem Datum
  (Doppeldienst-Zeile).
- Schichtzeit kommt aus der "Aufgabe"-Spalte: "HH:MM - HH:MM (Pausenminuten) ".
- Schichttyp kommt aus der "Ist"-Spalte, ueber die externe kuerzel_mapping.yaml aufgeloest
  (exact -> exact_prefix_override -> prefix als Fallback ueber den ersten Buchstaben).
- Ist-Code, der auf null gemappt wird (z.B. "/", "FZA So", "!") => keine Schicht, kein
  Kalendereintrag.

Validiert gegen die vom Nutzer bereitgestellte Beispieldatei (f2eaef64, August 2026,
39 Zeilen) - siehe Kommentare in kuerzel_mapping.yaml.example zu "SF"/"TB", die dort nicht
sicher zugeordnet werden konnten.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd
import yaml

_LOGGER = logging.getLogger(__name__)

_AUFGABE_RE = re.compile(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s*\((\d+)\)")

SHEET_NAME = "Dienstliste"


@dataclass
class Shift:
    datum: date
    ist_code: str
    typ_label: str
    start: str | None
    ende: str | None
    pause_min: int | None
    bereich: str | None


def _cell_str(value) -> str:
    """Wandelt einen pandas-Zellwert sicher in einen String um. Wichtig: "value or ''"
    waere hier ein Bug - NaN ist in Python truthy, "NaN or ''" liefert also NaN zurueck statt
    des gewuenschten Leerstrings, und str(NaN) ergibt den literalen Text "nan"."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def load_kuerzel_mapping(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("exact", {})
    data.setdefault("exact_prefix_override", {})
    data.setdefault("prefix", {})
    return data


def _resolve_typ_label(ist_code: str, mapping: dict) -> str | None:
    """None bedeutet: kein Kalendereintrag fuer diesen Code."""
    code = (ist_code or "").strip()
    code_lower = code.lower()

    exact = {k.lower(): v for k, v in mapping["exact"].items()}
    if code_lower in exact:
        return exact[code_lower]

    for prefix, label in mapping["exact_prefix_override"].items():
        if code_lower.startswith(prefix.lower()):
            return label

    if code:
        first_letter = code[0].upper()
        prefix_map = {k.upper(): v for k, v in mapping["prefix"].items()}
        if first_letter in prefix_map:
            return prefix_map[first_letter]

    _LOGGER.warning("Unbekannter Ist-Code %r - keine Zuordnung in kuerzel_mapping.yaml gefunden, wird uebersprungen", ist_code)
    return None


def parse_dienstplan(xlsx_path: str, kuerzel_mapping_path: str) -> list[Shift]:
    mapping = load_kuerzel_mapping(kuerzel_mapping_path)

    # Kein dtype=str hier: die Datum-Spalte soll pandas/openpyxl natuerlich als
    # Timestamp/NaT einlesen (nicht als String "2026-08-01 00:00:00" o.ae.), sonst wird die
    # Erkennung leerer Zellen unnoetig fehleranfaellig.
    df = pd.read_excel(xlsx_path, sheet_name=SHEET_NAME)
    df.columns = [str(c).strip() for c in df.columns]

    # Datum-Vererbung: leere Zellen sind hier NaT (nicht float-NaN, da Datumsspalte) ->
    # ffill deckt die Doppeldienst-Zeilen ab.
    df["Datum"] = df["Datum"].ffill()

    shifts: list[Shift] = []
    for _, row in df.iterrows():
        raw_datum = row.get("Datum")
        if pd.isna(raw_datum):
            continue
        try:
            datum = pd.to_datetime(raw_datum).date()
        except (ValueError, TypeError):
            _LOGGER.warning("Konnte Datum nicht parsen: %r - Zeile wird uebersprungen", raw_datum)
            continue

        ist_code = _cell_str(row.get("Ist"))
        typ_label = _resolve_typ_label(ist_code, mapping)
        if typ_label is None:
            continue

        aufgabe = _cell_str(row.get("Aufgabe"))
        m = _AUFGABE_RE.search(aufgabe)
        start = ende = None
        pause_min = None
        if m:
            start, ende, pause_str = m.groups()
            pause_min = int(pause_str)

        shifts.append(Shift(
            datum=datum,
            ist_code=ist_code,
            typ_label=typ_label,
            start=start,
            ende=ende,
            pause_min=pause_min,
            bereich=(_cell_str(row.get("Bereich")) or None),
        ))

    return shifts
