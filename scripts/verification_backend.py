"""Launch and supervise the real Lua backend using an independent IPC client."""

import json
import shutil
import time
from pathlib import Path
from threading import Event

from python_mpv_jsonipc import MPV


class LuaBackend:
    def __init__(self, model=None, render_offset=None):
        self.model = model
        self.render_offset = render_offset
        self.pid = None
        self.ready = Event()

    def reset(self):
        self.pid = None
        self.ready.clear()


def player_options(detector):
    if not isinstance(detector, LuaBackend):
        return {}
    root = Path(__file__).resolve().parents[1]
    opts = "reframe-backend_dir=" + root.as_posix()
    if detector.model:
        opts += ",reframe-model=" + str(Path(detector.model).absolute()).replace(
            "\\", "/"
        )
    return {"script": str(root / "mpv/reframe.lua"), "script_opts": opts}


def run(pipe, detector, *args, stop_event=None, output_dir=None, **kwargs):
    player = MPV(start_mpv=False, ipc_socket=pipe)
    source = None
    try:
        player.command("script-message-to", "reframe", "reframe-enable")
        deadline = time.monotonic() + 130
        while time.monotonic() < deadline and not stop_event.is_set():
            try:
                status = player.command("get_property", "user-data/reframe")
            except Exception:
                status = {}
            if status.get("log_dir"):
                source = Path(status["log_dir"])
            if status.get("state") == "error":
                raise RuntimeError(status.get("reason"))
            if status.get("state") == "active":
                for line in (source / "lifecycle.jsonl").read_text().splitlines():
                    record = json.loads(line)
                    if record["type"] == "hello":
                        detector.pid = record["pid"]
                if detector.render_offset is not None:
                    player.command(
                        "set_property", "video-timing-offset", detector.render_offset
                    )
                detector.ready.set()
                break
            time.sleep(0.05)
        else:
            if not stop_event.is_set():
                raise TimeoutError("Lua backend did not become active")
        while not stop_event.wait(0.1):
            status = player.command("get_property", "user-data/reframe")
            if status.get("state") == "error":
                raise RuntimeError(status.get("reason"))
    finally:
        player.command("script-message-to", "reframe", "reframe-stop")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status = player.command("get_property", "user-data/reframe")
            if status.get("state") in ("disabled", "error"):
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("Lua backend failed to stop")
        player.terminate()
        if source and source.is_dir() and output_dir:
            shutil.copytree(source, output_dir, dirs_exist_ok=True)
