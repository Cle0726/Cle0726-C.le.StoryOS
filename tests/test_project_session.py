from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml

from storyos.manuscript_recovery import ManuscriptRecovery
from storyos.project import StoryProject
from storyos.project_session import ProjectSession
from storyos.workspace import AuthoringWorkspace
from storyos.workspace_cli import main as workspace_cli_main


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
            "id": "session-test",
            "name": "Session Test",
            "language": "zh-CN",
            "paths": {"manuscript": "manuscript"},
        },
    )
    first = root / "manuscript" / "S01" / "EP01_First.txt"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("第一集\n原始正文。\n", encoding="utf-8", newline="\n")
    second = root / "manuscript" / "S01" / "EP02_Second.txt"
    second.write_text("第二集\n继续前进。\n", encoding="utf-8", newline="\n")
    return StoryProject.open(root)


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_session_is_compact_deterministic_and_read_only(tmp_path):
    project = _make_project(tmp_path / "project")
    before = _tree_snapshot(project.root)

    first = ProjectSession().build(project)
    second = ProjectSession().build(project)

    assert first == second
    assert first["schema"] == "story.authoring-project-session.v1"
    assert first["project"] == {
        "id": "session-test",
        "name": "Session Test",
        "language": "zh-CN",
    }
    assert first["summary"] == {
        "manuscripts": 2,
        "recovery_slots": 0,
        "recoverable_drafts": 0,
        "stale_base_drafts": 0,
    }
    assert first["recoveries"] == []
    assert first["policy"]["read_only"] is True
    assert first["policy"]["canonical_mutation"] is False
    assert first["policy"]["staging_mutation"] is False
    assert _tree_snapshot(project.root) == before


def test_session_surfaces_recovery_metadata_without_draft_content(tmp_path):
    project = _make_project(tmp_path / "project")
    manuscript = AuthoringWorkspace().load_manuscript(
        project, "manuscript/S01/EP01_First.txt"
    )
    draft_text = "第一集\n尚未正式保存的恢复草稿。\n"
    ManuscriptRecovery().save(
        project,
        manuscript["path"],
        base_sha256=manuscript["sha256"],
        content=draft_text,
    )
    before = _tree_snapshot(project.root)

    session = ProjectSession().build(project)

    assert session["summary"]["recovery_slots"] == 1
    assert session["summary"]["recoverable_drafts"] == 1
    assert session["summary"]["stale_base_drafts"] == 0
    assert len(session["recoveries"]) == 1
    recovery = session["recoveries"][0]
    assert recovery["path"] == "manuscript/S01/EP01_First.txt"
    assert recovery["episode"] == 1
    assert recovery["base_matches_current"] is True
    assert recovery["recoverable"] is True
    assert "content" not in recovery
    assert draft_text not in json.dumps(session, ensure_ascii=False)
    assert _tree_snapshot(project.root) == before


def test_session_marks_stale_base_and_cli_does_not_emit_draft_text(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")
    workspace = AuthoringWorkspace()
    manuscript = workspace.load_manuscript(project, "manuscript/S01/EP01_First.txt")
    draft_text = "第一集\n崩溃前草稿。\n"
    ManuscriptRecovery().save(
        project,
        manuscript["path"],
        base_sha256=manuscript["sha256"],
        content=draft_text,
    )

    disk_path = project.root / manuscript["path"]
    disk_path.write_text("第一集\n外部程序已经修改磁盘正文。\n", encoding="utf-8", newline="\n")

    session = ProjectSession().build(project)
    assert session["summary"]["stale_base_drafts"] == 1
    assert session["recoveries"][0]["base_matches_current"] is False

    monkeypatch.setattr(sys, "argv", ["storyos-workspace", "session", str(project.root)])
    workspace_cli_main()
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["schema"] == "story.authoring-project-session.v1"
    assert payload["summary"]["recoverable_drafts"] == 1
    assert draft_text not in output
    assert "content" not in payload["recoveries"][0]
