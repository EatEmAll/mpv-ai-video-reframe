"""Group framing: pan the fixed full-height crop to fit as many people as possible.

The crop width is fixed by the full-height requirement (``panscan=1``), so the
only free variable is the horizontal centre. A person counts as framed when at
least ``coverage_floor`` of their box width lies inside the crop window. The
most confident person is still framed when their box is wider than the crop and
cannot meet the floor: the crop is then kept inside their box to show as much of
them as possible.
"""

import bisect
from dataclasses import dataclass

from .timeline import iou

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


def _best_effort_interval(box, visible):
    """Centres that show as much as possible of a box wider than the crop.

    Such a box cannot reach the coverage floor. Keeping the whole crop inside
    the box maximises the visible fraction at ``visible / width``. When the box
    runs past the frame edge, the best available crop is at that edge.
    """
    left, right = box[0], box[2]
    if right - left <= 0:
        return None
    lo = max(left + visible / 2, visible / 2)
    hi = min(right - visible / 2, 1 - visible / 2)
    if lo <= hi + 1e-9:
        return (lo, hi)
    point = visible / 2 if right - visible / 2 < visible / 2 else 1 - visible / 2
    return (point, point)


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


def dedupe(people, prefer=(), overlap=0.6):
    """Keep one detection per person when the tracker split them across IDs.

    Boxes that overlap by more than ``overlap`` (IoU) are the same person; the
    one already in ``prefer`` is kept, then the most confident. Order is kept.
    """
    people = list(people)
    ranked = sorted(
        range(len(people)),
        key=lambda i: (people[i].track_id in prefer, people[i].confidence),
        reverse=True,
    )
    kept = []
    for i in ranked:
        if all(iou(people[i].box, people[k].box) <= overlap for k in kept):
            kept.append(i)
    return [people[i] for i in sorted(kept)]


def _vector(people):
    """Confidences, highest first: sets compare by who they include."""
    return tuple(sorted((d.confidence for d in people), reverse=True))


def _balanced_center(members, lo, hi, middle, visible):
    """The centre in ``[lo, hi]`` that keeps the least visible person most visible.

    A group that fits entirely sits at ``middle``. When the coverage floor was
    relaxed to include someone, the best centre balances their coverage.
    """
    middle = min(hi, max(lo, middle))
    points = [middle]
    if hi - lo > 1e-9:
        points += [lo + (hi - lo) * k / 32 for k in range(33)]

    def quality(x):
        return round(min(coverage(d.box, x, visible) for d in members), 3)

    return max(points, key=lambda x: (quality(x), -abs(x - middle)))


def clearly_better(best, held, margin=0.1):
    """Whether ``best`` includes more confident people than ``held``.

    Confidences within ``margin`` of each other are detector noise. Past that,
    a more confident person decides, then the number of people.
    """
    for a, b in zip(_vector(best), _vector(held)):
        if a > b + margin:
            return True
        if a < b - margin:
            return False
    return len(best) > len(held)


