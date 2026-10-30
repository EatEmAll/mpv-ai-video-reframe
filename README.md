# mpv Reframe

The extension runs inside ordinary mpv and supervises a local Python companion.
`Ctrl+r` toggles following; no custom player executable is required. The script,
IPC transport, installer and backend control code support Windows, Linux and
macOS. Inference selects CUDA, Apple MPS or CPU. Windows/NVIDIA is the platform
validated locally; Linux/macOS and MPS/CPU performance are not yet verified.

## Design influences and acknowledgements

The camera-planning design was informed by
[Ido Pinto's ai-video-reframing](https://github.com/idopinto/ai-video-reframing),
particularly its single-subject hold/follow policy, easing back to centre after
the subject leaves, and Gaussian camera-path smoothing around one-third of a
second. Thank you to Ido Pinto for this prior work.

Additional reference projects studied during development:

- [amys94fr/FaceFollow](https://github.com/amys94fr/FaceFollow): a baseline for
  causal exponential smoothing and portrait-crop geometry.
- [Ashyni/mpv-scripts](https://github.com/Ashyni/mpv-scripts): `dynamic-crop.lua`
  as a reference for runtime crop updates and mpv Lua script structure.

These credits describe design influences and reference material, rather than
runtime package dependencies or wholesale imports of those projects. The current
implementation adapts the approach to live mpv playback, bounded look-ahead and
its own camera/controller lifecycle. See the
[reference provenance](THIRD_PARTY_NOTICES.md#reference-project-provenance) for the
revisions studied.

## Prepare the backend

Install mpv 0.41 or newer, Python 3.12, and uv. Extract the source release or clone
this repository, then run from its root:

```text
uv sync --locked
uv run reframe doctor
```

`doctor` explicitly downloads the pinned YOLO26n asset, verifies its upstream
SHA-256 before loading, and reports the selected device. Unknown weights are
rejected; see [SECURITY.md](SECURITY.md) and the
[review and release blockers](SECURITY_REVIEW.md).
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
on Unix. `model` may name the pinned `.pt` weights or a locally built CUDA `.engine`
with matching verified weights and device/runtime/hash metadata; relative model
paths refer to the backend directory. Other model formats and unknown weights
are refused. `rate` is detection sampling (5â€“15), not the
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

A configured IPC endpoint is reused only after ownership, permissions and player
identity checks. Otherwise the script creates a random Windows named pipe or a
Unix socket in a private owned runtime directory. IPC remains available for that
player session, including when following is disabled. Only the script-created
endpoint is hardened; permissive existing endpoints are rejected. IPC is local
player control; session IDs are freshness checks, not authentication. The extension
has no footage-upload operation. See the
[network and privacy audit](SECURITY_REVIEW.md#outbound-traffic-privacy-and-logs)
for dependency limits. Session status is available in `user-data/reframe`;
local lifecycle and camera logs are under `<backend_dir>/runs/lua/<session-id>/`.
Logs are retained until you remove them. Startup stderr is bounded and reported
in mpv's log on process completion. Use `reframe-status` to locate the session.

## Upgrade, uninstall, and package

Disable following before upgrading the backend. Re-run environment setup and the
installer, then restart mpv. Upgrades update unedited configuration to the selected backend directory, preserve
edited configuration, and save the
first backed-up script as `reframe.lua.previous`; an existing backup is never
overwritten. Interrupted installs are rolled back from a bounded journal on the
next install/uninstall. Post-interruption edits are preserved and require manual
reconciliation. An edited/unowned script is
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
uv run pytest -q --basetemp=runs/pytest-security-unique-id
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
```

Choose a new test directory under the trusted checkout for each run (replace `unique-id`); tests intentionally reject broadly writable temporary ancestors.

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
