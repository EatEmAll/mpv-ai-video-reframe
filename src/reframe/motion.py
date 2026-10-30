"""Final camera movement: damp detection corrections and limit acceleration."""

import math


class CameraMotion:
    def __init__(self, response=0.25, max_speed=0.45, max_acceleration=1.0):
        self.response = response
        self.omega = 2 / response
        self.max_speed = max_speed
        self.max_acceleration = max_acceleration
        self.position = None
        self.velocity = 0.0
        self.time = None
        self.target = None
        self.target_velocity = 0.0

    def query(self, target, t, snap=False):
        target = min(1, max(0, target))
        if self.position is None or snap or t < self.time:
            self.position, self.velocity, self.time = target, 0.0, t
            self.target, self.target_velocity = target, 0.0
            return target
        dt = max(0, t - self.time)
        self.time = t
        if dt > 0:
            velocity = min(
                self.max_speed, max(-self.max_speed, (target - self.target) / dt)
            )
            self.target_velocity += (velocity - self.target_velocity) * (
                1 - math.exp(-dt / 0.05)
            )
        self.target = target
        # Compensate steady motion without querying/mutating the path planner at
        # a future time. Bound prediction so a noisy detection cannot fling it.
        target += min(0.01, max(-0.01, self.target_velocity * self.response))
        # A delayed loop must not make up a long gap with one abrupt movement.
        remaining = min(dt, 0.1)
        while remaining > 1e-9:
            step = min(remaining, 1 / 120)
            acceleration = (
                self.omega**2 * (target - self.position)
                - 2 * self.omega * self.velocity
            )
            acceleration = min(
                self.max_acceleration, max(-self.max_acceleration, acceleration)
            )
            velocity = min(
                self.max_speed,
                max(-self.max_speed, self.velocity + acceleration * step),
            )
            self.position += (self.velocity + velocity) * step / 2
            self.velocity = velocity
            remaining -= step
        self.position = min(1, max(0, self.position))
        return self.position
