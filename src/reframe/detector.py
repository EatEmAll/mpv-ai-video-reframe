"""YOLO26n + ByteTrack adapter; the model is owned by one worker."""

import os
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace


class Detector:
    def __init__(self, model_path=None):
        from .models import configure_inference

        configure_inference()
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import numpy as np
        import torch
        import yaml
        from ultralytics.trackers.byte_tracker import BYTETracker

        from .models import load_yolo, select_model

        cuda = torch.cuda.is_available()
        self.device = (
            0 if cuda else ("mps" if torch.backends.mps.is_available() else "cpu")
        )
        self.model_path = select_model(
            model_path, torch.cuda.get_device_capability(0) if cuda else None
        )
        if self.model_path.suffix == ".engine" and self.device != 0:
            raise RuntimeError(
                "TensorRT engines require CUDA; select local .pt weights"
            )
        self.model = load_yolo(self.model_path)
        config = yaml.safe_load(
            (Path(__file__).parent / "trackers/bytetrack_reframe.yaml").read_text()
        )
        self.tracker = BYTETracker(SimpleNamespace(**config))
        self.detect(np.zeros((360, 640, 3), dtype=np.uint8))
        self.reset()

    def reset(self):
        self.tracker.reset()

    def detect(self, frame):
        import cv2

        from .timeline import Det

        result = self.model.predict(
            frame,
            classes=[0],
            conf=0.25,
            imgsz=640,
            **({"quantize": 16} if self.device == 0 else {}),
            device=self.device,
            verbose=False,
        )[0]
        raw = result.boxes.cpu().numpy()
        tracks = self.tracker.update(raw, frame)
        detections = []
        h, w = frame.shape[:2]
        for track in tracks:
            index = int(track[-1])
            i = int(track[4])
            box = raw.xyxyn[index].tolist()
            c = float(raw.conf[index])
            x0, y0, x1, y1 = box
            torso = frame[
                max(0, int((y0 + 0.15 * (y1 - y0)) * h)) : min(
                    h, int((y0 + 0.65 * (y1 - y0)) * h)
                ),
                max(0, int((x0 + 0.2 * (x1 - x0)) * w)) : min(
                    w, int((x0 + 0.8 * (x1 - x0)) * w)
                ),
            ]
            appearance = ()
            if torso.size:
                hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist(
                    [hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]
                ).ravel()
                hist = hist / max(float(hist.sum()), 1)
                appearance = tuple(float(v) for v in hist)
            detections.append(
                Det(int(i), tuple(float(v) for v in box), float(c), appearance)
            )
        return detections


class DetectorLoader:
    """Build a Detector on a background thread.

    torch/Ultralytics imports, CUDA setup and warmup take seconds (much longer
    on a cold disk), so start them while waiting for a video.
    """

    def __init__(self, model_path=None, factory=Detector):
        self.done = Event()
        self.detector = None
        self.error = None
        self.thread = Thread(
            target=self._load,
            args=(factory, model_path),
            name="reframe-model-load",
            daemon=True,
        )
        self.thread.start()

    def _load(self, factory, model_path):
        try:
            self.detector = factory(model_path)
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def result(self, stop=None):
        """Wait for the detector; return None if `stop` is set first."""
        while not self.done.wait(0.1):
            if stop is not None and stop.is_set():
                return None
        if self.error is not None:
            raise self.error
        return self.detector
