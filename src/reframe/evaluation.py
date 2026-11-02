"""Independent render-quality metrics: ground truth versus mpv OSD margins."""

from bisect import bisect_right

import numpy as np


def truth_at(truth, t):
    frames = truth["frames"]
    times = [f["t"] for f in frames]
    return frames[max(0, min(len(frames) - 1, bisect_right(times, t) - 1))]


def score(rows, truth, speed):
    """Return measured values and explicit gates, never inferring missing evidence."""
    errors = []
    contained = []
    static = []
    motion = []
    valid = []
    for row in rows:
        t = row["time_pos"]
        frame = truth_at(truth, t)
        box = frame["primary"]
        scaled = row["w"] - row["ml"] - row["mr"]
        if scaled <= row["w"]:
            continue
        center = (row["w"] / 2 - row["ml"]) / scaled
        left, right = -row["ml"] / scaled, (row["w"] - row["ml"]) / scaled
        item = {**row, "center": center, "gt_center": None, "error": None}
        if box is not None:
            gt = (box[0] + box[2]) / 2
            reachable = min(
                1 - row["w"] / (2 * scaled), max(row["w"] / (2 * scaled), gt)
            )
            item.update(gt_center=reachable, error=abs(center - reachable))
            excluded = (
                any(event <= t < event + 1 for event in [0, 30, 45, 50, 55])
                or row.get("since_seek", 100) < 1
            )
            if not excluded:
                errors.append(item["error"])
                if box[2] - box[0] < row["w"] / scaled:
                    contained.append(
                        box[0] >= left - 1 / scaled and box[2] <= right + 1 / scaled
                    )
        if 0 <= t < 8:
            static.append(row["align"])
        motion.append((t, center))
        valid.append(item)
    variation = (
        sum(abs(b - a) for a, b in zip(static, static[1:])) if len(static) > 1 else None
    )
    lags = []
    for onset in [8, 20]:
        window = [p for p in motion if onset - 1 <= p[0] <= onset + 1]
        if len(window) < 3:
            continue
        # At 20 s motion reverses: continuing the old rightward pan does not
        # anticipate the new motion. Require velocity in the new GT direction.
        direction = 1 if onset == 8 else -1
        distinct = []
        for point in window:
            if not distinct or point[0] > distinct[-1][0]:
                distinct.append(point)
        movement = next(
            (
                b[0]
                for a, b in zip(distinct, distinct[1:])
                if direction * (b[1] - a[1]) / (b[0] - a[0]) > 0.004
            ),
            None,
        )
        if movement is not None:
            lags.append(movement - onset)
    median = float(np.median(errors)) if errors else None
    p95 = float(np.percentile(errors, 95)) if errors else None
    containment = float(np.mean(contained)) if contained else None
    limit = 0.12 if speed == 4 else 0.08
    gates = {
        "follow_median": None if median is None else median <= 0.03,
        "follow_p95": None if p95 is None else p95 <= limit,
        "containment": None if containment is None else containment >= 0.98,
        "static_stability": None if variation is None else variation <= 0.01,
        "anticipation": None if len(lags) != 2 else max(lags) <= 0,
    }
    return {
        "median_error": median,
        "p95_error": p95,
        "containment": containment,
        "static_align_total_variation": variation,
        "onset_lags": lags,
        "measured_samples": len(errors),
        "gates": gates,
    }, valid


def score_identity(telemetry, truth):
    """Count person changes using GT box association, separately from raw-ID churn.

    The plan explicitly allows adopting a replacement tracker ID for the same
    person. Such an adoption must not be confused with following the distractor.
    Ambiguous overlap is not assigned an identity; disjoint samples on both sides
    of the crossing are required to prove continuity.
    """
    from .timeline import iou

    crossing = [
        r
        for r in telemetry
        if 32 <= r["time_pos"] < 45 and r.get("target_id") is not None
    ]
    raw_changes = sum(
        a["target_id"] != b["target_id"]
        for a, b in zip(crossing, crossing[1:])
        if a["gen"] == b["gen"]
    )
    labels = []
    for row in crossing:
        box = row.get("target_box")
        gt = truth_at(truth, row["time_pos"])
        if box is None or gt["primary"] is None or gt["secondary"] is None:
            continue
        # At substantial overlap the visible silhouette can fit either box.
        # Require correct identity on both separated sides; retain raw ID churn.
        if iou(gt["primary"], gt["secondary"]) > 0.3:
            continue
        primary, secondary = iou(box, gt["primary"]), iou(box, gt["secondary"])
        if max(primary, secondary) < 0.3 or abs(primary - secondary) < 0.15:
            continue
        labels.append(
            (row["time_pos"], "primary" if primary > secondary else "secondary")
        )
    coverage = (
        bool(labels)
        and min(t for t, _ in labels) <= 33
        and max(t for t, _ in labels) >= 44
    )
    switches = sum(a[1] != b[1] for a, b in zip(labels, labels[1:]))
    wrong = sum(label != "primary" for _, label in labels)
    return {
        "raw_track_id_changes": raw_changes,
        "target_person_switches": switches,
        "distractor_samples": wrong,
        "identity_samples": len(labels),
        "pass": switches == 0 and wrong == 0 if coverage else None,
    }


def score_group(rows, truth, floor=0.7, settle=1.0):
    """Measure group framing against ground-truth person boxes.

    ``rows`` holds ``{"t", "center", "visible"}`` camera samples and ``truth``
    maps each ``t`` to the list of true person boxes. A sample is optimal when
    the camera frames (at ``floor`` box coverage) as many people as the best
    possible crop position could. Samples within ``settle`` seconds of a change
    in the best achievable head-count are excluded: the camera is still panning.
    """
    from types import SimpleNamespace

    from .group import coverage, fit

    results = []
    for row in rows:
        people = [
            SimpleNamespace(track_id=i, box=box, confidence=1.0)
            for i, box in enumerate(truth[row["t"]])
        ]
        best = fit(people, row["visible"], floor)
        best_count = len(best[1]) if best else 0
        framed = sum(
            coverage(p.box, row["center"], row["visible"]) >= floor - 1e-6
            for p in people
        )
        results.append((row["t"], best_count, framed))
    changes = [b[0] for a, b in zip(results, results[1:]) if a[1] != b[1]]
    scored = [
        r
        for r in results
        if not any(change <= r[0] < change + settle for change in changes)
    ]
    optimal = sum(framed >= best for _, best, framed in scored)
    return {
        "samples": len(scored),
        "optimal_fraction": optimal / len(scored) if scored else None,
        "max_framed_deficit": max((b - f for _, b, f in scored), default=0),
    }
