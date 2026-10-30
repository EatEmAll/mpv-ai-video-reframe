"""Look-ahead Gaussian camera target with a dead zone and media-time slew limit."""

import math

from .curves import soft_shoulder
from .filters import OneEuro


class Planner:
    def __init__(
        self,
        dead_zone=0.04,
        sigma=0.33,
        max_speed=0.6,
        anticipation=0.52,
        shoulder=0.06,
    ):
        self.dead_zone, self.sigma, self.max_speed = dead_zone, sigma, max_speed
        self.samples = []
        self.held = None
        self.last_t = None
        self.last_x = None
        self.causal = OneEuro()
        self.anchor = None
        self.anticipation = anticipation
        self.shoulder = shoulder

    def add(self, t, x, cut=False):
        if self.held is None:
            self.held = x
            self.anchor = x
        if self.samples and not cut:
            previous_t, previous_x, _ = self.samples[-1]
            if 0.10001 < t - previous_t <= 0.5 + 1e-7:
                gap_t = previous_t + 0.1
                while gap_t < t - 1e-7:
                    fraction = (gap_t - previous_t) / (t - previous_t)
                    self.samples.append(
                        (gap_t, previous_x + (x - previous_x) * fraction, False)
                    )
                    gap_t += 0.1
        self.samples.append((t, x, cut))

    def query(self, t):
        if not self.samples:
            return 0.5
        self.samples = [s for s in self.samples if s[0] >= t - 5]
        velocity_samples = [s for s in self.samples if abs(s[0] - t) <= 0.5]
        velocity = 0.0
        if len(velocity_samples) >= 2:
            a, b = velocity_samples[0], velocity_samples[-1]
            velocity = (b[1] - a[1]) / max(b[0] - a[0], 1e-9)
        # Forecast further on a slow walk, but cap projected travel so a fast
        # subject does not leave the crop while the camera leads too far.
        anticipation = min(self.anticipation, 0.02 / max(abs(velocity), 1e-9))
        smooth_time = t + anticipation
        local = [s for s in self.samples if abs(s[0] - smooth_time) <= 3 * self.sigma]
        nearest = (
            min(self.samples, key=lambda s: abs(s[0] - t)) if self.samples else None
        )
        if nearest is None:
            return self.last_x if self.last_x is not None else 0.5
        # Never smooth across a scene cut.
        cuts = [s[0] for s in self.samples if s[2]]
        before = max((c for c in cuts if c <= t), default=-math.inf)
        after = min((c for c in cuts if c > t), default=math.inf)
        local = [s for s in local if before <= s[0] < after]
        weights = [
            (x, math.exp(-0.5 * ((ts - smooth_time) / self.sigma) ** 2))
            for ts, x, _ in local
        ]
        x = (
            sum(x * w for x, w in weights) / sum(w for _, w in weights)
            if weights
            else nearest[1]
        )
        past = [s for s in self.samples if s[0] <= t and s[0] >= before]
        causal_raw = past[-1][1] if past else nearest[1]
        if self.last_t is not None and self.last_t < before <= t:
            self.causal = OneEuro()
        causal = self.causal.query(t, causal_raw)
        lookahead = max(0, self.samples[-1][0] - t)
        blend = min(1, lookahead / (3 * self.sigma + anticipation))
        x = blend * x + (1 - blend) * causal
        # Use coherent motion visible in the look-ahead to leave the dead zone
        # early. Applying a causal 4% hold before smoothing delayed slow walks
        # by a full second and defeated anticipation.
        moving = False
        if len(local) >= 3:
            mean_t = sum(s[0] for s in local) / len(local)
            mean_x = sum(s[1] for s in local) / len(local)
            variance_t = sum((s[0] - mean_t) ** 2 for s in local)
            variance_x = sum((s[1] - mean_x) ** 2 for s in local)
            covariance = sum((s[0] - mean_t) * (s[1] - mean_x) for s in local)
            slope = covariance / max(variance_t, 1e-9)
            r_squared = covariance**2 / max(variance_t * variance_x, 1e-12)
            moving = (abs(slope) > 0.005 and r_squared > 0.6) or max(
                s[1] for s in local
            ) - min(s[1] for s in local) > self.dead_zone + 1e-9
        snap = (
            self.last_t is None
            or t < self.last_t
            or any(self.last_t < c <= t for c in cuts)
        )
        cut_now = self.last_t is not None and any(self.last_t < c <= t for c in cuts)
        if cut_now:
            self.anchor = x
        elif moving:
            # A soft shoulder suppresses tiny anticipatory movements while
            # retaining their onset. A hard hold before filtering delayed slow
            # walks; no shoulder made a static shot visibly drift before motion.
            delta = x - self.anchor
            x = self.anchor + soft_shoulder(delta, self.shoulder)
        if not moving and not cut_now:
            # Continuous hysteresis: crossing the dead-zone edge must not
            # release the entire accumulated offset as a visible camera step.
            delta = x - self.held
            x = self.held + math.copysign(max(0, abs(delta) - self.dead_zone), delta)
        self.held = x
        if not snap:
            delta = self.max_speed * max(0, t - self.last_t)
            x = min(self.last_x + delta, max(self.last_x - delta, x))
        self.last_t, self.last_x = t, x
        return x
