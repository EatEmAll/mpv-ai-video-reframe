import json
from contextlib import nullcontext
from queue import Queue
from threading import Event
from types import SimpleNamespace

import pytest

from reframe import controller
from reframe.evaluation import score_group
from reframe.group import (
    GroupTimeline,
    contain_group,
    coverage,
    coverage_interval,
    fit,
    framed_at,
)
from reframe.motion import CameraMotion
from reframe.planner import Planner
from reframe.timeline import Det

V = 608 / 1920  # portrait window over a 16:9 source at panscan=1


def det(track_id, left, right, confidence=0.9, top=0.1, bottom=0.9):
    return Det(track_id, (left, top, right, bottom), confidence)


def test_coverage_interval_matches_the_floor_at_both_edges():
    box = (0.4, 0.1, 0.5, 0.9)
    lo, hi = coverage_interval(box, V, 0.7)
    assert coverage(box, lo, V) == pytest.approx(0.7)
    assert coverage(box, hi, V) == pytest.approx(0.7)
    assert coverage(box, lo - 0.01, V) < 0.7
    assert coverage(box, (lo + hi) / 2, V) == pytest.approx(1)


def test_coverage_interval_rejects_people_wider_than_the_floor_allows():
    # 0.7 of a 0.5-wide box exceeds the 0.317 crop: never framable.
    assert coverage_interval((0.2, 0, 0.7, 1), V, 0.7) is None
    # At a lower floor the same box becomes framable.
    assert coverage_interval((0.2, 0, 0.7, 1), V, 0.6) is not None


def test_coverage_interval_stays_inside_the_frame():
    lo, hi = coverage_interval((0.0, 0, 0.05, 1), V, 0.7)
    assert lo == pytest.approx(V / 2) and hi >= lo
    lo, hi = coverage_interval((0.95, 0, 1.0, 1), V, 0.7)
    assert hi == pytest.approx(1 - V / 2)


def test_two_people_who_fit_are_both_framed_and_centred():
    a, b = det(1, 0.36, 0.44), det(2, 0.54, 0.62)
    center, framed, _ = fit([a, b], V, 0.7)
    assert [d.track_id for d in framed] == [1, 2]
    assert center == pytest.approx(0.49)


def test_best_pair_is_chosen_and_the_unreachable_person_is_left_out():
    near_a, near_b = det(1, 0.40, 0.48), det(2, 0.56, 0.64)
    far = det(3, 0.02, 0.10)
    center, framed, (lo, hi) = fit([far, near_a, near_b], V, 0.7)
    assert {d.track_id for d in framed} == {1, 2}
    assert lo <= center <= hi
    assert coverage(far.box, center, V) < 0.7


def test_coverage_exactly_at_the_floor_counts_and_just_below_does_not():
    # A keeps 70 % only while the crop's left edge is at most 0.33, so the
    # crop's right edge can reach 0.33 + V and no further.
    a = det(1, 0.30, 0.40)
    edge = 0.33 + V
    at_floor = det(2, edge - 0.07, edge + 0.03)
    below = det(2, edge - 0.069, edge + 0.031)
    assert len(fit([a, at_floor], V, 0.7)[1]) == 2
    assert len(fit([a, below], V, 0.7)[1]) == 1


def test_ties_prefer_the_previous_position():
    left, right = det(1, 0.05, 0.13), det(2, 0.85, 0.93)
    assert fit([left, right], V, 0.7, previous=0.2)[1][0].track_id == 1
    assert fit([left, right], V, 0.7, previous=0.8)[1][0].track_id == 2


def test_larger_confident_person_wins_when_only_one_fits():
    small = det(1, 0.05, 0.12, confidence=0.6)
    large = det(2, 0.80, 0.92, confidence=0.9)
    assert fit([small, large], V, 0.7, previous=0.1)[1][0].track_id == 2


def test_confidence_floor_excludes_weak_detections():
    group = GroupTimeline(conf_floor=0.5)
    sample = group.add(0, [det(1, 0.40, 0.48), det(2, 0.6, 0.68, 0.3)], V)
    assert sample[2] == (1,)
    assert group.info[0]["candidates"] == 1


def test_no_eligible_people_uses_the_follow_target_then_holds():
    group = GroupTimeline()
    assert group.add(0, [], V, fallback=0.3)[1:] == (0.3, (), "fallback")
    assert group.add(0.1, [], V)[1:] == (0.5, (), "searching")
    group.add(0.2, [det(1, 0.70, 0.78)], V)
    held = group.add(0.8, [], V)
    assert held[3] == "group"  # still within the 0.7 s leave window
    later = group.add(1.5, [], V)
    assert later[3] == "hold" and later[1] == pytest.approx(held[1])


def test_unframed_people_are_reported():
    group = GroupTimeline(join=0)
    group.add(0, [det(1, 0.40, 0.48), det(2, 0.56, 0.64), det(3, 0.02, 0.1)], V)
    assert group.samples[-1][2] == (1, 2)
    assert group.info[0]["unframed"] == ((0.02, 0.1, 0.1, 0.9),)


