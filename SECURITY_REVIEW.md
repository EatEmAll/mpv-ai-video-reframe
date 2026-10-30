# Local security review â€” 2026-10-30

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
  checked using Win32 APIs. Windows pipe repair has been removed; trusted mpv
  0.41.x is required before script-created IPC starts (see follow-up below). Additional capability/account write grants are
  rejected rather than guessed safe. Unsupported ACL entry types fail closed.
- **Shared temporary directories and endpoint substitution:** sticky root-owned
  Unix temp ancestors may contain the private root; pre-created unsafe roots,
  symlinks/reparse points and unsafe ownership fail. Windows uses LOCALAPPDATA
  rather than a broadly writable temp directory. The client verifies its actual
  connected handle/peer before the pinned IPC library starts its reader or sends
  property commands. Linux checks `SO_PEERCRED`; systems with `getpeereid` check
  UID. Other Unix credential APIs fail closed. Both the check and actual data
  connections request identification-level SQOS before opening the handle.
  Native token-level coverage verifies SecurityIdentification. This is not a
  general sandbox.
- **Existing endpoints:** never silently chmod/harden an endpoint configured by
  the user. It must already meet the private-parent/socket or pipe DACL/owner/PID
  policy. Only the random Unix socket created by this Lua instance is hardened;
  Windows pipes are inspected without repair.
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
- All seven ZDI advisories, including 815, now have a fresh pinned-source
  reachability assessment and regression evidence in
  [SECURITY_ADVISORIES.md](SECURITY_ADVISORIES.md). This resolves the missing-page
  and unreviewed-call-path findings for this extension; it does not declare
  Ultralytics generally patched or safe for arbitrary model/dataset use.
- Vendor/native codec inventory and precise remaining mpv codec provenance gate
  are recorded in that assessment. TensorRT's cited affected range excludes the
  original pin for the May bulletin, but October's medium-severity bulletin
  required a new 11.2.1.2 pin and runtime refusal below 11.2. The installed
  player's old bundled FFmpeg still needs provenance/update.

## Outbound traffic, privacy and logs

Explicit setup contacts configured package indexes (PyPI, PyTorch and possibly
NVIDIA), GitHub and its asset CDN. The advisory script sends package names and
versions to OSV; it sends no footage, filesystem paths or credentials. GitHub API
metadata lookups in the original pass were read-only except a failed reporting
enable. Subsequently the maintainer authorized public visibility and enabled
private vulnerability reporting; the follow-up verified `enabled:true` and the
public reporting entry. There are no tagged releases/wiki listings yet.

