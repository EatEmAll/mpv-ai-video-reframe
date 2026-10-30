"""Build an optional device-specific FP16 TensorRT engine after installing accel.

Run on the deployment GPU. The .engine file is ignored and must not be treated as
portable across TensorRT versions or GPU architectures.
"""

import argparse
import os
from pathlib import Path


def main():
    os.environ["YOLO_AUTOINSTALL"] = "false"
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=Path("models/yolo26n.pt"))
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Record a just-built local engine without rebuilding it",
    )
    args = parser.parse_args()
    if not args.model.is_file():
        parser.error("Model weights must already exist; run reframe doctor first")
    from reframe.models import (
        configure_inference,
        load_yolo,
        record_engine,
        secure_tensorrt,
        verify_weights,
    )
    from reframe.security import SecurityError

    configure_inference()
    verify_weights(args.model)
    import tensorrt

    if not secure_tensorrt(tensorrt.__version__):
        raise SecurityError(
            "TensorRT >=11.2 required before export; sync the accel lock"
        )

    engine = (
        args.model.with_suffix(".engine")
        if args.manifest_only
        else load_yolo(args.model).export(
            format="engine",
            device=0,
            imgsz=640,
            quantize=16,
            workspace=1,
            batch=1,
            dynamic=False,
            simplify=False,
        )
    )
    path = Path(engine)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Export did not produce a nonempty engine")
    print(path.resolve())
    print(record_engine(args.model, path).resolve())


if __name__ == "__main__":
    main()
