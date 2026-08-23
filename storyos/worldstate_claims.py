from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from storyos.authority import CanonAuthority
from storyos.claims import CandidateClaim, ClaimStatus
from storyos.entities import StoryEntity
from storyos.events import StoryPosition
from storyos.ids import stable_id
from storyos.project import StoryProject


class WorldStateClaimExtractionError(RuntimeError):
    """Raised when imported World State source records cannot be safely analyzed."""


@dataclass(frozen=True)
class UnresolvedEvidence:
    scene_ref: str
    source_scene_id: str
    field: str
    raw: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_ref": self.scene_ref,
            "source_scene_id": self.source_scene_id,
            "field": self.field,
            "raw": self.raw,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WorldStateClaimExtraction:
    claims: tuple[CandidateClaim, ...]
    unresolved: tuple[UnresolvedEvidence, ...]
    scenes_scanned: int
    rule_counts: tuple[tuple[str, int], ...]

    def report(self) -> dict[str, Any]:
        counts = {name: count for name, count in self.rule_counts}
        return {
            "schema": "story.claim-extraction-report.v1",
            "adapter": DuanxianWorldStateClaimExtractor.adapter,
            "sequence_policy": {
                "season_stride": 1_000_000,
                "episode_stride": 10_000,
                "scene_stride": 100,
                "clause_ordinal_range": [0, 99],
            },
            "counts": {
                "scenes_scanned": self.scenes_scanned,
                "claims_total": len(self.claims),
                "unresolved_total": len(self.unresolved),
                "by_rule": counts,
            },
            "claim_ids": [claim.id for claim in self.claims],
            "unresolved": [item.as_dict() for item in self.unresolved],
            "policy": {
                "claims_are_noncanonical": True,
                "story_events_materialized": False,
                "canon_facts_materialized": False,
                "group_references_are_not_inferred": True,
                "condition_and_knowledge_text_is_preserved_raw": True,
            },
        }


class _NameMatcher:
    def __init__(self, entities: Iterable[StoryEntity]):
        entries: list[tuple[str, str, str]] = []
        for entity in entities:
            if entity.kind != "character":
                continue
            for alias in _entity_aliases(entity):
                normalized = _normalize(alias)
                if not _alias_is_safe(normalized):
                    continue
                entries.append((normalized, alias, entity.id))
        self.entries = tuple(
            sorted(set(entries), key=lambda item: (-len(item[0]), item[0], item[2]))
        )

    def mentions(self, text: str) -> tuple[str, ...]:
        normalized = _normalize(text)
        candidates: list[tuple[int, int, str, str]] = []
        for alias, _raw, entity_id in self.entries:
            start = 0
            while True:
                index = normalized.find(alias, start)
                if index < 0:
                    break
                candidates.append((index, index + len(alias), entity_id, alias))
                start = index + 1

        selected: list[tuple[int, int, str]] = []
        occupied: list[tuple[int, int]] = []
        for begin, end, entity_id, alias in sorted(
            candidates,
            key=lambda item: (-(item[1] - item[0]), item[0], item[2], item[3]),
        ):
            if any(begin < used_end and end > used_begin for used_begin, used_end in occupied):
                continue
            occupied.append((begin, end))
            selected.append((begin, end, entity_id))

        selected.sort(key=lambda item: (item[0], item[1], item[2]))
        result: list[str] = []
        for _begin, _end, entity_id in selected:
            if entity_id not in result:
                result.append(entity_id)
        return tuple(result)

    def entry_mentions(self, text: str) -> tuple[str, ...]:
        normalized = _normalize(text)
        candidates: list[tuple[int, int, str, str]] = []
        for alias, _raw, entity_id in self.entries:
            for match in re.finditer(re.escape(alias) + r"\s*entry\b", normalized):
                candidates.append((match.start(), match.start() + len(alias), entity_id, alias))

        selected: list[tuple[int, int, str]] = []
        occupied: list[tuple[int, int]] = []
        for begin, end, entity_id, alias in sorted(
            candidates,
            key=lambda item: (-(item[1] - item[0]), item[0], item[2], item[3]),
        ):
            if any(begin < used_end and end > used_begin for used_begin, used_end in occupied):
                continue
            occupied.append((begin, end))
            selected.append((begin, end, entity_id))
        selected.sort(key=lambda item: (item[0], item[1], item[2]))
        return tuple(dict.fromkeys(entity_id for _begin, _end, entity_id in selected))


