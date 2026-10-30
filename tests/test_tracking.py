import pytest

from reframe.planner import Planner
from reframe.timeline import Det, Timeline


def det(i, x):
    return Det(i, (x - 0.05, 0.1, x + 0.05, 0.9), 0.9)


def test_stickiness_and_id_adoption():
    t = Timeline(selection_window=0)
    assert t.add(0, [det(1, 0.3)])[2] == 1
    assert t.add(0.1, [det(1, 0.3), det(2, 0.5)])[2] == 1
    assert t.add(0.2, [det(3, 0.31)])[2] == 3
    assert t.add(0.3, [])[3] == "hold"
    assert t.add(4, [])[1] == 0.5
    assert t.add(4.1, [det(5, 0.7)])[2] == 5


def test_planner_anticipates_without_overshoot():
    p = Planner()
    for i in range(40):
        p.add(i / 10, 0.3 if i < 20 else 0.7)
    assert 0.3 < p.query(1.8) < 0.7
    assert p.query(2.1) <= 0.7


def test_dead_zone():
    p = Planner()
    for i in range(40):
        p.add(i / 10, 0.5 + (0.02 if i % 2 else -0.02))
    assert p.query(1) == pytest.approx(0.48)
    assert p.query(1.5) == pytest.approx(0.48)


def test_rate_limit_and_cut_snap():
    p = Planner()
    p.add(0, 0.1)
    assert p.query(0) == 0.1
    p.add(1, 0.9)
    assert p.query(0.1) <= 0.16
    p.add(1.1, 0.8, cut=True)
    assert p.query(1.1) == pytest.approx(0.8)


def test_initial_window_selects_the_persistent_track():
    timeline = Timeline()
    assert timeline.add(0, [det(2, 0.5)]) is None
    for i in range(1, 5):
        assert timeline.add(i / 10, [det(1, 0.3)]) is None
    result = timeline.add(0.5, [det(1, 0.3)])
    assert result[2] == 1
    assert len(timeline.samples) == 6


def test_appearance_prevents_following_a_swapped_raw_id():
    timeline = Timeline(selection_window=0)
    timeline.add(0, [Det(1, (0.2, 0, 0.4, 1), 0.9, (1, 0))])
    result = timeline.add(
        0.1,
        [Det(1, (0.2, 0, 0.4, 1), 0.9, (0, 1)), Det(2, (0.3, 0, 0.5, 1), 0.9, (1, 0))],
    )
    assert result[1] == pytest.approx(0.4)
    assert result[2] == 2


def test_short_gap_is_interpolated():
    planner = Planner()
    planner.add(0, 0.2)
    planner.add(0.4, 0.6)
    assert planner.samples[2][1] == pytest.approx(0.4)


def test_short_clip_can_finalize_selection_before_half_a_second():
    timeline = Timeline()
    assert timeline.add(0, [det(1, 0.7)]) is None
    assert timeline.add(0.2, [det(1, 0.7)]) is None
    assert timeline.finalize()[1:3] == (0.7, 1)
    assert timeline.initialized
    timeline.prune(6)
    assert timeline.samples == []
    assert timeline.boxes == {}


def test_repick_buffers_candidates_and_replaces_the_old_appearance_anchor():
    timeline = Timeline(selection_window=0.5)
    original = Det(1, (0.2, 0, 0.4, 1), 0.9, (1, 0))
    replacement = Det(2, (0.6, 0, 0.8, 1), 0.9, (0, 1))
    for i in range(6):
        timeline.add(i / 10, [original])
    assert timeline.target_id == 1
    timeline.add(4, [])
    assert timeline.add(4.1, [replacement])[2] == 1
    assert timeline.add(4.4, [replacement])[2] == 1
    assert timeline.add(4.6, [replacement])[2] == 2
    assert timeline.template == (0, 1)
    assert timeline.add(4.7, [replacement])[3] == "tracking"
