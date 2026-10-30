import json

import pytest

from reframe.metrics import Telemetry


def test_telemetry_flushes_and_closes_during_failure(tmp_path):
    path = tmp_path / "[literal]" / "updates.jsonl"
    with pytest.raises(RuntimeError), Telemetry(path) as log:
        log.write({"time_pos": 1, "state": "tracking"})
        raise RuntimeError("disconnect")
    assert json.loads(path.read_text()) == {"time_pos": 1, "state": "tracking"}
    with path.open("a") as output:
        output.write("")


def test_nonfinite_measurements_cannot_silently_become_valid_json(tmp_path):
    path = tmp_path / "bad.jsonl"
    with Telemetry(path) as log, pytest.raises(ValueError):
        log.write({"x_smooth": float("nan")})
    assert path.read_text() == ""
