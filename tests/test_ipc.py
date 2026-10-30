from collections import deque
from threading import Lock

import pytest

from reframe.ipc import IPC


class FakeMPV:
    def __init__(self):
        self.pending = []

    def command_async(self, *args, callback):
        self.pending.append((args, callback))


def test_only_one_inflight_and_retry_latest():
    ipc = IPC.__new__(IPC)
    ipc.lock = Lock()
    ipc.generation = 0
    ipc.inflight = False
    ipc.last_align = None
    ipc.error = None
    ipc.roundtrips = deque(maxlen=120)
    ipc.latency_seconds = 0.0
    ipc.sent_count = 0
    ipc.last_sent = 0.0
    ipc.mpv = FakeMPV()
    assert ipc.set_align(0.2)
    assert not ipc.set_align(0.7)
    assert len(ipc.mpv.pending) == 1
    ipc.mpv.pending[0][1](None, None)
    assert ipc.set_align(0.7)
    ipc.mpv.pending[1][1](None, None)
    assert ipc.last_align == 0.7
    assert ipc.set_align(0.7001)
    assert len(ipc.mpv.pending) == 2


def test_forced_refresh_still_obeys_the_playback_frame_ceiling(monkeypatch):
    from reframe import ipc as module

    wall = [10.0]
    monkeypatch.setattr(module.time, "perf_counter", lambda: wall[0])
    ipc = IPC.__new__(IPC)
    ipc.lock = Lock()
    ipc.generation = 0
    ipc.inflight = False
    ipc.last_align = None
    ipc.error = None
    ipc.roundtrips = deque(maxlen=120)
    ipc.latency_seconds = 0
    ipc.sent_count = 0
    ipc.last_sent = 0
    ipc.mpv = FakeMPV()
    assert ipc.set_align(0.2, min_interval=0.1)
    ipc.mpv.pending[-1][1](None, None)
    wall[0] = 10.05
    assert not ipc.set_align(0.2, force=True, min_interval=0.1)
    wall[0] = 10.2
    assert ipc.set_align(0.2, force=True, min_interval=0.1)
    assert ipc.sent_count == 2


def test_old_generation_completion_does_not_set_new_alignment():
    ipc = IPC.__new__(IPC)
    ipc.lock = Lock()
    ipc.generation = 0
    ipc.inflight = False
    ipc.last_align = None
    ipc.error = None
    ipc.roundtrips = deque(maxlen=120)
    ipc.latency_seconds = 0
    ipc.sent_count = 0
    ipc.last_sent = 0
    ipc.mpv = FakeMPV()
    assert ipc.set_align(0.2)
    ipc.generation += 1
    ipc.mpv.pending[-1][1](None, None)
    assert not ipc.inflight
    assert ipc.last_align is None
    assert not ipc.roundtrips


@pytest.mark.parametrize("timing", [0, 0.05, 0.083])
@pytest.mark.parametrize("managed", [False, True])
def test_active_crop_preserves_configured_timing_and_restores_snapshot(timing, managed):
    from threading import Event
    from types import SimpleNamespace

    properties = {
        "panscan": 0.2,
        "video-align-x": 0.1,
        "cache": "auto",
        "video-timing-offset": timing,
        "keepaspect-window": True,
    }
    calls = []

    def command(*args):
        calls.append(args)
        if args[0] == "get_property":
            return properties[args[1]]
        if args[0] == "set_property":
            properties[args[1]] = args[2]

    ipc = IPC.__new__(IPC)
    ipc.lock, ipc.cache, ipc.generation = Lock(), {"path": "local.mp4"}, 1
    ipc.closed, ipc.inflight, ipc.enabled = Event(), False, False
    ipc.saved = {
        name: properties[name]
        for name in ("panscan", "video-align-x", "cache", "video-timing-offset")
    }
    acquired = []
    ipc.managed = (
        SimpleNamespace(
            acquire=lambda saved: acquired.append(saved.copy()),
            send=lambda *a, **kw: None,
            epoch=1,
        )
        if managed
        else None
    )
    ipc.mpv = SimpleNamespace(command=command)
    ipc.enable()
    assert properties["video-timing-offset"] == timing
    assert not any(c[:2] == ("set_property", "video-timing-offset") for c in calls)
    assert properties["panscan"] == 1
    if managed:
        assert acquired[0]["video-timing-offset"] == timing
    properties["video-timing-offset"] = 0.123
    ipc.restore()
    assert properties["video-timing-offset"] == timing
    assert properties["panscan"] == 0.2
