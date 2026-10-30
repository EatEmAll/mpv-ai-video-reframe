"""Causal One-Euro fallback for a temporarily empty look-ahead buffer."""

import math


class OneEuro:
    def __init__(self, min_cutoff=1.0, beta=0.5, derivative_cutoff=1.0):
        self.min_cutoff, self.beta, self.derivative_cutoff = (
            min_cutoff,
            beta,
            derivative_cutoff,
        )
        self.t = None
        self.raw = None
        self.value = None
        self.derivative = 0.0

    @staticmethod
    def alpha(cutoff, dt):
        tau = 1 / (2 * math.pi * cutoff)
        return 1 / (1 + tau / dt)

    def query(self, t, x):
        if self.t is None or t < self.t:
            self.t, self.raw, self.value, self.derivative = t, x, x, 0.0
            return x
        dt = t - self.t
        if dt <= 0:
            return self.value
        derivative = (x - self.raw) / dt
        a = self.alpha(self.derivative_cutoff, dt)
        self.derivative += a * (derivative - self.derivative)
        cutoff = self.min_cutoff + self.beta * abs(self.derivative)
        self.value += self.alpha(cutoff, dt) * (x - self.value)
        self.t, self.raw = t, x
        return self.value
