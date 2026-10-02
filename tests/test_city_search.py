import pytest
import geopandas as gpd
from shapely.geometry import Point, Polygon

import app


def test_resolve_place_map_location_returns_state_bounds(monkeypatch):
    calls = []
    florida = Polygon([(-87.6, 24.3), (-80.0, 24.3), (-80.0, 31.0), (-87.6, 31.0)])

    def geocode_to_gdf(query):
        calls.append(query)
        return gpd.GeoDataFrame(geometry=[florida], crs="EPSG:4326")

    monkeypatch.setattr(app.ox, "geocode_to_gdf", geocode_to_gdf)

    center, bounds, boundary = app.resolve_place_map_location("  Florida, USA  ")

    assert center == (27.65, -83.8)
    assert bounds == (-87.6, 24.3, -80.0, 31.0)
    assert boundary == florida.__geo_interface__
    assert calls == ["Florida, USA"]


def test_resolve_place_map_location_returns_city_bounds(monkeypatch):
    paris = Polygon([(2.2, 48.8), (2.4, 48.8), (2.4, 48.9), (2.2, 48.9)])
    monkeypatch.setattr(
        app.ox,
        "geocode_to_gdf",
        lambda _query: gpd.GeoDataFrame(geometry=[paris], crs="EPSG:4326"),
    )

    center, bounds, boundary = app.resolve_place_map_location("Paris, France")

    assert center == pytest.approx((48.85, 2.3))
    assert bounds == (2.2, 48.8, 2.4, 48.9)
    assert boundary == paris.__geo_interface__


def test_resolve_place_map_location_uses_point_fallback(monkeypatch):
    point = Point(-73.9855, 40.758)
    monkeypatch.setattr(
        app.ox,
        "geocode_to_gdf",
        lambda _query: gpd.GeoDataFrame(geometry=[point], crs="EPSG:4326"),
    )

    center, bounds, boundary = app.resolve_place_map_location("Times Square, New York, USA")

    assert center == (40.758, -73.9855)
    assert bounds is None
    assert boundary is None


def test_resolve_place_map_location_falls_back_when_no_area_is_available(monkeypatch):
    monkeypatch.setattr(app.ox, "geocode_to_gdf", lambda _query: (_ for _ in ()).throw(ValueError("no boundary")))
    monkeypatch.setattr(app.ox, "geocode", lambda _query: (25.7617, -80.1918))

    center, bounds, boundary = app.resolve_place_map_location("Miami, USA")

    assert center == (25.7617, -80.1918)
    assert bounds is None
    assert boundary is None


def test_resolve_place_map_location_requires_query():
    with pytest.raises(ValueError, match="Enter a place to search"):
        app.resolve_place_map_location("  ")


def test_resolve_place_map_location_reports_unresolved_place(monkeypatch):
    def fail(_query):
        raise LookupError("not found")

    monkeypatch.setattr(app.ox, "geocode_to_gdf", fail)
    monkeypatch.setattr(app.ox, "geocode", fail)

    with pytest.raises(ValueError, match="Could not find 'Atlantis, Ocean'"):
        app.resolve_place_map_location("Atlantis, Ocean")


def test_update_city_map_state_changes_only_map_state(monkeypatch):
    route = object()
    route_graph = object()
    session_state = {
        "city": "Old route city",
        "city-input": "Florida, USA",
        "route": route,
        "route_graph": route_graph,
        "map_revision": 3,
    }
    monkeypatch.setattr(
        app,
        "resolve_place_map_location",
        lambda query: (
            (27.65, -83.8),
            (-87.6, 24.3, -80.0, 31.0),
            {"type": "Polygon", "coordinates": []},
        ),
    )

    app.update_city_map_state(session_state, "Florida, USA")

    assert session_state["map_center"] == (27.65, -83.8)
    assert session_state["map_bounds"] == (-87.6, 24.3, -80.0, 31.0)
    assert session_state["map_boundary"] == {"type": "Polygon", "coordinates": []}
    assert session_state["map_view"] == "city"
    assert session_state["map_fit_pending"] is True
    assert session_state["map_revision"] == 4
    assert session_state["map_search_error"] is None
    assert session_state["city"] == "Old route city"
    assert session_state["city-input"] == "Florida, USA"
    assert session_state["route"] is route
    assert session_state["route_graph"] is route_graph


