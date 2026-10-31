"""Drive the real controller/sender with deterministic late mpv replies."""

import json
from collections import deque
from contextlib import nullcontext
from queue import Queue
from threading import Lock
from types import SimpleNamespace

import pytest

from reframe import controller
from reframe.ipc import IPC


@pytest.mark.parametrize("speed,ceiling", [(0.5, 15), (1, 30), (2, 60), (4, 60)])
def test_late_pan_reply_does_not_cost_another_output_frame(
    monkeypatch, tmp_path, speed, ceiling
):
    now = [100.0]
    pending = []
    sends = []
    waits = []

    class Wake:
        def __init__(self, interrupt=False):
            self.interrupt = interrupt
            self.signaled = False

        def is_set(self):
            return False

        def set(self):
            self.signaled = True

        def clear(self):
            self.signaled = False

        def wait(self, timeout):
            waits.append(timeout)
            if self.signaled:
                return
            deadline = now[0] + timeout
            if pending and pending[0][0] <= deadline:
                now[0], callback = pending.pop(0)
                callback(None, None)
                if not (self.interrupt and self.signaled):
                    now[0] = deadline
            else:
                now[0] = deadline

    def command_async(*args, callback):
        sends.append(now[0])
        # Reply just after the next output deadline. Previously this made
        # set_align fail and the controller sleep another entire frame.
        pending.append((now[0] + 1 / ceiling + 0.001, callback))

    ipc = IPC.__new__(IPC)
    ipc.managed, ipc.owner = None, None
    ipc.closed, ipc.changed, ipc.lock = Wake(), Wake(True), Lock()
    ipc.generation, ipc.inflight, ipc.last_align = 0, False, None
    ipc.error, ipc.latency_seconds = None, 0
    ipc.roundtrips = deque(maxlen=120)
    ipc.sent_count, ipc.last_sent = 0, 0
    ipc.mpv = SimpleNamespace(command_async=command_async)
    state = {
        "path": __file__,
        "time-pos": 0,
        "speed": speed,
        "display-fps": 60,
        "container-fps": 30,
        "osd-dimensions": dict(w=608, h=1080, ml=-656, mr=-656, mt=0, mb=0),
    }

    def snapshot():
        return {**state, "time-pos": (now[0] - 100) * speed}, 0

    ipc.snapshot, ipc.close = snapshot, lambda: None
    worker = SimpleNamespace(
        start=lambda: None,
        close=lambda: None,
        update=lambda *a: None,
        output=Queue(),
        stats={},
        error=None,
    )
    timeline = SimpleNamespace(
        samples=[(0, 0.3, 1, "tracking")],
        pending=[],
        boxes={},
        prune=lambda t: None,
        preview=lambda: None,
    )
    planner = SimpleNamespace(query=lambda t: 0.3 + t * 0.03, samples=[])
    monkeypatch.setattr(controller.time, "perf_counter", lambda: now[0])
    monkeypatch.setattr(controller.time, "time", lambda: (-1) ** len(sends) * 1e9)
    monkeypatch.setattr(controller, "frame_timer", nullcontext)
    monkeypatch.setattr(controller, "Worker", lambda *a: worker)
    monkeypatch.setattr(controller, "Timeline", lambda: timeline)
    monkeypatch.setattr(controller, "Planner", lambda: planner)
    controller.run(None, None, seconds=1, ipc=ipc, output_dir=tmp_path)
    gaps = [b - a for a, b in zip(sends, sends[1:])]
    assert len(gaps) >= ceiling * 0.8
    assert min(gaps) >= 1 / ceiling - 1e-10
    assert max(gaps) <= 1 / ceiling + 0.0011
    # A low update ceiling must not postpone close/stop/heartbeat checks.
    assert max(waits) <= 0.02 + 1e-10
    records = [
        json.loads(line)
        for line in (tmp_path / "telemetry.jsonl").read_text().splitlines()
    ]
    assert [r["pan_sent_at"] for r in records if r["sent"]] == sends
