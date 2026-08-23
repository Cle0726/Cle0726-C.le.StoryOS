from __future__ import annotations

import json
from pathlib import Path

import yaml

from storyos.cli import main as cli_main
from storyos.ids import stable_id
from storyos.project import StoryProject
from storyos.worldstate_claims import DuanxianWorldStateClaimExtractor, scene_sequence


KADEN = stable_id("character", "duanxian", "asset:H01")
CELIA = stable_id("character", "duanxian", "asset:H02")
NORA = stable_id("character", "duanxian", "asset:H03")
CECILIA = stable_id("character", "duanxian", "asset:H04")


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
            "id": "project_duanxian_test",
            "name": "断弦之歌测试",
            "language": "zh-CN",
        },
    )
    entities = [
        (KADEN, "凯登·维尔", "caden-vale", ["Caden Vale"]),
        (NORA, "诺拉·凯恩", "nora-kane", ["Nora Kane"]),
        (CELIA, "西莉亚·罗森", "celia-rosen", ["Celia Rosen"]),
        (CECILIA, "塞西莉亚·格兰特", "cecilia-grant", ["Cecilia Grant"]),
    ]
    for entity_id, name, slug, aliases in entities:
        _write_yaml(
            root / "entities" / "characters" / f"{slug}.yaml",
            {
                "schema": "story.entity.v1",
                "id": entity_id,
                "kind": "character",
                "name": name,
                "slug": slug,
                "aliases": aliases,
                "data": {},
            },
        )

    scenes = [
        {
            "source_scene_id": "EP01-S01",
            "episode": 1,
            "space_time": "眠沙旧城→剧院广场｜雨夜",
            "present_raw": "凯登/诺拉",
            "active_task_raw": "第144次赴约",
            "state_diff_raw": "凯登 Entry；节目单=凯登；凯登重伤仍活；凯登确认北线；三人继续移动",
            "exit_snapshot_raw": "二人在入口",
        },
        {
            "source_scene_id": "EP01-S02",
            "episode": 1,
            "space_time": "剧院大厅｜午夜前",
            "present_raw": "同上",
            "active_task_raw": "检查钢琴",
            "state_diff_raw": "节目单角卡入裂缝",
            "exit_snapshot_raw": "保持",
        },
        {
            "source_scene_id": "EP02-S01",
            "episode": 2,
            "space_time": "剧院舞台｜凌晨",
            "present_raw": "凯登/诺拉/西莉亚",
            "active_task_raw": "救援",
            "state_diff_raw": "伤势继续；诺拉确认旅行车仍在广场；塞西莉亚 Entry",
            "exit_snapshot_raw": "继续",
        },
    ]
    for scene in scenes:
        scene_id = stable_id("scene", "duanxian", scene["source_scene_id"])
        _write_yaml(
            root / "sources" / "world_state" / "scenes" / f"{scene['source_scene_id']}.yaml",
            {
                "schema": "story.imported-scene.v1",
                "id": scene_id,
                **scene,
                "canonical_event_materialized": False,
                "source": {
                    "path": "03_连续性状态/World_State_Ledger_36集.json",
                    "ledger_version": "2.9",
                },
            },
        )
    return StoryProject.open(root)


def _claim_signature(extraction):
    return [
        (
            claim.id,
            claim.subject,
            claim.predicate,
            claim.value,
            claim.at.sequence,
            claim.confidence,
            claim.source,
        )
        for claim in extraction.claims
    ]


def test_sequence_policy_reserves_intra_scene_space():
    assert scene_sequence(1, 1, 1, 0) == 1_010_100
    assert scene_sequence(1, 1, 1, 7) == 1_010_107
    assert scene_sequence(1, 2, 1, 0) > scene_sequence(1, 1, 99, 99)


def test_conservative_extraction_emits_observations_and_unresolved(tmp_path):
    project = _make_project(tmp_path / "project")
    extraction = DuanxianWorldStateClaimExtractor().analyze(project)

    assert extraction.scenes_scanned == 3
    predicates = [claim.predicate for claim in extraction.claims]
    assert predicates.count("location.observed") == 5
    assert predicates.count("story.entry_scene") == 2
    assert "condition.note" in predicates
    assert "knowledge.note" in predicates
    assert any(predicate.startswith("possession.item.") for predicate in predicates)

    kaden_locations = [
        claim
        for claim in extraction.claims
        if claim.subject == KADEN and claim.predicate == "location.observed"
    ]
    assert len(kaden_locations) == 2
    assert kaden_locations[0].value["space_time_raw"] == "眠沙旧城→剧院广场｜雨夜"
    assert kaden_locations[0].at.sequence == scene_sequence(1, 1, 1, 0)

    reasons = {(item.source_scene_id, item.reason) for item in extraction.unresolved}
    assert ("EP01-S01", "group_reference_not_inferred") in reasons
    assert ("EP01-S02", "group_reference_not_inferred") in reasons
    assert ("EP02-S01", "no_explicit_character_subject") in reasons


