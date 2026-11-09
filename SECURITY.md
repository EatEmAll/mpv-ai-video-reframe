# Security policy

This is a public source preview with no tagged releases or supported public
binary releases. Only current `main` receives security fixes. Public repository
visibility does not establish release readiness; issue #2 and the remaining gates
in [SECURITY_REVIEW.md](SECURITY_REVIEW.md) still apply.

## Reporting privately

Use GitHub's [Report a vulnerability](https://github.com/EatEmAll/mpv-ai-video-reframe/security/advisories/new)
from the repository's [Security page](https://github.com/EatEmAll/mpv-ai-video-reframe/security).
GitHub private vulnerability reporting is enabled (`GET .../private-vulnerability-reporting`
returned `enabled:true` on 2026-10-30). An unauthenticated fetch sees the public
Report a vulnerability entry; following it redirects to GitHub sign-in. No bogus
advisory was submitted, and a separate external reporter identity was not tested.

Ordinary repository issues are **public**. Never put undisclosed vulnerability
details there. Start the private report with a minimal description and synthetic
reproducer; omit private footage, credentials and complete user logs. If the
private form is unavailable, wait for a maintainer-approved private contact rather
than posting sensitive details publicly.

The maintainer aims to acknowledge reports within seven days and give a triage
update within fourteen days. These are response targets, not a staffed service
or guaranteed remediation deadline. Coordinate disclosure privately after triage.

## Safe operation

Run as a normal user. Trust the Lua script, Python interpreter/environment,
backend, configuration, mpv/FFmpeg, dependencies, weights and locally built
engines as executable inputs. Session IDs reject stale messages; they are not
authentication. mpv IPC grants local player control to anyone who can connect.
Never forward the pipe/socket, expose TCP IPC, or run the backend as a network
service. The only background process is the local shared detector described below.

Use a private backend/configuration tree that other users cannot modify. Checks
reject unsafe ownership, write permissions, links/reparse points and hard-linked
installer targets. Unix interpreter links, including a linked version directory
such as uv's, are permitted only when each link is user- or root-owned in a trusted
parent and resolves to a trusted executable. Backend paths must still avoid links.
Do not weaken permissions to bypass a failure. Edited configuration can select executable code and must be reviewed.
Windows Unicode backend paths have unresolved Python editable-install/native CUDA
loader failures in the [baseline assessment](SECURITY_REVIEW.md#validation-and-remaining-acceptance-gates).
Ownership checks and `-I` do not resolve those compatibility failures.
The interpreter must already be trusted before it can run the checker; this is
defense in depth, not protection against a compromised installation or same-user
malware. Administrators/root/SYSTEM and processes under your account are trusted.

The shared detector (`reframe serve`) is a per-user background process that the
first player starts and that exits after `detector_idle_timeout` seconds without a
player. It listens only on a Windows named pipe or on a Unix socket in the private
per-user runtime directory, never on TCP. Before either side sends anything else,
the two prove they hold a random 32-byte key kept in that private directory
(mutual HMAC-SHA256 challenge), so another account that can reach the endpoint
cannot read frames or use the GPU. On Windows the pipe has the default security
descriptor and the key is what excludes other accounts; on Unix the peer must also
be your user. Messages are length-bounded fixed binary layouts, never pickle, and
frames are limited to 4096 by 4096. A connection that stalls during authentication
holds one service thread for five seconds. The service runs the same code, weights
and engine as an in-process backend, so it is trusted exactly like them, and
processes under your account can talk to it just as they can reach mpv's IPC.
`shared_detector=no` restores one private model per player.

`doctor` is the explicit model-download step. It checks the pinned upstream
YOLO26n checksum before loading. Unknown `.pt` weights, YAML, ONNX and other
formats are rejected. TensorRT engines must be locally built from the pinned
weights, in trusted storage, with matching device/runtime/content metadata.
`--manifest-only` is an explicit assertion that you built and trust that engine;
it cannot establish upstream authenticity or make an arbitrary engine safe.
Never bless an engine downloaded from someone else.

See [the review](SECURITY_REVIEW.md) for native permission evidence, Windows build requirements, dependency advisories, outbound network behavior,
privacy, retention, and integration checks still required before release.
