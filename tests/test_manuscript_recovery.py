from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
import yaml

from storyos.manuscript_recovery import ManuscriptRecovery, ManuscriptRecoveryError
from storyos.manuscript_writer import ManuscriptWriter
from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace
from storyos.workspace_cli import main as workspace_cli_main


class _BinaryStdin:
    def __init__(self, value: str) -> None:
        self.buffer = io.BytesIO(value.encode("utf-8"))


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _make_project(root: Path) -> StoryProject:
    _write_yaml(
        root / "storyos.yaml",
        {
            "schema": "story.project.v1",
            "id": "recovery-test",
            "name": "Recovery Test",
            "language": "zh-CN",
            "paths": {"manuscript": "manuscript"},
        },
    )
    manuscript = root / "manuscript" / "S01" / "EP01_First Bell.txt"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text("第一集\n磁盘正文。\n", encoding="utf-8", newline="\n")
    _write_yaml(root / "canon" / "sentinel.yaml", {"sentinel": "must-not-change"})
    _write_yaml(root / "staging" / "sentinel.yaml", {"sentinel": "must-not-change"})
    return StoryProject.open(root)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_recovery_save_is_isolated_from_manuscript_history_canon_and_staging(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    manuscript = project.root / relative
    canon = project.root / "canon" / "sentinel.yaml"
    staging = project.root / "staging" / "sentinel.yaml"
    loaded = AuthoringWorkspace().load_manuscript(project, relative)
    manuscript_before = manuscript.read_bytes()
    canon_before = _sha(canon)
    staging_before = _sha(staging)

    result = ManuscriptRecovery().save(
        project,
        relative,
        base_sha256=loaded["sha256"],
        content="第一集\n尚未正式保存的草稿。\n",
    )

    assert result["schema"] == "story.authoring-manuscript-recovery-save.v1"
    assert result["recovery"]["base_sha256"] == loaded["sha256"]
    assert result["recovery"]["base_matches_current"] is True
    assert result["recovery"]["draft_matches_current"] is False
    assert result["recovery"]["recoverable"] is True
    assert result["policy"] == {
        "read_only": False,
        "manuscript_mutation": False,
        "history_mutation": False,
        "recovery_mutation": True,
        "canonical_mutation": False,
        "staging_mutation": False,
    }
    assert manuscript.read_bytes() == manuscript_before
    assert _sha(canon) == canon_before
    assert _sha(staging) == staging_before
    assert not (project.root / ".storyos" / "manuscript-history").exists()


def test_recovery_uses_one_atomic_slot_per_manuscript(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    base = AuthoringWorkspace().load_manuscript(project, relative)["sha256"]
    recovery = ManuscriptRecovery()

    first = recovery.save(project, relative, base_sha256=base, content="草稿一。\n")
    second = recovery.save(project, relative, base_sha256=base, content="草稿二。\n")
    loaded = recovery.load(project, relative)

    assert first["recovery"]["draft_sha256"] != second["recovery"]["draft_sha256"]
    assert loaded["present"] is True
    assert loaded["recovery"]["content"] == "草稿二。\n"
    assert loaded["recovery"]["draft_sha256"] == second["recovery"]["draft_sha256"]
    directory = project.root / ".storyos" / "manuscript-recovery" / relative
    assert [path.name for path in directory.iterdir()] == ["draft.json"]


def test_recovery_keeps_stale_base_as_information_without_overwriting_disk(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    manuscript = project.root / relative
    loaded = AuthoringWorkspace().load_manuscript(project, relative)
    manuscript.write_text("外部编辑器的新磁盘版本。\n", encoding="utf-8", newline="\n")
    disk_sha = _sha(manuscript)

    result = ManuscriptRecovery().save(
        project,
        relative,
        base_sha256=loaded["sha256"],
        content="StoryOS 里的未保存草稿。\n",
    )

    assert result["recovery"]["base_matches_current"] is False
    assert result["recovery"]["recoverable"] is True
    assert _sha(manuscript) == disk_sha
    assert manuscript.read_text(encoding="utf-8") == "外部编辑器的新磁盘版本。\n"


def test_recovery_marks_draft_redundant_when_it_matches_current_manuscript(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    loaded = AuthoringWorkspace().load_manuscript(project, relative)

    ManuscriptRecovery().save(
        project,
        relative,
        base_sha256=loaded["sha256"],
        content=loaded["content"],
    )
    result = ManuscriptRecovery().load(project, relative)

    assert result["present"] is True
    assert result["recovery"]["draft_matches_current"] is True
    assert result["recovery"]["recoverable"] is False
    assert result["policy"]["read_only"] is True
    assert result["policy"]["recovery_mutation"] is False


def test_recovery_clear_requires_the_exact_current_draft_sha(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    base = AuthoringWorkspace().load_manuscript(project, relative)["sha256"]
    recovery = ManuscriptRecovery()
    first = recovery.save(project, relative, base_sha256=base, content="旧恢复稿。\n")
    second = recovery.save(project, relative, base_sha256=base, content="新恢复稿。\n")

    with pytest.raises(ManuscriptRecoveryError, match="newer draft"):
        recovery.clear(
            project,
            relative,
            expected_draft_sha256=first["recovery"]["draft_sha256"],
        )
    assert recovery.load(project, relative)["recovery"]["content"] == "新恢复稿。\n"

    cleared = recovery.clear(
        project,
        relative,
        expected_draft_sha256=second["recovery"]["draft_sha256"],
    )
    assert cleared["cleared"] is True
    assert cleared["policy"]["recovery_mutation"] is True
    assert recovery.load(project, relative)["present"] is False


def test_recovery_reuses_workspace_path_boundary_and_validates_integrity(tmp_path):
    project = _make_project(tmp_path / "project")
    recovery = ManuscriptRecovery()

    with pytest.raises(ManuscriptRecoveryError, match="escapes"):
        recovery.load(project, "manuscript/../storyos.yaml")
    with pytest.raises(ManuscriptRecoveryError, match="base_sha256"):
        recovery.save(
            project,
            "manuscript/S01/EP01_First Bell.txt",
            base_sha256="BAD",
            content="x",
        )

    relative = "manuscript/S01/EP01_First Bell.txt"
    loaded = AuthoringWorkspace().load_manuscript(project, relative)
    recovery.save(project, relative, base_sha256=loaded["sha256"], content="完整草稿。\n")
    record = project.root / ".storyos" / "manuscript-recovery" / relative / "draft.json"
    value = json.loads(record.read_text(encoding="utf-8"))
    value["content"] = "被篡改。\n"
    record.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ManuscriptRecoveryError, match="integrity"):
        recovery.load(project, relative)


def test_workspace_cli_recovery_save_read_and_clear_are_scoped(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    base = AuthoringWorkspace().load_manuscript(project, relative)["sha256"]

    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "manuscript-recovery-save", str(project.root), relative, base],
    )
    monkeypatch.setattr("sys.stdin", _BinaryStdin("CLI 自动恢复草稿。\n"))
    workspace_cli_main()
    saved = json.loads(capsys.readouterr().out)
    assert saved["schema"] == "story.authoring-manuscript-recovery-save.v1"
    assert saved["policy"]["recovery_mutation"] is True
    assert saved["policy"]["manuscript_mutation"] is False
    draft_sha = saved["recovery"]["draft_sha256"]

    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "manuscript-recovery", str(project.root), relative],
    )
    workspace_cli_main()
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["schema"] == "story.authoring-manuscript-recovery.v1"
    assert loaded["recovery"]["content"] == "CLI 自动恢复草稿。\n"
    assert loaded["policy"]["read_only"] is True

    monkeypatch.setattr(
        "sys.argv",
        [
            "storyos-workspace",
            "manuscript-recovery-clear",
            str(project.root),
            relative,
            draft_sha,
        ],
    )
    workspace_cli_main()
    cleared = json.loads(capsys.readouterr().out)
    assert cleared["schema"] == "story.authoring-manuscript-recovery-clear.v1"
    assert cleared["cleared"] is True
    assert cleared["policy"]["recovery_mutation"] is True


def test_official_save_leaves_matching_recovery_nonrecoverable_instead_of_racing_cleanup(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    loaded = AuthoringWorkspace().load_manuscript(project, relative)
    draft = "第一集\n最终正式保存的草稿。\n"

    ManuscriptRecovery().save(
        project,
        relative,
        base_sha256=loaded["sha256"],
        content=draft,
    )
    ManuscriptWriter().save(
        project,
        relative,
        expected_sha256=loaded["sha256"],
        content=draft,
    )
    recovery = ManuscriptRecovery().load(project, relative)

    assert recovery["present"] is True
    assert recovery["recovery"]["draft_matches_current"] is True
    assert recovery["recovery"]["recoverable"] is False
