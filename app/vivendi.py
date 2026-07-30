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
- WICHTIGER BEFUND (2. Testlauf, 30.07.2026): Der Export bezieht sich auf den GERADE
  ANGEZEIGTEN Monat der Kalenderansicht, nicht automatisch auf den aktuellen Monat. Beim
  Testlauf zeigte die Ansicht beim ersten Laden "Februar 2026" (vermutlich der zuletzt
  angesehene Monat des Vivendi-Accounts), 5 Monate in der Vergangenheit - der Sync fand
  dadurch 0 passende Schichten im Sync-Fenster. Deshalb navigiert der Code jetzt vor dem
  Export per Pfeil-Buttons zum aktuellen Monat (siehe _go_to_current_month unten) - diese
  Selektoren sind noch NICHT live verifiziert (naechster Testlauf zeigt es).
"""

from __future__ import annotations

import logging
import os

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


def _go_to_current_month(page: Page) -> None:
    """Navigiert die Kalenderansicht per Pfeil-Buttons zum aktuellen Monat.

    Noetig, weil der Export sich auf den GERADE ANGEZEIGTEN Monat bezieht, nicht automatisch
    auf den aktuellen (echt beobachtet 30.07.2026: Ansicht zeigte beim Laden "Februar 2026",
    5 Monate in der Vergangenheit, wodurch der Sync 0 Treffer im Sync-Fenster fand). Noch NICHT
    live verifiziert - mehrere Fallback-Selektoren fuer die Pfeil-Buttons, analog zum
    urspruenglichen (und dann korrigierten) Drei-Punkte-Menu-Versuch. Bricht defensiv ab,
    wenn die Monatsueberschrift nicht erkannt wird, statt endlos zu klicken.
    """
    import re
    from datetime import date as _date

    target = (date_today := _date.today()).year, date_today.month
    heading = page.locator("text=/^[A-Za-zäöüÄÖÜ]+ \\d{4}$/").first
    next_button = page.get_by_role("button", name=re.compile("nächst|weiter|next", re.I)).or_(
        page.locator("button:has(mat-icon:text('chevron_right'))")
    ).or_(
        page.locator("button:has(mat-icon:text('navigate_next'))")
    ).first
    prev_button = page.get_by_role("button", name=re.compile("vorherig|zurück|previous", re.I)).or_(
        page.locator("button:has(mat-icon:text('chevron_left'))")
    ).or_(
        page.locator("button:has(mat-icon:text('navigate_before'))")
    ).first

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
    _LOGGER.warning("Aktueller Monat nach 36 Klicks nicht erreicht, breche Navigation ab")


class VivendiLoginError(Exception):
    """Login ist fehlgeschlagen (falsche Zugangsdaten oder Portal nicht erreichbar)."""


class VivendiExportError(Exception):
    """Login war ok, aber der Export-Download ist fehlgeschlagen."""


def download_dienstplan(login_url: str, username: str, password: str, target_path: str) -> str:
    """Loggt sich ein, laedt den aktuellen Dienstplan-Export herunter und speichert ihn
    unter target_path. Gibt target_path zurueck.

    Wirft VivendiLoginError bzw. VivendiExportError bei Fehlern.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

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

        # "Browser is out-of-date"-Banner der Startseite schliessen, falls vorhanden (echter
        # Testlauf 30.07.2026 zeigte es zuverlaessig oben ueber der ganzen Seite an - koennte
        # spaetere Klicks ueberlagern). Rein defensiv, bricht bei Nichtvorhandensein nicht ab.
        try:
            page.locator("button, [role=button]").filter(has_text="×").first.click(timeout=3000)
        except PlaywrightTimeoutError:
            pass

        try:
            # KORRIGIERT (30.07.2026, nach echtem Testlauf mit Debug-Screenshots): Die vom
            # Nutzer aus dem Gedaechtnis beschriebenen Schritte ("Kalender anzeigen" -> Drei-
            # Punkte-Menu -> Export -> Excel) passten nicht zur echten Startseite - "Kalender
            # anzeigen" kommt dort gar nicht vor. Tatsaechlich vorhanden: ein Lesezeichen
            # "Dienstplan" im Bereich "Lesezeichen" auf der Startseite, das direkt zur
            # Dienstplan-Ansicht fuehrt (siehe Modul-Docstring, beide Testlaeufe 30.07.2026).
            page.get_by_text("Dienstplan", exact=True).first.click()
            page.wait_for_load_state("networkidle", timeout=20000)
            _debug_shot(page, "03_kalender", html=True)

            # Export bezieht sich auf den angezeigten Monat, nicht automatisch auf den
            # aktuellen (siehe Modul-Docstring) - deshalb zuerst dorthin navigieren.
            _go_to_current_month(page)
            _debug_shot(page, "03b_aktueller_monat")

            # Kalenderansicht hat oben links einen direkt beschrifteten "Export"-Button
            # (Dropdown-Menu-Trigger, kein Drei-Punkte-Menu, siehe Modul-Docstring). Ein Klick
            # darauf oeffnet das Menu mit den Export-Formaten.
            page.get_by_role("button", name="Export").click()
            _debug_shot(page, "04_export_menu")

            with page.expect_download(timeout=30000) as download_info:
                page.get_by_text("Excel", exact=False).click()
            download = download_info.value
            download.save_as(target_path)
            _debug_shot(page, "06_download_fertig")
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

    return target_path
