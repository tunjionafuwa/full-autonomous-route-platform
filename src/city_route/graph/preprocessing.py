from __future__ import annotations

import networkx as nx


def prepare_graph(graph: nx.Graph | nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Normalize a graph for routing with direction-aware edge metadata."""
    if isinstance(graph, nx.MultiDiGraph):
        return graph
    converted = nx.MultiDiGraph()
    for u, v, data in graph.edges(data=True):
        converted.add_edge(u, v, **data)
        if not converted.has_edge(v, u):
            converted.add_edge(v, u, **data)
    return converted
