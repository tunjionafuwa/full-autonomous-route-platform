import networkx as nx
import osmnx as ox
import pytest
import requests

from app import resolve_graph_node
from city_route.graph import loader as graph_loader
from city_route.graph.loader import (
    GraphDownloadError,
    build_recovery_graph,
    load_city_graph,
    load_route_corridor_graph,
)


def build_graph(edges):
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    nodes = {node for start, end, _, _ in edges for node in (start, end)}
    for node in nodes:
        graph.add_node(node, x=float(node), y=0.0)
    for start, end, key, highway in edges:
        graph.add_edge(
            start,
            end,
            key=key,
            highway=highway,
            length=100.0,
            maxspeed="30 km/h",
        )
    return graph


@pytest.mark.parametrize(
    ("mode", "selected_highway", "connector_highway"),
    [
        ("City", "residential", "unclassified"),
        ("Rural", "primary", "service"),
        ("Highway", "motorway", "primary"),
    ],
)
def test_recovery_graph_adds_fetched_connector_roads(mode, selected_highway, connector_highway):
    base_graph = build_graph([(0, 1, "base", selected_highway)])
    patch_graph = build_graph([
        (1, 2, "connector", connector_highway),
        (2, 3, "patch-selected", selected_highway),
    ])

    recovery_graph = build_recovery_graph(base_graph, patch_graph, mode, 0, 3)

    assert nx.has_path(recovery_graph, 0, 3)
    assert recovery_graph[1][2]["connector"]["highway"] == connector_highway
    assert base_graph.number_of_edges() == 1
    assert patch_graph.number_of_edges() == 2


def test_recovery_graph_prefers_connected_selected_mode_path():
    base_graph = build_graph([
        (0, 1, "selected-a", "residential"),
        (1, 2, "selected-b", "tertiary"),
    ])
    patch_graph = build_graph([(0, 2, "off-mode-shortcut", "motorway")])

    recovery_graph = build_recovery_graph(base_graph, patch_graph, "City", 0, 2)

    assert {data["highway"] for _, _, data in recovery_graph.edges(data=True)} == {
        "residential",
        "tertiary",
    }
    assert patch_graph.number_of_edges() == 1


def test_route_corridor_download_uses_bounded_drive_polygon(monkeypatch):
    patch_graph = build_graph([(0, 1, "road", "residential")])
    download = {}

    def fake_graph_from_polygon(polygon, **kwargs):
        download["polygon"] = polygon
        download.update(kwargs)
        return patch_graph

    monkeypatch.setattr(ox, "graph_from_polygon", fake_graph_from_polygon)

    result = load_route_corridor_graph((40.70, -74.02), (40.72, -73.98))

    assert download["polygon"].is_valid
    assert download["network_type"] == "drive"
    assert download["retain_all"] is True
    assert download["truncate_by_edge"] is True
    assert result[0][1]["road"]["travel_time"] > 0


def test_route_corridor_download_rejects_long_distance_before_query(monkeypatch):
    def unexpected_download(*args, **kwargs):
        raise AssertionError("The over-limit corridor must not be downloaded")

    monkeypatch.setattr(ox, "graph_from_polygon", unexpected_download)

    with pytest.raises(ValueError, match="limited to routes within 75 km"):
        load_route_corridor_graph((0.0, 0.0), (0.0, 1.0))


def test_resolve_graph_node_rejects_remote_mode_only_snap():
    graph = build_graph([(1, 2, "road", "residential")])
    graph.nodes[1].update(x=-74.0, y=40.0)
    graph.nodes[2].update(x=-73.999, y=40.0)

    assert resolve_graph_node(graph, "nearby", (40.0, -74.0)) == 1
    with pytest.raises(ValueError, match="more than 2 km"):
        resolve_graph_node(graph, "remote", (40.03, -74.0))


def test_city_graph_retries_fallback_and_restores_overpass_url(tmp_path, monkeypatch):
    graph = build_graph([(1, 2, "road", "residential")])
    monkeypatch.setattr(graph_loader, "CITY_GRAPH_CACHE_DIR", tmp_path)
    original_url = ox.settings.overpass_url
    attempted_urls = []

    def download(*args, **kwargs):
        attempted_urls.append(ox.settings.overpass_url)
        if ox.settings.overpass_url == original_url:
            raise requests.ConnectionError("primary unavailable")
        return graph

    monkeypatch.setattr(ox, "graph_from_place", download)

    result = load_city_graph("Test City")

    assert result is graph
    assert attempted_urls == [original_url, graph_loader.OVERPASS_FALLBACK_URL]
    assert ox.settings.overpass_url == original_url
    assert list(tmp_path.glob("*.graphml"))


def test_city_graph_uses_saved_graph_when_all_endpoints_fail(tmp_path, monkeypatch):
    graph = build_graph([(1, 2, "road", "residential")])
    monkeypatch.setattr(graph_loader, "CITY_GRAPH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(ox, "graph_from_place", lambda *_args, **_kwargs: graph)
    load_city_graph("Test City")

    original_url = ox.settings.overpass_url
    attempted_urls = []

    def fail_download(*args, **kwargs):
        attempted_urls.append(ox.settings.overpass_url)
        raise requests.ConnectionError("endpoint unavailable")

    monkeypatch.setattr(ox, "graph_from_place", fail_download)

    cached_graph = load_city_graph("Test City")

    assert set(cached_graph.nodes) == {1, 2}
    assert attempted_urls == [original_url, graph_loader.OVERPASS_FALLBACK_URL]
    assert ox.settings.overpass_url == original_url


def test_city_graph_reports_outage_when_no_saved_graph_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_loader, "CITY_GRAPH_CACHE_DIR", tmp_path)
    original_url = ox.settings.overpass_url
    attempted_urls = []

    def fail_download(*args, **kwargs):
        attempted_urls.append(ox.settings.overpass_url)
        raise requests.ConnectionError("endpoint unavailable")

    monkeypatch.setattr(ox, "graph_from_place", fail_download)

    with pytest.raises(GraphDownloadError, match="no local graph is cached"):
        load_city_graph("Uncached City")

    assert attempted_urls == [original_url, graph_loader.OVERPASS_FALLBACK_URL]
    assert ox.settings.overpass_url == original_url
