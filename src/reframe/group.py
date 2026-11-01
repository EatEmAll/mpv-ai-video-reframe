"""Group framing: pan the fixed full-height crop to fit as many people as possible.

The crop width is fixed by the full-height requirement (``panscan=1``), so the
only free variable is the horizontal centre. A person counts as framed when at
least ``coverage_floor`` of their box width lies inside the crop window.
"""

import bisect
from dataclasses import dataclass

MODES = ("follow", "group")


def coverage(box, center, visible):
    """Fraction of ``box`` width inside the window centred at ``center``."""
    width = box[2] - box[0]
    if width <= 0:
        return 0.0
    inside = min(box[2], center + visible / 2) - max(box[0], center - visible / 2)
    return max(0.0, inside) / width


def coverage_interval(box, visible, floor):
    """Centres at which ``box`` meets the coverage floor, or ``None``.

    The window's right edge must reach ``floor`` of the way into the box and
    its left edge must not pass the matching point from the right.
    """
    left, right = box[0], box[2]
    width = right - left
    if width <= 0 or floor * width > visible + 1e-9:
        return None
    lo = left + floor * width - visible / 2
    hi = right - floor * width + visible / 2
    if visible < 1:
        # The crop cannot leave the frame (align is clamped to [-1, 1]).
        lo, hi = max(lo, visible / 2), min(hi, 1 - visible / 2)
    else:
        lo, hi = 0.5, 0.5
    return (lo, hi) if lo <= hi + 1e-9 else None


def _score(det):
    box = det.box
    return det.confidence * (box[2] - box[0]) * (box[3] - box[1])


def union(boxes):
    boxes = list(boxes)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def fit(people, visible, floor, previous=None):
    """Choose the largest set of people that can share one crop position.

    ``people`` is a list of ``Det``-like objects. Returns ``(center, framed,
    bounds)`` where ``framed`` is the chosen subset and ``bounds`` the centre
    range in which every framed person keeps the coverage floor. Ties prefer the
    higher summed ``confidence * area``, then the set nearest ``previous``.
    Returns ``None`` when nobody can be framed.
    """
    intervals = []
    for det in people:
        interval = coverage_interval(det.box, visible, floor)
        if interval is not None:
            intervals.append((det, interval))
    if not intervals:
        return None
    best = None
    seen = set()
    # A maximal set of overlapping intervals always contains the lower edge
    # of one of its intervals, so those points are sufficient candidates.
    for _, (point, _) in intervals:
        chosen = tuple(
            k
            for k, (_, (lo, hi)) in enumerate(intervals)
            if lo - 1e-9 <= point <= hi + 1e-9
        )
        if chosen in seen:
            continue
        seen.add(chosen)
        members = [intervals[k][0] for k in chosen]
        lo = max(intervals[k][1][0] for k in chosen)
        hi = min(intervals[k][1][1] for k in chosen)
        span = union(d.box for d in members)
        center = min(hi, max(lo, (span[0] + span[2]) / 2))
        distance = abs(center - previous) if previous is not None else 0.0
        rank = (len(members), round(sum(_score(d) for d in members), 9), -distance)
        if best is None or rank > best[0]:
            best = (rank, center, members, (lo, hi))
    _, center, members, bounds = best
    return center, sorted(members, key=lambda d: d.track_id), bounds


@dataclass
class _Member:
    det: object
    since: float
    seen: float
    active: bool = False


