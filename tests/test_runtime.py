import pytest

from reframe.runtime import find_mpv


def test_explicit_path_with_spaces(tmp_path):
    path = tmp_path / "a space" / "mpv.exe"
    path.parent.mkdir()
    path.touch()
    assert find_mpv(str(path)) == str(path.resolve())


def test_invalid_override_does_not_fall_back(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_mpv(str(tmp_path / "missing.exe"))
