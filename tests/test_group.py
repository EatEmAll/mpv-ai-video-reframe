import json
import sys
from contextlib import nullcontext
from queue import Queue
from threading import Event
from types import SimpleNamespace

import pytest

from reframe import controller
from reframe.evaluation import score_group
from reframe.group import (
    GroupTimeline,
    clearly_better,
    contain_group,
    coverage,
    coverage_interval,
    dedupe,
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


def test_widest_most_confident_person_is_framed_below_the_floor():
    wide = det(1, 0.10, 0.90, confidence=0.92)
    left = det(2, 0.20, 0.28, confidence=0.85)
    right = det(3, 0.72, 0.80, confidence=0.71)
    center, framed, _ = fit([wide, left, right], V, 0.7)
    assert wide in framed
    assert coverage(wide.box, center, V) < 0.7
    assert coverage(left.box, center, V) >= 0.7 - 1e-9


def test_most_confident_person_wins_over_a_larger_group():
    top = det(1, 0.02, 0.10, confidence=0.92)
    pair = [det(2, 0.40, 0.48, confidence=0.85), det(3, 0.56, 0.64, confidence=0.71)]
    _, framed, _ = fit([top] + pair, V, 0.7)
    assert [d.track_id for d in framed] == [1]


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


def test_confidences_are_recorded_in_box_order():
    group = GroupTimeline(join=0)
    people = [det(2, 0.56, 0.64, 0.8), det(1, 0.40, 0.48, 0.6), det(3, 0.02, 0.1, 0.7)]
    group.add(0, people, V)
    assert group.info[0]["framed_confidence"] == (0.6, 0.8)  # sorted by track ID
    assert group.info[0]["unframed_confidence"] == (0.7,)


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


def test_contain_group_keeps_the_crop_inside_a_wider_anchor():
    box = (0.10, 0.0, 0.90, 1.0)
    center = contain_group(0.5, [box], V, 0.7)
    assert 0.10 + V / 2 - 1e-9 <= center <= 0.90 - V / 2 + 1e-9
    assert coverage(box, center, V) == pytest.approx(V / 0.8)


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
    # score_group measures the strict coverage floor, so switch relaxation off.
    group = GroupTimeline(relaxed_floor=0.7, pair_floor=0.7, slack=1.0)
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


# The 1:00 frame: three people, the two most confident about equally large.
LEFT = Det(2, (0.0, 0.1, 0.547, 0.9), 0.80)
RIGHT = Det(1, (0.555, 0.1, 0.999, 0.9), 0.85)
MIDDLE = Det(3, (0.361, 0.1, 0.621, 0.9), 0.52)


def test_more_confident_person_is_framed_at_the_cost_of_coverage():
    # At 1133 px the crop shows 60% of the video: the 0.85 and 0.80 people
    # cannot both keep 70%, but a centred crop shows more than half of each.
    people = [LEFT, RIGHT, MIDDLE]
    strict = fit(people, 0.603, 0.7)
    assert [d.track_id for d in strict[1]] == [1, 3]  # only the weaker fits
    center, framed, _ = fit(people, 0.603, 0.7, relaxed=0.5)
    # The 0.52 person is inside that crop anyway, so all three are framed.
    assert [d.track_id for d in framed] == [1, 2, 3]
    assert 0.5 < coverage(LEFT.box, center, 0.603) < 0.7
    assert 0.5 < coverage(RIGHT.box, center, 0.603) < 0.7
    # The crop balances the two instead of favouring one.
    assert (
        abs(coverage(LEFT.box, center, 0.603) - coverage(RIGHT.box, center, 0.603))
        < 0.05
    )


def test_two_most_confident_people_share_a_tight_portrait_crop():
    # A portrait window over 16:9 shows 33% of the video, and these close-up
    # boxes are each about half of it wide: no crop keeps 50% of either.
    V_portrait = 0.333
    people = [det(1, 0.00, 0.53, 0.82), det(2, 0.55, 1.00, 0.78)]
    assert [d.track_id for d in fit(people, V_portrait, 0.7, relaxed=0.5)[1]] == [1]
    center, framed, _ = fit(people, V_portrait, 0.7, relaxed=0.5, pair=0.25)
    assert [d.track_id for d in framed] == [1, 2]
    # The crop sits on the seam and shows a similar share of each person.
    assert center == pytest.approx(0.54, abs=0.02)
    shares = [coverage(d.box, center, V_portrait) for d in framed]
    assert min(shares) > 0.25 and abs(shares[0] - shares[1]) < 0.05
    # Below the pair floor, even the two most confident people split up.
    assert [d.track_id for d in fit(people, 0.2, 0.7, relaxed=0.5, pair=0.25)[1]] == [1]


def test_a_pair_that_only_barely_fits_is_adopted_and_kept_despite_jitter():
    # In a 0.333 crop the right person's box jitters so the pair fits on every
    # other frame; one dropout must neither reset the switch timer nor drop the
    # pair once it is framed.
    left = det(1, 0.00, 0.53, 0.82)

    def run(**options):
        group = GroupTimeline(join=0, **options)
        for k in range(30):
            people = [left]
            if k:  # the right person appears after the first frame
                right = det(2, 0.70, 1.00, 0.78) if k % 2 else det(2, 0.69, 0.99, 0.78)
                people.append(right)
            group.add(k / 10, people, 0.333)
        return [s[2] for s in group.samples]

    ids = run()
    assert ids[:5] == [(1,)] * 5
    assert set(ids[10:]) == {(1, 2)}
    # Without the grace period every dropout restarts the delay and the pair
    # is never reached.
    assert set(run(grace=0)) == {(1,)}


def test_dedupe_merges_one_person_split_across_track_ids():
    first = det(1, 0.45, 1.00, 0.68)
    twin = det(3, 0.53, 1.00, 0.75)  # the same person under a new track ID
    other = det(2, 0.00, 0.47, 0.70)
    assert [d.track_id for d in dedupe([first, twin, other])] == [3, 2]
    # The ID already framed is kept, whatever the confidences are.
    assert [d.track_id for d in dedupe([first, twin, other], prefer=(1,))] == [1, 2]
    # People who merely stand close together are not merged.
    assert len(dedupe([det(1, 0.0, 0.5), det(2, 0.45, 0.95)])) == 2


def test_a_person_the_tracker_keeps_renumbering_still_joins_the_group():
    # The right person gets a new track ID on every detection. Without
    # merging, each renumbering changed the "better group" and restarted the
    # switch delay, so the camera stayed on the left person alone.
    group = GroupTimeline()
    left = det(1, 0.00, 0.47, 0.85)
    for k in range(30):
        people = [left]
        if k:  # the right person appears after the first frame
            people.append(det(2 + k % 2, 0.57, 1.00, 0.75))
        group.add(k / 10, people, 0.334)
    ids = [s[2] for s in group.samples]
    assert ids[:5] == [(1,)] * 5
    # Adopted once, after the join and switch delays, and then kept.
    assert len({len(i) for i in ids[15:]}) == 1 and len(ids[-1]) == 2
    assert len(set(ids[15:])) == 1


def test_only_the_two_most_confident_people_get_the_pair_floor():
    # Sharing the seam crop would show under 30% of the third person, who has
    # the stricter 50% bar; the other two are framed without them.
    people = [
        det(1, 0.00, 0.53, 0.90),
        det(2, 0.55, 1.00, 0.85),
        det(3, 0.05, 0.35, 0.60),
    ]
    framed = fit(people, 0.333, 0.7, relaxed=0.5, pair=0.25)[1]
    assert [d.track_id for d in framed] == [1, 2]


def test_group_timeline_frames_a_portrait_pair_by_default():
    group = GroupTimeline(join=0)
    people = [det(1, 0.00, 0.53, 0.82), det(2, 0.55, 1.00, 0.78)]
    for k in range(10):
        group.add(k / 10, people, 0.333)
    assert {s[2] for s in group.samples} == {(1, 2)}


def test_relaxation_stops_at_the_relaxed_floor():
    # Below 50% of the 0.80 person there is no crop that holds both.
    assert [d.track_id for d in fit([LEFT, RIGHT], 0.5, 0.7, relaxed=0.5)[1]] == [1]
    assert [d.track_id for d in fit([LEFT, RIGHT], 0.55, 0.7, relaxed=0.5)[1]] == [1, 2]


def test_a_confident_group_beats_more_but_weaker_people():
    strong = [det(1, 0.30, 0.40, 0.9), det(2, 0.44, 0.54, 0.85)]
    weak = [
        det(3, 0.60, 0.70, 0.55),
        det(4, 0.72, 0.82, 0.55),
        det(5, 0.84, 0.94, 0.55),
    ]
    framed = fit(strong + weak, V, 0.7)[1]
    assert [d.track_id for d in framed] == [1, 2]


def test_confidence_noise_does_not_change_the_group():
    held = [det(1, 0.30, 0.40, 0.90), det(2, 0.44, 0.54, 0.85)]
    other = [det(3, 0.30, 0.40, 0.92), det(4, 0.44, 0.54, 0.86)]
    assert not clearly_better(other, held)
    assert clearly_better([det(3, 0.3, 0.4, 0.99)], [det(1, 0.3, 0.4, 0.7)])
    assert clearly_better(held + other, held)  # more people at equal confidence


def test_replay_redecides_the_group_for_a_wider_crop():
    group = GroupTimeline(join=0)
    for k in range(10):
        group.add(k / 10, [LEFT, RIGHT, MIDDLE], 0.1)
    # A very narrow crop holds only the most confident person.
    assert {s[2] for s in group.samples} == {(1,)}
    rebuilt = group.replay(0.973)
    assert [r[0] for r in rebuilt] == [k / 10 for k in range(10)]
    assert {s[2] for s in group.samples} == {(1, 2, 3)}
    assert group.visible == 0.973
    # The retained inputs survive, so a second resize can replay again.
    group.replay(0.1)
    assert {s[2] for s in group.samples} == {(1,)}


def drive_controller(
    monkeypatch, tmp_path, managed, width_at, mpv=None, seconds=6, **run_options
):
    """Run the controller on a fake clock over three people; return telemetry.

    ``width_at(t)`` gives the window width in a 1920 px wide source and ``mpv``
    records the commands sent to the player.
    """
    now = [100.0]

    class Clock:
        def is_set(self):
            return False

        def set(self):
            pass

        def clear(self):
            pass

        def wait(self, timeout):
            now[0] += max(timeout, 1e-3)

    def set_align(value, **_):
        ipc.sent_count += 1
        ipc.last_sent = now[0]
        return True

    ipc = SimpleNamespace(
        managed=managed,
        mpv=mpv,
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

    def snapshot():
        t = now[0] - 100
        w = width_at(t)
        if hasattr(managed, "schedule"):
            managed.schedule(t)
        return {
            "path": __file__,
            "time-pos": t,
            "speed": 1,
            "display-fps": 60,
            "container-fps": 30,
            "video-params": {"rotate": 0},
            "osd-dimensions": dict(
                w=w, h=1080, ml=-(1920 - w) // 2, mr=-(1920 - w) // 2, mt=0, mb=0
            ),
        }, 0

    ipc.snapshot = snapshot
    produced = [0]

    def update(path, generation, t, speed, rotation):
        # The detector runs 2 s ahead of playback at 10 Hz.
        while produced[0] / 10 <= t + 2:
            dets = [LEFT, RIGHT, MIDDLE]
            worker.output.put((generation, produced[0] / 10, dets, False, False))
            produced[0] += 1

    worker = SimpleNamespace(
        start=lambda: None,
        close=lambda: None,
        suspend=lambda: None,
        update=update,
        output=Queue(),
        stats={"sample_rate": 10, "decoder_rtf": 20, "behind": 0},
        error=None,
    )
    monkeypatch.setattr(controller.time, "perf_counter", lambda: now[0])
    monkeypatch.setattr(controller, "frame_timer", nullcontext)
    monkeypatch.setattr(controller, "Worker", lambda *args: worker)
    controller.run(
        None,
        None,
        seconds=seconds,
        ipc=ipc,
        output_dir=tmp_path,
        stop_event=Event(),
        **run_options,
    )
    return [
        json.loads(line)
        for line in (tmp_path / "telemetry.jsonl").read_text().splitlines()
    ]


def test_controller_reframes_the_group_when_the_window_is_resized(
    monkeypatch, tmp_path
):
    managed = SimpleNamespace(mode="group", progress=0, state=lambda *a: None)
    # The source is 1920 px wide; the window widens from 192 to 1500 px.
    rows = drive_controller(
        monkeypatch, tmp_path, managed, lambda t: 192 if t < 3 else 1500
    )
    before = [r for r in rows if 2 <= r["time_pos"] < 3]
    after = [r for r in rows if 3.3 <= r["time_pos"] < 5]
    assert before and after
    assert {tuple(r["group_ids"]) for r in before} == {(1,)}
    # The 2 s of detections queued at the narrow width are re-decided at once.
    assert {tuple(r["group_ids"]) for r in after} == {(1, 2, 3)}


def test_group_options_reach_the_controller_timeline(monkeypatch, tmp_path):
    # A crop 0.2 of the video wide: by default the 0.85 and 0.52 people share
    # it at the relaxed floors; strict floors leave only the most confident.
    def framed(name, **options):
        managed = SimpleNamespace(mode="group", progress=0, state=lambda *a: None)
        rows = drive_controller(
            monkeypatch, tmp_path / name, managed, lambda t: 384, **options
        )
        return {tuple(r["group_ids"]) for r in rows if r["time_pos"] > 3}

    assert framed("default") == {(1, 3)}
    strict = {"group_options": {"relaxed_floor": 0.7, "pair_floor": 0.7}}
    assert framed("strict", **strict) == {(1,)}


def test_group_delays_reach_the_controller_timeline(monkeypatch, tmp_path):
    # With no join or switch delay and detections always present, the group is
    # complete from the first sample.
    managed = SimpleNamespace(mode="group", progress=0, state=lambda *a: None)
    rows = drive_controller(
        monkeypatch,
        tmp_path,
        managed,
        lambda t: 1500,
        group_options={"join": 0, "switch": 0, "leave": 0},
    )
    assert {tuple(r["group_ids"]) for r in rows if r["time_pos"] > 0.5} == {(1, 2, 3)}


def test_cli_passes_and_validates_group_settings(monkeypatch, tmp_path):
    from reframe import __main__ as cli
    from reframe import managed

    seen = []
    monkeypatch.setattr(cli, "validate_backend_runtime", lambda _: None)
    monkeypatch.setattr(managed, "run_managed", seen.append)
    base = [
        "reframe", "attach", "--managed", "--pipe", "p", "--player-pid", "1",
        "--session-id", "s", "--lua-client", "reframe",
        "--backend-dir", str(tmp_path), "--output-dir", str(tmp_path / "out"),
    ]  # fmt: skip

    def parse(*extra):
        monkeypatch.setattr(sys, "argv", base + list(extra))
        cli.main()
        return seen[-1]

    args = parse()
    assert (args.group_relaxed_floor, args.group_pair_floor) == (0.5, 0.2)
    assert (args.group_join, args.group_leave, args.group_switch) == (0.3, 0.7, 0.6)
    args = parse(
        "--group-relaxed-floor", "0.4", "--group-pair-floor", "0.1",
        "--group-join", "0", "--group-leave", "1.5", "--group-switch", "30",
    )  # fmt: skip
    assert (args.group_relaxed_floor, args.group_pair_floor) == (0.4, 0.1)
    assert (args.group_join, args.group_leave, args.group_switch) == (0, 1.5, 30)
    for bad in (
        ("--group-relaxed-floor", "0"),
        ("--group-relaxed-floor", "1.5"),
        ("--group-pair-floor", "nan"),
        ("--group-join", "-1"),
        ("--group-leave", "31"),
        ("--group-switch", "soon"),
    ):
        with pytest.raises(SystemExit) as error:
            parse(*bad)
        assert error.value.code == 2


def test_controller_draws_and_clears_the_debug_overlay_when_toggled(
    monkeypatch, tmp_path
):
    managed = SimpleNamespace(
        mode="group", debug=False, progress=0, state=lambda *a: None
    )
    # Switched on at 2 s and off again at 4 s, as by the Lua key binding.
    managed.schedule = lambda t: setattr(managed, "debug", 2 <= t < 4)
    calls = []
    mpv = SimpleNamespace(command_async=lambda *args: calls.append(args))
    drive_controller(monkeypatch, tmp_path, managed, lambda t: 608, mpv=mpv)
    kinds = [c[2] for c in calls]
    assert kinds and kinds[0] == "ass-events"
    # One removal after the last drawing, and nothing drawn once it is off.
    assert kinds.count("none") == 1 and kinds[-1] == "none"
    assert set(kinds[:-1]) == {"ass-events"}
    # The boxes carry their confidence.
    assert any("0.85" in c[3] and "0.80" in c[3] for c in calls[:-1])
    # Without the debug flag nothing is drawn at all.
    calls.clear()
    managed.schedule = lambda t: None
    managed.debug = False
    drive_controller(monkeypatch, tmp_path / "off", managed, lambda t: 608, mpv=mpv)
    assert calls == []
