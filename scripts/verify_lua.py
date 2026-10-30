"""Real-player plugin checks: idle, fill, suspension and forced-crash recovery."""

import argparse
import json
import os
import signal
import time
from pathlib import Path

from verification_backend import LuaBackend, player_options
from verification_player import player_for


def wait(player, expected, timeout=130):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            state = player.command("get_property", "user-data/reframe")
        except Exception:
            state = {}
        if state.get("state") in expected:
            return state
        time.sleep(0.05)
    raise TimeoutError(f"Expected {expected}, last status {state}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = root / "runs" / (time.strftime("%Y%m%d-%H%M%S") + "-lua-lifecycle")
    report.mkdir(exist_ok=False)
    checks = {}
    with player_for(args.clip, pause=True, **player_options(LuaBackend())) as (
        player,
        _,
    ):
        player.command("set_property", "panscan", 0.2)
        baseline = {
            name: player.command("get_property", name)
            for name in (
                "panscan",
                "video-align-x",
                "cache",
                "keepaspect-window",
                "video-timing-offset",
            )
        }
        player.command("script-message-to", "reframe", "reframe-enable")
        active = wait(player, {"active", "error"})
        assert active["state"] == "active", active
        cases = []
        for geometry in ("608x1080", "1080x1080", "1920x1080"):
            player.command("set_property", "geometry", geometry)
            time.sleep(0.5)
            dims = player.command("get_property", "osd-dimensions")
            assert player.command("get_property", "panscan") == 1
            assert dims["mt"] <= 0 and dims["mb"] <= 0, dims
            cases.append(dict(geometry=geometry, dimensions=dims))
        checks["full_height"] = cases
        player.command("set_property", "video-zoom", 0.1)
        wait(player, {"suspended"})
        assert player.command("get_property", "panscan") == baseline["panscan"]
        player.command("set_property", "video-zoom", 0)
        wait(player, {"active"})
        checks["suspend_resume"] = True
        # Camera translations require horizontal overflow. The fill checks end
        # in landscape, where correct behavior is to send no pan updates.
        player.command("set_property", "geometry", "608x1080")
        time.sleep(0.5)
        cadence = []
        for speed in (0.5, 1, 2, 4):
            player.command("set_property", "speed", speed)
            player.command("seek", 10, "absolute+exact")
            time.sleep(0.5)
            player.command("set_property", "pause", False)
            started = time.time()
            time.sleep(3)
            ended = time.time()
            player.command("set_property", "pause", True)
            cadence.append(
                {
                    "speed": speed,
                    "start": started,
                    "end": ended,
                    "fps": player.command("get_property", "container-fps"),
                    "display_fps": player.command("get_property", "display-fps"),
                }
            )
        lifecycle = Path(active["log_dir"]) / "lifecycle.jsonl"
        hello = next(
            r
            for r in map(json.loads, lifecycle.read_text().splitlines())
            if r["type"] == "hello"
        )
        os.kill(hello["pid"], signal.SIGTERM)
        wait(player, {"error"}, 15)
        rows = [
            json.loads(line)
            for line in (Path(active["log_dir"]) / "telemetry.jsonl")
            .read_text()
            .splitlines()
        ]
        for case in cadence:
            observed = [
                r
                for r in rows
                if case["start"] + 0.2 < r["wall"] < case["end"]
                and r["speed"] == case["speed"]
            ]
            sent = [r["wall"] for r in observed if r["sent"]]
            gaps = [b - a for a, b in zip(sent, sent[1:])]
            ceiling = min(case["display_fps"] or 60, case["fps"] * case["speed"])
            case.update(
                samples=len(observed),
                minimum_gap_ms=min(gaps) * 1000 if gaps else None,
                ceilings=sorted({r["update_ceiling_hz"] for r in observed}),
            )
            assert observed and all(
                abs(r["update_ceiling_hz"] - ceiling) < 0.01 for r in observed
            ), case
            assert gaps and min(gaps) >= 1 / ceiling - 0.001, case
        checks["cadence"] = cadence
        restored = {name: player.command("get_property", name) for name in baseline}
        assert restored == baseline, (restored, baseline)
        checks["crash_restores_view"] = True
        player.command("stop")
        player.command("script-message-to", "reframe", "reframe-enable")
        wait(player, {"waiting"}, 15)
        checks["idle_enable"] = True
        player.command("script-message-to", "reframe", "reframe-stop")
        wait(player, {"disabled"}, 15)
        checks["idle_stop"] = True
    (report / "result.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps({"directory": str(report), "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
