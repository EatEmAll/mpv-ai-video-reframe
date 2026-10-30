"""Install/uninstall the user script or build a portable source archive."""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Also usable as a direct setup script before the project is installed.
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from windows_preflight import probe, supported_backend  # noqa: E402

from reframe.security import (  # noqa: E402
    SecurityError,
    strict_json,
    trusted_executable,
    trusted_path,
)

FILES = {"scripts/reframe.lua", "script-opts/reframe.conf"}
MANIFEST = "reframe-install.json"
JOURNAL = "reframe-install.pending.json"


def safe(path):
    path = trusted_path(path)
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise SecurityError("Hard-linked installer file rejected")
    return path


def read_json(path):
    safe(path)
    if path.stat().st_size > 131072:
        raise ValueError("Installer metadata exceeds limit")
    return strict_json(path.read_text(encoding="utf-8"), limit=131072)


def manifest_data(path):
    data = read_json(path) if path.exists() else {"version": 1, "files": {}}
    import re

    if (
        not isinstance(data, dict)
        or set(data) != {"version", "files"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or not isinstance(data["files"], dict)
    ):
        raise ValueError("Invalid installation manifest")
    if any(
        k not in FILES or not isinstance(v, str) or not re.fullmatch("[a-f0-9]{64}", v)
        for k, v in data["files"].items()
    ):
        raise ValueError("Invalid installation manifest")
    return data


def atomic(path, content):
    safe(path)
    fd, temporary = tempfile.mkstemp(prefix=".reframe-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        safe(path)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def installer_lock(config):
    safe(config)
    config.mkdir(parents=True, exist_ok=True)
    path = safe(config / ".reframe-install.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as file:
        safe(path)
        if not file.read(1):
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another installer owns this configuration") from None
        yield


def recover(config):
    journal = safe(config / JOURNAL)
    if not journal.exists():
        return
    data = read_json(journal)
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "changes"}
        or data["version"] != 1
        or not isinstance(data["changes"], dict)
        or not data["changes"]
        or set(data["changes"]) - (FILES | {MANIFEST})
    ):
        raise ValueError("Invalid recovery journal")
    decoded = {}
    for relative, record in data["changes"].items():
        if not isinstance(record, dict) or set(record) != {"old", "new"}:
            raise ValueError("Invalid recovery record")
        old = (
            None
            if record["old"] is None
            else base64.b64decode(record["old"], validate=True)
        )
        new = base64.b64decode(record["new"], validate=True)
        path = safe(config / relative)
        current = path.read_bytes() if path.exists() else None
        if current not in (old, new):
            raise ValueError(
                "Interrupted upgrade contains edited files; preserve and recover manually"
            )
        decoded[relative] = old
    for relative, old in decoded.items():
        path = safe(config / relative)
        if old is None:
            path.unlink(missing_ok=True)
        else:
            atomic(path, old)
    journal.unlink()


def transaction(config, changes):
    records = {}
    for relative, content in changes.items():
        path = safe(config / relative)
        old = path.read_bytes() if path.exists() else None
        records[relative] = {
            "old": None if old is None else base64.b64encode(old).decode(),
            "new": base64.b64encode(content).decode(),
        }
    journal = config / JOURNAL
    if journal.exists():
        raise ValueError("Recover pending upgrade before installing")
    serialized = json.dumps({"version": 1, "changes": records}).encode()
    if len(serialized) > 131072:
        raise ValueError("Upgrade journal exceeds recovery limit")
    atomic(journal, serialized)
    try:
        for relative, content in changes.items():
            atomic(config / relative, content)
    except BaseException:
        recover(config)
        raise
    journal.unlink()


def backup(path):
    previous = safe(path.with_suffix(path.suffix + ".previous"))
    if previous.exists():
        # Never replace a backup (it might contain a user's edits).
        return
    atomic(previous, path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_dir():
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "mpv"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "mpv"


def install(root, config):
    supported_backend(root)
    root, config = safe(root), safe(config)
    with installer_lock(config):
        recover(config)
        return _install(root, config)


def _install(root, config):
    if any(ch in str(root) for ch in "\r\n"):
        raise ValueError("Backend path cannot contain configuration line breaks")
    for name in ("pyproject.toml", "src/reframe/__main__.py", "mpv/reframe.lua"):
        if not safe(root / name).is_file():
            raise ValueError(f"Backend is not prepared: missing {root / name}")
    python = root / (
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    )
    if not trusted_executable(python).is_file():
        raise ValueError(f"Prepare the Python environment first: {python}")
    manifest = config / "reframe-install.json"
    previous = manifest_data(manifest)
    script = safe(config / "scripts/reframe.lua")
    conf = safe(config / "script-opts/reframe.conf")
    if script.exists() and digest(script) != previous["files"].get(
        "scripts/reframe.lua"
    ):
        raise ValueError(f"Refusing to overwrite unowned or edited script: {script}")
    script.parent.mkdir(parents=True, exist_ok=True)
    conf.parent.mkdir(parents=True, exist_ok=True)
    if script.exists():
        backup(script)
    source = (root / "mpv/reframe.lua").read_bytes()
    changes = {"scripts/reframe.lua": source}
    files = {"scripts/reframe.lua": hashlib.sha256(source).hexdigest()}
    previous_config = previous["files"].get("script-opts/reframe.conf")
    if not conf.exists() or digest(conf) == previous_config:
        text = safe(root / "mpv/reframe.conf.example").read_text(encoding="utf-8-sig")
        text = text.replace("backend_dir=", "backend_dir=" + root.as_posix(), 1)
        if conf.exists() and conf.read_text(encoding="utf-8") != text:
            backup(conf)
        changes["script-opts/reframe.conf"] = text.encode("utf-8")
        files["script-opts/reframe.conf"] = hashlib.sha256(
            changes["script-opts/reframe.conf"]
        ).hexdigest()
    elif previous_config:
        files["script-opts/reframe.conf"] = previous_config
    changes[MANIFEST] = json.dumps({"version": 1, "files": files}, indent=2).encode()
    transaction(config, changes)
    for relative in files:
        destination = config / relative
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"Install verification failed: {destination}")
    return [str(script), str(conf)]


def uninstall(config):
    config = safe(config)
    with installer_lock(config):
        recover(config)
        return _uninstall(config)


def _uninstall(config):
    manifest = safe(config / MANIFEST)
    if not manifest.exists():
        return []
    data = manifest_data(manifest)
    preserved = {}
    for relative, expected in data["files"].items():
        if relative not in ("scripts/reframe.lua", "script-opts/reframe.conf"):
            raise ValueError("Invalid installation manifest")
        path = safe(config / relative)
        if path.is_file() and digest(path) == expected:
            path.unlink()
        elif path.exists():
            preserved[relative] = expected
    if preserved:
        atomic(manifest, json.dumps({"version": 1, "files": preserved}).encode())
    else:
        manifest.unlink()
    return list(preserved)


def package(output):
    safe(output)
    checksum = safe(output.with_suffix(output.suffix + ".sha256"))
    if output.exists() or checksum.exists():
        raise ValueError(f"Refusing to replace {output}")
    paths = [
        ROOT / n
        for n in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "SECURITY.md",
            "SECURITY_REVIEW.md",
            "SECURITY_ADVISORIES.md",
            "security-dependencies.json",
            "security-upstream-sources.json",
            "MPV_PROVENANCE.md",
            "mpv-provenance.json",
            "scripts/security_audit.py",
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "scripts/plugin.py",
            "scripts/export_engine.py",
            "WINDOWS_VERIFICATION.md",
            "WINDOWS_VERIFICATION_RESULTS.md",
            "WINDOWS_VERIFICATION_RESULTS.json",
            "LINUX_VERIFICATION.md",
        )
    ]
    paths.extend(
        p
        for p in (ROOT / "scripts").iterdir()
        if p.suffix in {".py", ".ps1"} and p not in paths
    )
    for name in ("src/reframe", "mpv", "tests"):
        paths.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            safe(path)
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr(
            "reframe-release.json",
            json.dumps({"protocol": 1, "version": "0.1.0", "format": "source-preview"}),
        )
    with checksum.open("x", encoding="utf-8") as file:
        file.write(digest(output) + "  " + output.name + "\n")
    return str(output.absolute())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("install", "uninstall", "package"))
    p.add_argument("--backend-dir", type=Path, default=ROOT)
    p.add_argument("--config-dir", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if args.command == "package":
        if args.output is None:
            p.error("package requires --output")
        result = package(args.output)
    else:
        config = args.config_dir or config_dir()
        if args.command == "install":
            try:
                probe(args.backend_dir)
            except (ValueError, RuntimeError, OSError) as error:
                p.error(str(error))
        result = (
            install(args.backend_dir, config)
            if args.command == "install"
            else uninstall(config)
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
