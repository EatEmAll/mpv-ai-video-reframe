import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from windows_preflight import probe, supported_backend  # noqa: E402


def test_unicode_backend_fails_before_config_is_created(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "setup_plugin", ROOT / "scripts/plugin.py"
    )
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    monkeypatch.setattr(
        plugin, "supported_backend", lambda p: supported_backend(p, windows=True)
    )
    config = tmp_path / "config שלום"
    with pytest.raises(ValueError, match="ASCII-only"):
        plugin.install(tmp_path / "backend שלום", config)
    assert not config.exists()


def test_path_constraint_only_applies_to_windows_backend(tmp_path):
    supported_backend(tmp_path / "backend space [1]", windows=True)
    supported_backend(tmp_path / "backend שלום", windows=False)


def test_native_abort_is_contained_and_actionable(tmp_path, monkeypatch):
    import subprocess

    import reframe.security

    monkeypatch.setattr(reframe.security, "trusted_path", lambda p: p)
    monkeypatch.setattr(reframe.security, "trusted_executable", lambda p: p)
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, str(tmp_path), ""),
            subprocess.CompletedProcess(
                [], 3221226505, "", "Invalid handle. Cannot load symbol cudnnGetVersion"
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(responses))
    with pytest.raises(RuntimeError, match="0xc0000409"):
        probe(tmp_path)


def test_failed_precision_finalization_leaves_engine_unselectable(
    tmp_path, monkeypatch
):
    import json
    from types import SimpleNamespace

    import export_windows_engine
    import plugin
    import windows_preflight

    import reframe.models as models
    import reframe.security as security

    weights = tmp_path / "yolo26n.pt"
    weights.write_bytes(b"fixture")
    engine = weights.with_suffix(".engine")
    manifest = Path(str(engine) + ".json")

    def export(**kwargs):
        assert kwargs["quantize"] == 32
        engine.write_bytes(b"local test artifact")
        return engine

    def record(*args):
        manifest.write_text(json.dumps({"precision": "fp16"}))
        return manifest

    def fail(*args):
        raise OSError("simulated metadata write failure")

    monkeypatch.setattr(windows_preflight, "inspect_backend", lambda p: None)
    monkeypatch.setattr(models, "configure_inference", lambda: None)
    monkeypatch.setattr(models, "verify_weights", lambda p: p)
    monkeypatch.setattr(models, "load_yolo", lambda p: SimpleNamespace(export=export))
    monkeypatch.setattr(models, "record_engine", record)
    monkeypatch.setattr(security, "trusted_path", lambda p: p)
    monkeypatch.setattr(plugin, "atomic", fail)
    monkeypatch.setitem(
        sys.modules, "tensorrt", SimpleNamespace(__version__="11.2.1.2")
    )
    monkeypatch.setattr(sys, "argv", ["export", "--model", str(weights)])
    with pytest.raises(OSError, match="simulated"):
        export_windows_engine.main()
    assert engine.is_file()
    assert not manifest.exists()
    assert not models.compatible_engine(engine, weights, (8, 6), "11.2.1.2")
