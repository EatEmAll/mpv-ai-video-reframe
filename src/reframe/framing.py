"""Continuous horizontal box framing within a fixed full-height crop."""

import bisect


def box_at(timeline, t):
    samples = timeline.samples
    if not samples:
        preview = timeline.preview()
        return preview[1] if preview else None
    times = [s[0] for s in samples]
    i = max(0, bisect.bisect_right(times, t) - 1)
    a = samples[i]
    box = timeline.boxes.get(a[0])
    if box is None or a[3] != "tracking" or abs(t - a[0]) > 0.3:
        return None
    if i + 1 < len(samples):
        b = samples[i + 1]
        other = timeline.boxes.get(b[0])
        if other and b[2] == a[2] and b[3] == "tracking" and 0 < b[0] - a[0] <= 0.25:
            u = min(1, max(0, (t - a[0]) / (b[0] - a[0])))
            return tuple(x + (y - x) * u for x, y in zip(box, other))
    return box


def contain_center(center, box, visible_fraction, padding=0.015):
    if box is None or visible_fraction >= 1:
        return center
    left, right = box[0], box[2]
    lo, hi = (
        right + padding - visible_fraction / 2,
        left - padding + visible_fraction / 2,
    )
    if lo > hi:
        if right - left <= visible_fraction:
            # The person fits without the optional padding.
            lo, hi = right - visible_fraction / 2, left + visible_fraction / 2
        else:
            # Keep the entire viewport over the oversized subject where possible.
            # Do not replace a smooth planned path with a noisy detector center.
            lo, hi = left + visible_fraction / 2, right - visible_fraction / 2
    return min(hi, max(lo, center))
