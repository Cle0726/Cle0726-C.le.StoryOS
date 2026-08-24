from __future__ import annotations

from pathlib import Path

import pytest

from storyos.file_lock import ProjectFileLockError, project_file_lock


def test_project_file_lock_preserves_body_exception(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(OSError, match="business failure"):
        with project_file_lock(project, "test"):
            raise OSError("business failure")


def test_project_file_lock_rejects_storyos_symlink_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    try:
        (project / ".storyos").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable in this test environment: {exc}")

    with pytest.raises(ProjectFileLockError, match="symlink"):
        with project_file_lock(project, "canon-commit"):
            pass

    assert not (outside / "locks").exists()
