"""Build reproducible person-composite scenarios with per-frame ground truth.

Fixed public-image cutouts remove inference/device variation from generation.
The truth is the exact compositing transform, independent of future detections.
"""

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np


def scenario(t):
    """Primary centre, presence, crossing-person centre, scene number."""
    if t < 8:
        x = 0.3
    elif t < 20:
        x = 0.3 + 0.45 * (t - 8) / 12
    elif t < 26:
        x = 0.75 - 0.55 * (t - 20) / 6
    elif t < 27:
        x = 0.2 - 0.35 * (t - 26)
    elif t < 30:
        return -0.15, False, None, 0
    elif t < 32:
        x = 1.15 - 0.55 * (t - 30) / 2
    elif t < 45:
        x = 0.6 - 0.2 * (t - 32) / 13
    else:
        x = [0.75, 0.2, 0.65][min(2, int((t - 45) / 5))]
    second = 0.15 + 0.7 * (t - 32) / 13 if 32 <= t < 45 else None
    return x, True, second, 0 if t < 45 else 1 + int((t - 45) / 5)


def cutouts(height):
    assets = Path(importlib.util.find_spec("ultralytics").origin).parent / "assets"
    source = assets / "bus.jpg"
    if (
        hashlib.sha256(source.read_bytes()).hexdigest()
        != "c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63"
    ):
        raise ValueError("Public fixture image hash mismatch")
    image = cv2.imread(str(source))
    # Fixed source-image coordinates; detector output never defines evaluation truth.
    boxes = [(50, 400, 245, 903), (670, 390, 809, 877)]
    crops = []
    for box in boxes[:2]:
        x0, y0, x1, y1 = [int(v) for v in box]
        crop = image[y0:y1, x0:x1]
        h = round(height * 0.66)
        w = round(crop.shape[1] * h / crop.shape[0])
        crops.append(cv2.resize(crop, (w, h), interpolation=cv2.INTER_CUBIC))
    return crops


def paste(frame, crop, center, bottom=0.94):
    h, w = frame.shape[:2]
    ch, cw = crop.shape[:2]
    left = round(center * w - cw / 2)
    top = round(bottom * h - ch)
    x0, x1 = max(0, left), min(w, left + cw)
    y0, y1 = max(0, top), min(h, top + ch)
    if x1 <= x0 or y1 <= y0:
        return None
    frame[y0:y1, x0:x1] = crop[y0 - top : y1 - top, x0 - left : x1 - left]
    return [x0 / w, y0 / h, x1 / w, y1 / h]


def generate(output, variant, duration=60):
    width, height, fps, codec = {
        "1080p30": (1920, 1080, 30, "libx264"),
        "1080p60": (1920, 1080, 60, "libx264"),
        "2160p60": (3840, 2160, 60, "libx265"),
        "vfr": (1920, 1080, 30, "libx264"),
    }[variant]
    if not output.parent.is_dir():
        raise ValueError(f"Output directory must already exist: {output.parent}")
    truth_path = output.with_suffix(".gt.json")
    if output.exists() or truth_path.exists():
        raise FileExistsError(f"Refusing to overwrite {output} or {truth_path}")
    primary, secondary = cutouts(height)
    y, x = np.indices((height, width))
    texture = ((x // 48 + y // 48) % 2 * 12).astype(np.uint8)
    backgrounds = [
        np.stack([texture + c[0], texture + c[1], texture + c[2]], axis=-1)
        for c in [(76, 94, 102), (145, 40, 40), (35, 140, 50), (40, 40, 150)]
    ]
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
    ]
    if variant == "vfr":
        # Alternate 30/15 fps blocks while preserving the original media PTS.
        command += [
            "-vf",
            "select='if(lt(mod(t,4),2),1,not(mod(n,2)))'",
            "-fps_mode",
            "vfr",
        ]
    command += [
        "-c:v",
        codec,
        "-preset",
        "ultrafast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    records = []
    with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
        try:
            for index in range(round(duration * fps)):
                t = index / fps
                center, present, other, scene = scenario(t)
                frame = backgrounds[min(3, scene)].copy()
                box = paste(frame, primary, center) if present else None
                other_box = (
                    paste(frame, secondary, other, bottom=0.98)
                    if other is not None
                    else None
                )
                # VFR frames rejected by ffmpeg also have no entry in the truth.
                keep = variant != "vfr" or t % 4 < 2 or index % 2 == 0
                if keep:
                    records.append(
                        {
                            "t": t,
                            "primary": box,
                            "secondary": other_box,
                            "scene": scene,
                            "events": (
                                ["cut"]
                                if index in [45 * fps, 50 * fps, 55 * fps]
                                else []
                            ),
                        }
                    )
                process.stdin.write(frame.tobytes())
        finally:
            process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(
                "ffmpeg failed; incomplete output must be inspected before retry"
            )
    truth_path.write_text(
        json.dumps(
            {
                "version": 1,
                "width": width,
                "height": height,
                "fps": fps,
                "variant": variant,
                "duration": duration,
                "source": "ultralytics/assets/bus.jpg",
                "source_sha256": "c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63",
                "source_cutouts_xyxy": [[50, 400, 245, 903], [670, 390, 809, 877]],
                "frames": records,
            }
        ),
        encoding="utf-8",
    )
    if output.stat().st_size == 0 or truth_path.stat().st_size == 0:
        raise RuntimeError("Empty generated artifact")
    print(
        json.dumps(
            {
                "video": str(output.resolve()),
                "truth": str(truth_path.resolve()),
                "frames": len(records),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant", choices=["1080p30", "1080p60", "2160p60", "vfr"], default="1080p30"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--duration", type=float, default=60)
    args = parser.parse_args()
    output = args.output or Path("samples") / (
        f"synth_walk_{args.variant}" + (".mkv" if args.variant == "vfr" else ".mp4")
    )
    generate(output, args.variant, args.duration)


if __name__ == "__main__":
    main()
