"""Pure-logic tests for the tool modules (no network, no fixtures)."""
from types import SimpleNamespace

import pytest
from google.adk.tools import FunctionTool

from travel_planner import config
from travel_planner.tools import ALL_TOOLS
from travel_planner.tools import flights, hotels, serpapi, sights, transit, trip, wiki
from travel_planner.tools.geo import cluster_by_distance, haversine_km


# ---------------------------------------------------------------- declarations

@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.__name__)
def test_every_tool_has_a_clean_declaration(tool):
    decl = FunctionTool(tool)._get_declaration()
    schema = decl.parameters_json_schema or {}
    assert decl.description and len(decl.description) > 40
    assert "tool_context" not in schema.get("properties", {})
    for name in schema.get("properties", {}):
        assert name in decl.description, f"{tool.__name__}: parameter '{name}' is not documented in Args"


# ---------------------------------------------------------------- geo

LISBON = [
    {"name": "Belém Tower", "lat": 38.6916, "lon": -9.2160},
    {"name": "Jerónimos", "lat": 38.6979, "lon": -9.2063},
    {"name": "Discoveries Monument", "lat": 38.6936, "lon": -9.2056},
    {"name": "São Jorge Castle", "lat": 38.7139, "lon": -9.1334},
    {"name": "Lisbon Cathedral", "lat": 38.7100, "lon": -9.1335},
    {"name": "Alfama viewpoint", "lat": 38.7118, "lon": -9.1301},
    {"name": "Oceanário", "lat": 38.7635, "lon": -9.0937},
    {"name": "Vasco da Gama Tower", "lat": 38.7680, "lon": -9.0940},
    {"name": "No coords", "lat": None, "lon": None},
]


def test_haversine():
    assert haversine_km(60.1699, 24.9384, 59.4370, 24.7536) == pytest.approx(82, abs=2)  # Helsinki-Tallinn


def test_cluster_groups_neighbourhoods_and_reports_unplaced():
    r = cluster_by_distance(LISBON, 3)
    assert r["status"] == "ok" and r["count"] == 3 and r["unplaced"] == ["No coords"]
    groups = [{p["name"] for p in d["places"]} for d in r["results"]]
    assert {"Belém Tower", "Jerónimos", "Discoveries Monument"} in groups
    assert {"São Jorge Castle", "Lisbon Cathedral", "Alfama viewpoint"} in groups
    assert {"Oceanário", "Vasco da Gama Tower"} in groups
    assert all(d["walk_km"] < 3 for d in r["results"])


def test_cluster_keeps_neighbouring_outliers_together():
    """Regression: k-means with farthest-point seeds put Belém Tower and Jerónimos (1 km apart) on two days."""
    sights = [
        {"name": "São Jorge Castle", "lat": 38.7139, "lon": -9.1335}, {"name": "St Anthony", "lat": 38.7097, "lon": -9.1336},
        {"name": "MUDE", "lat": 38.7093, "lon": -9.1370}, {"name": "Rua Augusta Arch", "lat": 38.7083, "lon": -9.1366},
        {"name": "Santa Justa Lift", "lat": 38.7119, "lon": -9.1392}, {"name": "Chiado Museum", "lat": 38.7093, "lon": -9.1418},
        {"name": "Ancient Art Museum", "lat": 38.7048, "lon": -9.1609}, {"name": "Belém Tower", "lat": 38.6916, "lon": -9.2160},
        {"name": "Panteão", "lat": 38.7148, "lon": -9.1250}, {"name": "Jerónimos", "lat": 38.6978, "lon": -9.2061},
    ]
    for n in (3, 4, 5):
        groups = [{p["name"] for p in d["places"]} for d in cluster_by_distance(sights, n)["results"]]
        assert any({"Belém Tower", "Jerónimos"} <= g for g in groups), (n, groups)


def test_cluster_lists_food_nearby_each_day():
    food = [{"name": "Pastéis de Belém", "lat": 38.6975, "lon": -9.2033}, {"name": "Alfama tasca", "lat": 38.7110, "lon": -9.1310},
            {"name": "No coords", "lat": None, "lon": None}]
    days = cluster_by_distance(LISBON, 3, food_places=food)["results"]
    by_first = {d["places"][0]["name"]: d for d in days}
    belem = next(d for d in days if any(p["name"] == "Belém Tower" for p in d["places"]))
    alfama = next(d for d in days if any(p["name"] == "Lisbon Cathedral" for p in d["places"]))
    oceans = next(d for d in days if any(p["name"] == "Oceanário" for p in d["places"]))
    assert [f["name"] for f in belem["food_nearby"]] == ["Pastéis de Belém"]
    assert [f["name"] for f in alfama["food_nearby"]] == ["Alfama tasca"] and oceans["food_nearby"] == []
    assert by_first and "food_nearby" not in cluster_by_distance(LISBON, 3)["results"][0]


