from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from storyos.claim_review import ClaimReviewWorkbench
from storyos.ids import stable_id
from storyos.materialization import MaterializationWorkbench
from storyos.project import StoryProject
from storyos.scene_workspace import SceneWorkspace


PROJECT_ID = "hardening-invariants-test"
CHAR = stable_id("character", PROJECT_ID, "pov")
FUTURE_FACT = stable_id("canon", PROJECT_ID, "future-fact")
FUTURE_CLAIM = stable_id("claim", PROJECT_ID, "future-overlap-claim")


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _project(root: Path) -> StoryProject:
    _write_yaml(
        root / "storyos.yaml",
        {
            "schema": "story.project.v1",
            "id": PROJECT_ID,
            "name": "Hardening Invariants",
            "language": "en",
            "paths": {"manuscript": "manuscript"},
        },
    )
    _write_yaml(
        root / "entities" / "pov.yaml",
        {
            "schema": "story.entity.v1",
            "id": CHAR,
            "kind": "character",
            "name": "POV",
            "slug": "pov",
            "aliases": [],
            "data": {},
        },
    )
    _write_yaml(
        root / "canon" / "future.yaml",
        {
            "schema": "story.canon.v1",
            "id": FUTURE_FACT,
            "subject": CHAR,
            "predicate": "identity.future_secret",
            "value": "must-not-leak-before-valid-from",
            "authority": "current",
            "valid_from": 30,
            "source": {"kind": "test"},
        },
    )
    _write_yaml(
        root / "events" / "knows.yaml",
        {
            "schema": "story.event.v1",
            "id": stable_id("event", PROJECT_ID, "knows"),
            "subject": CHAR,
            "type": "knowledge.gained",
            "at": {"sequence": 10, "season": 1, "episode": 1, "scene": 1},
            "payload": {"fact_id": FUTURE_FACT},
            "source": {"kind": "test"},
        },
    )
    _write_yaml(
        root / "events" / "boundary.yaml",
        {
            "schema": "story.event.v1",
            "id": stable_id("event", PROJECT_ID, "boundary"),
            "subject": CHAR,
            "type": "location.set",
            "at": {"sequence": 20, "season": 1, "episode": 1, "scene": 2},
            "payload": {"value": "room"},
            "source": {"kind": "test"},
        },
    )
    manuscript = root / "manuscript" / "S01" / "EP01_First.txt"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text("Episode one\n", encoding="utf-8", newline="\n")
    return StoryProject.open(root)


def test_pov_hides_known_fact_until_fact_is_active(tmp_path):
    project = _project(tmp_path / "project")
    view = SceneWorkspace().build(
        project,
        "manuscript/S01/EP01_First.txt",
        pov_entity_id=CHAR,
    )

    assert view["timeline"]["effective_through_sequence"] == 20
    knowledge = view["characters"][0]["knowledge"]
    assert knowledge["visible"] == []
    assert knowledge["hidden_inactive"] == 1
    assert "must-not-leak-before-valid-from" not in json.dumps(view, ensure_ascii=False)


def test_fact_materialization_blocks_future_equal_authority_overlap(tmp_path):
    project = _project(tmp_path / "project")
    _write_yaml(
        project.root / "staging" / "claims" / "future-overlap.yaml",
        {
            "schema": "story.claim.v1",
            "id": FUTURE_CLAIM,
            "subject": CHAR,
            "predicate": "identity.future_secret",
            "value": "different-value-before-future-canon",
            "at": {"sequence": 10, "season": 1, "episode": 1, "scene": 1},
            "confidence": 0.9,
            "source": {"kind": "test"},
            "proposed_authority": "current",
            "status": "pending",
        },
    )
    project = StoryProject.open(project.root)

    ClaimReviewWorkbench().decide(
        project,
        claim_id=FUTURE_CLAIM,
        decision="accept_fact_candidate",
        normalized_predicate="identity.future_secret",
        normalized_value="different-value-before-future-canon",
        note="must not create a future equal-authority overlap",
    )

    item = MaterializationWorkbench().build_plan(
        StoryProject.open(project.root),
        claim_id=FUTURE_CLAIM,
    )["items"][0]

    assert item["ready"] is False
    assert "future_interval_conflict" in item["reasons"]
    issue_codes = {issue["code"] for issue in item["check"]["issues"]}
    assert "canon_interval_conflict" in issue_codes


def test_project_data_loader_rejects_symlinks(tmp_path):
    project = _project(tmp_path / "project")
    outside = tmp_path / "outside-canon.yaml"
    _write_yaml(
        outside,
        {
            "schema": "story.canon.v1",
            "id": stable_id("canon", PROJECT_ID, "outside"),
            "subject": CHAR,
            "predicate": "secret.outside",
            "value": "must-not-be-imported-through-symlink",
            "authority": "current",
            "valid_from": 0,
            "source": {"kind": "test"},
        },
    )
    linked = project.root / "canon" / "outside-link.yaml"
    try:
        linked.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable in this test environment: {exc}")

    with pytest.raises(ValueError, match="symlink"):
        StoryProject.open(project.root).load_canon_facts()


def test_reference_validator_checks_legacy_fact_alias(tmp_path):
    project = _project(tmp_path / "project")
    missing_fact = stable_id("canon", PROJECT_ID, "missing-fact")
    _write_yaml(
        project.root / "events" / "legacy-fact-alias.yaml",
        {
            "schema": "story.event.v1",
            "id": stable_id("event", PROJECT_ID, "legacy-fact-alias"),
            "subject": CHAR,
            "type": "knowledge.gained",
            "at": {"sequence": 21, "season": 1, "episode": 1, "scene": 3},
            "payload": {"fact": missing_fact},
            "source": {"kind": "test"},
        },
    )

    errors = StoryProject.open(project.root).validate_references()
    assert any(missing_fact in error for error in errors)


def test_project_rejects_sequence_overlap_between_episodes(tmp_path):
    project = _project(tmp_path / "project")
    _write_yaml(
        project.root / "events" / "future-episode-low-sequence.yaml",
        {
            "schema": "story.event.v1",
            "id": stable_id("event", PROJECT_ID, "future-episode-low-sequence"),
            "subject": CHAR,
            "type": "location.set",
            "at": {"sequence": 20, "season": 1, "episode": 2, "scene": 1},
            "payload": {"value": "future-room"},
            "source": {"kind": "test"},
        },
    )

    with pytest.raises(ValueError, match="overlap or move backward"):
        StoryProject.open(project.root).load_events()
