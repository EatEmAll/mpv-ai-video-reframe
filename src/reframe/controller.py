"""Connect playback events to generation-local inference and camera planning."""

import logging
import os
import time
from pathlib import Path
from queue import Empty

from .clock import PlaybackClock, PresentationClock
from .decoder import Worker
from .framing import box_at, contain_center
from .geometry import align_for_center
from .group import MODES, GroupTimeline, contain_group, framed_at, union
from .ipc import IPC, ViewChanged
from .metrics import Telemetry
from .motion import CameraMotion
from .overlay import make_overlay
from .planner import Planner
from .timeline import Timeline
from .timing import frame_timer


def run(
    pipe,
    detector,
    rate=10,
    debug_osd=False,
    seconds=None,
    stop_event=None,
    output_dir=None,
    ipc=None,
    mode="follow",
    group_conf_floor=0.5,
    group_coverage_floor=0.7,
):
    if mode not in MODES:
        raise ValueError(f"Unknown framing mode: {mode}")
    ipc = ipc or IPC(pipe)
    managed = ipc.managed

    def new_group():
        return GroupTimeline(group_conf_floor, group_coverage_floor)

    worker = Worker(detector, rate)
    worker.start()
    generation = None
    timeline = Timeline()
    planner = Planner()
    # Both modes consume every detection so a toggle never re-selects the
    # follow target or starts group framing from an empty history.
    group = new_group()
    group_planner = Planner()
    motion = CameraMotion()
    started = time.perf_counter()
    directory = (
        Path(output_dir)
        if output_dir
        else Path("runs") / time.strftime("%Y%m%d-%H%M%S")
    )
    last_key = None
    clock = PlaybackClock()
    presentation = PresentationClock()
    next_tick = time.perf_counter()
    cadence = 60.0
    suspended = False
    resume_epoch = 0
    validated_path = None
    validated_source = None
    arrivals = 0
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with Telemetry(directory / "telemetry.jsonl") as log, frame_timer():
            while (
                not ipc.closed.is_set()
                and not (stop_event and stop_event.is_set())
                and (seconds is None or time.perf_counter() - started < seconds)
            ):
                now = time.perf_counter()
                if now < next_tick:
                    # Property changes and crop acknowledgements can make a
                    # blocked output ready before its retry deadline.
                    ipc.changed.wait(min(0.02, next_tick - now))
                now = time.perf_counter()
                if managed:
                    managed.progress = now
                # Retry readiness more often than the output ceiling. A second
                # frame-rate timer here used to skip whole frames when its phase
                # differed slightly from the IPC sender's actual send timestamp.
                next_tick = now + min(0.02, 1 / (cadence * 4))
                ipc.changed.clear()
                if worker.error:
                    raise RuntimeError(worker.error)
                if ipc.error:
                    raise RuntimeError(ipc.error)
                state, gen = ipc.snapshot()
                display_fps = state.get("display-fps") or 60
                video_fps = (
                    state.get("estimated-vf-fps")
                    or state.get("container-fps")
                    or display_fps
                )
                cadence = max(
                    0.01, min(display_fps, video_fps * (state.get("speed") or 1))
                )
                path = state.get("path")
                t = state.get("time-pos")
                dims = state.get("osd-dimensions")
                params = state.get("video-params") or {}
                if not path or t is None or not dims or state.get("seeking"):
                    if not path or t is None:
                        worker.suspend()
                        if managed:
                            ipc.restore()
                            managed.state("waiting")
                    continue
                source = (path, state.get("working-directory"))
                if source != validated_source:
                    path = os.path.abspath(
                        os.path.join(
                            state.get("working-directory") or os.getcwd(), path
                        )
                    )
                    if not Path(path).is_file():
                        if managed:
                            worker.suspend()
                            ipc.restore()
                            managed.state("suspended", "Local video files only")
                            continue
                        raise ValueError(f"Local files only; cannot open {path}")
                    validated_path = path
                    validated_source = source
                else:
                    path = validated_path
                rotation = int(params.get("rotate", 0)) % 360
                if (
                    rotation not in (0, 90, 180, 270)
                    or state.get("video-zoom", 0) != 0
                    or state.get("video-unscaled") not in (False, "no", None)
                ):
                    if not suspended:
                        logging.getLogger(__name__).warning(
                            "Reframing suspended: requires right-angle rotation, video-zoom=0 and video-unscaled=no"
                        )
                        worker.suspend()
                        ipc.restore()
                        suspended = True
                    if managed:
                        managed.state(
                            "suspended", "Unsupported zoom, rotation or unscaled mode"
                        )
                    continue
                if managed and (
                    not params or (state.get("current-tracks/video") or {}).get("image")
                ):
                    worker.suspend()
                    ipc.restore()
                    managed.state("suspended", "No video track")
                    continue
                if dims["w"] <= 0 or dims["h"] <= 0:
                    continue
                if suspended or (managed and not ipc.enabled):
                    resume_epoch += 1
                    try:
                        ipc.enable()
                    except ViewChanged:
                        continue
                    suspended = False
                if managed:
                    managed.state("active")
                epoch = (gen, path, rotation, resume_epoch)
                if generation != epoch:
                    generation = epoch
                    timeline = Timeline()
                    planner = Planner()
                    group = new_group()
                    group_planner = Planner()
                    motion = CameraMotion()
                    last_key = None
                    arrivals = 0
                visible = dims["w"] / max(1, dims["w"] - dims["ml"] - dims["mr"])
                worker.update(path, generation, t, state.get("speed") or 1, rotation)
                while True:
                    try:
                        sample_gen, ts, dets, cut, resync = worker.output.get_nowait()
                    except Empty:
                        break
                    if sample_gen != generation:
                        continue
                    if ts is None:
                        count = len(timeline.samples)
                        timeline.finalize()
                        for sample in timeline.samples[count:]:
                            planner.add(sample[0], sample[1])
                        continue
                    arrivals += 1
                    if resync:
                        timeline = Timeline()
                        planner = Planner()
                        group = new_group()
                        group_planner = Planner()
                        last_key = None
                    previous_x = timeline.last.center if timeline.last else None
                    sample_count = len(timeline.samples)
                    raw = timeline.add(ts, dets, cut=cut)
                    if raw is not None:
                        for new_sample in timeline.samples[sample_count:]:
                            planner.add(
                                new_sample[0],
                                new_sample[1],
                                cut
                                and previous_x is not None
                                and abs(new_sample[1] - previous_x) > 0.25,
                            )
                    # Without a framable group, aim where follow mode would.
                    if timeline.samples and timeline.samples[-1][0] == ts:
                        fallback = timeline.samples[-1][1]
                    else:
                        early = timeline.preview()
                        fallback = early[0][1] if early else None
                    group_previous = group.samples[-1][1] if group.samples else None
                    group_sample = group.add(
                        ts, dets, visible, cut=cut, fallback=fallback
                    )
                    group_planner.add(
                        group_sample[0],
                        group_sample[1],
                        cut
                        and group_previous is not None
                        and abs(group_sample[1] - group_previous) > 0.25,
                    )
                timeline.prune(t)
                group.prune(t)
                active_mode = getattr(managed, "mode", None) or mode
                grouped = active_mode == "group" and bool(group.samples)
                # Queue draining/planning must not date this camera step with
                # the timestamp from before detector results were consumed.
                now = time.perf_counter()
                media_t = clock.query(
                    t,
                    now,
                    state.get("speed") or 1,
                    bool(state.get("pause")),
                    generation,
                )
                key = (
                    media_t,
                    dims["w"],
                    dims["h"],
                    dims["ml"],
                    dims["mr"],
                    len(timeline.samples),
                    len(timeline.pending),
                    len(group.samples),
                    active_mode,
                    gen,
                    state.get("video-align-x"),
                )
                if key == last_key:
                    continue
                if now < ipc.last_sent + 1 / cadence:
                    next_tick = ipc.last_sent + 1 / cadence
                    continue
                fps = state.get("display-fps") or 60
                query_t = presentation.query(
                    media_t,
                    now,
                    lead=(state.get("speed") or 1) * (2 / fps + ipc.latency_seconds),
                    speed=state.get("speed") or 1,
                    paused=bool(state.get("pause")),
                    generation=generation,
                )
                preview = timeline.preview()
                if not timeline.samples and preview is None and not grouped:
                    continue
                w = dims["w"]
                scaled = w - dims["ml"] - dims["mr"]
                # Query both planners every tick: a planner left idle keeps a
                # stale dead-zone anchor and would offset the first frames
                # after a mode switch.
                follow_x = (
                    planner.query(query_t)
                    if timeline.samples
                    else (preview[0][1] if preview else None)
                )
                group_x = group_planner.query(query_t) if group.samples else None
                if grouped:
                    framed = framed_at(group, media_t)
                    box = union(framed)
                    x = contain_group(group_x, framed, w / scaled, group.coverage_floor)
                    active_planner = group_planner
                else:
                    box = box_at(timeline, media_t)
                    # Keep crop-fill fixed at panscan=1 (set by IPC.enable).
                    # A box wider than the viewport must remain cropped;
                    # shrinking the image would violate the full-height
                    # playback requirement.
                    x = contain_center(follow_x, box, w / scaled)
                    active_planner = planner
                target = x
                # A mode switch is not a cut: CameraMotion pans to the new
                # target under its normal speed and acceleration limits.
                cut = motion.time is not None and any(
                    motion.time < ts <= query_t
                    for ts, _, is_cut in active_planner.samples
                    if is_cut
                )
                x = motion.query(target, query_t, snap=cut or bool(state.get("pause")))
                align = align_for_center(x, w, scaled)
                # Suppress subpixel noise rather than a fixed align step which
                # becomes several visible pixels in a large overflow crop.
                tolerance = min(0.002, 0.5 / max(1, scaled - w))
                force = abs(align - (state.get("video-align-x") or 0)) > tolerance
                before_sent = ipc.sent_count
                accepted = ipc.set_align(
                    align, force=force, min_interval=1 / cadence, tolerance=tolerance
                )
                if accepted:
                    last_key = key
                sample = (
                    min(timeline.samples, key=lambda s: abs(s[0] - t))
                    if timeline.samples
                    else (preview[0] if preview else None)
                )
                group_sample = (
                    min(group.samples, key=lambda s: abs(s[0] - t))
                    if group.samples
                    else None
                )
                group_info = group.info.get(group_sample[0], {}) if group_sample else {}
                record = {
                    "wall": time.time(),
                    "monotonic": time.perf_counter(),
                    "pan_sent_at": ipc.last_sent
                    if ipc.sent_count > before_sent
                    else None,
                    "query_time": query_t,
                    "detector_arrivals": arrivals,
                    "time_pos": t,
                    "camera_time": media_t,
                    "speed": state.get("speed"),
                    "paused": bool(state.get("pause")),
                    "x_smooth": x,
                    "x_target": target,
                    "camera_velocity": motion.velocity,
                    "panscan": state.get("panscan"),
                    "framing_box": box,
                    "x_raw": sample[1] if sample else None,
                    "align": align,
                    "sent": ipc.sent_count > before_sent,
                    "pan_blocked": not accepted,
                    "align_applied": state.get("video-align-x"),
                    "rotation": rotation,
                    "detector_model": str(getattr(detector, "model_path", "unknown")),
                    "W": w,
                    "H": dims["h"],
                    "mt": dims["mt"],
                    "mb": dims["mb"],
                    "S": scaled,
                    "ml": dims["ml"],
                    "gen": gen,
                    "target_id": sample[2] if sample else None,
                    "target_box": (
                        timeline.boxes.get(sample[0])
                        if timeline.samples
                        else (preview[1] if preview else None)
                    ),
                    "state": (
                        group_sample[3]
                        if grouped
                        else (sample[3] if sample else "buffering")
                    ),
                    "mode": active_mode,
                    "group_ids": list(group_sample[2]) if group_sample else [],
                    "group_box": group.boxes.get(group_sample[0])
                    if group_sample
                    else None,
                    "group_framed": list(group.framed.get(group_sample[0], ()))
                    if group_sample
                    else [],
                    "group_candidates": group_info.get("candidates", 0),
                    "group_unframed": list(group_info.get("unframed", ())),
                    "lookahead": timeline.samples[-1][0] - t if timeline.samples else 0,
                    "update_ceiling_hz": cadence,
                    "ipc_latency_ms": ipc.latency_seconds * 1000,
                    **worker.stats,
                }
                log.write(record)
                arrivals = 0
                if ipc.sent_count > before_sent:
                    next_tick = ipc.last_sent + 1 / cadence
                elif accepted:
                    # Stable/subpixel targets still need only one HUD/log update
                    # per output frame, not four retries per frame.
                    next_tick = time.perf_counter() + 1 / cadence
                else:
                    # A late acknowledgement is not a stable/subpixel target.
                    # Sleeping a whole frame here doubled pan intervals when
                    # replies arrived just after the nominal frame deadline.
                    next_tick = time.perf_counter() + min(0.02, 1 / (cadence * 4))
                if debug_osd:
                    ipc.mpv.command_async(
                        "osd-overlay",
                        0,
                        "ass-events",
                        make_overlay(record, dims),
                        dims["w"],
                        dims["h"],
                    )
    finally:
        try:
            worker.close()
        finally:
            ipc.close()
