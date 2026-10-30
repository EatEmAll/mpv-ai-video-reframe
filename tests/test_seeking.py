from contextlib import nullcontext
from types import SimpleNamespace

import av

from reframe.seeking import seek_frames


class Container:
    name = "fake.ts"

    def __init__(self):
        self.targets = []

    def seek(self, pts, **kwargs):
        self.targets.append(pts)

    def decode(self, stream):
        times = [16.733, 16.766] if self.targets[-1] > 8400 else [8.4, 8.433]
        return iter(SimpleNamespace(time=t) for t in times)


def test_nonzero_start_overshoot_rewinds_instead_of_accepting_next_keyframe(
    monkeypatch,
):
    container = Container()
    stream = SimpleNamespace(time_base=0.001, index=0)
    rewound = SimpleNamespace(
        streams=[stream],
        decode=lambda stream: iter(SimpleNamespace(time=t) for t in [8.4, 8.433]),
    )
    monkeypatch.setattr(av, "open", lambda path: nullcontext(rewound))
    fallbacks = []
    frames = list(
        seek_frames(
            container,
            stream,
            1,
            8.4,
            lambda: fallbacks.append(True),
        )
    )
    assert [round(f.time - 8.4, 3) for f in frames] == [0, 0.033]
    assert len(fallbacks) == 1
    assert container.targets == [9400]
