"""Build a verified FP32 TensorRT 11 engine using only the locked accel group.

FP16 export on TensorRT 11 additionally requires ModelOpt, which is not in this
lock. This explicit FP32 path never enables automatic dependency installation.
Close players before building; engines are local to this runtime/device.
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("models/yolo26n.pt"))
    args = parser.parse_args()
    from windows_preflight import inspect_backend

    inspect_backend(Path(__file__).resolve().parents[1])
    from reframe.models import (
        configure_inference,
        engine_metadata,
        load_yolo,
        record_engine,
        secure_tensorrt,
        verify_weights,
    )
    from reframe.security import trusted_path

    configure_inference()
    import tensorrt

    if not secure_tensorrt(tensorrt.__version__):
        parser.error("TensorRT >=11.2 required; sync the locked accel group first")
    weights = verify_weights(args.model)
    engine = trusted_path(weights.with_suffix(".engine"))
    manifest = trusted_path(Path(str(engine) + ".json"))
    if engine.exists() or manifest.exists():
        parser.error(
            "Refusing to replace an existing engine or manifest; use a fresh prepared model directory"
        )
    path = Path(
        load_yolo(weights).export(
            format="engine",
            device=0,
            imgsz=640,
            quantize=32,
            workspace=1,
            batch=1,
            dynamic=False,
            simplify=False,
            # nms=False keeps YOLO26's native NMS-free head in the graph. With the
            # default (nms=None) Ultralytics exports raw predictions and imports
            # torchvision on first use to run NMS, which costs seconds on a cold
            # start and is not needed by the one-to-one head.
            nms=False,
        )
    )
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError("Export produced no engine")
    try:
        if engine_metadata(path).get("end2end") is not True:
            raise RuntimeError(
                "Ultralytics did not export the NMS-free head; the engine would "
                "still import torchvision at startup"
            )
        manifest = record_engine(weights, path)
        # record_engine's legacy default describes the older FP16 exporter.
        # Correct this newly built local artifact before exposing it to callers.
        # Hash/device/runtime trust checks are unchanged.
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.update(
            precision="fp32",
            tf32_allowed=True,
            exporter="locked-ultralytics-fp32",
            end2end=True,
        )
        from plugin import atomic

        atomic(manifest, json.dumps(data, indent=2).encode("utf-8"))
    except BaseException:
        # Never leave misleading metadata selectable after an interrupted export.
        # The unmanifested engine is retained for diagnosis and will be rejected.
        manifest.unlink(missing_ok=True)
        raise
    print(path.resolve())
    print(manifest.resolve())


if __name__ == "__main__":
    main()
