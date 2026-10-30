from queue import Empty

import av
import numpy as np

from reframe.decoder import Worker
from reframe.timeline import Det, Timeline


def test_priority_preview_does_not_commit_the_half_second_identity_pick():
    timeline = Timeline()
    first = Det(1, (0.6, 0.1, 0.8, 0.9), 0.9)
    winner = Det(2, (0.1, 0.1, 0.4, 0.9), 0.99)
    timeline.add(4, [first])
    assert timeline.preview() == ((4, 0.7, None, "buffering"), first.box)
    assert timeline.target_id is None and not timeline.samples
    for t in (4.1, 4.2, 4.3, 4.4, 4.5):
        timeline.add(t, [first, winner])
    assert timeline.preview() is None
    assert timeline.target_id == 2


class Subject:
    def reset(self):
        pass

    def detect(self, frame):
        return [Det(1, (0.6, 0.1, 0.8, 0.9), 0.9)]


def test_end_of_inference_flushes_a_short_initial_selection(tmp_path):
    file = tmp_path / "short.mp4"
    with av.open(str(file), "w") as out:
        stream = out.add_stream("libx264", rate=30)
        stream.width, stream.height = 64, 48
        stream.pix_fmt = "yuv420p"
        for _ in range(6):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((48, 64, 3), dtype=np.uint8), format="bgr24"
            )
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    worker = Worker(Subject())
    timeline = Timeline()
    worker.start()
    try:
        worker.update(str(file), 1, 0)
        while True:
            try:
                generation, t, dets, cut, resync = worker.output.get(timeout=3)
            except Empty:
                raise AssertionError(
                    worker.error or "Missing ordered EOF marker"
                ) from None
            assert generation == 1
            if t is None:
                timeline.finalize()
                break
            assert timeline.add(t, dets) is None
        assert timeline.initialized
        assert timeline.samples[0][1] == 0.7
    finally:
        worker.close()
