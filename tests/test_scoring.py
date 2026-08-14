import networkx as nx

from city_route.geometry.patterns import RoutePatternScorer


def test_rectangular_route_scores_higher_than_zigzag():
    graph = nx.Graph()
    graph.add_weighted_edges_from([
        ("A", "B", 1),
        ("B", "C", 1),
        ("C", "D", 1),
        ("D", "A", 1),
        ("B", "E", 1),
        ("E", "F", 1),
        ("F", "C", 1),
        ("A", "G", 1),
        ("G", "H", 1),
        ("H", "D", 1),
    ])
    coords = {
        "A": (0.0, 0.0),
        "B": (0.0, 1.0),
        "C": (1.0, 1.0),
        "D": (1.0, 0.0),
        "E": (0.0, 2.0),
        "F": (1.0, 2.0),
        "G": (-1.0, 0.0),
        "H": (-1.0, 1.0),
    }
    for node, (x, y) in coords.items():
        graph.nodes[node]["x"] = x
        graph.nodes[node]["y"] = y

    square_nodes = ["A", "B", "C", "D", "A"]
    zigzag_nodes = ["A", "B", "E", "F", "C", "D", "A"]
    scorer = RoutePatternScorer()
    square_score = scorer.score(square_nodes, graph)
    zigzag_score = scorer.score(zigzag_nodes, graph)
    assert square_score > zigzag_score
