"""Exercise the real PyAV worker on a tiny CFR clip without requiring CUDA."""

import time
from fractions import Fraction

import av
import numpy as np
import pytest

from reframe.decoder import Worker


class NoopDetector:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1

    def detect(self, frame):
        assert frame.shape[1] == 640
        return []


def test_sampling_and_seek_generation(tmp_path, monkeypatch):
    from reframe import video

    opened = []
    original_open = video.open_video

    def counted_open(path):
        opened.append(path)
        return original_open(path)

    monkeypatch.setattr(video, "open_video", counted_open)
    path = tmp_path / "tiny.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=30)
        stream.width, stream.height = 64, 48
        stream.pix_fmt = "yuv420p"
        for _ in range(90):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((48, 64, 3), dtype=np.uint8), format="bgr24"
            )
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    detector = NoopDetector()
    worker = Worker(detector, rate=10)
    worker.start()
    try:
        worker.update(str(path), 1, 0)
        samples = [worker.output.get(timeout=3) for _ in range(8)]
        assert [round(s[1], 3) for s in samples] == [i / 10 for i in range(8)]
        assert all(s[0] == 1 for s in samples)
        worker.update(str(path), 2, 1.5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            item = worker.output.get(timeout=3)
            if item[0] == 2:
                assert 1.5 <= item[1] < 1.5 + 1 / 30
                break
        else:
            raise AssertionError("No new generation output")
        assert detector.resets == 2
        assert len(opened) == 1  # Keep the codec/device across an in-file seek.
        assert worker.error is None
    finally:
        worker.close()


@pytest.mark.parametrize("suffix,start_ms", [(".mkv", 0), (".ts", 7000)])
def test_vfr_sampling_and_nonzero_start_normalization(tmp_path, suffix, start_ms):
    path = tmp_path / ("variable" + suffix)
    pts = [0, 40, 120, 160, 240, 360, 400, 440, 520, 640, 760, 800, 840, 920, 1040]
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=25)
        stream.width, stream.height = 64, 48
        stream.pix_fmt = "yuv420p"
        stream.codec_context.time_base = Fraction(1, 1000)
        for timestamp in pts:
            frame = av.VideoFrame.from_ndarray(
                np.zeros((48, 64, 3), dtype=np.uint8), format="bgr24"
            )
            frame.pts = start_ms + timestamp
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    if start_ms:
        with av.open(str(path)) as source:
            assert source.start_time / av.time_base >= 7
    worker = Worker(NoopDetector(), rate=10)
    worker.start()
    try:
        worker.update(str(path), 1, 0)
        samples = [worker.output.get(timeout=3) for _ in range(5)]
        assert [s[1] for s in samples] == pytest.approx(
            [0, 0.12, 0.24, 0.36, 0.4], abs=0.001
        )
        assert worker.error is None
    finally:
        worker.close()


def test_new_generation_flushes_queues_and_suspension_retires_requests():
    worker = Worker(NoopDetector())
    worker.update("first.mp4", 1, 0)
    worker.frames.put((1, 0, None, False, 0, False))
    worker.output.put((1, 0, [], False, False))
    worker.update("second.mp4", 2, 3)
    assert worker.frames.empty() and worker.output.empty()
    assert worker.current().generation == 2
    worker.suspend()
    assert worker.current() is None
