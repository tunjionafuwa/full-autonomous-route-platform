from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoutingConfig:
    target_time_seconds: float = 1800.0
    max_time_seconds: float | None = None
    time_tolerance_ratio: float = 0.05
    time_tolerance_min_seconds: float = 60.0
    pattern_weight: float = 1.2
    destination_weight: float = 1.5
    reuse_penalty: float = 1.5
    turn_angle_target_degrees: float = 90.0
    turn_angle_tolerance_degrees: float = 20.0
    beam_width: int = 12
    max_search_steps: int = 256
    max_reuse_count: int = 2
    allow_reuse: bool = True
    route_min_turns: int = 2
    enable_explainability: bool = True
    random_seed: int | None = None
    min_viable_remaining_time_ratio: float = 0.15
    fallback_reuse_threshold: int = 1
    destination_heuristic_weight: float = 0.7
    fallback_route_strategy: str = "beam_search"
    detail_level: str = "medium"
    geometry_buffer_meters: float = 5.0
    max_parallel_candidates: int = 8
    turn_bonus_weight: float = 0.8
    compactness_weight: float = 0.4
    progress_weight: float = 0.6
    preferred_unused_ratio: float = 0.8
    path_nodes: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.max_time_seconds is None:
            object.__setattr__(self, "max_time_seconds", self.target_time_seconds * 1.5)
