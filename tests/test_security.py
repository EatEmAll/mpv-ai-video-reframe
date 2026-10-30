"""Local trust-boundary regressions; no model deserialization, GPU or media."""

import json
import os
import subprocess
import sys
from threading import Event

import pytest
from test_managed import plugin

from reframe.managed import Session
from reframe.models import select_model, verify_weights
from reframe.security import SecurityError, private_dir, trusted_path, validate_endpoint


def protocol():
    session = Session.__new__(Session)
    session.id, session.received, session.epoch = "ours", 0, 1
    session.stop, session.ack = Event(), Event()
    return session


@pytest.mark.parametrize(
    "raw", ["null", "[]", "true", "1", '"hi"', '{"x":NaN}', "{" * 1000]
)
def test_bad_json_never_advances_protocol(raw):
    session = protocol()
    session.receive({"args": ["reframe-control", raw]})
    assert session.received == 0 and not session.stop.is_set()


def test_duplicate_fields_and_deep_json_rejected():
    session = protocol()
    for raw in (
        '{"protocol":1,"session":"ours","seq":1,"type":"stop","type":"stop"}',
        "[" * 2000 + "]" * 2000,
    ):
        session.receive({"args": ["reframe-control", raw]})
    assert session.received == 0


@pytest.mark.parametrize(
    "change",
    [
        {"seq": True},
        {"seq": 2**54},
        {"protocol": True},
        {"seq": 1.5},
        {"type": "unknown"},
        {"reason": []},
        {"reason": "x" * 1025},
        {"extra": "field"},
        {"type": "acquire-ack", "view_epoch": True},
        {"type": "acquire-ack", "view_epoch": 2},
    ],
)
def test_invalid_messages_do_not_poison_sequence(change):
    session = protocol()
    data = dict(protocol=1, session="ours", seq=1, type="stop")
    data.update(change)
    session.receive({"args": ["reframe-control", json.dumps(data)]})
    assert session.received == 0 and not session.stop.is_set()
    data = dict(protocol=1, session="ours", seq=1, type="stop")
    session.receive({"args": ["reframe-control", json.dumps(data)]})
    assert session.stop.is_set()


def test_protocol_non_string_and_oversize():
    session = protocol()
    for event in (
        None,
        [],
        {"args": None},
        {"args": ["reframe-control", {}]},
        {"args": ["reframe-control", "x" * 16385]},
    ):
        session.receive(event)
    assert session.received == 0


def test_untrusted_model_formats_and_hashes_rejected(tmp_path):
    for name in ("model.pt", "model.yaml", "model.onnx", "model.engine"):
        path = tmp_path / name
        path.write_bytes(b"untrusted pickle or model")
        with pytest.raises(SecurityError):
            select_model(str(path), None)
    with pytest.raises(SecurityError):
        verify_weights(tmp_path / "missing.pt")


def test_setup_download_is_bounded_verified_and_atomic(tmp_path, monkeypatch):
    import hashlib
    import io
    import urllib.request

    from reframe import models

    monkeypatch.chdir(tmp_path)
    payload = b"synthetic trusted model bytes (never deserialized)"
    monkeypatch.setattr(models, "WEIGHTS_SIZE", len(payload))
    monkeypatch.setattr(models, "WEIGHTS_SHA256", hashlib.sha256(payload).hexdigest())

    class Response(io.BytesIO):
        url = "https://release-assets.githubusercontent.com/test"

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: Response(payload + b"tampered")
    )
    with pytest.raises(SecurityError, match="checksum"):
        models.prepare_weights()
    assert list((tmp_path / "models").iterdir()) == []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(payload))
    path = models.prepare_weights()
    assert path.read_bytes() == payload
    path.write_bytes(b"tampered")
    with pytest.raises(SecurityError):
        models.prepare_weights()
    assert path.read_bytes() == b"tampered"


def test_network_and_loader_controls_set_before_import(tmp_path, monkeypatch):
    from reframe.models import configure_inference

    monkeypatch.chdir(tmp_path)
    for key in (
        "YOLO_OFFLINE",
        "YOLO_AUTOINSTALL",
        "ULTRALYTICS_SAFE_LOAD",
        "YOLO_CONFIG_DIR",
    ):
        monkeypatch.setenv(key, "untrusted override")
    configure_inference()
    assert os.environ["YOLO_OFFLINE"] == "true"
    assert os.environ["YOLO_AUTOINSTALL"] == "false"
    assert os.environ["ULTRALYTICS_SAFE_LOAD"] == "true"
    trusted_path(os.environ["YOLO_CONFIG_DIR"], private=True)


