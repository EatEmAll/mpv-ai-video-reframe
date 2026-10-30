import pytest

from reframe.curves import soft_shoulder


def test_shoulder_is_monotone_odd_and_never_overshoots():
    values = [soft_shoulder(i / 1000, 0.04) for i in range(-1000, 1001)]
    assert all(a <= b for a, b in zip(values, values[1:]))
    for delta in [0, 0.001, 0.02, 0.1, 0.5]:
        assert abs(soft_shoulder(delta, 0.04)) <= delta
        assert soft_shoulder(-delta, 0.04) == -soft_shoulder(delta, 0.04)
    assert soft_shoulder(0.017, 0.04) < 0.003
    assert soft_shoulder(0.5, 0.04) > 0.495
    with pytest.raises(ValueError):
        soft_shoulder(0.1, -1)