def test_update_city_map_state_skips_lookup_for_empty_query(monkeypatch):
    session_state = {"map_center": (1.0, 2.0), "map_revision": 1, "map_search_error": "old error"}

    def fail_if_called(_query):
        raise AssertionError("empty input should not geocode")

    monkeypatch.setattr(app, "resolve_place_map_location", fail_if_called)

    app.update_city_map_state(session_state, "  ")

    assert session_state["map_center"] == (1.0, 2.0)
    assert session_state["map_revision"] == 1
    assert session_state["map_search_error"] is None


def test_update_city_map_state_preserves_map_on_lookup_error(monkeypatch):
    session_state = {
        "map_center": (1.0, 2.0),
        "map_bounds": (0.0, 0.0, 3.0, 4.0),
        "map_view": "city",
        "map_revision": 1,
    }

    def fail(_query):
        raise ValueError("place not found")

    monkeypatch.setattr(app, "resolve_place_map_location", fail)

    app.update_city_map_state(session_state, "Unknown place")

    assert session_state["map_center"] == (1.0, 2.0)
    assert session_state["map_bounds"] == (0.0, 0.0, 3.0, 4.0)
    assert session_state["map_view"] == "city"
    assert session_state["map_revision"] == 1
    assert session_state["map_search_error"] == "place not found"


def test_update_map_camera_state_saves_viewport_center_and_zoom():
    session_state = {}

    updated = app.update_map_camera_state(
        session_state,
        {
            "bounds": {
                "_southWest": {"lat": 40.6, "lng": -74.1},
                "_northEast": {"lat": 40.8, "lng": -73.9},
            },
            "center": {"lat": 40.7128, "lng": -74.006},
            "zoom": 13.5,
        },
    )

    assert updated is True
    assert session_state["map-camera-center"] == pytest.approx((40.7128, -74.006))
    assert session_state["map-camera-zoom"] == 13.5


def test_update_map_camera_state_falls_back_to_bounds_center():
    session_state = {}

    updated = app.update_map_camera_state(
        session_state,
        {
            "bounds": {
                "_southWest": {"lat": 40.6, "lng": -74.1},
                "_northEast": {"lat": 40.8, "lng": -73.9},
            },
            "zoom": 13,
        },
    )

    assert updated is True
    assert session_state["map-camera-center"] == pytest.approx((40.7, -74.0))
    assert session_state["map-camera-zoom"] == 13


def test_update_map_camera_state_ignores_missing_bounds():
    session_state = {"map-camera-center": (1.0, 2.0), "map-camera-zoom": 8}

    updated = app.update_map_camera_state(session_state, {"zoom": 14})

    assert updated is False
    assert session_state == {"map-camera-center": (1.0, 2.0), "map-camera-zoom": 8}


def test_apply_map_point_selection_updates_selected_endpoint():
    session_state = {"map-point-target": "Finish", "map_revision": 2}

    changed = app.apply_map_point_selection(
        session_state,
        {"lat": 40.123456789, "lng": -73.987654321},
        "map-city-0",
    )

    assert changed is True
    assert session_state["pending-map-point-selection"] == {
        "endpoint_key": "end-input",
        "value": "40.1234568,-73.9876543",
    }
    assert session_state["finish-point"] == (40.123456789, -73.987654321)
    assert session_state["focus-endpoint-after-map-click"] == "Finish"
    assert session_state["focus-value-after-map-click"] == "40.1234568,-73.9876543"
    assert session_state["map_revision"] == 2
    assert "start-input" not in session_state


