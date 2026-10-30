# Windows implementation and verification results

Measured 2026-10-30. Uncommitted integration based on `1af2ad76a6aa03b2104e3c2481f135a02ae4cd35`. No branch, checkout, commit, push, release or listing was created. Original POC and read-only baseline sources were not modified. **Issue #1 remains open; native Linux is unverified.**

Machine: Windows 11 25H2 build 26200.9711, Ryzen 5 5600X (6 cores/12 threads), RTX 3060 12 GB, driver 617.42, CUDA capability 8.6. Python 3.12.3; Torch 2.14.1+cu130; Ultralytics 8.4.193; PyAV 19.0.2; TensorRT 11.2.1.2. Player: reviewed portable mpv 0.41.0-1227-ge471496c5, FFmpeg N-127893-g9a861e968. See [player provenance](MPV_PROVENANCE.md).

## Changes and diagnosis

- Supported Windows setup requires ASCII backend, venv and base-Python paths; media/config paths retain Unicode support. The original editable `.pth` is UTF-8 but CPython 3.12/CP1252 fails at byte 109 (`0x9d`). Identical cuDNN stub DLLs (SHA256 `506be0b0f0b0f8856c9b2171a859097fa383d7f28243ebff3859d8502acb0e95`) return version 92400 from an ASCII environment but abort `0xC0000409` during version probing from the noneditable Unicode environment, without YOLO. Wide-character DLL/version probing also aborts. Exact faulty upstream source line remains unidentified; no arbitrary Unicode CUDA support is claimed.
- Setup rejects unsupported paths before uv; bounded subprocess preflight runs actual inference before CLI installation. Parent integrated an early runtime guard for manual configurations. A newly built noneditable Unicode environment now exits 2 before model download, and native attach rejects a Unicode venv before IPC/output creation. No trust checks were relaxed.
- Large H.264/HEVC shadow decoding now discards only non-reference pictures. Presentation decoding is unchanged. On a fixed 10 Hz grid, all six decoder runs convert exactly 51 frames: first sample DEFAULT 0.6581?0.7036 s versus NONREF 0.3456?0.3583 s; five media seconds take 1.5572?1.5730 s versus 0.8855?0.8983 s, during 2x player playback. Large B-frame sampling/seek-generation regression passes.
- TensorRT 11 FP16 export requires missing ModelOpt. The new explicit locked-dependency exporter builds FP32 (TF32 allowed), records accurate precision, rejects preexisting engines and removes selectable metadata if finalization fails. Actual export took 87.9 s; real engine inference passes. Older TensorRT 10 evidence is historical, not a supported configuration.
- Normal full-matrix measurements exposed continuing 4K frame drops. A controlled render-offset experiment identified the forced 2 ms queue as a contributor. Parent removed that override; the current runtime preserves mpv/user timing and its saved/restored properties. A speculative GPU-priority hint was removed without being shipped.

## Exact model/device scope

Pinned `.pt`: SHA256 `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`; CUDA logical device 0 uses the detector FP16 setting; CPU uses `.pt` with `CUDA_VISIBLE_DEVICES=-1`. CPU playback still uses GPU presentation in mpv.

Locally built engine: TensorRT **11.2.1.2**, RTX **3060**, capability **8.6**, FP32 with TF32 permitted, fixed `[1,3,640,640]`, SHA256 `9b912db351f3884132917986e97700a6f678b0236fe09093c3f5dabbbf138b9a`. Other GPUs/runtimes/precisions are unverified; do not distribute or substitute an engine. Restricted `.pt` CUDA/CPU inference and verified engine inference pass on integrated root.

## Repeated playback measurements

All clips are generated 60-second public-person composites; truth is independent compositing geometry. Each speed has two complete paired baseline/active repetitions with reversed ordering. Player settings and instrumentation match, and every failure is retained. Fractions below are **percent of source frames**, budget **0.5%**. Negative values are baseline variation. The 2x 4K baseline necessarily drops frames on this 59.951 Hz display; the gate measures additional drops.

### Original 2 ms default (frozen combined-security source)

