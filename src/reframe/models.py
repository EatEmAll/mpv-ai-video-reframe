"""Select only a verified, device-compatible cached acceleration artifact."""

import hashlib
import importlib.util
import json
import logging
from pathlib import Path


def sha256(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def compatible_engine(engine, weights, capability, runtime):
    manifest = Path(str(engine) + ".json")
    if not engine.is_file() or not weights.is_file() or not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return (
            data["capability"] == list(capability)
            and data["tensorrt"] == runtime
            and data["engine_sha256"] == sha256(engine)
            and data["weights_sha256"] == sha256(weights)
        )
    except (KeyError, ValueError, OSError):
        return False


def select_model(explicit, capability):
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"Explicit model does not exist: {path}")
        return path
    weights = Path("models/yolo26n.pt")
    engine = Path("models/yolo26n.engine")
    if (
        capability is not None
        and engine.is_file()
        and importlib.util.find_spec("tensorrt")
    ):
        import tensorrt

        if compatible_engine(engine, weights, capability, tensorrt.__version__):
            return engine
        logging.getLogger(__name__).warning(
            "Cached engine is unverified or incompatible; using PyTorch weights"
        )
    weights.parent.mkdir(exist_ok=True)
    return weights


def record_engine(weights, engine):
    import tensorrt
    import torch

    data = {
        "capability": list(torch.cuda.get_device_capability(0)),
        "gpu": torch.cuda.get_device_name(0),
        "tensorrt": tensorrt.__version__,
        "torch": torch.__version__,
        "engine_sha256": sha256(engine),
        "weights_sha256": sha256(weights),
        "precision": "fp16",
        "shape": [1, 3, 640, 640],
    }
    manifest = Path(str(engine) + ".json")
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return manifest
