from __future__ import annotations

import math
from typing import Iterable

import networkx as nx


class RoutePatternScorer:
    """Heuristic multi-objective pattern scoring for route geometry."""

    def score(self, nodes: Iterable[str], graph: nx.Graph) -> float:
        nodes = list(nodes)
        if len(nodes) < 3:
            return 0.0

        score = 0.0
        meaningful_turns = 0
        orientation_changes = 0
        last_heading: tuple[float, float] | None = None

        for i in range(1, len(nodes) - 1):
            prev = graph.nodes[nodes[i - 1]]
            curr = graph.nodes[nodes[i]]
            nxt = graph.nodes[nodes[i + 1]]
            dx1 = prev.get("x", 0) - curr.get("x", 0)
            dy1 = prev.get("y", 0) - curr.get("y", 0)
            dx2 = nxt.get("x", 0) - curr.get("x", 0)
            dy2 = nxt.get("y", 0) - curr.get("y", 0)
            if (dx1 == 0 and dy1 == 0) or (dx2 == 0 and dy2 == 0):
                continue

            angle = self._angle(dx1, dy1, dx2, dy2)
            heading = (dx2, dy2)
            if last_heading is not None:
                turn_delta = self._angle(*last_heading, *heading)
                if turn_delta > 45.0:
                    orientation_changes += 1
            last_heading = heading

            if 60 <= angle <= 120:
                meaningful_turns += 1
                score += 12.0
                if 80 <= angle <= 100:
                    score += 8.0
            elif angle > 150:
                score -= 12.0
            elif angle < 30:
                score -= 5.0

        score += min(meaningful_turns * 4.0, 30.0)
        score += self._compactness_bonus(nodes, graph)
        score += self._parallelism_bonus(nodes, graph)
        score += self._rectangularity_bonus(nodes, graph)
        score -= max(0, orientation_changes - 2) * 4.0
        score -= self._axis_repetition_penalty(nodes, graph)

        if nodes[0] == nodes[-1] and len(set(nodes)) >= 4:
            score += 15.0

        return max(0.0, min(score, 100.0))

    def _angle(self, dx1: float, dy1: float, dx2: float, dy2: float) -> float:
        dot = dx1 * dx2 + dy1 * dy2
        mag = math.hypot(dx1, dy1) * math.hypot(dx2, dy2)
        if mag == 0:
            return 0.0
        cos_value = max(-1.0, min(1.0, dot / mag))
        return math.degrees(math.acos(cos_value))

    def _compactness_bonus(self, nodes: list[str], graph: nx.Graph) -> float:
        if len(nodes) < 4:
            return 0.0
        xs = [graph.nodes[n].get("x", 0.0) for n in nodes]
        ys = [graph.nodes[n].get("y", 0.0) for n in nodes]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if width == 0 and height == 0:
            return 0.0
        aspect = min(width, height) / max(width, height)
        return 12.0 * aspect

    def _parallelism_bonus(self, nodes: list[str], graph: nx.Graph) -> float:
        if len(nodes) < 4:
            return 0.0
        score = 0.0
        for i in range(1, len(nodes) - 1):
            prev = graph.nodes[nodes[i - 1]]
            curr = graph.nodes[nodes[i]]
            nxt = graph.nodes[nodes[i + 1]]
            if "x" not in curr or "x" not in nxt:
                continue
            dx1 = prev.get("x", 0) - curr.get("x", 0)
            dy1 = prev.get("y", 0) - curr.get("y", 0)
            dx2 = nxt.get("x", 0) - curr.get("x", 0)
            dy2 = nxt.get("y", 0) - curr.get("y", 0)
            if abs(dx1 * dx2 + dy1 * dy2) > 0:
                score += 1.0
        return min(15.0, score)

    def _rectangularity_bonus(self, nodes: list[str], graph: nx.Graph) -> float:
        if len(nodes) < 4:
            return 0.0
        xs = [graph.nodes[n].get("x", 0.0) for n in nodes]
        ys = [graph.nodes[n].get("y", 0.0) for n in nodes]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if width == 0 or height == 0:
            return 0.0
        aspect_ratio = min(width, height) / max(width, height)
        return 25.0 * aspect_ratio

    def _axis_repetition_penalty(self, nodes: list[str], graph: nx.Graph) -> float:
        if len(nodes) < 4:
            return 0.0
        penalty = 0.0
        for i in range(1, len(nodes) - 1):
            prev = graph.nodes[nodes[i - 1]]
            curr = graph.nodes[nodes[i]]
            nxt = graph.nodes[nodes[i + 1]]
            dx1 = prev.get("x", 0) - curr.get("x", 0)
            dy1 = prev.get("y", 0) - curr.get("y", 0)
            dx2 = nxt.get("x", 0) - curr.get("x", 0)
            dy2 = nxt.get("y", 0) - curr.get("y", 0)
            if abs(dx1) > 0 and abs(dx2) > 0 and abs(dy1) > 0 and abs(dy2) > 0:
                penalty += 1.0
        return min(penalty * 4.0, 25.0)
