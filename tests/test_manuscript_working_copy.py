import hashlib
from pathlib import Path

import pytest
import yaml

from storyos.manuscript_working_copy import (
    ManuscriptConflictError,
    ManuscriptWorkingCopy,
    ManuscriptWorkingCopyError,
)
from storyos.project import StoryProject


def make_project(tmp_path: Path) -> tuple[StoryProject, Path]:
    (tmp_path / "manuscript").mkdir()
    target = tmp_path / "manuscript" / "EP01.txt"
    target.write_text("原稿\n", encoding="utf-8")
    (tmp_path / "storyos.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "story.project.v1",
                "id": "story_demo",
                "name": "Demo",
                "paths": {"manuscript": "manuscript"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return StoryProject.open(tmp_path), target


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_save_is_atomic_and_reports_new_hash(tmp_path: Path):
    project, target = make_project(tmp_path)
    before = sha256(target)

    result = ManuscriptWorkingCopy().save(
        project,
        "manuscript/EP01.txt",
        expected_sha256=before,
        content="修改后的正文\n第二行\n",
    )

    assert result["status"] == "saved"
    assert result["previous_sha256"] == before
    assert result["sha256"] == sha256(target)
    assert target.read_text(encoding="utf-8") == "修改后的正文\n第二行\n"
    assert result["policy"]["canonical_mutation"] is False
    assert result["policy"]["staging_mutation"] is False


def test_external_change_causes_conflict_instead_of_overwrite(tmp_path: Path):
    project, target = make_project(tmp_path)
    editor_hash = sha256(target)
    target.write_text("外部修改\n", encoding="utf-8")

    with pytest.raises(ManuscriptConflictError):
        ManuscriptWorkingCopy().save(
            project,
            "manuscript/EP01.txt",
            expected_sha256=editor_hash,
            content="编辑器中的旧内容",
        )

    assert target.read_text(encoding="utf-8") == "外部修改\n"


def test_path_escape_is_rejected(tmp_path: Path):
    project, target = make_project(tmp_path)

    with pytest.raises(ManuscriptWorkingCopyError):
        ManuscriptWorkingCopy().save(
            project,
            "../outside.txt",
            expected_sha256=sha256(target),
            content="nope",
        )


def test_unchanged_save_is_idempotent(tmp_path: Path):
    project, target = make_project(tmp_path)
    before = sha256(target)

    result = ManuscriptWorkingCopy().save(
        project,
        "manuscript/EP01.txt",
        expected_sha256=before,
        content="原稿\n",
    )

    assert result["status"] == "unchanged"
    assert result["sha256"] == before