class DuanxianWorldStateClaimExtractor:
    """Conservative deterministic extraction from imported Duanxian World State records.

    This layer only creates non-canonical Candidate Claims. Group references are
    deliberately not resolved, and condition/knowledge statements remain raw notes
    instead of being normalized into facts the source text does not explicitly state.
    """

    adapter = "duanxian.world_state.v1"
    _SCENE_RE = re.compile(r"^EP(?P<episode>\d{2})-S(?P<scene>\d{2})$")
    _GROUP_MARKERS = (
        "同上",
        "众人",
        "主队",
        "三人",
        "四人",
        "五人",
        "六人",
        "在场者不变",
        "同前",
    )
    _CONDITION_TERMS = (
        "重伤",
        "受伤",
        "伤势",
        "冻伤",
        "失去知觉",
        "失灵",
        "手抖",
        "止血",
        "流血",
        "昏迷",
        "虚弱",
        "旧伤",
        "疼痛",
    )
    _KNOWLEDGE_TERMS = (
        "知道",
        "确认",
        "发现",
        "看见",
        "看到",
        "听到",
        "认出",
        "意识到",
        "知识被纠正",
        "获得信息",
    )

    def analyze(self, project: StoryProject) -> WorldStateClaimExtraction:
        entities = tuple(project.load_entities())
        matcher = _NameMatcher(entities)
        scene_files = sorted(
            (project.root / "sources" / "world_state" / "scenes").glob("*.yaml")
        )
        if not scene_files:
            raise WorldStateClaimExtractionError("no imported World State scene records found")

        claims: list[CandidateClaim] = []
        unresolved: list[UnresolvedEvidence] = []
        rule_counts: dict[str, int] = {}

        for path in scene_files:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if raw.get("schema") != "story.imported-scene.v1":
                raise WorldStateClaimExtractionError(
                    f"unsupported imported scene schema: {path}"
                )
            source_scene_id = str(raw["source_scene_id"])
            match = self._SCENE_RE.fullmatch(source_scene_id)
            if match is None:
                raise WorldStateClaimExtractionError(
                    f"invalid source scene id: {source_scene_id}"
                )
            episode = int(match.group("episode"))
            scene_number = int(match.group("scene"))
            scene_ref = str(raw["id"])
            source_path = path.relative_to(project.root).as_posix()

            present_raw = str(raw.get("present_raw") or "")
            explicit_present = matcher.mentions(present_raw)
            for subject in explicit_present:
                claims.append(
                    self._claim(
                        subject=subject,
                        predicate="location.observed",
                        value={
                            "scene_ref": scene_ref,
                            "source_scene_id": source_scene_id,
                            "space_time_raw": str(raw.get("space_time") or ""),
                        },
                        episode=episode,
                        scene_number=scene_number,
                        ordinal=0,
                        source_scene_id=source_scene_id,
                        scene_ref=scene_ref,
                        source_path=source_path,
                        field="present_raw",
                        evidence=present_raw,
                        rule="present.explicit_character_location",
                        confidence=0.99,
                    )
                )
                _inc(rule_counts, "present.explicit_character_location")

            if any(marker in present_raw for marker in self._GROUP_MARKERS):
                unresolved.append(
                    UnresolvedEvidence(
                        scene_ref=scene_ref,
                        source_scene_id=source_scene_id,
                        field="present_raw",
                        raw=present_raw,
                        reason="group_reference_not_inferred",
                    )
                )
            elif present_raw and not explicit_present:
                unresolved.append(
                    UnresolvedEvidence(
                        scene_ref=scene_ref,
                        source_scene_id=source_scene_id,
                        field="present_raw",
                        raw=present_raw,
                        reason="no_explicit_character_match",
                    )
                )

            state_diff = str(raw.get("state_diff_raw") or "")
            for index, clause in enumerate(_split_clauses(state_diff), start=1):
                ordinal = min(index, 99)
                produced = False

                for subject in matcher.entry_mentions(clause):
                    claims.append(
                        self._claim(
                            subject=subject,
                            predicate="story.entry_scene",
                            value={
                                "scene_ref": scene_ref,
                                "source_scene_id": source_scene_id,
                            },
                            episode=episode,
                            scene_number=scene_number,
                            ordinal=ordinal,
                            source_scene_id=source_scene_id,
                            scene_ref=scene_ref,
                            source_path=source_path,
                            field="state_diff_raw",
                            evidence=clause,
                            rule="state.entry_explicit",
                            confidence=0.995,
                        )
                    )
                    _inc(rule_counts, "state.entry_explicit")
                    produced = True

                assignment = _assignment_holder(clause, matcher)
                if assignment is not None:
                    item_label, subject = assignment
                    item_key = hashlib.sha256(
                        _normalize(item_label).encode("utf-8")
                    ).hexdigest()[:12]
                    claims.append(
                        self._claim(
                            subject=subject,
                            predicate=f"possession.item.{item_key}",
                            value={
                                "label": item_label,
                                "held": True,
                                "scene_ref": scene_ref,
                                "source_scene_id": source_scene_id,
                            },
                            episode=episode,
                            scene_number=scene_number,
                            ordinal=ordinal,
                            source_scene_id=source_scene_id,
                            scene_ref=scene_ref,
                            source_path=source_path,
                            field="state_diff_raw",
                            evidence=clause,
                            rule="state.assignment_character_holder",
                            confidence=0.97,
                        )
                    )
                    _inc(rule_counts, "state.assignment_character_holder")
                    produced = True

                mentioned = matcher.mentions(clause)
                if len(mentioned) == 1 and any(
                    term in clause for term in self._CONDITION_TERMS
                ):
                    claims.append(
                        self._claim(
                            subject=mentioned[0],
                            predicate="condition.note",
                            value={
                                "text": clause,
                                "scene_ref": scene_ref,
                                "source_scene_id": source_scene_id,
                            },
                            episode=episode,
                            scene_number=scene_number,
                            ordinal=ordinal,
                            source_scene_id=source_scene_id,
                            scene_ref=scene_ref,
                            source_path=source_path,
                            field="state_diff_raw",
                            evidence=clause,
                            rule="state.condition_explicit_character_note",
                            confidence=0.90,
                        )
                    )
                    _inc(rule_counts, "state.condition_explicit_character_note")
                    produced = True

                if len(mentioned) == 1 and any(
                    term in clause for term in self._KNOWLEDGE_TERMS
                ):
                    claims.append(
                        self._claim(
                            subject=mentioned[0],
                            predicate="knowledge.note",
                            value={
                                "text": clause,
                                "scene_ref": scene_ref,
                                "source_scene_id": source_scene_id,
                            },
                            episode=episode,
                            scene_number=scene_number,
                            ordinal=ordinal,
                            source_scene_id=source_scene_id,
                            scene_ref=scene_ref,
                            source_path=source_path,
                            field="state_diff_raw",
                            evidence=clause,
                            rule="state.knowledge_explicit_character_note",
                            confidence=0.88,
                        )
                    )
                    _inc(rule_counts, "state.knowledge_explicit_character_note")
                    produced = True

                if not produced:
                    reason = "no_conservative_rule"
                    if any(marker in clause for marker in self._GROUP_MARKERS):
                        reason = "group_reference_not_inferred"
                    elif len(mentioned) > 1:
                        reason = "multiple_character_mentions_ambiguous"
                    elif not mentioned:
                        reason = "no_explicit_character_subject"
                    unresolved.append(
                        UnresolvedEvidence(
                            scene_ref=scene_ref,
                            source_scene_id=source_scene_id,
                            field="state_diff_raw",
                            raw=clause,
                            reason=reason,
                        )
                    )

        return WorldStateClaimExtraction(
            claims=_dedupe_claims(claims),
            unresolved=tuple(unresolved),
            scenes_scanned=len(scene_files),
            rule_counts=tuple(sorted(rule_counts.items())),
        )

    def persist(
        self,
        project: StoryProject,
        extraction: WorldStateClaimExtraction,
    ) -> dict[str, Any]:
        existing = {claim.id: claim for claim in project.load_claims()}
        target = project.root / "staging" / "claims" / "world_state"
        created = 0
        unchanged = 0

        for claim in extraction.claims:
            current = existing.get(claim.id)
            if current is not None:
                if current != claim:
                    raise WorldStateClaimExtractionError(
                        f"existing claim id has different content: {claim.id}"
                    )
                unchanged += 1
                continue

            source_scene_id = str(claim.source["source_scene_id"])
            destination = target / f"{source_scene_id}--{claim.id[4:12]}.yaml"
            _write_yaml(destination, _claim_mapping(claim))
            existing[claim.id] = claim
            created += 1

        report_path = (
            project.root / "staging" / "extraction" / "world_state" / "report.json"
        )
        _write_json(report_path, extraction.report())
        return {
            "created": created,
            "unchanged": unchanged,
            "claims_total": len(extraction.claims),
            "report": report_path.relative_to(project.root).as_posix(),
        }

    def _claim(
        self,
        *,
        subject: str,
        predicate: str,
        value: Any,
        episode: int,
        scene_number: int,
        ordinal: int,
        source_scene_id: str,
        scene_ref: str,
        source_path: str,
        field: str,
        evidence: str,
        rule: str,
        confidence: float,
    ) -> CandidateClaim:
        sequence = scene_sequence(1, episode, scene_number, ordinal)
        identity = json.dumps(
            {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "source_scene_id": source_scene_id,
                "field": field,
                "evidence": evidence,
                "rule": rule,
                "sequence": sequence,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        claim = CandidateClaim(
            id=stable_id("claim", self.adapter, identity),
            subject=subject,
            predicate=predicate,
            value=value,
            at=StoryPosition(
                sequence=sequence,
                season=1,
                episode=episode,
                scene=scene_number,
            ),
            confidence=confidence,
            source={
                "kind": "deterministic_extraction",
                "adapter": self.adapter,
                "rule": rule,
                "scene_ref": scene_ref,
                "source_scene_id": source_scene_id,
                "source_path": source_path,
                "field": field,
                "evidence": evidence,
            },
            proposed_authority=CanonAuthority.DRAFT,
            status=ClaimStatus.PENDING,
        )
        claim.validate()
        return claim


def scene_sequence(season: int, episode: int, scene: int, ordinal: int = 0) -> int:
    """Stable sequence anchor with room for intra-scene approved events."""
    if season < 0 or episode < 0 or scene < 0 or not 0 <= ordinal <= 99:
        raise ValueError("invalid story position for sequence policy")
    return season * 1_000_000 + episode * 10_000 + scene * 100 + ordinal


def _entity_aliases(entity: StoryEntity) -> tuple[str, ...]:
    values: list[str] = [entity.name, *entity.aliases]
    for key in ("english_name", "source_original_name"):
        value = entity.data.get(key)
        if value:
            values.append(str(value))

    expanded: list[str] = []
    for value in values:
        for part in re.split(r"[/／]", str(value)):
            part = part.strip()
            if not part:
                continue
            expanded.append(part)
            if "·" in part:
                given = part.split("·", 1)[0].strip()
                if given:
                    expanded.append(given)

    result: list[str] = []
    for value in expanded:
        cleaned = value.strip().strip("*`")
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return tuple(result)


def _alias_is_safe(value: str) -> bool:
    if not value or value in {"—", "-"}:
        return False
    if len(value) < 2:
        return False
    if value.isascii() and len(value) < 3:
        return False
    return True


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def _split_clauses(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"[；;]", text) if part.strip())


def _assignment_holder(clause: str, matcher: _NameMatcher) -> tuple[str, str] | None:
    match = re.fullmatch(r"\s*([^=]{1,80}?)\s*=\s*(.+?)\s*", clause)
    if match is None:
        return None
    item_label = match.group(1).strip()
    holder_raw = match.group(2).strip()
    subjects = matcher.mentions(holder_raw)
    if len(subjects) != 1 or len(holder_raw) > 30:
        return None
    return item_label, subjects[0]


def _dedupe_claims(claims: Iterable[CandidateClaim]) -> tuple[CandidateClaim, ...]:
    by_id: dict[str, CandidateClaim] = {}
    for claim in claims:
        previous = by_id.get(claim.id)
        if previous is not None and previous != claim:
            raise WorldStateClaimExtractionError(f"claim id collision: {claim.id}")
        by_id[claim.id] = claim
    return tuple(sorted(by_id.values(), key=lambda claim: (claim.at.sequence, claim.id)))


def _claim_mapping(claim: CandidateClaim) -> dict[str, Any]:
    return {
        "schema": "story.claim.v1",
        "id": claim.id,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "value": claim.value,
        "at": {
            "sequence": claim.at.sequence,
            "season": claim.at.season,
            "episode": claim.at.episode,
            "scene": claim.at.scene,
        },
        "confidence": claim.confidence,
        "source": claim.source,
        "proposed_authority": claim.proposed_authority.name.lower(),
        "status": claim.status.value,
    }


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _inc(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1
