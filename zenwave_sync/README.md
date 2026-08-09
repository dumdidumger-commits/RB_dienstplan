# ZenWave Sync

Home-Assistant-Add-on, das täglich die "Intervalldaten"-Karte im ZenWave-Kundenportal
(`https://zenwave.customerportal.energy/`, Betreiber Zendure) ausliest und als Sensoren nach
Home Assistant schreibt: `sensor.zenwave_real_verbrauch`, `sensor.zenwave_real_kosten`,
`sensor.zenwave_real_durchschnittspreis`.

## Warum

Die lokal berechneten Kostenwerte (Shelly Pro 3EM bzw. Poweropti × Preis) wichen an einem
Vergleichstag deutlich von der echten Abrechnung ab (siehe Memory
`project_shelly_vs_poweropti_miscalibration`, Nachtrag 09.08.2026: Poweropti −59 % gegenüber der
echten Abrechnung, vermutete Ursache verpasste Impulse bei niedriger Grundlast). Statt weiter
zwischen zwei unsicheren lokalen Messungen zu kalibrieren, holt dieses Add-on die tatsächlich
abgerechneten Werte direkt vom Anbieter.

## Status (Stand 09.08.2026, v0.1.4) — Login + Basis-Auslese live verifiziert

| Schritt | Status |
|---|---|
| 1. Add-on-Grundgerüst (config.yaml, Dockerfile, requirements.txt) | ✅ fertig |
| 2. Login-Mechanismus (zweistufig: E-Mail → Passwort, "powered by Nomos") | ✅ live verifiziert, 4 Iterationen bis stabil (siehe unten) |
| 3. "Intervalldaten"-Karte auslesen (Ø Preis/Verbrauch/Variable Kosten) | ✅ live verifiziert, liest den Default-Zeitraum (aktuell "gestern") |
| 4. Sensoren nach HA schreiben (`sensor.zenwave_real_*`) | ✅ live verifiziert |
| 5. Status Final/Vorläufig/Ersatzwert für einen ganzen Tag erkennen | ❌ noch nicht geklärt, wie das im DOM abgebildet ist - Attribut `status` liefert bisher immer `"unknown"` |
| 6. "Zeitraum"-Dropdown für beliebige vergangene Tage bedienen | ❌ noch nicht geklärt - liest bisher nur den beim Laden default gezeigten Zeitraum (aktuell "gestern") |
| 7. Anbindung an die Energieprognose-Tag-Sensoren (Offset ≤ -2 → echte statt geschätzte Werte) | ❌ noch offen |

**Weg dorthin (09.08.2026, vier echte Testläufe mit Debug-Screenshots nötig, wie schon bei
Vivendi):** Der ursprüngliche Entwurf nahm ein einstufiges Login mit direktem Passwortfeld an -
tatsächlich ist es zweistufig (erst E-Mail, dann erst erscheint das Passwortfeld, "powered by
Nomos"-Auth). Danach zwei Runden Timing-Probleme: `networkidle` als Erfolgs-/Fehler-Signal
erwies sich als unzuverlässig, weil die SPA vermutlich dauerhaft Hintergrundverbindungen offen
hält (Websocket-Heartbeat o.ä.) und "idle" dadurch entweder zu früh (mitten in der
Redirect-Animation) oder gar nicht auslöste. Fix: statt auf Abwesenheit des Passwortfelds oder
`networkidle` zu prüfen, wird jetzt direkt auf das nächste konkret erwartete Element gewartet
("Verbrauch"-Navigation nach Login, "INTERVALLDATEN"-Karte nach Tab-Wechsel).

`debug_screenshots: true` bleibt vorerst weiter an (siehe `config.yaml`) - kostet nur etwas
Speicherplatz, ist aber hilfreich für die noch offenen Punkte 5 und 6 oben.

## Bekannte Portal-Struktur (aus Screenshots, 09.08.2026)

- Next.js/React-SPA, kein Server-Login möglich, braucht Playwright (echter Headless-Browser)
- Zwei Tabs: "Verbrauch" und "Rechnungen" (Rechnungen-Tab noch nicht untersucht)
- Startseite hat eine eigene "Strompreis"-Karte (nicht genutzt von diesem Add-on, da uns primär
  die reale Abrechnung interessiert, nicht die Prognose)
- "Verbrauch"-Tab → "Intervalldaten"-Karte: Dropdown "Zeitraum: [Tag] ▾", darunter Ø Preis /
  Verbrauch / Variable Kosten für den gewählten Tag, 15-Minuten-Auflösung
- Legende mit drei Datenstatus: Final (grün) / Vorläufig (grau) / Ersatzwert (orange)

## Installation

Liegt im selben GitHub-Repository wie "Vivendi Dienstplan Sync"
(`github.com/dumdidumger-commits/RB_dienstplan`), als zweiter Add-on-Ordner. Installation:
Einstellungen → Add-ons → Add-on Store → Repository ggf. neu laden ("⋮" → Check for updates) →
"ZenWave Sync" → Installieren. Zugangsdaten unter dem Reiter "Konfiguration" eintragen, dann
starten.
