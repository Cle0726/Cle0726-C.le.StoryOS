from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from storyos.entities import StoryEntity
from storyos.ids import stable_id


class DuanxianImportError(RuntimeError):
    """Raised when a Duanxian v3.9 mother package cannot be safely imported."""


@dataclass(frozen=True)
class DuanxianCharacterRecord:
    id: str
    name: str
    slug: str
    aliases: tuple[str, ...]
    data: dict[str, Any]

    def as_entity_mapping(self) -> dict[str, Any]:
        return {
            "schema": "story.entity.v1",
            "id": self.id,
            "kind": "character",
            "name": self.name,
            "slug": self.slug,
            "aliases": list(self.aliases),
            "data": self.data,
        }


@dataclass(frozen=True)
class DuanxianImportPlan:
    manifest: dict[str, Any]
    integrity: dict[str, Any]
    episodes: tuple[dict[str, Any], ...]
    characters: tuple[DuanxianCharacterRecord, ...]
    scenes: tuple[dict[str, Any], ...]
    editorial_inserts: tuple[dict[str, Any], ...]
    source_files: tuple[Path, ...]
    fingerprint: str

    @property
    def dirty(self) -> bool:
        return bool(
            self.integrity["missing"]
            or self.integrity["mismatches"]
            or self.integrity["file_count_mismatch"]
        )


