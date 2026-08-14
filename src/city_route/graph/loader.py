from __future__ import annotations

import re
from typing import Any

import networkx as nx
import osmnx as ox


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
    """Download a city street network using OSMnx."""
    graph = ox.graph_from_place(place, network_type=network_type, simplify=True)
    if graph is None:
        raise ValueError(f"No graph found for place: {place}")
    return graph


def load_graph_from_bbox(north: float, south: float, east: float, west: float, network_type: str = "drive") -> nx.MultiDiGraph:
    graph = ox.graph_from_bbox(north, south, east, west, network_type=network_type, simplify=True)
    if graph is None:
        raise ValueError("No graph found in the provided bounding box")
    return graph


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
