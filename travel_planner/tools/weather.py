"""Weather from Open-Meteo: forecast for the next ~2 weeks, otherwise the same dates last year."""
from datetime import date, timedelta

from .. import config
from ._http import get_json
from ._result import ToolError, ok, safe_tool

# Open-Meteo forecasts reach 15 days ahead, but the last one can come back empty (seen live for 1-3 December
# asked on 18 November): trips ending later use the typical weather instead.
FORECAST_DAYS = 14
ARCHIVE_LAG_DAYS = 5  # the archive API trails today by a few days
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"
RAINY_MM = 2.0

WMO = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def _today() -> date:  # patched in tests
    return config.today()


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


@safe_tool
async def get_forecast(lat: float, lon: float, start_date: str, end_date: str) -> dict:
    """Daily weather for the trip dates.

    If the trip is within ~2 weeks this is the real forecast. Otherwise it is the
    observed weather on the same dates last year, as a typical guide ("kind" says
    which). A day has "rainy": true when precipitation is at least 2 mm; plan
    indoor sights on rainy days. "weekday" (Mon..Sun) is the trip date's weekday,
    for checking opening hours.

    Args:
        lat: Latitude of the destination.
        lon: Longitude of the destination.
        start_date: First trip day, ISO "YYYY-MM-DD".
        end_date: Last trip day, ISO "YYYY-MM-DD".
    """
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if end < start:
        raise ToolError("end_date is before start_date")
    today = _today()
    params = {"latitude": round(lat, 3), "longitude": round(lon, 3), "timezone": "auto"}
    if today <= start and end <= today + timedelta(days=FORECAST_DAYS):
        kind, offset = "forecast", 0
        data = await get_json(
            "https://api.open-meteo.com/v1/forecast",
            namespace="open-meteo",
            max_age_s=6 * 3600,
            params={**params, "daily": DAILY + ",precipitation_probability_max",
                    "start_date": start.isoformat(), "end_date": end.isoformat()},
        )
    else:
        # The archive window starts on the same date in an earlier year and is exactly as long as the trip, so a
        # trip over 29 February still gets one weather day per trip day (day i of the window = trip day i).
        kind, offset = "typical (observed on the same dates in an earlier year, not a forecast)", -1
        while _shift_year(start, offset) + (end - start) >= today - timedelta(days=ARCHIVE_LAG_DAYS):
            offset -= 1
        archive_start = _shift_year(start, offset)
        data = await get_json(
            "https://archive-api.open-meteo.com/v1/archive",
            namespace="open-meteo",
            params={**params, "daily": DAILY,
                    "start_date": archive_start.isoformat(),
                    "end_date": (archive_start + (end - start)).isoformat()},
        )
    d = data["daily"]
    days = []
    for i, t in enumerate(d["time"]):
        precip = d["precipitation_sum"][i]
        code = d["weather_code"][i]
        trip_day = start + timedelta(days=i)
        day = {
            "date": trip_day.isoformat(),
            "weekday": trip_day.strftime("%a"),
            # no data is unknown weather, not a dry day ("clear (no data)" in a live plan)
            "weather": "unknown (no forecast data yet)" if code is None else WMO.get(code, f"code {code}"),
            "t_max_c": d["temperature_2m_max"][i],
            "t_min_c": d["temperature_2m_min"][i],
            "precip_mm": precip,
            "rainy": None if precip is None else precip >= RAINY_MM,
        }
        if "precipitation_probability_max" in d:
            day["precip_probability_pct"] = d["precipitation_probability_max"][i]
        days.append(day)
    return ok(days, kind=kind, attribution="Weather data by Open-Meteo.com (CC BY 4.0)")
