from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from storyos.ids import validate_id


@dataclass(frozen=True, order=True)
class StoryPosition:
    sequence: int
    season: int | None = None
    episode: int | None = None
    scene: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "StoryPosition":
        return cls(
            sequence=int(data["sequence"]),
            season=_maybe_int(data.get("season")),
            episode=_maybe_int(data.get("episode")),
            scene=_maybe_int(data.get("scene")),
        )


@dataclass(frozen=True)
class StoryEvent:
    id: str
    subject: str
    type: str
    at: StoryPosition
    payload: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "StoryEvent":
        event = cls(
            id=str(data["id"]),
            subject=str(data["subject"]),
            type=str(data["type"]),
            at=StoryPosition.from_mapping(data["at"]),
            payload=dict(data.get("payload") or {}),
            source=dict(data.get("source") or {}),
        )
        event.validate()
        return event

    def validate(self) -> None:
        if not validate_id(self.id, "event"):
            raise ValueError(f"invalid event id: {self.id}")
        if not validate_id(self.subject):
            raise ValueError(f"invalid subject id: {self.subject}")
        if not self.type or "." not in self.type:
            raise ValueError("event type must be namespaced, e.g. location.changed")
        if self.at.sequence < 0:
            raise ValueError("event sequence must be >= 0")


def validate_projection_semantics(events: Iterable[StoryEvent]) -> None:
    """Reject same-sequence events whose projected result would depend on event ID order.

    Multiple events may legitimately share a sequence when they affect different semantic
    slots. Equal effects on the same slot are also harmless. Different effects targeting
    the same projected slot at the same sequence are ambiguous and must be assigned
    distinct sequence values instead of relying on stable-ID lexical order.
    """

    seen: dict[tuple[int, str, str, str], tuple[str, str]] = {}
    for event in events:
        semantic = _projection_semantic(event)
        if semantic is None:
            continue
        domain, key, effect = semantic
        slot = (event.at.sequence, event.subject, domain, key)
        previous = seen.get(slot)
        if previous is None:
            seen[slot] = (effect, event.id)
            continue
        previous_effect, previous_id = previous
        if previous_effect != effect:
            raise ValueError(
                "same-sequence projection conflict for "
                f"{event.subject} {domain}:{key} at sequence {event.at.sequence}: "
                f"{previous_id} conflicts with {event.id}; assign distinct sequence values"
            )


def _projection_semantic(event: StoryEvent) -> tuple[str, str, str] | None:
    if event.type.endswith(".changed") or event.type.endswith(".set"):
        key = event.type.rsplit(".", 1)[0]
        if "value" not in event.payload:
            return None
        return "state", key, _stable_value(event.payload["value"])

    if event.type in {"knowledge.gained", "knowledge.lost"}:
        raw = event.payload.get("fact_id", event.payload.get("fact"))
        if raw is None:
            return None
        effect = "known" if event.type == "knowledge.gained" else "unknown"
        return "knowledge", str(raw), effect

    if event.type in {"plot.resolved", "plot.reopened"}:
        raw = event.payload.get("plot_id")
        if raw is None:
            return None
        effect = "resolved" if event.type == "plot.resolved" else "open"
        return "plot", str(raw), effect

    return None


def _stable_value(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _maybe_int(value: Any) -> int | None:
    return None if value is None else int(value)
