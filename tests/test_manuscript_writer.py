from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
import yaml

from storyos.manuscript_writer import (
    ManuscriptConflictError,
    ManuscriptWriteError,
    ManuscriptWriter,
)
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
            "id": "writer-test",
            "name": "Writer Test",
            "language": "zh-CN",
            "paths": {"manuscript": "manuscript"},
        },
    )
    manuscript = root / "manuscript" / "S01" / "EP01_First Bell.txt"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text("第一集\n旧版本。\n", encoding="utf-8", newline="\n")
    _write_yaml(root / "canon" / "sentinel.yaml", {"sentinel": "must-not-change"})
    _write_yaml(root / "staging" / "sentinel.yaml", {"sentinel": "must-not-change"})
    return StoryProject.open(root)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_writer_saves_only_existing_manuscript_with_exact_cas_guard(tmp_path):
    project = _make_project(tmp_path / "project")
    path = project.root / "manuscript" / "S01" / "EP01_First Bell.txt"
    canon = project.root / "canon" / "sentinel.yaml"
    staging = project.root / "staging" / "sentinel.yaml"
    canon_before = _sha(canon)
    staging_before = _sha(staging)

    loaded = AuthoringWorkspace().load_manuscript(
        project, "manuscript/S01/EP01_First Bell.txt"
    )
    result = ManuscriptWriter().save(
        project,
        loaded["path"],
        expected_sha256=loaded["sha256"],
        content="第一集\n新版本。\n第二段。\n",
    )

    assert result["schema"] == "story.authoring-manuscript-save.v1"
    assert result["written"] is True
    assert result["previous_sha256"] == loaded["sha256"]
    assert result["sha256"] == _sha(path)
    assert result["sha256"] != loaded["sha256"]
    assert result["policy"] == {
        "read_only": False,
        "manuscript_mutation": True,
        "canonical_mutation": False,
        "staging_mutation": False,
    }
    assert path.read_text(encoding="utf-8") == "第一集\n新版本。\n第二段。\n"
    assert _sha(canon) == canon_before
    assert _sha(staging) == staging_before


def test_writer_rejects_stale_sha_without_overwriting_external_change(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    path = project.root / relative
    loaded = AuthoringWorkspace().load_manuscript(project, relative)

    path.write_text("外部编辑器已经修改。\n", encoding="utf-8", newline="\n")
    external_sha = _sha(path)

    with pytest.raises(ManuscriptConflictError, match="changed since it was loaded"):
        ManuscriptWriter().save(
            project,
            relative,
            expected_sha256=loaded["sha256"],
            content="StoryOS 中的旧草稿。\n",
        )

    assert path.read_text(encoding="utf-8") == "外部编辑器已经修改。\n"
    assert _sha(path) == external_sha


def test_writer_preserves_existing_utf8_bom(tmp_path):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    path = project.root / relative
    path.write_bytes(b"\xef\xbb\xbf" + "有 BOM。\n".encode("utf-8"))
    loaded = AuthoringWorkspace().load_manuscript(project, relative)

    result = ManuscriptWriter().save(
        project,
        relative,
        expected_sha256=loaded["sha256"],
        content="仍然有 BOM。\n",
    )

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert result["sha256"] == _sha(path)
    assert AuthoringWorkspace().load_manuscript(project, relative)["content"] == "仍然有 BOM。\n"


def test_writer_reuses_workspace_path_boundary_and_validates_sha(tmp_path):
    project = _make_project(tmp_path / "project")
    writer = ManuscriptWriter()

    with pytest.raises(ManuscriptWriteError, match="expected_sha256"):
        writer.save(
            project,
            "manuscript/S01/EP01_First Bell.txt",
            expected_sha256="not-a-sha",
            content="x",
        )

    with pytest.raises(ManuscriptWriteError, match="escapes"):
        writer.save(
            project,
            "manuscript/../storyos.yaml",
            expected_sha256="0" * 64,
            content="x",
        )


def test_workspace_cli_manuscript_save_reads_content_from_stdin(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")
    relative = "manuscript/S01/EP01_First Bell.txt"
    loaded = AuthoringWorkspace().load_manuscript(project, relative)

    monkeypatch.setattr(
        "sys.argv",
        [
            "storyos-workspace",
            "manuscript-save",
            str(project.root),
            relative,
            loaded["sha256"],
        ],
    )
    monkeypatch.setattr("sys.stdin", _BinaryStdin("CLI 保存。\n"))
    workspace_cli_main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "story.authoring-manuscript-save.v1"
    assert payload["policy"]["canonical_mutation"] is False
    assert payload["policy"]["staging_mutation"] is False
    assert (project.root / relative).read_text(encoding="utf-8") == "CLI 保存。\n"
