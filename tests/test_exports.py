"""REVIEW.md A7: the guide writer's calendar (.ics) and map (GeoJSON) files of the plan."""
import json
from types import SimpleNamespace

from tests.test_review import _state, fixed_plan
from travel_planner import config
from travel_planner.agents.guide_writer import build_guide_writer
from travel_planner.tools.exports import calendar_events, export_calendar, export_map_stops


def _ctx(state):
    return SimpleNamespace(state=state)


def test_calendar_has_one_event_per_timed_item_and_ends_at_the_next_item(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    state = {**_state(fixed_plan()), "guide_paths": {"markdown": str(tmp_path / "lisbon-2027-05-11-to-2027-05-13.md")}}
    result = export_calendar(_ctx(state))
    assert result["status"] == "ok"
    path = tmp_path / "lisbon-2027-05-11-to-2027-05-13.ics"
    assert result["results"][0]["calendar_path"] == str(path) and state["guide_paths"]["calendar"] == str(path)
    text = path.read_bytes().decode("utf-8")
    items = sum(len(d["items"]) for d in fixed_plan()["days"])
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == items == result["results"][0]["events"]
    assert "DTSTART:20270512T103000" in text and "SUMMARY:MUDE - Museu do Design" in text
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))
    plan = fixed_plan()
    plan["days"][0]["items"][2]["time"] = "12:45"  # lunch 45 minutes after the castle visit starts
    events = calendar_events(plan)
    day1, day2 = ([e for e in events if e["day"] == n] for n in (1, 2))
    assert day2[0]["end"].strftime("%H:%M") == "12:00"  # MUDE 10:30, a typical 90-minute visit
    assert day1[1]["end"] == day1[2]["start"]  # the castle visit ends when lunch starts
    assert all(e["end"] > e["start"] for e in events)


def test_calendar_escapes_text():
    plan = fixed_plan()
    plan["days"][0]["items"][1]["name"] = "Café; bar, and more"
    from travel_planner.tools.exports import calendar_text
    assert "SUMMARY:Café\\; bar\\, and more" in calendar_text(plan, {"destination": "Lisbon"}, "x")


def test_map_lists_the_stay_and_every_place_with_coordinates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    state = _state(fixed_plan())
    result = export_map_stops(_ctx(state))
    assert result["status"] == "ok"
    data = json.loads((tmp_path / "lisbon-2027-05-11-to-2027-05-13.geojson").read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection" and data["features"][0]["properties"]["kind"] == "stay"
    stops = data["features"][1:]
    assert [f["properties"]["order"] for f in stops] == list(range(1, len(stops) + 1))
    castle = [f for f in stops if f["properties"]["name"] == "São Jorge Castle"][0]
    assert castle["geometry"]["coordinates"] == [-9.1335, 38.7139]  # GeoJSON is lon, lat


def test_exports_refuse_without_a_plan():
    assert export_calendar(_ctx({"itinerary": None}))["status"] == "error"
    assert export_map_stops(_ctx({"itinerary": "not json"}))["status"] == "error"


def test_guide_writer_is_asked_to_export_after_saving():
    writer = build_guide_writer()
    assert {getattr(t, "__name__", "") for t in writer.tools} == {"save_guide", "export_calendar", "export_map_stops"}
    assert writer.instruction.index("save_guide(") < writer.instruction.index("export_calendar()")
