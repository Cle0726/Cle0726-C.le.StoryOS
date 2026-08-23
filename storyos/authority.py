from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable

from storyos.ids import validate_id


class CanonAuthority(IntEnum):
    """Priority of a fact inside the mainline story authority system."""

    DEPRECATED = 0
    ALTERNATIVE = 50
    PLANNING = 100
    DRAFT = 200
    CURRENT = 300
    LOCKED = 400

    @classmethod
    def parse(cls, value: str | int | "CanonAuthority") -> "CanonAuthority":
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return cls(value)
        normalized = str(value).strip().upper().replace("-", "_")
        try:
            return cls[normalized]
        except KeyError as exc:
            raise ValueError(f"unsupported canon authority: {value}") from exc

    @property
    def is_mainline(self) -> bool:
        return self not in {CanonAuthority.DEPRECATED, CanonAuthority.ALTERNATIVE}


@dataclass(frozen=True)
class CanonFact:
    id: str
    subject: str
    predicate: str
    value: Any
    authority: CanonAuthority = CanonAuthority.CURRENT
    valid_from: int | None = None
    valid_to: int | None = None
    reveal_at: int | None = None
    source: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "CanonFact":
        fact = cls(
            id=str(raw["id"]),
            subject=str(raw["subject"]),
            predicate=str(raw["predicate"]),
            value=raw.get("value"),
            authority=CanonAuthority.parse(raw.get("authority", "current")),
            valid_from=_maybe_int(raw.get("valid_from")),
            valid_to=_maybe_int(raw.get("valid_to")),
            reveal_at=_maybe_int(raw.get("reveal_at")),
            source=dict(raw.get("source") or {}),
            tags=tuple(str(x) for x in raw.get("tags", [])),
        )
        fact.validate()
        return fact

    def validate(self) -> None:
        if not validate_id(self.id, "canon"):
            raise ValueError(f"invalid canon fact id: {self.id}")
        if not validate_id(self.subject):
            raise ValueError(f"invalid canon subject id: {self.subject}")
        if not self.predicate.strip():
            raise ValueError("canon predicate cannot be empty")
        if self.valid_from is not None and self.valid_from < 0:
            raise ValueError("valid_from must be >= 0")
        if self.valid_to is not None and self.valid_to < 0:
            raise ValueError("valid_to must be >= 0")
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot be earlier than valid_from")
        if self.reveal_at is not None and self.reveal_at < 0:
            raise ValueError("reveal_at must be >= 0")

    def active_at(self, sequence: int | None, *, include_nonmainline: bool = False) -> bool:
        if not include_nonmainline and not self.authority.is_mainline:
            return False
        if sequence is None:
            return True
        if self.valid_from is not None and sequence < self.valid_from:
            return False
        if self.valid_to is not None and sequence > self.valid_to:
            return False
        return True

    def revealed_at(self, sequence: int | None) -> bool:
        if self.reveal_at is None:
            return True
        if sequence is None:
            return False
        return sequence >= self.reveal_at


@dataclass(frozen=True)
class CanonResolution:
    fact: CanonFact | None
    conflicts: tuple[CanonFact, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return bool(self.conflicts)


class CanonResolver:
    """Resolve a fact deterministically by subject, predicate, timeline and authority."""

    def resolve(
        self,
        facts: Iterable[CanonFact],
        *,
        subject: str,
        predicate: str,
        through_sequence: int | None = None,
        include_nonmainline: bool = False,
    ) -> CanonResolution:
        candidates = [
            fact
            for fact in facts
            if fact.subject == subject
            and fact.predicate == predicate
            and fact.active_at(through_sequence, include_nonmainline=include_nonmainline)
        ]
        if not candidates:
            return CanonResolution(None)

        highest = max(fact.authority for fact in candidates)
        winners = sorted((fact for fact in candidates if fact.authority == highest), key=lambda f: f.id)
        values = {_stable_value_key(fact.value) for fact in winners}
        if len(values) > 1:
            return CanonResolution(fact=None, conflicts=tuple(winners))
        return CanonResolution(fact=winners[0])


def _stable_value_key(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _maybe_int(value: Any) -> int | None:
    return None if value is None else int(value)
