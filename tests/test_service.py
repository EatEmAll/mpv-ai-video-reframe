"""The shared detector service, tested with fake engines (no weights, no GPU)."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from multiprocessing import Pipe
from pathlib import Path
from threading import Thread

import numpy as np
import pytest

from reframe import service
from reframe.security import SecurityError, private_dir
from reframe.service import (
    Service,
    authenticate_client,
    decode_detections,
    decode_frame,
    encode_detections,
    encode_frame,
    wait_ready,
)
from reframe.timeline import Det

KEY = b"k" * 32
APPEARANCE = tuple(i / 128 for i in range(128))


class FakeEngine:
    model_path = Path("models/yolo26n.engine")
    device = 0


class FakeStream:
    def __init__(self):
        self.shapes, self.resets = [], 0

    def detect(self, frame):
        self.shapes.append(frame.shape)
        return [Det(len(self.shapes), (0.1, 0.2, 0.3, 0.4), 0.9, APPEARANCE)]

    def reset(self):
        self.resets += 1


def make_service(load=FakeEngine, idle=0, key=KEY):
    streams = []

    def new_stream(engine):
        streams.append(FakeStream())
        return streams[-1]

    fake = Service(load, new_stream, key, idle)
    fake.streams = streams
    fake.start_loading()
    return fake


def attach(fake, key=KEY):
    """A client connection to the service, without an OS endpoint."""
    server_end, client_end = Pipe()
    Thread(target=fake.handle, args=(server_end,), daemon=True).start()
    authenticate_client(client_end, key)
    return client_end


def remote(fake):
    connection = attach(fake)
    return service.RemoteDetector(
        connection, wait_ready(connection, time.monotonic() + 5)
    )


def test_detections_survive_the_wire_exactly():
    sent = [
        Det(7, (0.1, 0.2, 0.30000000000000004, 0.4), 0.8, APPEARANCE),
        Det(9, (0, 0, 1, 1), 0.25),
    ]
    assert decode_detections(encode_detections(sent)) == sent
    assert decode_detections(encode_detections([])) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m[:-1],
        lambda m: m + b"x",
        lambda m: b"X" + m[1:],
        lambda m: m[:1] + (10**6).to_bytes(4, "little") + m[5:],
    ],
)
def test_malformed_replies_are_rejected(mutate):
    message = encode_detections([Det(1, (0, 0, 1, 1), 0.5, APPEARANCE)])
    with pytest.raises(service.ProtocolError):
        decode_detections(mutate(message))


def test_service_failure_reply_is_raised():
    with pytest.raises(RuntimeError, match="boom"):
        decode_detections(service.FAILURE + b"boom")


def test_frames_round_trip_and_validate():
    frame = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    assert np.array_equal(decode_frame(encode_frame(frame)), frame)
    with pytest.raises(ValueError):
        encode_frame(frame.astype(np.float32))
    with pytest.raises(ValueError):
        encode_frame(np.zeros((service.MAX_DIMENSION + 1, 2, 3), dtype=np.uint8))
    message = encode_frame(frame)
    with pytest.raises(service.ProtocolError):
        decode_frame(message[:-1])
    with pytest.raises(service.ProtocolError):
        decode_frame(service.DETECT + (0).to_bytes(2, "little") * 2)


def test_each_player_gets_its_own_tracker_on_one_engine():
    fake = make_service()
    first, second = remote(fake), remote(fake)
    assert first.model_path == FakeEngine.model_path and first.device == "0"
    assert first.detect(np.zeros((36, 64, 3), np.uint8))[0].track_id == 1
    assert first.detect(np.zeros((36, 64, 3), np.uint8))[0].track_id == 2
    assert second.detect(np.zeros((18, 32, 3), np.uint8))[0].track_id == 1
    first.reset()
    second.reset()
    # A reply proves that player's earlier reset was handled.
    first.detect(np.zeros((36, 64, 3), np.uint8))
    second.detect(np.zeros((18, 32, 3), np.uint8))
    assert [s.resets for s in fake.streams] == [1, 1]
    assert fake.streams[1].shapes == [(18, 32, 3), (18, 32, 3)]
    assert fake.active == 2
    first.close()
    second.close()
    deadline = time.monotonic() + 5
    while fake.active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.active == 0


def test_a_peer_without_the_key_is_rejected():
    fake = make_service()
    with pytest.raises((SecurityError, EOFError, OSError)):
        attach(fake, key=b"x" * 32)
    deadline = time.monotonic() + 5
    while fake.active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.active == 0 and not fake.streams


def test_an_impostor_server_is_rejected_by_the_client():
    server_end, client_end = Pipe()

    def impostor():
        with pytest.raises(SecurityError):
            service.authenticate_server(server_end, b"x" * 32)

    Thread(target=impostor, daemon=True).start()
    with pytest.raises((SecurityError, EOFError, OSError)):
        authenticate_client(client_end, KEY)


def test_oversized_or_inconsistent_requests_drop_only_that_player():
    fake = make_service()
    broken, healthy = remote(fake), remote(fake)
    header = (
        service.DETECT + (4096).to_bytes(2, "little") + (4096).to_bytes(2, "little")
    )
    broken.connection.send_bytes(header + b"short")
    with pytest.raises((EOFError, OSError)):
        broken.connection.recv_bytes()
    assert healthy.detect(np.zeros((4, 4, 3), np.uint8))[0].track_id == 1


def test_load_failure_reaches_waiting_players():
    def failing():
        raise RuntimeError("no CUDA device")

    fake = make_service(load=failing)
    connection = attach(fake)
    with pytest.raises(RuntimeError, match="no CUDA device"):
        wait_ready(connection, time.monotonic() + 5)
    assert fake.run() == service.LOAD_FAILED


def test_idle_service_exits_only_when_no_player_is_connected():
    fake = make_service(idle=1)
    detector = remote(fake)
    outcome = []
    Thread(target=lambda: outcome.append(fake.run()), daemon=True).start()
    time.sleep(1.8)
    assert outcome == []  # a connected player keeps it alive
    detector.close()
    deadline = time.monotonic() + 6
    while not outcome and time.monotonic() < deadline:
        time.sleep(0.05)
    assert outcome == [0]


def test_idle_timeout_zero_never_exits():
    fake = make_service(idle=0)
    outcome = []
    Thread(target=lambda: outcome.append(fake.run()), daemon=True).start()
    time.sleep(1.5)
    assert outcome == []


@pytest.fixture
def runtime(monkeypatch):
    # pytest's temp directory fails the trust checks on Windows (inherited ACLs
    # for other groups), so build the private root where the real one lives.
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    root = private_dir(base / f"mpv-reframe-test-{os.urandom(4).hex()}")
    monkeypatch.setattr(service, "runtime_root", lambda: root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_key_is_created_once_and_shared(runtime):
    keys = []
    threads = [Thread(target=lambda: keys.append(service.load_key())) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(keys) == 6 and len(set(keys)) == 1 and len(keys[0]) == 32
    assert list(p.name for p in runtime.iterdir()) == ["detector.key"]


def test_key_of_the_wrong_size_is_refused(runtime):
    service.load_key()
    (runtime / "detector.key").write_bytes(b"short")
    with pytest.raises(SecurityError):
        service.load_key()


def serve_on_endpoint(print_id, fake):
    listener = service.open_listener(print_id)
    Thread(target=fake.accept_loop, args=(listener,), daemon=True).start()
    return listener


def test_real_endpoint_round_trip(runtime, tmp_path):
    print_id = "t" + tmp_path.name[-12:].replace("_", "x").replace("-", "x")
    key = service.load_key()
    fake = make_service(key=key)
    listener = serve_on_endpoint(print_id, fake)
    try:
        connection = service.open_connection(print_id, key)
        detector = service.RemoteDetector(
            connection, wait_ready(connection, time.monotonic() + 5)
        )
        frame = np.zeros((36, 64, 3), np.uint8)
        assert detector.detect(frame)[0].box == (0.1, 0.2, 0.3, 0.4)
        with pytest.raises(SecurityError):
            service.open_connection(print_id, b"x" * 32)
        detector.close()
    finally:
        listener.close()


def test_connect_starts_the_service_once_and_waits_for_it(
    runtime, tmp_path, monkeypatch
):
    backend = tmp_path / "backend"
    (backend / "models").mkdir(parents=True)
    key = service.load_key()
    print_id = service.fingerprint(backend, None)
    fake = make_service(key=key)
    spawned, listeners = [], []

    class Process:
        def poll(self):
            return None

    def spawn(backend_dir, model, idle, wanted):
        spawned.append((backend_dir, model, idle, wanted))

        def start():
            time.sleep(0.4)  # the interpreter starting up
            listeners.append(serve_on_endpoint(wanted, fake))

        Thread(target=start, daemon=True).start()
        return Process()

    monkeypatch.setattr(service, "spawn_service", spawn)
    detector = service.connect_detector(None, backend, 123, timeout=10)
    try:
        assert spawned == [(backend, None, 123, print_id)]
        assert detector.detect(np.zeros((4, 4, 3), np.uint8))
    finally:
        detector.close()
        [listener.close() for listener in listeners]


def test_connect_reports_a_service_that_dies(runtime, tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    (backend / "models").mkdir(parents=True)

    class Dead:
        def poll(self):
            return 5

    monkeypatch.setattr(service, "spawn_service", lambda *args: Dead())
    with pytest.raises(RuntimeError, match="exited with status 5"):
        service.connect_detector(None, backend, 60, timeout=5)


def test_fingerprint_follows_model_and_interpreter_inputs(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    weights = models / "yolo26n.pt"
    weights.write_bytes(b"a")
    base = service.fingerprint(tmp_path)
    assert service.fingerprint(tmp_path) == base
    assert service.fingerprint(tmp_path, "other.pt") != base
    weights.write_bytes(b"bb")
    assert service.fingerprint(tmp_path) != base
    engine = models / "yolo26n.engine"
    changed = service.fingerprint(tmp_path)
    engine.write_bytes(b"e")
    assert service.fingerprint(tmp_path) != changed


def test_a_player_backend_never_imports_torch():
    code = (
        "import sys, reframe.managed, reframe.controller, reframe.decoder, reframe.service;"
        "service = reframe.service;"
        "service.decode_detections(service.encode_detections([]));"
        "sys.exit(any(name == 'torch' or name.startswith('ultralytics') for name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