def test_cluster_more_days_than_places_and_errors():
    assert cluster_by_distance(LISBON[:2], 5)["count"] == 2
    assert cluster_by_distance(LISBON, 0)["status"] == "error"
    assert cluster_by_distance([{"name": "x"}], 2)["status"] == "error"


# ---------------------------------------------------------------- wikivoyage parsing

EAT_WIKITEXT = """==Eat==
Lisbon has great food.
===Budget===
* {{eat
| name=Tasca do Chico | url=http://tasca.example | lat=38.71 | long=-9.14
| hours=Daily 12:00-23:00 | price=€8-12
| content=Grilled sardines and {{w|Bacalhau|bacalhau}} dishes. See [[Lisbon/Alfama|Alfama]].
}}
===Splurge===
* {{eat
| name=Belcanto | lat= | long= | price=€200
| content=Two Michelin stars.
}}
"""


def test_parse_listings_tracks_budget_headings():
    places = wiki.parse_listings(EAT_WIKITEXT, "https://en.wikivoyage.org/wiki/Lisbon")
    assert [(p.name, p.budget_tier) for p in places] == [("Tasca do Chico", "low"), ("Belcanto", "high")]
    tasca = places[0]
    assert (tasca.lat, tasca.lon, tasca.hours, tasca.kind) == (38.71, -9.14, "Daily 12:00-23:00", "restaurant")
    assert "Bacalhau dishes. See Alfama." in tasca.summary and "Price: €8-12" in tasca.summary
    assert tasca.source_urls == ["https://en.wikivoyage.org/wiki/Lisbon", "http://tasca.example"]
    assert places[1].lat is None


def test_budget_from_price_when_no_headings():
    text = "==Eat==\n* {{eat | name=Leitaria | price=€15-30 | content=x }}\n* {{eat | name=Cheap | price=€5 }}"
    assert [(p.name, p.budget_tier) for p in wiki.parse_listings(text, "u")] == [("Leitaria", "mid"), ("Cheap", "low")]


def test_see_listing_ticket_price():
    text = "* {{see | name=Tower | price=€10 }}\n* {{see | name=Park | price=Free }}\n* {{see | name=Museum | price=€5-10 }}"
    assert [(p.name, p.ticket_eur) for p in wiki.parse_listings(text, "u")] == [("Tower", 10.0), ("Park", 0.0), ("Museum", None)]


def test_clean_wikitext_and_districts():
    raw = "A {{m2|140000}} hall, a {{m|28}} statue. [[File:X.jpg|thumb|cap [[link]]]] '''Bold''' <!-- c -->[[Almada#Q1|Cristo Rei]]"
    assert wiki.clean_wikitext(raw) == "A 140000 m² hall, a 28 m statue.  Bold Cristo Rei"
    assert wiki.district_pages("===[[Lisbon/Alfama|Alfama]]=== [[Lisbon/Belém]] [[Lisbon/Alfama]]", "Lisbon") == [
        "Lisbon/Alfama", "Lisbon/Belém"]


# ---------------------------------------------------------------- sights

def test_sights_kinds_and_indoor():
    assert sights.kinds_for(["history", "street food"]) == "historic"
    assert sights.kinds_for(["cooking"]) == sights.DEFAULT_KINDS
    assert sights.guess_indoor("historic,museums") is True
    assert sights.guess_indoor("historic,monuments") is False
    assert sights.guess_indoor("historic") is None


# ---------------------------------------------------------------- transit

