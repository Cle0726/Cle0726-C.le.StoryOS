from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from storyos.ids import validate_id


@dataclass(frozen=True)
class StoryEntity:
    id: str
    kind: str
    name: str
    slug: str = ""
    aliases: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "StoryEntity":
        entity = cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            name=str(raw["name"]),
            slug=str(raw.get("slug") or ""),
            aliases=tuple(str(x) for x in raw.get("aliases", [])),
            data=dict(raw.get("data") or {}),
        )
        if not validate_id(entity.id, entity.kind):
            raise ValueError(f"invalid {entity.kind} id: {entity.id}")
        if not entity.name.strip():
            raise ValueError("entity name cannot be empty")
        return entity
