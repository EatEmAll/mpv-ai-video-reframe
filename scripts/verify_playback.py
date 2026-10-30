"""Ground-truth renderer evaluation. Default runs complete speed coverage per clip.

The measuring IPC client is independent of the sidecar. Sidecar telemetry is used
only for ID continuity, not for crop geometry or follow error.
"""

import argparse
import csv
import json
import math
import threading
import time
from pathlib import Path

from control_quality import control_quality
from verification_backend import LuaBackend, player_options, run
from verification_manifest import source_manifest, write_manifest
from verification_player import player_for
from verification_resources import ResourceMonitor

from reframe.evaluation import score, score_identity


def observe_clip(clip, speed, directory, detector=None):
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    errors = []
    stop = threading.Event()
    thread = None
    # GPU diagnostics can perturb rendering. Apply identical instrumentation
    # to baseline and companion playback so its overhead is not attributed to
    # the companion alone.
    resource_monitor = ResourceMonitor(directory / "resources.jsonl")
    cache = {}
    lock = threading.Lock()

    def observed(name, value):
        with lock:
            cache[name] = value

    with player_for(clip, pause=True, **player_options(detector)) as (player, name):
        player.command("set_property", "geometry", "608x1080")
        owned = (
            "panscan",
            "video-align-x",
            "cache",
            "keepaspect-window",
            "video-timing-offset",
        )
        original = {p: player.command("get_property", p) for p in owned}
        for prop in [
            "time-pos",
            "osd-dimensions",
            "video-align-x",
            "frame-drop-count",
            "vo-delayed-frame-count",
            "eof-reached",
            "video-timing-offset",
            "hwdec-current",
            "display-fps",
        ]:
            player.bind_property_observer(prop, observed)
        if detector:
            detector.reset()

            def companion():
                try:
                    run(
                        name,
                        detector,
                        stop_event=stop,
                        output_dir=directory / "sidecar",
                    )
                except Exception as exc:
                    errors.append(repr(exc))
                    stop.set()

            thread = threading.Thread(target=companion, daemon=True)
            thread.start()
        if isinstance(detector, LuaBackend):
            deadline = time.monotonic() + 130
            while detector.pid is None and not errors and time.monotonic() < deadline:
                time.sleep(0.05)
            if detector.pid is None:
                errors.append("Lua backend startup failed")
            resource_monitor.pid = detector.pid
        resource_monitor.start()
        try:
            time.sleep(2.0)  # Prime look-ahead before media time starts.
            player.command("set_property", "speed", speed)
            player.command("set_property", "pause", False)
            duration = player.command("get_property", "duration")
            deadline = time.monotonic() + duration / speed + 20
            next_sample = time.monotonic()
            while time.monotonic() < deadline and not errors:
                with lock:
                    data = cache.copy()
                if data.get("eof-reached"):
                    break
                dims = data.get("osd-dimensions")
                t = data.get("time-pos")
                if t is not None and dims:
                    rows.append(
                        {
                            "wall": time.time(),
                            "time_pos": t,
                            "speed": speed,
                            "w": dims["w"],
                            "h": dims["h"],
                            "ml": dims["ml"],
                            "mr": dims["mr"],
                            "align": data.get("video-align-x", 0),
                            "frame_drops": data.get("frame-drop-count"),
                            "delayed_frames": data.get("vo-delayed-frame-count"),
                            "video_timing_offset": data.get("video-timing-offset"),
                            "hwdec": data.get("hwdec-current"),
                            "display_fps": data.get("display-fps"),
                        }
                    )
                next_sample += 1 / 30
                time.sleep(max(0, next_sample - time.monotonic()))
            else:
                if not errors:
                    errors.append("Timed out before EOF")
        finally:
            # Stop sampling while the measured process still exists. Sampling
            # after normal teardown falsely reports NoSuchProcess as a gap.
            resource_monitor.close()
            stop.set()
            if thread:
                thread.join(timeout=15)
                if thread.is_alive():
                    errors.append("Sidecar failed to stop")
            restored = {p: player.command("get_property", p) for p in owned}
            if restored != original:
                errors.append("Owned properties not restored: " + repr(restored))
    resources = (
        {
            "peak_process_dedicated_bytes": resource_monitor.peak_bytes,
            "error": resource_monitor.error,
        }
        if resource_monitor
        else None
    )
    if rows:
        with (directory / "observations.csv").open(
            "w", newline="", encoding="utf-8"
        ) as out:
            writer = csv.DictWriter(out, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (directory / "errors.json").write_text(json.dumps(errors), encoding="utf-8")
    return rows, errors, resources


def report(directory, result, rows, truth):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    if rows:
        with (directory / "metrics.csv").open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = [
        "# Ground-truth E2E result",
        "",
        f"Clip: `{truth['variant']}`; speed: {result['speed']}x.",
        "",
        "| Gate | Status |",
        "|---|---|",
    ]
    for name, value in result["gates"].items():
        lines.append(
            f"| {name} | {'PASS' if value is True else 'FAIL' if value is False else 'NOT MEASURED'} |"
        )
    lines += [
        "",
        "```json",
        json.dumps({k: v for k, v in result.items() if k != "gates"}, indent=2),
        "```",
        "",
    ]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    parser.add_argument(
        "--baseline-last",
        action="store_true",
        help="Alternate paired ordering on repeated runs",
    )
    parser.add_argument("--model", help="Explicit detector weights or TensorRT engine")
    parser.add_argument(
        "--render-offset",
        type=float,
        help="Experiment only: override active player's render queue offset (seconds)",
    )
    parser.add_argument(
        "--output", type=Path, help="Exact report directory (must not exist)"
    )
    parser.add_argument("--speeds", type=float, nargs="+", default=[1, 2, 4, 0.5])
    parser.add_argument(
        "--skip-controls",
        action="store_true",
        help="Exploration only; control gates stay unmeasured",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Exploration only; health gate stays unmeasured",
    )
    parser.add_argument("--integration", choices=("lua",), default="lua")
    args = parser.parse_args()
    if any(not math.isfinite(v) or v <= 0 for v in args.speeds):
        parser.error("Speeds must be finite and positive")
    if args.render_offset is not None and (
        not math.isfinite(args.render_offset) or not 0 <= args.render_offset <= 0.25
    ):
        parser.error(
            "Experimental render offset must be finite and between 0 and 0.25 s"
        )
    truth = json.loads(args.clip.with_suffix(".gt.json").read_text(encoding="utf-8"))
    output = args.output or Path("runs") / (time.strftime("%Y%m%d-%H%M%S") + "-e2e")
    output.mkdir(parents=True, exist_ok=False)
    initial_source = write_manifest(output, args.clip, args.model)
    detector = LuaBackend(args.model, render_offset=args.render_offset)
    controls = (
        None
        if args.skip_controls
        else control_quality(args.clip, truth, output / "controls", detector)
    )
    summaries = []
    for speed in args.speeds:
        directory = output / f"{speed:g}x"
        baseline = []
        baseline_errors = []
        if not args.skip_baseline and not args.baseline_last:
            baseline, baseline_errors, _ = observe_clip(
                args.clip, speed, directory / "baseline"
            )
        rows, errors, resources = observe_clip(args.clip, speed, directory, detector)
        if not args.skip_baseline and args.baseline_last:
            baseline, baseline_errors, _ = observe_clip(
                args.clip, speed, directory / "baseline"
            )
        result, scored = score(rows, truth, speed)
        result.update(
            speed=speed,
            integration=args.integration,
            model=args.model,
            render_offset=args.render_offset,
            baseline_last=args.baseline_last,
            errors=errors + baseline_errors,
        )
        result["informational"] = truth["width"] >= 3840 and speed == 4
        result["resources"] = resources
        result["gates"]["runtime"] = not result["errors"]

        def drops(items):
            if not items or any(
                items[-1][key] is None for key in ["frame_drops", "delayed_frames"]
            ):
                return None
            return sum(items[-1][key] for key in ["frame_drops", "delayed_frames"])

        base_drops, actual_drops = drops(baseline), drops(rows)
        result["baseline_drops"] = base_drops
        result["active_drops"] = actual_drops
        result["additional_frame_drop_fraction"] = (
            (actual_drops - base_drops) / len(truth["frames"])
            if base_drops is not None and actual_drops is not None
            else None
        )
        result["gates"]["frame_health"] = (
            None
            if result["additional_frame_drop_fraction"] is None
            else result["additional_frame_drop_fraction"] <= 0.005
        )
        # These require dedicated control/resource/identity evidence, not crop-only metrics.
        if controls is None:
            result["gates"]["seek_recovery"] = None
        else:
            result["controls"] = controls
            result["gates"].update(controls["gates"])
        peak = resources["peak_process_dedicated_bytes"]
        result["gates"]["process_vram"] = (
            None if peak is None or resources["error"] else peak <= 2 * 1024**3
        )
        telemetry = [
            json.loads(line)
            for line in (directory / "sidecar/telemetry.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        result["identity"] = score_identity(telemetry, truth)
        result["gates"]["crossing_identity"] = result["identity"]["pass"]
        report(directory, result, scored, truth)
        summaries.append(result)
        print(json.dumps({"directory": str(directory.resolve()), **result}), flush=True)
    (output / "summary.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    if source_manifest(Path(__file__).resolve().parents[1]) != initial_source:
        raise RuntimeError(
            "Source changed during measurement; results require integration repeat"
        )
    if not summaries or not all(
        all(v is True for v in r["gates"].values()) for r in summaries
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
