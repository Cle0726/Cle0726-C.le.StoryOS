from __future__ import annotations

from pathlib import Path

import pytest

import storyos.atomic_io as atomic_io
from storyos.atomic_io import AtomicWriteError, atomic_create_text, atomic_replace_text


def _temps(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if ".storyos-" in path.name)


def test_atomic_create_publishes_complete_file_and_refuses_overwrite(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    destination = project / "staging" / "reviews" / "review.yaml"

    atomic_create_text(project, destination, "first\n")
    assert destination.read_text(encoding="utf-8") == "first\n"
    assert _temps(destination.parent) == []

    with pytest.raises(FileExistsError):
        atomic_create_text(project, destination, "second\n")

    assert destination.read_text(encoding="utf-8") == "first\n"
    assert _temps(destination.parent) == []


def test_atomic_replace_keeps_old_file_when_publish_fails(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    destination = project / "staging" / "reviews" / "review.yaml"
    destination.parent.mkdir(parents=True)
    destination.write_text("old\n", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(AtomicWriteError, match="simulated replace failure"):
        atomic_replace_text(project, destination, "new\n")

    assert destination.read_text(encoding="utf-8") == "old\n"
    assert _temps(destination.parent) == []


def test_atomic_writer_rejects_symlinked_parent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    staging = project / "staging"
    try:
        staging.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable in this test environment: {exc}")

    with pytest.raises(AtomicWriteError, match="symlink"):
        atomic_create_text(project, staging / "reviews" / "review.yaml", "unsafe\n")

    assert not (outside / "reviews").exists()
