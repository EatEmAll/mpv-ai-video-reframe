"""Select only a verified, device-compatible cached acceleration artifact."""

import hashlib
import importlib.util
import json
import logging
import os
import tempfile
from pathlib import Path

from .security import SecurityError, private_dir, strict_json, trusted_path

# GitHub releases API digest, retrieved 2026-10-30 over authenticated HTTPS.
# Pin updates require a source review; a local manifest cannot authorize weights.
WEIGHTS_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"
)
WEIGHTS_SHA256 = "9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef"
WEIGHTS_SIZE = 5544453


def verify_weights(path):
    path = trusted_path(path)
    if path.suffix != ".pt" or not path.is_file():
        raise SecurityError("Only pinned local YOLO26n .pt weights are supported")
    if path.stat().st_size != WEIGHTS_SIZE or sha256(path) != WEIGHTS_SHA256:
        raise SecurityError("Model integrity check failed before deserialization")
    return path


def prepare_weights():
    """Explicit setup-only download; bounded, hashed, then atomically installed."""
    import urllib.request

    root = private_dir(Path("models"))
    destination = root / "yolo26n.pt"
    trusted_path(destination)
    if destination.exists():
        return verify_weights(destination)
    fd, temporary = tempfile.mkstemp(prefix=".download-", dir=root)
    try:
        with (
            os.fdopen(fd, "wb") as file,
            _download_response(urllib.request, WEIGHTS_URL) as response,
        ):
            if not response.url.startswith("https://"):
                raise SecurityError("Insecure model redirect")
            remaining = WEIGHTS_SIZE + 1
            while remaining:
                chunk = response.read(min(65536, remaining))
                if not chunk:
                    break
                file.write(chunk)
                remaining -= len(chunk)
            file.flush()
            os.fsync(file.fileno())
        temporary = Path(temporary)
        if (
            temporary.stat().st_size != WEIGHTS_SIZE
            or sha256(temporary) != WEIGHTS_SHA256
        ):
            raise SecurityError("Downloaded model size/checksum mismatch")
        if destination.exists():
            return verify_weights(destination)
        os.replace(temporary, destination)
        return verify_weights(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure_inference():
    """Set controls before importing Ultralytics; do not mutate user settings."""
    import sys

    tasks = sys.modules.get("ultralytics.nn.tasks")
    if tasks is not None and (not tasks.SAFE_LOAD or not tasks._SafeLoad.SUPPORTED):
        raise SecurityError(
            "Ultralytics was initialized without a supported restricted loader"
        )
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["ULTRALYTICS_SAFE_LOAD"] = "true"
    os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] = "1"
    from .network import install_network_guard

    install_network_guard()
    os.environ["YOLO_CONFIG_DIR"] = str(private_dir(Path("runs") / "ultralytics"))


def sha256(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def secure_tensorrt(runtime):
    import re

    version = re.match(r"^(\d+)\.(\d+)(?:\.|$)", runtime)
    return bool(version and tuple(map(int, version.groups())) >= (11, 2))


def compatible_engine(engine, weights, capability, runtime):
    if not secure_tensorrt(runtime):
        return False
    manifest = Path(str(engine) + ".json")
    if not engine.is_file() or not weights.is_file() or not manifest.is_file():
        return False
    try:
        trusted_path(engine)
        trusted_path(weights)
        trusted_path(manifest)
        if manifest.stat().st_size > 16384:
            return False
        data = strict_json(manifest.read_text(encoding="utf-8"))
        return (
            data["capability"] == list(capability)
            and data["tensorrt"] == runtime
            and data["engine_sha256"] == sha256(engine)
            and data["weights_sha256"] == sha256(weights)
        )
    except (KeyError, ValueError, OSError, TypeError):
        return False


def select_model(explicit, capability):
    if explicit is not None:
        path = Path(explicit)
        if path.suffix == ".pt":
            return verify_weights(path)
        if path.suffix != ".engine":
            raise SecurityError(
                "Only pinned .pt or locally built verified .engine supported"
            )
        weights = verify_weights(path.with_suffix(".pt"))
        if capability is None:
            raise SecurityError("Engine requires CUDA")
        import tensorrt

        if not compatible_engine(path, weights, capability, tensorrt.__version__):
            raise SecurityError("Engine provenance/integrity/runtime mismatch")
        return path
    weights = verify_weights(Path("models/yolo26n.pt"))
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
    return weights


def record_engine(weights, engine):
    verify_weights(weights)
    trusted_path(engine)
    import tensorrt
    import torch

    if not secure_tensorrt(tensorrt.__version__):
        raise SecurityError("TensorRT >=11.2 required by CVE-2026-65122")
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
    manifest = trusted_path(Path(str(engine) + ".json"))
    fd, temporary = tempfile.mkstemp(prefix=".engine-manifest-", dir=manifest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, manifest)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return manifest


def _download_response(request, url):
    # Only this explicit, fixed-URL setup operation can use Python networking.
    from .network import setup_network

    with setup_network():
        return request.urlopen(url, timeout=30)


def load_yolo(path):
    """Require effective restricted loading, even after an earlier upstream import."""
    configure_inference()
    from ultralytics import YOLO
    from ultralytics.nn import tasks

    if not tasks.SAFE_LOAD or not tasks._SafeLoad.SUPPORTED:
        raise SecurityError(
            "Restricted Ultralytics loader unavailable; restart with safe loading enabled"
        )
    path = Path(path)
    if path.suffix == ".pt":
        path = verify_weights(path)
    elif path.suffix == ".engine":
        import torch

        capability = (
            torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None
        )
        path = select_model(path, capability)
    else:
        raise SecurityError("Unsupported model format")
    return YOLO(str(path))