def test_group_reference_is_not_used_to_invent_character_location(tmp_path):
    project = _make_project(tmp_path / "project")
    extraction = DuanxianWorldStateClaimExtractor().analyze(project)
    second_scene_ref = stable_id("scene", "duanxian", "EP01-S02")
    assert not any(
        claim.predicate == "location.observed"
        and claim.value.get("scene_ref") == second_scene_ref
        for claim in extraction.claims
    )


def test_overlapping_names_use_longest_nonoverlapping_entry_match(tmp_path):
    extraction = DuanxianWorldStateClaimExtractor().analyze(
        _make_project(tmp_path / "project")
    )
    entries = [claim for claim in extraction.claims if claim.predicate == "story.entry_scene"]
    ep2_entries = [claim for claim in entries if claim.source["source_scene_id"] == "EP02-S01"]
    assert len(ep2_entries) == 1
    assert ep2_entries[0].subject == CECILIA
    assert not any(claim.subject == CELIA for claim in ep2_entries)


def test_condition_and_knowledge_notes_preserve_raw_evidence(tmp_path):
    extraction = DuanxianWorldStateClaimExtractor().analyze(
        _make_project(tmp_path / "project")
    )
    condition = next(
        claim
        for claim in extraction.claims
        if claim.subject == KADEN and claim.predicate == "condition.note"
    )
    knowledge = next(
        claim
        for claim in extraction.claims
        if claim.subject == KADEN and claim.predicate == "knowledge.note"
    )
    assert condition.value["text"] == "凯登重伤仍活"
    assert condition.source["evidence"] == "凯登重伤仍活"
    assert knowledge.value["text"] == "凯登确认北线"
    assert knowledge.source["evidence"] == "凯登确认北线"


def test_extraction_is_deterministic_across_project_roots(tmp_path):
    first = DuanxianWorldStateClaimExtractor().analyze(_make_project(tmp_path / "a"))
    second = DuanxianWorldStateClaimExtractor().analyze(_make_project(tmp_path / "b"))
    assert _claim_signature(first) == _claim_signature(second)
    assert first.report() == second.report()


def test_persist_is_explicit_idempotent_and_noncanonical(tmp_path):
    project = _make_project(tmp_path / "project")
    extractor = DuanxianWorldStateClaimExtractor()
    extraction = extractor.analyze(project)

    assert project.load_claims() == []
    assert project.load_events() == []
    assert project.load_canon_facts() == []

    first = extractor.persist(project, extraction)
    assert first["created"] == len(extraction.claims)
    assert first["unchanged"] == 0
    assert len(project.load_claims()) == len(extraction.claims)
    assert project.load_events() == []
    assert project.load_canon_facts() == []
    assert project.validate_references() == []

    second = extractor.persist(project, extractor.analyze(project))
    assert second["created"] == 0
    assert second["unchanged"] == len(extraction.claims)

    report = json.loads((project.root / second["report"]).read_text(encoding="utf-8"))
    assert report["policy"]["claims_are_noncanonical"] is True
    assert report["policy"]["story_events_materialized"] is False
    assert report["policy"]["canon_facts_materialized"] is False


def test_cli_defaults_to_dry_run_and_write_is_explicit(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path / "project")

    monkeypatch.setattr("sys.argv", ["storyos", "worldstate-claims", str(project.root)])
    cli_main()
    dry = json.loads(capsys.readouterr().out)
    assert dry["persistence"]["write"] is False
    assert project.load_claims() == []
    assert project.load_events() == []
    assert project.load_canon_facts() == []

    monkeypatch.setattr(
        "sys.argv",
        ["storyos", "worldstate-claims", str(project.root), "--write"],
    )
    cli_main()
    written = json.loads(capsys.readouterr().out)
    assert written["persistence"]["created"] == written["counts"]["claims_total"]
    assert len(project.load_claims()) == written["counts"]["claims_total"]
    assert project.load_events() == []
    assert project.load_canon_facts() == []
