"""Linux GPU attribution: device handles decide measured zero versus unknown."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verification_resources import LinuxGPUMemory  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux /proc layout")


def process(tmp_path, links, fdinfo=None):
    (tmp_path / "7" / "fd").mkdir(parents=True)
    (tmp_path / "7" / "fdinfo").mkdir()
    for name, target in links.items():
        (tmp_path / "7" / "fd" / name).symlink_to(target)
    for name, text in (fdinfo or {}).items():
        (tmp_path / "7" / "fdinfo" / name).write_text(text)
    return LinuxGPUMemory(7, proc=tmp_path, nvidia_used=lambda: 5)


def test_no_gpu_handle_is_measured_zero(tmp_path):
    assert process(tmp_path, {"0": "/dev/null"}).sample() == 0


def test_drm_client_counted_once_across_descriptors(tmp_path):
    text = "drm-client-id:\t4\ndrm-total-vram0:\t2 MiB\ndrm-total-system0:\t4 KiB\n"
    memory = process(
        tmp_path,
        {"3": "/dev/dri/renderD128", "4": "/dev/dri/renderD128"},
        {"3": text, "4": text},
    )
    assert memory.sample() == 2 * 1024**2 + 4 * 1024


def test_nvidia_handle_uses_nvml(tmp_path):
    assert process(tmp_path, {"3": "/dev/nvidia0"}).sample() == 5


def test_unreadable_drm_statistics_are_not_zero(tmp_path):
    memory = process(tmp_path, {"3": "/dev/dri/card1"}, {"3": "pos: 0\n"})
    with pytest.raises(OSError):
        memory.sample()
