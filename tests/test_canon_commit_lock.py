from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import yaml

import storyos.canon_commit as canon_commit_module
from storyos.canon_commit import CanonCommitWorkbench
from storyos.project import StoryProject


def _project(root: Path) -> StoryProject:
    root.mkdir(parents=True, exist_ok=True)
    (root / "storyos.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "story.project.v1",
                "id": "canon-lock-regression",
                "name": "Canon Lock Regression",
                "language": "en",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return StoryProject.open(root)


def test_direct_core_commit_uses_project_wide_lock(tmp_path, monkeypatch):
    project = _project(tmp_path / "project")
    transitions: list[str] = []

    @contextmanager
    def fake_project_lock(project_root, namespace, *, resource=None):
        assert project_root == project.root
        assert namespace == "canon-commit"
        assert resource is None
        transitions.append("enter")
        try:
            yield
        finally:
            transitions.append("exit")

    def fake_commit_locked(self, project_arg, **kwargs):
        assert project_arg == project
        assert transitions == ["enter"]
        assert kwargs["claim_id"] == "claim_direct_lock_test"
        assert kwargs["actor"] == "author"
        return {"schema": "test.canon-lock.v1"}, "created"

    monkeypatch.setattr(canon_commit_module, "project_file_lock", fake_project_lock)
    monkeypatch.setattr(CanonCommitWorkbench, "_commit_locked", fake_commit_locked)

    payload, status = CanonCommitWorkbench().commit(
        project,
        claim_id="claim_direct_lock_test",
        confirm_sha256="0" * 64,
        actor="author",
    )

    assert status == "created"
    assert payload["schema"] == "test.canon-lock.v1"
    assert transitions == ["enter", "exit"]
