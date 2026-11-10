import json

from reframe.models import compatible_engine, sha256


def test_engine_requires_matching_device_runtime_and_content(tmp_path):
    engine = tmp_path / "model.engine"
    weights = tmp_path / "model.pt"
    engine.write_bytes(b"engine")
    weights.write_bytes(b"weights")
    manifest = tmp_path / "model.engine.json"
    data = {
        "capability": [8, 6],
        "tensorrt": "11.2.1.2",
        "engine_sha256": sha256(engine),
        "weights_sha256": sha256(weights),
    }
    manifest.write_text(json.dumps(data))
    assert compatible_engine(engine, weights, (8, 6), "11.2.1.2")
    assert not compatible_engine(engine, weights, (12, 0), "11.2.1.2")
    assert not compatible_engine(engine, weights, (8, 6), "11")
    engine.write_bytes(b"different engine")
    assert not compatible_engine(engine, weights, (8, 6), "11.2.1.2")


def test_vulnerable_tensorrt_never_accepted():
    from reframe.models import secure_tensorrt

    for version in ("10.16.1.11", "11.1.0", "unknown", "11"):
        assert not secure_tensorrt(version)
    assert secure_tensorrt("11.2.1.2")


def test_blocked_tensorrt_falls_back_to_pytorch_weights(tmp_path, monkeypatch):
    import builtins
    import importlib.util

    from reframe import models

    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "yolo26n.engine").write_bytes(b"engine")
    weights = tmp_path / "models" / "yolo26n.pt"
    weights.write_bytes(b"weights")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(models, "verify_weights", lambda path: path)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "tensorrt":
            raise ImportError("An Application Control policy has blocked this file.")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert models.select_model(None, (8, 6)) == models.Path("models/yolo26n.pt")
