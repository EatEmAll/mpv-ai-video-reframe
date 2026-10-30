from collections import deque
from threading import Lock

from reframe.ipc import IPC


class FakeMPV:
    def __init__(self):
        self.pending = []

    def command_async(self, *args, callback):
        self.pending.append((args, callback))


def test_only_one_inflight_and_retry_latest():
    ipc = IPC.__new__(IPC)
    ipc.lock = Lock()
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
