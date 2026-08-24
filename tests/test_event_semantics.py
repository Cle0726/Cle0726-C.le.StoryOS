from __future__ import annotations

import pytest

from storyos.events import StoryEvent, StoryPosition
from storyos.ids import stable_id
from storyos.knowledge import KnowledgeTimeline
from storyos.state import StoryStateProjector


SUBJECT = stable_id("character", "same-sequence-test", "subject")
FACT = stable_id("canon", "same-sequence-test", "fact")


def _event(slug: str, event_type: str, value: object, *, sequence: int = 10) -> StoryEvent:
    payload = {"value": value}
    if event_type in {"knowledge.gained", "knowledge.lost"}:
        payload = {"fact_id": FACT}
    return StoryEvent(
        id=stable_id("event", "same-sequence-test", slug),
        subject=SUBJECT,
        type=event_type,
        at=StoryPosition(sequence=sequence),
        payload=payload,
    )


def test_same_sequence_different_state_values_are_rejected():
    events = [
        _event("left", "location.set", "north"),
        _event("right", "location.set", "south"),
    ]

    with pytest.raises(ValueError, match="same-sequence projection conflict"):
        StoryStateProjector().project(events)


def test_same_sequence_knowledge_gain_and_loss_are_rejected():
    events = [
        _event("gain", "knowledge.gained", None),
        _event("loss", "knowledge.lost", None),
    ]

    with pytest.raises(ValueError, match="same-sequence projection conflict"):
        KnowledgeTimeline(events)


def test_same_sequence_distinct_semantic_slots_remain_valid():
    events = [
        _event("location", "location.set", "north"),
        _event("injury", "injury.left_hand.set", "numb"),
    ]

    state = StoryStateProjector().project(events)[SUBJECT]
    assert state.values == {"injury.left_hand": "numb", "location": "north"}


def test_same_sequence_identical_effect_is_order_independent():
    first = _event("first", "location.set", "north")
    second = _event("second", "location.set", "north")

    forward = StoryStateProjector().project([first, second])[SUBJECT].values
    reverse = StoryStateProjector().project([second, first])[SUBJECT].values
    assert forward == reverse == {"location": "north"}
