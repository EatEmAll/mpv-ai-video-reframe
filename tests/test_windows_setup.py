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

    import export_fp32_engine
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
        assert kwargs["nms"] is False  # keep the NMS-free head; no torchvision import
        header = json.dumps({"end2end": True}).encode()
        engine.write_bytes(
            len(header).to_bytes(4, "little", signed=True)
            + header
            + b"local test artifact"
        )
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
        export_fp32_engine.main()
    assert engine.is_file()
    assert not manifest.exists()
    assert not models.compatible_engine(engine, weights, (8, 6), "11.2.1.2")


def engine_with_header(path, header):
    import json

    raw = json.dumps(header).encode()
    path.write_bytes(len(raw).to_bytes(4, "little", signed=True) + raw + b"engine")
    return path


def test_engine_metadata_reads_the_ultralytics_header(tmp_path):
    import reframe.models as models
    from reframe.security import SecurityError

    engine = engine_with_header(tmp_path / "a.engine", {"end2end": True, "stride": 32})
    assert models.engine_metadata(engine) == {"end2end": True, "stride": 32}
    for raw in (
        b"",  # no header at all
        bytes(4),  # zero-length header
        (2**31 - 1).to_bytes(4, "little") + b"{}",  # oversized header
        (4).to_bytes(4, "little") + b"{}",  # truncated header
        (2).to_bytes(4, "little") + b"[]",  # header is not an object
    ):
        engine.write_bytes(raw)
        with pytest.raises(SecurityError):
            models.engine_metadata(engine)


def test_export_refuses_an_engine_that_still_needs_nms(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import export_fp32_engine
    import windows_preflight

    import reframe.models as models
    import reframe.security as security

    weights = tmp_path / "yolo26n.pt"
    weights.write_bytes(b"fixture")
    engine = weights.with_suffix(".engine")
    manifest = Path(str(engine) + ".json")

    def export(**kwargs):
        return engine_with_header(engine, {"end2end": False})

    def record(*args):
        raise AssertionError("an NMS engine must never get a manifest")

    monkeypatch.setattr(windows_preflight, "inspect_backend", lambda p: None)
    monkeypatch.setattr(models, "configure_inference", lambda: None)
    monkeypatch.setattr(models, "verify_weights", lambda p: p)
    monkeypatch.setattr(models, "load_yolo", lambda p: SimpleNamespace(export=export))
    monkeypatch.setattr(models, "record_engine", record)
    monkeypatch.setattr(security, "trusted_path", lambda p: p)
    monkeypatch.setitem(
        sys.modules, "tensorrt", SimpleNamespace(__version__="11.2.1.2")
    )
    monkeypatch.setattr(sys, "argv", ["export", "--model", str(weights)])
    with pytest.raises(RuntimeError, match="NMS-free"):
        export_fp32_engine.main()
    assert not manifest.exists()
    assert json  # silence unused-import linters in older editors
