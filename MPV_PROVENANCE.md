# Reviewed Windows verification player

Reviewed 2026-10-30 for issue #2. This is a specific source/build provenance
assessment, not a certification that mpv or FFmpeg has no vulnerabilities.
The player is an external prerequisite; its binaries are not included in source
archives or the Python package.

## Selected portable build

The [mpv installation page](https://mpv.io/installation/) links shinchiro as a
Windows distributor. The selected [20261021 release](https://github.com/shinchiro/mpv-winbuild-cmake/releases/tag/20261021)
contains `mpv-x86_64-20261021-git-e471496c56.7z`:

- Archive SHA-256: `4a355f3e89c83fabc730d33fb4e759dfec747806ed2287b61a90d1681b83e8d3`,
  matched to the release API asset digest before extraction.
- `mpv.exe` SHA-256: `8c2e4d24eb961723cc5e2f1f4401982836e82c96e0e0a944c0576090fc48993f`.
- Reported player: `0.41.0-1227-ge471496c5`, built 2026-10-21.
- Reported FFmpeg: `N-127893-g9a861e968`, libavcodec 63.2.100.
- Full FFmpeg revision: `9a861e9687d30e657d59521817cad18405995983`.

[Machine-readable provenance](mpv-provenance.json) records the hashes, upstream
source URLs, and GitHub comparison results. Release-host metadata is a trust
anchor and can itself be compromised; matching a digest is not an independent
binary attestation or proof of reproducible compilation.

## FFmpeg fixes

The [FFmpeg security list](https://ffmpeg.org/security.html) provides fixing
commits. Each of these listed CVEs has at least one applicable fixing commit
verified as an ancestor of the selected build's reported FFmpeg revision:
CVE-2026-8461, CVE-2026-12706, CVE-2026-30997, CVE-2026-30998,
CVE-2026-30999, CVE-2026-66038, CVE-2026-70629, and CVE-2026-70631.
Some release-branch backport hashes diverge from master; the corresponding
master fixing hash is an ancestor. The JSON retains both results rather than
claiming every backport hash is present. Commit dates alone were not used to
establish patch inclusion. This covers the cited set, not all possible defects
or third-party libraries linked into the executable.

The previously installed March development build reports FFmpeg
`N-123099-g862338fe3` and remains unverified for those later fixes. Results using
that old player are historical and do not clear the codec gate. Use the reviewed
portable player for current verification via a process-scoped `REFRAME_MPV`
absolute path; do not infer that another executable with the same mpv major/minor
version has the same FFmpeg patches. No user player was stopped or system binary
replaced during this provenance check.

## IPC and runtime evidence

The selected player's [input/ipc-win.c](https://github.com/mpv-player/mpv/blob/e471496c56/input/ipc-win.c)
creates the current-user/integrity security descriptor before the first
CreateNamedPipe call and reuses it for subsequent instances. First-instance
exclusion and remote-client rejection are present. A native Windows smoke passed
the extension's reviewed-player gate and verified a real connected data handle
against the expected server PID; the owned test player exited cleanly. This
confirms basic IPC compatibility, not full inference or rendered playback.
Those integration/quality measurements must identify this exact player in their
manifests. Native Unix permission evidence is separate from this Windows binary.
