"""Pure display-axis transforms for encoded rotation metadata."""

import numpy as np


def rotate_frame(frame, clockwise_degrees):
    angle = clockwise_degrees % 360
    if angle not in (0, 90, 180, 270):
        raise ValueError("Only right-angle video rotations are supported")
    return np.ascontiguousarray(np.rot90(frame, k=-angle // 90)) if angle else frame


def display_point(x, y, clockwise_degrees):
    angle = clockwise_degrees % 360
    match angle:
        case 0:
            return x, y
        case 90:
            return 1 - y, x
        case 180:
            return 1 - x, 1 - y
        case 270:
            return y, 1 - x
        case _:
            raise ValueError("Only right-angle video rotations are supported")