def test_flickering_confidence_does_not_change_membership():
    group = GroupTimeline()
    a = det(1, 0.36, 0.44)
    xs = []
    for k in range(40):
        t = k / 10
        flicker = det(2, 0.54, 0.62, 0.55 if k % 2 else 0.45)
        xs.append(group.add(t, [a, flicker], V)[1])
    members = {s[2] for s in group.samples[12:]}
    assert members == {(1, 2)}
    assert max(xs[12:]) - min(xs[12:]) < 1e-9


def test_newcomer_joins_after_the_join_and_switch_delays():
    group = GroupTimeline(join=0.3, switch=0.6)
    a = det(1, 0.36, 0.44)
    group.add(0, [a], V)
    for k in range(1, 12):
        group.add(k / 10, [a, det(2, 0.54, 0.62)], V)
    ids = {round(s[0], 1): s[2] for s in group.samples}
    # Eligible from 0.1 s, active at 0.4 s, adopted 0.6 s later.
    assert ids[0.9] == (1,)
    assert ids[1.0] == (1, 2)


def test_equal_sized_groups_do_not_hop_on_detector_noise():
    # Only one of two far-apart people fits; their scores alternate by 10 %.
    group = GroupTimeline(join=0)
    xs = []
    for k in range(30):
        big, small = (0.9, 0.82) if k % 2 else (0.82, 0.9)
        dets = [det(1, 0.05, 0.13, big), det(2, 0.80, 0.88, small)]
        xs.append(group.add(k / 10, dets, V)[1])
    assert len({s[2] for s in group.samples}) == 1
    assert max(xs) - min(xs) < 1e-9


def test_clearly_better_group_takes_over_after_the_switch_delay():
    group = GroupTimeline(join=0, switch=0.6)
    group.add(0, [det(1, 0.05, 0.13)], V)
    for k in range(1, 12):
        dets = [det(1, 0.05, 0.13, 0.6), det(2, 0.75, 0.90, 0.95)]
        group.add(k / 10, dets, V)
    ids = {round(s[0], 1): s[2] for s in group.samples}
    assert ids[0.6] == (1,)
    assert ids[0.7] == (2,)


def test_group_that_no_longer_fits_together_switches_immediately():
    group = GroupTimeline(join=0, switch=0.6)
    group.add(0, [det(1, 0.36, 0.44), det(2, 0.54, 0.62)], V)
    # Person 2 jumps away: the pair cannot share a crop any more.
    sample = group.add(0.1, [det(1, 0.36, 0.44), det(2, 0.85, 0.93, 0.95)], V)
    assert len(sample[2]) == 1


def test_departed_member_leaves_after_the_leave_delay():
    group = GroupTimeline(join=0, leave=0.7)
    a, b = det(1, 0.36, 0.44), det(2, 0.54, 0.62)
    group.add(0, [a, b], V)
    for k in range(1, 12):
        group.add(k / 10, [a], V)
    ids = {round(s[0], 1): s[2] for s in group.samples}
    assert ids[0.7] == (1, 2)
    assert ids[0.8] == (1,)


def test_cut_resets_membership():
    group = GroupTimeline(join=0.3)
    group.add(0, [det(1, 0.36, 0.44)], V)
    sample = group.add(0.1, [det(5, 0.70, 0.78)], V, cut=True)
    assert sample[2] == (5,)


def test_contain_group_keeps_a_fitting_group_fully_visible():
    boxes = [(0.36, 0, 0.44, 1), (0.54, 0, 0.62, 1)]
    x = contain_group(0.7, boxes, V, 0.7)
    assert all(coverage(b, x, V) == pytest.approx(1) for b in boxes)
    assert contain_group(0.49, boxes, V, 0.7) == 0.49


def test_contain_group_keeps_the_floor_when_the_group_overflows():
    boxes = [(0.30, 0, 0.40, 1), (0.55, 0, 0.65, 1)]  # 0.35 wide union > V
    for center in (0.2, 0.5, 0.8):
        x = contain_group(center, boxes, V, 0.7)
        assert all(coverage(b, x, V) >= 0.7 - 1e-9 for b in boxes)


def test_framed_boxes_interpolate_only_within_one_member_set():
    group = GroupTimeline(join=0)
    group.add(0, [det(1, 0.30, 0.40)], V)
    group.add(0.1, [det(1, 0.40, 0.50)], V)
    assert framed_at(group, 0.05)[0][0] == pytest.approx(0.35)
    group.add(0.2, [det(1, 0.40, 0.50), det(2, 0.55, 0.6)], V)
    assert framed_at(group, 0.15) == group.framed[0.1]
    assert framed_at(group, 5) == ()


