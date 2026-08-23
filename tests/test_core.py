from storyos.authority import CanonAuthority, CanonFact, CanonResolver
from storyos.claims import CandidateClaim, ClaimStager
from storyos.events import StoryEvent, StoryPosition
from storyos.ids import stable_id, validate_id
from storyos.knowledge import KnowledgeTimeline
from storyos.state import StoryStateProjector


CHARACTER = "chr_00000000000000000000000000000001"
LOCATION_A = "loc_00000000000000000000000000000001"
LOCATION_B = "loc_00000000000000000000000000000002"
FACT = "canon_00000000000000000000000000000001"


def event(event_id: str, sequence: int, event_type: str, payload: dict):
    return StoryEvent.from_mapping(
        {
            "id": event_id,
            "subject": CHARACTER,
            "type": event_type,
            "at": {"sequence": sequence},
            "payload": payload,
        }
    )


def test_stable_ids_are_deterministic_and_typed():
    first = stable_id("character", "demo", "H01")
    second = stable_id("character", "demo", "H01")
    assert first == second
    assert validate_id(first, "character")
    assert first != stable_id("character", "demo", "H02")


def test_story_state_is_point_in_time_and_input_order_independent():
    events = [
        event("evt_00000000000000000000000000000002", 20, "location.changed", {"value": LOCATION_B}),
        event("evt_00000000000000000000000000000001", 10, "location.changed", {"value": LOCATION_A}),
    ]
    projector = StoryStateProjector()

    at_10 = projector.project(events, through_sequence=10)[CHARACTER]
    at_20 = projector.project(list(reversed(events)), through_sequence=20)[CHARACTER]

    assert at_10.values["location"] == LOCATION_A
    assert at_20.values["location"] == LOCATION_B


def test_canon_resolver_prefers_higher_authority():
    current = CanonFact.from_mapping(
        {
            "id": "canon_00000000000000000000000000000002",
            "subject": CHARACTER,
            "predicate": "identity.real_name",
            "value": "Draft Name",
            "authority": "current",
        }
    )
    locked = CanonFact.from_mapping(
        {
            "id": FACT,
            "subject": CHARACTER,
            "predicate": "identity.real_name",
            "value": "Locked Name",
            "authority": CanonAuthority.LOCKED,
        }
    )

    resolution = CanonResolver().resolve(
        [current, locked],
        subject=CHARACTER,
        predicate="identity.real_name",
    )

    assert not resolution.ambiguous
    assert resolution.fact == locked


def test_staged_claim_cannot_override_locked_canon():
    locked = CanonFact.from_mapping(
        {
            "id": FACT,
            "subject": CHARACTER,
            "predicate": "identity.real_name",
            "value": "Locked Name",
            "authority": "locked",
        }
    )
    claim = CandidateClaim.from_mapping(
        {
            "id": "clm_00000000000000000000000000000001",
            "subject": CHARACTER,
            "predicate": "identity.real_name",
            "value": "Different Name",
            "at": {"sequence": 100},
            "confidence": 0.99,
            "status": "approved",
        }
    )

    result = ClaimStager().check(claim, canon_facts=[locked])

    assert not result.can_approve
    assert [issue.code for issue in result.issues] == ["canon_conflict"]


def test_knowledge_is_time_bounded():
    gained = event(
        "evt_00000000000000000000000000000003",
        30,
        "knowledge.gained",
        {"fact_id": FACT},
    )
    lost = event(
        "evt_00000000000000000000000000000004",
        40,
        "knowledge.lost",
        {"fact_id": FACT},
    )
    timeline = KnowledgeTimeline([lost, gained])

    assert not timeline.knows(CHARACTER, FACT, through_sequence=29)
    assert timeline.knows(CHARACTER, FACT, through_sequence=30)
    assert not timeline.knows(CHARACTER, FACT, through_sequence=40)
