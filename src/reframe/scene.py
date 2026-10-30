"""Channel-aware cheap cut descriptor (permuted colours must differ)."""

import numpy as np


def descriptor(frame):
    sampled = frame[::8, ::8]
    hist = np.concatenate(
        [np.histogram(sampled[:, :, c], bins=16, range=(0, 256))[0] for c in range(3)]
    ).astype(float)
    return hist / hist.sum()


def is_cut(previous, current):
    return previous is not None and float(np.abs(current - previous).sum()) > 0.8
