"""Open a sampled shadow stream, using NVDEC for large supported sources."""

import logging

import av
from av.codec.hwaccel import HWAccel


def open_video(path):
    container = av.open(path)
    stream = container.streams.video[0]
    if stream.width >= 2560 and stream.codec_context.name in {
        "h264",
        "hevc",
        "av1",
        "vp9",
    }:
        container.close()
        try:
            import torch

            # Make Torch's primary context current on this decoder thread.
            # FFmpeg can use it without trying to change its scheduling flags.
            if not torch.cuda.is_available():
                raise OSError("CUDA unavailable")
            torch.cuda.set_device(0)
            torch.empty(0, device="cuda")
            container = av.open(
                path,
                hwaccel=HWAccel(
                    "cuda",
                    options={"primary_ctx": "0", "current_ctx": "1"},
                    allow_software_fallback=True,
                    is_hw_owned=True,
                ),
            )
        except (av.FFmpegError, OSError, RuntimeError) as error:
            logging.getLogger(__name__).warning(
                "Hardware decoder unavailable; using software: %s", error
            )
            container = av.open(path)
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 2
    stream.thread_type = "AUTO"
    return container