def test_apply_map_point_selection_does_not_replay_click_after_endpoint_focus():
    session_state = {"start-input": "Times Square"}
    clicked = {"lat": 40.7, "lng": -74.0}

    assert app.apply_map_point_selection(session_state, clicked, "map-city-0") is False
    session_state["map-point-target"] = "Start"

    assert app.apply_map_point_selection(session_state, clicked, "map-city-0") is False
    assert session_state["start-input"] == "Times Square"
    assert "pending-map-point-selection" not in session_state
    assert "start-point" not in session_state


def test_pending_map_selections_preserve_start_when_selecting_finish():
    session_state = {"map-point-target": "Start", "start-input": "Times Square"}

    assert app.apply_map_point_selection(session_state, {"lat": 40.7, "lng": -74.0}, "map-city-0")
    assert app.apply_pending_map_point_selection(session_state) is True
    start_value = session_state["start-input"]

    session_state["map-point-target"] = "Finish"
    assert app.apply_map_point_selection(session_state, {"lat": 40.8, "lng": -73.9}, "map-city-0")
    assert app.apply_pending_map_point_selection(session_state) is True

    assert session_state["start-input"] == start_value == "40.7000000,-74.0000000"
    assert session_state["end-input"] == "40.8000000,-73.9000000"
    assert "pending-map-point-selection" not in session_state


def test_update_map_point_target_clears_when_focus_leaves_endpoint_inputs():
    session_state = {"map-point-target": "Start"}

    app.update_map_point_target(session_state, "none")

    assert "map-point-target" not in session_state


def test_apply_map_point_selection_ignores_stale_click_after_target_changes():
    session_state = {"map-point-target": "Start"}
    clicked = {"lat": 40.7, "lng": -74.0}

    assert app.apply_map_point_selection(session_state, clicked, "map-city-0") is True
    assert app.apply_pending_map_point_selection(session_state) is True
    session_state["map-point-target"] = "Finish"

    assert app.apply_map_point_selection(session_state, clicked, "map-city-0") is False
    assert session_state["start-input"] == "40.7000000,-74.0000000"
    assert session_state["start-point"] == (40.7, -74.0)
    assert "end-input" not in session_state


def test_add_endpoint_markers_adds_small_green_and_red_dots():
    route_map = app.folium.Map(location=(40.7, -74.0))
    session_state = {
        "start-point": (40.7, -74.0),
        "finish-point": (40.8, -73.9),
    }

    app.add_endpoint_markers(route_map, session_state)

    markers = [child for child in route_map._children.values() if isinstance(child, app.folium.CircleMarker)]
    assert len(markers) == 2
    assert {marker.options["color"] for marker in markers} == {"#16845b", "#d64045"}
    assert {tuple(marker.location) for marker in markers} == {(40.7, -74.0), (40.8, -73.9)}


def test_add_city_boundary_adds_outline_geojson_layer():
    route_map = app.folium.Map(location=(41.88, -87.63))
    boundary = {
        "type": "Polygon",
        "coordinates": [[[-87.7, 41.8], [-87.6, 41.8], [-87.6, 41.9], [-87.7, 41.8]]],
    }

    added = app.add_city_boundary(route_map, {"map_boundary": boundary})

    layers = [child for child in route_map._children.values() if isinstance(child, app.folium.GeoJson)]
    assert added is True
    assert len(layers) == 1
    assert layers[0].data["features"][0]["geometry"] == boundary
    assert layers[0].style_function({}) == {
        "color": "#16845b",
        "weight": 2,
        "fill": False,
        "fillOpacity": 0,
    }


def test_add_city_boundary_skips_point_only_search_results():
    route_map = app.folium.Map(location=(40.7, -74.0))

    added = app.add_city_boundary(route_map, {"map_boundary": None})

    assert added is False
    assert not any(isinstance(child, app.folium.GeoJson) for child in route_map._children.values())