# Strava Auto-Kudos Bot 👍

Gibt automatisch Kudos an alle Aktivitäten in deinem Strava-Feed.

## Methode
Playwright (headless Chromium) — kein API-Key nötig, funktioniert mit jedem Strava-Account.

---

## Setup (einmalig auf dem Pi)

### 1. Abhängigkeiten installieren
```bash
cd ~/Serbo_bot/strava_kudos
pip install -r requirements.txt
playwright install chromium
```

### 2. .env anlegen
```bash
cp .env.example .env
nano .env   # E-Mail und Passwort eintragen
```

### 3. Einmalig einloggen (sichtbarer Browser)
```bash
export $(cat .env | xargs)
python kudos_bot.py --login
```
Ein Browser öffnet sich, du loggst dich ein → Session wird gespeichert.

### 4. Test-Lauf
```bash
python kudos_bot.py
# oder mit sichtbarem Browser:
python kudos_bot.py --visible
```

---

## Automatisch alle 30 Minuten (Cronjob)

```bash
crontab -e
```

Zeile einfügen:
```
*/30 * * * * cd /home/pi/Serbo_bot/strava_kudos && source .env && /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
```

---

## Dateien

| Datei | Beschreibung |
|---|---|
| `session_state.json` | Strava-Session (automatisch erstellt, nicht einchecken!) |
| `kudosed.json` | Cache der bereits gekudosten Activity-IDs |
| `kudos.log` | Protokoll aller Läufe |

---

## Session abgelaufen?

```bash
export $(cat .env | xargs)
python kudos_bot.py --login
```

---

## Hinweis
Dieser Bot automatisiert Klicks im Strava-Webinterface.
Nutzung auf eigene Verantwortung. Keine Garantie bei UI-Änderungen durch Strava.
