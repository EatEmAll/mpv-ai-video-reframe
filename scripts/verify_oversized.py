"""Verify full-height crop-fill despite an oversized tracked box and resizing."""

import argparse
import json
import time
from pathlib import Path

from verification_player import player_for

from reframe.controller import run
from reframe.timeline import Det


class WideSubject:
    def reset(self):
        pass

    def detect(self, frame):
        return [Det(1, (0.3, 0.1, 0.95, 0.95), 0.99)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    args = parser.parse_args()
    directory = Path("runs") / (time.strftime("%Y%m%d-%H%M%S") + "-wide-box")
    cases = []
    with player_for(args.clip, pause=True) as (player, name):
        for geometry in ("608x1080", "1080x1080", "1920x1080"):
            player.command("set_property", "geometry", geometry)
            output = directory / geometry
            run(name, WideSubject(), seconds=2, output_dir=output)
            rows = [
                json.loads(line)
                for line in (output / "telemetry.jsonl").read_text().splitlines()
            ]
            cases.append(
                {
                    "geometry": geometry,
                    "samples": len(rows),
                    "pass": bool(rows)
                    and all(
                        r["panscan"] == 1 and r["mt"] <= 0 and r["mb"] <= 0
                        for r in rows
                    ),
                }
            )
    result = {"cases": cases, "pass": all(case["pass"] for case in cases)}
    (directory / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"directory": str(directory.resolve()), **result}, indent=2))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
