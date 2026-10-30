import pytest

from reframe.evaluation import score, truth_at


def test_truth_uses_presentation_time_not_frame_index():
    truth = {
        "frames": [
            {"t": 0, "primary": [0.2, 0, 0.4, 1]},
            {"t": 0.1, "primary": [0.5, 0, 0.7, 1]},
            {"t": 0.4, "primary": None},
        ]
    }
    assert truth_at(truth, 0.35)["primary"] == [0.5, 0, 0.7, 1]


def test_geometry_is_independent_of_commanded_alignment():
    truth = {"frames": [{"t": 0, "primary": [0.21, 0, 0.39, 1]}]}
    # Margins show a crop centred at 0.75 even though the align property says zero.
    rows = [{"time_pos": 2, "w": 200, "ml": -650, "mr": -150, "align": 0}]
    result, scored = score(rows, truth, 1)
    assert scored[0]["center"] == 0.75
    assert result["median_error"] == pytest.approx(0.45)
    assert result["gates"]["follow_p95"] is False
    assert result["gates"]["containment"] is False
    assert result["gates"]["anticipation"] is None


def test_missing_data_cannot_pass():
    result, _ = score([], {"frames": [{"t": 0, "primary": None}]}, 1)
    assert all(value is None for value in result["gates"].values())
