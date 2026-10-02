from __future__ import annotations

import networkx as nx
import folium
import osmnx as ox
import streamlit as st
from pathlib import Path
from pyproj import Transformer
from shapely.geometry import LineString
from streamlit_folium import st_folium

from city_route.config import RoutingConfig
from city_route.graph.loader import (
    ROAD_MODE_HIGHWAY_VALUES,
    ROUTE_CORRIDOR_BUFFER_M,
    add_travel_attributes,
    build_recovery_graph,
    filter_graph_by_road_mode,
    load_city_graph,
    load_route_corridor_graph,
)
from city_route.graph.preprocessing import prepare_graph
from city_route.routing.router import RoutePlanner


DEFAULT_MAP_CENTER = (40.7580, -73.9855)
_endpoint_focus = st.components.v1.declare_component(
    "endpoint_focus",
    path=str(Path(__file__).parent / "components" / "endpoint_focus"),
)


def resolve_place_map_location(
    query: str,
) -> tuple[
    tuple[float, float],
    tuple[float, float, float, float] | None,
    dict[str, object] | None,
]:
    query = query.strip()
    if not query:
        raise ValueError("Enter a place to search.")

    try:
        places = ox.geocode_to_gdf(query)
        if places.empty:
            raise ValueError("No place geometry was returned.")
        geometry = places.geometry.iloc[0]
        if geometry is None or geometry.is_empty:
            raise ValueError("The place result has no geometry.")

        center = geometry.representative_point()
        center_coordinates = (float(center.y), float(center.x))
        west, south, east, north = (float(value) for value in geometry.bounds)
        if west < east and south < north:
            boundary = (
                geometry.__geo_interface__
                if geometry.geom_type in {"Polygon", "MultiPolygon"}
                else None
            )
            return center_coordinates, (west, south, east, north), boundary
        return center_coordinates, None, None
    except Exception as exc:  # pragma: no cover - network/provider errors
        try:
            latitude, longitude = ox.geocode(query)
            return (float(latitude), float(longitude)), None, None
        except Exception as fallback_exc:
            raise ValueError(f"Could not find '{query}'. Check the place name and try again.") from fallback_exc


def update_city_map_state(session_state, query: str) -> None:
    session_state["map_search_error"] = None
    if not query.strip():
        return

    try:
        map_center, map_bounds, map_boundary = resolve_place_map_location(query)
    except ValueError as exc:
        session_state["map_search_error"] = str(exc)
        return

    session_state["map_center"] = map_center
    session_state["map_bounds"] = map_bounds
    session_state["map_boundary"] = map_boundary
    session_state["map_view"] = "city"
    session_state["map_fit_pending"] = True
    session_state["map_revision"] = int(session_state.get("map_revision", 0)) + 1


def _on_city_input_change() -> None:
    update_city_map_state(st.session_state, st.session_state.get("city-input", ""))


def _on_start_input_change() -> None:
    st.session_state["start-input"] = st.session_state["start-input-widget"]


def _on_end_input_change() -> None:
    st.session_state["end-input"] = st.session_state["end-input-widget"]


def update_map_point_target(session_state, focused_endpoint: str | None) -> None:
    if focused_endpoint in {"Start", "Finish"}:
        session_state["map-point-target"] = focused_endpoint
    else:
        session_state.pop("map-point-target", None)


def apply_pending_map_point_selection(session_state) -> bool:
    pending = session_state.pop("pending-map-point-selection", None)
    if not isinstance(pending, dict):
        return False

    endpoint_key = pending.get("endpoint_key")
    value = pending.get("value")
    if endpoint_key not in {"start-input", "end-input"} or not isinstance(value, str):
        return False

    session_state[endpoint_key] = value
    return True


def apply_map_point_selection(session_state, clicked, map_key: str) -> bool:
    if not isinstance(clicked, dict):
        return False

    try:
        latitude = float(clicked["lat"])
        longitude = float(clicked["lng"])
    except (KeyError, TypeError, ValueError):
        return False

    click_identity = (map_key, latitude, longitude)
    if session_state.get("last-map-click") == click_identity:
        return False

    session_state["last-map-click"] = click_identity
    endpoint_key = {"Start": "start-input", "Finish": "end-input"}.get(
        session_state.get("map-point-target")
    )
    if endpoint_key is None:
        return False

    value = f"{latitude:.7f},{longitude:.7f}"
    session_state["pending-map-point-selection"] = {
        "endpoint_key": endpoint_key,
        "value": value,
    }
    endpoint = "start" if endpoint_key == "start-input" else "finish"
    session_state[f"{endpoint}-point"] = (latitude, longitude)
    session_state["focus-endpoint-after-map-click"] = "Start" if endpoint == "start" else "Finish"
    session_state["focus-value-after-map-click"] = value
    return True


