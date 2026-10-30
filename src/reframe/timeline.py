"""Generation-local target selection evaluated in detection timestamp order."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Det:
    track_id: int
    box: tuple[float, float, float, float]
    confidence: float
    appearance: tuple[float, ...] = ()

    @property
    def center(self):
        return (self.box[0] + self.box[2]) / 2


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )

    def area(x):
        return max(0, x[2] - x[0]) * max(0, x[3] - x[1])

    return intersection / max(1e-9, area(a) + area(b) - intersection)


class Timeline:
    def __init__(self, selection_window=0.5):
        self.target_id = None
        self.last = None
        self.last_seen = None
        self.samples = []
        self.template = ()
        self.selection_window = selection_window
        self.initialized = selection_window <= 0
        self.pending = []
        self.boxes = {}
        self.repick_pending = []

    def preview(self):
        """Aim at the priority sample while the definitive pick is buffering.

        This does not commit an identity or enter the smoothed timeline. A seek
        can restore the crop on its first detection without waiting for the
        complete half-second selection window.
        """
        if self.initialized or not self.pending:
            return None
        t, detections = self.pending[-1]
        chosen = max(
            detections,
            key=lambda d: (
                d.confidence
                * (d.box[2] - d.box[0])
                * (d.box[3] - d.box[1])
                * (1 - abs(d.center - 0.5))
            ),
            default=None,
        )
        if chosen is None:
            return None
        return (t, chosen.center, None, "buffering"), chosen.box

    def similarity(self, det):
        if not self.template or not det.appearance:
            return 1.0
        return sum(math.sqrt(a * b) for a, b in zip(self.template, det.appearance))

    def finalize(self):
        """Select from available frames when a clip ends before the initial window."""
        if self.initialized or not self.pending:
            return None
        return self._finish_initial()

    def _finish_initial(self):
        scores = {}
        for _, items in self.pending:
            for det in items:
                scores[det.track_id] = scores.get(det.track_id, 0) + det.confidence * (
                    det.box[2] - det.box[0]
                ) * (det.box[3] - det.box[1]) * (1 - abs(det.center - 0.5))
        self.target_id = max(scores, key=scores.get, default=None)
        self.initialized = True
        pending, self.pending = self.pending, []
        for pts, items in pending:
            self.add(pts, [d for d in items if d.track_id == self.target_id])
        return self.samples[-1]

    def add(self, t, detections, cut=False):
        if not self.initialized and not cut:
            self.pending.append((t, detections))
            if t - self.pending[0][0] < self.selection_window - 1e-7:
                return None
            return self._finish_initial()
        if cut:
            self.initialized = True
            self.target_id = None
            self.last = None
            self.last_seen = None
            self.template = ()
            self.repick_pending = []
        chosen = next((d for d in detections if d.track_id == self.target_id), None)
        # A raw ByteTrack ID can attach to the other person after an occlusion.
        # Keep an appearance anchor instead of blindly following that ID.
        if chosen is not None and self.similarity(chosen) < 0.85:
            chosen = None
        if chosen is None and self.template:
            matches = [d for d in detections if self.similarity(d) >= 0.85]
            chosen = max(matches, key=self.similarity, default=None)
        if chosen is None and self.last is not None and t - self.last_seen <= 0.5:
            matches = [
                d
                for d in detections
                if iou(d.box, self.last.box) > 0.3 and self.similarity(d) >= 0.85
            ]
            chosen = max(matches, key=lambda d: iou(d.box, self.last.box), default=None)
        repicked = False
        if chosen is None and (self.last_seen is None or t - self.last_seen > 1):
            eligible = [
                d
                for d in detections
                if not self.template
                or self.similarity(d) >= 0.85
                or t - self.last_seen > 3
            ]
            if eligible:
                if self.repick_pending and t - self.repick_pending[-1][0] > 0.5:
                    self.repick_pending = []
                self.repick_pending.append((t, eligible))
                if cut or t - self.repick_pending[0][0] >= self.selection_window - 1e-7:
                    scores = {d.track_id: 0.0 for d in eligible}
                    for _, candidates in self.repick_pending:
                        for d in candidates:
                            if d.track_id in scores:
                                scores[d.track_id] += (
                                    d.confidence
                                    * (d.box[2] - d.box[0])
                                    * (d.box[3] - d.box[1])
                                    * (1 - abs(d.center - 0.5))
                                )
                    pick = max(scores, key=scores.get)
                    chosen = next(d for d in eligible if d.track_id == pick)
                    repicked = True
            else:
                self.repick_pending = []
        if chosen:
            if not self.template or repicked:
                self.template = chosen.appearance
            self.repick_pending = []
            self.last, self.last_seen, self.target_id = chosen, t, chosen.track_id
            x, state = chosen.center, "tracking"
        elif self.last is None:
            x, state = 0.5, "searching"
        else:
            u = min(1, max(0, (t - self.last_seen - 1) / 2))
            x = self.last.center + (0.5 - self.last.center) * u * u * (3 - 2 * u)
            state = "hold" if u == 0 else "lost"
        sample = (t, x, self.target_id, state)
        self.samples.append(sample)
        self.boxes[t] = chosen.box if chosen else (self.last.box if self.last else None)
        return sample

    def prune(self, playhead):
        self.samples = [s for s in self.samples if s[0] >= playhead - 5]
        self.boxes = {t: b for t, b in self.boxes.items() if t >= playhead - 5}
