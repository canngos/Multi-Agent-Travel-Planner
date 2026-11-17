import pytest
from pydantic import ValidationError

from travel_planner.models import Place, StayOption, TransportOption, TripBrief


def test_trip_brief_defaults_and_iso_dates():
    b = TripBrief(destination="Lisbon", start_date="2027-05-10", end_date="2027-05-14")
    assert (b.origin, b.travellers, b.budget_tier, b.interests) == ("Helsinki", 2, "mid", [])
    assert (b.nights, b.days) == (4, 5)
    dumped = b.model_dump(mode="json")
    assert dumped["start_date"] == "2027-05-10"  # round-trips as ISO strings for session state
    assert TripBrief.model_validate(dumped) == b


@pytest.mark.parametrize(
    "overrides",
    [
        {"end_date": "2027-05-10"},  # same day
        {"end_date": "2027-05-01"},  # before start
        {"start_date": "10/05/2027"},  # not ISO
        {"budget_tier": "luxury"},
        {"travellers": 0},
        {"destination": ""},
        {"destination_lat": 120},
        {"unknown_field": 1},  # extra fields rejected
    ],
)
def test_trip_brief_rejects_bad_input(overrides):
    data = {"destination": "Lisbon", "start_date": "2027-05-10", "end_date": "2027-05-14", **overrides}
    with pytest.raises(ValidationError):
        TripBrief(**data)


def test_transport_option_requires_price_source():
    base = dict(mode="flight", operator="TAP", depart="2027-05-10T07:00", arrive="2027-05-10T10:00",
                duration_min=300, source="kiwi", url="https://kiwi.com/x")
    with pytest.raises(ValidationError):  # price without a source
        TransportOption(**base, price_eur=189.5)
    assert TransportOption(**base).price_source is None  # schedule-only option (e.g. Transitous)
    t = TransportOption(**base, price_source="live", price_eur=189.5)
    assert t.stops == 0
    with pytest.raises(ValidationError):
        TransportOption(**base, price_source="guess")
    with pytest.raises(ValidationError):
        TransportOption(**{**base, "mode": "rocket"}, price_source="live")


def test_stay_option_and_place():
    s = StayOption(kind="hotel", name="H", area="Baixa", lat=38.71, lon=-9.14, price_per_night_eur=90,
                   total_price_eur=360, price_source="live", source="liteapi", url="https://x")
    assert s.rating is None
    with pytest.raises(ValidationError):
        StayOption(**{**s.model_dump(), "price_per_night_eur": -1})
    p = Place(kind="sight", name="Belém Tower", lat=38.6916, lon=-9.2160)
    assert p.source_urls == [] and p.indoor is None
    assert Place(kind="restaurant", name="No coords").lat is None
