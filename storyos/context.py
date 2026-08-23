from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from itertools import islice
from typing import Any, Iterable, Mapping, Protocol

from storyos import __version__
from storyos.authority import CanonFact, CanonResolver
from storyos.knowledge import KnowledgeTimeline
from storyos.project import StoryProject
from storyos.state import StoryStateProjector


class ContextMode(str, Enum):
    AUTHOR = "author"
    POV = "pov"


@dataclass(frozen=True)
class RetrievalHit:
    ref: str
    score: float


class SemanticRetriever(Protocol):
    def search(self, query: str, *, limit: int = 8) -> Iterable[RetrievalHit]: ...


@dataclass(frozen=True)
class ContextRequest:
    through_sequence: int
    participants: tuple[str, ...] = ()
    pov: str | None = None
    pinned: tuple[str, ...] = ()
    semantic_query: str | None = None
    semantic_limit: int = 8
    max_chars: int = 12000
    mode: ContextMode = ContextMode.POV
    pov_state_keys: tuple[str, ...] = ("location",)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ContextMode):
            object.__setattr__(self, "mode", ContextMode(self.mode))
        object.__setattr__(self, "participants", _unique(self.participants))
        object.__setattr__(self, "pinned", _unique(self.pinned))
        object.__setattr__(self, "pov_state_keys", _unique(self.pov_state_keys))

    def validate(self) -> None:
        if self.through_sequence < 0:
            raise ValueError("through_sequence must be >= 0")
        if self.semantic_limit < 0:
            raise ValueError("semantic_limit must be >= 0")
        if self.max_chars < 0:
            raise ValueError("max_chars must be >= 0")
        if self.mode is ContextMode.POV and self.pov is None:
            raise ValueError("POV context requires a pov entity")
        if any(not str(key).strip() for key in self.pov_state_keys):
            raise ValueError("pov_state_keys must contain non-empty keys")


@dataclass(frozen=True)
class ContextItem:
    ref: str
    kind: str
    content: str
    reasons: tuple[str, ...]
    priority: int
    required: bool = False

    @property
    def char_count(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class ExcludedContextItem:
    ref: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ContextManifest:
    request: ContextRequest
    retrieval_hits: tuple[RetrievalHit, ...]
    included: tuple[ContextItem, ...]
    excluded: tuple[ExcludedContextItem, ...]

    @property
    def used_chars(self) -> int:
        return sum(item.char_count for item in self.included)

    def render(self) -> str:
        return "\n\n".join(item.content for item in self.included)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "story.context.v1",
            "compiler_version": __version__,
            "request": {
                "through_sequence": self.request.through_sequence,
                "mode": self.request.mode.value,
                "pov": self.request.pov,
                "participants": list(self.request.participants),
                "pinned": list(self.request.pinned),
                "semantic_query": self.request.semantic_query,
                "semantic_limit": self.request.semantic_limit,
                "pov_state_keys": list(self.request.pov_state_keys),
            },
            "budget": {
                "max_chars": self.request.max_chars,
                "used_chars": self.used_chars,
            },
            "retrieval_hits": [
                {"ref": hit.ref, "score": hit.score} for hit in self.retrieval_hits
            ],
            "included": [
                {
                    "ref": item.ref,
                    "kind": item.kind,
                    "content": item.content,
                    "reasons": list(item.reasons),
                    "priority": item.priority,
                    "required": item.required,
                    "char_count": item.char_count,
                }
                for item in self.included
            ],
            "excluded": [
                {"ref": item.ref, "reason": item.reason, "detail": item.detail}
                for item in self.excluded
            ],
        }


@dataclass
class _Candidate:
    ref: str
    priority: int
    required: bool = False
    reasons: set[str] = field(default_factory=set)