def add_endpoint_markers(map_object: folium.Map, session_state) -> None:
    for endpoint, color in (("start", "#16845b"), ("finish", "#d64045")):
        coordinates = session_state.get(f"{endpoint}-point")
        if not coordinates:
            continue
        folium.CircleMarker(
            location=coordinates,
            radius=6,
            color=color,
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=1,
            tooltip="Start" if endpoint == "start" else "Finish",
        ).add_to(map_object)


def add_city_boundary(map_object: folium.Map, session_state) -> bool:
    boundary = session_state.get("map_boundary")
    if not isinstance(boundary, dict):
        return False

    folium.GeoJson(
        boundary,
        name="City boundary",
        style_function=lambda _feature: {
            "color": "#16845b",
            "weight": 2,
            "fill": False,
            "fillOpacity": 0,
        },
    ).add_to(map_object)
    return True


def update_map_camera_state(session_state, map_result) -> bool:
    if not isinstance(map_result, dict):
        return False

    center = map_result.get("center")
    if isinstance(center, dict):
        try:
            center = (float(center["lat"]), float(center["lng"]))
        except (KeyError, TypeError, ValueError):
            center = None
    else:
        center = None

    if center is None:
        bounds = map_result.get("bounds")
        if not isinstance(bounds, dict):
            return False
        try:
            southwest = bounds["_southWest"]
            northeast = bounds["_northEast"]
            center = (
                (float(southwest["lat"]) + float(northeast["lat"])) / 2,
                (float(southwest["lng"]) + float(northeast["lng"])) / 2,
            )
        except (KeyError, TypeError, ValueError):
            return False

    session_state["map-camera-center"] = center
    try:
        zoom = map_result.get("zoom")
        if zoom is not None:
            session_state["map-camera-zoom"] = float(zoom)
    except (TypeError, ValueError):
        pass
    return True


def resolve_location_coordinates(graph: nx.MultiDiGraph, query: str) -> tuple[float, float]:
    query = query.strip()
    if not query:
        raise ValueError("Location query cannot be empty.")

    if query in graph.nodes:
        return float(graph.nodes[query]["y"]), float(graph.nodes[query]["x"])

    try:
        if "," in query:
            parts = [part.strip() for part in query.split(",")]
            if len(parts) >= 2:
                return float(parts[0]), float(parts[1])
    except ValueError:
        pass

    try:
        latitude, longitude = ox.geocode(query)
        return float(latitude), float(longitude)
    except Exception as exc:  # pragma: no cover - user input validation
        raise ValueError(f"Could not resolve '{query}' to a graph node. Use a place name or coordinates like '40.7128,-74.0060'.") from exc


def resolve_graph_node(
    graph: nx.MultiDiGraph,
    query: str,
    coordinates: tuple[float, float] | None = None,
):
    query = query.strip()
    if query in graph.nodes:
        return query
    latitude, longitude = coordinates or resolve_location_coordinates(graph, query)
    node, distance_m = ox.nearest_nodes(
        graph,
        longitude,
        latitude,
        return_dist=True,
    )
    if float(distance_m) > ROUTE_CORRIDOR_BUFFER_M:
        raise ValueError(
            f"The nearest road is more than {ROUTE_CORRIDOR_BUFFER_M / 1000:g} km from '{query}'."
        )
    return node


def _graph_crs(graph) -> str:
    crs = graph.graph.get("crs", "EPSG:4326")
    return str(crs).upper() if crs else "EPSG:4326"


def _transform_node_to_folium(graph, node: str) -> tuple[float, float]:
    if node not in graph.nodes:
        raise KeyError(node)
    x = float(graph.nodes[node]["x"])
    y = float(graph.nodes[node]["y"])
    crs = _graph_crs(graph)
    if crs not in {"EPSG:4326", "4326"}:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        x, y = transformer.transform(x, y)
    return (y, x)


def _geometry_to_folium_coords(graph, geometry) -> list[tuple[float, float]]:
    if geometry is None:
        return []
    crs = _graph_crs(graph)
    if crs not in {"EPSG:4326", "4326"}:
        try:
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            xs, ys = geometry.xy
            new_xs, new_ys = transformer.transform(xs, ys)
            geometry = LineString(list(zip(new_xs, new_ys)))
        except Exception:
            pass
    return [(float(lat), float(lon)) for lon, lat in geometry.coords]


