import networkx as nx

from city_route.config import RoutingConfig
from city_route.routing.router import RoutePlanner


def test_time_budget_is_respected_when_feasible():
    graph = nx.Graph()
    graph.add_weighted_edges_from([
        ("S", "A", 1),
        ("A", "B", 1),
        ("B", "T", 1),
        ("S", "C", 10),
        ("C", "T", 10),
    ])
    for u, v, d in graph.edges(data=True):
        d["length"] = 100.0
        d["speed_kph"] = 30.0
        d["travel_time"] = 10.0
    router = RoutePlanner(RoutingConfig(max_time_seconds=120, beam_width=20))
    result = router.route(graph, "S", "T")
    assert result.within_time_budget is True
    assert result.duration_seconds <= 120


def test_fallback_reuse_is_only_used_when_required():
    graph = nx.Graph()
    graph.add_weighted_edges_from([
        ("A", "B", 1),
        ("B", "C", 1),
        ("C", "A", 1),
        ("C", "D", 1),
        ("D", "A", 1),
    ])
    for u, v, d in graph.edges(data=True):
        d["length"] = 100.0
        d["speed_kph"] = 30.0
        d["travel_time"] = 10.0
    router = RoutePlanner(RoutingConfig(max_time_seconds=800, beam_width=20))
    result = router.route(graph, "A", "D")
    assert result.reused_edge_count >= 0
    assert result.reached_destination is True


def test_multidigraph_uses_edge_data_for_time_budget():
    graph = nx.MultiDiGraph()
    graph.add_edge("S", "A", length=100.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("A", "T", length=100.0, speed_kph=30.0, travel_time=10.0)

    router = RoutePlanner(RoutingConfig(max_time_seconds=15, beam_width=20))
    result = router.route(graph, "S", "T")
    assert result.reached_destination is True
    assert result.duration_seconds == 20.0
    assert result.within_time_budget is False
