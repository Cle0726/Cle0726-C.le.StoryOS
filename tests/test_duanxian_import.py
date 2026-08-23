from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from storyos.duanxian_import import DuanxianImportError, DuanxianV39Importer
from storyos.ids import stable_id, validate_id
from storyos.project import StoryProject


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _make_package(root: Path) -> Path:
    for episode in range(1, 37):
        _write(
            root / "01_正文" / "分集" / f"第{episode:02d}集_测试标题{episode:02d}.txt",
            f"第{episode:02d}集\n测试正文 {episode}\n",
        )

    character_lines = [
        "# 角色总表",
        "",
        "## 3. 角色定名总表",
        "",
        "### 3.1 主线角色",
        "",
        "| 原称呼 | 定名 | 英文 | 身份 | 资产 |",
        "|---|---|---|---|---|",
    ]
    for index in range(1, 51):
        character_lines.append(
            f"| 旧名{index:02d} | **角色{index:02d}** | Character {index:02d} | 测试身份{index:02d} | `H{index:02d}` ✅ |"
        )
    character_lines.extend(["", "## 4. 人物穿搭基准", ""])
    _write(root / "02_CANON与大纲" / "02_角色总表.md", "\n".join(character_lines))

    scene_counts: dict[int, int] = {}
    scenes = []
    for index in range(1, 119):
        episode = (index - 1) % 36 + 1
        scene_counts[episode] = scene_counts.get(episode, 0) + 1
        scene_id = f"EP{episode:02d}-S{scene_counts[episode]:02d}"
        scenes.append(
            {
                "scene_id": scene_id,
                "episode": episode,
                "space_time": f"测试地点{index}｜夜",
                "present": f"角色{((index - 1) % 50) + 1:02d}",
                "active_task": f"测试任务{index}",
                "state_diff": f"原始状态变化文本{index}",
                "exit_snapshot": f"原始离场快照{index}",
            }
        )

    ledger = {
        "version": "2.9",
        "rule": "synthetic test ledger",
        "scenes": scenes,
        "editorial_inserts": [
            {
                "insert_id": "EP01-CO01",
                "display_episode": 1,
                "mode": "NOVEL_SAME_EPISODE_FLASHFORWARD",
                "source_scenes": ["EP01-S01"],
                "core_source_scene": "EP01-S01",
                "source_geometry": None,
                "canonical_mutation": False,
                "inherit_state_to_next": False,
                "return_target": "EP01-S01",
                "return_anchor": "测试返回锚点",
                "constraints": "测试插入不继承未来状态。",
            }
        ],
        "current_package_version": "3.9",
        "current_prose_version": "3.9",
    }
    ledger_path = root / "03_连续性状态" / "World_State_Ledger_36集.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    source_files = sorted(path for path in root.rglob("*") if path.is_file())
    hashes = {path.relative_to(root).as_posix(): _sha256(path) for path in source_files}
    manifest = {
        "package_version": "3.9",
        "prose_version": "3.9",
        "world_state_data_revision": "2.9",
        "file_count_total": len(source_files) + 1,
        "hashes_excluding_this_manifest": hashes,
    }
    manifest_path = root / "99_工具与兼容" / "MANIFEST_v3.9.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_stable_import_id_is_reproducible_and_typed():
    first = stable_id("character", "duanxian", "asset:H01")
    second = stable_id("character", "duanxian", "asset:H01")
    different = stable_id("character", "duanxian", "asset:H02")
    assert first == second
    assert first != different
    assert validate_id(first, "character")


def test_v39_plan_has_expected_structure_and_clean_integrity(tmp_path):
    source = _make_package(tmp_path / "source")
    plan = DuanxianV39Importer(source).build_plan()
    assert plan.dirty is False
    assert plan.integrity["matched_hashes"] == plan.integrity["expected_hashes"]
    assert plan.integrity["file_count_mismatch"] is False
    assert len(plan.episodes) == 36
    assert len(plan.characters) == 50
    assert len(plan.scenes) == 118
    assert len(plan.editorial_inserts) == 1
    assert plan.editorial_inserts[0]["canonical_mutation"] is False
    assert len(plan.fingerprint) == 64


def test_import_is_lossless_deterministic_and_noncanonical(tmp_path):
    source = _make_package(tmp_path / "source")
    importer = DuanxianV39Importer(source)
    first = tmp_path / "project-a"
    second = tmp_path / "project-b"

    report_a = importer.apply(first)
    report_b = importer.apply(second)

    assert report_a == report_b
    assert _tree_hashes(first) == _tree_hashes(second)
    assert report_a["counts"]["episodes"] == 36
    assert report_a["counts"]["characters"] == 50
    assert report_a["counts"]["world_state_scenes"] == 118
    assert report_a["counts"]["editorial_inserts"] == 1
    assert report_a["counts"]["canonical_events_materialized"] == 0
    assert report_a["counts"]["canon_facts_materialized"] == 0
    assert report_a["policy"]["natural_language_state_diffs_materialized_as_events"] is False

    project = StoryProject.open(first)
    assert len(project.load_entities()) == 50
    assert project.load_events() == []
    assert project.load_canon_facts() == []
    assert project.load_claims() == []
    assert project.validate_references() == []

    assert len(list((first / "manuscript" / "S01").glob("*.txt"))) == 36
    assert len(list((first / "sources" / "world_state" / "scenes").glob("*.yaml"))) == 118

    original_ep01 = source / "01_正文" / "分集" / "第01集_测试标题01.txt"
    imported_ep01 = first / "manuscript" / "S01" / "EP01_测试标题01.txt"
    assert imported_ep01.read_bytes() == original_ep01.read_bytes()


def test_source_snapshot_is_byte_exact(tmp_path):
    source = _make_package(tmp_path / "source")
    target = tmp_path / "project"
    report = DuanxianV39Importer(source).apply(target)
    snapshot = target / "sources" / "mother_package"
    source_hashes = _tree_hashes(source)
    assert _tree_hashes(snapshot) == source_hashes
    assert report["counts"]["source_snapshot_files"] == len(source_hashes)


def test_dirty_source_is_rejected_unless_explicitly_allowed(tmp_path):
    source = _make_package(tmp_path / "source")
    changed = source / "01_正文" / "分集" / "第01集_测试标题01.txt"
    changed.write_text("被修改的正文\n", encoding="utf-8")

    importer = DuanxianV39Importer(source)
    assert importer.build_plan().dirty is True
    with pytest.raises(DuanxianImportError, match="integrity check failed"):
        importer.apply(tmp_path / "rejected")

    report = importer.apply(tmp_path / "allowed", allow_dirty_source=True)
    assert len(report["integrity"]["mismatches"]) == 1


def test_import_refuses_nonempty_target_and_preserves_author_file(tmp_path):
    source = _make_package(tmp_path / "source")
    target = tmp_path / "existing-project"
    target.mkdir()
    sentinel = target / "my-author-work.txt"
    sentinel.write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(DuanxianImportError, match="target must be empty"):
        DuanxianV39Importer(source).apply(target)
    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"


def test_import_refuses_target_inside_source_package(tmp_path):
    source = _make_package(tmp_path / "source")
    target = source / "generated-project"
    with pytest.raises(DuanxianImportError, match="outside the source mother package"):
        DuanxianV39Importer(source).apply(target)
    assert not target.exists()
