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

## Status (Stand 09.08.2026) — erster Entwurf, noch UNGETESTET

| Schritt | Status |
|---|---|
| 1. Add-on-Grundgerüst (config.yaml, Dockerfile, requirements.txt) | ✅ fertig |
| 2. Login-Mechanismus | ⚠️ erster Versuch, noch nicht gegen die echte Seite getestet |
| 3. "Intervalldaten"-Karte auslesen (Ø Preis/Verbrauch/Variable Kosten) | ⚠️ erster Versuch |
| 4. Status Final/Vorläufig/Ersatzwert für einen ganzen Tag erkennen | ❌ noch nicht geklärt, wie das im DOM abgebildet ist |
| 5. "Zeitraum"-Dropdown für beliebige vergangene Tage bedienen | ❌ noch nicht geklärt - liest bisher nur den beim Laden default gezeigten Zeitraum |
| 6. Anbindung an die Energieprognose-Tag-Sensoren (Offset ≤ -2 → echte statt geschätzte Werte) | ❌ noch offen |

**Anders als beim Nachbar-Add-on "Vivendi Dienstplan Sync"** kennen wir bisher nur Screenshots
der bereits eingeloggten Seite, nicht das Login-Formular selbst. Die Login-Selektoren in
`app/zenwave.py` sind ein informierter erster Versuch (generische E-Mail-/Passwort-Feldtypen
statt hartcodierter CSS-Klassen). Erwartungsgemäß braucht es wie bei Vivendi mehrere echte
Testläufe mit `debug_screenshots: true` (Standard bei diesem Add-on vorerst AN, siehe
`config.yaml`), um die tatsächlichen Selektoren zu finden - Screenshots/HTML-Dumps landen unter
`/share/zenwave_sync/debug/`.

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
