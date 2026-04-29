# Strava Kudos Bot

Gibt automatisch Kudos auf alle neuen Activities im Strava-Friend-Feed.
Läuft als Cronjob auf dem Pi – kein Browser nötig, sobald der Session-Cookie einmal gesetzt ist.

## Wie es funktioniert

Strava blockiert automatische Logins mit reCAPTCHA. Der Bot umgeht das, indem er den
Browser-Session-Cookie (`_strava4_session`) aus einem echten Browser-Login übernimmt.
Dieser Cookie gilt mehrere Wochen. Sobald er abläuft, einmalig neu setzen (Schritt 3).

## Setup

### 1. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

### 2. .env befüllen

```bash
cp .env.example .env
nano .env
```

Eintragen:
```
STRAVA_EMAIL=deine@email.com
STRAVA_PASSWORD=deinPasswort
STRAVA_CLIENT_ID=...        # optional, für zukünftige API-Erweiterungen
STRAVA_CLIENT_SECRET=...    # optional
```

### 3. Einmalig: Session-Cookie setzen

1. Im Browser (Chrome/Firefox) bei **strava.com einloggen**
2. DevTools öffnen: `F12` (Windows/Linux) oder `Cmd+Option+I` (Mac)
3. Tab **"Application"** → links **"Cookies"** → **`https://www.strava.com`**
4. Eintrag **`_strava4_session`** suchen → den kompletten **Value** kopieren
5. Auf dem Pi ausführen:

```bash
/home/pi/Serbo_bot/.venv/bin/python kudos_bot.py --set-session WERT_HIER
```

Der Cookie wird in `session.json` gespeichert. Der Bot bestätigt ob er gültig ist.

### 4. Bot testen

```bash
/home/pi/Serbo_bot/.venv/bin/python kudos_bot.py
```

### 5. Cronjob einrichten (alle 30 Min)

```bash
crontab -e
```

```
*/30 * * * * cd /home/pi/Serbo_bot/strava_kudos && /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
```

## Wenn der Session-Cookie abläuft

Strava-Session-Cookies halten mehrere Wochen. Wenn der Bot meldet:

```
🔒 Session abgelaufen.
```

Einfach Schritt 3 wiederholen (neu einloggen, Cookie kopieren, `--set-session` ausführen).

## Dateien

| Datei | Beschreibung |
|---|---|
| `kudos_bot.py` | Hauptskript |
| `.env` | Credentials (nicht im Git) |
| `session.json` | Gespeicherter Session-Cookie (nicht im Git) |
| `tokens.json` | OAuth-Token (nicht im Git) |
| `kudos.log` | Log-Ausgabe |

## Hinweise

- Der Feed liefert bis zu 30 Activities (`FEED_LIMIT` in `kudos_bot.py` anpassbar)
- Bereits gekudoste und eigene Activities werden automatisch übersprungen
- Zwischen zwei Kudos wartet der Bot 2 Sekunden (`DELAY`) um Rate Limits zu vermeiden
- Strava erlaubt ~100 Requests / 15 Min und ~1000 / Tag