def test_itinerary_mapping_picks_longest_leg_and_local_times():
    it = {
        "duration": 9600, "startTime": "2026-12-01T08:08:00Z", "endTime": "2026-12-01T10:48:00Z", "transfers": 2,
        "legs": [
            {"mode": "WALK", "duration": 240, "from": {"name": "START", "tz": "Europe/Helsinki"}, "to": {"name": "Simonkatu"}},
            {"mode": "TRAM", "duration": 600, "routeShortName": "7", "agencyName": "HSL",
             "from": {"name": "Simonkatu"}, "to": {"name": "West Harbour"}},
            {"mode": "FERRY", "duration": 7200, "agencyName": "Tallink", "agencyUrl": "https://tallink.example",
             "from": {"name": "Helsinki"}, "to": {"name": "Tallinn"}},
            {"mode": "WALK", "duration": 300, "from": {"name": "Tallinn"}, "to": {"name": "END", "tz": "Europe/Tallinn"}},
        ],
    }
    o = transit.itinerary_to_option(it)
    assert (o.mode, o.operator, o.url, o.stops, o.duration_min) == ("ferry", "Tallink", "https://tallink.example", 2, 160)
    assert (o.depart, o.arrive) == ("2026-12-01T10:08", "2026-12-01T12:48")  # UTC+2 in December
    assert o.price_eur is None and o.price_source is None
    assert o.details == "tram 7 (HSL): Simonkatu → West Harbour | ferry (Tallink): Helsinki → Tallinn"
    assert transit.itinerary_to_option({"legs": [{"mode": "WALK"}]}) is None


# ---------------------------------------------------------------- flights

def test_flight_conversion_and_consent_detection():
    f = {
        "type": "multi", "price": 412, "airlines": ["Finnair", "TAP"],
        "flights": [
            {"from_airport": {"code": "HEL", "name": ""}, "to_airport": {"code": "FRA", "name": ""},
             "departure": {"date": (2026, 12, 10), "time": (7, 5)}, "arrival": {"date": (2026, 12, 10), "time": (8, 50)},
             "duration": 165, "plane_type": ""},
            {"from_airport": {"code": "FRA", "name": ""}, "to_airport": {"code": "LIS", "name": ""},
             "departure": {"date": (2026, 12, 10), "time": (10, 0)}, "arrival": {"date": (2026, 12, 10), "time": (12, 5)},
             "duration": 185, "plane_type": ""},
        ],
    }
    o = flights.flight_to_option(f, "https://g.example")
    assert (o.depart, o.arrive, o.stops, o.duration_min, o.price_eur, o.price_source) == (
        "2026-12-10T07:05", "2026-12-10T12:05", 1, 350, 412.0, "live")
    assert o.details.startswith("HEL → FRA → LIS")
    assert flights.is_consent_page('<html><head><base href="https://consent.google.com/">')
    assert not flights.is_consent_page("<html><title>Google Flights</title>")


# ---------------------------------------------------------------- hotels

def test_cheapest_offer_and_description():
    hotel_rates = {"roomTypes": [
        {"offerRetailRate": {"amount": 500.0}, "rates": [{"name": "Big"}]},
        {"offerRetailRate": {"amount": 398.5}, "rates": [{
            "name": "Twin", "boardName": "Breakfast Included",
            "retailRate": {"taxesAndFees": [{"included": False, "description": "City Tax", "amount": 24}]},
            "cancellationPolicies": {"refundableTag": "NRFN"}}]},
    ]}
    total, rate = hotels.cheapest_offer(hotel_rates)
    assert total == 398.5
    desc = hotels.describe({"stars": 4, "rating": 9, "review_count": 120}, rate)
    assert desc == "4★, guest rating 9/10 (120 reviews), Twin, Breakfast Included, excl. City Tax €24, non-refundable"
    assert hotels.cheapest_offer({"roomTypes": []}) is None


def test_stay_deep_links():
    r = hotels.stay_deep_links("Lisbon", "2027-05-10", "2027-05-14", 2)
    urls = {x["site"]: x["url"] for x in r["results"]}
    assert urls["airbnb"] == "https://www.airbnb.com/s/Lisbon/homes?checkin=2027-05-10&checkout=2027-05-14&adults=2"
    assert "ss=Lisbon" in urls["booking.com"] and "group_adults=2" in urls["booking.com"]


# ---------------------------------------------------------------- trip (own code)

def test_save_trip_brief_writes_state():
    ctx = SimpleNamespace(state={})
    r = trip.save_trip_brief("Lisbon", "2027-05-10", "2027-05-14", ctx, interests=["history"], destination_lat=38.7)
    assert r["status"] == "ok"
    assert ctx.state["trip_brief"]["destination"] == "Lisbon"
    assert (ctx.state["trip_brief"]["nights"], ctx.state["trip_brief"]["days"]) == (4, 5)


