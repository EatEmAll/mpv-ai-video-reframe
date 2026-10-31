"""Observed property cache and one-in-flight crop commands."""

import time
from collections import deque
from threading import Event, Lock

from .transport import connect_mpv

PROPERTIES = (
    "current-tracks/video",
    "working-directory",
    "time-pos",
    "pause",
    "speed",
    "seeking",
    "path",
    "duration",
    "video-params",
    "osd-dimensions",
    "panscan",
    "video-zoom",
    "video-unscaled",
    "video-align-x",
    "display-fps",
    "estimated-vf-fps",
    "container-fps",
)


class ViewChanged(RuntimeError):
    """Playback changed while Lua acknowledged an acquisition."""


class IPC:
    def __init__(self, pipe, managed=None):
        self.managed = managed
        self.closed = Event()
        self.changed = Event()
        self.lock = Lock()
        self.cache = {}
        self.generation = 0
        self.inflight = False
        self.last_align = None
        self.error = None
        self.roundtrips = deque(maxlen=120)
        self.latency_seconds = 0.0
        self.restart_pending = False
        self.enabled = False
        self.sent_count = 0
        self.last_sent = 0.0
        self.mpv = connect_mpv(
            pipe,
            player_pid=getattr(managed, "player_pid", None),
            quit_callback=self.closed.set,
        )
        self.owner = None
        if managed is None:
            from .managed import Ownership

            try:
                self.owner = Ownership(self.mpv.command("get_property", "pid"))
            except Exception:
                self.mpv.terminate()
                raise
        self.saved = {
            name: self.mpv.command("get_property", name)
            for name in ("panscan", "video-align-x", "cache", "video-timing-offset")
        }
        for name in PROPERTIES:
            self.mpv.bind_property_observer(name, self._property)
        for name in ("seek", "file-loaded", "end-file"):
            self.mpv.bind_event(name, self._reset)
        self.mpv.bind_event("playback-restart", self._restart)
        if managed is None:
            self.enable()

    def enable(self):
        if self.managed:
            before, generation = self.snapshot()
            self.saved = {
                name: self.mpv.command("get_property", name)
                for name in (*self.saved, "keepaspect-window")
            }
            self.managed.acquire(self.saved)
            after, current_generation = self.snapshot()
            if generation != current_generation or before.get("path") != after.get(
                "path"
            ):
                self.managed.send("released", view_epoch=self.managed.epoch)
                raise ViewChanged("Playback changed during view acquisition")
            self.enabled = True
            self.mpv.command("set_property", "keepaspect-window", False)
        # Preserve mpv/user render scheduling and its frame-health budget.
        self.mpv.command("set_property", "cache", "yes")
        self.mpv.command("set_property", "panscan", 1)
        self.last_align = None
        self.enabled = True

    def restore(self):
        if not self.enabled:
            return
        deadline = time.perf_counter() + 1
        while self.inflight and time.perf_counter() < deadline:
            time.sleep(0.005)
        if self.inflight:
            raise TimeoutError("Crop command did not drain")
        if not self.closed.is_set():
            self.mpv.command("osd-overlay", 0, "ass-events", "")
            for name, value in self.saved.items():
                self.mpv.command("set_property", name, value)
        self.last_align = None
        self.enabled = False
        if self.managed:
            self.managed.send("released", view_epoch=self.managed.epoch)

    def _property(self, name, value):
        with self.lock:
            if name == "seeking" and value is False and self.restart_pending:
                return
            self.cache[name] = value
        self.changed.set()

    def _reset(self, event):
        if event.get("event") == "end-file":
            # With keep-open, the final frame may still be visible and its
            # inference may be finishing. The next file-loaded/seek invalidates
            # the generation; EOF itself must not strand a short-clip buffer.
            self.changed.set()
            return
        with self.lock:
            self.generation += 1
            self.restart_pending = True
            self.cache["seeking"] = True
        self.changed.set()

    def _restart(self, event):
        # The seek event can precede its time-pos property notification. Reading
        # the settled position here prevents reopening at the old position under
        # the new generation and then throttling forever ahead of a backward seek.
        try:
            position = self.mpv.command("get_property", "time-pos")
        except Exception as error:
            self.error = str(error)
            return
        with self.lock:
            self.cache["time-pos"] = position
            self.cache["seeking"] = False
            self.restart_pending = False
            self.generation += 1
        self.changed.set()

    def snapshot(self):
        with self.lock:
            return self.cache.copy(), self.generation

    def set_align(self, value, force=False, min_interval=0.0, tolerance=0.002):
        with self.lock:
            if self.inflight:
                return False
            if (
                not force
                and self.last_align is not None
                and abs(value - self.last_align) <= tolerance
            ):
                return True
            if time.perf_counter() - self.last_sent < min_interval:
                return False
            self.inflight = True
            generation = self.generation

        def done(error, result):
            with self.lock:
                self.inflight = False
                self.changed.set()
                if generation != self.generation:
                    return
                if error is None:
                    self.last_align = value
                    self.roundtrips.append(time.perf_counter() - sent_at)
                    ordered = sorted(self.roundtrips)
                    self.latency_seconds = ordered[
                        min(len(ordered) - 1, int(0.95 * len(ordered)))
                    ]
                else:
                    self.error = str(error)

        try:
            sent_at = time.perf_counter()
            self.last_sent = sent_at
            self.sent_count += 1
            self.mpv.command_async(
                "set_property", "video-align-x", value, callback=done
            )
            return True
        except Exception:
            with self.lock:
                self.inflight = False
            raise

    def close(self):
        try:
            self.restore()
        finally:
            self.mpv.terminate()
            if self.owner:
                self.owner.close()
