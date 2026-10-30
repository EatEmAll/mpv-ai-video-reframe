import pytest

from reframe.filters import OneEuro
from reframe.planner import Planner


def test_camera_reverses_before_the_subject_with_lookahead():
    planner = Planner()
    for i in range(160, 241):
        t = i / 10
        x = 0.3 + 0.45 * (t - 8) / 12 if t <= 20 else 0.75 - 0.55 * (t - 20) / 6
        planner.add(t, x)
    samples = [(19 + i / 60, planner.query(19 + i / 60)) for i in range(121)]
    peak = max(samples, key=lambda s: s[1])
    assert peak[0] < 20


def test_causal_fallback_has_no_overshoot():
    euro = OneEuro()
    assert euro.query(0, 0.2) == 0.2
    values = [euro.query(i / 60, 0.8) for i in range(1, 61)]
    assert all(0.2 < x <= 0.8 for x in values)
    assert values[-1] > 0.79
    assert euro.query(0, 0.4) == pytest.approx(0.4)


def test_leaving_dead_zone_does_not_release_the_accumulated_offset():
    planner = Planner(sigma=0.01, anticipation=0, shoulder=0)
    planner.add(0, 0.5)
    assert planner.query(0) == 0.5
    planner.add(1, 0.55)
    assert 0.5 <= planner.query(1) < 0.515
