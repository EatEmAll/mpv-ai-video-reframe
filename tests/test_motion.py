import pytest

from reframe.motion import CameraMotion
from reframe.planner import Planner


def test_noisy_detection_corrections_have_bounded_speed_and_acceleration():
    motion = CameraMotion()
    values = [motion.query(0.5, 0)]
    velocities = [0.0]
    for i in range(1, 241):
        values.append(motion.query(0.5 + (0.04 if (i // 6) % 2 else -0.04), i / 60))
        velocities.append(motion.velocity)
    assert max(abs(v) for v in velocities) <= motion.max_speed
    assert (
        max(abs(b - a) * 60 for a, b in zip(velocities, velocities[1:]))
        <= motion.max_acceleration + 1e-8
    )
    assert max(values) - min(values) < 0.04
    assert max(abs(b - a) for a, b in zip(values, values[1:])) < 0.005


def test_seek_cut_and_paused_frame_changes_can_snap():
    motion = CameraMotion()
    assert motion.query(0.2, 0) == 0.2
    assert 0.2 < motion.query(0.8, 0.02) < 0.21
    assert motion.query(0.8, 0.03, snap=True) == 0.8
    assert motion.velocity == 0
    assert motion.query(0.3, 0) == 0.3


def test_stationary_camera_stays_still_and_tracks_a_sustained_move():
    motion = CameraMotion()
    for i in range(60):
        assert motion.query(0.4, i / 60) == 0.4
    for i in range(60, 240):
        motion.query(0.6, i / 60)
    assert motion.position == pytest.approx(0.6, abs=0.001)


def test_buffered_intent_compensates_damping_without_static_drift():
    planner, motion = Planner(), CameraMotion()
    for i in range(160):
        t = i / 10
        planner.add(t, 0.3 if t < 8 else 0.3 + (t - 8) * 0.0375)
    camera = []
    for i in range(151):
        t = i * 4 / 60
        query_t = t + 8 / 60
        x = motion.query(planner.query(query_t), query_t)
        camera.append((t, x))
    onset = next(
        b[0]
        for a, b in zip(camera, camera[1:])
        if (b[1] - a[1]) / (b[0] - a[0]) > 0.004
    )
    assert onset <= 8
    static = [x for t, x in camera if t < 8]
    assert (
        sum(abs(b - a) for a, b in zip(static, static[1:])) * 2 * 1920 / (1920 - 608)
        <= 0.01
    )
