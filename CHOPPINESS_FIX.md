# Follow-camera choppiness investigation

Implementation developed from clean `main` at `553d43672d9356dd889e4035dca3603c601c4ea9`. Work is confined to this extension repository. Existing player/configuration files, the older POC, models and earlier evidence were not modified. All footage used for playback checks is the existing generated public fixture set. This section describes the delegated implementation handback before parent integration.

## Findings and changes

1. **A late pan acknowledgement cost another full output frame.** `IPC.set_align()` correctly refused a second in-flight command. The controller then treated that refusal as a stable/subpixel target and slept another full interval. Retained 1080p 1x telemetry contains adjacent moving-camera records at the same media time: one successful send, one refused send, then a successful send about 70 ms after the first. The old 30 Hz controller therefore introduced avoidable holds even in a run with zero active/baseline playback drops.

   The controller now distinguishes refused sends from successfully suppressed subpixel changes, retries refused sends promptly, and wakes on acknowledgements/property changes. Early wakes still wait for `last_sent + 1 / ceiling`. A 20 ms maximum wait keeps stop/close/heartbeat checks responsive at low ceilings. The sender retains one in-flight command and the strict `min(source FPS * playback speed, display FPS)` ceiling. Detection FPS, quality thresholds and mpv render offset are unchanged.

2. **Prediction time could run backward during ordinary playback.** In the intermediate scheduling-only run, IPC p95 dropped from 55.1162 ms to 35.4362 ms. At media positions 8.4/8.433333, the resulting planner query retreated from 8.524804 to 8.506050 while the monotonic timestamp advanced. `CameraMotion` interpreted backward time as a seek and snapped to the target, zeroing velocity. This is a separate cause from dropped playback frames or detector target jumps.

   `PresentationClock` now slews phase corrections toward the estimated render/IPC lead, bounds correction speed and stalled extrapolation, and stays nondecreasing during playback. Generation changes and pause/frame-step alignment remain explicit resets. The controller timestamps the camera after consuming detector results, instead of using a timestamp captured before that work.

3. **Old cadence logs did not timestamp the send itself.** Their wall timestamp was written after sending and other loop work. New telemetry includes actual monotonic pan-send time, record time, planner query time, pause state, blocked attempts and detector arrivals. Lua cadence verification now measures monotonic send timestamps with the existing thresholds. Independent playback observations retain every pan-property notification, plus available display FPS/vsync/mistimed-frame diagnostics, separately from the existing frame-drop counters.

