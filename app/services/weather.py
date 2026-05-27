"""
weather.py — Live weather via Open-Meteo (free, no API key required).

Two calls:
  1. Geocoding API  — turn a place name into lat/lon + canonical name
  2. Forecast API   — current conditions + 3-day daily outlook

Returns a structured dict the weather node formats for the LLM. Never
raises — returns None on any failure so the caller can degrade
gracefully.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather-code → German label
_WMO: dict[int, str] = {
    0: "klar",
    1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt",
    45: "Nebel", 48: "gefrierender Nebel",
    51: "leichter Niesel", 53: "Niesel", 55: "starker Niesel",
    56: "gefrierender Niesel", 57: "starker gefrierender Niesel",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    66: "gefrierender Regen", 67: "starker gefrierender Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall",
    77: "Schneegriesel",
    80: "leichte Schauer", 81: "Schauer", 82: "heftige Schauer",
    85: "leichte Schneeschauer", 86: "starke Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel",
}


def wmo_label(code: int | None) -> str:
    if code is None:
        return "unbekannt"
    return _WMO.get(int(code), f"Code {code}")


async def _geocode(location: str) -> dict | None:
    params = {"name": location, "count": 1, "language": "de", "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(_GEOCODE_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning("weather: geocode failed for %r: %s", location, exc)
        return None
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    return {
        "name": top.get("name") or location,
        "country": top.get("country") or "",
        "latitude": top.get("latitude"),
        "longitude": top.get("longitude"),
        "timezone": top.get("timezone") or "auto",
    }


async def get_weather(location: str) -> dict | None:
    """Geocode `location`, fetch current + 3-day forecast.

    Returns:
      {
        "location": "Berlin", "country": "Deutschland",
        "current": {"temp", "feels_like", "humidity", "precip",
                    "wind", "code", "label"},
        "daily": [{"date", "code", "label", "tmax", "tmin", "precip_prob"}, ...],
      }
    or None on failure.
    """
    geo = await _geocode(location)
    if not geo or geo.get("latitude") is None:
        return None

    params = {
        "latitude": geo["latitude"],
        "longitude": geo["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "timezone": geo["timezone"],
        "forecast_days": 3,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(_FORECAST_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning("weather: forecast failed for %r: %s", location, exc)
        return None

    cur = data.get("current") or {}
    daily = data.get("daily") or {}

    daily_out = []
    dates = daily.get("time") or []
    for i, day in enumerate(dates):
        daily_out.append({
            "date": day,
            "code": (daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None,
            "label": wmo_label((daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None),
            "tmax": (daily.get("temperature_2m_max") or [None])[i] if i < len(daily.get("temperature_2m_max") or []) else None,
            "tmin": (daily.get("temperature_2m_min") or [None])[i] if i < len(daily.get("temperature_2m_min") or []) else None,
            "precip_prob": (daily.get("precipitation_probability_max") or [None])[i] if i < len(daily.get("precipitation_probability_max") or []) else None,
        })

    return {
        "location": geo["name"],
        "country": geo["country"],
        "current": {
            "temp": cur.get("temperature_2m"),
            "feels_like": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "precip": cur.get("precipitation"),
            "wind": cur.get("wind_speed_10m"),
            "code": cur.get("weather_code"),
            "label": wmo_label(cur.get("weather_code")),
        },
        "daily": daily_out,
    }


def format_weather_context(w: dict) -> str:
    """Compact text block fed to the LLM as ground-truth weather data."""
    loc = w["location"] + (f", {w['country']}" if w.get("country") else "")
    c = w["current"]
    lines = [
        f"Ort: {loc}",
        f"Jetzt: {c['label']}, {c['temp']}°C (gefühlt {c['feels_like']}°C), "
        f"Luftfeuchte {c['humidity']}%, Wind {c['wind']} km/h, "
        f"Niederschlag {c['precip']} mm",
    ]
    if w.get("daily"):
        lines.append("Vorhersage:")
        for d in w["daily"]:
            lines.append(
                f"  {d['date']}: {d['label']}, {d['tmin']}–{d['tmax']}°C, "
                f"Regenrisiko {d['precip_prob']}%"
            )
    return "\n".join(lines)