def test_symlink_or_junction_target_never_touched(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
        )
        assert result.returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SecurityError):
        trusted_path(link / "new-file")
    with pytest.raises(SecurityError):
        plugin.uninstall(link)
    assert list(target.iterdir()) == []


def test_manifest_traversal_and_bad_digests_rejected_before_deletion(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep")
    for files in ({"../sentinel": "a" * 64}, {"scripts/reframe.lua": True}, []):
        (tmp_path / plugin.MANIFEST).write_text(
            json.dumps({"version": 1, "files": files})
        )
        with pytest.raises(ValueError):
            plugin.uninstall(tmp_path)
        assert sentinel.read_text() == "keep"


def test_installer_rejects_hardlinks_without_changing_target(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"private target")
    link = tmp_path / "link"
    os.link(target, link)
    with pytest.raises(SecurityError, match="Hard-linked"):
        plugin.atomic(link, b"replacement")
    assert target.read_bytes() == b"private target"
    assert link.read_bytes() == b"private target"


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":1,"version":1,"files":{}}',
        '{"version":true,"files":{}}',
        '{"version":1,"files":{},"extra":1}',
        '{"version":1,"files":{"scripts/reframe.lua":NaN}}',
        " " * 131073,
    ],
)
def test_invalid_installer_metadata_fails_closed(tmp_path, raw):
    manifest = tmp_path / plugin.MANIFEST
    manifest.write_text(raw)
    with pytest.raises(ValueError):
        plugin.uninstall(tmp_path)
    assert manifest.read_text() == raw


def test_recovery_path_traversal_cannot_write(tmp_path):
    journal = tmp_path / plugin.JOURNAL
    journal.write_text(
        json.dumps(
            {"version": 1, "changes": {"../outside": {"old": None, "new": "eA=="}}}
        )
    )
    with pytest.raises(ValueError, match="journal"):
        plugin.recover(tmp_path)
    assert not (tmp_path.parent / "outside").exists()


def test_upgrade_failure_rolls_back_and_recovers_after_interruption(
    tmp_path, monkeypatch
):
    config = tmp_path
    (config / "scripts").mkdir()
    script = config / "scripts/reframe.lua"
    script.write_bytes(b"old")
    manifest = config / plugin.MANIFEST
    manifest.write_bytes(b"old manifest")
    atomic = plugin.atomic

    def fail_manifest(path, content):
        if path == manifest and content == b"new manifest":
            raise OSError("simulated disk failure")
        atomic(path, content)

    monkeypatch.setattr(plugin, "atomic", fail_manifest)
    with pytest.raises(OSError):
        plugin.transaction(
            config, {"scripts/reframe.lua": b"new", plugin.MANIFEST: b"new manifest"}
        )
    assert script.read_bytes() == b"old"
    assert manifest.read_bytes() == b"old manifest"
    assert not (config / plugin.JOURNAL).exists()

    # Simulate a killed process after durable journal + first replacement.
    def killed(path, content):
        if path == manifest:
            raise KeyboardInterrupt()
        atomic(path, content)

    monkeypatch.setattr(plugin, "atomic", killed)
    monkeypatch.setattr(plugin, "recover", lambda _: None)
    with pytest.raises(KeyboardInterrupt):
        plugin.transaction(
            config, {"scripts/reframe.lua": b"new", plugin.MANIFEST: b"new manifest"}
        )
    assert (config / plugin.JOURNAL).exists()
    monkeypatch.undo()
    plugin.recover(config)
    assert script.read_bytes() == b"old"
    assert manifest.read_bytes() == b"old manifest"


def test_recovery_preserves_post_crash_edits(tmp_path, monkeypatch):
    import base64

    script = tmp_path / "scripts/reframe.lua"
    script.parent.mkdir()
    script.write_bytes(b"edited")
    record = {
        "old": base64.b64encode(b"old").decode(),
        "new": base64.b64encode(b"new").decode(),
    }
    (tmp_path / plugin.JOURNAL).write_text(
        json.dumps({"version": 1, "changes": {"scripts/reframe.lua": record}})
    )
    with pytest.raises(ValueError, match="edited"):
        plugin.recover(tmp_path)
    assert script.read_bytes() == b"edited"