| Run | Speed | Baseline/active drops | Extra % | Failed gates |
|---|---:|---:|---:|---|
| cuda-pt-1080p30-1 | 1x | 1/2 | 0.0556 | none |
| cuda-pt-1080p30-1 | 4x | 896/929 | 1.8333 | frame_health |
| cuda-pt-1080p30-2 | 1x | 0/5 | 0.2778 | none |
| cuda-pt-1080p30-2 | 4x | 912/905 | -0.3889 | none |
| cuda-pt-2160p60-1 | 1x | 102/119 | 0.4722 | none |
| cuda-pt-2160p60-1 | 2x | 1866/1831 | -0.9722 | none |
| cuda-pt-2160p60-2 | 1x | 31/167 | 3.7778 | frame_health |
| cuda-pt-2160p60-2 | 2x | 1836/1808 | -0.7778 | none |
| cuda-engine-1080p30-1 | 1x | 1/1 | 0.0000 | none |
| cuda-engine-1080p30-1 | 4x | 903/948 | 2.5000 | anticipation, frame_health |
| cuda-engine-1080p30-2 | 1x | 2/4 | 0.1111 | none |
| cuda-engine-1080p30-2 | 4x | 897/898 | 0.0556 | none |
| cuda-engine-2160p60-1 | 1x | 10/162 | 4.2222 | frame_health |
| cuda-engine-2160p60-1 | 2x | 1881/1900 | 0.5278 | frame_health |
| cuda-engine-2160p60-2 | 1x | 146/193 | 1.3056 | frame_health |
| cuda-engine-2160p60-2 | 2x | 1953/1882 | -1.9722 | none |
| cpu-pt-1080p30-1 | 1x | 24/18 | -0.3333 | none |
| cpu-pt-1080p30-2 | 1x | 21/25 | 0.2222 | none |

### Controlled 50 ms offset (same frozen runtime; explicit experimental override)

These rows are not mislabeled as default-source integration. All required gates remain unchanged.

| Run | Speed | Baseline/active drops | Extra % | Failed gates |
|---|---:|---:|---:|---|
| offset-cpu-1080-r1 | 1x | 0/3 | 0.1667 | none |
| offset-cpu-1080-r2 | 1x | 1/2 | 0.0556 | none |
| offset-engine-1080-r1 | 1x | 0/0 | 0.0000 | none |
| offset-engine-1080-r1 | 4x | 914/914 | 0.0000 | none |
| offset-engine-1080-r2 | 1x | 0/0 | 0.0000 | none |
| offset-engine-1080-r2 | 4x | 912/912 | 0.0000 | none |
| offset-engine-4k-r1 | 1x | 60/66 | 0.1667 | none |
| offset-engine-4k-r2 | 1x | 66/67 | 0.0278 | none |
| offset-engine-4k-r2 | 2x | 1845/1841 | -0.1111 | none |
| offset-engine-4k-r3 | 2x | 1844/1847 | 0.0833 | none |
| offset-pt-1080-r1 | 1x | 0/0 | 0.0000 | none |
| offset-pt-1080-r1 | 4x | 912/921 | 0.5000 | anticipation |
| offset-pt-1080-r2 | 1x | 0/0 | 0.0000 | none |
| offset-pt-1080-r2 | 4x | 911/932 | 1.1667 | frame_health |
| offset-pt-4k-r1 | 1x | 64/62 | -0.0556 | none |
| offset-pt-4k-r2 | 1x | 64/61 | -0.0833 | none |
| offset-pt-4k-r2 | 2x | 1834/1848 | 0.3889 | none |
| offset-pt-4k-r3 | 2x | 1837/1873 | 1.0000 | frame_health |

### Integrated root (runtime guards and timing preservation, no override)

| Run | Speed | Baseline/active drops | Extra % | Failed gates |
|---|---:|---:|---:|---|
| root-smoke-engine-4k | 1x | 66/70 | 0.1111 | none |
| root-smoke-engine-4k | 2x | 1828/1853 | 0.6944 | frame_health |

| root-smoke-cpu-1080 | 1x | 0/1 | 0.0556 | none |

## Current limits and remaining work

