# Strava Kudos Bot

Gibt automatisch Kudos auf alle neuen Activities im Strava-Friend-Feed.
Läuft als Cronjob auf dem Raspberry Pi – kein Browser nötig, sobald der Session-Cookie einmal gesetzt ist.

---

## Wie der Strava-Login funktioniert (und warum es schwierig ist)

### Was wir versucht haben – und warum es nicht funktioniert hat

**Versuch 1 – Strava OAuth2 API**
Strava bietet eine offizielle REST-API mit OAuth2. Das Problem: die API hat
keinen Endpunkt für den Friend-Feed. `/api/v3/activities/following` wurde
2017 abgeschaltet. Club-Activities sind abrufbar, enthalten aber keine
Activity-IDs – Kudos damit nicht möglich.

**Versuch 2 – HTTP-Login mit `requests`**
Die Login-Seite (`/login`) liefert ein CSRF-Token per `<meta name="csrf-token">`.
Das Token wird mit dem POST nach `/session` geschickt (2-Step: erst E-Mail,
dann Passwort). Strava antwortete aber immer mit `403 {"success": false, "details": {}}` –
unabhängig vom Content-Type (form-encoded, multipart, JSON) oder HTTP-Version (HTTP/2).

**Versuch 3 – Playwright headless**
Playwright startet einen echten Chromium-Browser ohne sichtbares Fenster.
Nach dem Absenden der E-Mail-Adresse zeigte die Seite stets ein **reCAPTCHA**
statt des Passwortfeldes – Strava erkennt headless Browser zuverlässig.
Auch `playwright-stealth` (das `navigator.webdriver` versteckt) half nicht,
da reCAPTCHA weitere Signale auswertet (fehlende GPU-Daten, Timing, etc.).

### Was tatsächlich funktioniert

Der Strava-Webclient (React/Next.js SPA) authentifiziert sich über einen
**Session-Cookie** namens `_strava4_session`. Dieser Cookie wird beim normalen
Browser-Login gesetzt und enthält eine verschlüsselte Rails-Session.

Mit diesem Cookie kann der Bot:
1. Das Dashboard aufrufen und den **CSRF-Token** aus dem HTML lesen
2. Den **Friend-Feed** über `/dashboard/feed?feed_type=following` als JSON abrufen
3. Kudos per POST auf `/feed/activity/{id}/kudo` vergeben (+ CSRF-Token im Header)

Der Cookie hält mehrere Wochen. Sobald er abläuft, muss er einmalig erneuert werden.

> **Wichtig:** Strava hat im Laufe der Entwicklung mehrere Endpunkte geändert:
> - Kudos-URL war früher `/activities/{id}/kudos` → jetzt `/feed/activity/{id}/kudo`
> - Feed-Response war früher eine Liste → jetzt `{"entries": [...], "pagination": {}}`
> - Kudos-Status hieß früher `kudosed`/`has_kudoed` → jetzt `kudosAndComments.hasKudoed`

---

## Setup

### 1. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

### 2. .env anlegen

```bash
cp .env.example .env
nano .env
```

Minimale Konfiguration:
```
STRAVA_EMAIL=deine@email.com
STRAVA_PASSWORD=deinPasswort
```

Optional (für zukünftige API-Nutzung):
```
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
```

### 3. Einmalig: Session-Cookie aus dem Browser kopieren

1. Im Browser (Chrome/Firefox/Safari) bei **strava.com einloggen**
2. DevTools öffnen:
   - Mac: `Cmd + Option + I`
   - Windows/Linux: `F12`
3. Tab **"Application"** (Chrome) oder **"Storage"** (Firefox) öffnen
4. Links: **"Cookies"** → **`https://www.strava.com`** aufklappen
5. Eintrag **`_strava4_session`** suchen
6. Den kompletten **Value** (sehr langer String) kopieren
7. Auf dem Pi ausführen:

```bash
/home/pi/Serbo_bot/.venv/bin/python kudos_bot.py --set-session WERT_HIER_EINFÜGEN
```

Der Bot speichert den Cookie in `session.json` und prüft sofort ob er gültig ist.

### 4. Bot manuell testen

```bash
/home/pi/Serbo_bot/.venv/bin/python kudos_bot.py
```

Erwartete Ausgabe (wenn alles funktioniert):
```
2026-04-29 23:21:26 [INFO] Lade Friend Feed …
2026-04-29 23:21:28 [INFO] 30 Einträge im Feed.
2026-04-29 23:21:28 [INFO] Hole CSRF vom Dashboard …
2026-04-29 23:21:29 [INFO] Fertig. ✅ 3 Kudos | ⏭ 27 Skipped
```

### 5. Cronjob einrichten (alle 30 Minuten)

```bash
crontab -e
```

Folgende Zeile einfügen:
```
*/30 * * * * cd /home/pi/Serbo_bot/strava_kudos && /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
```

---

## Wenn der Session-Cookie abläuft

Der Bot gibt folgende Meldung aus:
```
🔒 Session abgelaufen.
Neu einloggen und Cookie aktualisieren:
  python kudos_bot.py --set-session <neuer_cookie_wert>
```

→ Einfach **Schritt 3** wiederholen: neu einloggen, Cookie kopieren, `--set-session` ausführen.

Alternativ kann der Cookie auch direkt in der `.env` gesetzt werden:
```
STRAVA_SESSION_COOKIE=dein_cookie_wert
```

---

## Dateien

| Datei | Beschreibung |
|---|---|
| `kudos_bot.py` | Hauptskript |
| `.env` | Credentials (nicht im Git) |
| `session.json` | Gespeicherter Session-Cookie (nicht im Git) |
| `tokens.json` | OAuth-Token aus früherer API-Phase (nicht im Git) |
| `kudos.log` | Log-Ausgabe des Bots |
| `requirements.txt` | Python-Abhängigkeiten |

---

## Technische Details

### Ablauf pro Cron-Lauf

```
1. session.json lesen → _strava4_session Cookie laden
2. GET /dashboard → prüfen ob Session gültig (kein Redirect auf /login)
3. GET /dashboard → CSRF-Token aus <meta name="csrf-token"> extrahieren
4. GET /dashboard/feed?feed_type=following&num_entries=30 → Friend-Feed als JSON
5. Für jeden Feed-Eintrag:
   - activity.id extrahieren
   - kudosAndComments.hasKudoed / canKudo prüfen → überspringen wenn schon gekudost
   - POST /feed/activity/{id}/kudo mit x-csrf-token Header → Kudos vergeben
   - 2 Sekunden warten
```

### Konfigurierbare Werte (in `kudos_bot.py`)

| Konstante | Standard | Beschreibung |
|---|---|---|
| `FEED_LIMIT` | `30` | Anzahl Feed-Einträge pro Lauf |
| `DELAY` | `2.0` | Sekunden zwischen zwei Kudos |

### Rate Limits

Strava erlaubt laut Dokumentation ~100 Requests / 15 Minuten und ~1000 / Tag.
Bei `FEED_LIMIT=30` und `DELAY=2s` macht ein Lauf maximal ~35 Requests in ~60 Sekunden –
weit unter dem Limit. Bei HTTP 429 stoppt der Bot automatisch.
