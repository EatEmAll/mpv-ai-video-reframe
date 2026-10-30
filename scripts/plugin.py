"""Install/uninstall the user script or build a portable source archive."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_dir():
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "mpv"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "mpv"


def install(root, config):
    root, config = root.absolute(), config.absolute()
    for name in ("pyproject.toml", "src/reframe/__main__.py", "mpv/reframe.lua"):
        if not (root / name).is_file():
            raise ValueError(f"Backend is not prepared: missing {root / name}")
    python = root / (
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    )
    if not python.is_file():
        raise ValueError(f"Prepare the Python environment first: {python}")
    manifest = config / "reframe-install.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else {"files": {}}
    script = config / "scripts/reframe.lua"
    conf = config / "script-opts/reframe.conf"
    if script.exists() and digest(script) != previous["files"].get(
        "scripts/reframe.lua"
    ):
        raise ValueError(f"Refusing to overwrite unowned or edited script: {script}")
    script.parent.mkdir(parents=True, exist_ok=True)
    conf.parent.mkdir(parents=True, exist_ok=True)
    if script.exists():
        shutil.copy2(script, script.with_suffix(".lua.previous"))
    shutil.copy2(root / "mpv/reframe.lua", script)
    files = {"scripts/reframe.lua": digest(script)}
    previous_config = previous["files"].get("script-opts/reframe.conf")
    if not conf.exists() or digest(conf) == previous_config:
        text = (root / "mpv/reframe.conf.example").read_text(encoding="utf-8-sig")
        text = text.replace("backend_dir=", "backend_dir=" + root.as_posix(), 1)
        if conf.exists() and conf.read_text(encoding="utf-8") != text:
            shutil.copy2(conf, conf.with_suffix(".conf.previous"))
        conf.write_text(text, encoding="utf-8")
        files["script-opts/reframe.conf"] = digest(conf)
    elif previous_config:
        files["script-opts/reframe.conf"] = previous_config
    manifest.write_text(
        json.dumps({"version": 1, "files": files}, indent=2), encoding="utf-8"
    )
    for relative in files:
        destination = config / relative
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"Install verification failed: {destination}")
    return [str(script), str(conf)]


def uninstall(config):
    manifest = config / "reframe-install.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    preserved = {}
    for relative, expected in data["files"].items():
        if relative not in ("scripts/reframe.lua", "script-opts/reframe.conf"):
            raise ValueError("Invalid installation manifest")
        path = config / relative
        if path.is_file() and digest(path) == expected:
            path.unlink()
        elif path.exists():
            preserved[relative] = expected
    if preserved:
        manifest.write_text(
            json.dumps({"version": 1, "files": preserved}), encoding="utf-8"
        )
    else:
        manifest.unlink()
    return list(preserved)


def package(output):
    if output.exists():
        raise ValueError(f"Refusing to replace {output}")
    paths = [
        ROOT / n
        for n in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "scripts/plugin.py",
            "scripts/export_engine.py",
        )
    ]
    for name in ("src/reframe", "mpv", "tests"):
        paths.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr(
            "reframe-release.json",
            json.dumps({"protocol": 1, "version": "0.1.0", "format": "source-preview"}),
        )
    output.with_suffix(output.suffix + ".sha256").write_text(
        digest(output) + "  " + output.name + "\n"
    )
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
        result = (
            install(args.backend_dir, config)
            if args.command == "install"
            else uninstall(config)
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
