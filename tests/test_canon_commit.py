from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import storyos.canon_commit as canon_commit_module
from storyos.canon_commit import CanonCommitError, CanonCommitWorkbench
from storyos.claim_review import ClaimReviewWorkbench
from storyos.cli import main as cli_main
from storyos.ids import stable_id
from storyos.materialization import MaterializationWorkbench
from storyos.project import StoryProject


CHAR = stable_id("character", "standalone-canon-commit-test", "character")
CLAIM = stable_id("claim", "standalone-canon-commit-test", "claim")
CONFLICT_CANON = stable_id("canon", "standalone-canon-commit-test", "conflict")


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
            "id": "standalone-canon-commit-test",
            "name": "Standalone Canon Commit Test",
            "language": "en",
        },
    )
    _write_yaml(
        root / "entities" / "character.yaml",
        {
            "schema": "story.entity.v1",
            "id": CHAR,
            "kind": "character",
            "name": "Test Character",
            "slug": "test-character",
            "aliases": [],
            "data": {},
        },
    )
    _write_yaml(
        root / "staging" / "claims" / "claim.yaml",
        {
            "schema": "story.claim.v1",
            "id": CLAIM,
            "subject": CHAR,
            "predicate": "condition.note",
            "value": {"text": "left hand is numb"},
            "at": {"sequence": 1010101, "season": 1, "episode": 1, "scene": 1},
            "confidence": 0.9,
            "source": {"kind": "test"},
            "proposed_authority": "draft",
            "status": "pending",
        },
    )
    return StoryProject.open(root)


def _accept_event(
    project: StoryProject,
    *,
    predicate: str = "injury.left_hand",
    value="numb",
    replace: bool = False,
):
    return ClaimReviewWorkbench().decide(
        project,
        claim_id=CLAIM,
        decision="accept_event_candidate",
        normalized_predicate=predicate,
        normalized_value=value,
        note=f"reviewed {predicate}",
        replace=replace,
    )


def _accept_fact(
    project: StoryProject,
    *,
    predicate: str = "identity.role",
    value="protagonist",
    replace: bool = False,
):
    return ClaimReviewWorkbench().decide(
        project,
        claim_id=CLAIM,
        decision="accept_fact_candidate",
        normalized_predicate=predicate,
        normalized_value=value,
        note=f"reviewed {predicate}",
        replace=replace,
    )


def _stage(project: StoryProject) -> dict:
    MaterializationWorkbench().stage(project, claim_id=CLAIM)
    item = CanonCommitWorkbench().build_plan(project, claim_id=CLAIM)["items"][0]
    assert item["candidate_sha256"] is not None
    return item


def _audit_files(project: StoryProject) -> list[Path]:
    root = project.root / "audit" / "canon_commits"
    return [] if not root.exists() else sorted(root.glob("*.yaml"))


