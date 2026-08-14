#!/usr/bin/env python3
"""Debug script to test routing with various target durations."""

from city_route.config import RoutingConfig
from city_route.graph.loader import add_travel_attributes, load_city_graph
from city_route.graph.preprocessing import prepare_graph
from city_route.routing.router import RoutePlanner
import osmnx as ox

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
    except Exception as exc:
        raise ValueError(f"Could not resolve '{query}' to a graph node. Use a place name or coordinates like '40.7128,-74.0060'.") from exc


def test_durations(target_durations):
    """Test routing with different target durations."""
    print("Loading graph...")
    graph = load_city_graph("Manhattan, New York City, NY, USA")
    print(f"  Graph has {len(graph)} nodes and {sum(1 for _ in graph.edges())} edges")
    
    print("Adding travel attributes...")
    graph = add_travel_attributes(graph)
    
    print("Preparing graph...")
    graph = prepare_graph(graph)
    
    print("Resolving start and end nodes...")
    start = resolve_graph_node(graph, "Times Square")
    end = resolve_graph_node(graph, "Brooklyn Bridge")
    
    print(f"Start: {start}, End: {end}\n")
    
    results = []
    for target_minutes in target_durations:
        target_seconds = target_minutes * 60
        print(f"Testing target duration: {target_minutes} minutes ({target_seconds} seconds)")
        
        config = RoutingConfig(
            target_time_seconds=target_seconds,
            max_search_steps=300
        )
        planner = RoutePlanner(config)
        route = planner.route(graph, start, end)
        
        estimated_minutes = route.duration_seconds / 60
        time_diff_minutes = estimated_minutes - target_minutes
        time_error_percent = abs(time_diff_minutes) / target_minutes * 100 if target_minutes > 0 else 0
        
        result_dict = {
            "target_minutes": target_minutes,
            "estimated_minutes": estimated_minutes,
            "difference_minutes": time_diff_minutes,
            "error_percent": time_error_percent,
            "distance_km": route.distance_m / 1000,
            "num_edges": len(route.edges),
            "nodes": route.nodes[:5] + (["..."] if len(route.nodes) > 5 else [])
        }
        results.append(result_dict)
        
        print(f"  Requested: {target_minutes} min")
        print(f"  Estimated: {estimated_minutes:.1f} min")
        print(f"  Difference: {time_diff_minutes:+.1f} min")
        print(f"  Error: {time_error_percent:.1f}%")
        print(f"  Distance: {route.distance_m / 1000:.2f} km")
        print(f"  Edges: {len(route.edges)}")
        print()
    
    print("\nSummary:")
    print("Target (min) | Estimated (min) | Error (%) | Distance (km) | Edges")
    print("-" * 75)
    for r in results:
        print(f"{r['target_minutes']:12} | {r['estimated_minutes']:15.1f} | {r['error_percent']:9.1f} | {r['distance_km']:13.2f} | {r['num_edges']}")


if __name__ == "__main__":
    test_durations([15, 30, 60, 90, 122, 150])
