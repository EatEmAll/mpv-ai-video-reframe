import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from summarize_windows_matrix import basename, summarize, verified_gates  # noqa: E402


def test_absent_measurements_never_pass(tmp_path):
    (tmp_path / "commands.json").write_text("[]")
    result = summarize(tmp_path, 2)
    assert not result["complete"]
    assert not result["all_required_pass"]
    with pytest.raises(ValueError):
        summarize(tmp_path, 0)


def test_failed_run_without_report_is_retained(tmp_path):
    commands = [
        {"command": ["python", "verify", "--output", "runs/partial"], "exit": 1}
    ]
    (tmp_path / "commands.json").write_text(json.dumps(commands))
    result = summarize(tmp_path, 2)
    assert result["missing"] == ["partial"]
    assert not result["all_required_pass"]


def test_windows_paths_are_not_published_as_names():
    assert basename(r"C:\private\model.pt") == "model.pt"
    assert basename("/private/model.pt") == "model.pt"


def test_missing_required_gates_never_become_a_verified_result():
    assert not verified_gates({})
    assert not verified_gates({"frame_health": True})
