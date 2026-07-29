# Vivendi Dienstplan → Google Calendar Sync

Home-Assistant-Add-on, das taeglich automatisch den Vivendi-Dienstplan herunterlaedt und mit
einem dedizierten Google-Kalender ("Dienstplan") abgleicht.

## Status (Stand: 29.07.2026)

| Schritt | Status |
|---|---|
| 1. Add-on-Grundgerüst (config.yaml, Dockerfile, run.sh-Aequivalent) | ✅ fertig |
| 2. Vivendi-Portal-Login/Export-Mechanismus ermitteln | ✅ Code steht komplett (Login + Navigation zu Kalender→Menu→Export→Excel), Selektoren fuer Drei-Punkte-Menu/Excel sind Bestwissen und brauchen einmal Live-Verifikation |
| 3. Download-Funktion | ✅ implementiert (`vivendi.download_dienstplan`), noch nicht live getestet (siehe Schritt 9/10) |
| 4. Excel-Parser | ✅ fertig, gegen echte Beispieldatei manuell durchgetestet (siehe unten) |
| 5. Google-OAuth-Setup + Code | ✅ Code fertig, Ersteinrichtung durch dich noch offen |
| 6. Kalender-Suche/-Erstellung | ✅ fertig |
| 7. Sync-Logik (Insert/Update/Delete) | ✅ fertig |
| 8. Scheduler + Logging + Fehlerbenachrichtigung | ✅ fertig |
| 9. Lokal installieren/testen | ⏳ offen - siehe "Installation" unten |
| 10. Testlauf (Beispieldatei / echtes Portal) | ⏳ Excel-Parser manuell verifiziert, echter End-to-End-Lauf steht noch aus |
| 11. README | 🔄 dieser Stand, wird bei jedem weiteren Schritt ergaenzt |

**Wichtigste offene Baustelle:** Schritt 2/3 (Vivendi-Portal), genauer: die Navigation nach
dem Login zum Dienstplan-Export. Der Login selbst ist geloest (siehe unten), aber welche
Seite/welcher Button nach dem Einloggen zum Excel-Export fuehrt, konnte ich nicht durch
Code-Analyse herausfinden - die entsprechenden Angular-Module laden erst nach echtem Login
nach. Das brauche ich noch von dir: entweder eine kurze Beschreibung der Klick-Schritte
(z.B. "nach Login -> Menuepunkt 'Dienstplan' -> Button 'Excel-Export'"), oder ich baue einen
Debug-Modus, der bei einem echten Testlauf nach jedem Schritt einen Screenshot speichert.

### Login-Mechanismus (geloest)

Das Formular schickt neben Username/Password ein drittes Pflichtfeld `PublicKey` an
`/api/vivendi/v1/auth/login` - vermutlich clientseitig per Web-Crypto-API erzeugt, aus dem
minifizierten Bundle nicht sicher rekonstruierbar. Deshalb jetzt **Playwright**
(echter Headless-Chromium) statt reiner `requests`-Session: Das Formular wird ganz normal
ueber die sichtbaren Labels "Benutzer"/"Kennwort" befuellt und per "Anmelden"-Button
abgeschickt - die Seite generiert PublicKey usw. selbst, wir muessen den Mechanismus nicht
verstehen. Kompletter Login-Endpunkt und Feldnamen wurden ausschliesslich durch sichere,
credential-lose Analyse ermittelt (leere Test-Anfrage, HTTP-Statuscodes) - es wurde zu
keinem Zeitpunkt ein echter Login-Versuch mit deinen Zugangsdaten durchgefuehrt, bevor der
Code dafuer stand.

