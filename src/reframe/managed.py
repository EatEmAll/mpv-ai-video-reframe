"""Local protocol and process supervision, independent of the host OS."""

import json
import os
import re
import time
from pathlib import Path
from threading import Event, Lock, Thread

from .group import MODES
from .security import (
    private_dir,
    runtime_root,
    strict_json,
    trusted_path,
    validate_endpoint,
)
from .transport import connect_mpv


class BackendBusyError(RuntimeError):
    pass


class Ownership:
    """An OS-released file lock; never unlink the lock inode during release."""

    def __init__(self, pid):
        if type(pid) is not int or pid <= 0:
            raise ValueError("Invalid player PID")
        root = private_dir(runtime_root() / "locks")
        path = trusted_path(root / f"{pid}.lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            import stat

            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Unsafe ownership lock")
            trusted_path(path, private=True)
            self.file = os.fdopen(fd, "r+b")
        except BaseException:
            os.close(fd)
            raise
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                if self.file.read(1) == b"":
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise BackendBusyError("Another reframe backend owns this player") from None

    def close(self):
        self.file.close()


class Session:
    def __init__(self, args):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", args.session_id):
            raise ValueError("Invalid session identifier")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", args.lua_client):
            raise ValueError("Invalid Lua client")
        validate_endpoint(args.pipe, player_pid=args.player_pid)
        self.id, self.client = args.session_id, args.lua_client
        self.player_pid = args.player_pid
        self.stop = Event()
        self.ack = Event()
        self.lock = Lock()
        self.seq = 0
        self.received = 0
        self.epoch = 0
        self.progress = time.perf_counter()
        self.phase = "starting"
        # Read by the controller every tick; replaced atomically by receive().
        self.mode = getattr(args, "mode", None) or "follow"
        # Whether the controller draws the bounding-box overlay; same hand-off.
        self.debug = bool(getattr(args, "debug_osd", False))
        self.last_state = None
        self.thread = None
        self.owner = None
        self.mpv = None
        self.log = None
        directory = trusted_path(args.output_dir)
        private_dir(directory.parent)
        if directory.exists():
            raise ValueError("Session output already exists")
        private_dir(directory)
        self.log = (directory / "lifecycle.jsonl").open("w", encoding="utf-8")
        try:
            self.mpv = connect_mpv(
                args.pipe,
                player_pid=args.player_pid,
                quit_callback=self.stop.set,
            )
            self.mpv.bind_event("client-message", self.receive)
            self.owner = Ownership(self.mpv.command("get_property", "pid"))
            self.send("hello", backend_version="0.1.0", pid=os.getpid())
            self.thread = Thread(target=self.heartbeat, daemon=True)
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def send(self, kind, **fields):
        with self.lock:
            if self.seq >= 9007199254740991:
                raise ValueError("Protocol sequence exhausted")
            self.seq += 1
            payload = dict(
                protocol=1, session=self.id, seq=self.seq, type=kind, **fields
            )
            text = json.dumps(payload, allow_nan=False)
            if len(text.encode("utf-8")) > 16384:
                raise ValueError("Protocol message exceeds limit")
            self.log.write(text + "\n")
            self.log.flush()
            self.mpv.command("script-message-to", self.client, "reframe-backend", text)

    def receive(self, event):
        args = event.get("args", []) if isinstance(event, dict) else []
        if (
            not isinstance(args, list)
            or len(args) != 2
            or args[0] != "reframe-control"
            or not isinstance(args[1], str)
            or len(args[1]) > 16384
        ):
            return
        try:
            data = strict_json(args[1])
            seq = data.get("seq")
            if (
                type(data.get("protocol")) is not int
                or data.get("protocol") != 1
                or data.get("session") != self.id
                or type(seq) is not int
                or not 1 <= seq <= 9007199254740991
                or seq <= self.received
            ):
                return
            kind = data.get("type")
            if kind == "stop":
                if (
                    set(data) - {"protocol", "session", "seq", "type", "reason"}
                    or not isinstance(data.get("reason", ""), str)
                    or len(data.get("reason", "")) > 1024
                ):
                    return
            elif kind == "acquire-ack":
                if (
                    set(data) != {"protocol", "session", "seq", "type", "view_epoch"}
                    or type(data.get("view_epoch")) is not int
                    or data["view_epoch"] != self.epoch
                ):
                    return
            elif kind == "mode":
                if (
                    set(data) != {"protocol", "session", "seq", "type", "mode"}
                    or data.get("mode") not in MODES
                ):
                    return
            elif kind == "debug":
                if (
                    set(data) != {"protocol", "session", "seq", "type", "enabled"}
                    or type(data.get("enabled")) is not bool
                ):
                    return
            else:
                return
            self.received = seq
            if kind == "mode":
                self.mode = data["mode"]
            elif kind == "debug":
                self.debug = data["enabled"]
            elif kind == "stop":
                self.stop.set()
                self.ack.set()
            elif (
                data.get("type") == "acquire-ack"
                and data.get("view_epoch") == self.epoch
            ):
                self.ack.set()
        except (ValueError, TypeError, AttributeError, RecursionError):
            return

    def heartbeat(self):
        while not self.stop.wait(1):
            try:
                self.send(
                    "heartbeat",
                    phase=self.phase,
                    controller_progress=self.progress,
                    stalled=self.phase == "active"
                    and time.perf_counter() - self.progress > 5,
                )
            except Exception:
                self.stop.set()

    def state(self, state, reason=""):
        self.phase = state
        if self.last_state != (state, reason):
            self.last_state = state, reason
            self.send("state", state=state, reason=reason)

    def acquire(self, saved):
        self.epoch += 1
        self.ack.clear()
        self.send("acquire", view_epoch=self.epoch, saved=saved)
        if not self.ack.wait(2) or self.stop.is_set():
            raise RuntimeError(
                "View acquisition cancelled or acknowledgement timed out"
            )

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.mpv:
            self.mpv.terminate()
        if self.owner:
            self.owner.close()
        if self.log:
            self.log.close()


def run_managed(args):
    from .runtime import validate_backend_runtime

    validate_backend_runtime(args.backend_dir)
    os.chdir(trusted_path(args.backend_dir))
    session = Session(args)
    ipc = None
    try:
        from .ipc import IPC

        ipc = IPC(args.pipe, managed=session)
        model = Path(args.model or "models/yolo26n.pt")
        if not model.is_file():
            raise FileNotFoundError(
                f"Model missing: {model}. Prepare weights using reframe doctor first."
            )
        from .detector import DetectorLoader

        # Load while the user picks a video; usually ready before one arrives.
        loader = DetectorLoader(args.model)
        session.state("waiting")
        while not session.stop.wait(0.1):
            if loader.error is not None:
                break  # report load failures without waiting for a video
            state, _ = ipc.snapshot()
            path = state.get("path")
            path = (
                os.path.abspath(
                    os.path.join(state.get("working-directory") or os.getcwd(), path)
                )
                if path
                else None
            )
            if path and Path(path).is_file() and state.get("video-params"):
                break
        if session.stop.is_set():
            return
        if not loader.done.is_set():
            session.state("loading")
        detector = loader.result(session.stop)
        if detector is None or session.stop.is_set():
            return
        session.send(
            "state",
            state="ready",
            model=str(detector.model_path),
            log_dir=args.output_dir,
        )
        from .controller import run

        run(
            args.pipe,
            detector,
            args.rate,
            args.debug_osd,
            args.seconds,
            session.stop,
            args.output_dir,
            ipc=ipc,
            mode=session.mode,
            group_conf_floor=getattr(args, "group_conf_floor", 0.5),
            group_coverage_floor=getattr(args, "group_coverage_floor", 0.7),
            group_options={
                key: getattr(args, "group_" + key, default)
                for key, default in (
                    ("relaxed_floor", 0.5),
                    ("pair_floor", 0.2),
                    ("join", 0.3),
                    ("leave", 0.7),
                    ("switch", 0.6),
                )
            },
        )
    except Exception:
        if session.stop.is_set():
            return
        session.send(
            "error",
            code="backend",
            message="Backend failed; inspect local stderr (may contain private paths)",
        )
        raise
    finally:
        try:
            if ipc:
                ipc.close()
        finally:
            session.close()