class DuanxianV39Importer:
    """Lossless deterministic importer for the Duanxian Season 1 v3.9 mother package.

    Natural-language World State and Canon documents are preserved as source
    material instead of being promoted directly to StoryEvent/CanonFact.
    """

    adapter = "duanxian.v3_9"
    source_namespace = "duanxian"
    project_name = "断弦之歌"

    _MANIFEST = Path("99_工具与兼容/MANIFEST_v3.9.json")
    _CHARACTERS = Path("02_CANON与大纲/02_角色总表.md")
    _WORLD_LEDGER = Path("03_连续性状态/World_State_Ledger_36集.json")
    _EPISODE_DIR = Path("01_正文/分集")

    def __init__(self, source_root: str | Path):
        self.source_root = Path(source_root).resolve()

    def build_plan(self) -> DuanxianImportPlan:
        manifest = self._load_manifest()
        source_files = self._source_files()
        integrity = self._verify_integrity(manifest, source_files)
        episodes = tuple(self._load_episodes())
        characters = tuple(self._load_characters())
        scenes, editorial_inserts = self._load_world_state()

        self._validate_expected_structure(
            episodes=episodes,
            characters=characters,
            scenes=scenes,
            editorial_inserts=editorial_inserts,
        )

        fingerprint_payload = {
            "adapter": self.adapter,
            "package_version": str(manifest.get("package_version")),
            "prose_version": str(manifest.get("prose_version")),
            "manifest_hashes": manifest.get("hashes_excluding_this_manifest", {}),
            "episodes": [[item["episode"], item["title"], item["sha256"]] for item in episodes],
            "character_ids": [item.id for item in characters],
            "scene_ids": [item["id"] for item in scenes],
            "editorial_insert_ids": [item["id"] for item in editorial_inserts],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        return DuanxianImportPlan(
            manifest=manifest,
            integrity=integrity,
            episodes=episodes,
            characters=characters,
            scenes=scenes,
            editorial_inserts=editorial_inserts,
            source_files=source_files,
            fingerprint=fingerprint,
        )

    def apply(
        self,
        target_root: str | Path,
        *,
        allow_dirty_source: bool = False,
    ) -> dict[str, Any]:
        target = Path(target_root).resolve()
        self._assert_target_outside_source(target)
        self._assert_empty_target(target)
        plan = self.build_plan()
        if plan.dirty and not allow_dirty_source:
            raise DuanxianImportError(
                "source integrity check failed; pass allow_dirty_source=True only for an intentional modified package"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{target.name}.storyos-import-",
            dir=target.parent,
        ) as temp_dir:
            staging = Path(temp_dir) / "project"
            staging.mkdir()
            report = self._write_project(staging, plan)

            if target.exists():
                target.rmdir()
            staging.replace(target)
            return report

    def _write_project(self, target: Path, plan: DuanxianImportPlan) -> dict[str, Any]:
        project_manifest = {
            "schema": "story.project.v1",
            "id": "project_duanxian",
            "name": self.project_name,
            "language": "zh-CN",
            "paths": {
                "manuscript": "manuscript",
                "entities": "entities",
                "events": "events",
                "canon": "canon",
                "sources": "sources",
            },
            "import": {
                "adapter": self.adapter,
                "source_namespace": self.source_namespace,
                "source_package_version": str(plan.manifest["package_version"]),
                "source_prose_version": str(plan.manifest["prose_version"]),
                "plan_fingerprint": plan.fingerprint,
            },
        }
        _write_yaml(target / "storyos.yaml", project_manifest)

        snapshot_root = target / "sources" / "mother_package"
        copied = 0
        for source_path in plan.source_files:
            relative = source_path.relative_to(self.source_root)
            destination = snapshot_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            copied += 1

        for episode in plan.episodes:
            destination = target / "manuscript" / "S01" / f"EP{episode['episode']:02d}_{episode['title']}.txt"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(episode["source_path"], destination)
            if _sha256(destination) != episode["sha256"]:
                raise DuanxianImportError(
                    f"episode working copy hash changed during import: {destination.name}"
                )

        for character in plan.characters:
            mapping = character.as_entity_mapping()
            StoryEntity.from_mapping({key: value for key, value in mapping.items() if key != "schema"})
            short_id = character.id.split("_", 1)[1][:8]
            destination = target / "entities" / "characters" / f"{character.slug}--{short_id}.yaml"
            _write_yaml(destination, mapping)

        for scene in plan.scenes:
            _write_yaml(
                target / "sources" / "world_state" / "scenes" / f"{scene['source_scene_id']}.yaml",
                scene,
            )

        for insert in plan.editorial_inserts:
            _write_yaml(
                target / "sources" / "world_state" / "editorial_inserts" / f"{insert['insert_id']}.yaml",
                insert,
            )

        report = {
            "schema": "story.import-report.v1",
            "adapter": self.adapter,
            "plan_fingerprint": plan.fingerprint,
            "source": {
                "package_version": str(plan.manifest["package_version"]),
                "prose_version": str(plan.manifest["prose_version"]),
                "world_state_revision": str(plan.manifest["world_state_data_revision"]),
                "file_count_total": int(plan.manifest["file_count_total"]),
            },
            "integrity": plan.integrity,
            "counts": {
                "source_snapshot_files": copied,
                "episodes": len(plan.episodes),
                "characters": len(plan.characters),
                "world_state_scenes": len(plan.scenes),
                "editorial_inserts": len(plan.editorial_inserts),
                "canonical_events_materialized": 0,
                "canon_facts_materialized": 0,
            },
            "policy": {
                "source_snapshot_copied": True,
                "episode_working_copies_byte_preserved": True,
                "natural_language_state_diffs_materialized_as_events": False,
                "canon_documents_materialized_as_facts": False,
                "staged_claims_created_from_state_diff": False,
            },
            "warnings": [
                "World State scene text is preserved as imported source records only; state_diff is not auto-promoted to StoryEvent.",
                "Canon/outline documents are preserved in the source snapshot but are not auto-promoted to CanonFact in this import layer.",
            ],
        }
        _write_json(target / "imports" / "duanxian_v3_9" / "report.json", report)
        return report

    def _load_manifest(self) -> dict[str, Any]:
        path = self.source_root / self._MANIFEST
        if not path.is_file():
            raise DuanxianImportError(f"missing v3.9 manifest: {path}")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if str(data.get("package_version")) != "3.9":
            raise DuanxianImportError(
                f"unsupported Duanxian package version: {data.get('package_version')!r}"
            )
        if not isinstance(data.get("hashes_excluding_this_manifest"), dict):
            raise DuanxianImportError("invalid v3.9 manifest hash table")
        return data

    def _source_files(self) -> tuple[Path, ...]:
        if not self.source_root.is_dir():
            raise DuanxianImportError(f"source root is not a directory: {self.source_root}")
        files: list[Path] = []
        for path in self.source_root.rglob("*"):
            if path.is_symlink():
                raise DuanxianImportError(f"symlink source is not supported: {path}")
            if path.is_file():
                files.append(path)
        return tuple(sorted(files, key=lambda path: path.relative_to(self.source_root).as_posix()))

    def _verify_integrity(
        self,
        manifest: dict[str, Any],
        source_files: tuple[Path, ...],
    ) -> dict[str, Any]:
        matched = 0
        missing: list[str] = []
        mismatches: list[dict[str, str]] = []
        expected_hashes = manifest["hashes_excluding_this_manifest"]

        for relative, expected in sorted(expected_hashes.items()):
            path = self.source_root / relative
            if not path.is_file():
                missing.append(relative)
                continue
            actual = _sha256(path)
            if actual != expected:
                mismatches.append({"path": relative, "expected": str(expected), "actual": actual})
            else:
                matched += 1

        declared_file_count = int(manifest.get("file_count_total", 0))
        actual_file_count = len(source_files)
        return {
            "expected_hashes": len(expected_hashes),
            "matched_hashes": matched,
            "missing": missing,
            "mismatches": mismatches,
            "declared_file_count": declared_file_count,
            "actual_file_count": actual_file_count,
            "file_count_mismatch": declared_file_count != actual_file_count,
        }

    def _load_episodes(self) -> Iterable[dict[str, Any]]:
        directory = self.source_root / self._EPISODE_DIR
        if not directory.is_dir():
            raise DuanxianImportError(f"missing episode directory: {directory}")

        for path in sorted(directory.glob("第*集_*.txt")):
            match = re.fullmatch(r"第(\d{2})集_(.+)\.txt", path.name)
            if match is None:
                continue
            yield {
                "episode": int(match.group(1)),
                "title": match.group(2),
                "source_path": path,
                "source_relative_path": path.relative_to(self.source_root).as_posix(),
                "sha256": _sha256(path),
            }

    def _load_characters(self) -> Iterable[DuanxianCharacterRecord]:
        relative = self._CHARACTERS.as_posix()
        path = self.source_root / self._CHARACTERS
        if not path.is_file():
            raise DuanxianImportError(f"missing character authority table: {path}")

        lines = path.read_text(encoding="utf-8-sig").splitlines()
        in_roster = False
        section: str | None = None
        seen_ids: set[str] = set()

        for line in lines:
            if line.startswith("## 3. "):
                in_roster = True
                continue
            if in_roster and line.startswith("## 4."):
                break
            if not in_roster:
                continue

            heading = re.match(r"### (3\.[1-5])\s+(.+)", line)
            if heading:
                section = heading.group(1)
                continue
            if section is None or not line.startswith("|"):
                continue

            cells = [_clean_markdown(cell) for cell in line.strip().strip("|").split("|")]
            if len(cells) != 5:
                continue
            if cells[0] == "原称呼" or cells[0].startswith("---"):
                continue

            old_name, named, english, identity, asset = cells
            names = _split_slash_values(named)
            display_name = names[-1] if names else named
            english_names = _split_slash_values(english)
            canonical_english = english_names[-1] if english_names else english
            asset_match = re.search(r"\b([HT]\d{2})\b", asset)
            asset_code = asset_match.group(1) if asset_match else None
            identity_key = f"asset:{asset_code}" if asset_code else f"english:{canonical_english.casefold()}"
            character_id = stable_id("character", self.source_namespace, identity_key)
            if character_id in seen_ids:
                raise DuanxianImportError(f"duplicate deterministic character identity: {identity_key}")
            seen_ids.add(character_id)

            aliases: list[str] = []
            for alias in _split_slash_values(old_name) + names[:-1] + english_names:
                if alias and alias != display_name and alias not in aliases:
                    aliases.append(alias)

            markers = [marker for marker in ("✅", "⬜", "⚠️", "🔒") if marker in asset]
            yield DuanxianCharacterRecord(
                id=character_id,
                name=display_name,
                slug=_slugify(canonical_english),
                aliases=tuple(aliases),
                data={
                    "english_name": canonical_english,
                    "identity": identity,
                    "asset_code": asset_code,
                    "asset_markers": markers,
                    "asset_raw": asset,
                    "source_original_name": old_name,
                    "source_table_section": section,
                    "source_identity_key": identity_key,
                    "source": {"path": relative, "package_version": "3.9"},
                },
            )

    def _load_world_state(self) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        relative = self._WORLD_LEDGER.as_posix()
        path = self.source_root / self._WORLD_LEDGER
        if not path.is_file():
            raise DuanxianImportError(f"missing World State ledger: {path}")
        ledger = json.loads(path.read_text(encoding="utf-8-sig"))
        ledger_version = str(ledger.get("version", ""))

        scenes: list[dict[str, Any]] = []
        for source_scene in ledger.get("scenes", []):
            source_scene_id = str(source_scene["scene_id"])
            scenes.append(
                {
                    "schema": "story.imported-scene.v1",
                    "id": stable_id("scene", self.source_namespace, source_scene_id),
                    "source_scene_id": source_scene_id,
                    "episode": int(source_scene["episode"]),
                    "space_time": str(source_scene.get("space_time", "")),
                    "present_raw": str(source_scene.get("present", "")),
                    "active_task_raw": str(source_scene.get("active_task", "")),
                    "state_diff_raw": str(source_scene.get("state_diff", "")),
                    "exit_snapshot_raw": str(source_scene.get("exit_snapshot", "")),
                    "canonical_event_materialized": False,
                    "source": {"path": relative, "ledger_version": ledger_version},
                }
            )

        editorial_inserts: list[dict[str, Any]] = []
        for source_insert in ledger.get("editorial_inserts", []):
            insert_id = str(source_insert["insert_id"])
            editorial_inserts.append(
                {
                    "schema": "story.imported-editorial-insert.v1",
                    "id": stable_id("scene", self.source_namespace, insert_id),
                    **dict(source_insert),
                    "source": {"path": relative, "ledger_version": ledger_version},
                }
            )

        return tuple(scenes), tuple(editorial_inserts)

    def _validate_expected_structure(
        self,
        *,
        episodes: tuple[dict[str, Any], ...],
        characters: tuple[DuanxianCharacterRecord, ...],
        scenes: tuple[dict[str, Any], ...],
        editorial_inserts: tuple[dict[str, Any], ...],
    ) -> None:
        episode_numbers = [item["episode"] for item in episodes]
        if episode_numbers != list(range(1, 37)):
            raise DuanxianImportError(f"expected episodes 1..36, got {episode_numbers}")
        if len(characters) != 50:
            raise DuanxianImportError(f"expected 50 role-table characters, got {len(characters)}")
        if len(scenes) != 118:
            raise DuanxianImportError(f"expected 118 World State scenes, got {len(scenes)}")
        if len(editorial_inserts) != 1:
            raise DuanxianImportError(f"expected 1 editorial insert, got {len(editorial_inserts)}")
        insert = editorial_inserts[0]
        if insert.get("canonical_mutation") is not False:
            raise DuanxianImportError(
                "v3.9 editorial insert must remain non-canonical-mutation source data"
            )

    def _assert_target_outside_source(self, target: Path) -> None:
        if target == self.source_root or self.source_root in target.parents:
            raise DuanxianImportError(f"target must be outside the source mother package: {target}")

    @staticmethod
    def _assert_empty_target(target: Path) -> None:
        if not target.exists():
            return
        if not target.is_dir():
            raise DuanxianImportError(f"target is not a directory: {target}")
        if any(target.iterdir()):
            raise DuanxianImportError(
                f"target must be empty to protect existing author work: {target}"
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_markdown(value: str) -> str:
    return re.sub(r"\*\*|`", "", value).strip()


def _split_slash_values(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[/／]", value)
        if part.strip() and part.strip() != "—"
    ]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "character"


def _write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
