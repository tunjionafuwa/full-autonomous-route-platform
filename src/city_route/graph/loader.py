from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
from threading import RLock
from typing import Any

import networkx as nx
import osmnx as ox
import requests
from shapely.geometry import LineString

from city_route.graph.preprocessing import prepare_graph


ROAD_MODE_HIGHWAY_VALUES = {
    "City": frozenset({"living_street", "residential", "service", "tertiary", "tertiary_link"}),
    "Rural": frozenset({
        "unclassified",
        "tertiary",
        "tertiary_link",
        "secondary",
        "secondary_link",
        "primary",
        "primary_link",
    }),
    "Highway": frozenset({"motorway", "motorway_link", "trunk", "trunk_link"}),
}
ROUTE_CORRIDOR_BUFFER_M = 2000.0
OVERPASS_FALLBACK_URL = "https://overpass.kumi.systems/api"
CITY_GRAPH_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "city-route" / "graphs"
_OVERPASS_LOCK = RLock()
_LOGGER = logging.getLogger(__name__)


class GraphDownloadError(RuntimeError):
    """Raised when no Overpass endpoint or local city graph can satisfy a load."""


def _download_with_overpass_fallback(
    download: Callable[[], Any],
) -> Any:
    failures: list[requests.exceptions.RequestException] = []
    with _OVERPASS_LOCK:
        original_url = ox.settings.overpass_url
        endpoints = tuple(dict.fromkeys((original_url, OVERPASS_FALLBACK_URL)))
        try:
            for endpoint in endpoints:
                ox.settings.overpass_url = endpoint
                try:
                    return download()
                except requests.exceptions.RequestException as exc:
                    failures.append(exc)
        finally:
            ox.settings.overpass_url = original_url

    raise GraphDownloadError(
        "All configured Overpass endpoints failed. Check your internet connection and retry."
    ) from failures[-1]


def _city_graph_cache_path(place: str, network_type: str) -> Path:
    cache_key = hashlib.sha256(f"{place}\0{network_type}".encode("utf-8")).hexdigest()
    return CITY_GRAPH_CACHE_DIR / f"{cache_key}.graphml"


def _save_city_graph(graph: Any, cache_path: Path) -> None:
    temporary_path = cache_path.with_suffix(".tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(graph, filepath=temporary_path)
        temporary_path.replace(cache_path)
    except Exception:
        _LOGGER.warning("Could not save city graph cache at %s", cache_path, exc_info=True)
        temporary_path.unlink(missing_ok=True)


def _load_cached_city_graph(cache_path: Path, place: str) -> Any:
    try:
        return ox.load_graphml(filepath=cache_path)
    except Exception as exc:
        raise GraphDownloadError(
            f"Overpass is unavailable and the saved street graph for {place} could not be loaded. "
            "Check your connection and retry."
        ) from exc


def filter_graph_by_road_mode(graph: nx.MultiDiGraph, mode: str) -> nx.MultiDiGraph:
    """Return a copy containing only edges in the selected OSM road class group."""
    if mode not in ROAD_MODE_HIGHWAY_VALUES:
        raise ValueError(f"Unknown road mode '{mode}'. Choose City, Rural, or Highway.")

    allowed_highway_values = ROAD_MODE_HIGHWAY_VALUES[mode]
    filtered = nx.MultiDiGraph()
    filtered.graph.update(graph.graph)

    for start, end, key, data in graph.edges(keys=True, data=True):
        highway = data.get("highway")
        highway_values = highway if isinstance(highway, (list, tuple, set)) else (highway,)
        if not any(str(value).lower() in allowed_highway_values for value in highway_values if value is not None):
            continue

        if start not in filtered:
            filtered.add_node(start, **dict(graph.nodes[start]))
        if end not in filtered:
            filtered.add_node(end, **dict(graph.nodes[end]))
        filtered.add_edge(start, end, key=key)
        filtered[start][end][key].update(data)

    return filtered


def _parse_speed_to_kph(value: Any) -> float:
    """Convert OSM speed values like '25 mph' or '50 km/h' to km/h."""
    if value is None:
        return 30.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple, set)):
        for item in value:
            parsed = _parse_speed_to_kph(item)
            if parsed > 0:
                return parsed
        return 30.0

    text = str(value).strip().lower()
    if not text:
        return 30.0

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return 30.0

    numeric_value = float(match.group(1))
    if "mph" in text:
        return numeric_value * 1.60934
    if "km/h" in text or "kmh" in text or "kph" in text:
        return numeric_value
    if "m/s" in text:
        return numeric_value * 3.6
    if "knots" in text or "knot" in text:
        return numeric_value * 1.852

    return numeric_value


