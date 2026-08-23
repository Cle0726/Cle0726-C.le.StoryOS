from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from storyos.events import StoryEvent


@dataclass(frozen=True)
class KnowledgeChange:
    entity_id: str
    fact_id: str
    sequence: int
    gained: bool
    event_id: str


class KnowledgeTimeline:
    """Point-in-time knowledge queries derived only from canonical events."""

    def __init__(self, events: Iterable[StoryEvent]):
        self._events = tuple(sorted(events, key=lambda e: (e.at.sequence, e.id)))

    def known_facts(self, entity_id: str, *, through_sequence: int | None = None) -> set[str]:
        facts: set[str] = set()
        for event in self._events:
            if through_sequence is not None and event.at.sequence > through_sequence:
                break
            if event.subject != entity_id:
                continue
            fact_id = _knowledge_fact_id(event)
            if fact_id is None:
                continue
            if event.type == "knowledge.gained":
                facts.add(fact_id)
            elif event.type == "knowledge.lost":
                facts.discard(fact_id)
        return facts

    def knows(self, entity_id: str, fact_id: str, *, through_sequence: int | None = None) -> bool:
        return fact_id in self.known_facts(entity_id, through_sequence=through_sequence)

    def holders(self, fact_id: str, *, through_sequence: int | None = None) -> set[str]:
        subjects = {event.subject for event in self._events}
        return {
            subject
            for subject in subjects
            if self.knows(subject, fact_id, through_sequence=through_sequence)
        }

    def history(self, entity_id: str, fact_id: str) -> list[KnowledgeChange]:
        result: list[KnowledgeChange] = []
        for event in self._events:
            if event.subject != entity_id:
                continue
            current = _knowledge_fact_id(event)
            if current != fact_id:
                continue
            if event.type not in {"knowledge.gained", "knowledge.lost"}:
                continue
            result.append(
                KnowledgeChange(
                    entity_id=entity_id,
                    fact_id=fact_id,
                    sequence=event.at.sequence,
                    gained=event.type == "knowledge.gained",
                    event_id=event.id,
                )
            )
        return result


def _knowledge_fact_id(event: StoryEvent) -> str | None:
    if event.type not in {"knowledge.gained", "knowledge.lost"}:
        return None
    value = event.payload.get("fact_id", event.payload.get("fact"))
    return None if value is None else str(value)