@pytest.mark.skipif(
    os.name == "nt", reason="Requires native Unix filesystem/socket semantics"
)
def test_unix_private_socket_and_unsafe_parent(tmp_path):
    import socket

    root = private_dir(tmp_path / "runtime")
    endpoint = root / "ipc"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(endpoint))
        validate_endpoint(str(endpoint), harden=True)
        assert endpoint.stat().st_mode & 0o777 == 0o600
        root.chmod(0o755)
        with pytest.raises(SecurityError):
            validate_endpoint(str(endpoint))
    with pytest.raises(SecurityError):
        validate_endpoint("/" + "x" * 101)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows DACL/pipe regression")
def test_native_windows_pipe_acl_hardening_and_wrong_pid():
    import ctypes as c
    from ctypes import wintypes as w

    from reframe.windows_security import adv, checked, kernel

    class Attributes(c.Structure):
        _fields_ = [
            ("length", w.DWORD),
            ("descriptor", c.c_void_p),
            ("inherit", w.BOOL),
        ]

    kernel.CreateNamedPipeW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        c.c_void_p,
    ]
    kernel.CreateNamedPipeW.restype = w.HANDLE
    kernel.DisconnectNamedPipe.argtypes = [w.HANDLE]
    kernel.ConnectNamedPipe.argtypes = [w.HANDLE, c.c_void_p]
    name = "reframe-security-test-" + os.urandom(12).hex()
    descriptor = c.c_void_p()
    checked(
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;;GA;;;WD)", 1, c.byref(descriptor), None
        )
    )
    attributes = Attributes(c.sizeof(Attributes), descriptor, False)
    pipe = kernel.CreateNamedPipeW(
        "\\\\.\\pipe\\" + name, 3, 1, 1, 4096, 4096, 0, c.byref(attributes)
    )
    assert pipe != w.HANDLE(-1).value
    kernel.LocalFree(descriptor)
    try:
        with pytest.raises(SecurityError):
            validate_endpoint(name, player_pid=os.getpid())
        kernel.DisconnectNamedPipe(pipe)
        kernel.ConnectNamedPipe(pipe, None)
        with pytest.raises(SecurityError, match="expected player"):
            validate_endpoint(name, player_pid=os.getpid() + 1, harden=True)
        kernel.DisconnectNamedPipe(pipe)
        kernel.ConnectNamedPipe(pipe, None)
        with pytest.raises(SecurityError):
            validate_endpoint(name, player_pid=os.getpid(), harden=True)
    finally:
        kernel.CloseHandle(pipe)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows directory DACL")
def test_native_private_directory_acl(tmp_path):
    directory = private_dir(tmp_path / "private")
    trusted_path(directory, private=True)
    child = directory / "child"
    child.write_text("safe")
    trusted_path(child, private=True)


