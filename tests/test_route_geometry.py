from __future__ import annotations

import networkx as nx
from shapely.geometry import LineString

from app import build_route_geometry


def test_route_geometry_uses_osm_street_geometry_for_multidigraph_edges():
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node("A", x=-73.99, y=40.70)
    graph.add_node("B", x=-73.98, y=40.71)
    graph.add_node("C", x=-73.97, y=40.72)

    graph.add_edge(
        "A",
        "B",
        key="short",
        length=120.0,
        geometry=LineString([(-73.99, 40.70), (-73.985, 40.705), (-73.98, 40.71)]),
    )
    graph.add_edge(
        "A",
        "B",
        key="long",
        length=300.0,
        geometry=LineString([(-73.99, 40.70), (-73.97, 40.72)]),
    )
    graph.add_edge(
        "B",
        "C",
        key="curve",
        length=90.0,
        geometry=LineString([(-73.98, 40.71), (-73.975, 40.715), (-73.97, 40.72)]),
    )

    line = build_route_geometry(graph, ["A", "B", "C"])

    assert line.geom_type == "LineString"
    assert line.coords[0] == (-73.99, 40.70)
    assert list(line.coords)[-1] == (-73.97, 40.72)
    assert len(line.coords) == 5
    assert line.length > 0
