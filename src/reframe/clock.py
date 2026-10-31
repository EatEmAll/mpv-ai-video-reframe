"""Smooth media clock between low-cadence mpv time-pos notifications."""

import math


class PlaybackClock:
    def __init__(self):
        self.value = None
        self.wall = None
        self.observed = None
        self.anchor_wall = None
        self.generation = None

    def query(self, position, wall, speed=1, paused=False, generation=0):
        reset = self.value is None or generation != self.generation
        elapsed = 0 if self.wall is None else max(0, wall - self.wall)
        if reset or paused:
            self.value = position
            self.anchor_wall = wall
        else:
            self.value += elapsed * speed
        if reset or position != self.observed:
            self.observed = position
            self.anchor_wall = wall
        if not paused and not reset:
            desired = position + min(max(0, wall - self.anchor_wall), 0.25) * speed
            # Gradual phase correction avoids a visible jump at each source frame.
            weight = min(1, elapsed / 0.15)
            self.value += (desired - self.value) * weight
            # A stalled player must not send the camera arbitrarily far ahead.
            self.value = min(self.value, position + 0.25 * speed)
        self.wall = wall
        self.generation = generation
        return self.value


class PresentationClock:
    """Slew render/IPC prediction changes without inventing a backward seek.

    Both clocks take monotonic seconds. An IPC percentile can fall abruptly as
    old replies leave its window; adding it directly to media time reset both
    Planner and CameraMotion mid-pan. Playback discontinuities are explicit.
    """

    def __init__(self, response=0.15):
        self.response = response
        self.value = None
        self.wall = None
        self.generation = None

    def query(self, media, wall, lead=0, speed=1, paused=False, generation=0):
        desired = media + (0 if paused else lead)
        if self.value is None or generation != self.generation or paused:
            self.value = desired
        else:
            elapsed = max(0, wall - self.wall)
            advance = elapsed * speed
            predicted = self.value + advance
            correction = (desired - predicted) * -math.expm1(-elapsed / self.response)
            step = min(1.5 * advance, max(0, advance + correction))
            # Preserve the stalled-player bound and never rewind to compensate
            # for a new latency estimate or a speed decrease.
            self.value = max(self.value, min(self.value + step, desired + 0.25 * speed))
        self.wall, self.generation = wall, generation
        return self.value
