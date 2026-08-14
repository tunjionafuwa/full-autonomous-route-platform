import networkx as nx

from city_route.config import RoutingConfig
from city_route.routing.router import RoutePlanner


def build_graph():
    graph = nx.Graph()
    graph.add_weighted_edges_from([
        ("A", "B", 1),
        ("B", "C", 1),
        ("C", "D", 1),
        ("D", "A", 1),
        ("B", "E", 1),
        ("E", "D", 1),
    ])
    for u, v, d in graph.edges(data=True):
        d["length"] = 100.0
        d["speed_kph"] = 30.0
        d["travel_time"] = 10.0
    return graph


def test_different_start_end_reaches_destination():
    graph = build_graph()
    router = RoutePlanner(RoutingConfig(target_time_seconds=1200, beam_width=20))
    result = router.route(graph, "A", "D")
    assert result.reached_destination is True
    assert result.nodes[-1] == "D"


def test_same_start_end_generates_loop():
    graph = build_graph()
    router = RoutePlanner(RoutingConfig(target_time_seconds=2000, beam_width=20))
    result = router.route(graph, "A", "A")
    assert result.loop is True
    assert result.nodes[0] == "A"
    assert result.nodes[-1] == "A"
    assert len(result.nodes) > 1
    assert result.duration_seconds > 0.0


def test_same_start_end_tracks_real_edge_time_and_path():
    graph = build_graph()
    router = RoutePlanner(RoutingConfig(target_time_seconds=2000, beam_width=20))
    result = router.route(graph, "A", "A")
    assert result.reached_destination is True
    assert result.within_time_budget is True
    assert result.duration_seconds <= 2000
    assert len(result.edges) >= 1
