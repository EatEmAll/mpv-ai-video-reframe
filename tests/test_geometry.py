import pytest

from reframe.geometry import align_for_center, visible_center


@pytest.mark.parametrize("width", [320, 608, 1080, 1920])
@pytest.mark.parametrize("scaled", [640, 1920, 3840])
@pytest.mark.parametrize("p", [0, 0.1, 0.3, 0.5, 0.8, 1])
def test_aspect_oracle(width, scaled, p):
    align = align_for_center(p, width, scaled)
    margin = int((width - scaled) * (align + 1) / 2)
    if scaled > width:
        reachable = max(width / (2 * scaled), min(1 - width / (2 * scaled), p))
        assert abs(visible_center(width, scaled, margin) - reachable) <= 1 / scaled
        assert width - scaled <= margin <= 0
    else:
        assert align == 0


@pytest.mark.parametrize(
    "p,w,s", [(float("nan"), 608, 1920), (0.5, 0, 1920), (0.5, 608, -1)]
)
def test_invalid(p, w, s):
    with pytest.raises(ValueError):
        align_for_center(p, w, s)
