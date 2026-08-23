from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from storyos.claim_review import ClaimReviewWorkbench
from storyos.ids import stable_id
from storyos.materialization import MaterializationWorkbench
from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError
from storyos.workspace_cli import main as workspace_cli_main


ARIA = stable_id("character", "workspace-test", "aria")
CADEN = stable_id("character", "workspace-test", "caden")
EVENT_ARIA_LOCATION = stable_id("event", "workspace-test", "aria-location")
EVENT_CADEN_LOCATION = stable_id("event", "workspace-test", "caden-location")
EVENT_ARIA_INJURY = stable_id("event", "workspace-test", "aria-injury")
ROLE = stable_id("canon", "workspace-test", "aria-role")
CLAIM = stable_id("claim", "workspace-test", "aria-goal")


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
            "id": "workspace-test",
            "name": "Workspace Test",
            "language": "zh-CN",
            "paths": {"manuscript": "manuscript"},
        },
    )
    for entity_id, name, slug, aliases, data in [
        (ARIA, "Aria", "aria", ["A"], {"role": "lead"}),
        (CADEN, "Caden", "caden", [], {}),
    ]:
        _write_yaml(
            root / "entities" / f"{slug}.yaml",
            {
                "schema": "story.entity.v1",
                "id": entity_id,
                "kind": "character",
                "name": name,
                "slug": slug,
                "aliases": aliases,
                "data": data,
            },
        )

    for filename, event_id, subject, event_type, sequence, value in [
        ("aria-location.yaml", EVENT_ARIA_LOCATION, ARIA, "location.set", 10, "old-city"),
        ("caden-location.yaml", EVENT_CADEN_LOCATION, CADEN, "location.set", 15, "station"),
        ("aria-injury.yaml", EVENT_ARIA_INJURY, ARIA, "injury.left_hand.set", 20, "numb"),
    ]:
        _write_yaml(
            root / "events" / filename,
            {
                "schema": "story.event.v1",
                "id": event_id,
                "subject": subject,
                "type": event_type,
                "at": {"sequence": sequence, "season": 1, "episode": 1, "scene": 1},
                "payload": {"value": value},
                "source": {"kind": "test"},
            },
        )

    _write_yaml(
        root / "canon" / "aria-role.yaml",
        {
            "schema": "story.canon.v1",
            "id": ROLE,
            "subject": ARIA,
            "predicate": "identity.role",
            "value": "protagonist",
            "authority": "locked",
            "valid_from": 0,
            "source": {"kind": "test"},
            "tags": ["identity"],
        },
    )
    _write_yaml(
        root / "staging" / "claims" / "aria-goal.yaml",
        {
            "schema": "story.claim.v1",
            "id": CLAIM,
            "subject": ARIA,
            "predicate": "goal.destination",
            "value": "north",
            "at": {"sequence": 30, "season": 1, "episode": 3, "scene": 1},
            "confidence": 0.9,
            "source": {"kind": "test"},
            "proposed_authority": "draft",
            "status": "pending",
        },
    )

    ep1 = root / "manuscript" / "S01" / "EP01_First Bell.txt"
    ep1.parent.mkdir(parents=True, exist_ok=True)
    ep1.write_text("第一集\n雨落在旧城。\n", encoding="utf-8", newline="\n")
    ep2 = root / "manuscript" / "S01" / "EP02_Answer.txt"
    ep2.write_text("第二集\n她给出了自己的回答。\n", encoding="utf-8", newline="\n")
    return StoryProject.open(root)


def _accept_and_stage(project: StoryProject) -> None:
    ClaimReviewWorkbench().decide(
        project,
        claim_id=CLAIM,
        decision="accept_event_candidate",
        normalized_predicate="goal.destination",
        normalized_value="north",
        note="workspace test",
    )
    MaterializationWorkbench().stage(project, claim_id=CLAIM)


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_snapshot_is_deterministic_compact_read_only_and_time_bounded(tmp_path):
    project = _make_project(tmp_path / "project")
    workspace = AuthoringWorkspace()
    before = _tree_snapshot(project.root)

    first = workspace.build_snapshot(project, through_sequence=10)
    second = workspace.build_snapshot(project, through_sequence=10)

    assert first == second
    assert first["schema"] == "story.authoring-workspace.v1"
    assert first["timeline"]["effective_through_sequence"] == 10
    assert first["timeline"]["events"] == 1
    assert first["summary"]["manuscripts"] == 2
    assert [item["episode"] for item in first["manuscripts"]] == [1, 2]
    assert all("content" not in item for item in first["manuscripts"])
    assert first["workflow"]["review"]["unreviewed"] == 1
    assert first["policy"]["read_only"] is True
    assert first["policy"]["canonical_mutation"] is False

    aria = next(item for item in first["entities"] if item["id"] == ARIA)
    caden = next(item for item in first["entities"] if item["id"] == CADEN)
    assert aria["state"]["values"] == {"location": "old-city"}
    assert aria["counts"]["events"] == 1
    assert aria["counts"]["events_total"] == 2
    assert aria["counts"]["canon_facts"] == 1
    assert aria["counts"]["claims"] == 1
    assert caden["state"]["values"] == {}
    assert _tree_snapshot(project.root) == before


