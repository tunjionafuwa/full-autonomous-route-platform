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
