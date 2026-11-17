"""MCP toolsets: offline tool lists, result normalising, cache callbacks; plus one recorded call per server."""
from types import SimpleNamespace

import pytest

from travel_planner.tools import _cache, mcp
from travel_planner.tools._cache import CacheMiss
from travel_planner.tools.mcp import airbnb_toolset, kiwi_toolset, mcp_cache_after, mcp_cache_before

recorded = pytest.mark.parametrize("mode", ["offline", pytest.param("live", marks=pytest.mark.live)])
LISBON_BRIEF = {"trip_brief": {"destination": "Lisbon", "destination_lat": 38.7077, "destination_lon": -9.1366}}

KIWI_SPECS = [
    {"name": "search-flight", "description": "Search for flights",
     "inputSchema": {"type": "object", "properties": {"flyFrom": {"type": "string"}}, "required": ["flyFrom"]}},
    {"name": "feedback-to-devs", "description": "Send feedback",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
]


def _seg(frm, to, dep, arr, carrier, number):
    return {"from": frm, "to": to, "departureTime": dep, "arrivalTime": arr, "carrierName": carrier, "flightNumber": number}


KIWI_RAW = {"isError": False, "content": [{"type": "text", "text": "{}"}], "structuredContent": {
    "currency": "EUR", "passengers": {"adults": 2},
    "itineraries": [
        {"price": 293.0, "bookingUrl": "https://kiwi.com/u/a", "totalDurationSeconds": 45600,
         "baggage": {"personalItem": 2, "cabinBag": 0, "checkedBag": 0},
         "outbound": {"departureTime": "2027-05-10T23:15:00", "arrivalTime": "2027-05-11T09:55:00", "durationSeconds": 45600,
                      "stops": 1, "route": ["HEL", "STN", "LIS"], "segments": [
                          _seg("HEL", "STN", "2027-05-10T23:15:00", "2027-05-11T00:10:00", "Ryanair", "FR646"),
                          _seg("LTN", "LIS", "2027-05-11T07:00:00", "2027-05-11T09:55:00", "easyJet", "U22459")]}},
        {"price": 411.0, "bookingUrl": "https://kiwi.com/u/b", "totalDurationSeconds": 17400,
         "baggage": {"personalItem": 2, "cabinBag": 2, "checkedBag": 0},
         "outbound": {"departureTime": "2027-05-10T13:00:00", "arrivalTime": "2027-05-10T15:50:00", "durationSeconds": 17400,
                      "stops": 0, "route": ["HEL", "LIS"], "segments": [
                          _seg("HEL", "LIS", "2027-05-10T13:00:00", "2027-05-10T15:50:00", "Finnair", "AY1739")]}},
    ]}}


def _listing(lid, name, lat, lon, total, rating="4.9 out of 5 average rating,  120 reviews"):
    return {"id": lid, "url": f"https://www.airbnb.com/rooms/{lid}", "badges": "Superhost",
            "demandStayListing": {"description": {"name": {"localizedStringWithTranslationPreference": name}},
                                  "location": {"coordinate": {"latitude": lat, "longitude": lon}}},
            "structuredContent": {"primaryLine": "1 bedroom"}, "avgRatingA11yLabel": rating,
            "structuredDisplayPrice": {"primaryLine": {"accessibilityLabel": f"€{total:,} total"},
                                       "explanationData": {"priceDetails": f"4 nights x €80.00: €320.00, Total: €{total:,}.00"}}}


AIRBNB_ARGS = {"location": "Lisbon", "checkin": "2027-05-10", "checkout": "2027-05-14", "adults": 2}
AIRBNB_RAW = {"isError": False, "content": [{"type": "text", "text": __import__("json").dumps({
    "searchUrl": "https://www.airbnb.com/s/Lisbon/homes",
    "searchResults": [
        _listing("1", "Alfama flat", 38.7115, -9.1300, 1388),
        _listing("2", "Country house", 39.20, -8.80, 300),  # ~55 km away
        _listing("3", "Baixa studio", 38.7100, -9.1390, 355, rating=""),
    ]})}]}


@pytest.fixture
def offline_tool(tmp_cache):
    """A real McpTool built from a cached tool list, without any server."""
    _cache.write("mcp_tools", {"server": "kiwi"}, KIWI_SPECS)

    async def get():
        return (await kiwi_toolset().get_tools())[0]
    return get


@pytest.fixture
def quiet_geocoder(monkeypatch):
    async def fake(lat, lon):
        return "Centro"
    monkeypatch.setattr(mcp, "neighbourhood", fake)


# ---------------------------------------------------------------- toolsets

async def test_offline_tool_list_comes_from_disk_and_is_filtered(offline_tool):
    tool = await offline_tool()
    assert tool.name == "search-flight"  # feedback-to-devs filtered out
    assert "flyFrom" in str(tool._get_declaration())
    assert tool.description == mcp.DESCRIPTIONS["search-flight"]  # our description replaces the server's


async def test_offline_tool_list_miss_raises(tmp_cache):
    with pytest.raises(CacheMiss):
        await airbnb_toolset().get_tools()


def test_airbnb_server_args(monkeypatch):
    monkeypatch.delenv("AIRBNB_IGNORE_ROBOTS_TXT", raising=False)
    assert mcp.airbnb_args()[-1] == "--ignore-robots-txt"
    monkeypatch.setenv("AIRBNB_IGNORE_ROBOTS_TXT", "0")
    assert mcp.airbnb_args() == ["-y", mcp.AIRBNB_PACKAGE]


# ---------------------------------------------------------------- normalising

async def test_kiwi_results_become_transport_options():
    r = await mcp.normalise("search-flight", {"adults": 2}, KIWI_RAW)
    assert r["status"] == "ok" and r["count"] == 2
    cheap, direct = r["results"]
    assert (cheap["operator"], cheap["depart"], cheap["arrive"], cheap["duration_min"], cheap["price_eur"]) == (
        "Ryanair, easyJet", "2027-05-10T23:15", "2027-05-11T09:55", 760, 293.0)
    assert "AIRPORT CHANGE during layover (STN→LTN)" in cheap["details"]
    assert "self-transfer" in cheap["details"] and "total for 2 adult(s)" in cheap["details"]
    assert direct["stops"] == 0 and "AIRPORT CHANGE" not in direct["details"] and "self-transfer" not in direct["details"]
    assert cheap["source"] == "kiwi" and cheap["url"] == "https://kiwi.com/u/a"


async def test_airbnb_results_become_stay_options_and_far_ones_are_dropped(quiet_geocoder):
    r = await mcp.normalise("airbnb_search", AIRBNB_ARGS, AIRBNB_RAW, centre=(38.7077, -9.1366))
    assert r["status"] == "ok" and r["dropped_far_away"] == 1 and r["nights"] == 4
    names = [s["name"] for s in r["results"]]
    assert names == ["Baixa studio", "Alfama flat"]  # cheapest first, country house dropped
    alfama = r["results"][1]
    assert (alfama["total_price_eur"], alfama["price_per_night_eur"], alfama["rating"], alfama["kind"]) == (1388.0, 347.0, 4.9, "airbnb")
    assert alfama["url"] == "https://www.airbnb.com/rooms/1?check_in=2027-05-10&check_out=2027-05-14&adults=2"
    assert "km from the centre" in alfama["details"] and r["results"][0]["rating"] is None


async def test_airbnb_without_centre_keeps_all_and_warns(quiet_geocoder):
    r = await mcp.normalise("airbnb_search", AIRBNB_ARGS, AIRBNB_RAW)
    assert r["count"] == 3 and "note" in r


@pytest.mark.parametrize("raw, reason", [
    ({"error": "Failed to connect"}, "Failed to connect"),  # ADK's own error dict
    ({"isError": True, "content": [{"type": "text", "text": "robots.txt disallows"}]}, "robots.txt"),
    ("not a dict", "Unexpected MCP result"),
])
async def test_errors_are_normalised(raw, reason):
    r = await mcp.normalise("search-flight", {}, raw)
    assert r["status"] == "error" and reason in r["reason"]


async def test_unknown_tools_pass_json_or_capped_text():
    r = await mcp.normalise("airbnb_listing_details", {}, {"content": [{"type": "text", "text": '{"listingUrl": "u"}'}]})
    assert r["results"] == [{"listingUrl": "u"}]
    r = await mcp.normalise("other", {}, {"content": [{"type": "text", "text": "x" * 9000}]})
    assert r["count"] == 0 and len(r["text"]) == mcp.MAX_TEXT_CHARS + 1


# ---------------------------------------------------------------- callbacks

async def test_callbacks_cache_and_replay(offline_tool, monkeypatch):
    tool = await offline_tool()
    ctx = SimpleNamespace(state={})
    args = {"flyFrom": "HEL", "flyTo": "LIS", "departureDate": "10/05/2027", "adults": 2}
    miss = await mcp_cache_before(tool, dict(args), ctx)
    assert miss["status"] == "error" and "OFFLINE" in miss["reason"]  # tests run with OFFLINE=1

    monkeypatch.setenv("OFFLINE", "0")
    assert await mcp_cache_before(tool, dict(args), ctx) is None  # online miss: let the tool run
    normalised = await mcp_cache_after(tool, dict(args), ctx, KIWI_RAW)
    assert normalised["status"] == "ok" and normalised["count"] == 2

    monkeypatch.setenv("OFFLINE", "1")
    reordered = dict(reversed(list(args.items())))  # argument order must not matter
    assert await mcp_cache_before(tool, reordered, ctx) == normalised
    # ADK also runs the after-callback on a cached answer: it must leave it unchanged
    assert await mcp_cache_after(tool, reordered, ctx, normalised) is None
    assert await mcp_cache_after(tool, args, ctx, miss) is None


async def test_callbacks_do_not_cache_errors(offline_tool, monkeypatch):
    tool = await offline_tool()
    monkeypatch.setenv("OFFLINE", "0")
    args = {"flyFrom": "HEL", "flyTo": "XXX", "departureDate": "10/05/2027"}
    r = await mcp_cache_after(tool, args, SimpleNamespace(state={}), {"isError": True, "content": []})
    assert r["status"] == "error"
    assert await mcp_cache_before(tool, args, SimpleNamespace(state={})) is None


async def test_callbacks_ignore_function_tools():
    fake = SimpleNamespace(name="geocode_place")
    assert await mcp_cache_before(fake, {}, None) is None
    assert await mcp_cache_after(fake, {}, None, {"x": 1}) is None


# ---------------------------------------------------------------- recorded (real servers)

async def _call(toolset, name: str, args: dict, state: dict) -> tuple[list[str], dict]:
    """Same path as inside an agent: before-callback, (server call), after-callback."""
    try:
        tools = {t.name: t for t in await toolset.get_tools()}
        tool, ctx = tools[name], SimpleNamespace(state=state)
        response = await mcp_cache_before(tool, args, ctx)
        if response is None:  # live run: call the server
            raw = await toolset._execute_with_session(lambda s: s.call_tool(name, args), "call failed", None, headers=None)
            response = await mcp_cache_after(tool, args, ctx, raw.model_dump(mode="json", by_alias=True, exclude_none=True))
        return sorted(tools), response
    finally:
        await toolset.close()


@recorded
async def test_kiwi_search_flight(mode):
    names, r = await _call(kiwi_toolset(), "search-flight",
                           {"flyFrom": "HEL", "flyTo": "LIS", "departureDate": "10/05/2027", "adults": 2}, LISBON_BRIEF)
    assert names == ["search-flight"]
    assert r["status"] == "ok" and r["count"] >= 3
    assert all(o["source"] == "kiwi" and o["price_source"] == "live" and o["url"].startswith("https://") for o in r["results"])
    assert all(o["depart"].startswith("2027-05-10") for o in r["results"])


@recorded
async def test_airbnb_search(mode):
    names, r = await _call(airbnb_toolset(), "airbnb_search",
                           {"location": "Lisbon, Portugal", "checkin": "2027-05-10", "checkout": "2027-05-14", "adults": 2},
                           LISBON_BRIEF)
    assert names == ["airbnb_listing_details", "airbnb_search"]
    assert r["status"] == "ok" and r["count"] >= 3 and r["nights"] == 4
    for s in r["results"]:
        assert s["kind"] == "airbnb" and s["url"].startswith("https://www.airbnb.com/rooms/")
        assert s["price_per_night_eur"] == pytest.approx(s["total_price_eur"] / 4, abs=0.01)
