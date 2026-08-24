from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from storyos.project import StoryProject
from storyos.project_authoring import (
    ProjectAuthoringError,
    create_manuscript,
    create_project,
)
from storyos.workspace import AuthoringWorkspace


def test_create_project_initializes_openable_storyos_structure_without_touching_existing_files(tmp_path):
    root = tmp_path / "novel"
    root.mkdir()
    existing = root / "notes.txt"
    existing.write_text("keep me", encoding="utf-8")

    result = create_project(root, name="断弦之歌", language="zh-CN")

    assert result["schema"] == "story.project-create.v1"
    assert result["project"]["name"] == "断弦之歌"
    assert result["policy"]["create_only"] is True
    assert existing.read_text(encoding="utf-8") == "keep me"
    manifest = yaml.safe_load((root / "storyos.yaml").read_text(encoding="utf-8"))
    assert manifest["schema"] == "story.project.v1"
    assert manifest["paths"]["manuscript"] == "manuscript"
    for relative in (
        "manuscript",
        "entities",
        "events",
        "canon",
        "staging/claims",
        "staging/reviews",
        "staging/materialization/events",
        "staging/materialization/facts",
    ):
        assert (root / relative).is_dir()

    project = StoryProject.open(root)
    snapshot = AuthoringWorkspace().build_snapshot(project)
    assert snapshot["summary"]["manuscripts"] == 0
    assert snapshot["summary"]["entities"] == 0


def test_create_project_never_overwrites_existing_manifest(tmp_path):
    root = tmp_path / "novel"
    root.mkdir()
    manifest = root / "storyos.yaml"
    manifest.write_text("existing: true\n", encoding="utf-8")

    with pytest.raises(ProjectAuthoringError, match="已经包含 storyos.yaml"):
        create_project(root, name="Should not overwrite")

    assert manifest.read_text(encoding="utf-8") == "existing: true\n"


def test_create_project_requires_existing_non_symlink_directory(tmp_path):
    with pytest.raises(ProjectAuthoringError, match="已经存在"):
        create_project(tmp_path / "missing", name="Missing")

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ProjectAuthoringError, match="符号链接"):
        create_project(link, name="Link")


def test_create_manuscript_creates_empty_working_copy_and_workspace_can_open_it(tmp_path):
    root = tmp_path / "novel"
    root.mkdir()
    create_project(root, name="Novel")
    project = StoryProject.open(root)

    result = create_manuscript(project, title="第一章 雨夜", season=1, episode=1)

    assert result["schema"] == "story.manuscript-create.v1"
    assert result["path"] == "manuscript/S01/EP01_第一章_雨夜.txt"
    assert (root / result["path"]).read_text(encoding="utf-8") == ""
    listing = AuthoringWorkspace().list_manuscripts(project)
    assert [(row["season"], row["episode"], row["title"]) for row in listing] == [
        (1, 1, "第一章_雨夜"),
    ]


def test_create_manuscript_refuses_duplicate_episode_even_with_different_title(tmp_path):
    root = tmp_path / "novel"
    root.mkdir()
    create_project(root, name="Novel")
    project = StoryProject.open(root)
    create_manuscript(project, title="Opening", season=1, episode=1)

    with pytest.raises(ProjectAuthoringError, match="已经存在"):
        create_manuscript(project, title="Different title", season=1, episode=1)

    files = list((root / "manuscript" / "S01").glob("*.txt"))
    assert len(files) == 1


def test_create_manuscript_does_not_confuse_ep01_with_ep010(tmp_path):
    root = tmp_path / "novel"
    root.mkdir()
    create_project(root, name="Novel")
    project = StoryProject.open(root)

    first = create_manuscript(project, title="Ten", season=1, episode=10)
    second = create_manuscript(project, title="One", season=1, episode=1)

    assert first["path"].startswith("manuscript/S01/EP10_")
    assert second["path"].startswith("manuscript/S01/EP01_")
    assert len(list((root / "manuscript" / "S01").glob("*.txt"))) == 2


def test_create_manuscript_sanitizes_cross_platform_filename_characters(tmp_path):
    root = tmp_path / "novel"
    root.mkdir()
    create_project(root, name="Novel")
    project = StoryProject.open(root)

    result = create_manuscript(project, title='A/B:C*D? "E"', season=2, episode=3)

    relative = Path(result["path"])
    assert relative.parts[1] == "S02"
    assert relative.name.startswith("EP03_")
    assert all(char not in relative.name for char in '<>:"/\\|?*')