Playback code sends local IPC commands and decodes local video frames in memory;
it contains no footage-upload operation. Before Ultralytics import, it sets
`YOLO_OFFLINE=true` (avoids that version's connectivity probe), disables automatic
dependency installation and selects `<backend>/runs/ultralytics` for isolated
settings. It does not edit the user's existing Ultralytics settings. In the pinned
source, Sentry initialization is gated on `ONLINE`, sync settings and the `yolo`
CLI; this backend runs as `reframe` with offline forced. These are observed code
paths. A process-local Python audit guard now rejects Internet socket creation,
DNS, connect/bind and datagram sends outside the explicit setup download context.
Native libraries, child processes and independently running mpv are outside this
guard; it is not a firewall. No dependency-wide packet capture or OS firewall
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

Current Windows regressions cover private directory inheritance, permissive-pipe
refusal without mutation, connected-handle owner/DACL/PID/image/version checks,
restricted-token denial, native data-handle SQOS, OS-released ownership locks,
Lua lifecycle/crash restoration and status indicator, malicious installer
manifests/reparse targets, interrupted-upgrade recovery, edited-file preservation,
and malformed/stale protocol inputs. Test players are owned idle/null-output
instances; no user player is interrupted by this task.

Final security-task checks after the TensorRT lock update:

```text
.venv/Scripts/python.exe -m pytest -q --basetemp=runs/security-followup-final2
  183 passed, 1 skipped in 9.35s (native Unix test skips on Windows)
.venv/Scripts/ruff.exe check src/reframe scripts/export_engine.py tests/test_models.py tests/test_security.py tests/native_unix_security.py
  All checks passed!
.venv/Scripts/ruff.exe format --check src/reframe scripts/export_engine.py tests/test_security.py tests/test_models.py tests/native_unix_security.py
  33 files already formatted
uv lock --check
  Resolved 84 packages; exit 0
git diff --check
  exit 0 (only Git newline-conversion notices)
python scripts/security_audit.py
  exit 0; 81 locked registry package/version pairs, 0 OSV matches
```

Tests use the existing environment deliberately; the new optional accelerator was
not installed during the parallel GPU work. The current advisory snapshot matches
the updated LF-normalized lock SHA-256. Initial-pass source-package smoke evidence
predates this follow-up; parent must recheck final package inclusion, especially
the new audit document/source snapshot, before publishing. Source files remain
uncommitted for parent integration. No public release was created.

The parent separately installed the badge/config at the user's request and
reported a normal-startup, generated-ruler CPU test: Ctrl+r moved disabled ? active
? disabled, and OFF/ON screenshots were inspected. This is parent integration
evidence, not an inference/export/render claim made by these security regressions.
Installed Lua and source SHA-256 were both
`cb47a3828753b32c444c01c5a5ebf487a66ebbdd60659ec23bf4f0e3a0c32d49`.
No subsequent Lua edits were made in this follow-up. Broader stable-source/GPU
integration remains with the parent and Windows task.

## Follow-up: native permissions and reporting (2026-10-30)

Windows no longer requests WRITE_DAC or repairs mpv's DACL. Source inspection of
[mpv 0.41.0 input/ipc-win.c](https://github.com/mpv-player/mpv/blob/v0.41.0/input/ipc-win.c)
and installed revision `af9c81fa1` establishes that `create_restricted_sd()` supplies
the current-user-only descriptor to both the first and subsequent pipe instances,
with first-instance exclusion and remote-client rejection. The helper checks the
running player's trusted image path and its reported 0.41.x version **before**
Lua sets `input-ipc-server`. Other families fail closed pending source review.
The actual connected pipe's owner, ACL, server PID and trusted image/version are
checked again before the library reader sends commands. A trusted binary's version
is not cryptographic attestation: use official/reviewed builds, not a modified
binary claiming that version. No global token default DACL is changed.

This resolves the earlier unsupported assumption that Lua must fix an initially
permissive mpv pipe. Refusal of permissive pipes also eliminates reliance on
revoking already-connected handles. Reusing a pipe that another client or tool
previously exposed/modified is unsupported: restart the trusted player with a new
endpoint. Same-account clients, administrators and SYSTEM remain trusted.

Native Windows coverage exercises unmodified mpv pipe denial with a restricted
token (`ERROR_ACCESS_DENIED`), normal-account reconnect, wrong server PID,
permissive-DACL refusal even with `harden=True`, and identification-only SQOS on
a real data handle (server-observed TokenImpersonationLevel = 1). This is
**restricted-token evidence, not full second-account validation**. No other
signed-in session, account or global security setting was changed. No independent
Windows account credentials were available; a real separate-account denial test
remains an optional stronger acceptance check, not evidence claimed here.

WSL Ubuntu-24.04 / Python 3.12.3 / uid 1000 ran
[`tests/native_unix_security.py`](tests/native_unix_security.py) from a disposable
private directory on the Linux filesystem. The Windows source/dependency module
was copied into that fixture; `/mnt/c` permissions were not used as evidence.
Under umask 000 it verifies 0700 root/session directories, 0600 socket, accepted
same-UID/PID credentials, wrong-PID rejection, wrong-UID peer rejection even with
a user-owned socket pathname, and connection denial by a child dropped to uid/gid
65534 with no supplemental groups. `sudo -n` was available only for these isolated
children; no account/session changes were made. Closing sockets leaves pathnames;
only test-owned closed endpoints are unlinked, and a second live socket survives.
Production Lua/backend never unlink endpoints by path. Empty runtime directories
can remain after mpv exits. This is native Linux security evidence, not Linux GUI
portability (#1), nor evidence for macOS `getpeereid`.

Reporting is now enabled on the public repository: authenticated GET returned
`enabled:true`; an unauthenticated Security-page fetch exposed Report a
vulnerability and following that URL redirected to GitHub login. No advisory was
submitted or fabricated external identity used. [SECURITY.md](SECURITY.md)
now warns that ordinary issues are public. External logged-in submission is
unexercised, but the configured route and public entry are verified.

Remaining release gates: establish patched/provenance-verified FFmpeg in the
installed mpv build, final stable-source restricted-loader inference/export and
player integration with #1, plus any platform guarantees beyond the native tests
above. See [the advisory assessment](SECURITY_ADVISORIES.md) for exact limits.
Issue #2 stays open; 0 OSV matches alone is never a resolution.

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


## Parent follow-up: reviewed player provenance

The codec provenance gate now has a specific reviewed verification player:
[MPV_PROVENANCE.md](MPV_PROVENANCE.md), with comparison evidence in
[mpv-provenance.json](mpv-provenance.json). The October 21 shinchiro mpv 0.41
build's reported FFmpeg revision contains the cited fixing ancestors, its archive
matches the release API SHA-256, and native trusted-player/data-handle IPC smoke
passes. This does not clear the old system-installed March binary or substitute
for final combined-source inference/playback on the reviewed player. No user
player was stopped and no system installation was replaced during this check.


## Final combined Windows integration evidence

The Windows follow-up has now exercised the combined root source, including
restricted loading, pinned model checks, networking controls, runtime path guards
and preservation of configured render timing. Actual TensorRT 11.2.1.2 export
and inference passed; CPU controls and full 1080p/1x playback passed; the integrated
engine 4K/1x run passed every gate. See
[WINDOWS_VERIFICATION_RESULTS.md](WINDOWS_VERIFICATION_RESULTS.md) for the exact
source manifests, configurations, repeated experiments and failed runs.

Earlier statements above that inference/export had not yet been run describe the
staged review history. They are superseded for these measured configurations only.
The final 4K/2x engine frame-health failure remains a performance limit under #1,
not a waived gate or a security test pass. Native Linux GUI/macOS verification,
independent-account Windows validation beyond restricted-token checks, external
logged-in reporter submission and manual perceptual review remain unclaimed.
The source is public and reporting is enabled, but no preview release/listing
has been published by this work.


## Native Linux verification (2026-10-31)

Run on Omarchy x86_64 from a clean copy with mpv 0.41.0 and CPU inference; see
[LINUX_VERIFICATION.md](LINUX_VERIFICATION.md). The first run found that
`trusted_executable` rejected uv-managed interpreters, because uv links a version
directory and only the final path component was followed. The documented install
and every enable then failed closed. Links are now followed in any component. Each
link must be user- or root-owned in a trusted parent, and the resolved file must
pass `trusted_path`. Backend paths still reject links.

Native checks confirmed `0700` runtime and session directories and a `0600` socket
on the real script-created path, including under umask 000. Symlinked, permissive
and regular-file runtime roots, unsafe endpoints, writable backends and interpreters
were refused. Tampered, truncated, unknown and non-`.pt` models were refused before
deserialization, confirmed by a pickle canary that never fired. A real root-owned
peer was rejected through `SO_PEERCRED`. Installer link, hard-link, traversal and
duplicate-key manifests were refused, and an interrupted upgrade recovered.

`strace` across mpv and the backend recorded no Internet socket during playback,
including with weights absent. This covers one run; it is not a universal offline
guarantee. An OSV re-query returned 0 matches for 81 packages and the six pinned
Ultralytics sources matched.

Arch's mpv uses system FFmpeg 9.0.2, a release at or after the 9.0 line that
FFmpeg's security page lists for all eight cited CVEs. This is version evidence;
the distribution build was not inspected.

**Not run:** separate-uid connection denial needs real privilege, which the test
session lacked. `tests/native_unix_security.py` reports that as partial, with exit
3. The earlier WSL run remains the only evidence for it.