def load_city_graph(place: str, network_type: str = "drive") -> nx.MultiDiGraph:
    """Download or load a cached city street network using OSMnx."""
    cache_path = _city_graph_cache_path(place, network_type)
    try:
        graph = _download_with_overpass_fallback(
            lambda: ox.graph_from_place(place, network_type=network_type, simplify=True)
        )
    except GraphDownloadError as exc:
        if cache_path.is_file():
            return _load_cached_city_graph(cache_path, place)
        raise GraphDownloadError(
            f"Unable to download the street network for {place}; Overpass is unavailable and "
            "no local graph is cached. Check your internet connection and retry."
        ) from exc

    if graph is None:
        raise ValueError(f"No graph found for place: {place}")
    _save_city_graph(graph, cache_path)
    return graph


def load_graph_from_bbox(north: float, south: float, east: float, west: float, network_type: str = "drive") -> nx.MultiDiGraph:
    graph = _download_with_overpass_fallback(
        lambda: ox.graph_from_bbox(
            bbox=(north, south, east, west),
            network_type=network_type,
            simplify=True,
        )
    )
    if graph is None:
        raise ValueError("No graph found in the provided bounding box")
    return graph


def load_route_corridor_graph(
    start_coordinates: tuple[float, float],
    end_coordinates: tuple[float, float],
    *,
    buffer_m: float = ROUTE_CORRIDOR_BUFFER_M,
    max_distance_km: float = 75.0,
) -> nx.MultiDiGraph:
    """Download and prepare drivable roads in a bounded corridor between endpoints."""
    for latitude, longitude in (start_coordinates, end_coordinates):
        if not isfinite(latitude) or not isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Route coordinates must be valid latitude/longitude values.")

    start_latitude, start_longitude = start_coordinates
    end_latitude, end_longitude = end_coordinates
    latitude_delta = radians(end_latitude - start_latitude)
    longitude_delta = radians(end_longitude - start_longitude)
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(radians(start_latitude))
        * cos(radians(end_latitude))
        * sin(longitude_delta / 2) ** 2
    )
    distance_km = 6371.0088 * 2 * asin(sqrt(haversine))
    if distance_km > max_distance_km:
        raise ValueError(
            f"Ad-hoc road downloads are limited to routes within {max_distance_km:g} km. "
            "Choose a closer pair of endpoints or a smaller area."
        )
    if buffer_m <= 0:
        raise ValueError("The route corridor buffer must be greater than zero.")

    endpoint_line = LineString(
        [(start_longitude, start_latitude), (end_longitude, end_latitude)]
    )
    projected_line, projected_crs = ox.projection.project_geometry(
        endpoint_line,
        crs="EPSG:4326",
    )
    projected_corridor = projected_line.buffer(buffer_m)
    corridor, _ = ox.projection.project_geometry(
        projected_corridor,
        crs=projected_crs,
        to_crs="EPSG:4326",
    )
    graph = _download_with_overpass_fallback(
        lambda: ox.graph_from_polygon(
            corridor,
            network_type="drive",
            simplify=True,
            retain_all=True,
            truncate_by_edge=True,
        )
    )
    if graph is None:
        raise ValueError("No drivable roads were found along the requested route corridor.")
    return prepare_graph(add_travel_attributes(graph))


def build_recovery_graph(
    base_graph: nx.MultiDiGraph,
    patch_graph: nx.MultiDiGraph,
    mode: str,
    start_node: Any | None,
    end_node: Any | None,
) -> nx.MultiDiGraph:
    """Prefer selected-mode roads, adding fetched drive roads only if needed to connect."""
    merged_graph = nx.compose(base_graph, patch_graph)
    mode_graph = filter_graph_by_road_mode(merged_graph, mode)
    if (
        start_node in mode_graph
        and end_node in mode_graph
        and nx.has_path(mode_graph, start_node, end_node)
    ):
        return mode_graph
    return nx.compose(patch_graph, mode_graph)


def add_travel_attributes(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Populate length, speed, and travel time attributes for each directed edge."""
    for _, _, key, data in graph.edges(keys=True, data=True):
        length_m = float(data.get("length", 0.0))
        if length_m <= 0:
            length_m = float(data.get("geometry", None).length) if data.get("geometry") is not None else 0.0

        raw_speed = data.get("maxspeed", data.get("speed_kph", 30.0))
        speed_kph = _parse_speed_to_kph(raw_speed)

        if speed_kph <= 0:
            speed_kph = 30.0

        data["length"] = length_m
        data["speed_kph"] = speed_kph
        data["travel_time"] = (length_m / 1000.0) * 3600.0 / speed_kph if speed_kph > 0 else 0.0
    return graph
