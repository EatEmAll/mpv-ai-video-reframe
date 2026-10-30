"""Produce a sanitized summary without hiding failed or missing matrix runs."""

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath

REQUIRED_GATES = {
    "follow_median",
    "follow_p95",
    "containment",
    "static_stability",
    "anticipation",
    "runtime",
    "frame_health",
    "seek_recovery",
    "frame_step",
    "resize",
    "full_height",
    "no_overflow_no_pan",
    "fullscreen",
    "control_runtime",
    "process_vram",
    "crossing_identity",
}


def verified_gates(gates):
    return REQUIRED_GATES <= gates.keys() and all(v is True for v in gates.values())


def basename(value):
    return PureWindowsPath(value).name if "\\" in value else Path(value).name


def summarize(directory, repeat):
    if repeat < 2:
        raise ValueError("At least two repetitions are required")
    commands = json.loads((directory / "commands.json").read_text(encoding="utf-8"))
    expected = {
        f"{device}-{variant}-{index}": speeds
        for device, variant, speeds in (
            ("cuda-pt", "1080p30", [1, 4]),
            ("cuda-pt", "2160p60", [1, 2]),
            ("cuda-engine", "1080p30", [1, 4]),
            ("cuda-engine", "2160p60", [1, 2]),
            ("cpu-pt", "1080p30", [1]),
        )
        for index in range(1, repeat + 1)
    }
    rows, sources = [], {}
    missing = []
    seen = []
    for record in commands:
        command = record["command"]
        name = basename(command[command.index("--output") + 1])
        seen.append(name)
        run = directory / name
        if not (run / "summary.json").is_file():
            missing.append(name)
            continue
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        source = manifest["source"]
        fingerprint = hashlib.sha256(
            json.dumps(source, sort_keys=True).encode()
        ).hexdigest()
        sources[fingerprint] = source
        results = json.loads((run / "summary.json").read_text(encoding="utf-8"))
        if (
            name not in expected
            or sorted(r["speed"] for r in results) != expected[name]
        ):
            missing.append(name + ": unexpected speed coverage")
        for result in results:
            rows.append(
                {
                    "run": name,
                    "source_fingerprint": fingerprint,
                    "exit": record["exit"],
                    "model": basename(result["model"])
                    if result.get("model")
                    else "automatic",
                    "cuda_available": manifest["cuda"],
                    "speed": result["speed"],
                    "mpv": manifest["mpv"],
                    "mpv_sha256": manifest.get("mpv_sha256"),
                    "packages": manifest["packages"],
                    "gpu": manifest["gpu"],
                    "fixture_hashes": manifest["fixtures"],
                    "baseline_last": result["baseline_last"],
                    "baseline_drops": result["baseline_drops"],
                    "active_drops": result["active_drops"],
                    "additional_frame_drop_fraction": result[
                        "additional_frame_drop_fraction"
                    ],
                    "median_error": result["median_error"],
                    "p95_error": result["p95_error"],
                    "containment": result["containment"],
                    "static_align_total_variation": result[
                        "static_align_total_variation"
                    ],
                    "onset_lags": result["onset_lags"],
                    "identity": result["identity"],
                    "seek_p95_seconds": result.get("controls", {}).get(
                        "seek_recovery_p95_seconds"
                    ),
                    "peak_dedicated_bytes": result["resources"][
                        "peak_process_dedicated_bytes"
                    ],
                    "gates": result["gates"],
                    "configuration_matches": manifest["cuda"]
                    == name.startswith("cuda-")
                    and str(result.get("model", "")).endswith(
                        ".engine" if name.startswith("cuda-engine") else ".pt"
                    ),
                }
            )
    complete = (
        len(commands) == len(expected)
        and set(seen) == set(expected)
        and len(set(seen)) == len(seen)
        and not missing
        and len(rows) == 9 * repeat
    )
    return {
        "expected_runs": 5 * repeat,
        "completed_commands": len(commands),
        "complete": complete,
        "missing": missing,
        "sources": sources,
        "uniform_source": len(sources) == 1,
        "rows": rows,
        "all_required_pass": complete
        and len(sources) == 1
        and all(
            r["exit"] == 0 and r["configuration_matches"] and verified_gates(r["gates"])
            for r in rows
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.directory, args.repeat)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2, allow_nan=False)
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "all_required_pass": result["all_required_pass"],
                "rows": len(result["rows"]),
            }
        )
    )
    raise SystemExit(0 if result["all_required_pass"] else 1)


if __name__ == "__main__":
    main()
