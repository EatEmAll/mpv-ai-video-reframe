"""Setup and managed companion entry points for the mpv Lua extension."""

import argparse
import json
import subprocess
import sys

from .runtime import find_mpv


def doctor(mpv_path=None):
    from .models import configure_inference, prepare_weights

    configure_inference()
    weights = prepare_weights()
    import av
    import python_mpv_jsonipc  # noqa: F401
    import torch
    from ultralytics import YOLO

    mpv = find_mpv(mpv_path)
    version = subprocess.run(
        [mpv, "--version"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()[0]
    model = YOLO(str(weights))
    report = {
        "mpv": version,
        "mpv_path": mpv,
        "torch": torch.__version__,
        "device": "cuda"
        if torch.cuda.is_available()
        else ("mps" if torch.backends.mps.is_available() else "cpu"),
        "cuda": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "capability": torch.cuda.get_device_capability(0)
        if torch.cuda.is_available()
        else None,
        "av": av.__version__,
        "codecs": sorted({"h264", "hevc"} & av.codecs_available),
        "model": str(model.ckpt_path),
        "class_0": model.names[0],
    }
    print(json.dumps(report, indent=2))
    return 0 if report["class_0"] == "person" and len(report["codecs"]) == 2 else 1


def main():
    parser = argparse.ArgumentParser(description="AI person-following mpv companion")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "doctor", help="Check mpv, CUDA, codecs and detector weights"
    )
    check.add_argument("--mpv-path")
    cmd = sub.add_parser(
        "attach", help="Managed companion launched by the Lua extension"
    )
    cmd.add_argument("--pipe", required=True)
    cmd.add_argument("--player-pid", type=int, required=True)
    cmd.add_argument("--managed", action="store_true")
    for option in ("session-id", "lua-client", "backend-dir", "output-dir"):
        cmd.add_argument("--" + option)
    cmd.add_argument("--rate", type=int, choices=range(5, 16), default=10)
    cmd.add_argument("--debug-osd", action="store_true")
    cmd.add_argument(
        "--model",
        help="Explicit model path; otherwise prefer a compatible verified TensorRT engine",
    )
    cmd.add_argument(
        "--seconds",
        type=float,
        help="Stop sidecar after this many seconds (smoke tests)",
    )
    args = parser.parse_args()
    if args.command == "attach":
        context = (args.session_id, args.lua_client, args.backend_dir, args.output_dir)
        if args.managed != all(context) or (not args.managed and any(context)):
            parser.error(
                "--managed requires --session-id, --lua-client, --backend-dir and --output-dir"
            )
        if args.managed:
            from .managed import BackendBusyError, run_managed

            try:
                run_managed(args)
            except BackendBusyError as error:
                print(str(error), file=sys.stderr)
                raise SystemExit(3) from None
            except (ImportError, FileNotFoundError) as error:
                print(str(error), file=sys.stderr)
                raise SystemExit(4) from None
            except ValueError as error:
                print(str(error), file=sys.stderr)
                raise SystemExit(2) from None
            except Exception as error:
                print(str(error), file=sys.stderr)
                raise SystemExit(5) from None
            return
    if args.command == "doctor":
        raise SystemExit(doctor(args.mpv_path))
    parser.error("attach requires --managed; enable the extension from mpv")


if __name__ == "__main__":
    main()
