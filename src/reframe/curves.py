"""Continuous small-motion shoulder for camera paths."""

import math


def soft_shoulder(delta, width):
    """Suppress tiny offsets without imposing a constant lag on large moves."""
    if not math.isfinite(delta) or not math.isfinite(width) or width < 0:
        raise ValueError("Finite delta and nonnegative width required")
    return delta if width == 0 else delta**3 / (delta * delta + width * width)
