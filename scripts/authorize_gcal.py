#!/usr/bin/env python3
"""
Einmaliger OAuth2-Autorisierungsflow für Google Calendar.

Voraussetzungen:
  1. Google Cloud Console → APIs & Services → OAuth2-Zugangsdaten
     → "Desktop-App" erstellen → JSON herunterladen → als gcal_oauth.json speichern
  2. Calendar API aktivieren (APIs & Services → Bibliothek → Google Calendar API)

Für Raspberry Pi (headless) — SSH-Portforwarding verwenden:
  Auf deinem lokalen Rechner:
    ssh -L 8888:localhost:8888 pi@<pi-ip>
  Dann dieses Skript auf dem Pi ausführen und den Link im Browser öffnen.

Aufruf:
  python scripts/authorize_gcal.py --account 1   # Gmail-Account
  python scripts/authorize_gcal.py --account 2   # Workspace-Account
"""
import argparse
import sys
from pathlib import Path

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CREDENTIALS_FILE = 'gcal_oauth.json'
PORT = 8888


def authorize(account: int) -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Fehler: google-auth-oauthlib nicht installiert.")
        print("Ausführen: pip install google-auth-oauthlib")
        sys.exit(1)

    creds_path = Path(CREDENTIALS_FILE)
    if not creds_path.exists():
        print(f"Fehler: {CREDENTIALS_FILE} nicht gefunden.")
        print(
            "Bitte OAuth2-Credentials in der Google Cloud Console erstellen:\n"
            "  APIs & Services → Zugangsdaten → OAuth-Client-ID → Desktop-App\n"
            f"  JSON als '{CREDENTIALS_FILE}' im Bot-Verzeichnis speichern."
        )
        sys.exit(1)

    token_file = f"gcal_token_{account}.json"

    print(f"\n=== Google Calendar Autorisierung — Account {account} ===")
    print(f"Token wird gespeichert in: {token_file}")
    print(f"\nWichtig für Raspberry Pi: SSH-Portforwarding aktiv?")
    print(f"  Auf deinem Rechner: ssh -L {PORT}:localhost:{PORT} pi@<pi-ip>")
    print(f"  Dann diesen URL im Browser öffnen wenn er erscheint.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)

    try:
        creds = flow.run_local_server(
            port=PORT,
            open_browser=False,
            access_type='offline',
            prompt='consent',
        )
    except Exception as e:
        print(f"\nFehler beim Autorisierungsflow: {e}")
        sys.exit(1)

    token_path = Path(token_file)
    token_path.write_text(creds.to_json())
    print(f"\n✅ Token gespeichert: {token_file}")
    print(f"\nJetzt in .env eintragen:")
    print(f"  GCAL_TOKEN_{account}={token_file}")


def main():
    parser = argparse.ArgumentParser(description='Google Calendar OAuth2 Setup')
    parser.add_argument('--account', type=int, choices=[1, 2], required=True,
                        help='Account-Nummer (1=Gmail, 2=Workspace)')
    args = parser.parse_args()
    authorize(args.account)


if __name__ == '__main__':
    main()