class ContextCompiler:
    """Deterministic context compiler with semantic retrieval behind safety gates.

    Retrieval only proposes references. Every reference still passes timeline,
    Canon authority, reveal, POV knowledge and entity-binding checks before it can
    enter model-visible context.
    """

    def __init__(
        self,
        project: StoryProject,
        *,
        retriever: SemanticRetriever | None = None,
        dependencies: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.project = project
        self.retriever = retriever
        self.dependencies = {
            str(ref): tuple(str(item) for item in values)
            for ref, values in (dependencies or {}).items()
        }
        self._canon = CanonResolver()

    def compile(self, request: ContextRequest) -> ContextManifest:
        request.validate()
        entities = {entity.id: entity for entity in self.project.load_entities()}
        facts = {fact.id: fact for fact in self.project.load_canon_facts()}
        events = self.project.load_events()
        states = StoryStateProjector().project(
            events, through_sequence=request.through_sequence
        )
        knowledge = KnowledgeTimeline(events)

        excluded: list[ExcludedContextItem] = []
        candidates: dict[str, _Candidate] = {}

        def add(ref: str, reason: str, priority: int, *, required: bool = False) -> None:
            current = candidates.get(ref)
            if current is None:
                current = _Candidate(ref=ref, priority=priority, required=required)
                candidates[ref] = current
            current.priority = max(current.priority, priority)
            current.required = current.required or required
            current.reasons.add(reason)

        for entity_id in request.participants:
            if entity_id not in entities:
                excluded.append(ExcludedContextItem(entity_id, "unknown_participant"))
                continue
            add(entity_id, "scene_participant", 1000, required=True)
            add(_state_ref(entity_id, request.through_sequence), "participant_state", 950, required=True)

        if request.pov is not None:
            if request.pov not in entities:
                excluded.append(ExcludedContextItem(request.pov, "unknown_pov"))
            else:
                add(request.pov, "pov", 1100, required=True)
                add(_state_ref(request.pov, request.through_sequence), "pov_state", 1050, required=True)

        pov_bound = _pov_bound_entities(
            request=request,
            states=states,
            known_entity_ids=set(entities),
        )

        for ref in request.pinned:
            add(ref, "manual_pin", 900)

        subjects = set(request.participants)
        if request.pov is not None:
            subjects.add(request.pov)
        grouped: dict[tuple[str, str], list[CanonFact]] = {}
        for fact in facts.values():
            if fact.subject in subjects:
                grouped.setdefault((fact.subject, fact.predicate), []).append(fact)

        for (subject, predicate), group in grouped.items():
            resolution = self._canon.resolve(
                group,
                subject=subject,
                predicate=predicate,
                through_sequence=request.through_sequence,
            )
            if resolution.ambiguous:
                excluded.extend(
                    ExcludedContextItem(fact.id, "canon_ambiguous")
                    for fact in resolution.conflicts
                )
            elif resolution.fact is not None:
                add(resolution.fact.id, "participant_canon", 700)

        retrieval_scores: dict[str, float] = {}
        if request.semantic_query and self.retriever is not None and request.semantic_limit:
            hits = self.retriever.search(
                request.semantic_query, limit=request.semantic_limit
            )
            for hit in islice(hits, request.semantic_limit):
                score = float(hit.score)
                if not math.isfinite(score):
                    raise ValueError(f"retrieval score must be finite for {hit.ref}")
                score = max(0.0, min(1.0, score))
                retrieval_scores[hit.ref] = max(
                    score, retrieval_scores.get(hit.ref, 0.0)
                )

        retrieval_hits = tuple(
            RetrievalHit(ref=ref, score=score)
            for ref, score in sorted(
                retrieval_scores.items(), key=lambda pair: (-pair[1], pair[0])
            )
        )
        for hit in retrieval_hits:
            add(hit.ref, "semantic_retrieval", 500 + int(hit.score * 100))

        safe: list[ContextItem] = []
        processed: set[str] = set()
        queue = sorted(candidates.values(), key=_candidate_sort_key)
        while queue:
            candidate = queue.pop(0)
            if candidate.ref in processed:
                continue
            processed.add(candidate.ref)

            item, rejection = self._materialize(
                candidate,
                request=request,
                entities=entities,
                facts=facts,
                states=states,
                knowledge=knowledge,
                pov_bound=pov_bound,
            )
            if rejection is not None:
                excluded.append(rejection)
                continue
            assert item is not None
            safe.append(item)

            for dependency in self.dependencies.get(candidate.ref, ()):
                if dependency in processed:
                    continue
                current = candidates.get(dependency)
                if current is None:
                    current = _Candidate(
                        ref=dependency,
                        priority=max(100, candidate.priority - 50),
                    )
                    candidates[dependency] = current
                current.priority = max(current.priority, max(100, candidate.priority - 50))
                current.reasons.add(f"dependency_of:{candidate.ref}")
                queue.append(current)
            queue.sort(key=_candidate_sort_key)

        included: list[ContextItem] = []
        used = 0
        for item in sorted(safe, key=_item_sort_key):
            if item.required:
                included.append(item)
                used += item.char_count
                continue
            if used + item.char_count > request.max_chars:
                excluded.append(
                    ExcludedContextItem(
                        item.ref,
                        "budget",
                        f"would exceed max_chars={request.max_chars}",
                    )
                )
                continue
            included.append(item)
            used += item.char_count

        return ContextManifest(
            request=request,
            retrieval_hits=retrieval_hits,
            included=tuple(included),
            excluded=tuple(_dedupe_excluded(excluded)),
        )

    def _materialize(
        self,
        candidate: _Candidate,
        *,
        request: ContextRequest,
        entities: Mapping[str, Any],
        facts: Mapping[str, CanonFact],
        states: Mapping[str, Any],
        knowledge: KnowledgeTimeline,
        pov_bound: set[str],
    ) -> tuple[ContextItem | None, ExcludedContextItem | None]:
        ref = candidate.ref

        if ref.startswith("state:"):
            entity_id = _parse_state_ref(ref)
            if entity_id is None or entity_id not in entities:
                return None, ExcludedContextItem(ref, "unknown_state_ref")
            if request.mode is ContextMode.POV and entity_id not in pov_bound:
                return None, ExcludedContextItem(ref, "pov_state_unbound")
            state = states.get(entity_id)
            values = {} if state is None else _visible_state_values(state.values, request)
            content = json.dumps(
                {
                    "type": "state",
                    "entity": entity_id,
                    "through": request.through_sequence,
                    "values": values,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            return ContextItem(
                ref=ref,
                kind="state",
                content=content,
                reasons=tuple(sorted(candidate.reasons)),
                priority=candidate.priority,
                required=candidate.required,
            ), None

        entity = entities.get(ref)
        if entity is not None:
            if request.mode is ContextMode.POV and ref not in pov_bound:
                return None, ExcludedContextItem(ref, "pov_entity_unbound")
            if request.mode is ContextMode.POV:
                payload = {
                    "type": "entity",
                    "id": entity.id,
                    "kind": entity.kind,
                    "name": entity.name,
                }
            else:
                payload = {
                    "type": "entity",
                    "id": entity.id,
                    "kind": entity.kind,
                    "name": entity.name,
                    "slug": entity.slug,
                    "aliases": list(entity.aliases),
                }
            return ContextItem(
                ref=ref,
                kind="entity",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                reasons=tuple(sorted(candidate.reasons)),
                priority=candidate.priority,
                required=candidate.required,
            ), None

        fact = facts.get(ref)
        if fact is None:
            return None, ExcludedContextItem(ref, "unknown_ref")
        if not fact.active_at(request.through_sequence):
            return None, ExcludedContextItem(ref, "timeline_inactive")

        resolution = self._canon.resolve(
            facts.values(),
            subject=fact.subject,
            predicate=fact.predicate,
            through_sequence=request.through_sequence,
        )
        if resolution.ambiguous:
            return None, ExcludedContextItem(ref, "canon_ambiguous")
        if resolution.fact is None:
            return None, ExcludedContextItem(ref, "canon_unresolved")
        if resolution.fact.id != fact.id:
            return None, ExcludedContextItem(
                ref, "shadowed_by_authority", f"resolved={resolution.fact.id}"
            )

        if request.mode is ContextMode.POV:
            if not fact.revealed_at(request.through_sequence):
                return None, ExcludedContextItem(ref, "not_revealed")
            public = "public" in fact.tags
            known = request.pov is not None and knowledge.knows(
                request.pov,
                fact.id,
                through_sequence=request.through_sequence,
            )
            if not public and not known:
                return None, ExcludedContextItem(ref, "pov_unknown")

        payload = {
            "type": "canon",
            "id": fact.id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "authority": fact.authority.name.lower(),
        }
        return ContextItem(
            ref=ref,
            kind="canon",
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            reasons=tuple(sorted(candidate.reasons)),
            priority=candidate.priority,
            required=candidate.required,
        ), None


def _visible_state_values(values: Mapping[str, Any], request: ContextRequest) -> dict[str, Any]:
    if request.mode is ContextMode.AUTHOR:
        return dict(values)
    allowed = set(request.pov_state_keys)
    return {key: value for key, value in values.items() if key in allowed}


def _pov_bound_entities(
    *,
    request: ContextRequest,
    states: Mapping[str, Any],
    known_entity_ids: set[str],
) -> set[str]:
    if request.mode is ContextMode.AUTHOR:
        return set(known_entity_ids)
    bound = {item for item in request.participants if item in known_entity_ids}
    if request.pov in known_entity_ids:
        bound.add(request.pov)
    for entity_id in tuple(bound):
        state = states.get(entity_id)
        if state is None:
            continue
        for value in _visible_state_values(state.values, request).values():
            bound.update(_extract_entity_refs(value, known_entity_ids))
    return bound


def _extract_entity_refs(value: Any, known_entity_ids: set[str]) -> set[str]:
    if isinstance(value, str):
        return {value} if value in known_entity_ids else set()
    if isinstance(value, Mapping):
        result: set[str] = set()
        for child in value.values():
            result.update(_extract_entity_refs(child, known_entity_ids))
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        result: set[str] = set()
        for child in value:
            result.update(_extract_entity_refs(child, known_entity_ids))
        return result
    return set()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values))


def _state_ref(entity_id: str, sequence: int) -> str:
    return f"state:{entity_id}@{sequence}"


def _parse_state_ref(ref: str) -> str | None:
    if not ref.startswith("state:") or "@" not in ref:
        return None
    return ref[len("state:"):].rsplit("@", 1)[0]


def _candidate_sort_key(candidate: _Candidate):
    return (-int(candidate.required), -candidate.priority, candidate.ref)


def _item_sort_key(item: ContextItem):
    kind_rank = {"entity": 0, "state": 1, "canon": 2}.get(item.kind, 9)
    return (-int(item.required), -item.priority, kind_rank, item.ref)


def _dedupe_excluded(items: Iterable[ExcludedContextItem]) -> list[ExcludedContextItem]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ExcludedContextItem] = []
    for item in items:
        key = (item.ref, item.reason, item.detail)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return sorted(result, key=lambda item: (item.ref, item.reason, item.detail))
