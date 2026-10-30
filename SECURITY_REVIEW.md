# Local security review — 2026-10-30

Scope: extension checkout initially at `cf9d0e83a0d45db559c9119879e6d42a831545ce`,
issue [#2](https://github.com/EatEmAll/mpv-ai-video-reframe/issues/2).
This is an implementation/audit record, not a penetration-test certification.
Camera geometry, full-height fill, timing/FPS limits and planning algorithms are
unchanged. AGPL licensing and provenance acknowledgements remain in place.

## Threat model and implemented controls

- **Other local users:** cannot be trusted with player control, model/log writes,
  installation targets or executable replacement. Unix endpoints use an
  exclusively created random directory under a checked, current-user-owned
  `0700` runtime root (`mpv-reframe-<uid>`), a `0700` per-player directory and a
  checked owned `0600` socket. Encoded paths longer than 100 bytes are rejected.
  Windows runtime directories have protected DACLs created atomically allowing
  the account, SYSTEM and administrators. Pipe owner, DACL and server PID are
  checked using Win32 APIs. Additional capability/account write grants are
  rejected rather than guessed safe. Unsupported ACL entry types fail closed.
- **Shared temporary directories and endpoint substitution:** sticky root-owned
  Unix temp ancestors may contain the private root; pre-created unsafe roots,
  symlinks/reparse points and unsafe ownership fail. Windows uses LOCALAPPDATA
  rather than a broadly writable temp directory. The client verifies its actual
  connected handle/peer before the pinned IPC library starts its reader or sends
  property commands. Linux checks `SO_PEERCRED`; systems with `getpeereid` check
  UID. Other Unix credential APIs fail closed. The check connection requests
  identification-level security rather than impersonation. The underlying
  library's data connection still uses its default Windows SQOS; validation
  occurs before data is sent, but this is not a general impersonation sandbox.
- **Existing endpoints:** never silently chmod/harden an endpoint configured by
  the user. It must already meet the private-parent/socket or pipe DACL/owner/PID
  policy. Only the random endpoint created by this Lua instance is hardened.
  Pipe names are restricted to local simple names; UNC hosts, TCP and malformed
  pipe paths are rejected. Disabling leaves IPC available to trusted local
  clients for the player lifetime. Lua never removes an endpoint by pathname.
  mpv owns its socket lifetime. Empty/stale runtime directories and persistent
  lock files may remain; no automatic unlink/prune can remove another process's
  endpoint. Remove empty owned runtime directories only after player exit.
- **Locks and installer:** OS-released locks in a private directory reject links,
  nonregular files and multiple hard links; release never unlinks the inode.
  Installer/uninstaller use a persistent configuration lock. Manifest and bounded
  recovery-journal keys allow only the two managed files and manifest; strict
  JSON rejects duplicate keys/nonfinite values. Parent/target ownership and
  symlink/reparse checks precede writes/deletions. Replacements use exclusive
  temporary files, fsync and same-directory rename. Durable old/new recovery
  records roll back partial installs on the next install/uninstall; edits made
  after interruption cause refusal without overwriting them. This covers process
  interruption, not guaranteed power-loss durability on every filesystem.
  Uninstall is idempotent after interruption and removes matching hashes only.
  Existing backups are preserved, never replaced. `.previous` is the first saved
  version, not a promise of the immediately preceding version after many upgrades.
- **Protocol and lifetime:** direct argument arrays, isolated Python (`-I`),
  bounded subprocess output, per-enable session IDs, safe-integer sequences,
  bounded JSON/script payloads and schema/type checks. Invalid Python control
  messages cannot consume a sequence number. Acquisition ACK must match the
  current integer view epoch. Playback generation changes invalidate old crop
  completion bookkeeping. View acquisition rechecks the playback generation
  before changing properties. Stop drains/restores; Lua holds the crash snapshot,
  waits for exit and does not launch a second backend during shutdown. Session
  IDs are freshness checks, not authentication. Trusted same-account clients
  can still issue arbitrary mpv commands. The IPC dependency's raw stream parser
  is not a hardened general-purpose untrusted network parser.
- **Edited configuration and executable paths:** absolute backend/interpreter
  paths required by preflight, checked ownership/ACLs and no shell evaluation.
  `-I` removes current-directory/PYTHONPATH injection. The environment's installed
  packages/editable source path are still trusted code. Explicit mpv overrides
  fail if invalid or unsafe instead of silently falling back.
- **Untrusted media:** mpv, PyAV/FFmpeg and GPU decoders parse local files under
  the user's privileges. Filesystem checks do not sandbox malformed media or
  native codecs. Use trustworthy media and current codec/player builds. Only
  local video paths enter following; remote URLs suspend following. mpv itself
  may still play remote media and perform its own network operations.

## Model provenance and loading

The upstream release asset
[`ultralytics/assets` v8.4.0 `yolo26n.pt`](https://github.com/ultralytics/assets/releases/tag/v8.4.0)
was checked through GitHub's live releases API on 2026-10-30:

```text
size:   5544453 bytes
sha256: 9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef
```

`doctor` downloads only that explicit HTTPS asset (GitHub may redirect to its
asset CDN), limits bytes, hashes before atomic installation and verifies again
before `YOLO` loading. Existing tampered files are refused, not replaced. Playback
does not request missing weights. Explicit `.pt` paths must match this same pin.
The trust anchor is reviewed source containing the upstream digest; it is not
an independent signature and cannot defend against upstream/repository compromise.
Review the upstream release and provenance before changing the pin.

`ULTRALYTICS_SAFE_LOAD=true` activates the pinned version's known-class,
`weights_only=True` loader before import; `YOLO_AUTOINSTALL=false` disables
missing-module installation. Never fall back to unrestricted pickle loading.
[PyTorch warns that models remain trusted programs](https://github.com/pytorch/pytorch/security/policy);
restricted loading is additional protection, not a sandbox. No unknown checkpoint
was deserialized during this review. A full restricted-loader inference/export
smoke on the pinned asset still needs coordination with issue #1's GPU owner.

Default and explicit engines require trusted files, matching engine/weights
hashes, GPU capability and TensorRT version. Weights must independently match
the upstream pin. Local manifests only bind a locally trusted build to its bytes
and device; they cannot prove the engine was compiled from those weights.
`--manifest-only` must only be used on your own known local build. Native engine
deserialization remains trusted code. YAML/ONNX/URLs/other model formats are not
accepted by the playback entry point.

## Dependencies and advisory coverage

`uv.lock` pins runtime, optional accelerator, development and platform-specific
transitives with registry artifact hashes; PyTorch uses the explicit CUDA index.
Use `uv sync --locked`, never an unreviewed resolver upgrade. The build backend is
pinned to Hatchling 1.33.0 and represented with its dependencies in the dev lock.
Isolated builds can still resolve backend transitives independently; use the
reviewed environment/lock and review build-tool changes. Hashes prevent unnoticed
artifact changes, not a malicious publisher or vulnerable pinned binary.

[`scripts/security_audit.py`](scripts/security_audit.py) queries OSV for **all**
locked registry package/version pairs, including non-Windows and accelerator
packages, normalizing the PyTorch CUDA local suffix to the upstream version.
The sanitized snapshot is [security-dependencies.json](security-dependencies.json).
No matches were returned on 2026-10-30. This is not a clean bill of health:

- [PyTorch GHSA-63cw-57p8-fm3p](https://github.com/pytorch/pytorch/security/advisories/GHSA-63cw-57p8-fm3p)
  affects <=2.9.1; upstream lists >=2.10.0 patched. Locked Torch 2.14.1 is beyond
  that range. This does not establish immunity to other native/model flaws.
- ZDI published seven Ultralytics advisories on 2026-10-28, which the OSV snapshot
  did not match. Sources: [812 unrestricted checkpoint load](https://www.zerodayinitiative.com/advisories/ZDI-26-812/),
  [813 dataset YAML execution](https://www.zerodayinitiative.com/advisories/ZDI-26-813/),
  [814 missing-module command injection](https://www.zerodayinitiative.com/advisories/ZDI-26-814/),
  [815 model parsing](https://www.zerodayinitiative.com/advisories/ZDI-26-815/),
  [816 dataset cache deserialization](https://www.zerodayinitiative.com/advisories/ZDI-26-816/),
  [817 model parsing](https://www.zerodayinitiative.com/advisories/ZDI-26-817/),
  [818 Google Drive path traversal](https://www.zerodayinitiative.com/advisories/ZDI-26-818/).
  The pinned 8.4.193 source offers restricted loading and no-eval model construction
  when the safe-load flag is set. Hash-gating, restricted loading and disabled
  autoinstall mitigate the model-entry paths here. Training, dataset YAML/cache
  loading, and Google Drive downloading are not used. Do not use this environment
  as an untrusted model/dataset conversion service. These are still **high-severity
  release blockers pending upstream status and an independent reachability review**;
  the newest pin alone is not a resolution. Page 815 could not be retrieved in
  this review; its full analysis is also pending.
- TensorRT 10.16.1.11/ONNX 1.23.2 and all locked NVIDIA/CUDA packages were included
  in the registry query; scanner coverage of vendor binaries, drivers and bundled
  native codec libraries is incomplete. Native vendor security-bulletin review
  and installed player/driver inventory remain required before public release.

No vulnerable version was silently waived for publication. Local trusted-preview
use retains the above documented risks; public preview remains blocked.

## Outbound traffic, privacy and logs

Explicit setup contacts configured package indexes (PyPI, PyTorch and possibly
NVIDIA), GitHub and its asset CDN. The advisory script sends package names and
versions to OSV; it sends no footage, filesystem paths or credentials. GitHub API
metadata lookups were read-only except an attempted private-reporting enable that
returned HTTP 404; no visibility or release settings were changed.

Playback code sends local IPC commands and decodes local video frames in memory;
it contains no footage-upload operation. Before Ultralytics import, it sets
`YOLO_OFFLINE=true` (avoids that version's connectivity probe), disables automatic
dependency installation and selects `<backend>/runs/ultralytics` for isolated
settings. It does not edit the user's existing Ultralytics settings. In the pinned
source, Sentry initialization is gated on `ONLINE`, sync settings and the `yolo`
CLI; this backend runs as `reframe` with offline forced. These are observed code
paths, not a firewall. No dependency-wide packet capture or system network-denial
validation has been completed. Native libraries/drivers, mpv scripts and future
dependency changes may behave differently; no universal offline guarantee is made.

Per-session logs are in `<backend>/runs/lua/<session-id>/`. Lifecycle records have
IDs, PIDs, timestamps/progress, states, saved player view settings and model/log
paths. Camera telemetry has playback positions/speed, dimensions, person IDs and
boxes, camera targets/velocity and timing/performance metrics; it does not save
video frames or appearance histograms. Paths and exception messages can appear in
stderr/mpv logs; captured stdout/stderr is limited to 65536 bytes. Generic protocol
errors avoid copying arbitrary exception text into player status, but local
stderr may still contain it. Session directories are private. Logs have **no
automatic rotation/expiry** and can grow during long sessions. Delete your own
inactive session directories when no longer needed; never share complete logs
without checking paths and derived movement information. No private footage was
used, uploaded, or packaged in these security checks.

## Validation and remaining acceptance gates

Native Windows automated evidence includes DACL rejection/hardening, private
directory inheritance, wrong server PID, an isolated mpv with two successive
verified data connections after hardening, OS-released ownership locks, Lua
lifecycle/crash restoration, malicious manifests/reparse targets, recovery after
partial upgrades, edited-file preservation and malformed/stale protocol inputs.
Tests launch only their own null-output, idle mpv; there is no media/GPU workload.
The real Lua integration also passed preflight, private endpoint creation,
backend startup in an empty player, waiting state and clean stop. The test-only
players disable OS media controls: an earlier run logged `LUA_LIFECYCLE_PASS`
but timed out during native player shutdown; the focused retry and final suite
passed. This does not establish full interactive shutdown coverage.

Final checks from the extension checkout:

```text
uv run --locked pytest -q --basetemp=runs/security-pytest-release-check
  174 passed, 1 skipped in 3.51s (the skip requires native Unix)
uv run --locked ruff check src scripts tests
  All checks passed!
uv run --locked ruff format --check src scripts tests
  55 files already formatted
uv lock --check
  Resolved 84 packages; exit 0
git diff --check
  exit 0
python scripts/security_audit.py
  exit 0; 81 locked registry package/version pairs, 0 OSV matches
```

The advisory snapshot is tied to the LF-normalized lock SHA-256. An isolated
source-package smoke check verified the package checksum, security-document
inclusion and exclusion of environments/models/runs. CLI help for `attach` and
the security helper was checked. No live configuration was installed or changed.

Unix tests are included but skipped on Windows. No Linux/macOS ownership/socket,
umask, peer credentials, wrong-UID access denial or cleanup behavior has been
verified natively. Do not infer native Unix acceptance from mocked checks.
Windows DACL inspection is native, but no second non-admin account was used to
attempt access. mpv creates the pipe before Lua can harden it: there is a startup
window with mpv's default DACL. Existing connected handles cannot be revoked by
changing the DACL. Resolving that exposure (for example upstream atomic creation
with an appropriate descriptor) and a second-account denial test remain release
gates, even though subsequent connections pass our native checks.

Issue #2 must stay open for native Unix evidence, Windows startup/access-denial
gates, advisory/vendor review, restricted-loader inference/export and external
private-reporting verification. The private-repository collaborator route is
documented in [SECURITY.md](SECURITY.md); GET/PUT private-vulnerability-reporting
returned HTTP 404, so no external route is claimed configured.

Issue #1's immutable `cf9d0e8` Windows verification has now completed; its GPU
priority is released. Its [sanitized results](https://github.com/EatEmAll/mpv-ai-video-reframe/issues/1#issuecomment-6529189109)
are baseline evidence only, not verification of these security changes. The local
`RESULTS.md` and `COMMANDS.md` were read to reconcile the following limits:

- The default editable install under a Unicode backend path failed CP1252 `.pth`
  processing during Python `site` initialization. `-I` still imports `site` and
  processes installed `.pth` files; our preflight cannot validate anything if the
  interpreter fails before executing it. Ownership checks establish trust, not
  Unicode/bootstrap compatibility. No Unicode backend success is claimed here.
- A non-editable Unicode environment passed setup, but CUDA initialization
  aborted with `0xC0000409` / `Cannot load symbol cudnnGetVersion`; CPU initialized
  successfully. `--no-editable` is not an established CUDA workaround. These
  remain issue #1 environment/native-loader failures, not fixes delivered by #2.
- Baseline 1080p CUDA and measured CPU quality passed; CPU controls were unmeasured.
  The 4K60/1x `.pt` CUDA run failed the frame-health gate (0.667% additional drops
  against a 0.5% budget). TensorRT was not measured. None of those quality or
  performance outcomes was repeated on the patched backend.
- The baseline's `.pt` SHA-256 matches this review's upstream pin. That supports
  artifact alignment only; the new restricted-loader path still needs an
  inference/export integration check. The published source omits the performance
  harness, so reproducible distribution of that harness is separate parent work.

For post-#2 integration, use an isolated config with the new Lua/backend together,
verify pinned weights and restricted loader, rebuild/trust the engine if needed,
repeat enable/disable, empty-player, seek, playlist, startup failure and crash
restoration, then compare 1x/4x FPS, full-height fill and camera quality. No heavy
GPU/render run was attempted by this implementation task. Released GPU priority
does not substitute for these integration checks; no new benchmark was launched.
