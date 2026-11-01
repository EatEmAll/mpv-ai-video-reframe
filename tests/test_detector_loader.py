import threading
from types import SimpleNamespace

import pytest

from reframe import detector as detector_module
from reframe import managed
from reframe.detector import DetectorLoader


def test_loader_returns_detector_built_in_background():
    built = []

    def factory(path):
        built.append((path, threading.current_thread().name))
        return "detector"

    loader = DetectorLoader("model.pt", factory=factory)
    assert loader.result() == "detector"
    assert built == [("model.pt", "reframe-model-load")]


def test_loader_reraises_load_failure():
    def factory(path):
        raise RuntimeError("no CUDA")

    loader = DetectorLoader(factory=factory)
    loader.done.wait(5)
    assert isinstance(loader.error, RuntimeError)
    with pytest.raises(RuntimeError, match="no CUDA"):
        loader.result()


def test_loader_wait_is_cancellable():
    release = threading.Event()
    stop = threading.Event()
    loader = DetectorLoader(factory=lambda path: release.wait(5) and "late")
    stop.set()
    assert loader.result(stop) is None
    release.set()
    assert loader.result() == "late"


class FakeSession:
    def __init__(self, args):
        self.stop = threading.Event()
        self.states = []
        self.sent = []

    def state(self, state, reason=""):
        self.states.append(state)

    def send(self, kind, **fields):
        self.sent.append(kind)

    def close(self):
        pass


class FakeIPC:
    def __init__(self, pipe, managed=None):
        pass

    def snapshot(self):
        return {}, 0  # no video is ever opened

    def close(self):
        pass


def test_load_failure_is_reported_without_a_video(monkeypatch, tmp_path):
    from reframe import ipc as ipc_module
    from reframe import runtime

    (tmp_path / "models").mkdir()
    (tmp_path / "models/yolo26n.pt").write_bytes(b"weights")
    monkeypatch.chdir(tmp_path)
    sessions = []

    def session(args):
        sessions.append(FakeSession(args))
        return sessions[-1]

    def failing(path):
        raise RuntimeError("load failed")

    monkeypatch.setattr(managed, "Session", session)
    monkeypatch.setattr(ipc_module, "IPC", FakeIPC)
    monkeypatch.setattr(runtime, "validate_backend_runtime", lambda path: None)
    monkeypatch.setattr(managed, "trusted_path", lambda path: path)
    monkeypatch.setattr(detector_module, "Detector", failing)
    monkeypatch.setattr(
        detector_module,
        "DetectorLoader",
        lambda path: DetectorLoader(path, factory=failing),
    )
    args = SimpleNamespace(backend_dir=str(tmp_path), pipe="test", model=None)
    with pytest.raises(RuntimeError, match="load failed"):
        managed.run_managed(args)
    assert sessions[0].sent == ["error"]
