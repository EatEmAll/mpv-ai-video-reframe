"""Repeat paired native-player measurements sequentially; retain every failure.

Run in a prepared backend, never alongside another GPU benchmark/export.
The output directory must not exist. No user configuration is loaded.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROFILES = (
    ("cuda-pt", "models/yolo26n.pt", "1080p30", [1, 4]),
    ("cuda-pt", "models/yolo26n.pt", "2160p60", [1, 2]),
    ("cuda-engine", "models/yolo26n.engine", "1080p30", [1, 4]),
    ("cuda-engine", "models/yolo26n.engine", "2160p60", [1, 2]),
    ("cpu-pt", "models/yolo26n.pt", "1080p30", [1]),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--render-offset",
        type=float,
        help="Experiment only: override active render offset",
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        choices=("cuda-pt", "cuda-engine", "cpu-pt"),
        default=["cuda-pt", "cuda-engine", "cpu-pt"],
        help="Subset to run; summaries must name the same subset",
    )
    args = parser.parse_args()
    if args.repeat < 2:
        parser.error("At least two repetitions required")
    args.output.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).with_name("verify_playback.py")
    records = []
    for device, model, variant, speeds in PROFILES:
        if device not in args.devices:
            continue
        for repeat in range(args.repeat):
            output = args.output / f"{device}-{variant}-{repeat + 1}"
            command = [
                sys.executable,
                str(script),
                str(args.samples / f"synth_walk_{variant}.mp4"),
                "--model",
                model,
                "--speeds",
                *map(str, speeds),
                "--output",
                str(output),
            ]
            if repeat % 2:
                command += ["--baseline-last"]
            if args.render_offset is not None:
                command += ["--render-offset", str(args.render_offset)]
            env = os.environ.copy()
            if device.startswith("cpu"):
                env["CUDA_VISIBLE_DEVICES"] = "-1"
            with output.with_suffix(".log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    command, env=env, stdout=log, stderr=subprocess.STDOUT
                )
            records.append({"command": command, "exit": result.returncode})
            (args.output / "commands.json").write_text(
                json.dumps(records, indent=2), encoding="utf-8"
            )
            print(f"{output.name}: exit {result.returncode}", flush=True)
    raise SystemExit(0 if all(r["exit"] == 0 for r in records) else 1)


if __name__ == "__main__":
    main()
