import pytest

from reframe.framing import box_at, contain_center
from reframe.timeline import Timeline


def test_smoothing_cannot_push_a_fittable_person_out_of_the_crop():
    box = (0.6, 0.1, 0.75, 0.9)
    x = contain_center(0.4, box, 0.25)
    assert x - 0.125 <= box[0] and x + 0.125 >= box[2]
    assert contain_center(0.7, box, 0.25) == 0.7


def test_oversize_person_keeps_the_smooth_path_within_maximum_overlap():
    box = (0.15, 0.05, 0.85, 0.95)
    assert contain_center(0.45, box, 608 / 1920) == 0.45
    assert contain_center(0.2, box, 608 / 1920) == pytest.approx(0.15 + 304 / 1920)


def test_box_interpolation_does_not_cross_identity_changes_or_losses():
    timeline = Timeline()
    timeline.samples = [(0, 0.3, 1, "tracking"), (0.1, 0.5, 1, "tracking")]
    timeline.boxes = {0: (0.2, 0.1, 0.4, 0.9), 0.1: (0.4, 0.1, 0.6, 0.9)}
    assert box_at(timeline, 0.05) == pytest.approx((0.3, 0.1, 0.5, 0.9))
    timeline.samples[1] = (0.1, 0.5, 2, "tracking")
    assert box_at(timeline, 0.05) == timeline.boxes[0]
    assert box_at(timeline, 1) is None
