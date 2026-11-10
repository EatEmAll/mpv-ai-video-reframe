"""A per-user detector service that every player shares.

Importing torch, creating the CUDA context and loading the engine takes seconds
(much longer on a cold disk) and holds gigabytes. The first player starts one
background process that loads the model; each player's backend then connects to
it, so later players, and the first one after it is warm, skip the load. Every
player keeps its own ByteTrack state in the service. Only inference is
serialized, so parallel players take turns on the GPU.

Messages are length-prefixed bytes with a fixed binary layout, never pickle.
Peers prove they hold the per-user key with a mutual HMAC challenge before either
side sends anything else; the key lives in the private per-user runtime
directory. Same-account processes and administrators are outside the isolation
boundary, as everywhere else in this project.
"""

import hashlib
import hmac
import json
import os
import stat
import struct
import subprocess
import sys
import time
import traceback
from multiprocessing.connection import Client, Listener
from pathlib import Path
from threading import Event, Lock, Thread

from .security import (
    SecurityError,
    runtime_root,
    strict_json,
    trusted_path,
)

PROTOCOL = 1
MAX_DIMENSION = 4096
MAX_REQUEST = 5 + MAX_DIMENSION * MAX_DIMENSION * 3
MAX_REPLY = 1 << 20
MAX_DETECTIONS = 1000
MAX_APPEARANCE = 1024
AUTH_TIMEOUT = 5.0
DETECT_TIMEOUT = 30.0
CONNECT_TIMEOUT = 60.0
READY_TIMEOUT = 600.0
LOG_LIMIT = 1 << 20
LOCK_BUSY = 3
LOAD_FAILED = 5

DETECT, RESET, HELLO, FAILURE = b"D", b"R", b"H", b"E"
DETECTION = struct.Struct("<q5dH")


class ProtocolError(RuntimeError):
    pass


# --- identity ---------------------------------------------------------------


def owner_token():
    if os.name == "nt":
        from .windows_security import user_sid

        return user_sid()
    return str(os.getuid())