**Debug-Hilfe fuer den ersten echten Testlauf:** Add-on-Option `debug_screenshots: true`
setzen - dann speichert `vivendi.py` nach jedem Navigationsschritt (Login, "Kalender
anzeigen", Drei-Punkte-Menu, Export-Menu, Download) einen Screenshot unter `/data/debug/`.
Damit laesst sich sofort sehen, an welcher Stelle die (noch unverifizierten) Selektoren
fuer Drei-Punkte-Menu/Excel-Auswahl ggf. angepasst werden muessen.

**Testen konnte ich das trotzdem noch nicht:** Die Entwicklungsumgebung, in der dieses
Add-on entsteht, verbietet aus Sicherheitsgruenden das Ausfuehren heruntergeladener
Programme (auch der Chromium-Browser, den Playwright braucht) - `EACCES`/Permission denied
beim Start, ganz bewusst so eingerichtet. Der Code ist nach bestem Wissen geschrieben, die
echte Verifikation kann erst in Schritt 9/10 (echter Add-on-Container) stattfinden.

## Verzeichnisstruktur

```
dienstplan_sync/
  config.yaml              Add-on-Manifest (Optionen-Schema)
  Dockerfile
  requirements.txt
  setup_oauth.py           Einmaliges LOKALES Setup-Skript (siehe unten) - NICHT im Container
  app/
    main.py                Einstiegspunkt, Scheduler-Loop
    vivendi.py              Login + Export-Download (Platzhalter, Schritt 2/3 offen)
    parser.py               Excel-Parser
    calendar_sync.py        Google-Calendar-Anbindung + Sync-Logik
    notify.py                HA-persistent_notification bei Fehlern
  config/
    kuerzel_mapping.yaml.example   Vorlage fuer die Schicht-Kuerzel-Zuordnung
```

## Google-OAuth-Ersteinrichtung (einmalig, auf DEINEM Computer, nicht auf HAOS)

**Warum lokal und nicht im Add-on?** Der interaktive Consent-Flow oeffnet einen Browser und
wartet auf einen Redirect an `http://localhost:<port>`. Wuerde das im Add-on-Container (auf
dem HAOS-Rechner) laufen, wuerde "localhost" aus Sicht deines Browsers (vermutlich ein
anderes Geraet) ins Leere zeigen. Deshalb: einmal lokal ausfuehren, das Ergebnis danach in
den Share-Ordner kopieren.

1. **Google Cloud Console** (https://console.cloud.google.com/):
   - Neues Projekt anlegen (oder ein bestehendes verwenden)
   - "APIs & Dienste" → "Bibliothek" → **Google Calendar API** aktivieren
   - "APIs & Dienste" → "Anmeldedaten" → "Anmeldedaten erstellen" → "OAuth-Client-ID"
   - Anwendungstyp: **Desktop-App**
   - JSON-Datei herunterladen, als `client_secret.json` speichern
2. Auf deinem eigenen Rechner (nicht HAOS):
   ```
   pip install google-auth-oauthlib
   ```
   `client_secret.json` neben `setup_oauth.py` legen, dann:
   ```
   python3 setup_oauth.py
   ```
   Browser oeffnet sich, Consent bestaetigen. Erzeugt `token.json`.
3. `token.json` nach `/share/dienstplan_sync/config/token.json` auf dem HAOS-Rechner kopieren
   (z.B. per Samba- oder File-Editor-Add-on).

Der laufende Add-on-Container liest danach nur noch diese Datei und erneuert den
Zugriffstoken selbststaendig ueber den enthaltenen Refresh-Token - kein Browser mehr noetig.

## Kuerzel-Mapping anpassen

`config/kuerzel_mapping.yaml.example` nach `config/kuerzel_mapping.yaml` kopieren (ohne
`.example`) und anpassen. Details/Logik siehe Kommentare in der Datei selbst.

**Zwei Kuerzel aus der Beispieldatei brauchen noch deine Bestaetigung:** `SF` und `TB`
tauchen als zweite Schicht an einzelnen Tagen auf, waren in der urspruenglichen
Spezifikation aber nicht beschrieben. Ich hab sie testweise mit einer Platzhalter-Bezeichnung
eingetragen ("SF (bitte pruefen/anpassen)" bzw. "TB (bitte pruefen/anpassen)") - bitte in der
Mapping-Datei durch die richtige Bezeichnung ersetzen.

## Installation als lokales Add-on (Schritt 9, noch nicht durchgefuehrt)

Dieser Ordner liegt aktuell unter `/share/dienstplan_sync` und ist von dort noch NICHT als
Add-on installierbar (Home Assistant sucht lokale Add-ons unter `/addons`, worauf dieser
Claude-Code-Container keinen Zugriff hat). Geplanter Weg: GitHub-Repository, das du als
Add-on-Repository in HA eintraegst - Details folgen, sobald das Repo eingerichtet ist.

## Beispieldatei-Validierung (Schritt 4/10, Teilstand)

Der Parser wurde manuell gegen die von dir bereitgestellte Beispieldatei (August 2026,
39 Zeilen) durchgerechnet - alle Faelle aus der Spezifikation (Datum-Vererbung bei
Doppeldiensten, `!`-Vermerk, `BB`, `FZA So`, `/`) kommen darin vor und wurden Zeile fuer
Zeile gegen die erwartete Logik geprueft. Eine echte automatisierte Testausfuehrung war in
der aktuellen Entwicklungsumgebung nicht moeglich (pandas/openpyxl liessen sich dort wegen
einer Sandbox-Einschraenkung nicht installieren) - das ist unabhaengig vom eigentlichen
Docker-Build und sollte dort kein Problem sein, wird aber in Schritt 9/10 nochmal real
verifiziert.