def test_save_trip_brief_reports_bad_fields():
    ctx = SimpleNamespace(state={})
    r = trip.save_trip_brief("Lisbon", "2027-05-14", "2027-05-10", ctx, budget_tier="luxury")
    assert r["status"] == "error" and "budget_tier" in r["reason"] and "trip_brief" not in ctx.state


def test_calculate_trip_cost():
    r = trip.calculate_trip_cost(400, 600, 100, 5, 2, food_per_person_per_day_eur=40,
                                 local_transport_per_person_per_day_eur=6, total_budget_eur=2000)
    c = r["results"][0]
    assert c["categories_eur"] == {"transport": 400, "stay": 600, "food": 400, "activities": 100, "local_transport": 60}
    assert (c["total_eur"], c["per_person_eur"], c["per_person_per_day_eur"]) == (1560, 780, 156)
    assert c["within_budget"] is True and c["difference_eur"] == 440 and c["budget_used_pct"] == 78.0
    assert c["assumptions"] == []
    over = trip.calculate_trip_cost(400, 600, 100, 5, 2, food_per_person_per_day_eur=40,
                                    local_transport_per_person_per_day_eur=0, total_budget_eur=1000)["results"][0]
    assert over["within_budget"] is False and over["difference_eur"] == -500
    assert trip.calculate_trip_cost(1, 1, 1, 0, 1)["status"] == "error"
    assert trip.calculate_trip_cost(-1, 1, 1, 1, 1)["status"] == "error"


def test_calculate_trip_cost_uses_tier_assumptions():
    c = trip.calculate_trip_cost(100, 300, 0, 4, 2, budget_tier="low")["results"][0]
    assert c["categories_eur"]["food"] == 30 * 2 * 4 and c["categories_eur"]["local_transport"] == 5 * 2 * 4
    assert len(c["assumptions"]) == 2 and "low-budget" in c["assumptions"][0]
    assert c["within_budget"] is None
    unknown = trip.calculate_trip_cost(100, 300, 0, 1, 1, budget_tier="luxury")["results"][0]
    assert unknown["categories_eur"]["food"] == 50  # falls back to mid


def test_save_guide_writes_md_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    guide = "# Lisbon, 10–14 May\n\n| Day | Plan |\n| --- | --- |\n| 1 | Belém‑Tower & pastéis |\n"
    r = trip.save_guide(guide, "Lisbon 2027-05-10!")
    paths = r["results"][0]
    assert paths["markdown_path"].endswith("lisbon-2027-05-10.md")
    md_text = (tmp_path / "lisbon-2027-05-10.md").read_text(encoding="utf-8")
    html = (tmp_path / "lisbon-2027-05-10.html").read_text(encoding="utf-8")
    assert "OpenStreetMap" in md_text and "Transitous" in md_text  # attribution footer
    assert "<table>" in html and "<title>Lisbon, 10–14 May</title>" in html and "Belém‑Tower" in html
    assert trip.save_guide("  ", "x")["status"] == "error"


def test_save_guide_keeps_guide_in_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    ctx = SimpleNamespace(state={})
    trip.save_guide("# Tallinn\n\nDay 1", "tallinn-2026-12-01", ctx)
    assert ctx.state["final_guide"].startswith("# Tallinn") and "OpenStreetMap" in ctx.state["final_guide"]
    assert ctx.state["guide_paths"]["html"].endswith("tallinn-2026-12-01.html")


def test_slim_rates_keeps_cheapest_room_only():
    raw = {"sandbox": True, "hotels": [{"id": "h1", "name": "H", "latitude": 1, "longitude": 2, "hotelDescription": "x" * 5000}],
           "data": [{"hotelId": "h1", "roomTypes": [
               {"offerRetailRate": {"amount": 300}, "offerId": "A" * 2000, "rates": [{"name": "Big", "rateId": "B" * 900}]},
               {"offerRetailRate": {"amount": 200}, "offerId": "C" * 2000, "rates": [{"name": "Small", "boardName": "RO"}]},
           ]}]}
    slim = hotels.slim_rates(raw)
    assert len(str(slim)) < 600
    assert slim["hotels"][0]["name"] == "H" and "hotelDescription" not in slim["hotels"][0]
    total, rate = hotels.cheapest_offer(slim["data"][0])
    assert (total, rate["name"], rate["boardName"]) == (200, "Small", "RO")


