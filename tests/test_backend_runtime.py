import sys
from types import SimpleNamespace

import pytest

from reframe import runtime


@pytest.mark.parametrize("target", ["backend", "prefix", "base_prefix"])
def test_windows_rejects_unicode_native_runtime_paths(monkeypatch, tmp_path, target):
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    backend = tmp_path / "backend"
    if target == "backend":
        backend = tmp_path / "backend-\u05d0"
    else:
        monkeypatch.setattr(sys, target, str(tmp_path / "python-\u05d0"))
    with pytest.raises(ValueError, match="ASCII-only absolute paths"):
        runtime.validate_backend_runtime(backend)


def test_windows_accepts_ascii_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    runtime.validate_backend_runtime(tmp_path / "backend")


def test_unix_does_not_restrict_unicode_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="posix"))
    runtime.validate_backend_runtime(tmp_path / "backend-\u05d0")


def test_manual_attach_rejects_bad_backend_before_session(monkeypatch, tmp_path):
    from reframe import __main__ as cli
    from reframe import managed

    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(managed, "Session", lambda _: pytest.fail("Session started"))
    args = SimpleNamespace(backend_dir=str(tmp_path / "backend-\u05d0"))
    with pytest.raises(ValueError, match="ASCII-only"):
        managed.run_managed(args)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reframe",
            "attach",
            "--managed",
            "--pipe",
            "test",
            "--player-pid",
            "123",
            "--session-id",
            "test",
            "--lua-client",
            "reframe",
            "--backend-dir",
            args.backend_dir,
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_doctor_rejects_bad_base_python_before_model_setup(monkeypatch, tmp_path):
    from reframe import __main__ as cli
    from reframe import models

    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "python-\u05d0"))
    monkeypatch.setattr(
        models, "configure_inference", lambda: pytest.fail("Model setup started")
    )
    with pytest.raises(ValueError, match="base Python"):
        cli.doctor()
