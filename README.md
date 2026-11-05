# mpv Reframe

An mpv extension that keeps a person in frame. It fills the window height with
the video and pans the crop sideways to follow them. It runs in your normal mpv,
and `Ctrl+r` turns it on and off.

- Follows one person with look-ahead and damped camera motion, or switches to
  group mode to fit everyone the crop can hold.
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

Press `Ctrl+g` to switch between two framing modes. The switch happens during
playback, and the camera pans to the new framing:

- **Follow** (default) locks onto one person: the largest, most confident and
  most central one when following starts.
- **Group** pans to fit as many people as possible. A person counts when their
  detection confidence is at least `group_conf_floor` (default 0.5). They are
  framed once at least `group_coverage_floor` (default 70%) of their box width
  is inside the crop. The crop keeps the full window height and never zooms
  out, so people too far apart to fit together are left out. Confidence
  decides who is framed: the most confident person always is, even when their
  box is wider than the crop, and a more confident person beats any number of
  weaker ones. To keep a more confident person, coverage may drop below the
  floor, down to 50% for most people (`group_relaxed_floor`) and 20% for the
  two most confident (`group_pair_floor`, so a tight portrait crop sits on the
  seam between two close-up people instead of on one), and the crop is placed
  to keep the least visible person as visible as possible. A new person counts
  after `group_join` seconds (0.3) and a missing one is held at their last
  box for `group_leave` seconds (0.7). The camera stays with its current group
  until one with clearly more confident people (by more than 0.1) or more
  people has been available for `group_switch` seconds (0.6), or until the
  current group no longer fits. Resizing the window re-decides the group
  straight away, since a wider crop can hold more people.

Follow mode keeps tracking its person while group mode is active, so switching
back resumes on the same person. The `mode` setting picks the mode used at
startup. Pressing `Ctrl+g` while following is off changes the mode for the next
start. For `input.conf`, use `script-message reframe-mode follow|group|toggle`.

Press `Ctrl+d` to show or hide the debug overlay: each detected person's box
with its confidence (green when framed, orange when left out, and a cyan outline
around the group in group mode), plus a status line. It works during playback;
`debug_osd` sets the starting state and `debug_key` the key. For `input.conf`,
use `script-message reframe-debug on|off|toggle`.

Following suspends on zoom, unscaled video, unsupported rotation, remote URLs and
non-video playlist entries, and resumes once those are cleared. While it's on, the
extension controls panscan, horizontal alignment, cache, timing offset and
keepaspect-window, so stop following before changing those.

Settings are in `script-opts/reframe.conf` and apply the next time you enable
following. The main ones are `rate` (detections per second, 5 to 15), `autostart`,
`toggle_key`, `mode`, `mode_key`, `debug_osd` and `debug_key`; the group-mode
thresholds above are settings too, and [the example](mpv/reframe.conf.example) lists
the rest.

Cold starts allow 30 seconds for the backend handshake and 300 seconds for model
loading. Set `startup_timeout` or `loading_timeout` (positive seconds) if your
machine needs longer. Loading still requires heartbeats; a backend that stops
sending them is stopped after five seconds. Timeout messages distinguish these
three cases and include the session log directory.

## License

**GNU AGPL-3.0-only**, copyright (C) 2026 Roman (EatEmAll) and contributors. See
[LICENSE](LICENSE), [NOTICE](NOTICE) and
[third-party notices](THIRD_PARTY_NOTICES.md). The camera planning draws on
[Ido Pinto's ai-video-reframing](https://github.com/idopinto/ai-video-reframing),
with thanks. Report vulnerabilities privately through [SECURITY.md](SECURITY.md).
