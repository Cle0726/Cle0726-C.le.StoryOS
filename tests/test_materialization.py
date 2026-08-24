from pathlib import Path

import pytest
import yaml

from storyos.claim_review import ClaimReviewWorkbench, ReviewDecision
from storyos.materialization import MaterializationError, MaterializationWorkbench
from storyos.project import StoryProject


CHAR = "chr_00000000000000000000000000000001"
CLAIM = "clm_00000000000000000000000000000001"


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")


def make_project(tmp_path: Path) -> StoryProject:
    write_yaml(tmp_path / "storyos.yaml", {"schema": "story.project.v1", "id": "story_demo", "name": "Demo"})
    write_yaml(tmp_path / "entities" / "char.yaml", {"schema": "story.entity.v1", "id": CHAR, "kind": "character", "name": "凯登"})
    write_yaml(
        tmp_path / "staging" / "claims" / "claim.yaml",
        {
            "schema": "story.claim.v1",
            "id": CLAIM,
            "subject": CHAR,
            "predicate": "injury.left_hand",
            "value": "numb",
            "at": {"sequence": 20},
            "confidence": 0.9,
            "proposed_authority": "draft",
            "status": "pending",
        },
    )
    return StoryProject.open(tmp_path)


def canonical_snapshot(project: StoryProject):
    return (
        [(event.id, event.type, event.at.sequence, event.payload) for event in project.load_events()],
        [(fact.id, fact.predicate, fact.value, int(fact.authority)) for fact in project.load_canon_facts()],
    )


def accept_event(project: StoryProject) -> None:
    ClaimReviewWorkbench().decide(
        project,
        claim_id=CLAIM,
        decision=ReviewDecision.ACCEPT_EVENT_CANDIDATE,
        normalized_predicate="injury.left_hand",
        normalized_value="numb",
    )


def test_unreviewed_claim_is_blocked(tmp_path: Path):
    project = make_project(tmp_path)
    plan = MaterializationWorkbench().build_plan(project, claim_id=CLAIM)
    assert plan["items"][0]["ready"] is False
    assert plan["items"][0]["reasons"] == ["unreviewed"]


def test_defer_review_is_blocked(tmp_path: Path):
    project = make_project(tmp_path)
    ClaimReviewWorkbench().decide(project, claim_id=CLAIM, decision=ReviewDecision.DEFER)
    plan = MaterializationWorkbench().build_plan(project, claim_id=CLAIM)
    assert plan["items"][0]["ready"] is False
    assert "decision_defer" in plan["items"][0]["reasons"]


def test_ready_event_stages_only_in_quarantine_and_is_idempotent(tmp_path: Path):
    project = make_project(tmp_path)
    accept_event(project)
    before = canonical_snapshot(project)

    plan = MaterializationWorkbench().build_plan(project, claim_id=CLAIM)
    item = plan["items"][0]
    assert item["ready"] is True
    assert item["kind"] == "event"

    first, first_result = MaterializationWorkbench().stage(project, claim_id=CLAIM)
    second, second_result = MaterializationWorkbench().stage(project, claim_id=CLAIM)

    assert first_result == "created"
    assert second_result == "unchanged"
    assert first == second
    assert first["policy"]["canonical_mutation"] is False
    assert canonical_snapshot(project) == before
    staged = project.root / "staging" / "materialization" / "events" / f"{item['target_id']}.yaml"
    assert staged.is_file()


def test_current_canon_conflict_blocks_previously_reviewed_claim(tmp_path: Path):
    project = make_project(tmp_path)
    accept_event(project)
    write_yaml(
        project.root / "canon" / "locked.yaml",
        {
            "schema": "story.canon.v1",
            "id": "canon_00000000000000000000000000000001",
            "subject": CHAR,
            "predicate": "injury.left_hand",
            "value": "healthy",
            "authority": "locked",
            "valid_from": 0,
        },
    )
    refreshed = StoryProject.open(project.root)
    plan = MaterializationWorkbench().build_plan(refreshed, claim_id=CLAIM)
    assert plan["items"][0]["ready"] is False
    assert "current_conflict" in plan["items"][0]["reasons"]
    with pytest.raises(MaterializationError):
        MaterializationWorkbench().stage(refreshed, claim_id=CLAIM)


def test_existing_equal_canon_is_duplicate_not_ready(tmp_path: Path):
    project = make_project(tmp_path)
    ClaimReviewWorkbench().decide(
        project,
        claim_id=CLAIM,
        decision=ReviewDecision.ACCEPT_FACT_CANDIDATE,
        normalized_predicate="injury.left_hand",
        normalized_value="numb",
    )
    write_yaml(
        project.root / "canon" / "same.yaml",
        {
            "schema": "story.canon.v1",
            "id": "canon_00000000000000000000000000000002",
            "subject": CHAR,
            "predicate": "injury.left_hand",
            "value": "numb",
            "authority": "current",
            "valid_from": 0,
        },
    )
    refreshed = StoryProject.open(project.root)
    item = MaterializationWorkbench().build_plan(refreshed, claim_id=CLAIM)["items"][0]
    assert item["ready"] is False
    assert "already_canonical_duplicate" in item["reasons"]


def test_stale_review_is_blocked(tmp_path: Path):
    project = make_project(tmp_path)
    accept_event(project)
    claim_path = project.root / "staging" / "claims" / "claim.yaml"
    raw = yaml.safe_load(claim_path.read_text(encoding="utf-8"))
    raw["value"] = "recovered"
    write_yaml(claim_path, raw)
    refreshed = StoryProject.open(project.root)
    item = MaterializationWorkbench().build_plan(refreshed, claim_id=CLAIM)["items"][0]
    assert item["ready"] is False
    assert item["reasons"] == ["stale_review"]
