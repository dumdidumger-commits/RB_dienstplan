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
- Navigation zum Export (vom Nutzer beschrieben, 29.07.2026 - noch nicht live verifiziert,
  da Chromium in der aktuellen Entwicklungsumgebung nicht ausfuehrbar ist, siehe README):
  nach dem Login mittig auf "Kalender anzeigen", dann oben rechts auf das Drei-Punkte-Menu,
  dort "Export" -> "Excel" auswaehlen. Die genauen Element-Selektoren fuer das
  Drei-Punkte-Menu und den Excel-Menuepunkt sind Bestwissen (ueblicherweise Material-Icon-
  Buttons ohne sichtbaren Text) - beim ersten echten Testlauf (Schritt 9/10) unbedingt mit
  PLAYWRIGHT_DEBUG_SCREENSHOTS=1 (siehe unten) gegenpruefen und ggf. anpassen.
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


def _debug_shot(page: Page, name: str) -> None:
    if os.environ.get("DIENSTPLAN_SYNC_DEBUG") != "1":
        return
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    path = f"{_DEBUG_DIR}/{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
        _LOGGER.debug("Debug-Screenshot gespeichert: %s", path)
    except Exception:  # noqa: BLE001 - Screenshot ist nur Debug-Hilfe, darf nie den Lauf abbrechen
        _LOGGER.exception("Debug-Screenshot fehlgeschlagen (%s)", name)


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

        try:
            # Schritte laut Nutzerbeschreibung (29.07.2026): "Kalender anzeigen" -> Drei-
            # Punkte-Menu oben rechts -> "Export" -> "Excel". Selektoren fuer Menu/Excel sind
            # Bestwissen, siehe Modul-Docstring - beim ersten Testlauf gegenpruefen.
            page.get_by_text("Kalender anzeigen").click()
            page.wait_for_load_state("networkidle", timeout=20000)
            _debug_shot(page, "03_kalender")

            # Drei-Punkte-Menu: meist ein Material-Icon-Button ohne sichtbaren Text -
            # ueblichste aria-label-Varianten der Reihe nach versuchen.
            more_button = page.get_by_role("button", name="Mehr").or_(
                page.get_by_role("button", name="Weitere Optionen")
            ).or_(
                page.get_by_role("button", name="Optionen")
            ).or_(
                page.locator("button:has(mat-icon:text('more_vert'))")
            )
            more_button.first.click()
            _debug_shot(page, "04_dreipunkte_menu")

            page.get_by_text("Export", exact=False).click()
            _debug_shot(page, "05_export_menu")

            with page.expect_download(timeout=30000) as download_info:
                page.get_by_text("Excel", exact=False).click()
            download = download_info.value
            download.save_as(target_path)
            _debug_shot(page, "06_download_fertig")
        except PlaywrightTimeoutError as exc:
            _debug_shot(page, "99_export_timeout")
            raise VivendiExportError(
                "Navigation zum Export oder Download nicht innerhalb des Zeitlimits "
                "abgeschlossen - vermutlich muessen die Selektoren fuer Drei-Punkte-Menu/"
                "Export/Excel nachjustiert werden (DIENSTPLAN_SYNC_DEBUG=1 setzen und "
                "Screenshots unter /share/dienstplan_sync/debug/ pruefen)."
            ) from exc
        finally:
            browser.close()

    return target_path
