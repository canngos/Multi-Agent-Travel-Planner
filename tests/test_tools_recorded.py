"""One real call per data source.

`pytest -m live` calls the APIs and records responses into tests/fixtures/cache/.
`pytest` replays those recordings offline (a missing recording fails the test).

The arguments are pinned so the cache keys match between the two modes. To
re-record after the pinned dates have passed, move them forward here (TRIP_*),
run `pytest -m live`, then `pytest`.
"""
from datetime import date

import pytest

from travel_planner.tools import (
    find_restaurants, find_sights, geocode_place, get_forecast, plan_public_transport, search_google_flights,
    find_sight_hours, local_dishes, search_google_hotels, search_hotel_rates, search_serpapi_flights, web_search, wiki_summary, wikivoyage_section,
)
from travel_planner.tools import weather

recorded = pytest.mark.parametrize("mode", ["offline", pytest.param("live", marks=pytest.mark.live)])

LISBON = (38.7077, -9.1366)
BELEM = (38.6979, -9.2063)
TRIP_START, TRIP_END = "2027-05-10", "2027-05-14"  # demo trip dates
FERRY_DAY = "2026-12-01"  # Transitous needs a day inside the published timetables
FORECAST_START, FORECAST_END = "2026-11-19", "2026-11-21"
RECORDED_ON = date(2026, 11, 17)  # "today" for the weather tool when these were recorded


@pytest.fixture
def pinned_today(monkeypatch):
    monkeypatch.setattr(weather, "_today", lambda: RECORDED_ON)


@recorded
async def test_geocode(mode):
    r = await geocode_place("Lisbon")
    assert r["status"] == "ok"
    top = r["results"][0]
    assert top["country_code"] == "PT" and abs(top["lat"] - LISBON[0]) < 0.1


@recorded
async def test_forecast_near_dates(mode, pinned_today):
    r = await get_forecast(*LISBON, FORECAST_START, FORECAST_END)
    assert r["status"] == "ok" and r["kind"] == "forecast" and r["count"] == 3
    assert {"date", "weather", "t_max_c", "precip_mm", "rainy", "precip_probability_pct"} <= set(r["results"][0])


@recorded
async def test_forecast_far_dates_uses_last_year(mode, pinned_today):
    r = await get_forecast(*LISBON, TRIP_START, TRIP_END)
    assert r["status"] == "ok" and r["kind"].startswith("typical") and r["count"] == 5
    assert r["results"][0]["date"] == TRIP_START  # mapped back onto the trip dates


@recorded
async def test_wiki_summary_exact_and_search(mode):
    r = await wiki_summary("Belém Tower")
    assert r["status"] == "ok" and "16th-century" in r["results"][0]["extract"]
    r = await wiki_summary("Jeronimos monastery lisbon")
    assert r["status"] == "ok" and r["results"][0]["title"] == "Jerónimos Monastery"


@recorded
async def test_wikivoyage_district_listings(mode):
    r = await wikivoyage_section("Lisbon/Belém", "See")
    assert r["status"] == "ok" and r["count"] >= 5
    names = {p["name"] for p in r["results"]}
    assert any("Belém Tower" in n or "Torre de Belém" in n for n in names)
    assert any(p["lat"] for p in r["results"])


@recorded
async def test_wikivoyage_city_points_to_districts(mode):
    r = await wikivoyage_section("Lisbon", "See")
    assert r["status"] == "ok" and "Lisbon/Belém" in r["district_pages"] and r["text"]
    bad = await wikivoyage_section("Lisbon", "Nightlife")
    assert bad["status"] == "error" and "Sections:" in bad["reason"]


@recorded
async def test_find_sights(mode):
    r = await find_sights(*LISBON, ["history", "architecture"], 4, 10)
    assert r["status"] == "ok" and r["count"] == 10
    assert all(p["kind"] == "sight" and p["lat"] and p["source_urls"] for p in r["results"])


@recorded
async def test_web_search(mode):
    r = await web_search("Jerónimos Monastery ticket price", 3)
    assert r["status"] == "ok" and r["count"] >= 1
    assert all(x["url"].startswith("http") for x in r["results"]) and r["price_source_hint"] == "web_estimate"


@recorded
async def test_find_restaurants(mode):
    r = await find_restaurants(*BELEM, 600, "", 8)
    assert r["status"] == "ok" and r["count"] == 8
    assert all(p["kind"] in ("restaurant", "cafe", "bar") and p["source_urls"][0].startswith("https://www.openstreetmap.org/")
               for p in r["results"])


@recorded
async def test_ferry_helsinki_tallinn(mode):
    r = await plan_public_transport(60.1699, 24.9384, 59.4370, 24.7536, FERRY_DAY, "07:00", 3)
    assert r["status"] == "ok" and r["count"] >= 1
    assert any(o["mode"] == "ferry" for o in r["results"])
    assert all(o["price_eur"] is None for o in r["results"])  # schedules only


@recorded
async def test_hotel_rates(mode):
    r = await search_hotel_rates(38.7139, -9.1394, TRIP_START, TRIP_END, 2, 2.5, 8)
    assert r["status"] == "ok" and r["nights"] == 4 and r["count"] >= 3
    prices = [s["total_price_eur"] for s in r["results"]]
    assert prices == sorted(prices)
    for s in r["results"]:
        assert s["price_source"] == "live" and s["url"].startswith("https://www.booking.com/")
        assert s["price_per_night_eur"] == pytest.approx(s["total_price_eur"] / 4, abs=0.01)


@recorded
async def test_google_flights_fallback_always_gives_a_link(mode):
    """From EU networks Google shows a consent page; the tool must still return a usable link."""
    r = await search_google_flights("HEL", "LIS", TRIP_START, 2)
    assert r["status"] in ("ok", "error")
    assert r["search_url"].startswith("https://www.google.com/travel/flights?")


@recorded
async def test_serpapi_flights(mode):
    r = await search_serpapi_flights("HEL", "LIS", TRIP_START, 2)
    assert r["status"] == "ok" and r["count"] >= 3
    prices = [o["price_eur"] for o in r["results"]]
    assert prices == sorted(prices)
    assert all(o["price_source"] == "live" and o["depart"].startswith(TRIP_START) for o in r["results"])
    assert r["search_url"].startswith("https://www.google.com/travel/flights")


@recorded
async def test_serpapi_google_hotels(mode):
    r = await search_google_hotels("Lisbon", TRIP_START, TRIP_END, 2, "lowest_price", 4.0, 6)
    assert r["status"] == "ok" and r["nights"] == 4 and r["count"] >= 3
    for s in r["results"]:
        assert s["price_source"] == "live" and 4.0 <= s["rating"] <= 5
        assert s["price_per_night_eur"] == pytest.approx(s["total_price_eur"] / 4, abs=0.01)


@recorded
async def test_local_dishes(mode):
    r = await local_dishes("Portugal", 15)
    assert r["status"] == "ok" and r["count"] >= 10 and not r["problems"]
    names = {d["name"].lower() for d in r["results"]}
    assert "pastel de nata" in names
    assert "caldo verde" in [t.lower() for t in r["wikivoyage"]["dish_terms"]]


@recorded
async def test_find_sight_hours(mode):
    r = await find_sight_hours(*LISBON, 6, 30)
    assert r["status"] == "ok" and r["count"] >= 15
    with_hours = [p for p in r["results"] if p["hours"]]
    assert len(with_hours) >= 10 and any(p["ticket_eur"] for p in r["results"])
    assert all(p["source_urls"][0].startswith("https://www.openstreetmap.org/") for p in r["results"])
