from __future__ import annotations

from storyos.authority import CanonAuthority
from storyos.claims import CandidateClaim, ClaimStager
from storyos.events import StoryPosition
from storyos.ids import stable_id


def test_materialize_event_keeps_approval_kind_when_claim_source_has_kind():
    claim = CandidateClaim(
        id=stable_id("claim", "provenance-test", "claim"),
        subject=stable_id("character", "provenance-test", "character"),
        predicate="location",
        value="station",
        at=StoryPosition(sequence=10, season=1, episode=1, scene=1),
        confidence=0.9,
        source={"kind": "import", "file": "legacy.yaml"},
        proposed_authority=CanonAuthority.DRAFT,
    )

    event = ClaimStager().materialize_event(
        claim,
        event_id=stable_id("event", "provenance-test", "event"),
    )

    assert event.source["kind"] == "claim_approval"
    assert event.source["claim_id"] == claim.id
    assert event.source["claim_source"] == {"kind": "import", "file": "legacy.yaml"}
