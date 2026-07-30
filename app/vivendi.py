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
- Navigation zum Export: erster echter Testlauf (30.07.2026, mit Debug-Screenshots) zeigte,
  dass die urspruenglich vom Nutzer aus dem Gedaechtnis beschriebenen Schritte ("Kalender
  anzeigen" zuerst) nicht zur echten Startseite passten - dieser Text kommt dort gar nicht
  vor. Tatsaechlich fuehrt ein Lesezeichen "Dienstplan" im Bereich "Lesezeichen" der
  Startseite direkt weiter. Zusaetzlich zeigte die Startseite ein "Browser is out-of-date"-
  Banner, das vorsichtshalber weggeklickt wird. Der Rest der Kette (Drei-Punkte-Menu ->
  "Export" -> "Excel") ist weiterhin Bestwissen und noch nicht verifiziert - naechster
  Testlauf mit debug_screenshots muss das bestaetigen oder korrigieren.
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
            # "Dienstplan" im Bereich "Lesezeichen" auf der Startseite (Screenshot
            # 99_export_timeout.png), das direkt zur Dienstplan-Ansicht fuehrt. Der Rest der
            # Kette (Drei-Punkte-Menu -> Export -> Excel) ist noch unverifiziert - Bestwissen,
            # siehe Modul-Docstring, muss beim naechsten Testlauf gegengeprueft werden.
            page.get_by_text("Dienstplan", exact=True).first.click()
            page.wait_for_load_state("networkidle", timeout=20000)
            _debug_shot(page, "03_kalender")

            # KORRIGIERT (30.07.2026, zweiter Testlauf): kein Drei-Punkte-Menu vorhanden - die
            # Kalenderansicht hat oben links einen direkt beschrifteten "Export"-Button
            # (Dropdown-Menu-Trigger, Material-Style-Pfeil daneben), siehe Screenshot
            # 99_export_timeout.png dieses Laufs. Ein Klick direkt darauf oeffnet das Menu mit
            # den Export-Formaten.
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
