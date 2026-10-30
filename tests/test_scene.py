import numpy as np

from reframe.scene import descriptor, is_cut


def test_colour_channel_permutations_are_a_cut():
    a = np.full((32, 32, 3), [145, 40, 40], dtype=np.uint8)
    b = np.full((32, 32, 3), [40, 145, 40], dtype=np.uint8)
    assert is_cut(descriptor(a), descriptor(b))
    assert not is_cut(descriptor(a), descriptor(a))
