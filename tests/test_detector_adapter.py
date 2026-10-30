"""Test the tracking adapter without weights, CUDA, or mirrored model internals."""

import numpy as np
import pytest

from reframe.detector import Detector


class RawBoxes:
    xyxyn = np.array([[0.1, 0.1, 0.2, 0.9], [0.6, 0.1, 0.8, 0.9]])
    conf = np.array([0.9, 0.8])

    def cpu(self):
        return self

    def numpy(self):
        return self


class Prediction:
    boxes = RawBoxes()


class Model:
    def predict(self, *args, **kwargs):
        self.options = kwargs
        return [Prediction()]


class Tracker:
    def update(self, boxes, frame):
        # The Kalman box is intentionally different from the matched raw box.
        return np.array([[10, 10, 20, 90, 7, 0.8, 0, 1]])


@pytest.mark.parametrize("device", [0, "cpu", "mps"])
def test_aim_point_uses_matched_detection_not_lagging_kalman_box(device):
    detector = Detector.__new__(Detector)
    detector.device = device
    detector.model = Model()
    detector.tracker = Tracker()
    result = detector.detect(np.full((100, 100, 3), [0, 0, 200], dtype=np.uint8))
    assert detector.model.options["device"] == device
    assert ("quantize" in detector.model.options) == (device == 0)
    assert len(result) == 1
    assert result[0].track_id == 7
    assert result[0].box == (0.6, 0.1, 0.8, 0.9)
    assert sum(result[0].appearance) == 1
