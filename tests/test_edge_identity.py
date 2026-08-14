import networkx as nx

from city_route.graph.edge_identity import PhysicalEdgeId, physical_edge_id_for


def test_edge_identity_reuses_reverse_direction():
    edge_a = physical_edge_id_for("A", "B", None)
    edge_b = physical_edge_id_for("B", "A", None)
    assert edge_a == edge_b
    assert edge_a.directionless_key == ("A", "B")


def test_parallel_edges_have_distinct_physical_identity():
    graph = nx.MultiDiGraph()
    graph.add_edge("A", "B", key="main", length=10)
    graph.add_edge("A", "B", key="alternate", length=9)

    ids = {
        physical_edge_id_for("A", "B", key)
        for key in graph["A"]["B"].keys()
    }
    assert len(ids) == 2
    assert len({edge.directionless_key for edge in ids}) == 1
