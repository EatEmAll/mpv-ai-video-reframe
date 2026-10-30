# Pinned-path advisory assessment ? 2026-10-30

This is a fresh source/call-path assessment for this extension, independent of the
previous review's conclusions. It is not an upstream fix certification or exploit
reproduction. No arbitrary checkpoint, malicious dataset or advisory payload was
executed. [security-upstream-sources.json](security-upstream-sources.json) records
SHA-256s of six files fetched from the upstream `v8.4.193` tag; each matches the
installed source after newline normalization. The reviewed files are
`nn/tasks.py`, `utils/{checks,patches,downloads,__init__}.py`, and `data/utils.py`.

## Seven Ultralytics advisories

All seven primary ZDI pages were retrieved, including previously missing 815.
They were published 2026-10-28 as zero-days; the pages reviewed do not identify a
fixed release. Safe-load support in our pin is opt-in and does not justify saying
the entire package is patched. CVEs 118613?118619 below have the `CVE-2026-` prefix.

| Advisory / CVE suffix | Upstream entry | Reachability and disposition for the extension |
| --- | --- | --- |
| [812](https://www.zerodayinitiative.com/advisories/ZDI-26-812/) / 118613 | `torch_load` | Checkpoint loading is reachable. Extension accepts only the size/SHA-256-pinned local asset before deserialization, forces `weights_only=True` through the restricted loader and PyTorch environment control, and refuses unavailable/previously disabled safe loading. Unrestricted fallback is unsupported. |
| [813](https://www.zerodayinitiative.com/advisories/ZDI-26-813/) / 118614 | `check_det_dataset` | Training/validation dataset YAML and its download code are outside the extension entry points. Detection consumes in-memory NumPy frames; tracker YAML uses `yaml.safe_load` on a trusted bundled file. No user-supplied dataset input is exposed. |
| [814](https://www.zerodayinitiative.com/advisories/ZDI-26-814/) / 118615 | `torch_safe_load` missing-module installation | Model loading is reachable, but the restricted branch re-raises `ModuleNotFoundError` before the installer/retry. `YOLO_AUTOINSTALL=false` is also forced. Regression substitutes a benign failing loader and asserts one restricted call and no installer call. |
| [815](https://www.zerodayinitiative.com/advisories/ZDI-26-815/) / 118616 | `parse_model` | Construction is transitively available in the checkpoint loader. It is not treated as categorically unreachable. Restricted mode resolves activations through `_SafeLoad.activation`, permits module classes only and parses argument literals with `ast.literal_eval`; no user architecture YAML is accepted. Regression rejects an invalid activation with an `eval` sentinel that must never run. |
| [816](https://www.zerodayinitiative.com/advisories/ZDI-26-816/) / 118617 | `load_dataset_cache_file` | Upstream still calls NumPy with `allow_pickle=True`; this is not patched by our flags. The extension never loads dataset cache files or invokes training/validation. Users must not run that functionality on untrusted data in this environment. |
| [817](https://www.zerodayinitiative.com/advisories/ZDI-26-817/) / 118618 | `parse_model` | Same restricted-construction boundary as 815. The public descriptions do not disclose enough detail to distinguish the two injection sites conclusively; they are assessed separately as published reports, not asserted to be duplicates. Module names resolve through known namespaces, and string arguments use literal parsing. |
| [818](https://www.zerodayinitiative.com/advisories/ZDI-26-818/) / 118619 | Google Drive download path handling | Neither playback nor setup calls Google Drive download helpers. Setup uses a fixed GitHub HTTPS release URL, byte limit and upstream checksum; local absolute verified model paths prevent bare-name repair/download. Internet socket creation/DNS/connect/datagram operations from Python dependencies are denied outside the explicit setup context. |

`Detector` and `doctor` use `load_yolo`, which checks effective `SAFE_LOAD` and
`_SafeLoad.SUPPORTED`. `configure_inference` refuses an already imported unsafe
upstream module and sets `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`, closing the upstream
unsupported-Torch fallback. Engine loading independently revalidates trusted
files, pinned companion weights and device/runtime/hash metadata. Local manifests
remain assertions about trusted local builds, never authorization for downloaded
engines. Source/environment compromise and same-user malicious replacement are
outside this boundary; restricted loading remains defense in depth, not a sandbox.

The seven findings' *extension input paths* are constrained as above. Untrusted
model/dataset conversion remains unsupported. This does not waive an upstream
high-severity advisory for unrestricted Ultralytics use. No new claim of
restricted-loader inference/export success is made from these unit regressions;
that needs the final stable-source integration run owned by the Windows task.

## Vendor and native codec review

- [NVIDIA TensorRT May 2026](https://nvidia.custhelp.com/app/answers/detail/a_id/5836)
  fixes high-severity CVE-2026-24188 in 10.16.1. The old 10.16.1.11 pin was outside
  that affected range. The newer [October bulletin](https://github.com/NVIDIA/product-security/blob/main/2026/5891/5891.md)
  covers medium-severity CVE-2026-65122 through 11.1 and fixes it in 11.2. This
  review upgrades all three locked CUDA13 TensorRT packages to **11.2.1.2**.
  Runtime selection/manifest recording and export refuse versions below 11.2;
  incompatible cached engines fall back to pinned PyTorch weights. An explicit
  engine refuses. Environments were not resynchronized during parallel GPU work;
  sync the new accel lock and rebuild engines before validation. This dependency
  change requires export/inference compatibility testing, not an assumed pass.
- [NVIDIA GPU driver September bulletin, updated October 13](https://github.com/NVIDIA/product-security/blob/main/2026/5861/5861.md)
  lists GeForce Windows R615 fixes at 616.56. The locally inventoried driver is
  617.42, beyond that threshold. This is branch/version evidence, not testing of
  each driver CVE or a claim about all platforms/driver branches.
- [CUDA Toolkit January bulletin](https://nvidia.custhelp.com/app/answers/detail/a_id/5755)
  concerns Nsight profiling/monitoring/installer components. This extension uses
  CUDA runtime/cuDNN packages, not Nsight tools or their report-processing paths.
  A CUDA13 package name alone must not be equated to the affected toolkit tools.
  TensorRT-LLM, Triton servers, containers and NeMo are absent and are not covered
  by this application's TensorRT dependency. The [2026 vendor index](https://github.com/NVIDIA/product-security/blob/main/2026/README.md)
  was checked for newer relevant bulletins.
- [FFmpeg's primary security list](https://ffmpeg.org/security.html) identifies
  fixes including CVE-2026-8461, 30997?30999, 66038, 70629 and 70631 in the 9.0
  release line, with selected backports to 8.1.2/8.0.3. PyAV 19.0.2 locally reports
  **FFmpeg 9.0.2**, libavcodec/libavformat **63.1.102**; no PyAV upgrade is justified
  by that list. This version evidence does not inspect every bundled binary patch.
  The installed mpv separately reports **FFmpeg N-123099-g862338fe3**,
  libavcodec **62.24.100**, built March 2. It predates later listed fixes and is
  not covered by PyAV's version. Its exact backport/provenance is unverified.
  Parent follow-up selected and checksum-verified the October 21 portable build,
  verified the cited FFmpeg fixing ancestry and native IPC compatibility; see
  [MPV_PROVENANCE.md](MPV_PROVENANCE.md). Final rendered integration must use
  that reviewed build. The inventoried old installed binary is not security-cleared.
  Updating a user's player/driver and validating playback belong to coordinated
  environment integration; no system installation was silently changed here.

## Scanner and outbound evidence

The refreshed [OSV snapshot](security-dependencies.json) covers 81 locked registry
package/version pairs and is tied to the updated lock's LF-normalized SHA-256.
There are zero returned matches. TensorRT's October advisory demonstrates why
that result cannot establish safety: vendor bulletins and bundled codec/source
review are independent requirements.

The Python process installs a permanent audit hook before Ultralytics import.
Synthetic subprocess regression checks Internet sockets/DNS/TCP/UDP refusal and
explicit setup permission. Unix IPC remains allowed. No packet capture or OS
firewall rule was installed. Native DLLs, subprocesses and mpv are outside the
Python hook; this is practical prevention of ordinary Python dependency traffic,
not a hostile-code sandbox or a universal offline guarantee. The fixed-URL setup
download alone temporarily permits networking on its thread. Package installation
and the standalone OSV audit remain explicit network operations.
