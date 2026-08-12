"""ZenWave-Kundenportal (Zendure): Login + Auslesen der "Intervalldaten"-Karte im
"Verbrauch"-Tab.

**Status 09.08.2026: erster Entwurf, noch UNGETESTET gegen die echte Seite.** Anders als beim
Nachbar-Add-on "Vivendi Dienstplan Sync" (wo drei echte Testlaeufe mit Debug-Screenshots noetig
waren, um die tatsaechlichen Selektoren zu finden - siehe dessen README) kennen wir bisher nur
das Login-ERGEBNIS (zwei Screenshots der bereits eingeloggten Verbrauch-/Startseite), nicht das
Login-FORMULAR selbst. Die Selektoren unten sind ein informierter erster Versuch (generische
E-Mail-/Passwort-Felder ueber Feldtyp statt hartcodierter CSS-Klassen, da Next.js-Buildhashes
sich bei jedem Deploy aendern) - nach dem ersten echten Testlauf mit debug_screenshots=true
werden sie wahrscheinlich nachjustiert werden muessen, genau wie bei Vivendi.

Bekannt aus den Screenshots vom 09.08.2026 (siehe Memory project_zenwave_sync_planning):
- ZenWave ist eine Next.js/React-SPA - kein Server-Login moeglich, braucht echten Browser
- "Verbrauch"-Tab -> "Intervalldaten"-Karte hat ein "Zeitraum: [Tag] ▾"-Dropdown, darunter
  Ø Preis / Verbrauch / Variable Kosten fuer den gewaehlten Tag
- Drei Datenstatus (Legende): Final / Vorlaeufig / Ersatzwert - wie genau sich der Status fuer
  einen kompletten Tag (nicht nur ein einzelnes 15-Min-Intervall) ablesen laesst, ist NOCH NICHT
  geklaert (auf den bisherigen Screenshots nicht sichtbar) - `status` liefert vorerst "unknown"
- Wie sich das Zeitraum-Dropdown fuer beliebige vergangene Tage bedienen laesst, ist ebenfalls
  noch nicht geklaert - dieser erste Wurf liest nur den beim Laden der Seite standardmaessig
  gezeigten Zeitraum (vermutlich der letzte abgeschlossene Tag).

Naechste Iteration (nach dem ersten echten Testlauf): Debug-Screenshots/HTML-Dumps unter
/share/zenwave_sync/debug/ auswerten und diese Datei entsprechend nachziehen.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date

from playwright.sync_api import Page, sync_playwright

_LOGGER = logging.getLogger(__name__)
_DEBUG_DIR = "/share/zenwave_sync/debug"
_MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def _debug_shot(page: Page, name: str, html: bool = False) -> None:
    if os.environ.get("ZENWAVE_SYNC_DEBUG") != "1":
        return
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    path = f"{_DEBUG_DIR}/{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        _LOGGER.exception("Debug-Screenshot fehlgeschlagen: %s", name)
    if html:
        try:
            with open(f"{_DEBUG_DIR}/{name}.html", "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception:
            _LOGGER.exception("Debug-HTML-Dump fehlgeschlagen: %s", name)


class ZenwaveLoginError(Exception):
    pass


class ZenwaveScrapeError(Exception):
    pass


def _parse_de_number(text: str) -> float | None:
    """Wandelt deutsches Zahlenformat ("33,56" oder "1.234,56") in float um."""
    if not text:
        return None
    cleaned = text.strip().replace(".", "").replace(",", ".")
    match = re.search(r"-?\d+(\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def _labeled_ct_value(text: str, label: str) -> float | None:
    """Sucht "<label>\\n<Zahl>ct/kWh" (echtes Format laut erstem Live-Auslesen 09.08.2026 -
    KEIN Leerzeichen vor "ct/kWh", Label steht direkt vor dem Wert, kein Legenden-Rateversuch
    mehr noetig)."""
    m = re.search(re.escape(label) + r"\s*\n?\s*([\d.,]+)\s*ct/kWh", text)
    return _parse_de_number(m.group(1)) if m else None


def _parse_strompreis_card(text: str) -> dict:
    """Auslese der "Strompreis"-Karte auf der Startseite: Ø Preis, aktueller Preis, und (aus
    der "Jetzt"-Detailbox) die 3 Preiskomponenten samt Zeitstempel des Intervalls.

    Format live verifiziert (09.08.2026): "Steuern & Abgaben\\n7,83ct/kWh\\nNetzentgelte\\n
    11,34ct/kWh\\nBörsenpreis & Beschaffung\\n1,92ct/kWh\\nGesamtpreis\\n21,09ct/kWh" - jeder
    Wert hat sein eigenes Inline-Label direkt davor, kein Raten der Reihenfolge noetig.

    Auffaellig beim Vergleich zweier Messungen am selben Tag (09:45 vs. 11:45 Uhr):
    Netzentgelte war beide Male EXAKT 11,34 ct/kWh, Steuern & Abgaben praktisch identisch
    (7,84 vs. 7,83). Nur "Börsenpreis & Beschaffung" aenderte sich (2,01 vs. 1,92) - passt zur
    Erwartung, dass Netzentgelte/Steuern in Deutschland ueblicherweise feste Ct/kWh-Saetze
    sind, waehrend nur die Beschaffungskomponente mit dem EPEX-Spotpreis schwankt. Falls sich
    das bestaetigt, muss fuer eine volle 15-Min-Kurve NICHT jedes Intervall einzeln gescraped
    werden - stattdessen reicht es, Netzentgelte+Steuern (aendern sich vermutlich nur sehr
    selten) periodisch zu scrapen und mit dem ohnehin vorhandenen eigenen epex_spot-Sensor
    (liefert alle 15-Min-Intervalle fuer heute+morgen) selbst zusammenzurechnen - siehe
    project_zenwave_sync_planning Memory fuer den Stand dieser Untersuchung.
    """
    preis_pos = text.find("Ø Preis")
    tail = text[preis_pos:] if preis_pos >= 0 else text
    avg_match = re.search(r"Ø\s*Preis[^\d]*([\d.,]+)\s*ct", tail)
    aktuell_match = re.search(r"Aktuell[^\d]*([\d.,]+)\s*ct", tail)
    zeitpunkt_match = re.search(r"(\d{1,2}\.\s*\w+\.,\s*\d{2}:\d{2})", tail)

    return {
        # Eigener Praefix "strompreis_" (statt "durchschnittspreis_ct_kwh" wie bei der
        # Intervalldaten-Karte), da beide Karten je ein "Ø Preis"-Feld haben, aber mit
        # unterschiedlichem Bezug (Intervalldaten: gewaehlter Tag: Strompreis-Karte: "Aktuell").
        "strompreis_durchschnitt_ct_kwh": _parse_de_number(avg_match.group(1)) if avg_match else None,
        "aktueller_preis_ct_kwh": _parse_de_number(aktuell_match.group(1)) if aktuell_match else None,
        "intervall_zeitpunkt": zeitpunkt_match.group(1) if zeitpunkt_match else None,
        "gesamtpreis_jetzt_ct_kwh": _labeled_ct_value(tail, "Gesamtpreis"),
        "boersenpreis_beschaffung_ct_kwh": _labeled_ct_value(tail, "Börsenpreis & Beschaffung"),
        "netzentgelte_ct_kwh": _labeled_ct_value(tail, "Netzentgelte"),
        "steuern_abgaben_ct_kwh": _labeled_ct_value(tail, "Steuern & Abgaben"),
        "raw_strompreis_text": text,
    }


def fetch_intervalldaten(
    login_url: str, username: str, password: str, requested_dates: list[date] | None = None
) -> dict:
    """Loggt sich ein und liest die "Intervalldaten"-Karte im "Verbrauch"-Tab aus.

    Gibt den beim Laden standardmaessig gezeigten Zeitraum zurueck (fuer den taeglichen Sync).
    `requested_dates` (12.08.2026 ergaenzt): optionale Liste konkreter vergangener Tage, die
    zusaetzlich gezielt ueber den Kalender im "Zeitraum"-Dropdown ausgelesen werden (z.B. zum
    manuellen Nachtragen/Verifizieren einzelner Tage) - landet im Rueckgabe-Dict unter
    "specific_days" als {"2026-08-10": {...}, ...}.
    """
    specific_days_result: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Netzwerk-Mitschnitt (nur im Debug-Modus): SPAs laden Chart-/Preisdaten fast immer
        # ueber eine JSON-API im Hintergrund - das ist viel robuster auszuwerten als ein
        # SVG/Canvas-Chart optisch nachzubauen. Dient der Erkundung, WELCHE Endpunkte die
        # "Strompreis"-Karte (naechste 24h, Preis-Komponenten) speist, bevor dafuer eigener
        # Auslese-Code geschrieben wird.
        api_log: list[dict] = []
        if os.environ.get("ZENWAVE_SYNC_DEBUG") == "1":
            def _log_api_response(response):
                try:
                    ctype = response.headers.get("content-type", "")
                    if "application/json" not in ctype:
                        return
                    try:
                        body = response.text()
                    except Exception:
                        body = None
                    api_log.append({
                        "url": response.url,
                        "status": response.status,
                        "method": response.request.method,
                        "body": body[:20000] if body else None,
                    })
                except Exception:
                    pass
            page.on("response", _log_api_response)

        try:
            page.goto(login_url, wait_until="networkidle", timeout=30000)
            _debug_shot(page, "01_login_page", html=True)

            # Zweistufiger Login (09.08.2026 per Debug-Screenshot bestaetigt, "powered by
            # Nomos"-Auth): Schritt 1 zeigt NUR ein E-Mail-Feld, kein Passwort. Passwort (oder
            # ggf. ein Einmalcode) kommt erst auf einer zweiten Seite - deshalb hier bewusst
            # NICHT wie im ersten Entwurf beide Felder blind zusammen befuellen.
            email_field = page.locator(
                "input[type=email], input[name*=mail i], input[autocomplete=username]"
            ).first
            email_field.wait_for(state="visible", timeout=15000)
            email_field.fill(username)
            _debug_shot(page, "02_email_ausgefuellt")

            submit_button = page.locator(
                "button[type=submit], button:has-text('Einloggen'), "
                "button:has-text('Anmelden'), button:has-text('Login'), "
                "button:has-text('Weiter')"
            ).first
            submit_button.click()
            page.wait_for_load_state("networkidle", timeout=30000)
            _debug_shot(page, "03_nach_email_schritt", html=True)

            password_field = page.locator("input[type=password]").first
            try:
                password_field.wait_for(state="visible", timeout=15000)
            except Exception as exc:
                _debug_shot(page, "03b_kein_passwortfeld", html=True)
                raise ZenwaveLoginError(
                    "Nach der E-Mail-Eingabe erschien innerhalb von 15s kein Passwortfeld - "
                    "moeglicherweise ein Einmalcode-/Magic-Link-Verfahren statt Passwort-Login. "
                    "Siehe Debug-Screenshot 03_nach_email_schritt."
                ) from exc
            password_field.fill(password)
            _debug_shot(page, "04_passwort_ausgefuellt")

            password_submit = page.locator(
                "button[type=submit], button:has-text('Einloggen'), "
                "button:has-text('Anmelden'), button:has-text('Login'), "
                "button:has-text('Weiter')"
            ).first
            password_submit.click()
            # Zwei vorige Versuche pruefte per Abwesenheit des Passwortfelds - Debug-
            # Screenshots vom 09.08.2026 zeigten aber wiederholt, dass der Login zu dem
            # Zeitpunkt schon erfolgreich war ("Sicher eingeloggt."-Toast sichtbar), die Seite
            # aber noch zwischen Login-Formular und Dashboard haengt (Ladespinner). Eine reine
            # Abwesenheitspruefung ist hier zu anfaellig fuer diesen Zwischenzustand - deshalb
            # jetzt direkt auf ein positives Erfolgsmerkmal warten (die "Verbrauch"-Navigation),
            # statt auf das Fehlen des Passwortfelds zu schliessen.
            try:
                page.locator("text=Verbrauch").first.wait_for(state="visible", timeout=25000)
            except Exception as exc:
                _debug_shot(page, "05b_login_vermutlich_fehlgeschlagen", html=True)
                raise ZenwaveLoginError(
                    "Nach dem Absenden des Passwort-Formulars ist innerhalb von 25s keine "
                    "'Verbrauch'-Navigation erschienen - Login vermutlich fehlgeschlagen. "
                    "Siehe Debug-Screenshots."
                ) from exc
            _debug_shot(page, "05_nach_login", html=True)

            # Kurz auf der Startseite bleiben, damit die "Strompreis"-Karte (naechste 24h,
            # Preis-Komponenten) ihre Daten laedt und dabei vom Netzwerk-Mitschnitt oben
            # erfasst wird, bevor wir zum "Verbrauch"-Tab weiterklicken (09.08.2026,
            # Nutzerwunsch: auch die Preisprognose/-struktur mit auslesen statt nur Verbrauch).
            strompreis_data: dict = {}
            try:
                page.locator("text=STROMPREIS").first.wait_for(state="visible", timeout=15000)
                page.wait_for_timeout(3000)  # Chart-/API-Daten laden meist noch kurz nach
                _debug_shot(page, "05b_startseite_strompreis", html=True)
                strompreis_card = page.locator("text=STROMPREIS").first.locator(
                    "xpath=ancestor::*[self::div][.//text()[contains(., 'Quelle: EPEX Spot')]]"
                ).first
                strompreis_data = _parse_strompreis_card(strompreis_card.inner_text())
            except Exception:
                _LOGGER.exception("STROMPREIS-Karte konnte nicht gelesen werden (nicht fatal)")

            # "networkidle" erwies sich hier als unzuverlaessig (SPA haelt vermutlich
            # dauerhaft Hintergrundverbindungen offen - Websocket-Heartbeat, Analytics - daher
            # loest "idle" nie aus, 30s-Timeout). Stattdessen direkt auf das naechste konkrete
            # Element warten, das wir sowieso brauchen ("INTERVALLDATEN"-Karte).
            verbrauch_tab = page.locator("text=Verbrauch").first
            verbrauch_tab.click()
            page.locator("text=INTERVALLDATEN").first.wait_for(state="visible", timeout=20000)
            _debug_shot(page, "06_verbrauch_tab", html=True)
            # Sucht das umschliessende Karten-Element ueber den gemeinsamen Vorfahren von
            # "INTERVALLDATEN" und "Variable Kosten" - robuster gegen wechselnde CSS-Klassen
            # als ein hartcodierter Selektor, siehe Modul-Docstring.
            card = page.locator("text=INTERVALLDATEN").first.locator(
                "xpath=ancestor::*[self::div][.//text()[contains(., 'Variable Kosten')]]"
            ).first
            card_text = card.inner_text()
            _debug_shot(page, "07_intervalldaten_karte")

            # 12.08.2026: Fuer explizit angefragte vergangene Tage (Parameter requested_dates)
            # den Kalender im "Zeitraum"-Dropdown bedienen, statt nur den default
            # gezeigten Zeitraum zu lesen. Aufbau des Dropdowns (per Screenshot verifiziert):
            # Schnellauswahl (Heute/Gestern/Letzte X Tage/...) links, echter Monatskalender
            # rechts mit anklickbaren Tageszahlen, unten "Ausgewaehlt: <Label>".
            if requested_dates:
                for target in requested_dates:
                    zeitraum_btn = card.locator("button:has-text('Zeitraum')").first
                    zeitraum_btn.click()
                    page.wait_for_timeout(500)
                    calendar_popup = page.locator("text=Ausgewählt:").locator(
                        "xpath=ancestor::*[self::div][.//table or .//*[contains(@class,'grid')]][1]"
                    ).first
                    # Ggf. Monat wechseln, falls das Zieldatum nicht im aktuell gezeigten
                    # Monat liegt (Kalenderkopf "August 2026" o.ae. mit </> Pfeilen daneben).
                    month_label = calendar_popup.locator("text=/^[A-Za-zäöü]+ \\d{4}$/").first
                    for _ in range(6):  # Sicherheitslimit gegen Endlosschleife
                        current_label = month_label.inner_text()
                        target_label = f"{_MONTH_NAMES_DE[target.month - 1]} {target.year}"
                        if current_label == target_label:
                            break
                        # Zurueckblaettern (Pfeil links = vorheriger Monat) - fuer unseren
                        # Anwendungsfall (kuerzlich vergangene Tage) reicht "rueckwaerts".
                        calendar_popup.locator("button").first.click()
                        page.wait_for_timeout(300)
                    day_cell = calendar_popup.locator(
                        f"xpath=.//button[normalize-space(text())='{target.day}']"
                    ).first
                    day_cell.click()
                    page.wait_for_timeout(800)
                    fresh_card_text = card.inner_text()
                    fresh_verbrauch = re.search(r"Verbrauch[^\d]*([\d.,]+)\s*kWh", fresh_card_text)
                    fresh_kosten = re.search(r"Variable Kosten[^\d]*([\d.,]+)\s*€", fresh_card_text)
                    fresh_preis = re.search(r"Ø\s*Preis[^\d]*([\d.,]+)\s*ct", fresh_card_text)
                    specific_days_result[target.isoformat()] = {
                        "verbrauch_kwh": _parse_de_number(fresh_verbrauch.group(1)) if fresh_verbrauch else None,
                        "kosten_eur": _parse_de_number(fresh_kosten.group(1)) if fresh_kosten else None,
                        "durchschnittspreis_ct_kwh": _parse_de_number(fresh_preis.group(1)) if fresh_preis else None,
                        "raw_card_text": fresh_card_text,
                    }
                    _debug_shot(page, f"day_{target.isoformat()}")
                    _LOGGER.info("Tag %s gelesen: %s", target.isoformat(), specific_days_result[target.isoformat()])

            preis_match = re.search(r"Ø\s*Preis[^\d]*([\d.,]+)\s*ct", card_text)
            verbrauch_match = re.search(r"Verbrauch[^\d]*([\d.,]+)\s*kWh", card_text)
            kosten_match = re.search(r"Variable Kosten[^\d]*([\d.,]+)\s*€", card_text)
            zeitraum_match = re.search(r"Zeitraum:\s*([^\n]+)", card_text)

            result = {
                "zeitraum_label": zeitraum_match.group(1).strip() if zeitraum_match else None,
                "durchschnittspreis_ct_kwh": _parse_de_number(preis_match.group(1)) if preis_match else None,
                "verbrauch_kwh": _parse_de_number(verbrauch_match.group(1)) if verbrauch_match else None,
                "kosten_eur": _parse_de_number(kosten_match.group(1)) if kosten_match else None,
                # TODO (naechste Iteration): Final/Vorlaeufig/Ersatzwert-Status fuer den ganzen
                # Tag noch nicht bekannt, siehe Modul-Docstring.
                "status": "unknown",
                "raw_card_text": card_text,
                "specific_days": specific_days_result,
                **strompreis_data,
            }
            if result["verbrauch_kwh"] is None or result["kosten_eur"] is None:
                _debug_shot(page, "99_parse_fehlgeschlagen", html=True)
                raise ZenwaveScrapeError(
                    f"Konnte Verbrauch/Kosten nicht aus der Intervalldaten-Karte lesen. "
                    f"Rohtext: {card_text!r}"
                )
            return result
        finally:
            if api_log:
                try:
                    os.makedirs(_DEBUG_DIR, exist_ok=True)
                    with open(f"{_DEBUG_DIR}/api_responses.json", "w", encoding="utf-8") as f:
                        json.dump(api_log, f, ensure_ascii=False, indent=2)
                    _LOGGER.info("%d API-Antworten mitgeschnitten -> %s/api_responses.json", len(api_log), _DEBUG_DIR)
                except Exception:
                    _LOGGER.exception("Konnte API-Mitschnitt nicht schreiben")
            browser.close()
