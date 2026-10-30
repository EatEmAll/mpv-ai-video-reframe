"""PTS seek with a correctness fallback for sparse/nonzero-start demux indexes."""


def seek_frames(container, stream, target, offset, on_fallback=None):
    if target <= 0:
        # Callers open a fresh local container for a new seek generation.
        # Even seeking to start can produce EOF in short sparse-index TS files.
        yield from container.decode(stream)
        return
    container.seek(
        int((target + offset) / stream.time_base), stream=stream, backward=True
    )
    frames = container.decode(stream)
    first = next(frames, None)
    # Some MPEG-TS indexes return the *next* keyframe for an early target.
    # Rewind to the known file start and decode forward rather than emitting a
    # perfectly timestamped frame several seconds away from the requested time.
    if first is None or (
        first.time is not None and first.time - offset > target + 0.001
    ):
        import av

        if on_fallback:
            on_fallback()
        with av.open(container.name) as rewound:
            yield from rewound.decode(rewound.streams[stream.index])
    else:
        yield first
        yield from frames
