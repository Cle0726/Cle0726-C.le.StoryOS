from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from storyos.ids import stable_id
from storyos.project import StoryProject
from storyos.scene_workspace import SceneWorkspace, SceneWorkspaceError
from storyos.workspace import AuthoringWorkspaceError
from storyos.workspace_cli import main as workspace_cli_main


ARIA = stable_id("character", "scene-workspace-test", "aria")
CADEN = stable_id("character", "scene-workspace-test", "caden")
PLOT_SIGNAL = stable_id("plot", "scene-workspace-test", "signal")
FACT_SAFE = stable_id("canon", "scene-workspace-test", "safe-fact")
FACT_FUTURE = stable_id("canon", "scene-workspace-test", "future-fact")
FACT_CONFLICT_A = stable_id("canon", "scene-workspace-test", "conflict-a")
FACT_CONFLICT_B = stable_id("canon", "scene-workspace-test", "conflict-b")


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _entity(root: Path, entity_id: str, kind: str, name: str, slug: str) -> None:
    _write_yaml(
        root / "entities" / f"{slug}.yaml",
        {
            "schema": "story.entity.v1",
            "id": entity_id,
            "kind": kind,
            "name": name,
            "slug": slug,
            "aliases": [],
            "data": {"private_note": f"author-only-{slug}"},
        },
    )


def _event(
    root: Path,
    slug: str,
    subject: str,
    event_type: str,
    sequence: int,
    episode: int,
    payload: dict,
) -> None:
    _write_yaml(
        root / "events" / f"{slug}.yaml",
        {
            "schema": "story.event.v1",
            "id": stable_id("event", "scene-workspace-test", slug),
            "subject": subject,
            "type": event_type,
            "at": {"sequence": sequence, "season": 1, "episode": episode, "scene": 1},
            "payload": payload,
            "source": {"kind": "test"},
        },
    )


def _canon(
    root: Path,
    fact_id: str,
    slug: str,
    subject: str,
    predicate: str,
    value,
    *,
    reveal_at: int | None = None,
) -> None:
    row = {
        "schema": "story.canon.v1",
        "id": fact_id,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "authority": "current",
        "valid_from": 0,
        "source": {"kind": "test"},
    }
    if reveal_at is not None:
        row["reveal_at"] = reveal_at
    _write_yaml(root / "canon" / f"{slug}.yaml", row)


def _make_project(root: Path) -> StoryProject:
    _write_yaml(
        root / "storyos.yaml",
        {
            "schema": "story.project.v1",
            "id": "scene-workspace-test",
            "name": "Scene Workspace Test",
            "language": "zh-CN",
            "paths": {"manuscript": "manuscript"},
        },
    )
    _entity(root, ARIA, "character", "Aria", "aria")
    _entity(root, CADEN, "character", "Caden", "caden")
    _entity(root, PLOT_SIGNAL, "plot", "Broken Signal", "broken-signal")

    _canon(
        root,
        FACT_SAFE,
        "safe-fact",
        ARIA,
        "memory.safe",
        "old-city-bell",
        reveal_at=5,
    )
    _canon(
        root,
        FACT_FUTURE,
        "future-fact",
        CADEN,
        "secret.future",
        "sealed-platform",
        reveal_at=30,
    )
    _canon(root, FACT_CONFLICT_A, "conflict-a", CADEN, "identity.route", "north")
    _canon(root, FACT_CONFLICT_B, "conflict-b", CADEN, "identity.route", "south")

    _event(root, "aria-location", ARIA, "location.set", 10, 1, {"value": "old-city"})
    _event(root, "caden-location", CADEN, "location.set", 12, 1, {"value": "station"})
    _event(root, "aria-knows", ARIA, "knowledge.gained", 14, 1, {"fact_id": FACT_SAFE})
    _event(root, "caden-knows", CADEN, "knowledge.gained", 18, 2, {"fact_id": FACT_FUTURE})
    _event(root, "aria-injury", ARIA, "injury.left_hand.set", 20, 2, {"value": "numb"})
    _event(root, "plot-resolved", PLOT_SIGNAL, "plot.resolved", 30, 3, {"plot_id": PLOT_SIGNAL})

    manuscript = root / "manuscript" / "S01"
    manuscript.mkdir(parents=True, exist_ok=True)
    (manuscript / "EP01_First.txt").write_text("第一集\n旧城。\n", encoding="utf-8", newline="\n")
    (manuscript / "EP02_Second.txt").write_text("第二集\n站台。\n", encoding="utf-8", newline="\n")
    (manuscript / "EP03_Third.txt").write_text("第三集\n信号。\n", encoding="utf-8", newline="\n")
    return StoryProject.open(root)


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_scene_workspace_uses_episode_end_boundary_and_is_read_only(tmp_path):
    project = _make_project(tmp_path / "project")
    before = _tree_snapshot(project.root)
    service = SceneWorkspace()

    first = service.build(project, "manuscript/S01/EP02_Second.txt")
    second = service.build(project, "manuscript/S01/EP02_Second.txt")

    assert first == second
    assert first["schema"] == "story.authoring-scene-workspace.v1"
    assert first["mode"] == "author"
    assert first["timeline"]["effective_through_sequence"] == 20
    assert first["timeline"]["boundary_source"] == "episode_end"
    assert first["manuscript"]["episode"] == 2
    assert "content" not in first["manuscript"]
    assert first["navigation"]["previous"]["episode"] == 1
    assert first["navigation"]["next"]["episode"] == 3

    aria = next(row for row in first["characters"] if row["id"] == ARIA)
    caden = next(row for row in first["characters"] if row["id"] == CADEN)
    assert aria["location"] == "old-city"
    assert aria["state"]["injury.left_hand"] == "numb"
    assert aria["knowledge"]["visible"][0]["value"] == "old-city-bell"
    assert caden["location"] == "station"
    assert caden["knowledge"]["visible"][0]["value"] == "sealed-platform"

    assert len(first["canon_conflicts"]) == 1
    assert first["canon_conflicts"][0]["predicate"] == "identity.route"
    assert [row["name"] for row in first["open_plots"]] == ["Broken Signal"]
    assert first["workflow_attention"]["reference_errors"] == 0
    assert first["policy"]["read_only"] is True
    assert first["policy"]["manuscript_content_included"] is False
    assert _tree_snapshot(project.root) == before