def test_slim_plan_keeps_what_mapping_needs():
    raw = {"debugOutput": {"x": 1}, "itineraries": [{
        "duration": 600, "startTime": "2026-12-01T08:00:00Z", "endTime": "2026-12-01T08:10:00Z", "transfers": 0,
        "legs": [{"mode": "BUS", "duration": 600, "agencyName": "HSL", "legGeometry": {"points": "z" * 3000},
                  "intermediateStops": [{"name": "s"}] * 20,
                  "from": {"name": "A", "tz": "Europe/Helsinki", "stopId": "1"}, "to": {"name": "B", "tz": "Europe/Helsinki"}}]}]}
    slim = transit.slim_plan(raw)
    assert "debugOutput" not in slim and "legGeometry" not in slim["itineraries"][0]["legs"][0]
    assert transit.itinerary_to_option(slim["itineraries"][0]) == transit.itinerary_to_option(raw["itineraries"][0])


# ---------------------------------------------------------------- SerpApi

SERP_FLIGHTS = {
    "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?tfs=x"},
    "best_flights": [{
        "price": 357, "total_duration": 495, "booking_token": "T" * 500, "carbon_emissions": {"this_flight": 1},
        "layovers": [{"id": "RIX", "name": "Riga Airport", "duration": 160}],
        "flights": [
            {"departure_airport": {"id": "HEL", "time": "2027-05-10 09:15"}, "arrival_airport": {"id": "RIX", "time": "2027-05-10 10:10"},
             "duration": 55, "airline": "Air Baltic", "flight_number": "BT 302", "extensions": ["x"] * 5},
            {"departure_airport": {"id": "RIX", "time": "2027-05-10 12:50"}, "arrival_airport": {"id": "LIS", "time": "2027-05-10 15:30"},
             "duration": 280, "airline": "Air Baltic", "flight_number": "BT 675"},
        ]}],
    "other_flights": [{"price": 391, "total_duration": 290, "flights": [
        {"departure_airport": {"id": "HEL", "time": "2027-05-10 13:00"}, "arrival_airport": {"id": "LIS", "time": "2027-05-10 15:50"},
         "duration": 290, "airline": "Finnair", "flight_number": "AY 1739"}]}],
}


def test_serpapi_flight_slim_and_conversion():
    slim = serpapi.slim_flights(SERP_FLIGHTS)
    assert "booking_token" not in str(slim) and len(slim["flights"]) == 2
    o = serpapi.serp_flight_to_option(slim["flights"][0], slim["google_flights_url"], 2)
    assert (o.operator, o.depart, o.arrive, o.duration_min, o.stops, o.price_eur, o.price_source) == (
        "Air Baltic", "2027-05-10T09:15", "2027-05-10T15:30", 495, 1, 357.0, "live")
    assert o.details == ("BT 302 HEL 09:15→RIX 10:10 | BT 675 RIX 12:50→LIS 15:30 | layover RIX 2h40; "
                         "price is the total for 2 adults, ~€178 per person")
    assert serpapi.serp_flight_to_option(slim["flights"][1], "u", 1).details == (
        "AY 1739 HEL 13:00→LIS 15:50; price is the total for 1 adults")


def test_serpapi_hotel_slim_and_description():
    raw = {"search_metadata": {"google_hotels_url": "https://www.google.com/travel/search?q=Lisbon"},
           "ads": [{"name": "ad"}],
           "properties": [
               {"type": "hotel", "name": "Central House", "gps_coordinates": {"latitude": 38.71, "longitude": -9.14},
                "total_rate": {"extracted_lowest": 194, "extracted_before_taxes_fees": 153}, "extracted_hotel_class": 2,
                "overall_rating": 4.3, "reviews": 771, "location_rating": 4.8, "images": [{"x": "y" * 500}] * 5,
                "amenities": ["Free Wi-Fi", "Bar", "Air conditioning", "Breakfast ($)", "Spa", "Pool", "Gym"]},
               {"type": "hotel", "name": "Sold out", "gps_coordinates": {"latitude": 38.7, "longitude": -9.1}},
           ]}
    slim = serpapi.slim_hotels(raw)
    assert [p["name"] for p in slim["properties"]] == ["Central House"]  # unpriced dropped
    assert "images" not in slim["properties"][0] and "ads" not in slim and len(slim["properties"][0]["amenities"]) == 6
    assert serpapi.describe_property(slim["properties"][0]) == (
        "2★; Google rating 4.3/5 (771 reviews); location 4.8/5; €153 before taxes and fees; "
        "Free Wi-Fi, Bar, Air conditioning, Breakfast ($)")


