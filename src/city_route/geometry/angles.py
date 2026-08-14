from __future__ import annotations

import math


def angle_between_vectors(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    mag = math.hypot(v1[0], v1[1]) * math.hypot(v2[0], v2[1])
    if mag == 0:
        return 0.0
    cos_theta = max(-1.0, min(1.0, dot / mag))
    return math.degrees(math.acos(cos_theta))


def turn_angle_degrees(prev_vec: tuple[float, float], next_vec: tuple[float, float]) -> float:
    angle = angle_between_vectors(prev_vec, next_vec)
    return min(angle, 180.0 - angle) if angle > 90.0 else angle