class GroupTimeline:
    """Per-sample group framing with membership hysteresis.

    Mirrors ``Timeline``'s ``samples``/``boxes`` interface: ``samples`` holds
    ``(t, x, member_ids, state)`` and ``boxes[t]`` the union of framed boxes,
    so the planner and ``box_at``-style lookups work for either mode.
    """

    def __init__(
        self,
        conf_floor=0.5,
        coverage_floor=0.7,
        join=0.3,
        leave=0.7,
        switch=0.6,
        margin=1.25,
    ):
        if not 0 <= conf_floor <= 1:
            raise ValueError("conf_floor must be within [0, 1]")
        if not 0 < coverage_floor <= 1:
            raise ValueError("coverage_floor must be within (0, 1]")
        self.conf_floor = conf_floor
        self.coverage_floor = coverage_floor
        self.join, self.leave = join, leave
        # A different set must be better (more people, or the same number with
        # ``margin`` times the score) for ``switch`` seconds before the camera
        # leaves a still-framable set; detector noise otherwise hops between
        # equally sized groups.
        self.switch, self.margin = switch, margin
        self.members = {}
        self.samples = []
        self.boxes = {}
        self.framed = {}
        self.info = {}
        self.last_x = None
        self.last_seen = None
        self.current = ()
        self.challenger = None
        self.challenger_since = None

    def reset(self):
        self.members = {}
        self.last_x = None
        self.last_seen = None
        self.current = ()
        self.challenger = None
        self.challenger_since = None

    def _choose(self, t, active, visible):
        best = fit(active, visible, self.coverage_floor, self.last_x)
        if best is None:
            self.current, self.challenger = (), None
            return None
        best_ids = tuple(d.track_id for d in best[1])
        kept = [d for d in active if d.track_id in self.current]
        held = None
        if self.current and len(kept) == len(self.current):
            held = fit(kept, visible, self.coverage_floor, self.last_x)
            if held is not None and len(held[1]) != len(kept):
                held = None  # the current group no longer fits together
        if held is None or best_ids == self.current:
            self.current, self.challenger = best_ids, None
            return best
        better = len(best[1]) > len(held[1]) or sum(map(_score, best[1])) > (
            self.margin * sum(map(_score, held[1]))
        )
        if not better:
            self.challenger = None
            return held
        if self.challenger != best_ids:
            self.challenger, self.challenger_since = best_ids, t
        if t - self.challenger_since < self.switch - 1e-7:
            return held
        self.current, self.challenger = best_ids, None
        return best

    def add(self, t, detections, visible, cut=False, fallback=None):
        """Add one detector sample; ``fallback`` is follow mode's x for ``t``."""
        if cut:
            self.reset()
        eligible = {
            d.track_id: d
            for d in detections
            if d.confidence >= self.conf_floor and d.box[2] > d.box[0]
        }
        bootstrap = not any(m.active for m in self.members.values())
        for track_id, det in eligible.items():
            member = self.members.get(track_id)
            if member is None:
                member = self.members[track_id] = _Member(det, t, t)
            member.det, member.seen = det, t
            if not member.active and (
                bootstrap or t - member.since >= self.join - 1e-7
            ):
                member.active = True
        for track_id, member in list(self.members.items()):
            if track_id not in eligible and t - member.seen > self.leave + 1e-7:
                del self.members[track_id]
        # Hold briefly-missed members at their last box so a dropped detection
        # does not pull the camera away and back.
        active = [m.det for m in self.members.values() if m.active]
        result = self._choose(t, active, visible)
        if result is not None:
            x, framed, _ = result
            ids = tuple(sorted(d.track_id for d in framed))
            boxes = tuple(d.box for d in framed)
            state = "group"
            self.last_x, self.last_seen = x, t
        else:
            ids, boxes = (), ()
            if fallback is not None:
                x, state = fallback, "fallback"
            elif self.last_x is None:
                x, state = 0.5, "searching"
            else:
                u = min(1, max(0, (t - self.last_seen - 1) / 2))
                x = self.last_x + (0.5 - self.last_x) * u * u * (3 - 2 * u)
                state = "hold" if u == 0 else "lost"
        framed_ids = set(ids)
        self.info[t] = {
            "candidates": len(eligible),
            "unframed": tuple(
                d.box for i, d in eligible.items() if i not in framed_ids
            ),
        }
        sample = (t, x, ids, state)
        self.samples.append(sample)
        self.framed[t] = boxes
        self.boxes[t] = union(boxes)
        return sample

    def prune(self, playhead):
        keep = playhead - 5
        self.samples = [s for s in self.samples if s[0] >= keep]
        for table in (self.boxes, self.framed, self.info):
            for t in [t for t in table if t < keep]:
                del table[t]


def framed_at(group, t):
    """Framed boxes for the sample at or before ``t``; interpolate one step.

    Interpolation applies only between consecutive samples with the same member
    set, matching ``framing.box_at`` for follow mode.
    """
    samples = group.samples
    if not samples:
        return ()
    times = [s[0] for s in samples]
    i = max(0, bisect.bisect_right(times, t) - 1)
    a = samples[i]
    boxes = group.framed.get(a[0], ())
    if not boxes or a[3] != "group" or abs(t - a[0]) > 0.3:
        return ()
    if i + 1 < len(samples):
        b = samples[i + 1]
        other = group.framed.get(b[0], ())
        # Framed boxes are ordered by track ID, so equal ID tuples align.
        if b[2] == a[2] and b[3] == "group" and 0 < b[0] - a[0] <= 0.25:
            u = min(1, max(0, (t - a[0]) / (b[0] - a[0])))
            return tuple(
                tuple(p + (q - p) * u for p, q in zip(x, y))
                for x, y in zip(boxes, other)
            )
    return boxes


def contain_group(center, boxes, visible, floor, padding=0.015):
    """Keep every framed person visible: fully when the group fits, else at the floor."""
    if not boxes or visible >= 1:
        return center
    span = union(boxes)
    if span[2] - span[0] + 2 * padding <= visible:
        lo, hi = span[2] + padding - visible / 2, span[0] - padding + visible / 2
    elif span[2] - span[0] <= visible:
        lo, hi = span[2] - visible / 2, span[0] + visible / 2
    else:
        intervals = [coverage_interval(b, visible, floor) for b in boxes]
        if any(i is None for i in intervals):
            return center
        lo, hi = max(i[0] for i in intervals), min(i[1] for i in intervals)
        if lo > hi:
            return center
    return min(hi, max(lo, center))