Property notifications acknowledge observable player state. They do not establish physical scanout timing, and IPC reply latency is an estimate of the control path, not a measured display-presentation delay. See the [mpv IPC contract](https://github.com/mpv-player/mpv/blob/master/DOCS/man/ipc.rst).

## Regression evidence

- `tests/test_controller.py` drives the real controller and sender with a deterministic reply arriving 1 ms after each output deadline at 0.5/1/2/4x. It checks prompt recovery, strict minimum send gaps, actual send timestamps, and immunity to jumping wall-clock time. It fails in all four cases with the original controller and passes with the fix.
- `tests/test_clock.py` reproduces the measured latency-percentile drop with the real motion filter. It checks forward time and retained velocity, speed decrease, pause, seek generation, convergence and bounded stalls.
- `tests/test_ipc.py` verifies acknowledgement wakeup, one in-flight command, ceiling enforcement even on forced refresh, generation isolation and restoration without changing the configured render offset.
- Authoritative handback-test/original-controller proof: `runs/choppy-handback-original-controller-regression.log` (4 expected failures). The earlier `runs/choppy-original-controller-regression.log` also failed all four timing cases. Harness-development logs were retained, but are not playback evidence.
- Exact handback source: `runs/choppy-handback-pytest.log` (208 passed, 11 Unix/Linux-only skips, 15.16 seconds). Ruff, formatting (77 files), lock and diff checks passed. Earlier full-suite evidence remains in `runs/choppy-final-pytest.log`.

## Playback evidence

The original complete three-repeat matrix remains in `runs/windows-pr4-553d436-matrix/`, including all failures and both Lua outcomes. It was not a pre-PR A/B comparison, and nothing here attributes its failures to PR #4.

The scheduling-only experiment remains in `runs/choppy-pan-fix-1080p-pt/`: 1x passed all gates; 4x failed static stability, anticipation and frame health. It is intermediate evidence, not the final implementation. Its source manifest is preserved.

The camera-fix playback sequence used one uniform source snapshot, retained in both manifests. Gates and thresholds were unchanged; controls and paired baselines were included, with no render-offset override:

| Profile | Speed | Failed gates | Extra drops/delays / source frames | Containment | Onset lags, seconds |
|---|---:|---|---:|---:|---|
| PyTorch 1080p30 | 1x | None | 9/1800 = 0.5000% | 99.81% | -0.1667, -0.6333 |
| PyTorch 1080p30 | 4x | None | 2/1800 = 0.1111% | 100% | -0.1333, -0.6000 |
| TensorRT 2160p60 | 1x | frame_health | 64/3600 = 1.7778% | 99.94% | -0.1833, -0.6000 |
| TensorRT 2160p60 | 2x | frame_health | 56/3600 = 1.5556% | 99.87% | -0.1000, -0.6167 |

All four measurements passed follow error, containment, static stability, anticipation, identity, runtime, VRAM and every control gate. All four had zero backward planner-query steps during active playback. This is focused verification, not a replacement three-repeat matrix. The 1080p 1x frame-health pass is exactly at the budget, with no headroom.

- `runs/choppy-final-1080p-pt/`: source/environment manifest, 1x/4x results and paired observations. Driver exit 0.
- `runs/choppy-final-2160p-engine/`: corresponding 4K 1x/2x evidence. Driver exit 1 for the two frame-health failures.
- `runs/choppy-final-exits.json`: sequential driver statuses; Lua and oversized checks both exit 0.
- `runs/choppy-final-lua.log` and `runs/20261031-031943-lua-lifecycle/result.json`: complete lifecycle, 0.5/1/2/4x cadence, suspension/resume, fill, forced-crash restoration, idle enable/stop.
- `runs/choppy-final-oversized.log`: full-height oversized-subject checks passed.
- `runs/choppy-handback-lua.log`: another complete lifecycle/cadence/restoration pass on the exact handback source, after the bounded-wait review fix.
- `runs/choppy-timing-analysis.json` and `runs/choppy-analyze.py`: offline timing extraction, including independent pan notification intervals. Legacy timestamps are explicitly marked as approximate.

The retained legacy 1080p 1x run had a steady-window send-record p95 of 69.45 ms. The scheduling-only experiment had an actual-send p95 of 38.66 ms; the camera-clock snapshot measured 57.80 ms at 1x and 25.62 ms at 4x. These live runs are **not a controlled performance A/B**: desktop load and playback frame health varied, and legacy timestamps were logged after sending. The deterministic original/current controller test supplies the causal scheduling proof. Live gaps still include occasional long holds (1080p 1x maximum 109 ms); one in-flight command and real player/GPU scheduling still constrain delivery.

After that playback snapshot, review added only the 20 ms maximum event wait, accumulated detector-arrival counts between emitted records, and strengthened the fake-event/maximum-wait assertions. `runs/choppy-handback-manifest.json` records the exact final source hashes and identifies the two differing files (`src/reframe/controller.py`, `tests/test_controller.py`). The full suite and Lua lifecycle were rerun on those final bytes; the four full-clip quality measurements were not repeated after these last guard/telemetry changes.

## Remaining limitations

- 4K TensorRT frame health still fails. The camera changes do not establish that renderer/GPU playback hitches have been solved. Neither the old matrix nor these focused runs establish regression attribution to PR #4.
- Physical scanout and subjective visual smoothness were not directly measured. Pan notifications can be queued/coalesced; the preserved display/vsync diagnostics may be unavailable outside display-sync mode. No claim of perfectly uniform on-screen camera updates is made.
- The complete three-repeat matrix, CPU backend and native Unix/macOS execution were not rerun for this patch. Windows unit skips remain explicit; runtime changes use OS-independent Python timing/events.
- Existing full-height geometry, target selection/identity, containment, anticipation policy, speed/seek generation handling, restoration, model loading and transport/security validation were retained. No gate was relaxed and no inference-rate increase was used.

## Reproduction

Run from this extension repository, keeping the locked accelerator environment:

```powershell
$env:REFRAME_MPV = (Get-Item -LiteralPath 'runs/mpv-codec-review/player/mpv.exe').FullName
.venv/Scripts/python.exe -m pytest -q -rs --basetemp runs/<fresh-trusted-local-directory>
.venv/Scripts/python.exe -m ruff check src scripts tests
.venv/Scripts/python.exe -m ruff format --check src scripts tests
uv lock --check
git diff --check
.venv/Scripts/python.exe scripts/verify_playback.py 'runs/windows-implementation/source-final/samples/synth_walk_1080p30.mp4' --model models/yolo26n.pt --speeds 1 4 --output runs/<fresh-1080p-directory>
.venv/Scripts/python.exe scripts/verify_playback.py 'runs/windows-implementation/source-final/samples/synth_walk_2160p60.mp4' --model models/yolo26n.engine --speeds 1 2 --output runs/<fresh-4k-directory>
.venv/Scripts/python.exe scripts/verify_lua.py 'runs/windows-implementation/source-final/samples/synth_walk_1080p30.mp4'
.venv/Scripts/python.exe scripts/verify_oversized.py 'runs/windows-implementation/source-final/samples/synth_walk_1080p30.mp4'
.venv/Scripts/python.exe runs/choppy-analyze.py
```

Benchmarks/exports must run sequentially. The last command only reads timing evidence; it opens no player, video or GPU context. Full logs, source/environment manifests, paired baseline observations, geometry metrics, pan notifications and telemetry are retained in the named run directories. Normal desktop applications and pre-existing players remained present; exclusive GPU access was not established.

To reproduce the original-controller regression without modifying the checkout:

```powershell
@'
import subprocess, pytest
from reframe import controller
exec(compile(subprocess.check_output(['git', 'show', '553d43672d9356dd889e4035dca3603c601c4ea9:src/reframe/controller.py']), '<baseline-controller>', 'exec'), controller.__dict__)
raise SystemExit(pytest.main(['-q', 'tests/test_controller.py', '--basetemp', 'runs/<fresh-baseline-test-directory>']))
'@ | .venv/Scripts/python.exe -
```

## Exact review scope

- `src/reframe/controller.py`: bounded output wake/retry scheduling, presentation-clock integration, timestamp placement and accumulated telemetry.
- `src/reframe/clock.py`: bounded, monotonic presentation prediction.
- `src/reframe/ipc.py`: wake controller when a pan completes.
- `scripts/verify_lua.py`: cadence from actual monotonic sends.
- `scripts/verify_playback.py`: independent pan notifications and presentation diagnostics.
- `tests/test_controller.py`: new controller/sender timing regression.
- `tests/test_clock.py`: prediction-clock regressions.
- `tests/test_ipc.py`: acknowledgement-wakeup assertion and fixture setup.
- `CHOPPINESS_FIX.md`: this handback report.

Raw evidence and the offline analysis helper are under the already ignored `runs/` directory; copy them separately if transferring the source diff.

## Parent integration verification

Parent reviewed all changed source and tests, then independently ran 208 tests (passed, 11 platform skips), Ruff lint/format (77 files), and lock checks. Focused full-clip playback on the exact final runtime source passed every unchanged gate at 1080p 1x and 4x, including controls, anticipation, static stability, identity and 100% containment. At 1x, paired baseline/active drops were 0/0; at 4x, 897/896. The 4x baseline itself drops frames because 120 source frames/s exceed the roughly 60 Hz display; the frame-health gate compares additional drops. Evidence is retained in `runs/choppy-parent-final-1080p/` and its adjacent log. This final-source run supersedes the earlier limitation about full-clip checks predating the last wait/telemetry guards for these two 1080p profiles. It does not supersede 4K failures or establish subjective smoothness.

Before committing, remote main advanced to `e2536a8` (status badge auto-hide only). Parent fast-forwarded that separate change without altering the camera patch. The full playback run above used the prior Lua badge version; combined-source unit and Lua lifecycle checks are recorded separately below. Full three-repeat, CPU, 4K and native Unix verification remain outside this patch's final integration verification.

Combined-source verification after e2536a8: 208 passed, 11 platform skips; native Lua lifecycle/cadence/suspension/restoration/idle checks passed. Evidence: runs/choppy-parent-integrated-lua.log. No installed configuration changes were made.
