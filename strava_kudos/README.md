# Strava Kudos Bot

Gibt automatisch Kudos auf alle Activities im Strava-Feed – via offizielle API.
Kein Browser, kein Playwright.

## Setup

### 1. Strava App erstellen
- https://www.strava.com/settings/api
- `Authorization Callback Domain` → `localhost`

### 2. Abhängigkeiten installieren
```bash
pip install -r requirements.txt
```

### 3. .env befüllen
```bash
cp .env.example .env
nano .env
# STRAVA_CLIENT_ID und STRAVA_CLIENT_SECRET eintragen
```

### 4. Einmalig authorisieren (öffnet Browser)
```bash
python kudos_bot.py --auth
```
→ Browser öffnet Strava-Login → Nach Login automatisch Token gespeichert in `tokens.json`

### 5. Bot starten
```bash
python kudos_bot.py
```

### 6. Cronjob einrichten (alle 30 Min)
```bash
crontab -e
```
```
*/30 * * * * cd /home/pi/Serbo_bot/strava_kudos && /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
```

## Dateien

| Datei | Beschreibung |
|---|---|
| `kudos_bot.py` | Hauptskript |
| `.env` | Deine API-Credentials (nicht ins Git!) |
| `tokens.json` | OAuth Token (wird automatisch erstellt) |
| `kudos.log` | Log-Ausgabe |

## Hinweise

- Token wird automatisch erneuert (Refresh Token)
- Rate Limit: Strava erlaubt 100 Requests/15 Min und 1000/Tag
- `FEED_LIMIT` in `kudos_bot.py` anpassbar (Standard: 30 Activities)
