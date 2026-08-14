from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, order=True)
class PhysicalEdgeId:
    """Canonical identity for a physical street segment independent of traversal direction."""

    directionless_key: tuple[str, str]
    osmid: Any | None = None
    key: str | None = None

    @classmethod
    def from_nodes(cls, u: str, v: str, osmid: Any | None = None, key: str | None = None) -> "PhysicalEdgeId":
        ordered = tuple(sorted((str(u), str(v))))
        return cls(directionless_key=ordered, osmid=osmid, key=key)


def physical_edge_id_for(u: str, v: str, edge_key: Any | None = None, osmid: Any | None = None) -> PhysicalEdgeId:
    return PhysicalEdgeId.from_nodes(u, v, osmid=osmid, key=str(edge_key) if edge_key is not None else None)


def edge_usage_key(u: str, v: str, edge_key: Any | None = None) -> tuple[str, str]:
    return physical_edge_id_for(u, v, edge_key).directionless_key
