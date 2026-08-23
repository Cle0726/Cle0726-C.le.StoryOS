from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from storyos.authority import CanonFact
from storyos.claims import CandidateClaim
from storyos.entities import StoryEntity
from storyos.events import StoryEvent


@dataclass(frozen=True)
class StoryProject:
    root: Path
    manifest: dict[str, Any]

    @classmethod
    def open(cls, root: str | Path) -> "StoryProject":
        root_path = Path(root).resolve()
        manifest_path = root_path / "storyos.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"missing project manifest: {manifest_path}")
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh) or {}
        if manifest.get("schema") != "story.project.v1":
            raise ValueError("unsupported or missing story.project.v1 manifest")
        return cls(root=root_path, manifest=manifest)

    def iter_event_files(self) -> Iterable[Path]:
        return _iter_data_files(self.root / "events")

    def iter_entity_files(self) -> Iterable[Path]:
        return _iter_data_files(self.root / "entities")

    def iter_canon_files(self) -> Iterable[Path]:
        return _iter_data_files(self.root / "canon")

    def iter_claim_files(self) -> Iterable[Path]:
        return _iter_data_files(self.root / "staging" / "claims")

    def load_events(self) -> list[StoryEvent]:
        events: list[StoryEvent] = []
        seen_ids: set[str] = set()
        for path, raw in _iter_records(self.iter_event_files()):
            if raw.get("schema") not in (None, "story.event.v1"):
                raise ValueError(f"unsupported event schema in {path}")
            data = dict(raw)
            data.pop("schema", None)
            event = StoryEvent.from_mapping(data)
            _ensure_unique(event.id, seen_ids, "event")
            events.append(event)
        return events

    def load_entities(self) -> list[StoryEntity]:
        entities: list[StoryEntity] = []
        seen_ids: set[str] = set()
        for path, raw in _iter_records(self.iter_entity_files()):
            if raw.get("schema") not in (None, "story.entity.v1"):
                raise ValueError(f"unsupported entity schema in {path}")
            data = dict(raw)
            data.pop("schema", None)
            entity = StoryEntity.from_mapping(data)
            _ensure_unique(entity.id, seen_ids, "entity")
            entities.append(entity)
        return entities

    def load_canon_facts(self) -> list[CanonFact]:
        facts: list[CanonFact] = []
        seen_ids: set[str] = set()
        for path, raw in _iter_records(self.iter_canon_files()):
            if raw.get("schema") not in (None, "story.canon.v1"):
                raise ValueError(f"unsupported canon schema in {path}")
            data = dict(raw)
            data.pop("schema", None)
            fact = CanonFact.from_mapping(data)
            _ensure_unique(fact.id, seen_ids, "canon fact")
            facts.append(fact)
        return facts

    def load_claims(self) -> list[CandidateClaim]:
        claims: list[CandidateClaim] = []
        seen_ids: set[str] = set()
        for path, raw in _iter_records(self.iter_claim_files()):
            if raw.get("schema") not in (None, "story.claim.v1"):
                raise ValueError(f"unsupported claim schema in {path}")
            data = dict(raw)
            data.pop("schema", None)
            claim = CandidateClaim.from_mapping(data)
            _ensure_unique(claim.id, seen_ids, "claim")
            claims.append(claim)
        return claims

    def validate_references(self) -> list[str]:
        entities = self.load_entities()
        entity_ids = {entity.id for entity in entities}
        facts = self.load_canon_facts()
        fact_ids = {fact.id for fact in facts}
        errors: list[str] = []

        for fact in facts:
            if fact.subject not in entity_ids:
                errors.append(f"canon fact {fact.id} references missing subject {fact.subject}")

        for event in self.load_events():
            if event.subject not in entity_ids:
                errors.append(f"event {event.id} references missing subject {event.subject}")
            if event.type in {"knowledge.gained", "knowledge.lost"}:
                fact_id = event.payload.get("fact_id")
                if fact_id is not None and str(fact_id) not in fact_ids:
                    errors.append(f"event {event.id} references missing canon fact {fact_id}")

        for claim in self.load_claims():
            if claim.subject not in entity_ids:
                errors.append(f"claim {claim.id} references missing subject {claim.subject}")

        return errors


def _iter_data_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in {".yaml", ".yml", ".json"}
    )


def _iter_records(paths: Iterable[Path]):
    for path in paths:
        data = _load_data(path)
        records = data if isinstance(data, list) else [data]
        for raw in records:
            if not isinstance(raw, dict):
                raise ValueError(f"record must be an object: {path}")
            yield path, raw


def _ensure_unique(value: str, seen: set[str], kind: str) -> None:
    if value in seen:
        raise ValueError(f"duplicate {kind} id: {value}")
    seen.add(value)


def _load_data(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() == ".json":
            return json.load(fh)
        return yaml.safe_load(fh)