def _select_edge_geometry(graph, u: str, v: str):
    if u not in graph.nodes or v not in graph.nodes:
        return None
    edge_data = graph.get_edge_data(u, v, default={})
    if not edge_data:
        return None

    if isinstance(graph, nx.MultiDiGraph):
        candidates = []
        for key, attrs in edge_data.items():
            if not isinstance(attrs, dict):
                continue
            weight = float(attrs.get("travel_time", attrs.get("length", float("inf"))))
            candidates.append((weight, key, attrs))
        if not candidates:
            return None
        _, _, attrs = min(candidates, key=lambda item: item[0])
        geometry = attrs.get("geometry")
        if geometry is not None:
            return geometry
        return LineString([
            (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
            (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
        ])

    attrs = edge_data
    geometry = attrs.get("geometry")
    if geometry is not None:
        return geometry
    return LineString([
        (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
        (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
    ])


def build_route_geometry(graph, route_nodes):
    """Rebuild the route from the actual OSM edge geometries, preserving route order and direction."""
    if not route_nodes:
        return LineString()

    route_nodes = [node for node in route_nodes if node in graph.nodes]
    if len(route_nodes) < 2:
        return LineString()

    segments = []
    for u, v in zip(route_nodes, route_nodes[1:]):
        geometry = _select_edge_geometry(graph, u, v)
        if geometry is None:
            continue
        segments.append(list(_geometry_to_folium_coords(graph, geometry)))

    if not segments:
        return LineString()

    combined = []
    for idx, segment in enumerate(segments):
        if idx == 0:
            combined.extend(segment)
            continue
        if segment and combined and segment[0] == combined[-1]:
            combined.extend(segment[1:])
        else:
            combined.extend(segment)

    if len(combined) < 2:
        return LineString()
    return LineString([(lon, lat) for lat, lon in combined])


def render_route_map(graph, route) -> folium.Map:
    if not route.nodes:
        return folium.Map(location=[0.0, 0.0], zoom_start=2)

    all_nodes = [node for node in route.nodes if node in graph.nodes]
    if not all_nodes:
        return folium.Map(location=[0.0, 0.0], zoom_start=2)

    route_coords = [_transform_node_to_folium(graph, node) for node in all_nodes]
    lats = [point[0] for point in route_coords]
    lons = [point[1] for point in route_coords]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    route_map = folium.Map(
        location=center,
        zoom_start=14,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    for u, v, attrs in graph.edges(data=True):
        try:
            if "geometry" in attrs:
                coords = _geometry_to_folium_coords(graph, attrs["geometry"])
            else:
                coords = [
                    _transform_node_to_folium(graph, u),
                    _transform_node_to_folium(graph, v),
                ]
            if coords:
                folium.PolyLine(coords, color="#5dade2", weight=2, opacity=0.35).add_to(route_map)
        except KeyError:
            continue

    route_geometry = build_route_geometry(graph, all_nodes)
    if route_geometry and len(route_geometry.coords) >= 2:
        route_line_coords = [(float(lat), float(lon)) for lon, lat in route_geometry.coords]
        folium.PolyLine(route_line_coords, color="#ff6b6b", weight=6, opacity=0.95).add_to(route_map)
        folium.PolyLine(route_line_coords, color="#ffd166", weight=3, opacity=0.75).add_to(route_map)

    folium.Marker(
        location=_transform_node_to_folium(graph, all_nodes[0]),
        popup="Start",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(route_map)
    folium.Marker(
        location=_transform_node_to_folium(graph, all_nodes[-1]),
        popup="End",
        icon=folium.Icon(color="red", icon="flag", prefix="fa"),
    ).add_to(route_map)
    return route_map


@st.cache_resource(show_spinner=False)
def load_prepared_city_graph(place: str):
    graph = load_city_graph(place)
    return prepare_graph(add_travel_attributes(graph))


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root {
            --ink: #1d2927;
            --muted: #6f7b76;
            --paper: #fffefa;
            --line: #e7e9e3;
            --green: #16785e;
            --lime: #d7ee8a;
        }
        html, body, [data-testid="stAppViewContainer"], .stApp { height: 100%; }
        .stApp { background: #e9ece7; color: var(--ink); font-family: 'DM Sans', sans-serif; }
        [data-testid="stHeader"], [data-testid="stToolbar"], footer, #MainMenu { display: none !important; }
        [data-testid="stAppViewBlockContainer"] { padding: 0 !important; max-width: none !important; }
        [data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: none !important; }
        [data-testid="stSidebar"] { display: none !important; }
        .st-key-map { position: fixed; inset: 0; z-index: 0; padding: 0; margin: 0; }
        .st-key-map [data-testid="stCustomComponentV1"],
        .st-key-map iframe { width: 100vw !important; height: 100vh !important; border: 0 !important; }
        .st-key-config, .st-key-route {
            position: fixed; z-index: 20; background: var(--paper); border: 1px solid rgba(29,41,39,.08);
            box-shadow: 0 10px 36px rgba(29,41,39,.13); border-radius: 10px;
        }
        .st-key-topbar { position: fixed; z-index: 20; left: 64px; top: 16px; width: max-content; padding: 0; background: transparent; border: 0; box-shadow: none; }
        .st-key-topbar [data-testid="stMarkdownContainer"] p { margin: 0; }
        .brand { display: flex; align-items: center; gap: 11px; min-height: 36px; }
        .brand-mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 8px; color: #fff; background: var(--green); font-size: 18px; }
        .brand-name { font: 700 16px 'Space Grotesk', sans-serif; letter-spacing: 0; color: var(--ink); }
        .brand-sub { color: var(--muted); font-size: 11px; margin-top: 1px; }
        .st-key-config {
            left: 20px; top: 82px; width: 340px; padding: 18px; max-height: calc(100vh - 105px); overflow-y: auto;
            background: rgba(255,254,250,.68); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
        }
        .st-key-config [data-testid="stElementContainer"].st-key-city {
            width: 264px; margin-left: 16px;
        }
        .st-key-config [data-testid="stElementContainer"].st-key-city [data-testid="stTextInput"] {
            width: 100%; max-width: 100%; box-sizing: border-box;
        }
        .st-key-config [data-testid="stElementContainer"].st-key-road,
        .st-key-config [data-testid="stElementContainer"].st-key-start,
        .st-key-config [data-testid="stElementContainer"].st-key-end {
            width: 264px; max-width: calc(100vw - 58px); margin-left: 16px;
        }
        .st-key-config [data-testid="stElementContainer"].st-key-road [data-testid="stSelectbox"],
        .st-key-config [data-testid="stElementContainer"].st-key-start [data-testid="stTextInput"],
        .st-key-config [data-testid="stElementContainer"].st-key-end [data-testid="stTextInput"] {
            width: 100%; max-width: 100%; box-sizing: border-box;
        }
        .st-key-config h3, .st-key-route h3 { font: 600 18px 'Space Grotesk', sans-serif; letter-spacing: 0; color: var(--ink) !important; margin: 0 0 4px; }
        .panel-caption { color: var(--muted); font-size: 12px; margin-bottom: 16px; }
        .st-key-config label { font-size: 12px !important; font-weight: 600 !important; color: #48534f !important; }
        .st-key-config input { border-radius: 6px !important; border-color: var(--line) !important; background: rgba(255,255,255,.94) !important; color: var(--ink) !important; }
        .st-key-config [data-testid="stSlider"] { padding-top: 2px; }
        .st-key-config [data-testid="stSlider"] [role="slider"] { background: var(--green); }
        .st-key-config [data-testid="stButton"] button,
        .st-key-openconfig [data-testid="stButton"] button {
            min-height: 40px; border-radius: 6px; border: 1px solid var(--green); background: var(--green); color: white;
            font-weight: 700; transition: background .16s ease, transform .16s ease;
        }
        .st-key-config [data-testid="stButton"] button:hover,
        .st-key-openconfig [data-testid="stButton"] button:hover { background: #105f4a; color: white; transform: translateY(-1px); }
        .st-key-config [data-testid="stHorizontalBlock"] { align-items: flex-start; gap: 8px; }
        .st-key-config [data-testid="stButton"] button { min-height: 34px; padding: 0 10px; }
        .st-key-openconfig { position: fixed; z-index: 20; left: 20px; top: 82px; width: max-content; }
        .st-key-route { right: 20px; bottom: 20px; width: 310px; padding: 16px; }
        .summary-kicker { color: var(--green); font-size: 10px; font-weight: 700; text-transform: uppercase; margin-bottom: 5px; }
        .summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; }
        .summary-value { font: 600 20px 'Space Grotesk', sans-serif; }
        .summary-label { color: var(--muted); font-size: 11px; margin-top: 2px; }
        .st-key-config [data-testid="stAlert"] { padding: 10px 12px; }
        @media (max-width: 640px) {
            .st-key-topbar { left: 50px; top: 10px; }
            .st-key-config { left: 10px; top: 230px; width: min(340px, calc(100vw - 20px)); max-height: calc(100vh - 240px); }
            .st-key-config [data-testid="stElementContainer"].st-key-city {
                width: min(264px, calc(100vw - 58px));
            }
            .st-key-route { right: 10px; left: 10px; bottom: 10px; width: auto; }
            .st-key-openconfig { left: 10px; top: 230px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Wayfinder | City Route Studio", layout="wide", initial_sidebar_state="collapsed")
    _render_styles()

    if "config_open" not in st.session_state:
        st.session_state.config_open = True
    if "map_revision" not in st.session_state:
        st.session_state.map_revision = 0
    if "start-input" not in st.session_state:
        st.session_state["start-input"] = "Times Square"
    if "end-input" not in st.session_state:
        st.session_state["end-input"] = "Brooklyn Bridge"
    apply_pending_map_point_selection(st.session_state)
    st.session_state["start-input-widget"] = st.session_state["start-input"]
    st.session_state["end-input-widget"] = st.session_state["end-input"]

    focus_endpoint = st.session_state.pop("focus-endpoint-after-map-click", None)
    focus_value = st.session_state.pop("focus-value-after-map-click", None)
    focused_endpoint = _endpoint_focus(
        key="endpoint-focus",
        default="none",
        focus_endpoint=focus_endpoint,
        focus_value=focus_value,
    )
    update_map_point_target(st.session_state, focused_endpoint)

    city = st.session_state.get("city", "Manhattan, New York City, NY, USA")
    route_graph = st.session_state.get("route_graph")
    route = st.session_state.get("route")
    map_view = st.session_state.get("map_view", "route" if route_graph is not None and route is not None else "city")
    map_key = f"map-{map_view}-{st.session_state.map_revision}"
    if map_view == "route" and route_graph is not None and route is not None:
        route_map = render_route_map(route_graph, route)
    else:
        map_fit_pending = map_view == "city" and st.session_state.get("map_fit_pending", False)
        camera_center = None if map_fit_pending else st.session_state.get("map-camera-center")
        camera_zoom = None if map_fit_pending else st.session_state.get("map-camera-zoom")
        route_map = folium.Map(
            location=camera_center or st.session_state.get("map_center", DEFAULT_MAP_CENTER),
            zoom_start=camera_zoom or 12,
            tiles="OpenStreetMap",
            control_scale=True,
        )
        map_bounds = st.session_state.get("map_bounds")
        if map_fit_pending and map_bounds is not None:
            west, south, east, north = map_bounds
            route_map.fit_bounds([[south, west], [north, east]])
        add_city_boundary(route_map, st.session_state)

    endpoint_marker_group = folium.FeatureGroup()
    add_endpoint_markers(endpoint_marker_group, st.session_state)
    if st.session_state.get("map-point-target") in {"Start", "Finish"}:
        route_map.get_root().header.add_child(
            folium.Element("<style>.leaflet-container, .leaflet-container * { cursor: pointer !important; }</style>")
        )

    with st.container(key="map-stage"):
        map_result = st_folium(
            route_map,
            height=1000,
            use_container_width=True,
            returned_objects=["last_clicked", "bounds", "zoom", "center"],
            key=map_key,
            feature_group_to_add=endpoint_marker_group,
        )
        update_map_camera_state(st.session_state, map_result)
        if map_view == "city" and st.session_state.get("map_fit_pending", False):
            st.session_state["map_fit_pending"] = False
        point_selected = apply_map_point_selection(
            st.session_state,
            map_result.get("last_clicked") if map_result else None,
            map_key,
        )
        if point_selected:
            st.rerun()

    with st.container(key="topbar"):
        st.markdown(
            '<div class="brand"><div class="brand-mark">W</div><div><div class="brand-name">WAYFINDER</div><div class="brand-sub">CITY ROUTE STUDIO</div></div></div>',
            unsafe_allow_html=True,
        )

    if st.session_state.config_open:
        with st.container(key="config-panel"):
            title_col, close_col = st.columns([5, 1])
            with title_col:
                st.markdown('<div class="summary-kicker">ROUTE BUILDER</div><h3>Shape your next route</h3><div class="panel-caption">Choose your area, endpoints and time budget.</div>', unsafe_allow_html=True)
            with close_col:
                if st.button("X", key="hide-config", use_container_width=True, help="Hide route configuration"):
                    st.session_state.config_open = False
                    st.rerun()
            with st.container(key="city-input-wrap"):
                city = st.text_input(
                    "City / place",
                    value=city,
                    key="city-input",
                    on_change=_on_city_input_change,
                    help="The background map updates when you finish editing this field.",
                )
            if st.session_state.get("map_search_error"):
                st.error(st.session_state.map_search_error)
            road_mode = st.selectbox("Road network", list(ROAD_MODE_HIGHWAY_VALUES), key="road-mode")
            start = st.text_input("Start", key="start-input-widget", on_change=_on_start_input_change)
            end = st.text_input("Finish", key="end-input-widget", on_change=_on_end_input_change)
            with st.form("route-form"):
                target_time_minutes = st.slider("Target duration", 5, 500, 45, format="%d min", key="target-time")
                search_limit = st.number_input("Search effort", min_value=20, max_value=2000, value=300, step=20, key="search-limit")
                generate_route = st.form_submit_button("Generate route", use_container_width=True)

            if generate_route:
                try:
                    with st.spinner("Loading streets and finding a route..."):
                        base_graph = load_prepared_city_graph(city)
                        start_coordinates = resolve_location_coordinates(base_graph, start)
                        end_coordinates = resolve_location_coordinates(base_graph, end)
                        graph = filter_graph_by_road_mode(base_graph, road_mode)
                        config = RoutingConfig(
                            target_time_seconds=target_time_minutes * 60,
                            max_search_steps=int(search_limit),
                        )
                        planner = RoutePlanner(config)
                        route = None
                        start_node = None
                        end_node = None

                        if graph.number_of_edges() > 0:
                            try:
                                start_node = resolve_graph_node(graph, start, start_coordinates)
                                end_node = resolve_graph_node(graph, end, end_coordinates)
                            except (ValueError, nx.NetworkXException):
                                pass
                            else:
                                route = planner.route(graph, start_node, end_node)

                        if route is None or not route.feasible or not route.reached_destination:
                            patch_graph = load_route_corridor_graph(
                                start_coordinates,
                                end_coordinates,
                            )
                            merged_mode_graph = filter_graph_by_road_mode(
                                nx.compose(base_graph, patch_graph),
                                road_mode,
                            )
                            try:
                                mode_start_node = resolve_graph_node(
                                    merged_mode_graph,
                                    start,
                                    start_coordinates,
                                )
                                mode_end_node = resolve_graph_node(
                                    merged_mode_graph,
                                    end,
                                    end_coordinates,
                                )
                            except (ValueError, nx.NetworkXException):
                                mode_start_node = None
                                mode_end_node = None

                            graph = build_recovery_graph(
                                base_graph,
                                patch_graph,
                                road_mode,
                                mode_start_node,
                                mode_end_node,
                            )
                            start_node = resolve_graph_node(graph, start, start_coordinates)
                            end_node = resolve_graph_node(graph, end, end_coordinates)
                            route = planner.route(graph, start_node, end_node)

                        if not route.feasible or not route.reached_destination:
                            raise ValueError(
                                f"No connected route could be found on or near the {road_mode.lower()} network, "
                                "even after downloading roads along the endpoint corridor."
                            )
                        st.session_state.city = city
                        st.session_state.route_mode = road_mode
                        st.session_state.route_graph = graph
                        st.session_state.route = route
                        st.session_state.map_view = "route"
                        st.session_state.map_revision += 1
                    st.rerun()
                except Exception as exc:  # pragma: no cover - streamlit only
                    st.error(f"Unable to generate route: {exc}")
    else:
        with st.container(key="openconfig"):
            if st.button("Plan a route", key="open-config"):
                st.session_state.config_open = True
                st.rerun()

    if route is not None and st.session_state.get("map_view") == "route":
        estimated_minutes = route.duration_seconds / 60
        route_mode = st.session_state.get("route_mode", "City")
        with st.container(key="route-summary"):
            st.markdown(
                f'<div class="summary-kicker">{route_mode.upper()} ROUTE READY</div><h3>{estimated_minutes:.0f} min journey</h3>'
                f'<div class="summary-grid"><div><div class="summary-value">{route.distance_m / 1000:.1f} km</div><div class="summary-label">Distance</div></div>'
                f'<div><div class="summary-value">{route.pattern_score:.0f}<span style="font-size:12px">/100</span></div><div class="summary-label">Pattern score</div></div></div>',
                unsafe_allow_html=True,
            )


if __name__ == "__main__":
    main()