def _committed_files(project: StoryProject, kind: str) -> list[Path]:
    root = project.root / ("events" if kind == "event" else "canon") / "committed"
    return [] if not root.exists() else sorted(root.glob("*.yaml"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_conflicting_role_canon(project: StoryProject, value="antagonist") -> None:
    _write_yaml(
        project.root / "canon" / "external-conflict.yaml",
        {
            "schema": "story.canon.v1",
            "id": CONFLICT_CANON,
            "subject": CHAR,
            "predicate": "identity.role",
            "value": value,
            "authority": "locked",
            "valid_from": 0,
            "source": {"kind": "test-conflict"},
            "tags": ["identity"],
        },
    )


def test_unstaged_review_is_not_ready_for_commit(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_event(project)

    plan = CanonCommitWorkbench().build_plan(project, claim_id=CLAIM)
    item = plan["items"][0]

    assert plan["schema"] == "story.canon-commit-plan.v1"
    assert item["ready"] is False
    assert "candidate_not_staged" in item["reasons"]
    assert _audit_files(project) == []
    assert _committed_files(project, "event") == []


def test_wrong_confirmation_or_empty_actor_creates_nothing(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    item = _stage(project)

    with pytest.raises(CanonCommitError, match="actor cannot be empty"):
        CanonCommitWorkbench().commit(
            project,
            claim_id=CLAIM,
            confirm_sha256=item["candidate_sha256"],
            actor="   ",
        )

    with pytest.raises(CanonCommitError, match="confirmation SHA-256"):
        CanonCommitWorkbench().commit(
            project,
            claim_id=CLAIM,
            confirm_sha256="0" * 64,
            actor="author",
        )

    assert _audit_files(project) == []
    assert _committed_files(project, "event") == []


def test_quarantine_drift_is_blocked_before_audit_or_canon(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    item = _stage(project)
    candidate_path = project.root / item["candidate_path"]

    raw = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    raw["assumptions"].append("tampered after staging")
    _write_yaml(candidate_path, raw)

    reopened = StoryProject.open(project.root)
    changed = CanonCommitWorkbench().build_plan(reopened, claim_id=CLAIM)["items"][0]
    assert changed["ready"] is False
    assert "candidate_drift" in changed["reasons"]

    with pytest.raises(CanonCommitError, match="candidate_drift"):
        CanonCommitWorkbench().commit(
            reopened,
            claim_id=CLAIM,
            confirm_sha256=_sha256(candidate_path),
            actor="author",
        )
    assert _audit_files(project) == []
    assert _committed_files(project, "event") == []


def test_world_change_after_stage_blocks_commit_before_audit(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_fact(project)
    item = _stage(project)
    _write_conflicting_role_canon(project)

    reopened = StoryProject.open(project.root)
    blocked = CanonCommitWorkbench().build_plan(reopened, claim_id=CLAIM)["items"][0]
    assert blocked["ready"] is False
    assert "materialization_current_conflict" in blocked["reasons"]

    with pytest.raises(CanonCommitError, match="materialization_current_conflict"):
        CanonCommitWorkbench().commit(
            reopened,
            claim_id=CLAIM,
            confirm_sha256=item["candidate_sha256"],
            actor="author",
        )
    assert _audit_files(project) == []
    assert _committed_files(project, "fact") == []


def test_event_commit_creates_exact_payload_and_audit(tmp_path):
    project = _make_project(tmp_path / "project")
    claim_path = project.root / "staging" / "claims" / "claim.yaml"
    claim_before = claim_path.read_bytes()
    _accept_event(project)
    review_path = project.root / "staging" / "reviews" / f"{CLAIM}.yaml"
    review_before = review_path.read_bytes()
    item = _stage(project)
    candidate_path = project.root / item["candidate_path"]
    candidate_before = candidate_path.read_bytes()
    quarantine = yaml.safe_load(candidate_before)

    result, status = CanonCommitWorkbench().commit(
        project,
        claim_id=CLAIM,
        confirm_sha256=item["candidate_sha256"],
        actor="author-A",
        note="commit exact reviewed event",
    )

    assert status == "created"
    assert result["schema"] == "story.canon-commit-result.v1"
    assert result["kind"] == "event"
    canonical_path = project.root / result["canonical_path"]
    audit_path = project.root / result["audit_path"]
    assert yaml.safe_load(canonical_path.read_text(encoding="utf-8")) == quarantine["canonical_payload"]

    audit = yaml.safe_load(audit_path.read_text(encoding="utf-8"))
    assert audit["schema"] == "story.canon-commit-audit.v1"
    assert audit["actor"] == "author-A"
    assert audit["candidate_sha256"] == item["candidate_sha256"]
    assert audit["policy"]["immutable"] is True
    assert audit["policy"]["audit_precedes_canonical_mutation"] is True

    assert claim_path.read_bytes() == claim_before
    assert review_path.read_bytes() == review_before
    assert candidate_path.read_bytes() == candidate_before


def test_fact_commit_and_repeat_are_idempotent(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_fact(project)
    item = _stage(project)

    first, first_status = CanonCommitWorkbench().commit(
        project,
        claim_id=CLAIM,
        confirm_sha256=item["candidate_sha256"],
        actor="author",
        note="commit reviewed fact",
    )
    assert first_status == "created"
    assert first["kind"] == "fact"

    second, second_status = CanonCommitWorkbench().commit(
        StoryProject.open(project.root),
        claim_id=CLAIM,
        confirm_sha256=item["candidate_sha256"],
        actor="different-after-completion",
        note="ignored after completion",
    )
    assert second_status == "unchanged"
    assert second["target_id"] == first["target_id"]
    assert second["audit_id"] == first["audit_id"]
    assert len(_committed_files(project, "fact")) == 1
    assert len(_audit_files(project)) == 1


def test_untracked_canonical_target_is_never_adopted(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    item = _stage(project)
    _write_yaml(project.root / item["canonical_path"], item["canonical_payload"])

    blocked = CanonCommitWorkbench().build_plan(
        StoryProject.open(project.root), claim_id=CLAIM
    )["items"][0]
    assert blocked["ready"] is False
    assert "canonical_target_untracked" in blocked["reasons"]
    assert _audit_files(project) == []


def test_audit_is_written_before_canonical_create_failure(tmp_path, monkeypatch):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    item = _stage(project)
    original = canon_commit_module._exclusive_write_yaml

    def fail_canonical(path: Path, data: dict) -> None:
        if "committed" in path.parts and "events" in path.parts:
            raise OSError("simulated canonical write failure")
        original(path, data)

    monkeypatch.setattr(canon_commit_module, "_exclusive_write_yaml", fail_canonical)
    with pytest.raises(OSError, match="simulated canonical write failure"):
        CanonCommitWorkbench().commit(
            project,
            claim_id=CLAIM,
            confirm_sha256=item["candidate_sha256"],
            actor="author",
            note="audit must survive",
        )

    assert len(_audit_files(project)) == 1
    assert _committed_files(project, "event") == []


def test_authorized_audit_can_resume_only_with_same_authorization(tmp_path, monkeypatch):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    item = _stage(project)
    original = canon_commit_module._exclusive_write_yaml

    def fail_canonical(path: Path, data: dict) -> None:
        if "committed" in path.parts and "events" in path.parts:
            raise OSError("stop after authorization")
        original(path, data)

    monkeypatch.setattr(canon_commit_module, "_exclusive_write_yaml", fail_canonical)
    with pytest.raises(OSError):
        CanonCommitWorkbench().commit(
            project,
            claim_id=CLAIM,
            confirm_sha256=item["candidate_sha256"],
            actor="author-A",
            note="resume-me",
        )

    monkeypatch.setattr(canon_commit_module, "_exclusive_write_yaml", original)
    resumed = CanonCommitWorkbench().build_plan(
        StoryProject.open(project.root), claim_id=CLAIM
    )["items"][0]
    assert resumed["ready"] is True
    assert resumed["state"] == "authorized"

    with pytest.raises(CanonCommitError, match="authorization audit already exists"):
        CanonCommitWorkbench().commit(
            StoryProject.open(project.root),
            claim_id=CLAIM,
            confirm_sha256=item["candidate_sha256"],
            actor="author-B",
            note="rewrite attempt",
        )

    result, status = CanonCommitWorkbench().commit(
        StoryProject.open(project.root),
        claim_id=CLAIM,
        confirm_sha256=item["candidate_sha256"],
        actor="author-A",
        note="resume-me",
    )
    assert status == "created"
    assert (project.root / result["canonical_path"]).is_file()
    assert len(_audit_files(project)) == 1


def test_revalidation_after_authorization_blocks_world_change(tmp_path, monkeypatch):
    project = _make_project(tmp_path / "project")
    _accept_fact(project)
    item = _stage(project)
    original = canon_commit_module._exclusive_write_yaml

    def inject_conflict(path: Path, data: dict) -> None:
        original(path, data)
        if "canon_commits" in path.parts:
            _write_conflicting_role_canon(project)

    monkeypatch.setattr(canon_commit_module, "_exclusive_write_yaml", inject_conflict)
    with pytest.raises(CanonCommitError, match="stopped being ready after authorization"):
        CanonCommitWorkbench().commit(
            project,
            claim_id=CLAIM,
            confirm_sha256=item["candidate_sha256"],
            actor="author",
            note="world changes after audit",
        )

    assert len(_audit_files(project)) == 1
    assert _committed_files(project, "fact") == []


def test_one_claim_cannot_commit_second_distinct_target(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    first_item = _stage(project)
    first, status = CanonCommitWorkbench().commit(
        project,
        claim_id=CLAIM,
        confirm_sha256=first_item["candidate_sha256"],
        actor="author",
        note="first target",
    )
    assert status == "created"

    reopened = StoryProject.open(project.root)
    _accept_event(
        reopened,
        predicate="injury.right_hand",
        value="weak",
        replace=True,
    )
    MaterializationWorkbench().stage(StoryProject.open(project.root), claim_id=CLAIM)
    blocked = CanonCommitWorkbench().build_plan(
        StoryProject.open(project.root), claim_id=CLAIM
    )["items"][0]
    assert blocked["ready"] is False
    assert blocked["reasons"] == ["claim_already_committed"]
    assert blocked["audit_id"] == first["audit_id"]
    assert len(_committed_files(project, "event")) == 1


def test_cli_requires_explicit_hash(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")
    _accept_event(project)
    _stage(project)

    monkeypatch.setattr(
        "sys.argv",
        ["storyos", "canon-commit-plan", str(project.root), "--claim", CLAIM],
    )
    cli_main()
    plan = json.loads(capsys.readouterr().out)
    item = plan["items"][0]
    assert plan["summary"]["ready"] == 1

    monkeypatch.setattr(
        "sys.argv",
        [
            "storyos",
            "canon-commit",
            str(project.root),
            CLAIM,
            "--confirm-sha256",
            item["candidate_sha256"],
            "--actor",
            "author",
            "--note",
            "explicit CLI commit",
        ],
    )
    cli_main()
    committed = json.loads(capsys.readouterr().out)
    assert committed["schema"] == "story.canon-commit-command-result.v1"
    assert committed["result"] == "created"
    assert committed["commit"]["schema"] == "story.canon-commit-result.v1"
    assert committed["policy"]["canonical_overwrite"] is False
