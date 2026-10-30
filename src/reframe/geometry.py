"""Geometry derived from mpv's reported pre-clipping OSD margins."""

import math


def align_for_center(p: float, width: float, scaled: float) -> float:
    if (
        not all(math.isfinite(v) for v in (p, width, scaled))
        or width <= 0
        or scaled <= 0
    ):
        raise ValueError("Invalid video geometry")
    if scaled - width < 1:
        return 0.0
    return 2 * min(1.0, max(0.0, (p * scaled - width / 2) / (scaled - width))) - 1


def visible_center(width: float, scaled: float, margin: float) -> float:
    return (width / 2 - margin) / scaled
