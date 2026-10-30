"""Independent control/seek recovery scenario used by the quantitative harness."""

import bisect
import json
import random
import threading
import time

import numpy as np
from verification_backend import player_options, run
from verification_player import player_for

from reframe.evaluation import truth_at


def control_quality(clip, truth, directory, detector):
    stop = threading.Event()
    errors = []
    cases = []
    sizes = []
    fullscreen = False
    index_before, index_after = 0, 0
    with player_for(clip, pause=True, **player_options(detector)) as (player, name):
        player.command("set_property", "geometry", "608x1080")

        def companion():
            try:
                run(name, detector, stop_event=stop, output_dir=directory / "sidecar")
            except Exception as exc:
                errors.append(repr(exc))

        thread = threading.Thread(target=companion, daemon=True)
        thread.start()

        def get(prop):
            return player.command("get_property", prop)

        def recover(target, relative=None):
            speed = get("speed")
            start = time.monotonic()
            wall_start = time.time()
            player.command(
                "seek",
                relative if relative is not None else target,
                "relative+exact" if relative is not None else "absolute+exact",
            )
            command_seconds = time.monotonic() - start
            recovery = None
            last_error = None
            while time.monotonic() - start < 1.5:
                t = get("time-pos")
                dims = get("osd-dimensions")
                elapsed = time.monotonic() - start
                box = truth_at(truth, t)["primary"]
                if (
                    box
                    and not get("seeking")
                    and abs(t - target) <= speed * elapsed + 2 / truth["fps"]
                ):
                    scaled = dims["w"] - dims["ml"] - dims["mr"]
                    gt = (box[0] + box[2]) / 2
                    gt = min(
                        1 - dims["w"] / (2 * scaled), max(dims["w"] / (2 * scaled), gt)
                    )
                    actual = (dims["w"] / 2 - dims["ml"]) / scaled
                    last_error = abs(gt - actual)
                    if last_error < 0.05:
                        recovery = elapsed
                        break
                time.sleep(1 / 60)
            cases.append(
                {
                    "requested": target,
                    "wall_start": wall_start,
                    "command_seconds": command_seconds,
                    "relative": relative,
                    "speed": speed,
                    "informational": truth["width"] >= 3840 and speed == 4,
                    "recovery_seconds": recovery,
                    "last_error": last_error,
                }
            )

        try:
            if hasattr(detector, "ready") and not detector.ready.wait(130):
                raise RuntimeError("Lua control backend did not become ready")
            time.sleep(2)
            player.command("set_property", "pause", False)
            time.sleep(0.5)
            target = get("time-pos") + 10
            recover(target, relative=10)
            target = max(0, get("time-pos") - 15)
            recover(target, relative=-15)
            rng = random.Random(7341)
            targets = [
                rng.uniform(2, 24),
                rng.uniform(33, 43),
                rng.uniform(2, 24),
                rng.uniform(51, 54),
                rng.uniform(56, 58),
            ]
            for i, target in enumerate(targets):
                player.command("set_property", "speed", [1, 2, 4, 0.5][i % 4])
                recover(target)
            player.command("set_property", "pause", True)
            time.sleep(0.15)
            before = get("time-pos")
            for _ in range(10):
                old = get("time-pos")
                player.command("frame-step")
                deadline = time.monotonic() + 1
                while get("time-pos") == old and time.monotonic() < deadline:
                    time.sleep(0.01)
            after = get("time-pos")
            times = [f["t"] for f in truth["frames"]]
            index_before = bisect.bisect_right(times, before + 0.001) - 1
            index_after = bisect.bisect_right(times, after + 0.001) - 1
            sizes = []
            for width, height in [(608, 1080), (1080, 1080), (1920, 1080)]:
                player.command("set_property", "geometry", f"{width}x{height}")
                time.sleep(0.3)
                dims = get("osd-dimensions")
                sizes.append(
                    {
                        "requested": [width, height],
                        "actual": [dims["w"], dims["h"]],
                        "align": get("video-align-x"),
                        "panscan": get("panscan"),
                        "mt": dims["mt"],
                        "mb": dims["mb"],
                    }
                )
            player.command("set_property", "fullscreen", True)
            time.sleep(0.3)
            fullscreen = get("fullscreen")
            player.command("set_property", "fullscreen", False)
        except Exception as error:
            errors.append(repr(error))
        finally:
            stop.set()
            thread.join(timeout=15)
            if thread.is_alive():
                errors.append("Control sidecar did not stop")
    # The plan makes all 4K/4x results informational, including its seek case.
    # Retain that observation but do not gate the required 4K/1x and 2x runs on it.
    recoveries = [
        case["recovery_seconds"] for case in cases if not case["informational"]
    ]
    p95 = (
        float(np.percentile(recoveries, 95))
        if recoveries and all(v is not None for v in recoveries)
        else None
    )
    result = {
        "seeks": cases,
        "seek_recovery_p95_seconds": p95,
        "frame_steps": index_after - index_before,
        "sizes": sizes,
        "fullscreen": fullscreen,
        "errors": errors,
        "gates": {
            "seek_recovery": p95 is not None and p95 <= 1,
            "frame_step": index_after - index_before == 10,
            "resize": len(sizes) == 3
            and all(r["requested"] == r["actual"] for r in sizes),
            "full_height": len(sizes) == 3
            and all(r["panscan"] == 1 and r["mt"] <= 0 and r["mb"] <= 0 for r in sizes),
            "no_overflow_no_pan": bool(sizes) and abs(sizes[-1]["align"]) <= 0.002,
            "fullscreen": bool(fullscreen),
            "control_runtime": not errors,
        },
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "controls.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result
