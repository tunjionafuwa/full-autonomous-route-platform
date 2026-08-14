from __future__ import annotations

import networkx as nx
import folium
import osmnx as ox
import streamlit as st
from pyproj import Transformer
from shapely.geometry import LineString
from streamlit_folium import folium_static

from city_route.config import RoutingConfig
from city_route.graph.loader import add_travel_attributes, load_city_graph
from city_route.graph.preprocessing import prepare_graph
from city_route.routing.router import RoutePlanner


def resolve_graph_node(graph, query: str):
    query = query.strip()
    if not query:
        raise ValueError("Location query cannot be empty.")

    if query in graph.nodes:
        return query

    try:
        if "," in query:
            parts = [part.strip() for part in query.split(",")]
            if len(parts) >= 2:
                lat = float(parts[0])
                lon = float(parts[1])
                return ox.nearest_nodes(graph, lon, lat)
    except ValueError:
        pass

    try:
        lat, lon = ox.geocode(query)
        return ox.nearest_nodes(graph, lon, lat)
    except Exception as exc:  # pragma: no cover - user input validation
        raise ValueError(f"Could not resolve '{query}' to a graph node. Use a place name or coordinates like '40.7128,-74.0060'.") from exc


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


def main() -> None:
    st.set_page_config(page_title="City Route Pattern Generator", layout="wide")

    st.title("City Route Pattern Generator")

    with st.sidebar:
        st.header("Route configuration")
        city = st.text_input("City / place", value="Manhattan, New York City, NY, USA")
        start = st.text_input("Start location", value="Times Square")
        end = st.text_input("End location", value="Brooklyn Bridge")
        target_time_minutes = st.slider("Target route duration (minutes)", 5, 500, 45)
        search_limit = st.number_input("Max search steps", min_value=20, max_value=2000, value=300)
        st.caption("This prototype keeps the algorithm independent from Streamlit and exposes a thin UI layer.")

    if st.button("Generate route"):
        config = RoutingConfig(target_time_seconds=target_time_minutes * 60, max_search_steps=int(search_limit))
        try:
            graph = load_city_graph(city)
            graph = add_travel_attributes(graph)
            graph = prepare_graph(graph)

            start_node = resolve_graph_node(graph, start)
            end_node = resolve_graph_node(graph, end)

            planner = RoutePlanner(config)
            route = planner.route(graph, start_node, end_node) if start_node != end_node else planner.route(graph, start_node, start_node)

            st.subheader("Route statistics")
            estimated_minutes = route.duration_seconds / 60
            time_diff_minutes = estimated_minutes - target_time_minutes
            time_error_percent = abs(time_diff_minutes) / target_time_minutes * 100 if target_time_minutes > 0 else 0
            
            st.write({
                "Requested time": f"{target_time_minutes} min",
                "Estimated time": f"{estimated_minutes:.1f} min",
                "Time difference": f"{time_diff_minutes:+.1f} min",
                "Time error": f"{time_error_percent:.1f}%",
                "Distance": f"{route.distance_m / 1000:.2f} km",
                "Edges": len(route.edges),
                "Pattern score": f"{route.pattern_score:.1f}/100",
                "Reached destination": route.reached_destination,
                "Within budget": route.within_time_budget,
                "Loop": route.loop,
            })

            st.subheader("Route map")
            route_map = render_route_map(graph, route)
            folium_static(route_map, width=1100, height=620)

            st.write("This is a thin visualization layer over the routing engine; the actual logic is in the Python package.")
        except Exception as exc:  # pragma: no cover - streamlit only
            st.error(f"Unable to generate route: {exc}")
    else:
        st.info("Configure a city and route, then click Generate route.")


if __name__ == "__main__":
    main()
