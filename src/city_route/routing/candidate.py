from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateEdge:
    u: str
    v: str
    edge_key: str | None
    edge_id: str
    travel_time: float
    distance: float
    score: float = 0.0
    reused: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
