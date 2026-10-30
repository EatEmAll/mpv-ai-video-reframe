# Linux setup and repeatable verification

This is a source preview, not a release or a platform-wide performance promise.
macOS/MPS remains unverified. Measured results are at the end of this file.

## Setup

```text
uv sync --locked
uv run reframe doctor
uv run python scripts/plugin.py install
```

`requires-python` excludes 3.14, so on distributions shipping it uv selects its
own managed Python. Its virtual environment reaches the interpreter through a
linked version directory; `trusted_executable` follows such links only when each
link and its parent are trusted. There is no ASCII path requirement on Linux.

The installer's inference preflight must pass before it writes configuration.
`scripts/windows_preflight.py --cpu` runs the same probe manually despite its name.

`input-ipc-server` is reused only when it is an absolute path inside a private
`0700` directory. mpv creates the socket `0600`. A socket directly in `/tmp` is
refused. Leave the option unset and the script creates a private endpoint.

## Harness

The harness scripts share a Windows-derived naming but run on Linux. Fixtures are
generated as in [WINDOWS_VERIFICATION.md](WINDOWS_VERIFICATION.md). Use a real
display, or the isolated display below. No user mpv configuration is loaded.

```text
mkdir runs
.venv/bin/python -m pytest -q --basetemp=runs/linux-pytest
.venv/bin/python scripts/verify_lua.py <samples>/synth_walk_1080p30.mp4
.venv/bin/python scripts/verify_oversized.py <samples>/synth_walk_1080p30.mp4
.venv/bin/python scripts/verify_windows_matrix.py --samples <samples> --output <new directory> --repeat 3 --devices cpu-pt
.venv/bin/python scripts/summarize_windows_matrix.py <directory> --repeat 3 --devices cpu-pt --output <new summary.json>
```

Omit `--devices` on NVIDIA hardware. The default matrix includes CUDA profiles,
and a CPU-only machine fails those rather than skipping them. Summaries refuse a
device subset that differs from the runs.

Dedicated GPU memory comes from the backend's open device handles. No GPU handle
is a measured zero. DRM handles use `fdinfo` statistics and NVIDIA handles use
NVML. Unreadable statistics leave the gate unmeasured.

`tests/native_unix_security.py` needs real privilege for its separate-uid checks:

```text
sudo -v
.venv/bin/python tests/native_unix_security.py
```

Exit 3 with `NATIVE_LINUX_PARTIAL` means sudo was unavailable, and the separate-uid
denial did not run. Do not report that as a pass.

## Isolated display

Tiling compositors can ignore requested window sizes, so resize gates need a
floating compositor. A headless weston gives a real Wayland compositor without
touching the desktop session:

```text
weston --backend=headless --renderer=gl --width=2560 --height=1440 --socket=reframe-verify
WAYLAND_DISPLAY=reframe-verify REFRAME_GPU_CONTEXT=wayland <harness command>
```

`REFRAME_GPU_CONTEXT=wayland` selects EGL, because `waylandvk` stalled on headless
weston. Frame timing on this display is not evidence for a physical monitor.

## Measured results

Measured 2026-10-31 from a clean copy of an uncommitted tree based on `aeb35fa`,
source fingerprint
`9759bf151c8dfc06599a587fa7b3db895692c2b66d87fd33035273a55aa42331`, with a fresh
`uv sync --locked` and no adapter. Keep the manifest; a Git SHA alone does not
describe this snapshot.

Environment: Omarchy (Arch-based) x86_64, Linux 7.2.8, Intel Core Ultra 7 255U,
integrated graphics, Mesa 26.2.5, no NVIDIA GPU. Python 3.12.14 (uv-managed),
Torch 2.14.1+cu130 on CPU, Ultralytics 8.4.193, PyAV 19.0.2, mpv 0.41.0 (Arch
`1:0.41.0-6`), FFmpeg 9.0.2. Display: headless weston 15 with EGL at 60 Hz.
Software decoding, because no VA-API driver for this GPU was installed.

| Check | Result |
|---|---|
| Unit and Lua suites | 207 passed, 6 Windows-only skipped |
| Installer, upgrade, uninstall, abuse cases | Pass, including a Unicode backend path |
| Lifecycle, crash, hang, shutdown, two players, duplicate backend | Pass |
| Full height at 608x1080, 1080x1080, 1920x1080 and fullscreen | Pass |
| Oversized subject | Pass |
| Cadence ceilings at 0.5x / 1x / 2x / 4x | 15 / 30 / 60 / 60 Hz |
| Private socket and directories, real user path | `0600` socket, `0700` directories |
| Internet sockets from mpv and backend, traced during playback | None |
| Separate-uid connection denial | **Not run**; needs sudo |

CPU playback on generated clips:

| Clip | Runs | Result |
|---|---:|---|
| 1080p30 at 1x | 3 | **All gates pass 3/3**; 0 extra drops, seek p95 0.28-0.38 s, 0 GPU bytes, 0.9 GB RSS |
| 1080p30 at 4x | 6 clean-tree runs | **Not verified**; 0/6 pass |
| 2160p60 at 1x, software decode | 1 | **Fails** seek recovery; no gate relaxed |

The 4x failures are a capacity limit on this machine. Sampling floors at 5 Hz, so
4x needs about 50 ms per detection. Detection took 33 ms when the machine was
idle and over 170 ms under load. Analysis then falls up to 5 media seconds behind
playback, and the camera follows late without recovering during the clip. An
earlier pre-fix run passed 2/3 at 4x on a quieter machine. An interleaved
comparison of old and new source showed the same degradation, but three noisy
pairs cannot prove equal speed. Android emulator crashes and other unrelated work
ran during these measurements, so 2160p60 and 4x numbers are load-dependent.

Unmeasured: CUDA, TensorRT, hardware decoding, physical displays, tiling
compositors, X11, other distributions, other architectures and manual perceptual
review. A closed socket, its session directory and a lock file remain under
`/tmp/mpv-reframe-<uid>` after a player exits, by the no-unlink design described
in [SECURITY_REVIEW.md](SECURITY_REVIEW.md).
