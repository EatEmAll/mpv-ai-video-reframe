"""Executable discovery without requiring a shell PATH refresh."""

import os
import shutil
from pathlib import Path


def find_mpv(override: str | None = None) -> str:
    candidate = override or os.environ.get("REFRAME_MPV") or shutil.which("mpv")
    if candidate:
        resolved = shutil.which(candidate) or candidate
        if Path(resolved).is_file():
            return str(Path(resolved).resolve())
        raise FileNotFoundError(f"mpv executable not found: {candidate}")
    installed = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "MPV Player/mpv.exe"
    )
    if installed.is_file():
        return str(installed)
    raise FileNotFoundError("Install mpv or set REFRAME_MPV / --mpv-path")


def normalize_endpoint(endpoint):
    """The IPC library expects an unprefixed Windows pipe or a Unix path."""
    return endpoint.removeprefix(chr(92) * 2 + "." + chr(92) + "pipe" + chr(92))
