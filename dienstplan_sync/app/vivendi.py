"""Login + Export-Download vom Vivendi-Self-Service-Portal via Playwright.

WARUM PLAYWRIGHT (nicht requests): Das Login-Formular sendet neben Username/Password ein
drittes Pflichtfeld "PublicKey" an POST /api/vivendi/v1/auth/login - vermutlich clientseitig
per Web-Crypto-API erzeugt. Der Mechanismus liess sich aus dem stark minifizierten
Angular-Bundle nicht sicher rekonstruieren (siehe Recherche-Notizen unten). Playwright
umgeht das Problem komplett: es bedient das echte Formular im echten Browser, die Seite
generiert PublicKey etc. also ganz normal selbst - wir muessen den Mechanismus gar nicht
verstehen.

RECHERCHE-STAND (29.07.2026, per HTML/JS-Analyse ohne Zugangsdaten zu verwenden):
- Login-Endpunkt: POST https://vivendiselfservice.johanniter.de:8755/api/vivendi/v1/auth/login
  erwartet JSON {"Username": ..., "Password": ..., "PublicKey": ...}
- Login-Formular (Angular Reactive Form): Labels "Benutzer" / "Kennwort", Submit-Button
  "Anmelden". Interne Formularfelder heissen "benutzernam" bzw. "passwort".
- Es gibt zusaetzlich "Mit Windows Benutzer anmelden" und "Microsoft Entra anmelden" als
  alternative Login-Wege - fuer dieses Add-on nicht relevant (Spec sagt: normales
  Formular-Login, kein 2FA sichtbar).
- Navigation zum Export: zwei echte Testlaeufe (30.07.2026, mit Debug-Screenshots) haben die
  Kette vollstaendig verifiziert und korrigiert. Tatsaechlicher Ablauf: Startseite ->
  Lesezeichen "Dienstplan" (nicht "Kalender anzeigen", das kommt nirgends vor) -> Kalender-
  ansicht mit einem direkt beschrifteten "Export"-Button oben links (kein Drei-Punkte-Menu) ->
  Klick oeffnet ein Dropdown mit "Excel" als Option -> Download startet.
- WICHTIGER BEFUND (2./3. Testlauf, 30.07.2026): Der Export bezieht sich auf den GERADE
  ANGEZEIGTEN Monat der Kalenderansicht, nicht automatisch auf den aktuellen Monat. Beim
  Testlauf zeigte die Ansicht beim ersten Laden "Februar 2026" (vermutlich der zuletzt
  angesehene Monat des Vivendi-Accounts), 5 Monate in der Vergangenheit - der Sync fand
  dadurch 0 passende Schichten im Sync-Fenster. Deshalb navigiert der Code vor jedem Export
  per Pfeil-Buttons zum gewuenschten Monat (siehe _go_to_month unten) - Selektoren per
  HTML-Dump verifiziert (aria-label "Monat Vor"/"Monat Zurück"), vierter Testlauf erfolgreich
  (18 Termine synchronisiert).
- ERWEITERT (30.07.2026, vom Nutzer bestaetigt): Vivendi veroeffentlicht den Folgemonat
  jeweils am 15./16. des Vormonats. download_dienstplan exportiert deshalb ab dem 17. zwei
  Monate (aktueller + Folgemonat) statt nur einen, in derselben Browser-Sitzung nacheinander
  (kein zweiter Login). Siehe main.py fuer die Datums-Schwelle.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date as _dt_date

from playwright.sync_api import Page, sync_playwright, TimeoutError as PlaywrightTimeoutError

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://vivendiselfservice.johanniter.de:8755/areas/login/#/login"

# Bei Bedarf DIENSTPLAN_SYNC_DEBUG=1 in den Add-on-Optionen/der Umgebung setzen (oder beim
# manuellen Testlauf exportieren): speichert nach jedem Navigationsschritt einen Screenshot
# unter /share/dienstplan_sync/debug/ - sehr hilfreich, um die noch unverifizierten Selektoren
# fuer das Drei-Punkte-Menu/Excel-Export beim ersten echten Testlauf nachzujustieren.
# Bewusst unter /share statt /data: /share ist von aussen (Samba, File-Editor-Add-on, andere
# Add-ons mit share:rw) einsehbar, /data ist der private, isolierte Datenbereich dieses
# Add-ons und von aussen nicht erreichbar.
#
# WICHTIG (Bug gefunden 2026-07-30 beim ersten echten Testlauf): dieses Flag darf NICHT als
# Modul-Konstante zum Import-Zeitpunkt gelesen werden. main.py importiert vivendi ganz oben,
# noch bevor main() die Optionen laedt und DIENSTPLAN_SYNC_DEBUG setzt - eine Konstante haette
# also immer den Stand VOR dem Setzen der Env-Variable eingefroren (= immer False). Deshalb
# hier bei jedem Aufruf frisch aus der Umgebung lesen.
_DEBUG_DIR = "/share/dienstplan_sync/debug"


def _debug_shot(page: Page, name: str, html: bool = False) -> None:
    if os.environ.get("DIENSTPLAN_SYNC_DEBUG") != "1":
        return
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    path = f"{_DEBUG_DIR}/{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
        _LOGGER.debug("Debug-Screenshot gespeichert: %s", path)
    except Exception:  # noqa: BLE001 - Screenshot ist nur Debug-Hilfe, darf nie den Lauf abbrechen
        _LOGGER.exception("Debug-Screenshot fehlgeschlagen (%s)", name)
    if html:
        # Zusaetzlich echtes HTML sichern statt nur Screenshot - noetig, wenn ein Text-basierter
        # Selektor unerwartet nicht greift (z.B. Monat/Jahr in getrennten Elementen statt einem
        # zusammenhaengenden Textknoten) und das reine Bild die DOM-Struktur nicht zeigt.
        try:
            with open(f"{_DEBUG_DIR}/{name}.html", "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception:  # noqa: BLE001 - auch hier: darf den Lauf nie abbrechen
            _LOGGER.exception("Debug-HTML fehlgeschlagen (%s)", name)


_MONATE = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}


def _go_to_month(page: Page, target_year: int, target_month: int) -> None:
    """Navigiert die Kalenderansicht per Pfeil-Buttons zum angegebenen Monat.

    Noetig, weil der Export sich auf den GERADE ANGEZEIGTEN Monat bezieht, nicht automatisch
    auf den aktuellen (echt beobachtet 30.07.2026: Ansicht zeigte beim Laden "Februar 2026",
    5 Monate in der Vergangenheit, wodurch der Sync 0 Treffer im Sync-Fenster fand).

    Selektoren verifiziert per HTML-Dump (30.07.2026, dritter Testlauf, siehe
    03_kalender.html): Ueberschrift ist ein <div class="cx-calendar-date-select__date-label">,
    die Pfeil-Buttons haben eindeutige aria-label "Monat Zurück"/"Monat Vor" (kein Icon-Text-
    Match noetig - die Icons sind SVGs ohne Textinhalt, das hatte den ersten Versuch scheitern
    lassen). Bricht defensiv ab, wenn die Monatsueberschrift nicht erkannt wird, statt endlos
    zu klicken.
    """
    target = (target_year, target_month)
    heading = page.locator(".cx-calendar-date-select__date-label").first
    next_button = page.get_by_role("button", name="Monat Vor")
    prev_button = page.get_by_role("button", name="Monat Zurück")

    for _ in range(36):  # Sicherheitsgrenze: max. 3 Jahre Differenz, verhindert Endlosschleife
        try:
            heading_text = (heading.text_content(timeout=5000) or "").strip()
        except PlaywrightTimeoutError:
            _LOGGER.warning("Monats-Ueberschrift nicht gefunden, Navigation abgebrochen - "
                             "Export bezieht sich dann auf den zufaellig angezeigten Monat")
            return
        match = re.match(r"([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", heading_text)
        if not match:
            _LOGGER.warning("Monats-Ueberschrift nicht erkannt (%r), Navigation abgebrochen", heading_text)
            return
        current_month = _MONATE.get(match.group(1).lower())
        current_year = int(match.group(2))
        if current_month is None:
            _LOGGER.warning("Unbekannter Monatsname %r, Navigation abgebrochen", match.group(1))
            return
        current = (current_year, current_month)
        if current == target:
            return
        (next_button if current < target else prev_button).click()
        page.wait_for_timeout(500)
    _LOGGER.warning("Zielmonat nach 36 Klicks nicht erreicht, breche Navigation ab")


def _export_excel(page: Page, target_path: str) -> None:
    """Klickt den "Export"-Button der Kalenderansicht, waehlt "Excel" und speichert den
    Download unter target_path. Setzt voraus, dass die Kalenderansicht bereits den
    gewuenschten Monat zeigt.
    """
    page.get_by_role("button", name="Export").click()
    with page.expect_download(timeout=30000) as download_info:
        page.get_by_text("Excel", exact=False).click()
    download_info.value.save_as(target_path)


class VivendiLoginError(Exception):
    """Login ist fehlgeschlagen (falsche Zugangsdaten oder Portal nicht erreichbar)."""


class VivendiExportError(Exception):
    """Login war ok, aber der Export-Download ist fehlgeschlagen."""


def _login_and_open_calendar(page: Page, login_url: str, username: str, password: str) -> None:
    """Login + Navigation bis zur Kalenderansicht (Lesezeichen 'Dienstplan') - der Teil, der
    sowohl fuer den normalen Excel-Export als auch fuer den Tag-Klick-Detailabruf (siehe
    _login_and_open_calendar()-Aufrufer) identisch ist. Extrahiert 14.08.2026 aus
    download_dienstplan() heraus, um Code-Duplizierung zu vermeiden."""
    try:
        page.goto(login_url or LOGIN_URL, wait_until="networkidle", timeout=30000)
        _debug_shot(page, "01_login_page")

        page.get_by_label("Benutzer").fill(username)
        page.get_by_label("Kennwort").fill(password)

        with page.expect_response(
            lambda r: "/api/vivendi/v1/auth/login" in r.url, timeout=15000
        ) as response_info:
            page.get_by_role("button", name="Anmelden").click()
        response = response_info.value

        if response.status != 200:
            _debug_shot(page, "02_login_failed")
            raise VivendiLoginError(
                f"Login fehlgeschlagen (HTTP {response.status}). "
                "Zugangsdaten pruefen oder Portal-Struktur hat sich geaendert."
            )
        page.wait_for_load_state("networkidle", timeout=20000)
        _debug_shot(page, "02_nach_login")
    except PlaywrightTimeoutError as exc:
        _debug_shot(page, "02_login_timeout")
        raise VivendiLoginError(
            "Login-Formular oder Antwort nicht innerhalb des Zeitlimits gefunden - "
            "hat sich die Seitenstruktur geaendert?"
        ) from exc

    try:
        page.locator("button, [role=button]").filter(has_text="×").first.click(timeout=3000)
    except PlaywrightTimeoutError:
        pass

    page.get_by_text("Dienstplan", exact=True).first.click()
    page.wait_for_load_state("networkidle", timeout=20000)
    _debug_shot(page, "03_kalender", html=True)


def download_dienstplan(login_url: str, username: str, password: str, target_paths: list[str]) -> list[str]:
    """Loggt sich einmal ein und exportiert der Reihe nach len(target_paths) aufeinanderfolgende
    Monate, beginnend beim aktuellen Monat: target_paths[0] = aktueller Monat, target_paths[1]
    = Folgemonat, usw. Gibt target_paths unveraendert zurueck (Erfolgsbestaetigung).

    Hintergrund (vom Nutzer bestaetigt, 30.07.2026): Vivendi veroeffentlicht den Dienstplan
    fuer den Folgemonat jeweils am 15. oder 16. des Vormonats. Ab dem 17. soll der Folgemonat
    deshalb automatisch mit importiert werden (siehe main.py, NEXT_MONTH_AVAILABLE_FROM_DAY) -
    diese Funktion selbst weiss nichts von diesem Datum, sie exportiert einfach so viele
    aufeinanderfolgende Monate wie target_paths lang ist.

    Wirft VivendiLoginError bzw. VivendiExportError bei Fehlern.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        _login_and_open_calendar(page, login_url, username, password)

        try:
            # Export bezieht sich auf den angezeigten Monat, nicht automatisch auf den
            # aktuellen (siehe Modul-Docstring) - deshalb zuerst dorthin navigieren, dann fuer
            # jeden weiteren gewuenschten Monat einmal "Monat Vor" klicken und erneut
            # exportieren, alles in derselben Browser-Sitzung (kein erneuter Login noetig).
            today = _dt_date.today()
            year, month = today.year, today.month
            for i, target_path in enumerate(target_paths):
                if i == 0:
                    _go_to_month(page, year, month)
                else:
                    month += 1
                    if month == 13:
                        month = 1
                        year += 1
                    _go_to_month(page, year, month)
                _debug_shot(page, f"03b_monat_{i}")

                # Kalenderansicht hat oben links einen direkt beschrifteten "Export"-Button
                # (Dropdown-Menu-Trigger, kein Drei-Punkte-Menu, siehe Modul-Docstring). Ein
                # Klick darauf oeffnet das Menu mit den Export-Formaten.
                _export_excel(page, target_path)
                _debug_shot(page, f"06_download_fertig_{i}")
        except PlaywrightTimeoutError as exc:
            _debug_shot(page, "99_export_timeout")
            raise VivendiExportError(
                "Navigation zum Export oder Download nicht innerhalb des Zeitlimits "
                "abgeschlossen - vermutlich muessen die Selektoren fuer Export-Button/"
                "Excel-Menupunkt nachjustiert werden (DIENSTPLAN_SYNC_DEBUG=1 setzen und "
                "Screenshots unter /share/dienstplan_sync/debug/ pruefen)."
            ) from exc
        finally:
            browser.close()

    return target_paths


