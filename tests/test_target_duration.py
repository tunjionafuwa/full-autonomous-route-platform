import pytest
import networkx as nx

from city_route.config import RoutingConfig
from city_route.routing.router import RoutePlanner


def build_simple_graph():
    """Create a simple graph with controllable edge times."""
    graph = nx.MultiDiGraph()
    
    # Create a linear path: A -> B -> C -> D -> E
    edges = [
        ("A", "B", 10.0),  # 10 seconds
        ("B", "C", 10.0),
        ("C", "D", 10.0),
        ("D", "E", 10.0),
    ]
    
    # Add reverse edges for undirected travel
    for u, v, time in edges:
        graph.add_edge(u, v, length=1000.0, speed_kph=30.0, travel_time=time)
        graph.add_edge(v, u, length=1000.0, speed_kph=30.0, travel_time=time)
    
    # Add some alternative paths with waypoints
    # Path: A -> B -> X -> C (taking longer)
    graph.add_edge("B", "X", length=1000.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("X", "C", length=1000.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("X", "B", length=1000.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("C", "X", length=1000.0, speed_kph=30.0, travel_time=10.0)
    
    # Path: A -> Y -> B (taking longer)
    graph.add_edge("A", "Y", length=1000.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("Y", "B", length=1000.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("B", "Y", length=1000.0, speed_kph=30.0, travel_time=10.0)
    graph.add_edge("Y", "A", length=1000.0, speed_kph=30.0, travel_time=10.0)
    
    return graph


def add_timed_path(graph, nodes, duration):
    edge_time = duration / (len(nodes) - 1)
    for start, end in zip(nodes, nodes[1:]):
        graph.add_edge(start, end, length=1000.0, speed_kph=30.0, travel_time=edge_time)


def test_target_duration_routing_short():
    """Test that short target duration returns fast route."""
    graph = build_simple_graph()
    # Shortest path A->E is 40 seconds (4 edges * 10 seconds)
    router = RoutePlanner(RoutingConfig(target_time_seconds=40, max_search_steps=100))
    result = router.route(graph, "A", "E")
    
    assert result.reached_destination is True
    assert result.nodes[-1] == "E"
    # Should return something close to shortest path (40 seconds)
    assert abs(result.duration_seconds - 40) <= 5, f"Expected ~40 seconds, got {result.duration_seconds}"


def test_target_duration_routing_medium():
    """Test that medium target duration returns longer route."""
    graph = build_simple_graph()
    # Shortest path A->E is 40 seconds
    # Target 70 seconds should use waypoint detours
    router = RoutePlanner(RoutingConfig(target_time_seconds=70, max_search_steps=100))
    result = router.route(graph, "A", "E")
    
    assert result.reached_destination is True
    assert result.nodes[-1] == "E"
    # Should return something close to target (70 seconds)
    # The algorithm should find routes longer than 40 seconds
    assert result.duration_seconds > 40, f"Expected > 40 seconds, got {result.duration_seconds}"


@pytest.mark.parametrize(
    ("overshoot_seconds", "undershoot_seconds"),
    [(63.0, 57.0), (61.0, 58.0)],
)
def test_target_duration_prefers_undershoot_over_overshoot(
    overshoot_seconds, undershoot_seconds, monkeypatch
):
    graph = build_simple_graph()
    overshoot = ["A", "OVER-1", "OVER-2", "E"]
    undershoot = ["A", "UNDER-1", "UNDER-2", "E"]
    add_timed_path(graph, overshoot, overshoot_seconds)
    add_timed_path(graph, undershoot, undershoot_seconds)

    router = RoutePlanner(RoutingConfig(target_time_seconds=60, max_search_steps=10))
    monkeypatch.setattr(
        router,
        "_generate_diverse_candidates",
        lambda *_args: [overshoot, undershoot],
    )

    result = router.route(graph, "A", "E")

    assert result.nodes == undershoot
    assert result.duration_seconds == pytest.approx(undershoot_seconds)


def test_target_duration_searches_when_shortest_path_is_close_below_target(monkeypatch):
    graph = nx.MultiDiGraph()
    shortest = ["A", "BASE", "E"]
    closer = ["A", "ALT-1", "ALT-2", "E"]
    add_timed_path(graph, shortest, 57.0)
    add_timed_path(graph, closer, 59.0)

    router = RoutePlanner(RoutingConfig(target_time_seconds=60, max_search_steps=10))
    monkeypatch.setattr(router, "_generate_diverse_candidates", lambda *_args: [closer])

    result = router.route(graph, "A", "E")

    assert result.nodes == closer
    assert result.duration_seconds == pytest.approx(59.0)


def test_target_duration_routing_loop():
    """Test that loop routing targets the requested duration."""
    graph = build_simple_graph()
    # For a 70 second loop starting from A
    router = RoutePlanner(RoutingConfig(target_time_seconds=70, max_search_steps=100))
    result = router.route(graph, "A", "A")
    
    assert result.loop is True
    assert result.nodes[0] == "A"
    assert result.nodes[-1] == "A"
    # Should try to reach ~70 seconds
    assert result.duration_seconds > 40, f"Expected > 40 seconds, got {result.duration_seconds}"


def test_loop_duration_penalty_accounts_for_return_path(monkeypatch):
    graph = nx.MultiDiGraph()
    edges = [
        ("A", "S", 50.0),
        ("S", "A", 1.0),
        ("S", "B", 46.0),
        ("B", "A", 1.0),
        ("S", "C", 51.0),
        ("C", "A", 2.0),
    ]
    for start, end, travel_time in edges:
        graph.add_edge(start, end, length=1000.0, travel_time=travel_time)

    router = RoutePlanner(RoutingConfig(
        target_time_seconds=100,
        max_time_seconds=200,
        max_search_steps=10,
        allow_reuse=False,
    ))
    monkeypatch.setattr(
        router,
        "_path_score",
        lambda _graph, path: 100.0 if "C" in path else 0.0,
    )

    result = router.route(graph, "A", "A")

    assert result.nodes == ["A", "S", "B", "A"]
    assert result.duration_seconds == pytest.approx(97.0)


def test_target_duration_impossible_short():
    """Test that impossible target (too short) returns shortest path."""
    graph = build_simple_graph()
    # Shortest A->E is 40 seconds, request 5 seconds (impossible)
    router = RoutePlanner(RoutingConfig(target_time_seconds=5, max_search_steps=100))
    result = router.route(graph, "A", "E")
    
    assert result.reached_destination is True
    # Should return shortest path since target is impossible
    assert result.duration_seconds >= 40, f"Got {result.duration_seconds}, expected >= 40"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
