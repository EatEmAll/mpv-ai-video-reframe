"""Local protocol and process supervision, independent of the host OS."""

import getpass
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import Event, Lock, Thread

from python_mpv_jsonipc import MPV

from .runtime import normalize_endpoint


class BackendBusyError(RuntimeError):
    pass


class Ownership:
    """An OS-released file lock; never unlink the lock inode during release."""

    def __init__(self, pid):
        user = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
        root = Path(tempfile.gettempdir()) / ("mpv-reframe-locks-" + user)
        root.mkdir(mode=0o700, exist_ok=True)
        self.file = (root / f"{int(pid)}.lock").open("a+b")
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
        self.id, self.client = args.session_id, args.lua_client
        self.stop = Event()
        self.ack = Event()
        self.lock = Lock()
        self.seq = 0
        self.received = 0
        self.epoch = 0
        self.progress = time.perf_counter()
        self.phase = "starting"
        self.last_state = None
        self.thread = None
        self.owner = None
        self.mpv = None
        self.log = None
        directory = Path(args.output_dir)
        directory.mkdir(parents=True, exist_ok=False)
        self.log = (directory / "lifecycle.jsonl").open("w", encoding="utf-8")
        try:
            self.mpv = MPV(
                start_mpv=False,
                ipc_socket=normalize_endpoint(args.pipe),
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
            self.seq += 1
            payload = dict(
                protocol=1, session=self.id, seq=self.seq, type=kind, **fields
            )
            text = json.dumps(payload, allow_nan=False)
            self.log.write(text + "\n")
            self.log.flush()
            self.mpv.command("script-message-to", self.client, "reframe-backend", text)

    def receive(self, event):
        args = event.get("args", [])
        if len(args) != 2 or args[0] != "reframe-control" or len(args[1]) > 16384:
            return
        try:
            data = json.loads(args[1])
            seq = data.get("seq")
            if (
                data.get("protocol") != 1
                or data.get("session") != self.id
                or type(seq) is not int
                or seq <= self.received
            ):
                return
            self.received = seq
            if data.get("type") == "stop":
                self.stop.set()
                self.ack.set()
            elif (
                data.get("type") == "acquire-ack"
                and data.get("view_epoch") == self.epoch
            ):
                self.ack.set()
        except (ValueError, TypeError, AttributeError):
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
    os.chdir(args.backend_dir)
    session = Session(args)
    ipc = None
    try:
        from .ipc import IPC

        ipc = IPC(args.pipe, managed=session)
        session.state("waiting")
        while not session.stop.wait(0.1):
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
        model = Path(args.model or "models/yolo26n.pt")
        if not model.is_file():
            raise FileNotFoundError(
                f"Model missing: {model}. Prepare weights using reframe doctor first."
            )
        session.state("loading")
        from .detector import Detector

        detector = Detector(args.model)
        if session.stop.is_set():
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
        )
    except Exception as error:
        if session.stop.is_set():
            return
        session.send("error", code="backend", message=str(error))
        raise
    finally:
        try:
            if ipc:
                ipc.close()
        finally:
            session.close()
