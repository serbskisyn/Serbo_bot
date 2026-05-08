import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
_BERLIN = ZoneInfo("Europe/Berlin")


def _get_service(token_path: str):
    path = Path(token_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Token nicht gefunden: {path.name}\n"
            "Einmalig ausführen: python scripts/authorize_gcal.py --account 1"
        )
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
        logger.info("Google-Token erneuert: %s", path.name)
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)


def get_events(
    token_path: str,
    calendar_id: str = 'primary',
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    max_results: int = 25,
) -> list[dict]:
    svc = _get_service(token_path)
    now = datetime.now(timezone.utc)
    time_min = (start or now).isoformat()
    time_max = (end or now + timedelta(hours=24)).isoformat()
    result = svc.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=max_results,
        singleEvents=True,
        orderBy='startTime',
    ).execute()
    return result.get('items', [])


def format_event(event: dict) -> str:
    summary = event.get('summary', '(kein Titel)')
    start = event.get('start', {})
    end = event.get('end', {})

    if 'dateTime' in start:
        dt_start = datetime.fromisoformat(start['dateTime']).astimezone(_BERLIN)
        dt_end = datetime.fromisoformat(end['dateTime']).astimezone(_BERLIN)
        time_str = f"🕐 {dt_start.strftime('%H:%M')}–{dt_end.strftime('%H:%M')}"
    else:
        time_str = "🗓 ganztägig"

    location = event.get('location', '')
    loc_str = f"\n   📍 {location}" if location else ""

    return f"{time_str}  *{summary}*{loc_str}"


def get_event_start_utc(event: dict) -> Optional[datetime]:
    start = event.get('start', {})
    if 'dateTime' in start:
        return datetime.fromisoformat(start['dateTime']).astimezone(timezone.utc)
    return None
