from __future__ import annotations

import json
from pathlib import Path

import yaml

from storyos.ids import stable_id
from storyos.project import StoryProject
from storyos.workspace_cli import main as workspace_cli_main


CHARACTER = stable_id("character", "product-flow", "protagonist")
CLAIM = stable_id("claim", "product-flow", "protagonist-role")


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _run(monkeypatch, capsys, argv: list[str]) -> dict:
    monkeypatch.setattr("sys.argv", ["storyos-workspace", *argv])
    workspace_cli_main()
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def test_product_cli_can_create_project_and_first_manuscript(tmp_path, monkeypatch, capsys):
    root = tmp_path / "novel"
    root.mkdir()
    (root / "author-notes.txt").write_text("preserve", encoding="utf-8")

    created = _run(
        monkeypatch,
        capsys,
        ["project-create", str(root), "--name", "Story Product", "--language", "zh-CN"],
    )
    assert created["schema"] == "story.project-create.v1"
    assert created["policy"]["create_only"] is True
    assert (root / "author-notes.txt").read_text(encoding="utf-8") == "preserve"

    manuscript = _run(
        monkeypatch,
        capsys,
        [
            "manuscript-create",
            str(root),
            "--title",
            "雨夜来信",
            "--season",
            "1",
            "--episode",
            "1",
        ],
    )
    assert manuscript["schema"] == "story.manuscript-create.v1"
    assert manuscript["policy"]["create_only"] is True

    snapshot = _run(monkeypatch, capsys, ["snapshot", str(root)])
    assert snapshot["summary"]["manuscripts"] == 1
    assert snapshot["manuscripts"][0]["path"] == manuscript["path"]


def test_product_cli_full_claim_to_canon_flow_is_explicit_and_reopenable(
    tmp_path,
    monkeypatch,
    capsys,
):
    root = tmp_path / "novel"
    root.mkdir()
    _run(monkeypatch, capsys, ["project-create", str(root), "--name", "Governed Story"])

    _write_yaml(
        root / "entities" / "protagonist.yaml",
        {
            "schema": "story.entity.v1",
            "id": CHARACTER,
            "kind": "character",
            "name": "林澈",
            "slug": "lin-che",
            "aliases": [],
            "data": {"role": "lead"},
        },
    )
    _write_yaml(
        root / "staging" / "claims" / "role.yaml",
        {
            "schema": "story.claim.v1",
            "id": CLAIM,
            "subject": CHARACTER,
            "predicate": "identity.role",
            "value": "protagonist",
            "at": {"sequence": 10, "season": 1, "episode": 1, "scene": 1},
            "confidence": 0.96,
            "source": {"kind": "product-flow-test"},
            "proposed_authority": "current",
            "status": "pending",
        },
    )

    queue = _run(monkeypatch, capsys, ["claim-review", str(root)])
    assert queue["schema"] == "story.claim-review-queue.v1"
    assert queue["summary"]["unreviewed"] == 1
    assert queue["items"][0]["claim"]["id"] == CLAIM

    review = _run(
        monkeypatch,
        capsys,
        [
            "claim-decide",
            str(root),
            CLAIM,
            "--decision",
            "accept_fact_candidate",
            "--predicate",
            "identity.role",
            "--value-json",
            json.dumps("protagonist"),
            "--note",
            "author confirmed",
        ],
    )
    assert review["schema"] == "story.claim-review-result.v1"
    assert review["policy"]["canonical_mutation"] is False
    assert not list((root / "canon").rglob("*.yaml"))

    plan = _run(monkeypatch, capsys, ["materialization-plan", str(root), "--claim", CLAIM])
    assert plan["summary"]["ready"] == 1

    staged = _run(monkeypatch, capsys, ["materialization-stage", str(root), CLAIM])
    assert staged["schema"] == "story.materialization-result.v1"
    assert staged["policy"]["quarantine_only"] is True
    assert staged["policy"]["canonical_mutation"] is False
    assert not list((root / "canon").rglob("*.yaml"))

    commit_plan = _run(monkeypatch, capsys, ["canon-commit-plan", str(root), "--claim", CLAIM])
    assert commit_plan["summary"]["ready"] == 1
    item = commit_plan["items"][0]
    assert item["claim_id"] == CLAIM
    candidate_sha = item["candidate_sha256"]
    assert isinstance(candidate_sha, str) and len(candidate_sha) == 64
    assert not list((root / "canon").rglob("*.yaml"))

    committed = _run(
        monkeypatch,
        capsys,
        [
            "canon-commit",
            str(root),
            CLAIM,
            "--confirm-sha256",
            candidate_sha,
            "--actor",
            "author",
            "--note",
            "explicit desktop confirmation",
        ],
    )
    assert committed["schema"] == "story.canon-commit-command-result.v1"
    assert committed["policy"]["canonical_mutation"] is True
    assert committed["policy"]["canonical_create_only"] is True
    assert committed["policy"]["canonical_overwrite"] is False

    reopened = StoryProject.open(root)
    facts = reopened.load_canon_facts()
    assert len(facts) == 1
    assert facts[0].subject == CHARACTER
    assert facts[0].predicate == "identity.role"
    assert facts[0].value == "protagonist"

    final_plan = _run(monkeypatch, capsys, ["canon-commit-plan", str(root), "--claim", CLAIM])
    assert final_plan["items"][0]["state"] == "committed"
    assert final_plan["items"][0]["ready"] is False
