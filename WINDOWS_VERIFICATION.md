# Windows setup and repeatable verification

This is a source preview, not a release or a platform-wide performance promise.
Linux and macOS/MPS native verification remain outstanding. Keep issue #1 open.

## Supported installation boundary

Use an **ASCII-only absolute backend path**, including all parent directories,
the virtual environment, and the base Python installation. Spaces and brackets
are supported. Keep normal filesystem ownership/ACL checks enabled. Extract a
fresh source copy at the chosen location and recreate its environment there;
never move a virtual environment or use a junction to conceal its original path.
If the Windows account home is Unicode, choose a genuinely ASCII local directory
with appropriate per-user ownership and an ASCII base Python installation.

This constraint applies to executable/native dependency locations. **Unicode
media filenames and mpv configuration directories remain supported.** It is not
necessary to rename media or change the Windows locale.

Two distinct dependency failures motivated the boundary:

* CPython 3.12 reads editable `.pth` files using the locale encoding, while the
  build backend writes the source path in UTF-8. On the tested CP1252 system the
  Hebrew path fails during `site` initialization, before application code runs.
  [Python documents UTF-8-first decoding as a 3.13 change](https://docs.python.org/3.13/library/site.html).
  Changing Python may fix this first failure; it does not establish CUDA support.
* The same noneditable Unicode environment imports Torch but aborts inside native
  cuDNN version loading: `Invalid handle. Cannot load symbol cudnnGetVersion`,
  exit `0xC0000409`. Direct Torch cuDNN version and wide-character `ctypes.WinDLL`
  probes reproduce it without YOLO. The ASCII environment returns version 92400.
  This isolates the failure to native library initialization, but does not identify
  an exact faulty upstream source line. `--no-editable` is not a CUDA fix.

From the extracted backend, in PowerShell:

```powershell
powershell -NoProfile -File scripts/setup_windows.ps1
# Optional locally built acceleration dependencies:
powershell -NoProfile -File scripts/setup_windows.ps1 -Accel
# Diagnostic CPU inference (does not configure future players to use CPU):
.venv\Scripts\python.exe scripts/windows_preflight.py --cpu
```

The setup script checks its location before asking uv to inspect the environment,
runs locked sync and explicit model preparation, then runs real detector inference
in a bounded child process. A native abort becomes an actionable preflight error.
`doctor` alone is not an inference test. The installer CLI repeats inference
preflight before changing a config directory; unsupported Unicode backends fail
before writing configuration. No model download happens during player startup.

After successful setup, explicitly select your mpv config directory:

```powershell
.venv\Scripts\python.exe scripts/plugin.py install --config-dir '<chosen mpv config directory>'
```

For a CPU-only player on a CUDA machine, start that player with
`CUDA_VISIBLE_DEVICES=-1` in its own inherited environment. The `--cpu` preflight
switch is diagnostic and does not persist that setting. Uninstall does not run
inference and remains usable when a dependency is broken.

## Trusted optional TensorRT

```powershell
uv sync --locked --group dev --group accel
.venv\Scripts\python.exe scripts/export_windows_engine.py
.venv\Scripts\python.exe scripts/windows_preflight.py --model models/yolo26n.engine
```

Build locally on the deployment GPU from the pinned, hash-verified `.pt` weights.
The supported locked Windows export path above builds **FP32 (TF32 allowed)** on
TensorRT **11.2.1.2**. It records that precision explicitly. The older
`scripts/export_engine.py` requests FP16; on TensorRT 11 Ultralytics additionally
requires `nvidia-modelopt[onnx]>=0.44`, which is not in the current accel lock.
That FP16 workflow fails explicitly and is not a verified setup. Do not enable
upstream automatic installation or silently downgrade TensorRT to make it work.
Do not download or share an engine as a portable model. Selection checks the
engine/weights hashes, manifest, CUDA capability and TensorRT version. CPU uses
the pinned `.pt`; an engine requires CUDA. Keep explicit `--model` arguments in
measurements so automatic cache selection cannot confuse `.pt` and engine results.
Do not use `--manifest-only` to bless an engine of unknown provenance.

The large H.264/HEVC shadow decoder discards non-reference pictures, retaining
reference frames, PTS and keyframes. It never changes mpv's presentation decoding.
The large B-frame regression test checks ordered sampling and seek generations;
the full playback matrix checks geometry, continuity and frame health afterwards.

## Distributable harness

The source archive includes these tools and requires only locked `dev` dependencies
(plus `accel` for TensorRT); no POC import, matplotlib, private data, live mpv config,
or desktop capture is needed. Install FFmpeg separately for fixture generation.
Set `REFRAME_MPV` to the chosen stable mpv executable. All players use `config=no`
and unique IPC names; only harness-owned players and companions are terminated.

Generate fixtures in an existing directory of your choice. The bundled public
Ultralytics bus image is checked against SHA256
`c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63` before use.
The public photo and software retain their upstream licensing (Ultralytics AGPL).
Fixed source-image rectangles make generation independent of inference device.
Only those person cutouts are composited; the independent truth records exact
compositing transforms, not the detector's output on the finished footage.

```powershell
.venv\Scripts\python.exe scripts/make_verification_fixtures.py --variant 1080p30 --output '<existing samples directory>/synth_walk_1080p30.mp4'
.venv\Scripts\python.exe scripts/make_verification_fixtures.py --variant 2160p60 --output '<existing samples directory>/synth_walk_2160p60.mp4'
.venv\Scripts\python.exe scripts/verify_lua.py '<samples directory>/synth_walk_1080p30.mp4'
.venv\Scripts\python.exe scripts/verify_oversized.py '<samples directory>/synth_walk_1080p30.mp4'
.venv\Scripts\python.exe scripts/verify_windows_matrix.py --samples '<samples directory>' --output '<new results directory>' --repeat 3
.venv\Scripts\python.exe scripts/summarize_windows_matrix.py '<results directory>' --repeat 3 --output '<new summary.json>'
```

`verify_windows_matrix.py` runs sequential 60-second 1080p30 1x/4x and 4K60 1x/2x
clips, on explicit CUDA `.pt` and engine configurations, plus CPU 1080p30 controls
and playback. Repetitions alternate baseline-first and active-first ordering.
Do not run exports or other benchmarks concurrently. It retains every subprocess
exit and command, even when a gate fails. Run on an idle system and record external
load and display state. Longer/harder public footage is useful additional evidence;
these generated clips do not establish universal 4K capacity.

Each playback report contains source/environment/fixture manifests, raw independent
IPC observations, per-frame metrics, lifecycle/telemetry copies, paired baseline
and active counts, resource samples, and control results. Source mutation during
measurement invalidates the run. Preserve the exact manifest; a Git SHA alone
does not describe an uncommitted integration snapshot. Local logs may contain local
paths; sanitize before public posting. No raw local paths belong in issue comments.

The summary command checks that all expected runs and per-speed reports exist.
Empty, failed, incomplete and unmeasured runs never become a passing matrix.
It omits absolute command/model paths from the exported summary. Inspect fixture
names before publication if you use any source other than the public generator.

`verify_playback.py --render-offset 0.05` is an explicitly labelled **experiment**:
it overrides the owned player's active render queue after enable, keeping every
quality/frame-health gate unchanged. The investigation used it to isolate the
former forced 2 ms offset in `IPC.enable()`. The runtime now preserves mpv's
configured render offset. Use the normal command without this flag for verifying
the current default. An experimental override is always identified in its report.

The unchanged gates include extra dropped/delayed frames <= **0.5%** of source
frames, independent follow error and containment, static alignment variation,
motion anticipation, identity continuity, seek recovery <=1 s p95, exact ten frame
steps, exact resize dimensions, full-height fill, restoration, and <=2 GiB companion
dedicated GPU memory. Unsupported resource measurements remain unmeasured. Skipping
controls or baseline keeps required gates unmeasured and returns failure. Cadence
checks compare sent updates against source FPS x speed and display FPS with a
1 ms timestamp tolerance. A faster engine does not excuse geometry/smoothness failures.

For unit/Lua checks and static checks:

```powershell
uv lock --check
uv sync --locked --group dev --group accel
.venv\Scripts\python.exe -m pytest -q --basetemp runs/windows-pytest
.venv\Scripts\python.exe -m ruff check src scripts tests
.venv\Scripts\python.exe -m ruff format --check src scripts tests
```

Use an owned/private test parent: the security suite deliberately rejects broad
ACLs on some system temporary directories. Do not weaken those checks to test.

## Recorded results

See [the implementation results](WINDOWS_VERIFICATION_RESULTS.md). Final
combined security integration must be tested separately from immutable measurement
snapshots. Do not infer Linux verification from Windows passes.