def test_snapshot_defaults_to_latest_event_boundary(tmp_path):
    project = _make_project(tmp_path / "project")
    snapshot = AuthoringWorkspace().build_snapshot(project)
    assert snapshot["timeline"]["effective_through_sequence"] == 20
    aria = next(item for item in snapshot["entities"] if item["id"] == ARIA)
    assert aria["state"]["values"] == {
        "location": "old-city",
        "injury.left_hand": "numb",
    }


def test_entity_view_joins_review_materialization_and_commit_readiness(tmp_path):
    project = _make_project(tmp_path / "project")
    _accept_and_stage(project)
    before = _tree_snapshot(project.root)

    view = AuthoringWorkspace().build_entity_view(project, ARIA, through_sequence=20)

    assert view["schema"] == "story.authoring-entity.v1"
    assert view["entity"]["name"] == "Aria"
    assert view["state"]["values"]["injury.left_hand"] == "numb"
    assert [event["id"] for event in view["events"]] == [
        EVENT_ARIA_LOCATION,
        EVENT_ARIA_INJURY,
    ]
    assert view["canon_facts"][0]["predicate"] == "identity.role"
    assert view["canon_facts"][0]["mainline_active"] is True
    assert len(view["claims"]) == 1
    claim = view["claims"][0]
    assert claim["review"]["decision"] == "accept_event_candidate"
    assert claim["materialization"]["ready"] is True
    assert claim["canon_commit"]["ready"] is True
    assert len(claim["canon_commit"]["candidate_sha256"]) == 64
    assert view["workflow"]["canon_commit_ready"] == 1
    assert _tree_snapshot(project.root) == before


def test_unknown_entity_and_negative_boundary_are_rejected_without_writes(tmp_path):
    project = _make_project(tmp_path / "project")
    before = _tree_snapshot(project.root)
    workspace = AuthoringWorkspace()
    with pytest.raises(AuthoringWorkspaceError, match="unknown entity"):
        workspace.build_entity_view(project, CADEN + "x")
    with pytest.raises(AuthoringWorkspaceError, match="through_sequence"):
        workspace.build_snapshot(project, through_sequence=-1)
    assert _tree_snapshot(project.root) == before


def test_manuscript_metadata_and_content_are_separate_and_path_safe(tmp_path):
    project = _make_project(tmp_path / "project")
    workspace = AuthoringWorkspace()
    before = _tree_snapshot(project.root)

    listing = workspace.list_manuscripts(project)
    assert listing[0]["path"] == "manuscript/S01/EP01_First Bell.txt"
    assert listing[0]["title"] == "First Bell"
    assert listing[0]["season"] == 1
    assert listing[0]["episode"] == 1
    assert "content" not in listing[0]

    document = workspace.load_manuscript(project, listing[0]["path"])
    assert document["schema"] == "story.authoring-manuscript.v1"
    assert document["content"] == "第一集\n雨落在旧城。\n"
    assert document["sha256"] == listing[0]["sha256"]

    with pytest.raises(AuthoringWorkspaceError, match="escapes"):
        workspace.load_manuscript(project, "manuscript/../storyos.yaml")
    with pytest.raises(AuthoringWorkspaceError, match="project-relative"):
        workspace.load_manuscript(project, str((project.root / "storyos.yaml").resolve()))
    assert _tree_snapshot(project.root) == before


def test_workspace_rejects_manifest_manuscript_path_escape(tmp_path):
    project = _make_project(tmp_path / "project")
    manifest_path = project.root / "storyos.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["paths"]["manuscript"] = "../outside"
    _write_yaml(manifest_path, raw)
    reopened = StoryProject.open(project.root)
    with pytest.raises(AuthoringWorkspaceError, match="escapes"):
        AuthoringWorkspace().list_manuscripts(reopened)


def test_workspace_rejects_manuscript_symlink(tmp_path):
    project = _make_project(tmp_path / "project")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = project.root / "manuscript" / "S01" / "EP03_Link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this runner")

    with pytest.raises(AuthoringWorkspaceError, match="symlinks"):
        AuthoringWorkspace().list_manuscripts(project)
    with pytest.raises(AuthoringWorkspaceError, match="symlinks"):
        AuthoringWorkspace().load_manuscript(project, "manuscript/S01/EP03_Link.txt")


def test_workspace_cli_snapshot_entity_and_manuscript(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")

    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "snapshot", str(project.root), "--through", "10"],
    )
    workspace_cli_main()
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["schema"] == "story.authoring-workspace.v1"
    assert snapshot["timeline"]["effective_through_sequence"] == 10

    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "entity", str(project.root), ARIA, "--through", "10"],
    )
    workspace_cli_main()
    entity = json.loads(capsys.readouterr().out)
    assert entity["schema"] == "story.authoring-entity.v1"
    assert entity["through_sequence"] == 10

    path = snapshot["manuscripts"][0]["path"]
    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "manuscript", str(project.root), path],
    )
    workspace_cli_main()
    manuscript = json.loads(capsys.readouterr().out)
    assert manuscript["schema"] == "story.authoring-manuscript.v1"
    assert manuscript["content"].startswith("第一集")
