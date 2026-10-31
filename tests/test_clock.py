import pytest

from reframe.clock import PlaybackClock, PresentationClock


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


def test_latency_percentile_drop_does_not_rewind_the_camera_or_reset_motion():
    from reframe.motion import CameraMotion

    clock, motion = PresentationClock(), CameraMotion()
    values = []
    for i in range(120):
        wall = i / 60
        media = 8 + wall
        # Reproduce a 20 ms p95 IPC latency drop observed in real telemetry.
        lead = 0.089 if i < 60 else 0.069
        query = clock.query(media, wall, lead)
        values.append(query)
        motion.query(0.3 + 0.04 * query, query)
        if i == 60:
            assert motion.velocity > 0.03  # No backward-time seek snap to zero.
            assert motion.position != motion.target
    deltas = [b - a for a, b in zip(values, values[1:])]
    assert min(deltas) > 0
    assert max(deltas) <= 1.5 / 60
    assert values[-1] == pytest.approx(8 + 119 / 60 + 0.069, abs=0.001)


def test_prediction_speed_decrease_pause_seek_and_stall():
    clock = PresentationClock()
    start = clock.query(10, 0, lead=0.4, speed=4)
    # Lower speed shortens predicted render delay; that is not a backward seek.
    assert clock.query(10.01, 0.02, lead=0.05, speed=0.5) >= start
    assert clock.query(10.01, 0.03, paused=True) == 10.01
    assert clock.query(2, 0.04, lead=0.1, generation=1) == 2.1
    for i in range(1, 600):
        value = clock.query(2, 0.04 + i / 60, lead=0.1, generation=1)
    assert value <= 2 + 0.1 + 0.25
