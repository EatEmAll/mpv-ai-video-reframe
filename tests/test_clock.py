from reframe.clock import PlaybackClock


def test_updates_between_ten_fps_observations():
    clock = PlaybackClock()
    values = [clock.query(int(i / 6) / 10, i / 60) for i in range(120)]
    assert all(b > a for a, b in zip(values, values[1:]))
    assert max(b - a for a, b in zip(values, values[1:])) < 0.025


def test_pause_seek_and_speed():
    clock = PlaybackClock()
    assert clock.query(3, 0) == 3
    assert clock.query(3, 0.02, speed=2) > 3.03
    assert clock.query(3, 0.03, paused=True) == 3
    assert clock.query(1, 0.04, generation=1) == 1


def test_stall_bounded():
    clock = PlaybackClock()
    for i in range(120):
        value = clock.query(0, i / 60)
    assert value <= 0.25
