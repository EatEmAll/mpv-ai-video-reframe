"""Compare shadow decode discard policies without detector or player changes."""

import argparse
import json
import math
import time
from pathlib import Path

from verification_player import player_for

from reframe.video import open_video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    with player_for(args.clip, speed=2):
        for repeat in range(3):
            for discard in ("DEFAULT", "NONREF"):
                with open_video(str(args.clip)) as container:
                    stream = container.streams.video[0]
                    stream.codec_context.skip_frame = discard
                    start = time.perf_counter()
                    container.seek(
                        int(37 / stream.time_base), stream=stream, backward=True
                    )
                    next_t, count, first = 37, 0, None
                    for frame in container.decode(stream):
                        if frame.time is None or frame.time + 1e-7 < next_t:
                            continue
                        frame.reformat(
                            width=640, height=360, format="bgr24"
                        ).to_ndarray()
                        if first is None:
                            first = time.perf_counter() - start
                        count += 1
                        next_t = (
                            37 + (math.floor((frame.time - 37) * 10 + 1e-5) + 1) / 10
                        )
                        if frame.time >= 42:
                            break
                    rows.append(
                        dict(
                            repeat=repeat,
                            discard=discard,
                            samples=count,
                            first_seconds=first,
                            seconds=time.perf_counter() - start,
                            hwaccel=stream.codec_context.is_hwaccel,
                        )
                    )
    args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
