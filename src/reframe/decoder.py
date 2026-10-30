"""Separate bounded decoder and inference threads with seek generations."""

import math
import time
import traceback
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread


@dataclass(frozen=True)
class Request:
    path: str
    generation: object
    time: float
    speed: float = 1.0
    rotation: int = 0


class Worker(Thread):
    def __init__(self, detector, rate=10):
        super().__init__(name="reframe-decoder", daemon=True)
        self.detector, self.rate = detector, rate
        self.output = Queue(maxsize=64)
        self.frames = Queue(maxsize=8)
        self.stop_event = Event()
        self.changed = Event()
        self.lock = Lock()
        self.request = None
        self.error = None
        self.inference = Thread(target=self.infer, name="reframe-detector", daemon=True)
        self.detector_seconds = 0.025
        self.stats = {
            "behind": 0,
            "decoder_rtf": 0.0,
            "sample_rate": rate,
            "detector_ms": 0.0,
        }

    def start(self):
        self.inference.start()
        super().start()

    def flush_queues(self):
        for queue in (self.frames, self.output):
            while True:
                try:
                    queue.get_nowait()
                except Empty:
                    break

    def suspend(self):
        with self.lock:
            self.request = None
            self.flush_queues()
        self.changed.set()

    def update(self, path, generation, time, speed=1.0, rotation=0):
        with self.lock:
            if self.request is None or self.request.generation != generation:
                self.flush_queues()
            self.request = Request(path, generation, time, speed, rotation)
        self.changed.set()

    def current(self):
        with self.lock:
            return self.request

    def close(self):
        self.stop_event.set()
        self.changed.set()
        self.join(timeout=10)
        self.inference.join(timeout=10)
        if self.is_alive() or self.inference.is_alive():
            raise RuntimeError("Decoder worker did not stop")

    def enqueue(self, queue, item, generation):
        while not self.stop_event.is_set():
            latest = self.current()
            if latest is None or latest.generation != generation:
                return False
            try:
                queue.put(item, timeout=0.05)
                return True
            except Full:
                pass
        return False

    def infer(self):
        active = None
        try:
            while not self.stop_event.is_set():
                try:
                    generation, t, frame, cut, segment, resync = self.frames.get(
                        timeout=0.1
                    )
                except Empty:
                    continue
                current = self.current()
                if current is None or current.generation != generation:
                    continue
                if frame is None:
                    self.enqueue(
                        self.output, (generation, None, [], False, False), generation
                    )
                    continue
                if active != (generation, segment) or cut:
                    self.detector.reset()
                    active = (generation, segment)
                start = time.perf_counter()
                detections = self.detector.detect(frame)
                elapsed = time.perf_counter() - start
                self.detector_seconds = 0.9 * self.detector_seconds + 0.1 * elapsed
                self.stats["detector_ms"] = self.detector_seconds * 1000
                self.enqueue(
                    self.output, (generation, t, detections, cut, resync), generation
                )
        except Exception:
            self.error = traceback.format_exc()
            self.stop_event.set()

    def run(self):
        import av

        from .scene import descriptor, is_cut
        from .seeking import seek_frames
        from .transforms import rotate_frame
        from .video import open_video

        active = None
        container = None
        iterator = None
        next_t = 0
        previous_hist = None
        segment = 0
        resync = False
        decode_cost = 0
        decode_first = None
        eof_sent = False
        try:
            while not self.stop_event.is_set():
                request = self.current()
                if request is None:
                    iterator = None
                    if container:
                        container.close()
                        container = None
                        active = None
                    self.changed.wait(0.1)
                    self.changed.clear()
                    continue
                if active is None or (request.path, request.generation) != (
                    active.path,
                    active.generation,
                ):
                    if iterator is not None:
                        iterator.close()
                    # Reuse the codec/device across seeks in the same file.
                    # Recreating a CUDA device per seek adds avoidable latency.
                    # A seek to zero gets a fresh demuxer for sparse TS indexes.
                    if (
                        container is None
                        or active.path != request.path
                        or request.time <= 0
                    ):
                        if container:
                            container.close()
                        container = open_video(request.path)
                    stream = container.streams.video[0]
                    offset = (container.start_time or 0) / av.time_base
                    iterator = seek_frames(container, stream, request.time, offset)
                    active = request
                    next_t = request.time
                    previous_hist = None
                    segment = 0
                    resync = False
                    decode_cost = 0
                    decode_first = None
                    eof_sent = False
                if next_t < request.time - 0.5:
                    target = request.time + 1.2
                    iterator = seek_frames(container, stream, target, offset)
                    next_t = target
                    segment += 1
                    resync = True
                    eof_sent = False
                    previous_hist = None
                    self.stats["behind"] += 1
                    # Keep the bounded queue in timestamp order. In particular,
                    # never discard a seek's priority frame before inference has
                    # consumed it. The segment tag resets the tracker at resync.
                if next_t > request.time + 2.5:
                    self.changed.wait(0.05)
                    self.changed.clear()
                    continue
                decode_start = time.perf_counter()
                frame = next(iterator, None)
                decode_cost += time.perf_counter() - decode_start
                if frame is None:
                    if not eof_sent:
                        eof_sent = self.enqueue(
                            self.frames,
                            (request.generation, None, None, False, segment, False),
                            request.generation,
                        )
                    self.changed.wait(0.1)
                    self.changed.clear()
                    continue
                if frame.time is None:
                    continue
                t = frame.time - offset
                if t + 1e-7 < next_t:
                    continue
                convert_start = time.perf_counter()
                frame = frame.reformat(
                    width=640,
                    height=max(2, round(frame.height * 640 / frame.width)),
                    format="bgr24",
                ).to_ndarray()
                frame = rotate_frame(frame, request.rotation)
                self.stats["hwaccel"] = bool(stream.codec_context.is_hwaccel)
                hist = descriptor(frame)
                cut = is_cut(previous_hist, hist)
                previous_hist = hist
                decode_cost += time.perf_counter() - convert_start
                if decode_first is None:
                    decode_first = t
                self.stats["decoder_rtf"] = (t - decode_first) / max(decode_cost, 1e-6)
                item = (request.generation, t, frame, cut, segment, resync)
                if self.enqueue(self.frames, item, request.generation):
                    resync = False
                effective_rate = min(
                    self.rate,
                    max(5, 0.7 / (self.detector_seconds * max(1, request.speed))),
                )
                self.stats["sample_rate"] = effective_rate
                # Preserve the requested regular grid unless adaptation is needed.
                if effective_rate == self.rate:
                    next_t = (
                        active.time
                        + (math.floor((t - active.time) * self.rate + 1e-5) + 1)
                        / self.rate
                    )
                else:
                    next_t = t + 1 / effective_rate
        except Exception:
            self.error = traceback.format_exc()
        finally:
            if container:
                container.close()
