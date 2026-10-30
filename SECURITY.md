# Security policy

This is a private, unreleased preview. Only the current `main` revision receives
security fixes; there are no supported public releases. Do not publish a preview
until the blockers in [SECURITY_REVIEW.md](SECURITY_REVIEW.md) and issue #2 are
resolved. Passing unit tests does not establish platform or dependency safety.

## Reporting privately

While this repository is private, authorized collaborators can report to the
maintainer in a [new private-repository issue](https://github.com/EatEmAll/mpv-ai-video-reframe/issues/new).
Verify the repository still displays **Private** before posting sensitive details;
all repository collaborators can read these reports. Start with a minimal text
description and a synthetic reproducer. Do not attach private footage, credentials,
complete user logs, or exploit payloads containing personal data.

If you do not have repository access, there is currently no verified external
private reporting channel. Do not post a public issue or disclose details in
another project's tracker. The maintainer must arrange a private contact channel
when granting preview access. Before any public release, enable GitHub private
vulnerability reporting (and verify the external reporter flow) or publish a
maintainer-approved security contact. The reporting API returned HTTP 404 on
2026-10-30; external private reporting is **not verified/configured** by this work.

The maintainer aims to acknowledge reports within seven days and give a triage
update within fourteen days. These are response targets, not a staffed service
or guaranteed remediation deadline. Coordinate disclosure privately after triage.

## Safe operation

Run as a normal user. Trust the Lua script, Python interpreter/environment,
backend, configuration, mpv/FFmpeg, dependencies, weights and locally built
engines as executable inputs. Session IDs reject stale messages; they are not
authentication. mpv IPC grants local player control to anyone who can connect.
Never forward the pipe/socket, expose TCP IPC, or run the backend as a service.

Use a private backend/configuration tree that other users cannot modify. Checks
reject unsafe ownership, write permissions, links/reparse points and hard-linked
installer targets. Unix virtual-environment interpreter links are permitted only
from a trusted parent to a trusted resolved executable. System-owned Unix alias
paths may require selecting a canonical path. Do not weaken permissions to bypass
a failure. Edited configuration can select executable code and must be reviewed.
The interpreter must already be trusted before it can run the checker; this is
defense in depth, not protection against a compromised installation or same-user
malware. Administrators/root/SYSTEM and processes under your account are trusted.

`doctor` is the explicit model-download step. It checks the pinned upstream
YOLO26n checksum before loading. Unknown `.pt` weights, YAML, ONNX and other
formats are rejected. TensorRT engines must be locally built from the pinned
weights, in trusted storage, with matching device/runtime/content metadata.
`--manifest-only` is an explicit assertion that you built and trust that engine;
it cannot establish upstream authenticity or make an arbitrary engine safe.
Never bless an engine downloaded from someone else.

See [the review](SECURITY_REVIEW.md) for native permission evidence, remaining
Windows startup exposure, dependency advisories, outbound network behavior,
privacy, retention, and integration checks still required before release.