def fingerprint(backend_dir, model=None):
    """Name the service by everything that decides what it would run.

    A player whose code, interpreter, backend directory or model files differ
    from those of a running service gets a different endpoint and so a fresh
    service; the old one exits when idle instead of serving stale code.
    """
    root = Path(backend_dir).resolve()
    package = Path(__file__).parent
    digest = hashlib.sha256()

    def add(*parts):
        for part in parts:
            digest.update(str(part).encode("utf-8", "surrogateescape"))
            digest.update(b"\0")

    add(PROTOCOL, owner_token(), root, os.path.normcase(sys.executable), model or "")
    for path in sorted(package.rglob("*")):
        if path.suffix in {".py", ".yaml"} and "__pycache__" not in path.parts:
            add(
                path.relative_to(package).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    files = [root / "models" / name for name in ("yolo26n.pt", "yolo26n.engine")]
    files.append(root / "models" / "yolo26n.engine.json")
    if model:
        files += [root / model, root / (str(model) + ".json")]
    for path in files:
        try:
            info = path.stat()
            add(path, info.st_size, info.st_mtime_ns)
        except OSError:
            add(path, "missing")
    return digest.hexdigest()[:16]


def endpoint(print_id):
    """Return (address, family) for this service."""
    if os.name == "nt":
        return "\\\\.\\pipe\\mpv-reframe-detector-" + print_id, "AF_PIPE"
    address = str(runtime_root() / f"detector-{print_id}.sock")
    if len(os.fsencode(address)) > 100:
        raise SecurityError("Detector socket path exceeds the portable 100-byte limit")
    return address, "AF_UNIX"


def log_path(print_id):
    return runtime_root() / f"detector-{print_id}.log"


def load_key():
    """Read the shared secret, creating it atomically if nobody has yet."""
    root = runtime_root()
    path = root / "detector.key"
    if not path.exists():
        temporary = root / f".detector-key-{os.getpid()}-{os.urandom(4).hex()}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(temporary, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(os.urandom(32))
            try:
                os.link(temporary, path)  # exclusive: the first creator wins
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    trusted_path(path, private=True)
    key = path.read_bytes()
    if len(key) != 32:
        raise SecurityError("Detector key is malformed; delete it to create a new one")
    return key


# --- authentication ---------------------------------------------------------


def _proof(key, role, first, second):
    return hmac.new(key, role + first + second, "sha256").digest()


def _receive(connection, limit, timeout=AUTH_TIMEOUT):
    if not connection.poll(timeout):
        raise TimeoutError("Detector peer did not answer in time")
    return connection.recv_bytes(limit)


def authenticate_server(connection, key):
    nonce = os.urandom(32)
    connection.send_bytes(b"RF" + bytes([PROTOCOL]) + nonce)
    reply = _receive(connection, 64)
    if len(reply) != 64:
        raise SecurityError("Malformed detector authentication")
    client_nonce, proof = reply[:32], reply[32:]
    if not hmac.compare_digest(proof, _proof(key, b"client", nonce, client_nonce)):
        # Answer with noise so a client holding a stale key fails with a clear
        # error instead of a dropped connection it would mistake for a restart.
        connection.send_bytes(os.urandom(32))
        raise SecurityError("Detector authentication failed")
    connection.send_bytes(_proof(key, b"server", client_nonce, nonce))


def authenticate_client(connection, key):
    greeting = _receive(connection, 35)
    if len(greeting) != 35 or greeting[:3] != b"RF" + bytes([PROTOCOL]):
        raise SecurityError("Unexpected detector service protocol")
    nonce, client_nonce = greeting[3:], os.urandom(32)
    connection.send_bytes(client_nonce + _proof(key, b"client", nonce, client_nonce))
    proof = _receive(connection, 32)
    if not hmac.compare_digest(proof, _proof(key, b"server", client_nonce, nonce)):
        raise SecurityError("Detector service failed authentication")


def verify_peer(connection):
    """On Unix, also require the peer to be this user (Windows relies on the key)."""
    if os.name == "nt":
        return
    import socket

    from .transport import verify_unix_peer

    peer = socket.fromfd(connection.fileno(), socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        verify_unix_peer(peer)
    finally:
        peer.close()


# --- message layout ---------------------------------------------------------


def encode_frame(frame):
    if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype.str != "|u1":
        raise ValueError("Frames must be height x width x 3 uint8 arrays")
    height, width = frame.shape[:2]
    if not (1 <= height <= MAX_DIMENSION and 1 <= width <= MAX_DIMENSION):
        raise ValueError(f"Frame size {width}x{height} exceeds {MAX_DIMENSION}")
    return DETECT + struct.pack("<HH", height, width) + frame.tobytes()


def decode_frame(message):
    import numpy as np

    if len(message) < 5 or message[:1] != DETECT:
        raise ProtocolError("Malformed detect request")
    height, width = struct.unpack_from("<HH", message, 1)
    if not (1 <= height <= MAX_DIMENSION and 1 <= width <= MAX_DIMENSION):
        raise ProtocolError("Frame size out of range")
    if len(message) != 5 + height * width * 3:
        raise ProtocolError("Frame length does not match its size")
    return np.frombuffer(message, np.uint8, offset=5).reshape(height, width, 3).copy()


def encode_detections(detections):
    parts = [DETECT, struct.pack("<I", len(detections))]
    for item in detections:
        appearance = tuple(item.appearance)
        parts.append(
            DETECTION.pack(item.track_id, *item.box, item.confidence, len(appearance))
        )
        parts.append(struct.pack(f"<{len(appearance)}d", *appearance))
    return b"".join(parts)


def decode_detections(message):
    from .timeline import Det

    if message[:1] == FAILURE:
        raise RuntimeError(
            "Detector service failed: " + message[1:1025].decode("utf-8", "replace")
        )
    if len(message) < 5 or message[:1] != DETECT:
        raise ProtocolError("Malformed detector reply")
    (count,) = struct.unpack_from("<I", message, 1)
    if count > MAX_DETECTIONS:
        raise ProtocolError("Too many detections in one reply")
    offset, result = 5, []
    try:
        for _ in range(count):
            track_id, x0, y0, x1, y1, confidence, size = DETECTION.unpack_from(
                message, offset
            )
            offset += DETECTION.size
            if size > MAX_APPEARANCE:
                raise ProtocolError("Appearance vector too long")
            appearance = struct.unpack_from(f"<{size}d", message, offset)
            offset += 8 * size
            result.append(Det(track_id, (x0, y0, x1, y1), confidence, appearance))
    except struct.error:
        raise ProtocolError("Truncated detector reply") from None
    if offset != len(message):
        raise ProtocolError("Trailing bytes in detector reply")
    return result


# --- server -----------------------------------------------------------------


def _log(text):
    print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)


class Service:
    """Accept authenticated players and give each its own tracker.

    ``load()`` builds the shared engine; ``new_stream(engine)`` builds one
    player's detector from it. Both are injected so tests need no GPU.
    """

    def __init__(self, load, new_stream, key, idle_timeout):
        self.load, self.new_stream = load, new_stream
        self.key, self.idle_timeout = key, idle_timeout
        self.ready, self.engine, self.error = Event(), None, None
        self.guard, self.active = Lock(), 0
        self.idle_since = time.monotonic()
        self.closing = Event()

    def start_loading(self):
        def run():
            started = time.monotonic()
            try:
                self.engine = self.load()
                _log(
                    f"model ready in {time.monotonic() - started:.1f}s: {self.engine.model_path}"
                )
            except BaseException as error:
                self.error = error
                _log("model load failed:\n" + traceback.format_exc())
            finally:
                self.idle_since = time.monotonic()
                self.ready.set()

        Thread(target=run, name="reframe-service-load", daemon=True).start()

    def accept_loop(self, listener):
        while not self.closing.is_set():
            try:
                connection = listener.accept()
            except Exception:
                if self.closing.is_set():
                    return
                time.sleep(0.05)
                continue
            Thread(
                target=self.handle,
                args=(connection,),
                name="reframe-player",
                daemon=True,
            ).start()

    def handle(self, connection):
        with self.guard:
            self.active += 1
        joined = False
        try:
            verify_peer(connection)
            authenticate_server(connection, self.key)
            self.ready.wait()
            if self.error is not None:
                connection.send_bytes(FAILURE + str(self.error).encode("utf-8")[:1024])
                return
            # Build the tracker first: the greeting means "ready for frames".
            stream = self.new_stream(self.engine)
            info = {
                "protocol": PROTOCOL,
                "model": str(self.engine.model_path),
                "device": str(self.engine.device),
            }
            connection.send_bytes(HELLO + json.dumps(info).encode("utf-8"))
            joined = True
            _log(f"player connected ({self.active} active)")
            while True:
                message = connection.recv_bytes(MAX_REQUEST)
                kind = message[:1]
                if kind == DETECT:
                    connection.send_bytes(
                        encode_detections(stream.detect(decode_frame(message)))
                    )
                elif kind == RESET and len(message) == 1:
                    stream.reset()
                else:
                    raise ProtocolError("Unknown request")
        except (EOFError, OSError):
            pass  # the player went away
        except (SecurityError, TimeoutError, ProtocolError) as error:
            _log(f"dropped a connection: {error}")
        except Exception as error:
            _log("request failed:\n" + traceback.format_exc())
            try:
                connection.send_bytes(FAILURE + str(error).encode("utf-8")[:1024])
            except (EOFError, OSError):
                pass
        finally:
            try:
                connection.close()
            finally:
                with self.guard:
                    self.active -= 1
                    self.idle_since = time.monotonic()
                if joined:
                    _log(f"player disconnected ({self.active} active)")

    def run(self):
        """Block until the model failed to load or the service sat idle too long."""
        while True:
            time.sleep(0.5)
            if self.ready.is_set() and self.error is not None:
                time.sleep(1)  # let waiting players read the failure
                return LOAD_FAILED
            with self.guard:
                idle = time.monotonic() - self.idle_since
                if (
                    self.ready.is_set()
                    and self.idle_timeout
                    and not self.active
                    and idle >= self.idle_timeout
                ):
                    _log(f"idle for {idle:.0f}s; exiting")
                    return 0


def open_listener(print_id):
    address, family = endpoint(print_id)
    if family == "AF_UNIX":
        path = Path(address)
        try:
            info = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise SecurityError("Detector endpoint is not our socket")
            path.unlink()  # we hold the instance lock, so it belongs to a dead service
    listener = Listener(address, family=family, backlog=16)
    if family == "AF_UNIX":
        os.chmod(address, 0o600)
    return listener


def serve(backend_dir, model=None, idle_timeout=600):
    """Run the service until idle. Returns the process exit status."""
    from .detector import Engine, TrackedStream
    from .managed import BackendBusyError, Ownership

    print_id = fingerprint(backend_dir, model)
    try:
        instance = Ownership(name=f"detector-{print_id}")
    except BackendBusyError as error:
        print(error, file=sys.stderr)
        return LOCK_BUSY
    listener = None
    try:
        key = load_key()
        listener = open_listener(print_id)
        model_lock = Lock()
        service = Service(
            lambda: Engine(model),
            lambda engine: TrackedStream(engine, model_lock),
            key,
            idle_timeout,
        )
        Thread(
            target=service.accept_loop,
            args=(listener,),
            name="reframe-accept",
            daemon=True,
        ).start()
        _log(f"listening as {print_id}; idle timeout {idle_timeout}s")
        service.start_loading()
        return service.run()
    finally:
        if listener is not None:
            address, family = endpoint(print_id)
            if family == "AF_UNIX":
                Path(address).unlink(missing_ok=True)
        instance.close()


# --- client -----------------------------------------------------------------


class RemoteDetector:
    """The Detector interface the decoder worker uses, backed by the service."""

    def __init__(self, connection, info):
        self.connection, self.lock = connection, Lock()
        self.model_path = Path(info["model"])
        self.device = info.get("device")

    def detect(self, frame):
        message = encode_frame(frame)
        with self.lock:
            self.connection.send_bytes(message)
            if not self.connection.poll(DETECT_TIMEOUT):
                raise TimeoutError(
                    f"Detector service did not answer within {DETECT_TIMEOUT:.0f}s"
                )
            return decode_detections(self.connection.recv_bytes(MAX_REPLY))

    def reset(self):
        with self.lock:
            self.connection.send_bytes(RESET)

    def close(self):
        self.connection.close()


def open_connection(print_id, key):
    address, family = endpoint(print_id)
    connection = Client(address, family=family)
    try:
        verify_peer(connection)
        authenticate_client(connection, key)
    except BaseException:
        connection.close()
        raise
    return connection


def wait_ready(connection, deadline):
    while not connection.poll(1.0):
        if time.monotonic() > deadline:
            raise TimeoutError(
                "Detector service did not finish loading the model in time"
            )
    try:
        message = connection.recv_bytes(MAX_REPLY)
    except EOFError:
        raise RuntimeError("Detector service stopped while loading the model") from None
    if message[:1] == FAILURE:
        raise RuntimeError(
            "Detector service could not load the model: "
            + message[1:1025].decode("utf-8", "replace")
        )
    if message[:1] != HELLO:
        raise ProtocolError("Unexpected detector service greeting")
    info = strict_json(message[1:].decode("utf-8"), limit=4096)
    if info.get("protocol") != PROTOCOL or not isinstance(info.get("model"), str):
        raise ProtocolError("Incompatible detector service")
    return info


def spawn_service(backend_dir, model, idle_timeout, print_id):
    log = log_path(print_id)
    mode = "wb" if log.exists() and log.stat().st_size > LOG_LIMIT else "ab"
    command = [
        sys.executable,
        "-I",
        "-m",
        "reframe",
        "serve",
        "--backend-dir",
        str(backend_dir),
    ]
    command += ["--idle-timeout", str(idle_timeout)]
    if model:
        command += ["--model", str(model)]
    with log.open(mode) as output:
        options = dict(
            cwd=str(backend_dir),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        if os.name != "nt":
            return subprocess.Popen(command, start_new_session=True, **options)
        # CREATE_NO_WINDOW, not DETACHED_PROCESS: the venv python.exe is a launcher
        # that starts the real interpreter, and a console child of a console-less
        # parent would get its own visible window.
        detached = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            # Outlive a player whose job object would otherwise kill its children.
            return subprocess.Popen(
                command, creationflags=detached | 0x01000000, **options
            )
        except OSError:
            return subprocess.Popen(command, creationflags=detached, **options)


def connect_detector(model, backend_dir, idle_timeout=600, *, timeout=READY_TIMEOUT):
    """Connect to the shared service, starting it first if none is running."""
    print_id = fingerprint(backend_dir, model)
    key = load_key()
    connect_deadline = time.monotonic() + CONNECT_TIMEOUT
    process, spawn_after = None, 0.0
    while True:
        try:
            connection = open_connection(print_id, key)
        except SecurityError:
            raise
        except (OSError, EOFError, TimeoutError) as error:
            # No service yet, or one that is exiting. Start one and keep trying.
            now = time.monotonic()
            if process is not None and (status := process.poll()) is not None:
                process = None
                if status != LOCK_BUSY:
                    raise RuntimeError(
                        f"Detector service exited with status {status}; see {log_path(print_id)}"
                    ) from error
                spawn_after = now + 2  # another instance holds the lock; wait for it
            if now > connect_deadline:
                raise RuntimeError(
                    f"Detector service did not start; see {log_path(print_id)}"
                ) from error
            if process is None and now >= spawn_after:
                process = spawn_service(backend_dir, model, idle_timeout, print_id)
            time.sleep(0.1)
            continue
        try:
            info = wait_ready(connection, time.monotonic() + timeout)
        except BaseException:
            connection.close()
            raise
        return RemoteDetector(connection, info)