def walkers(t):
    """A static pair, a walker drifting away from it and a distant bystander."""
    b_left = 0.52 + 0.025 * t  # leaves the pair's reach around t = 5 s
    return [
        (0.36, 0.1, 0.44, 0.9),
        (b_left, 0.1, b_left + 0.08, 0.9),
        (0.02, 0.1, 0.10, 0.9),
    ]


def test_simulated_scene_frames_every_reachable_person():
    group = GroupTimeline()
    planner = Planner()
    times = [k / 10 for k in range(120)]
    for t in times:
        dets = [Det(i + 1, box, 0.9) for i, box in enumerate(walkers(t))]
        sample = group.add(t, dets, V)
        planner.add(sample[0], sample[1])
    motion = CameraMotion()
    rows = []
    truth = {}
    for k in range(int(11.5 * 60)):
        t = k / 60
        x = planner.query(t)
        x = contain_group(x, framed_at(group, t), V, group.coverage_floor)
        x = motion.query(x, t)
        rows.append({"t": t, "center": x, "visible": V})
        truth[t] = walkers(t)
    result = score_group(rows[60:], truth)
    assert result["optimal_fraction"] >= 0.95, result
    # Camera speed stays within CameraMotion's limit: no snaps.
    steps = [abs(b["center"] - a["center"]) for a, b in zip(rows, rows[1:])]
    assert max(steps[60:]) <= 0.45 / 60 + 1e-9


def test_controller_toggles_modes_without_snapping_or_losing_the_follow_target(
    monkeypatch, tmp_path
):
    now = [100.0]
    a, b = (0.36, 0.1, 0.46, 0.9), (0.54, 0.1, 0.64, 0.9)

    class Clock:
        def is_set(self):
            return False

        def set(self):
            pass

        def clear(self):
            pass

        def wait(self, timeout):
            now[0] += max(timeout, 1e-3)

    managed = SimpleNamespace(mode="follow", progress=0, state=lambda *a: None)
    sent = []

    def set_align(value, **_):
        ipc.sent_count += 1
        ipc.last_sent = now[0]
        sent.append(value)
        return True

    ipc = SimpleNamespace(
        managed=managed,
        closed=Clock(),
        changed=Clock(),
        error=None,
        enabled=True,
        latency_seconds=0,
        last_sent=0,
        sent_count=0,
        set_align=set_align,
        restore=lambda: None,
        enable=lambda: None,
        close=lambda: None,
    )

    def media_time():
        return now[0] - 100

    def snapshot():
        t = media_time()
        # Follow until 2 s, group until 6 s, then follow again.
        managed.mode = "group" if 2 <= t < 6 else "follow"
        return {
            "path": __file__,
            "time-pos": t,
            "speed": 1,
            "display-fps": 60,
            "container-fps": 30,
            "video-params": {"rotate": 0},
            "osd-dimensions": dict(w=608, h=1080, ml=-656, mr=-656, mt=0, mb=0),
        }, 0

    ipc.snapshot = snapshot
    produced = [0]

    def update(path, generation, t, speed, rotation):
        # Detector runs 2 s ahead of playback at 10 Hz.
        while produced[0] / 10 <= t + 2:
            ts = produced[0] / 10
            dets = [Det(7, a, 0.6), Det(9, b, 0.95)]
            worker.output.put((generation, ts, dets, False, False))
            produced[0] += 1

    worker = SimpleNamespace(
        start=lambda: None,
        close=lambda: None,
        suspend=lambda: None,
        update=update,
        output=Queue(),
        stats={},
        error=None,
    )
    monkeypatch.setattr(controller.time, "perf_counter", lambda: now[0])
    monkeypatch.setattr(controller, "frame_timer", nullcontext)
    monkeypatch.setattr(controller, "Worker", lambda *args: worker)
    controller.run(
        None, None, seconds=9, ipc=ipc, output_dir=tmp_path, stop_event=Event()
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "telemetry.jsonl").read_text().splitlines()
    ]

    def window(lo, hi):
        return [r for r in rows if lo <= r["time_pos"] < hi]

    follow, group, back = window(1.5, 2), window(4.5, 6), window(8, 9)
    assert follow and group and back
    assert {r["mode"] for r in follow + back} == {"follow"}
    assert {r["mode"] for r in group} == {"group"}
    # Follow frames the more confident, more central person throughout.
    assert {r["target_id"] for r in window(1, 9)} == {9}
    for r in follow + back:
        assert coverage(b, r["x_smooth"], V) == pytest.approx(1)
        assert coverage(a, r["x_smooth"], V) < 1
    for r in group:
        assert r["group_ids"] == [7, 9]
        assert coverage(a, r["x_smooth"], V) == pytest.approx(1)
        assert coverage(b, r["x_smooth"], V) == pytest.approx(1)
    # Switching pans under CameraMotion's speed limit instead of snapping.
    for previous, current in zip(rows, rows[1:]):
        dt = current["camera_time"] - previous["camera_time"]
        if dt > 0:
            assert abs(current["x_smooth"] - previous["x_smooth"]) <= (0.45 * dt + 1e-6)