def fit(people, visible, floor, previous=None, relaxed=None, pair=None):
    """Choose the set of people to frame, favouring the most confident.

    ``people`` is a list of ``Det``-like objects. A person counts as framed when
    at least ``relaxed`` of their box (``floor`` when not given) can share one
    crop with the others, and the crop is placed to keep the least visible of
    them as visible as possible. The two most confident people need only
    ``pair`` of their box (``relaxed`` when not given), so a close-up of two
    people is framed on the seam between them instead of on one. The most
    confident person is always framed, even when their box is wider than the
    crop. Sets rank by their confidences,
    highest first, so a more confident person beats any number of weaker ones;
    then by the higher summed ``confidence * area`` and the set nearest
    ``previous``. Returns ``(center, framed, bounds)`` where ``framed`` is the
    chosen subset and ``bounds`` the centre range in which every framed person
    keeps the relaxed floor (or, for a wider anchor, the whole crop stays inside
    their box). Returns ``None`` when nobody can be framed.
    """
    people = list(people)
    if not people:
        return None
    relaxed = floor if relaxed is None else min(relaxed, floor)
    pair = relaxed if pair is None else min(pair, relaxed)
    top_confidence = max(d.confidence for d in people)
    order = sorted(range(len(people)), key=lambda i: -people[i].confidence)
    leading = set(order[:2])
    intervals = []
    for i, det in enumerate(people):
        interval = coverage_interval(
            det.box, visible, pair if i in leading else relaxed
        )
        if interval is None:
            if det.confidence < top_confidence - 1e-9:
                continue
            interval = _best_effort_interval(det.box, visible)
            if interval is None:
                continue
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
        center = _balanced_center(members, lo, hi, (span[0] + span[2]) / 2, visible)
        distance = abs(center - previous) if previous is not None else 0.0
        rank = (
            _vector(members),
            round(sum(_score(d) for d in members), 9),
            -distance,
        )
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
        margin=0.1,
        relaxed_floor=0.5,
        pair_floor=0.2,
        slack=0.8,
        grace=0.3,
    ):
        if not 0 <= conf_floor <= 1:
            raise ValueError("conf_floor must be within [0, 1]")
        if not 0 < coverage_floor <= 1:
            raise ValueError("coverage_floor must be within (0, 1]")
        if not 0 < relaxed_floor <= 1 or not 0 < pair_floor <= 1:
            raise ValueError("relaxed_floor and pair_floor must be within (0, 1]")
        self.conf_floor = conf_floor
        self.coverage_floor = coverage_floor
        # Coverage a person may drop to so that a more confident group still
        # fits (``pair`` for the two most confident people, who are framed
        # together even in a tight crop); the crop keeps everyone as visible as
        # it can.
        self.relaxed = min(relaxed_floor, coverage_floor)
        self.pair = min(pair_floor, self.relaxed)
        self.join, self.leave = join, leave
        # A different set must include more confident people (beyond ``margin``
        # of detector noise) or more people for ``switch`` seconds before the
        # camera leaves a still-framable set; noise otherwise hops between
        # equally good groups.
        self.switch, self.margin = switch, margin
        # ``slack`` scales every coverage bar for the group already framed;
        # ``grace`` is how long a better group may drop out without losing its
        # progress toward ``switch``.
        self.slack, self.grace = slack, grace
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
        self.challenger_seen = None
        # Raw inputs and the crop width they were decided at, so a resize can
        # re-decide the samples that are already queued ahead of the playhead.
        self.history = []
        self.visible = None

    def replay(self, visible):
        """Re-decide every retained sample for a new crop width.

        Returns ``(t, x, is_cut)`` for each rebuilt sample, ready for a planner.
        """
        history, self.history = self.history, []
        self.samples, self.boxes, self.framed, self.info = [], {}, {}, {}
        self.reset()
        rebuilt = []
        previous = None
        for t, detections, cut, fallback in history:
            sample = self.add(t, detections, visible, cut=cut, fallback=fallback)
            rebuilt.append(
                (
                    t,
                    sample[1],
                    bool(
                        cut
                        and previous is not None
                        and abs(sample[1] - previous) > 0.25
                    ),
                )
            )
            previous = sample[1]
        self.visible = visible
        return rebuilt

    def reset(self):
        self.members = {}
        self.last_x = None
        self.last_seen = None
        self.current = ()
        self.challenger = None
        self.challenger_since = None

    def _choose(self, t, active, visible):
        best = fit(
            active, visible, self.coverage_floor, self.last_x, self.relaxed, self.pair
        )
        if best is None:
            self.current, self.challenger = (), None
            return None
        best_ids = tuple(d.track_id for d in best[1])
        kept = [d for d in active if d.track_id in self.current]
        held = None
        if self.current and len(kept) == len(self.current):
            # A group already framed gets some slack so that box jitter around
            # a marginal fit does not drop it for a frame and restart the
            # switch delay.
            s = self.slack
            held = fit(
                kept,
                visible,
                self.coverage_floor * s,
                self.last_x,
                self.relaxed * s,
                self.pair * s,
            )
            if held is not None and len(held[1]) != len(kept):
                held = None  # the current group no longer fits together
        if held is None:
            self.current, self.challenger = best_ids, None
            return best
        if best_ids == self.current:
            # A better group that drops out for a frame or two keeps its timer.
            if self.challenger is not None and t - self.challenger_seen > self.grace:
                self.challenger = None
            return best
        if clearly_better(best[1], held[1], self.margin):
            # The delay runs while *a* clearly better group exists, not one
            # exact set: tracker ID churn changes which better set is best from
            # frame to frame without making any of them worse than ``held``.
            if self.challenger is None:
                self.challenger_since = t
            self.challenger = best_ids
            self.challenger_seen = t
            if t - self.challenger_since >= self.switch - 1e-7:
                self.current, self.challenger = best_ids, None
                return best
        elif self.challenger is not None and t - self.challenger_seen > self.grace:
            self.challenger = None
        return held

    def add(self, t, detections, visible, cut=False, fallback=None):
        """Add one detector sample; ``fallback`` is follow mode's x for ``t``."""
        self.history.append((t, detections, cut, fallback))
        self.visible = visible
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
        active = dedupe(
            [m.det for m in self.members.values() if m.active], prefer=self.current
        )
        result = self._choose(t, active, visible)
        if result is not None:
            x, framed, _ = result
            ids = tuple(sorted(d.track_id for d in framed))
            boxes = tuple(d.box for d in framed)
            confidences = tuple(d.confidence for d in framed)
            state = "group"
            self.last_x, self.last_seen = x, t
        else:
            ids, boxes, confidences = (), (), ()
            if fallback is not None:
                x, state = fallback, "fallback"
            elif self.last_x is None:
                x, state = 0.5, "searching"
            else:
                u = min(1, max(0, (t - self.last_seen - 1) / 2))
                x = self.last_x + (0.5 - self.last_x) * u * u * (3 - 2 * u)
                state = "hold" if u == 0 else "lost"
        unframed = [d for i, d in eligible.items() if i not in ids]
        self.info[t] = {
            "candidates": len(eligible),
            "framed_confidence": confidences,
            "unframed": tuple(d.box for d in unframed),
            "unframed_confidence": tuple(d.confidence for d in unframed),
            "unframed_ids": tuple(d.track_id for d in unframed),
            # Eligible but still inside the join delay, so not yet considered.
            "pending_ids": tuple(i for i in eligible if not self.members[i].active),
        }
        sample = (t, x, ids, state)
        self.samples.append(sample)
        self.framed[t] = boxes
        self.boxes[t] = union(boxes)
        return sample

    def prune(self, playhead):
        keep = playhead - 5
        self.samples = [s for s in self.samples if s[0] >= keep]
        self.history = [h for h in self.history if h[0] >= keep]
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
        intervals = []
        for b in boxes:
            interval = coverage_interval(b, visible, floor)
            if interval is None:
                interval = _best_effort_interval(b, visible)
            intervals.append(interval)
        if any(i is None for i in intervals):
            return center
        lo, hi = max(i[0] for i in intervals), min(i[1] for i in intervals)
        if lo > hi:
            return center
    return min(hi, max(lo, center))
