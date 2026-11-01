"""Protocol, locking and reversible installation regressions (no GPU required)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest

from reframe.managed import Ownership, Session
from reframe.runtime import find_mpv

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "plugin_install", ROOT / "scripts/plugin.py"
)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def test_owner_is_exclusive_and_released():
    owner = Ownership(os.getpid())
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"from reframe.managed import Ownership; Ownership({os.getpid()})",
            ],
            capture_output=True,
        )
        assert result.returncode != 0
        assert b"Another reframe" in result.stderr
    finally:
        owner.close()
    Ownership(os.getpid()).close()


def test_protocol_rejects_bad_stale_and_other_session_messages():
    s = Session.__new__(Session)
    s.id, s.received, s.epoch = "ours", 0, 4
    s.stop, s.ack = Event(), Event()

    def receive(**values):
        data = dict(protocol=1, session="ours", seq=1, type="stop")
        data.update(values)
        s.receive({"args": ["reframe-control", json.dumps(data)]})

    for data in ({"protocol": 2}, {"session": "other"}, {"seq": "x"}, {"seq": 0}):
        receive(**data)
        assert not s.stop.is_set()
    s.receive({"args": ["reframe-control", "[]"]})
    s.receive({"args": ["reframe-control", "{"]})
    receive(type="acquire-ack", view_epoch=3)
    assert not s.ack.is_set()
    receive(seq=2, type="acquire-ack", view_epoch=4)
    assert s.ack.is_set()
    receive(seq=1)
    assert not s.stop.is_set()
    receive(seq=3)
    assert s.stop.is_set()


def test_mode_control_switches_framing_mode_and_rejects_bad_values():
    s = Session.__new__(Session)
    s.id, s.received, s.epoch, s.mode = "ours", 0, 0, "follow"
    s.stop, s.ack = Event(), Event()

    def receive(seq, **values):
        data = dict(protocol=1, session="ours", seq=seq, type="mode")
        data.update(values)
        s.receive({"args": ["reframe-control", json.dumps(data)]})

    receive(1, mode="sideways")
    receive(1, mode=["group"])
    receive(1, mode="group", extra=True)
    receive(1)
    assert s.mode == "follow" and s.received == 0
    receive(1, mode="group")
    assert s.mode == "group" and s.received == 1
    receive(1, mode="follow")  # replayed sequence number
    assert s.mode == "group"
    receive(2, mode="follow")
    assert s.mode == "follow"
    assert not s.stop.is_set() and not s.ack.is_set()


def test_installer_preserves_user_files_and_modified_config(tmp_path):
    root = tmp_path / "backend spaces [test]"
    for relative in (
        "pyproject.toml",
        "src/reframe/__main__.py",
        "mpv/reframe.lua",
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python",
    ):
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("placeholder")
    (root / "mpv/reframe.conf.example").write_text("backend_dir=\nautostart=no\n")
    config = tmp_path / "config"
    config.mkdir()
    (config / "mpv.conf").write_text("user config")
    plugin.install(root, config)
    conf = config / "script-opts/reframe.conf"
    assert root.as_posix() in conf.read_text()
    conf.write_text(conf.read_text() + "# edited\n")
    plugin.install(root, config)
    assert "# edited" in conf.read_text()
    assert plugin.uninstall(config) == ["script-opts/reframe.conf"]
    assert conf.exists()
    assert (config / "mpv.conf").read_text() == "user config"
    assert not (config / "scripts/reframe.lua").exists()


def test_repeat_install_keeps_backup_for_a_real_upgrade(tmp_path):
    root = tmp_path / "backend"
    for relative in (
        "pyproject.toml",
        "src/reframe/__main__.py",
        "mpv/reframe.lua",
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python",
    ):
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("first")
    (root / "mpv/reframe.conf.example").write_text("backend_dir=\n")
    config = tmp_path / "config"
    config.mkdir()
    previous = config / "scripts/reframe.lua.previous"
    plugin.install(root, config)
    plugin.install(root, config)
    assert not previous.exists()
    (root / "mpv/reframe.lua").write_text("second")
    plugin.install(root, config)
    assert previous.read_text() == "first"
    assert (config / "scripts/reframe.lua").read_text() == "second"


def test_setup_prepares_then_installs_in_order(tmp_path, monkeypatch):
    calls = []
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    monkeypatch.setattr(plugin, "find_mpv", lambda: calls.append("mpv"))
    monkeypatch.setattr(
        plugin.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command[-1]) or kwargs["check"] and None,
    )
    monkeypatch.setattr(plugin, "inspect_backend", lambda root: "python")
    monkeypatch.setattr(
        plugin, "install", lambda root, config: calls.append("install") or []
    )
    assert plugin.setup(tmp_path, tmp_path / "config") == []
    assert calls == ["mpv", "3.12", "doctor", "install"]
    calls.clear()
    assert plugin.setup(tmp_path, tmp_path / "config", accel=True) == []
    assert calls == [
        "mpv",
        "accel",
        "doctor",
        "scripts/export_fp32_engine.py",
        "install",
    ]
    calls.clear()
    (tmp_path / "models").mkdir()
    (tmp_path / "models/yolo26n.engine").write_text("built")
    assert plugin.setup(tmp_path, tmp_path / "config", accel=True) == []
    assert calls == ["mpv", "accel", "doctor", "install"]


def test_setup_stops_before_downloads_without_mpv(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)

    def missing():
        raise FileNotFoundError("Install mpv")

    def unexpected(*args, **kwargs):
        raise AssertionError("uv must not run")

    monkeypatch.setattr(plugin, "find_mpv", missing)
    monkeypatch.setattr(plugin.subprocess, "run", unexpected)
    with pytest.raises(FileNotFoundError, match="Install mpv"):
        plugin.setup(tmp_path, tmp_path / "config")


def test_setup_rejects_foreign_uv_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "other"))
    with pytest.raises(ValueError, match="UV_PROJECT_ENVIRONMENT"):
        plugin.setup(tmp_path, tmp_path / "config")


def test_installer_rejects_unowned_script(tmp_path):
    # Exercise guard against replacing a user's script using the prepared repository.
    script = tmp_path / "scripts/reframe.lua"
    script.parent.mkdir()
    script.write_text("user script")
    with pytest.raises(ValueError, match="unowned"):
        plugin.install(ROOT, tmp_path)
    assert script.read_text() == "user script"


def test_lua_lifecycle_with_real_mpv_interpreter(tmp_path):
    try:
        executable = find_mpv()
    except FileNotFoundError:
        pytest.skip("mpv not installed")
    env = dict(os.environ, REFRAME_TEST_ROOT=str(ROOT))
    result = subprocess.run(
        [
            executable,
            "--no-config",
            *(["--media-controls=no"] if os.name == "nt" else []),
            "--log-file=" + str(tmp_path / "mpv.log"),
            "--vo=null",
            "--ao=null",
            "--idle=yes",
            "--script=" + str(ROOT / "tests/lua_lifecycle.lua"),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    log = (tmp_path / "mpv.log").read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, log
    assert "LUA_LIFECYCLE_PASS" in log


def test_acquisition_rechecks_generation_before_mutating_view():
    from threading import Lock
    from types import SimpleNamespace

    from reframe.ipc import IPC, ViewChanged

    ipc = IPC.__new__(IPC)
    ipc.lock, ipc.cache, ipc.generation = Lock(), {"path": "old.mp4"}, 1
    ipc.saved = {
        "panscan": 0,
        "video-align-x": 0,
        "cache": "auto",
        "video-timing-offset": 0,
    }
    ipc.enabled = False
    calls = []
    ipc.mpv = SimpleNamespace(command=lambda *args: calls.append(args) or 0)

    def acquire(saved):
        ipc.generation += 1

    released = []
    ipc.managed = SimpleNamespace(
        acquire=acquire, send=lambda *a, **kw: released.append((a, kw)), epoch=1
    )
    with pytest.raises(ViewChanged):
        ipc.enable()
    assert not ipc.enabled
    assert all(c[0] == "get_property" for c in calls)
    assert released[0][0] == ("released",)
