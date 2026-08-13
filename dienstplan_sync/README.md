# Vivendi Dienstplan → ICS-Kalenderabo

Home-Assistant-Add-on, das taeglich automatisch den Vivendi-Dienstplan herunterlaedt und als
ICS-Kalenderfeed bereitstellt (`ics_export.py`), den beliebige Kalender-Apps (Google Calendar,
Alexa, ...) per "Von URL abonnieren" einbinden koennen.

**13.08.2026 (Roland-Wunsch, "das Ding brauchen wir nicht mehr"):** Die urspruengliche
direkte Google-Calendar-API-Anbindung (OAuth, Insert/Update/Delete-Sync in einen dedizierten
Kalender) wurde komplett entfernt. Grund: der Google-Cloud-Testzugang gab nur 7 Tage gueltige
Refresh-Tokens aus und lief staendig ab, waehrend das ICS-Kalenderabo (urspruenglich nur als
Backup gedacht) sich als der zuverlaessigere Weg erwiesen hat. Siehe
`project_vivendi_dienstplan_addon` Memory fuer die volle Historie.

## Status (Stand: 30.07.2026) — voll funktionsfaehig, Ende-zu-Ende live verifiziert

| Schritt | Status |
|---|---|
| 1. Add-on-Grundgerüst (config.yaml, Dockerfile, run.sh-Aequivalent) | ✅ fertig |
| 2. Vivendi-Portal-Login/Export-Mechanismus | ✅ live verifiziert (echter Login + Navigation) |
| 3. Download-Funktion | ✅ live verifiziert (echter Excel-Download) |
| 4. Excel-Parser | ✅ fertig, gegen echte Daten verifiziert |
| 5. Google-OAuth-Setup + Code | ✅ fertig, Ersteinrichtung durchgefuehrt |
| 6. Kalender-Suche/-Erstellung | ✅ fertig, Kalender "Dienstplan" live angelegt |
| 7. Sync-Logik (Insert/Update/Delete) | ✅ live verifiziert (18 Termine erfolgreich angelegt) |
| 8. Scheduler + Logging + Fehlerbenachrichtigung | ✅ fertig |
| 9. Lokal installieren/testen | ✅ als Add-on `101a1615_dienstplan_sync` installiert und laeuft |
| 10. Testlauf (Beispieldatei / echtes Portal) | ✅ kompletter Ende-zu-Ende-Lauf gegen das echte Portal erfolgreich |
| 11. README | ✅ dieser Stand |

**Weg dorthin (30.07.2026, drei echte Testlaeufe mit Debug-Screenshots noetig):** Die
urspruenglich aus dem Gedaechtnis beschriebene Klickfolge ("Kalender anzeigen" -> Drei-Punkte-
Menu -> Export -> Excel) passte nicht zur echten Oberflaeche. Tatsaechlicher Ablauf: Startseite
-> Lesezeichen "Dienstplan" -> Kalenderansicht -> zum aktuellen Monat navigieren (Export bezieht
sich auf den gerade angezeigten Monat, nicht automatisch den aktuellen!) -> direkt beschrifteter
"Export"-Button (kein Drei-Punkte-Menu) -> "Excel". Details und exakte Selektoren siehe
Docstring/Kommentare in `vivendi.py`.

### Login-Mechanismus (geloest und live verifiziert)

Das Formular schickt neben Username/Password ein drittes Pflichtfeld `PublicKey` an
`/api/vivendi/v1/auth/login` - vermutlich clientseitig per Web-Crypto-API erzeugt. Deshalb
**Playwright** (echter Headless-Chromium) statt reiner `requests`-Session: Das Formular wird
ganz normal ueber die sichtbaren Labels "Benutzer"/"Kennwort" befuellt und per "Anmelden"-
Button abgeschickt - die Seite generiert PublicKey usw. selbst. Mit echten Zugangsdaten
getestet (30.07.2026) - funktioniert zuverlaessig.

**Debug-Hilfe:** Add-on-Option `debug_screenshots: true` setzen - dann speichert `vivendi.py`
nach jedem Navigationsschritt einen Screenshot unter `/share/dienstplan_sync/debug/`, bei
Bedarf zusaetzlich ein HTML-Dump der Seite (siehe `_debug_shot(..., html=True)` in
`vivendi.py`) - war entscheidend, um die drei echten Selektor-Korrekturen (Lesezeichen statt
Menuepunkt, direkter Export-Button statt Drei-Punkte-Menu, echte aria-label fuer die Monats-
Navigation) schnell zu finden. Fuer den taeglichen Normalbetrieb kann die Option wieder auf
`false` gesetzt werden - sie kostet nur unnoetig Zeit/Speicherplatz, ist aber harmlos, wenn
sie an bleibt.

## Verzeichnisstruktur

```
dienstplan_sync/
  config.yaml              Add-on-Manifest (Optionen-Schema)
  Dockerfile
  requirements.txt
  app/
    main.py                Einstiegspunkt, Scheduler-Loop
    vivendi.py              Login + Export-Download (Playwright, live verifiziert 30.07.2026)
    parser.py               Excel-Parser
    calendar_sync.py        Event-ID/-Body-Hilfsfunktionen (kein Google-API-Teil mehr)
    ics_export.py           ICS-Kalenderfeed-Export (der eigentliche Sync-Weg)
    notify.py                HA-persistent_notification + Push bei Fehlern/Aenderungen
  config/
    kuerzel_mapping.yaml.example   Vorlage fuer die Schicht-Kuerzel-Zuordnung
```

## Kuerzel-Mapping anpassen

`config/kuerzel_mapping.yaml.example` nach `config/kuerzel_mapping.yaml` kopieren (ohne
`.example`) und anpassen. Details/Logik siehe Kommentare in der Datei selbst.

**Bereits vom Nutzer bestaetigt (30.07.2026):** `SF` = Sonderfunktion, `TB` = Teambesprechung,
`Ut` = Urlaubstag. `U2` und `kfE` sind auf ausdruecklichen Wunsch bewusst 1:1 mit dem Kuerzel
als Bezeichnung eingetragen (Bedeutung aktuell nicht bekannt) - bei Gelegenheit in der Datei
selbst durch die echte Bezeichnung ersetzbar, keine Code-Aenderung noetig.

## Installation als Add-on

Liegt als GitHub-Repository vor (`github.com/dumdidumger-commits/RB_dienstplan`), als
Custom-Repository "RB Dienstplan Sync" in Home Assistant eingetragen. Installation:
Einstellungen → Add-ons → Add-on Store → "Vivendi Dienstplan Sync" → Installieren. Nutzername/
Passwort/Sync-Optionen danach unter dem Reiter "Konfiguration" des Add-ons eintragen, dann
starten.

## Beispieldatei-Validierung und Ende-zu-Ende-Test (Schritt 4/10, abgeschlossen)

Der Parser wurde zunaechst manuell gegen eine Beispieldatei (August 2026, 39 Zeilen)
durchgerechnet - alle Spezialfaelle (Datum-Vererbung bei Doppeldiensten, `!`-Vermerk, `BB`,
`FZA So`, `/`) wurden Zeile fuer Zeile geprueft. Am 30.07.2026 folgte der echte Ende-zu-Ende-
Test gegen das produktive Vivendi-Portal mit echten Zugangsdaten: Login, Navigation, Export,
Download, Parsing (18 Schichten, keine unbekannten Kuerzel mehr) und Google-Calendar-Sync
liefen vollstaendig automatisch durch, 18 Termine wurden erfolgreich angelegt.
