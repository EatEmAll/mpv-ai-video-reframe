# mpv Reframe

An mpv extension that keeps a person in frame. It fills the window height with
the video and pans the crop sideways to follow them. It runs in your normal mpv,
and `Ctrl+r` turns it on and off.

- Follows one person with look-ahead and damped camera motion.
- Detects locally with YOLO26n on CUDA, Apple MPS or CPU.
- Keeps playback running while the model loads. Seeking, speed, resizing and
  fullscreen work as usual.
- Restores your view settings when you stop following.

## Installation

You need [uv](https://docs.astral.sh/uv/) and mpv (0.41.x on Windows). From a clone
of this repository, on an ASCII-only path on Windows:

```text
uv run --no-project scripts/plugin.py setup
```

This installs the Python dependencies, downloads and verifies the model and adds
the extension to mpv's config directory. Run it again to repair or upgrade; it
keeps your edited settings. Stop following first and restart mpv afterwards. Use
`--config-dir "<path>"` for a custom mpv config, and keep the repository in place,
because mpv runs the backend from it.

On an NVIDIA GPU, add `--accel` for faster detection on Windows or Linux, and keep
passing it when you rerun setup. This also installs TensorRT and builds an engine
for your GPU, which the extension then uses automatically. After changing GPU or
TensorRT, delete `models/yolo26n.engine*` and rerun. The TensorRT build has only
been verified on Windows, but it should work on Linux too.

To uninstall, run `uv run --no-project scripts/plugin.py uninstall`.

## Usage

Restart mpv, open a local video and press `Ctrl+r`. Press it again to stop. A
badge at the lower left shows the status for five seconds after each change; if
it never appears, the extension isn't loaded.

Following suspends on zoom, unscaled video, unsupported rotation, remote URLs and
non-video playlist entries, and resumes once those are cleared. While it's on, the
extension controls panscan, horizontal alignment, cache, timing offset and
keepaspect-window, so stop following before changing those.

Settings are in `script-opts/reframe.conf` and apply the next time you enable
following. The main ones are `rate` (detections per second, 5 to 15), `autostart`
and `toggle_key`; [the example](mpv/reframe.conf.example) lists the rest.

## License

**GNU AGPL-3.0-only**, copyright (C) 2026 Roman (EatEmAll) and contributors. See
[LICENSE](LICENSE), [NOTICE](NOTICE) and
[third-party notices](THIRD_PARTY_NOTICES.md). The camera planning draws on
[Ido Pinto's ai-video-reframing](https://github.com/idopinto/ai-video-reframing),
with thanks. Report vulnerabilities privately through [SECURITY.md](SECURITY.md).
