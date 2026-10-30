"""Executable discovery without requiring a shell PATH refresh."""

import os
import shutil
import sys
from pathlib import Path


def find_mpv(override: str | None = None) -> str:
    candidate = override or os.environ.get("REFRAME_MPV") or shutil.which("mpv")
    if candidate:
        resolved = shutil.which(candidate) or candidate
        if Path(resolved).is_file():
            from .security import trusted_path

            return str(trusted_path(resolved))
        raise FileNotFoundError(f"mpv executable not found: {candidate}")
    installed = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "MPV Player/mpv.exe"
    )
    if installed.is_file():
        from .security import trusted_path

        return str(trusted_path(installed))
    raise FileNotFoundError("Install mpv or set REFRAME_MPV / --mpv-path")


def normalize_endpoint(endpoint):
    """The IPC library expects an unprefixed Windows pipe or a Unix path."""
    return endpoint.removeprefix(chr(92) * 2 + "." + chr(92) + "pipe" + chr(92))


def validate_backend_runtime(backend):
    """Reject known Windows native-loader path failures before GPU imports.

    This cannot repair Python site initialization in an already broken editable
    environment; setup must reject that path before creating the environment.
    Unicode media and mpv configuration paths are not constrained here.
    """
    if os.name != "nt":
        return
    for label, value in (
        ("backend", backend),
        ("virtual environment", sys.prefix),
        ("base Python", sys.base_prefix),
    ):
        if not str(Path(value).absolute()).isascii():
            raise ValueError(
                f"Unsupported Windows {label} path: use ASCII-only absolute paths "
                "for the backend, virtual environment and base Python. "
                "Re-extract there and recreate .venv; do not move an environment "
                "or bypass trust checks. Unicode media/configuration paths remain "
                "supported. See WINDOWS_VERIFICATION.md."
            )
