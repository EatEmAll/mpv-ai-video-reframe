"""Fail safely before loading native inference libraries on Windows.

This script uses only the standard library until the isolated probe starts.
Run with the prepared backend interpreter; doctor alone does not run inference.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path


def supported_backend(path, windows=None):
    windows = os.name == "nt" if windows is None else windows
    if windows and not str(Path(path).absolute()).isascii():
        raise ValueError(
            "Unsupported Windows backend path: use an ASCII-only absolute path for "
            "the backend, virtual environment and base Python installation. "
            "Re-extract the source there and recreate .venv (do not move a venv, "
            "use a junction, or disable trust checks). Unicode media and mpv "
            "configuration paths remain supported. See WINDOWS_VERIFICATION.md."
        )


def inspect_backend(root):
    root = Path(root).absolute()
    supported_backend(root)
    python = root / (
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    )
    from reframe.security import trusted_executable, trusted_path

    trusted_path(root)
    trusted_executable(python)
    # -S inspects the interpreter without executing a potentially broken .pth.
    inspection = subprocess.run(
        [str(python), "-X", "utf8", "-S", "-c", "import sys; print(sys.base_prefix)"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    if inspection.returncode:
        raise RuntimeError("Cannot inspect prepared interpreter: " + inspection.stderr)
    supported_backend(inspection.stdout.strip())
    return python


def probe(root, model=None, cpu=False, timeout=120):
    python = inspect_backend(root)
    code = (
        "import json,sys,numpy as np; from reframe.detector import Detector; "
        "d=Detector(sys.argv[1] or None); "
        "[d.detect(np.zeros((360,640,3),dtype=np.uint8)) for _ in range(3)]; "
        "print(json.dumps(dict(device=d.device,model=str(d.model_path),inference=True)))"
    )
    env = os.environ.copy()
    if cpu:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    try:
        result = subprocess.run(
            [str(python), "-X", "utf8", "-c", code, str(model or "")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "Inference preflight timed out; installation not approved"
        ) from error
    if result.returncode:
        raise RuntimeError(
            f"Inference preflight failed (exit {result.returncode & 0xFFFFFFFF:#x}). "
            "Do not enable the extension. Check pinned dependencies, GPU driver and "
            "ASCII backend/base-Python paths; rerun with --cpu to diagnose.\n"
            + result.stderr[-4000:]
        )
    return json.loads(result.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-dir", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--model", type=Path)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument(
        "--paths-only",
        action="store_true",
        help="Check interpreter paths before model preparation",
    )
    args = parser.parse_args()
    try:
        result = (
            {"paths": bool(inspect_backend(args.backend_dir))}
            if args.paths_only
            else probe(args.backend_dir, args.model, args.cpu)
        )
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
