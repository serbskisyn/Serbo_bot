import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
_BERLIN = ZoneInfo("Europe/Berlin")

_service = None


def _get_service():
    global _service
    if _service:
        return _service

    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if json_str:
        info = json.loads(json_str)
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        # Fallback: credentials.json Datei
        creds_path = Path("credentials.json")
        if not creds_path.exists():
            raise FileNotFoundError(
                "GOOGLE_SERVICE_ACCOUNT_JSON nicht gesetzt und credentials.json nicht gefunden."
            )
        creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)

    _service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
    return _service


def get_events(
    calendar_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    max_results: int = 25,
) -> list[dict]:
    """Fetch events from a calendar shared with the service account."""
    svc = _get_service()
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
