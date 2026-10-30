"""Large shadow streams keep reference pictures and ordered seek generations."""

import time

import av
import numpy as np
from test_decoder import NoopDetector

from reframe.decoder import Worker


def test_large_b_frame_stream_sampling_after_seek(tmp_path, monkeypatch):
    # Use software decode so this test also runs without an NVIDIA GPU.
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    path = tmp_path / "large.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=60)
        stream.width, stream.height = 2560, 64
        stream.pix_fmt = "yuv420p"
        stream.options = {"bf": "3", "g": "60", "preset": "fast"}
        for _ in range(180):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((64, 2560, 3), dtype=np.uint8), format="bgr24"
            )
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    worker = Worker(NoopDetector(), rate=10)
    worker.start()
    try:
        worker.update(str(path), 1, 0)
        samples = [worker.output.get(timeout=5) for _ in range(6)]
        times = [s[1] for s in samples]
        assert times[0] == 0
        assert all(0 < b - a <= 0.15 for a, b in zip(times, times[1:]))
        worker.update(str(path), 2, 1.5)
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            sample = worker.output.get(timeout=5)
            if sample[0] == 2:
                assert 1.5 <= sample[1] <= 1.6
                break
        else:
            raise AssertionError("Seek generation was lost")
        assert worker.error is None
    finally:
        worker.close()