def test_isolated_python_can_import_backend():
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import reframe.security"], capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows mpv integration")
def test_real_mpv_pipe_hardening_survives_connections():
    import time

    from reframe.runtime import find_mpv
    from reframe.transport import connect_mpv

    try:
        executable = find_mpv()
    except FileNotFoundError:
        pytest.skip("mpv not installed")
    name = "reframe-security-mpv-" + os.urandom(12).hex()
    process = subprocess.Popen(
        [
            executable,
            "--no-config",
            "--media-controls=no",
            "--vo=null",
            "--ao=null",
            "--idle=yes",
            "--input-ipc-server=" + name,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                validate_endpoint(name, player_pid=process.pid, harden=True)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        for _ in range(2):
            connection = connect_mpv(name, player_pid=process.pid)
            try:
                assert connection.command("get_property", "pid") == process.pid
            finally:
                connection.terminate()
    finally:
        process.terminate()  # only the isolated process created by this test
        process.wait(timeout=5)


@pytest.mark.skipif(
    os.name != "nt", reason="Native Windows idle Lua/backend integration"
)
def test_real_lua_preflight_backend_wait_and_stop(tmp_path):
    from test_managed import ROOT

    from reframe.runtime import find_mpv

    try:
        executable = find_mpv()
    except FileNotFoundError:
        pytest.skip("mpv not installed")
    log = tmp_path / "mpv-security.log"
    result = subprocess.run(
        [
            executable,
            "--no-config",
            "--media-controls=no",
            "--vo=null",
            "--ao=null",
            "--idle=yes",
            "--log-file=" + str(log),
            "--script=" + str(ROOT / "mpv/reframe.lua"),
            "--script=" + str(ROOT / "tests/lua_security_integration.lua"),
            "--script-opts=reframe-backend_dir=" + str(ROOT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=18,
    )
    text = log.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, text
    assert "LUA_SECURITY_INTEGRATION_PASS" in text


@pytest.mark.skipif(os.name != "nt", reason="Native restricted-token access check")
def test_mpv_atomic_pipe_denies_restricted_token():
    import ctypes as c
    import time
    from ctypes import wintypes as w

    from reframe.runtime import find_mpv
    from reframe.windows_security import adv, checked, kernel

    name = "reframe-restricted-" + os.urandom(12).hex()
    process = subprocess.Popen(
        [
            find_mpv(),
            "--no-config",
            "--media-controls=no",
            "--vo=null",
            "--ao=null",
            "--idle=yes",
            "--input-ipc-server=" + name,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    token, restricted, sid = w.HANDLE(), w.HANDLE(), c.c_void_p()

    class SidAttributes(c.Structure):
        _fields_ = [("sid", c.c_void_p), ("attributes", w.DWORD)]

    adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, c.POINTER(c.c_void_p)]
    adv.CreateRestrictedToken.argtypes = [
        w.HANDLE,
        w.DWORD,
        w.DWORD,
        c.c_void_p,
        w.DWORD,
        c.c_void_p,
        w.DWORD,
        c.c_void_p,
        c.POINTER(w.HANDLE),
    ]
    adv.ImpersonateLoggedOnUser.argtypes = [w.HANDLE]
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                # Inspection only: no hardening, including the first instance.
                validate_endpoint(name, player_pid=process.pid)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
        checked(
            adv.OpenProcessToken(kernel.GetCurrentProcess(), 0xF01FF, c.byref(token))
        )
        checked(adv.ConvertStringSidToSidW("S-1-5-7", c.byref(sid)))
        restriction = SidAttributes(sid, 0)
        checked(
            adv.CreateRestrictedToken(
                token, 1, 0, None, 0, None, 1, c.byref(restriction), c.byref(restricted)
            )
        )
        checked(adv.ImpersonateLoggedOnUser(restricted))
        try:
            handle = kernel.CreateFileW(
                "\\\\.\\pipe\\" + name, 0xC0000000, 0, None, 3, 0x110000, None
            )
            error = c.get_last_error()
            if handle != w.HANDLE(-1).value:
                kernel.CloseHandle(handle)
                raise AssertionError("restricted token connected")
            assert error == 5, error
        finally:
            checked(adv.RevertToSelf())
        # Normal account still connects, with the actual verified data handle.
        from reframe.transport import connect_mpv

        connection = connect_mpv(name, player_pid=process.pid)
        try:
            assert connection.command("get_property", "pid") == process.pid
        finally:
            connection.terminate()
    finally:
        if sid:
            kernel.LocalFree(sid)
        if restricted:
            kernel.CloseHandle(restricted)
        if token:
            kernel.CloseHandle(token)
        process.terminate()
        process.wait(timeout=5)


def test_network_guard_denies_tcp_dns_and_udp_in_isolated_process():
    code = """from reframe.models import configure_inference
import socket
from reframe.security import SecurityError
configure_inference()
for operation in (lambda: socket.getaddrinfo('example.com',443),
                  lambda: socket.socket().connect(('127.0.0.1',9)),
                  lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x',('127.0.0.1',9))):
    try: operation()
    except SecurityError: pass
    else: raise AssertionError('network allowed')
from reframe.network import setup_network
with setup_network():
    socket.getaddrinfo('127.0.0.1',443,flags=socket.AI_NUMERICHOST)
print('NETWORK_DENIAL_PASS')
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"NETWORK_DENIAL_PASS" in result.stdout


def test_restricted_upstream_paths_without_model_or_gpu(tmp_path):
    # Real pinned dependency code, with deserialization replaced by a benign
    # recorder. No arbitrary checkpoint or exploit string is executed.
    code = """from reframe.models import configure_inference, load_yolo
from reframe.security import SecurityError
configure_inference()
from ultralytics.nn import tasks
from ultralytics.utils import downloads
from contextlib import contextmanager
assert tasks.SAFE_LOAD and tasks._SafeLoad.SUPPORTED
calls=[]
@contextmanager
def loading(_): yield
tasks._SafeLoad.loading = loading
downloads.attempt_download_asset = lambda p: p
def load(*a, **kw):
    calls.append(kw)
    raise ModuleNotFoundError('synthetic missing dependency', name='not_a_real_dependency')
tasks.torch_load=load
tasks.check_requirements=lambda *a,**k: (_ for _ in ()).throw(AssertionError('installer reached'))
try: tasks.torch_safe_load('fixture.pt')
except ModuleNotFoundError: pass
else: raise AssertionError('load accepted')
assert len(calls)==1 and calls[0]['weights_only'] is True
# Guard the model-construction activation entry, without evaluating a payload.
tasks.eval=lambda *a: (_ for _ in ()).throw(AssertionError('eval reached'))
try: tasks.parse_model({'nc':1,'activation':'unsupported_activation','backbone':[],'head':[]},3,False)
except (ValueError,TypeError): pass
else: raise AssertionError('invalid activation accepted')
tasks._SafeLoad.SUPPORTED=False
try: load_yolo('fixture.pt')
except SecurityError: pass
else: raise AssertionError('unsafe fallback accepted')
print('RESTRICTED_PATH_PASS')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, timeout=25
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"RESTRICTED_PATH_PASS" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Native data-handle SQOS")
def test_native_data_handle_is_identification_only():
    import ctypes as c
    from ctypes import wintypes as w

    from reframe.windows_security import adv, checked, kernel, open_pipe_identification

    kernel.CreateNamedPipeW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        w.DWORD,
        c.c_void_p,
    ]
    kernel.CreateNamedPipeW.restype = w.HANDLE
    kernel.WriteFile.argtypes = [
        w.HANDLE,
        c.c_void_p,
        w.DWORD,
        c.POINTER(w.DWORD),
        c.c_void_p,
    ]
    kernel.ReadFile.argtypes = kernel.WriteFile.argtypes
    adv.ImpersonateNamedPipeClient.argtypes = [w.HANDLE]
    adv.OpenThreadToken.argtypes = [w.HANDLE, w.DWORD, w.BOOL, c.POINTER(w.HANDLE)]
    kernel.GetCurrentThread.restype = w.HANDLE
    name = "\\\\.\\pipe\\reframe-sqos-" + os.urandom(12).hex()
    server = kernel.CreateNamedPipeW(name, 3, 0, 1, 4096, 4096, 0, None)
    assert server != w.HANDLE(-1).value
    client, token = None, w.HANDLE()
    try:
        client = open_pipe_identification(name, 0xC0000000, 0, None, 3, 0, None)
        assert client != w.HANDLE(-1).value
        moved, buffer = w.DWORD(), c.create_string_buffer(1)
        checked(kernel.WriteFile(client, b"x", 1, c.byref(moved), None))
        checked(kernel.ReadFile(server, buffer, 1, c.byref(moved), None))
        checked(adv.ImpersonateNamedPipeClient(server))
        try:
            checked(
                adv.OpenThreadToken(kernel.GetCurrentThread(), 8, True, c.byref(token))
            )
            level, size = w.DWORD(), w.DWORD()
            checked(
                adv.GetTokenInformation(
                    token, 9, c.byref(level), c.sizeof(level), c.byref(size)
                )
            )
            assert level.value == 1  # SecurityIdentification, not SecurityImpersonation
        finally:
            checked(adv.RevertToSelf())
    finally:
        if token:
            kernel.CloseHandle(token)
        if client and client != w.HANDLE(-1).value:
            kernel.CloseHandle(client)
        kernel.CloseHandle(server)


@pytest.mark.skipif(os.name != "nt", reason="Native running-image version gate")
def test_unknown_windows_player_refused_before_endpoint_creation(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from reframe import security

    # Isolate version refusal from the test interpreter installation ACL.
    monkeypatch.setattr(security, "trusted_executable", lambda path: Path(path))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"mpv v0.40.0\n"),
    )
    monkeypatch.setattr(
        security,
        "runtime_root",
        lambda: (_ for _ in ()).throw(AssertionError("endpoint creation reached")),
    )
    with pytest.raises(SecurityError, match="0.41"):
        security.prepare_endpoint(os.getpid())
