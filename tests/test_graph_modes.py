import networkx as nx
import pytest

from city_route.graph.loader import ROAD_MODE_HIGHWAY_VALUES, filter_graph_by_road_mode


def build_graph():
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    for node in range(10):
        graph.add_node(node, x=float(node), y=0.0)

    graph.add_edge(0, 1, key="city", highway="residential")
    graph.add_edge(0, 1, key="highway", highway="motorway")
    graph.add_edge(1, 2, key="rural", highway="unclassified")
    graph.add_edge(2, 3, key="rural-link", highway="primary_link")
    graph.add_edge(3, 4, key="highway-link", highway="trunk_link")
    graph.add_edge(4, 5, key="multi-value", highway=["service", "tertiary"])
    graph.add_edge(5, 6, key="secondary-link", highway="secondary_link")
    graph.add_edge(6, 7, key="city-link", highway="living_street")
    graph.add_edge(7, 8, key="unclassified-road", highway="unclassified")
    return graph


@pytest.mark.parametrize(
    ("mode", "expected_keys"),
    [
        ("City", {"city", "multi-value", "city-link"}),
        ("Rural", {"rural", "rural-link", "multi-value", "secondary-link", "unclassified-road"}),
        ("Highway", {"highway", "highway-link"}),
    ],
)
def test_filter_graph_by_road_mode_includes_only_matching_classes(mode, expected_keys):
    filtered = filter_graph_by_road_mode(build_graph(), mode)

    assert {key for _, _, key in filtered.edges(keys=True)} == expected_keys
    assert all(
        any(highway in ROAD_MODE_HIGHWAY_VALUES[mode] for highway in highway_values)
        for _, _, data in filtered.edges(data=True)
        for highway_values in (
            data["highway"] if isinstance(data["highway"], list) else [data["highway"]],
        )
    )
    assert 9 not in filtered


def test_filter_preserves_multigraph_metadata_and_does_not_mutate_source():
    graph = build_graph()
    filtered = filter_graph_by_road_mode(graph, "City")

    assert filtered.graph["crs"] == graph.graph["crs"]
    assert filtered.nodes[0] == graph.nodes[0]
    assert filtered[0][1]["city"]["highway"] == "residential"
    assert set(graph[0][1]) == {"city", "highway"}
    assert 9 in graph and 9 not in filtered


def test_filter_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Choose City, Rural, or Highway"):
        filter_graph_by_road_mode(build_graph(), "Suburban")