- Repeated 50 ms results pass all gates for CUDA `.pt` at 1080p/1x and 4K/1x, TensorRT at 1080p/1x and 4x and 4K/1x, and CPU `.pt` at 1080p/1x. The integrated-root engine 4K/1x smoke also passes.
- **4K/2x remains unverified:** two engine experiments pass, but the final integrated-root run fails frame health at **0.6944%** (1828 baseline, 1853 active, 25 extra; budget permits 18). `.pt` 4K/2x is mixed (0.3889% pass, 1.0000% fail). Do not call these fixed.
- `.pt` 1080p/4x remains unverified: experimental repetitions fail anticipation or frame health (including 1.1667% extra drops). Use the measured engine configuration for the repeated 1080p/4x result.
- Native Linux, macOS/MPS, other Windows/Python/GPU combinations, CPU faster/higher-resolution capacity, rotated/VFR/remote native matrix, allocation profiling and manual perceptual smoothness review remain unverified. No missing measurement becomes PASS.

## Integration and lifecycle evidence

- Integrated-root CPU 1080p/1x passes every gate, including controls: extra drops **0.0556%**, seek p95 **0.6457 s**, dedicated GPU bytes **0**.
- Full integrated-root suite: **202 passed, 1 skipped** with trusted repository-local basetemp. Ruff, format and lock checks are recorded locally; final artifact checks repeat them.
- Stable mpv 0.41.0 was exercised earlier on combined source (184 passed/1 skipped). Final portable-player suite is 184 passed/1 skipped before the parent integration additions, then 202/1 on root. The reviewed portable build is the recommended provenance-selected player, not a claim that every 0.41 build is equivalent.
- Final cadence/lifecycle harness passes source-FPS x speed/display ceiling, portrait/square/landscape full-height fill, zoom suspend/resume, forced crash restoration, idle enable/stop. Oversized-subject full-height rendering passes.
- CPU controls and full 1080p/1x pass twice in both frozen groups; CPU controls were not skipped. Default-offset CPU seek p95 is recorded per row in JSON. Dedicated GPU bytes are zero for CPU inference.
- Positive current-source Unicode 4K media/config runtime passes with hardware shadow decode and exact saved-property restoration; default installation uses a child-only redirected APPDATA. Install/reinstall/uninstall and fresh explicit model download pass.

## Reproduction and evidence

Use [WINDOWS_VERIFICATION.md](WINDOWS_VERIFICATION.md) for setup, fixture generation, exact repeated harness commands, and interpretation. [Sanitized JSON](WINDOWS_VERIFICATION_RESULTS.json) includes source hashes, per-run measurements, device/version data, resource peaks and every gate. Exact source snapshots, command journals, raw IPC observations/telemetry, controls, logs and generated fixtures are retained locally under `runs/windows-implementation/` (ignored and not bundled). `portable-matrix-source.zip` preserves the original full-matrix source. The original Temp-ACL failures and first invalid cadence-harness run remain retained; corrected trusted-temp/portrait runs are separately named. No private footage, live user configuration or desktop capture is included.

No staging, commit, push, branch change, worktree, release, listing or issue closure was performed. Only owned test processes were used.

## Owned files and parent integration

Owned files changed:

- `scripts/plugin.py`
- `src/reframe/video.py`
- `scripts/windows_preflight.py`
- `scripts/setup_windows.ps1`
- `scripts/export_windows_engine.py`
- `scripts/make_verification_fixtures.py`
- `scripts/verify_windows_matrix.py`
- `scripts/verify_playback.py`
- `scripts/verification_backend.py`
- `scripts/verification_manifest.py`
- `scripts/verification_player.py`
- `scripts/verification_resources.py`
- `scripts/control_quality.py`
- `scripts/verify_lua.py`
- `scripts/verify_oversized.py`
- `scripts/benchmark_shadow_decode.py`
- `scripts/summarize_windows_matrix.py`
- `tests/test_windows_setup.py`
- `tests/test_shadow_discard.py`
- `tests/test_verification_summary.py`
- `WINDOWS_VERIFICATION.md`
- `WINDOWS_VERIFICATION_RESULTS.md`
- `WINDOWS_VERIFICATION_RESULTS.json`

Parent integrated the runtime path guards, render-offset preservation and README recipe in its owned files. Those are separate from this file-ownership list. No outstanding cross-ownership implementation request remains; 4K/2x performance, `.pt` fast profiles and native Linux still require follow-up.
