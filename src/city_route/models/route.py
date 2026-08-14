from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RouteDecision:
    node: str
    edge_id: str
    travel_time: float
    distance: float
    turn_angle: float | None
    pattern_contribution: float
    destination_contribution: float
    edge_reuse_status: str
    total_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteResult:
    nodes: list[str]
    edges: list[str]
    distance_m: float
    duration_seconds: float
    pattern_score: float
    reused_edges: list[str]
    reused_edge_count: int
    reached_destination: bool
    within_time_budget: bool
    loop: bool = False
    explanation: list[RouteDecision] = field(default_factory=list)
    elapsed_time: float = 0.0
    feasible: bool = True

    @property
    def unique_edge_count(self) -> int:
        return len(set(self.edges))

    @property
    def edge_count(self) -> int:
        return len(self.edges)