async def test_serpapi_tools_need_a_key_and_valid_args(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    r = await serpapi.search_serpapi_flights("HEL", "LIS", "2027-05-10")
    assert r["status"] == "error" and "SERPAPI_KEY" in r["reason"]
    monkeypatch.setenv("SERPAPI_KEY", "dummy")
    r = await serpapi.search_google_hotels("Lisbon", "2027-05-10", "2027-05-14", sort_by="cheapest")
    assert r["status"] == "error" and "sort_by" in r["reason"]
    r = await serpapi.search_google_hotels("Lisbon", "2027-05-10", "2027-05-14", min_rating=4.2)
    assert r["status"] == "error" and "min_rating" in r["reason"]
    r = await serpapi.search_google_hotels("Lisbon", "2027-05-14", "2027-05-10")
    assert r["status"] == "error" and "checkout" in r["reason"]


async def test_serpapi_error_reply_becomes_tool_error(tmp_cache, mock_http, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "secret-key")
    mock_http.handler = lambda req: mock_http.json_response({"error": "Google hasn't returned any results for this query."})
    r = await serpapi.search_serpapi_flights("HEL", "XXX", "2027-05-10")
    assert r["status"] == "error" and "hasn't returned any results" in r["reason"]
    assert mock_http.calls[0].url.params["api_key"] == "secret-key"  # sent, but never cached (see test_http_cache)


# ---------------------------------------------------------------- dishes and sight hours

def test_italic_terms_skip_bold_and_duplicates():
    from travel_planner.tools.dishes import italic_terms
    text = "Try ''caldo verde'' and '''Bold Name''' or ''[[Bacalhau à Brás]]''. Again ''Caldo verde''. ''x''"
    assert italic_terms(text) == ["caldo verde", "Bacalhau à Brás"]


def test_dish_rows_and_query():
    from travel_planner.tools.dishes import dishes_query, rows_to_dishes
    data = {"results": {"bindings": [
        {"dish": {"value": "http://www.wikidata.org/entity/Q1"}, "name": {"value": "pastel de nata"},
         "description": {"value": "custard tart"}, "links": {"value": "40"}},
        {"dish": {"value": "http://www.wikidata.org/entity/Q2"}},  # no English label: skipped
    ]}}
    assert rows_to_dishes(data) == [{"name": "pastel de nata", "description": "custard tart", "image": None,
                                     "url": "https://www.wikidata.org/wiki/Q1", "popularity": 40}]
    assert '"Portugal"@en' in dishes_query('Portugal"') and "COUNTRY" not in dishes_query("Estonia")


def test_osm_sight_parsing_from_both_backends():
    from travel_planner.tools.sight_hours import charge_eur, element_to_place, sparql_rows_to_elements
    assert charge_eur("10 EUR") == 10.0 and charge_eur("€ 12,50") == 12.5
    assert charge_eur("5 EUR/adult;12 EUR/family") == 5.0 and charge_eur("varies") is None
    rows = {"results": {"bindings": [
        {"osm": {"value": "https://www.openstreetmap.org/node/7"}, "name": {"value": "Museu X"},
         "name_en": {"value": "Museum X"}, "tourism": {"value": "museum"}, "hours": {"value": "Tu-Su 10:00-18:00"},
         "fee": {"value": "yes"}, "charge": {"value": "8 EUR"}, "c": {"value": "POINT(-9.14 38.71)"},
         "wikipedia": {"value": "https://en.wikipedia.org/wiki/Museum%20X"}},
        {"osm": {"value": "https://www.openstreetmap.org/way/9"}, "name": {"value": "Miradouro"},
         "tourism": {"value": "viewpoint"}, "fee": {"value": "no"}, "c": {"value": "POINT(-9.13 38.72)"}},
    ]}}
    museum, view = [element_to_place(e) for e in sparql_rows_to_elements(rows)]
    assert (museum.name, museum.hours, museum.ticket_eur, museum.indoor, museum.lat) == (
        "Museum X", "Tu-Su 10:00-18:00", 8.0, True, 38.71)
    assert museum.source_urls == ["https://www.openstreetmap.org/node/7", "https://en.wikipedia.org/wiki/Museum_X"]
    assert (view.ticket_eur, view.indoor, view.summary) == (0.0, False, "viewpoint; free entry")
