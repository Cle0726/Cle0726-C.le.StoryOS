from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


def _maybe_int(value: Any) -> int | None:
    return None if value is None else int(value)
