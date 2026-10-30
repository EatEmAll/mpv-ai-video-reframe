# mpv Reframe

The extension runs inside ordinary mpv and supervises a local Python companion.
`Ctrl+r` toggles following; no custom player executable is required. The script,
IPC transport, installer and backend control code support Windows, Linux and
macOS. Inference selects CUDA, Apple MPS or CPU. Windows/NVIDIA is the platform
validated locally; Linux/macOS and MPS/CPU performance are not yet verified.

## Prepare the backend

Install mpv 0.41 or newer, Python 3.12, and uv. Extract the source release or clone
this repository, then run from its root:

```text
uv sync --locked
uv run reframe doctor
```

`doctor` explicitly prepares model weights and reports the selected device.
On Windows/NVIDIA, the accelerated setup used for the existing performance gates is:

```text
uv sync --locked --group accel
uv run --group accel reframe doctor
uv run --group accel python scripts/export_engine.py
```

TensorRT engines are specific to the local GPU/runtime. Keep the `.pt` weights;
the backend uses them when a cached engine is incompatible. CPU/MPS may not keep
up at high resolutions or speeds. Playback-time activation never installs packages
or downloads missing weights. Prepare the environment again after dependency updates.

## Install and use

From the prepared repository, run:

```text
uv run python scripts/plugin.py install
```

The installer writes `scripts/reframe.lua` and `script-opts/reframe.conf` inside
mpv's configuration directory. Defaults are `%APPDATA%/mpv` on Windows and
`$XDG_CONFIG_HOME/mpv` or `~/.config/mpv` on Linux/macOS. To select a portable or
custom configuration, pass `--config-dir "<existing mpv config directory>"`.
Use `--backend-dir "<absolute prepared backend directory>"` to select another
checkout. Keep that backend directory in place after installation.

Restart mpv, open a local video, and press **Ctrl+r**. Enabling in an empty player
waits for a video. It stays enabled across playlist entries. Press Ctrl+r again
to stop, release inference resources, and restore your original view settings.
The installer does not change `mpv.conf`, key configuration, or a running player.

The default configuration is:

```ini
backend_dir=<absolute backend directory>
python_path=
autostart=no
toggle_key=Ctrl+r
rate=10
model=
debug_osd=no
```

`python_path` defaults to `.venv/Scripts/python.exe` on Windows or `.venv/bin/python`
on Unix. `model` may name existing `.pt` weights or a CUDA `.engine`; relative model
paths refer to the backend directory. `rate` is detection sampling (5â€“15), not the
camera framerate. Configuration changes take effect at the next enable, except
the default key binding, which is established at script load. Existing user key
bindings take precedence. Set `toggle_key=` and bind `script-binding reframe/toggle`
in `input.conf` to choose your own binding.

Additional controls, callable from mpv's console or IPC:

```text
script-message-to reframe reframe-enable
script-message-to reframe reframe-stop
script-message-to reframe reframe-toggle
script-message-to reframe reframe-status
```

## Behavior and recovery

The existing camera path remains in Python: full-height fill, box-aware following,
look-ahead and damped motion, with updates limited by source FPS Ã— speed and the
display rate. A person wider than the visible crop cannot fit completely without
changing this full-height requirement. Lua does not add a second camera loop.

Zoom, unscaled video, unsupported rotation, remote URLs, and non-video playlist
entries suspend following. Reset the unsupported mode or load a local video to
resume. Audio/video playback continues while dependencies initialize or tracking
is suspended. Seeking, playback speed, resizing and fullscreen remain normal mpv
controls.

While active, the backend owns panscan, horizontal alignment, cache, timing offset
and keepaspect-window. Disable before manually changing these five properties;
their saved values are restored when following stops. Lua also holds a restoration
snapshot for an unexpected backend exit. A crashed backend is not automatically
restarted; enable again to retry. A second companion targeting the same player is
rejected, including another managed companion.

A configured IPC endpoint is reused. Otherwise the script creates a Windows named
pipe or Unix socket that remains available for that player session, including when
following is disabled. IPC is local player control, not an authenticated network
service. No footage is uploaded. Session status is available in `user-data/reframe`;
local lifecycle and camera logs are under `<backend_dir>/runs/lua/<session-id>/`.
Logs are retained until you remove them. Startup stderr is bounded and reported
in mpv's log on process completion. Use `reframe-status` to locate the session.

## Upgrade, uninstall, and package

Disable following before upgrading the backend. Re-run environment setup and the
installer, then restart mpv. Upgrades update unedited configuration to the selected backend directory, preserve
edited configuration, and save the
previous installed script as `reframe.lua.previous`. An edited/unowned script is
never overwritten automatically. Back up and remove it yourself before retrying.

```text
uv run python scripts/plugin.py uninstall
```

Uninstall removes only files matching the recorded installed hashes. Edited files
are preserved and listed. It does not delete your backend, models, logs or other
mpv configuration. To roll back a script upgrade, disable, restore the `.previous`
script, and restart mpv; use the matching backend release if its protocol changed.

To create a local source preview and checksum:

```text
uv run python scripts/plugin.py package --output reframe-source-preview.zip
```

The archive includes source, dependency lock, Lua script, setup tools, tests and license notices.
It excludes environments, model engines/weights, videos, logs, private configuration,
reference clones and executable launchers. Its installer is `scripts/plugin.py`.
The source license and dependency notes are included; public release verification
remains pending.

## Verification

```text
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
```

The Lua lifecycle test runs in mpv's actual interpreter using a mocked player
API and requires no GPU. Focused Windows rendered checks in the development
workspace passed 1x/4x quality and control gates, full-height fill and actual
backend crash restoration. Native Linux/macOS, CPU/MPS performance, a stable mpv
0.41.0 binary, the complete playback matrix and manual smoothness review remain
pending. This is a preview; test footage and benchmark artifacts are not included.

## License

Copyright (C) 2026 Roman (EatEmAll) and contributors. Project source is licensed
under **GNU AGPL-3.0-only**. See [LICENSE](LICENSE), [NOTICE](NOTICE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Dependencies and downloaded
models retain their own terms. No weights, engines or third-party binaries are
included. This repository contains only the Lua extension, required Python
backend, setup tools and unit tests; it excludes the standalone player launcher
and proof-of-concept history.