def resolve_unbekannte_codes(
    login_url: str, username: str, password: str, unresolved: list[tuple[_dt_date, str]],
) -> dict[str, str]:
    """Loest unbekannte Ist-Codes automatisch auf, indem der jeweilige Schicht-Chip im
    Kalender angeklickt wird - das oeffnet ein Detail-Panel mit der genauen Bezeichnung
    (14.08.2026, Rolands Wunsch: "auf den Tag klicken..., diesen dann uebernehmen und der
    Datenbank hinzufuegen").

    Struktur per Erkundungslauf ermittelt (zwei Runden mit Debug-Screenshots/HTML-Dumps,
    siehe project_vivendi_dienstplan_addon Memory): Tageszelle ist
    .dienstliste-monat__cell (Tageszahl in .dienstliste-monat__cell-date), jede Schicht
    darin ein eigener .dienst-icon-Chip. Klick darauf oeffnet ein Detail-Panel; die genaue
    Bezeichnung steht im <pep-dienst-name>-Element (bestaetigt live: Klick auf den "MB"-Chip
    vom 14.08.2026 zeigte korrekt "Mäeutische Besprechung"). Panel wird ueber den
    "Schließen"-Button (aria-label) wieder geschlossen, bevor der naechste Code drankommt.

    unresolved: Liste von (Datum, Ist-Code)-Paaren (typischerweise von main.py aus den beim
    Parsen uebersprungenen Zeilen zusammengestellt). Bei mehreren Vorkommen desselben Codes
    reicht ein Vorkommen - wird hier dedupliziert (erstes Datum pro Code gewinnt), nach Monat
    gruppiert, um Monatswechsel-Klicks zu minimieren.

    Gibt {ist_code: genaue_bezeichnung} fuer alle ERFOLGREICH aufgeloesten Codes zurueck -
    einzelne Fehlschlaege (Chip nicht gefunden, Panel-Struktur weicht ab, o.ae.) werden pro
    Code geloggt und einfach uebersprungen, brechen den Rest nicht ab (gleiches Muster wie
    beim requested_dates-Nachtrag im ZenWave-Sync-Add-on).
    """
    code_zu_datum: dict[str, _dt_date] = {}
    for datum, code in unresolved:
        code_zu_datum.setdefault(code, datum)

    ergebnis: dict[str, str] = {}
    if not code_zu_datum:
        return ergebnis

    # Nach Monat gruppieren, chronologisch abarbeiten - _go_to_month() navigiert dann nur
    # einmal pro Monatswechsel statt bei jedem einzelnen Code hin und her.
    nach_monat: dict[tuple[int, int], list[tuple[str, _dt_date]]] = {}
    for code, datum in code_zu_datum.items():
        nach_monat.setdefault((datum.year, datum.month), []).append((code, datum))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            _login_and_open_calendar(page, login_url, username, password)

            for (year, month), eintraege in sorted(nach_monat.items()):
                _go_to_month(page, year, month)
                for code, datum in eintraege:
                    try:
                        day_cell = page.locator(".dienstliste-monat__cell").filter(
                            has=page.locator(".dienstliste-monat__cell-date", has_text=re.compile(rf"^\s*{datum.day}\s*$"))
                        ).first
                        chip = day_cell.locator(
                            ".dienst-icon", has_text=re.compile(rf"^\s*{re.escape(code)}\s*$")
                        ).first
                        if chip.count() == 0:
                            _LOGGER.warning(
                                "Chip fuer Code %r am %s nicht gefunden - vermutlich hat sich "
                                "die Tagesansicht seit dem Excel-Export veraendert, ueberspringe",
                                code, datum,
                            )
                            continue
                        chip.click(timeout=5000)
                        name_locator = page.locator("pep-dienst-name").first
                        name_locator.wait_for(state="visible", timeout=8000)
                        bezeichnung = (name_locator.inner_text(timeout=3000) or "").strip()
                        _debug_shot(page, f"90_aufgeloest_{code}", html=True)
                        if bezeichnung:
                            ergebnis[code] = bezeichnung
                            _LOGGER.info("Code %r aufgeloest: %r (via %s)", code, bezeichnung, datum)
                        page.get_by_role("button", name="Schließen").click(timeout=5000)
                        page.wait_for_timeout(300)
                    except PlaywrightTimeoutError:
                        _LOGGER.warning(
                            "Aufloesung fuer Code %r am %s nicht innerhalb des Zeitlimits "
                            "abgeschlossen (Chip-Klick oder Detail-Panel) - ueberspringe",
                            code, datum,
                        )
                        _debug_shot(page, f"90_aufloesung_fehlgeschlagen_{code}", html=True)
                        # Versuchen, ein evtl. offen gebliebenes Panel zu schliessen, damit der
                        # naechste Code nicht sofort auch fehlschlaegt.
                        try:
                            page.get_by_role("button", name="Schließen").click(timeout=2000)
                        except PlaywrightTimeoutError:
                            pass
        finally:
            browser.close()
    return ergebnis
