"""Portable hashes and environment evidence; no username or absolute paths."""

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path


def source_manifest(root):
    paths = [root / n for n in ("pyproject.toml", "uv.lock")]
    for directory in ("src", "mpv", "scripts", "tests"):
        paths.extend(
            p
            for p in (root / directory).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix in {".py", ".lua", ".yaml", ".ps1"}
        )
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def environment(root, clip, model=None):
    import torch

    from reframe.runtime import find_mpv

    mpv = find_mpv()
    mpv_version = subprocess.check_output(
        [mpv, "--version"], text=True, encoding="utf-8"
    )

    packages = {}
    for name in (
        "torch",
        "torchvision",
        "ultralytics",
        "av",
        "numpy",
        "tensorrt-cu13",
        "onnx",
        "nvidia-modelopt",
        "python-mpv-jsonipc",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    selected = None
    if model is not None:
        from reframe.security import trusted_path

        path = trusted_path(model)
        selected = {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if path.suffix == ".engine":
            for key, companion in (
                ("weights_sha256", path.with_suffix(".pt")),
                ("manifest_sha256", Path(str(path) + ".json")),
            ):
                selected[key] = hashlib.sha256(
                    trusted_path(companion).read_bytes()
                ).hexdigest()
    return {
        "source": source_manifest(root),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "cuda": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "capability": torch.cuda.get_device_capability(0)
        if torch.cuda.is_available()
        else None,
        "mpv": mpv_version.splitlines()[0],
        "mpv_version": mpv_version.splitlines(),
        "mpv_sha256": hashlib.sha256(Path(mpv).read_bytes()).hexdigest(),
        "processor": platform.processor(),
        "fixtures": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (clip, clip.with_suffix(".gt.json"))
        },
        "models": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "models").glob("yolo26n.*")
            if p.suffix in {".pt", ".engine", ".json"}
        },
        "selected_model": selected,
    }


def write_manifest(output, clip, model=None):
    root = Path(__file__).resolve().parents[1]
    data = environment(root, clip, model)
    (output / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data["source"]
