"""Fail-closed local filesystem and endpoint trust checks (no media access)."""

import json
import os
import re
import stat
import tempfile
from pathlib import Path


class SecurityError(ValueError):
    pass


def strict_json(raw, limit=16384):
    def pairs(values):
        data = {}
        for key, value in values:
            if key in data:
                raise ValueError("Duplicate JSON field")
            data[key] = value
        return data

    def constant(_):
        raise ValueError("Nonfinite JSON")

    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise ValueError("JSON exceeds limit")
    try:
        return json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)
    except RecursionError:
        raise ValueError("JSON nesting exceeds parser limit") from None


def trusted_path(path, *, private=False):
    """Reject links/reparse points and other-user writable existing ancestors.

    Same-user attackers and administrators are outside the isolation boundary.
    Missing trailing components are permitted; callers must use exclusive creation.
    """
    path = Path(path).absolute()
    for entry in reversed((path, *path.parents)):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SecurityError(f"Link/reparse point is not trusted: {entry}")
        if os.name == "nt":
            from .windows_security import check_path

            check_path(entry, private=private and entry == path, ancestor=entry != path)
        elif (
            info.st_uid not in (0, os.getuid())
            or info.st_mode & 0o022
            or (
                private
                and entry == path
                and (info.st_uid != os.getuid() or info.st_mode & 0o077)
            )
        ):
            # /tmp is only an ancestor, and sticky-bit directories cannot replace
            # another user's owned child. Never trust /tmp itself as a target.
            if not (
                entry != path
                and stat.S_ISDIR(info.st_mode)
                and info.st_uid == 0
                and info.st_mode & stat.S_ISVTX
            ):
                raise SecurityError(f"Unsafe ownership or permissions: {entry}")
    return path


def private_dir(path):
    path = trusted_path(path)
    if not path.exists():
        if not path.parent.is_dir():
            private_dir(path.parent)
        if os.name == "nt":
            from .windows_security import make_private_dir

            make_private_dir(path)
        else:
            path.mkdir(mode=0o700)
    trusted_path(path, private=True)
    if not path.is_dir():
        raise SecurityError("Private directory is not a directory")
    return path


def trusted_executable(path):
    """Permit the owned interpreter symlinks used by Unix virtual environments."""
    path = Path(path).absolute()
    if os.name != "nt":
        for _ in range(40):
            if not path.is_symlink():
                break
            trusted_path(path.parent)
            if path.lstat().st_uid not in (0, os.getuid()):
                raise SecurityError("Interpreter link is not owned by this user/root")
            target = path.readlink()
            path = Path(
                os.path.abspath(
                    target if target.is_absolute() else path.parent / target
                )
            )
        else:
            raise SecurityError("Interpreter link chain exceeds limit")
    path = trusted_path(path)
    if not path.is_file():
        raise SecurityError("Executable must be an existing trusted file")
    return path


def runtime_root():
    base = (
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        if os.name == "nt"
        else Path(tempfile.gettempdir()).resolve()
    )
    identity = str(os.getuid()) if os.name != "nt" else "user"
    return private_dir(base / ("mpv-reframe-" + identity))


def prepare_endpoint(pid):
    if type(pid) is not int or pid <= 0:
        raise SecurityError("Invalid player PID")
    # mkdtemp is exclusive; the checked root prevents cross-user substitution.
    if os.name == "nt":
        from .windows_security import require_secure_player

        require_secure_player(pid)
    root = runtime_root()
    if os.name == "nt":
        return "mpv-reframe-" + str(pid) + "-" + os.urandom(16).hex()
    directory = Path(tempfile.mkdtemp(prefix=f"p{pid}-", dir=root))
    endpoint = str(directory / "ipc")
    if len(os.fsencode(endpoint)) > 100:
        directory.rmdir()  # our new, empty directory only
        raise SecurityError("Unix IPC path exceeds portable 100-byte limit")
    return endpoint


def validate_endpoint(endpoint, *, player_pid=None, harden=False):
    if not isinstance(endpoint, str) or not endpoint or "\0" in endpoint:
        raise SecurityError("Invalid IPC endpoint")
    if os.name == "nt":
        from .windows_security import check_pipe

        name = endpoint.removeprefix("\\\\.\\pipe\\")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", name):
            raise SecurityError("Only local named pipes are supported")
        check_pipe(name, player_pid=player_pid, harden=False)
        return name
    path = Path(endpoint)
    if not path.is_absolute() or len(os.fsencode(endpoint)) > 100:
        raise SecurityError("IPC must be an absolute Unix socket path <=100 bytes")
    trusted_path(path.parent, private=True)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise SecurityError("IPC is not our owned socket")
    if harden:
        # Parent is private; no other user can swap the socket during chmod.
        os.chmod(path, 0o600, follow_symlinks=False)
    trusted_path(path, private=True)
    return endpoint


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--player-pid", type=int, required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--harden", action="store_true")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    trusted_path(args.backend)
    trusted_executable(args.python)
    if not Path(args.backend).is_absolute() or not Path(args.python).is_absolute():
        raise SecurityError("Backend and interpreter must be absolute trusted paths")
    if os.name == "nt" and args.command == "verify":
        from .windows_security import require_secure_player

        require_secure_player(args.player_pid)
    value = (
        prepare_endpoint(args.player_pid)
        if args.command == "prepare"
        else validate_endpoint(
            args.endpoint, player_pid=args.player_pid, harden=args.harden
        )
    )
    print(json.dumps({"endpoint": value}))


if __name__ == "__main__":
    main()
