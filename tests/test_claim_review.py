from pathlib import Path

import pytest
import yaml

from storyos.claim_review import ClaimReviewError, ClaimReviewWorkbench, ReviewDecision
from storyos.project import StoryProject


CHAR = "chr_00000000000000000000000000000001"
CLAIM = "clm_00000000000000000000000000000001"


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")


def make_project(tmp_path: Path) -> StoryProject:
    write_yaml(tmp_path / "storyos.yaml", {"schema": "story.project.v1", "id": "story_demo", "name": "Demo"})
    write_yaml(
        tmp_path / "entities" / "char.yaml",
        {"schema": "story.entity.v1", "id": CHAR, "kind": "character", "name": "凯登"},
    )
    write_yaml(
        tmp_path / "events" / "event.yaml",
        {
            "schema": "story.event.v1",
            "id": "evt_00000000000000000000000000000001",
            "subject": CHAR,
            "type": "location.set",
            "at": {"sequence": 10},
            "payload": {"value": "home"},
        },
    )
    write_yaml(
        tmp_path / "canon" / "fact.yaml",
        {
            "schema": "story.canon.v1",
            "id": "canon_00000000000000000000000000000001",
            "subject": CHAR,
            "predicate": "role",
            "value": "protagonist",
            "authority": "locked",
        },
    )
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
            "status": "pending",
        },
    )
    return StoryProject.open(tmp_path)


def canonical_snapshot(project: StoryProject):
    events = [(event.id, event.subject, event.type, event.at.sequence, event.payload) for event in project.load_events()]
    facts = [(fact.id, fact.subject, fact.predicate, fact.value, int(fact.authority)) for fact in project.load_canon_facts()]
    return events, facts


def test_accept_requires_normalized_target(tmp_path: Path):
    project = make_project(tmp_path)
    with pytest.raises(ClaimReviewError):
        ClaimReviewWorkbench().decide(
            project,
            claim_id=CLAIM,
            decision=ReviewDecision.ACCEPT_EVENT_CANDIDATE,
        )


def test_reject_cannot_carry_normalized_target(tmp_path: Path):
    project = make_project(tmp_path)
    with pytest.raises(ClaimReviewError):
        ClaimReviewWorkbench().decide(
            project,
            claim_id=CLAIM,
            decision=ReviewDecision.REJECT,
            normalized_predicate="injury.left_hand",
            normalized_value="numb",
        )


def test_review_does_not_change_claim_or_canonical_state(tmp_path: Path):
    project = make_project(tmp_path)
    before = canonical_snapshot(project)
    claim_status_before = project.load_claims()[0].status.value

    review, result = ClaimReviewWorkbench().decide(
        project,
        claim_id=CLAIM,
        decision=ReviewDecision.ACCEPT_EVENT_CANDIDATE,
        normalized_predicate="injury.left_hand",
        normalized_value="numb",
        note="confirmed",
    )

    assert result == "created"
    assert review.normalized == {"predicate": "injury.left_hand", "value": "numb"}
    assert canonical_snapshot(project) == before
    assert project.load_claims()[0].status.value == claim_status_before
    assert (project.root / "staging" / "reviews" / f"{CLAIM}.yaml").is_file()


def test_different_review_requires_explicit_replace(tmp_path: Path):
    project = make_project(tmp_path)
    workbench = ClaimReviewWorkbench()
    workbench.decide(project, claim_id=CLAIM, decision=ReviewDecision.DEFER)

    with pytest.raises(ClaimReviewError):
        workbench.decide(project, claim_id=CLAIM, decision=ReviewDecision.REJECT)

    review, result = workbench.decide(
        project,
        claim_id=CLAIM,
        decision=ReviewDecision.REJECT,
        replace=True,
    )
    assert result == "replaced"
    assert review.decision is ReviewDecision.REJECT


def test_claim_change_marks_existing_review_stale(tmp_path: Path):
    project = make_project(tmp_path)
    workbench = ClaimReviewWorkbench()
    workbench.decide(project, claim_id=CLAIM, decision=ReviewDecision.DEFER)

    claim_path = project.root / "staging" / "claims" / "claim.yaml"
    raw = yaml.safe_load(claim_path.read_text(encoding="utf-8"))
    raw["value"] = "recovered"
    write_yaml(claim_path, raw)

    refreshed = StoryProject.open(project.root)
    queue = workbench.build_queue(refreshed, claim_id=CLAIM)
    assert queue["summary"]["stale_reviews"] == 1
    assert queue["items"][0]["review_stale"] is True
