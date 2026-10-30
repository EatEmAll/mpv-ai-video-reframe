import numpy as np
import pytest

from reframe.transforms import display_point, rotate_frame


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_rotated_point_matches_pixel_axes(angle):
    image = np.zeros((3, 5, 3), dtype=np.uint8)
    image[0, 1] = [255, 1, 2]
    result = rotate_frame(image, angle)
    y, x = np.argwhere(result[:, :, 0] == 255)[0]
    expected = display_point(1.5 / 5, 0.5 / 3, angle)
    assert ((x + 0.5) / result.shape[1], (y + 0.5) / result.shape[0]) == pytest.approx(
        expected
    )
    assert result.flags.c_contiguous


def test_non_right_angle_is_rejected():
    with pytest.raises(ValueError):
        display_point(0.3, 0.2, 45)
