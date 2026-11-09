"""Setup and managed companion entry points for the mpv Lua extension."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .runtime import find_mpv, validate_backend_runtime


def doctor(mpv_path=None):
    validate_backend_runtime(Path.cwd())
    from .models import configure_inference, load_yolo, prepare_weights

    configure_inference()
    weights = prepare_weights()
    import av
    import python_mpv_jsonipc  # noqa: F401
    import torch

    mpv = find_mpv(mpv_path)
    version = subprocess.run(
        [mpv, "--version"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()[0]
    model = load_yolo(weights)
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


def _fraction(low, high, inclusive_low):
    def parse(text):
        try:
            value = float(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text}") from None
        above = value >= low if inclusive_low else value > low
        if not (above and value <= high):
            bracket = "[" if inclusive_low else "("
            raise argparse.ArgumentTypeError(
                f"must be within {bracket}{low}, {high}]: {text}"
            )
        return value

    return parse


def _seconds(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text}") from None
    if not 0 <= value <= 30:
        raise argparse.ArgumentTypeError(f"must be within [0, 30] seconds: {text}")
    return value


def _idle_seconds(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text}") from None
    if not 0 <= value <= 86400:
        raise argparse.ArgumentTypeError(f"must be within [0, 86400] seconds: {text}")
    return int(value)


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
        "--mode",
        choices=("follow", "group"),
        default="follow",
        help="Initial framing mode; the Lua mode key switches it at runtime",
    )
    cmd.add_argument(
        "--group-conf-floor",
        type=_fraction(0, 1, inclusive_low=True),
        default=0.5,
        help="Group mode: minimum detection confidence, 0-1",
    )
    cmd.add_argument(
        "--group-coverage-floor",
        type=_fraction(0, 1, inclusive_low=False),
        default=0.7,
        help="Group mode: minimum visible fraction of a framed person's box, >0-1",
    )
    cmd.add_argument(
        "--group-relaxed-floor",
        type=_fraction(0, 1, inclusive_low=False),
        default=0.5,
        help="Group mode: lowest visible fraction accepted to keep a more "
        "confident person (capped at the coverage floor), >0-1",
    )
    cmd.add_argument(
        "--group-pair-floor",
        type=_fraction(0, 1, inclusive_low=False),
        default=0.2,
        help="Group mode: the same for the two most confident people "
        "(capped at the relaxed floor), >0-1",
    )
    cmd.add_argument(
        "--group-join",
        type=_seconds,
        default=0.3,
        help="Group mode: seconds a new person must be seen before they count",
    )
    cmd.add_argument(
        "--group-leave",
        type=_seconds,
        default=0.7,
        help="Group mode: seconds a missing person is held at their last box",
    )
    cmd.add_argument(
        "--group-switch",
        type=_seconds,
        default=0.6,
        help="Group mode: seconds a clearly better group must be available "
        "before the camera moves to it",
    )
    cmd.add_argument(
        "--model",
        help="Explicit model path; otherwise prefer a compatible verified TensorRT engine",
    )
    cmd.add_argument(
        "--detector",
        choices=("shared", "local"),
        default="shared",
        help="shared: one background service keeps the model for every player; "
        "local: load a private copy in this process",
    )
    cmd.add_argument(
        "--detector-idle",
        type=_idle_seconds,
        default=600,
        help="Shared detector: seconds the service stays loaded with no player "
        "(0 keeps it running)",
    )
    cmd.add_argument(
        "--seconds",
        type=float,
        help="Stop sidecar after this many seconds (smoke tests)",
    )
    service = sub.add_parser(
        "serve", help="Shared detector service; players start it when needed"
    )
    service.add_argument("--backend-dir", required=True)
    service.add_argument("--model")
    service.add_argument("--idle-timeout", type=_idle_seconds, default=600)
    args = parser.parse_args()
    if args.command == "serve":
        try:
            validate_backend_runtime(args.backend_dir)
        except ValueError as error:
            parser.error(str(error))
        from .security import trusted_path
        from .service import serve

        os.chdir(trusted_path(args.backend_dir))
        sys.stdout.flush()
        code = serve(args.backend_dir, args.model, args.idle_timeout)
        # Daemon threads may be blocked in a pipe accept; do not wait for them.
        sys.stderr.flush()
        os._exit(code)
    if args.command == "attach":
        context = (args.session_id, args.lua_client, args.backend_dir, args.output_dir)
        if args.managed != all(context) or (not args.managed and any(context)):
            parser.error(
                "--managed requires --session-id, --lua-client, --backend-dir and --output-dir"
            )
        if args.managed:
            try:
                validate_backend_runtime(args.backend_dir)
            except ValueError as error:
                parser.error(str(error))
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
        try:
            validate_backend_runtime(Path.cwd())
        except ValueError as error:
            parser.error(str(error))
        raise SystemExit(doctor(args.mpv_path))
    parser.error("attach requires --managed; enable the extension from mpv")


if __name__ == "__main__":
    main()
