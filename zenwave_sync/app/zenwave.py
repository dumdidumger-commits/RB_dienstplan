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

import logging
import os
import re

from playwright.sync_api import Page, sync_playwright

_LOGGER = logging.getLogger(__name__)
_DEBUG_DIR = "/share/zenwave_sync/debug"


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


def fetch_intervalldaten(login_url: str, username: str, password: str) -> dict:
    """Loggt sich ein und liest die "Intervalldaten"-Karte im "Verbrauch"-Tab aus.

    Gibt vorerst nur den beim Laden standardmaessig gezeigten Zeitraum zurueck - siehe
    Modul-Docstring.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
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

            verbrauch_tab = page.locator("text=Verbrauch").first
            verbrauch_tab.click()
            page.wait_for_load_state("networkidle", timeout=30000)
            _debug_shot(page, "06_verbrauch_tab", html=True)

            page.locator("text=INTERVALLDATEN").first.wait_for(state="visible", timeout=15000)
            # Sucht das umschliessende Karten-Element ueber den gemeinsamen Vorfahren von
            # "INTERVALLDATEN" und "Variable Kosten" - robuster gegen wechselnde CSS-Klassen
            # als ein hartcodierter Selektor, siehe Modul-Docstring.
            card = page.locator("text=INTERVALLDATEN").first.locator(
                "xpath=ancestor::*[self::div][.//text()[contains(., 'Variable Kosten')]]"
            ).first
            card_text = card.inner_text()
            _debug_shot(page, "07_intervalldaten_karte")

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
            }
            if result["verbrauch_kwh"] is None or result["kosten_eur"] is None:
                _debug_shot(page, "99_parse_fehlgeschlagen", html=True)
                raise ZenwaveScrapeError(
                    f"Konnte Verbrauch/Kosten nicht aus der Intervalldaten-Karte lesen. "
                    f"Rohtext: {card_text!r}"
                )
            return result
        finally:
            browser.close()
