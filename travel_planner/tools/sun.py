"""Hours of daylight from the latitude and the date (solar declination; pure maths, no source needed).

A live Svalbard plan for January never said that the sun does not rise there then (polar night)."""
import math
from datetime import date, timedelta

SHORT_DAY_HOURS = 7.0


def daylight_hours(lat: float, day: date) -> float:
    """Hours between sunrise and sunset (0 in polar night, 24 under the midnight sun), with the usual -0.833° for
    refraction and the sun's disc. Accurate to a few minutes, which is all a travel tip needs."""
    n = day.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(math.radians(360 / 365 * (n - 81)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    if cos_w >= 1:
        return 0.0
    if cos_w <= -1:
        return 24.0
    return round(2 * math.degrees(math.acos(cos_w)) / 15, 1)


def daylight_note(lat, start: date, end: date) -> str | None:
    """One line for the guide when the light shapes the trip: polar night, midnight sun or short days."""
    if lat is None:
        return None
    hours = [daylight_hours(float(lat), start + timedelta(days=i)) for i in range((end - start).days + 1)]
    if max(hours) == 0:
        return ("Polar night: the sun does not rise on these dates, so there is only some twilight around midday. "
                "Plan outdoor sights for the brightest hours and bring lights.")
    if min(hours) == 24:
        return "Midnight sun: the sun does not set on these dates; bring a sleep mask."
    if min(hours) < SHORT_DAY_HOURS:
        low, high = round(min(hours)), round(max(hours))
        span = f"{low}" if low == high else f"{low}-{high}"
        return (f"Short days: only about {span} hours of daylight (sunrise to sunset) on these dates, so plan outdoor "
                "sights for the middle of the day.")
    return None
