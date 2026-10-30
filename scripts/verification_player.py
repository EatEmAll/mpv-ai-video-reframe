"""Isolated verification players; all launched players are closed on exit."""

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from python_mpv_jsonipc import MPV

from reframe.runtime import find_mpv
from reframe.security import prepare_endpoint


def save(name, result):
    directory = Path("runs/spikes")
    directory.mkdir(parents=True, exist_ok=True)
    result = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **result}
    (directory / f"{name}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    gates = [
        value for key, value in result.items() if key == "pass" or key.endswith("_pass")
    ]
    if gates and not all(value is True for value in gates):
        raise SystemExit(1)
    return result


def wait_property(player, name, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = player.command("get_property", name)
            if value is not None:
                return value
        except Exception:
            pass
        time.sleep(0.02)
    raise TimeoutError(name)


@contextmanager
def player_for(path, **options):
    # Lua refuses relative or shared endpoints; Unix needs a private socket path.
    name = (
        f"reframe-spike-{uuid.uuid4().hex}"
        if os.name == "nt"
        else prepare_endpoint(os.getpid())
    )
    options.setdefault("cache", True)
    if os.environ.get("REFRAME_GPU_CONTEXT"):
        options.setdefault("gpu_context", os.environ["REFRAME_GPU_CONTEXT"])
    player = MPV(
        mpv_location=find_mpv(),
        ipc_socket=name,
        start_retries=1,
        config=False,
        idle=True,
        keep_open=True,
        audio=False,
        discard_output=True,
        keepaspect_window=False,
        panscan=1,
        geometry="608x800",
        hwdec="auto-safe",
        **options,
    )
    try:
        player.command("loadfile", os.path.abspath(path))
        wait_property(player, "video-params")
        wait_property(player, "osd-dimensions")
        yield player, name
    finally:
        player.terminate()
        if os.name != "nt":
            try:
                Path(name).parent.rmdir()  # our new per-player directory only
            except OSError:
                pass
