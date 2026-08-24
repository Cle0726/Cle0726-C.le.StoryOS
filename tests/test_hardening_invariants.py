from __future__ import annotations

import json
from pathlib import Path

import yaml

from storyos.ids import stable_id
from storyos.project import StoryProject
from storyos.scene_workspace import SceneWorkspace


PROJECT_ID = "hardening-invariants-test"
CHAR = stable_id("character", PROJECT_ID, "pov")
FUTURE_FACT = stable_id("canon", PROJECT_ID, "future-fact")


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
