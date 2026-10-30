from types import SimpleNamespace

import pytest

from reframe import timing


def test_fine_timer_is_released_even_when_tracking_fails(monkeypatch):
    events = []

    class Timer:
        def timeBeginPeriod(self, period):
            events.append(("begin", period))
            return 0

        def timeEndPeriod(self, period):
            events.append(("end", period))

    monkeypatch.setattr(timing, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(timing.ctypes, "WinDLL", lambda name: Timer(), raising=False)
    with pytest.raises(ValueError), timing.frame_timer():
        raise ValueError("sidecar failed")
    assert events == [("begin", 1), ("end", 1)]


def test_background_timer_policy_is_restored(monkeypatch):
    events = []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def get(process, info, pointer, size):
        pointer._obj.ControlMask = 1
        pointer._obj.StateMask = 1
        return 1

    def set_state(process, info, pointer, size):
        events.append((pointer._obj.ControlMask, pointer._obj.StateMask))
        return 1

    kernel = SimpleNamespace(
        GetCurrentProcess=Function(lambda: -1),
        GetProcessInformation=Function(get),
        SetProcessInformation=Function(set_state),
    )
    monkeypatch.setattr(timing, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(timing.ctypes, "WinDLL", lambda _: kernel, raising=False)
    with pytest.raises(ValueError), timing.honor_timer_resolution():
        assert events == [(5, 1)]
        raise ValueError("failure")
    assert events == [(5, 1), (1, 1)]