def test_explicit_sequence_can_move_inside_selected_episode(tmp_path):
    project = _make_project(tmp_path / "project")
    view = SceneWorkspace().build(
        project,
        "manuscript/S01/EP02_Second.txt",
        through_sequence=18,
    )

    assert view["timeline"]["effective_through_sequence"] == 18
    assert view["timeline"]["boundary_source"] == "explicit_sequence"
    aria = next(row for row in view["characters"] if row["id"] == ARIA)
    assert "injury.left_hand" not in aria["state"]
    assert [row["sequence"] for row in view["episode_events"]] == [18]


def test_pov_mode_exposes_only_pov_state_and_reveal_guarded_knowledge(tmp_path):
    project = _make_project(tmp_path / "project")
    service = SceneWorkspace()

    aria_view = service.build(
        project,
        "manuscript/S01/EP02_Second.txt",
        pov_entity_id=ARIA,
    )
    assert aria_view["mode"] == "pov"
    assert [row["id"] for row in aria_view["characters"]] == [ARIA]
    assert aria_view["characters"][0]["knowledge"]["visible"][0]["value"] == "old-city-bell"
    assert aria_view["canon_conflicts"] == []
    assert aria_view["open_plots"] == []
    assert aria_view["workflow_attention"] == {}
    assert aria_view["policy"]["pov_safe"] is True
    assert aria_view["policy"]["other_character_state_exposed"] is False

    serialized = json.dumps(aria_view, ensure_ascii=False, sort_keys=True)
    assert "station" not in serialized
    assert "sealed-platform" not in serialized
    assert "north" not in serialized
    assert "south" not in serialized
    assert "author-only-caden" not in serialized

    caden_view = service.build(
        project,
        "manuscript/S01/EP02_Second.txt",
        pov_entity_id=CADEN,
    )
    knowledge = caden_view["characters"][0]["knowledge"]
    assert knowledge["visible"] == []
    assert knowledge["hidden_by_reveal"] == 1
    assert "sealed-platform" not in json.dumps(caden_view, ensure_ascii=False)


def test_plot_is_closed_after_resolution_boundary(tmp_path):
    project = _make_project(tmp_path / "project")
    view = SceneWorkspace().build(project, "manuscript/S01/EP03_Third.txt")
    assert view["timeline"]["effective_through_sequence"] == 30
    assert view["open_plots"] == []


def test_scene_workspace_rejects_invalid_boundary_pov_and_path_without_writes(tmp_path):
    project = _make_project(tmp_path / "project")
    before = _tree_snapshot(project.root)
    service = SceneWorkspace()

    with pytest.raises(SceneWorkspaceError, match="through_sequence"):
        service.build(
            project,
            "manuscript/S01/EP02_Second.txt",
            through_sequence=-1,
        )
    with pytest.raises(SceneWorkspaceError, match="unknown POV"):
        service.build(
            project,
            "manuscript/S01/EP02_Second.txt",
            pov_entity_id=ARIA + "x",
        )
    with pytest.raises(AuthoringWorkspaceError, match="escapes"):
        service.build(project, "manuscript/../storyos.yaml")
    assert _tree_snapshot(project.root) == before


def test_scene_workspace_cli_author_and_pov_protocol(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")
    path = "manuscript/S01/EP02_Second.txt"

    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "scene", str(project.root), path],
    )
    workspace_cli_main()
    author = json.loads(capsys.readouterr().out)
    assert author["schema"] == "story.authoring-scene-workspace.v1"
    assert author["mode"] == "author"
    assert author["timeline"]["effective_through_sequence"] == 20
    assert "content" not in author["manuscript"]

    monkeypatch.setattr(
        "sys.argv",
        ["storyos-workspace", "scene", str(project.root), path, "--pov", CADEN],
    )
    workspace_cli_main()
    pov = json.loads(capsys.readouterr().out)
    assert pov["mode"] == "pov"
    assert [row["id"] for row in pov["characters"]] == [CADEN]
    assert pov["canon_conflicts"] == []
    assert "sealed-platform" not in json.dumps(pov, ensure_ascii=False)
