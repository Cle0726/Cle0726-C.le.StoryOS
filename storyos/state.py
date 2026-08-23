from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from storyos.events import StoryEvent


@dataclass
class EntityState:
    entity_id: str
    values: dict[str, Any] = field(default_factory=dict)
    knowledge: set[str] = field(default_factory=set)
    resolved_plots: set[str] = field(default_factory=set)


class StoryStateProjector:
    """Deterministically derive point-in-time state from ordered canonical events."""

    def project(
        self,
        events: Iterable[StoryEvent],
        *,
        through_sequence: int | None = None,
    ) -> dict[str, EntityState]:
        states: dict[str, EntityState] = {}
        ordered = sorted(events, key=lambda e: (e.at.sequence, e.id))

        for event in ordered:
            if through_sequence is not None and event.at.sequence > through_sequence:
                break
            state = states.setdefault(event.subject, EntityState(event.subject))
            self._apply(state, event)

        return deepcopy(states)

    def _apply(self, state: EntityState, event: StoryEvent) -> None:
        if event.type.endswith(".changed") or event.type.endswith(".set"):
            key = event.type.rsplit(".", 1)[0]
            if "value" not in event.payload:
                raise ValueError(f"{event.type} requires payload.value")
            state.values[key] = event.payload["value"]
            return

        if event.type == "knowledge.gained":
            fact = _fact_id(event)
            if fact is None:
                raise ValueError("knowledge.gained requires payload.fact_id or payload.fact")
            state.knowledge.add(fact)
            return

        if event.type == "knowledge.lost":
            fact = _fact_id(event)
            if fact is None:
                raise ValueError("knowledge.lost requires payload.fact_id or payload.fact")
            state.knowledge.discard(fact)
            return

        if event.type == "plot.resolved":
            plot_id = str(event.payload["plot_id"])
            state.resolved_plots.add(plot_id)
            return

        if event.type == "plot.reopened":
            plot_id = str(event.payload["plot_id"])
            state.resolved_plots.discard(plot_id)
            return

        # Unknown namespaced events remain valid source data but intentionally
        # have no projection effect until an explicit rule is implemented.


def _fact_id(event: StoryEvent) -> str | None:
    value = event.payload.get("fact_id", event.payload.get("fact"))
    return None if value is None else str(value)
