"""Smooth media clock between low-cadence mpv time-pos notifications."""


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
