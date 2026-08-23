from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from storyos.authority import CanonAuthority, CanonFact, CanonResolver
from storyos.events import StoryEvent, StoryPosition
from storyos.ids import validate_id
from storyos.state import StoryStateProjector


class ClaimStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CandidateClaim:
    """Non-canonical fact proposed by AI or an import/extraction step."""

    id: str
    subject: str
    predicate: str
    value: Any
    at: StoryPosition
    confidence: float = 0.0
    source: dict[str, Any] = field(default_factory=dict)
    proposed_authority: CanonAuthority = CanonAuthority.DRAFT
    status: ClaimStatus = ClaimStatus.PENDING

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "CandidateClaim":
        claim = cls(
            id=str(raw["id"]),
            subject=str(raw["subject"]),
            predicate=str(raw["predicate"]),
            value=raw.get("value"),
            at=StoryPosition.from_mapping(raw["at"]),
            confidence=float(raw.get("confidence", 0.0)),
            source=dict(raw.get("source") or {}),
            proposed_authority=CanonAuthority.parse(raw.get("proposed_authority", "draft")),
            status=ClaimStatus(str(raw.get("status", "pending"))),
        )
        claim.validate()
        return claim

    def validate(self) -> None:
        if not validate_id(self.id, "claim"):
            raise ValueError(f"invalid claim id: {self.id}")
        if not validate_id(self.subject):
            raise ValueError(f"invalid claim subject id: {self.subject}")
        if not self.predicate.strip():
            raise ValueError("claim predicate cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("claim confidence must be between 0 and 1")
        if self.at.sequence < 0:
            raise ValueError("claim sequence must be >= 0")


@dataclass(frozen=True)
class ClaimIssue:
    code: str
    severity: str
    message: str
    existing_ref: str | None = None


@dataclass(frozen=True)
class ClaimCheckResult:
    claim_id: str
    issues: tuple[ClaimIssue, ...] = ()
    duplicate_of: str | None = None

    @property
    def can_approve(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


class ClaimStager:
    """Deterministically check staged claims without mutating canonical data."""

    def __init__(self) -> None:
        self._canon = CanonResolver()
        self._state = StoryStateProjector()

    def check(
        self,
        claim: CandidateClaim,
        *,
        canon_facts: Iterable[CanonFact] = (),
        events: Iterable[StoryEvent] = (),
    ) -> ClaimCheckResult:
        facts = list(canon_facts)
        event_list = list(events)
        issues: list[ClaimIssue] = []
        duplicate_of: str | None = None

        resolution = self._canon.resolve(
            facts,
            subject=claim.subject,
            predicate=claim.predicate,
            through_sequence=claim.at.sequence,
        )
        if resolution.ambiguous:
            issues.append(
                ClaimIssue(
                    code="canon_ambiguous",
                    severity="error",
                    message="multiple equal-authority canon facts disagree at this timeline point",
                    existing_ref=",".join(fact.id for fact in resolution.conflicts),
                )
            )
        elif resolution.fact is not None:
            if resolution.fact.value == claim.value:
                duplicate_of = resolution.fact.id
                issues.append(
                    ClaimIssue(
                        code="canon_duplicate",
                        severity="info",
                        message="claim repeats an already resolved canon fact",
                        existing_ref=resolution.fact.id,
                    )
                )
            else:
                issues.append(
                    ClaimIssue(
                        code="canon_conflict",
                        severity="error",
                        message=(
                            f"claim conflicts with {resolution.fact.authority.name.lower()} canon "
                            f"for {claim.predicate}"
                        ),
                        existing_ref=resolution.fact.id,
                    )
                )

        projected = self._state.project(event_list, through_sequence=claim.at.sequence)
        state = projected.get(claim.subject)
        if state is not None and claim.predicate in state.values:
            existing_value = state.values[claim.predicate]
            if existing_value != claim.value:
                issues.append(
                    ClaimIssue(
                        code="state_conflict",
                        severity="error",
                        message=f"claim conflicts with projected story state for {claim.predicate}",
                    )
                )

        return ClaimCheckResult(claim_id=claim.id, issues=tuple(issues), duplicate_of=duplicate_of)

    def materialize_event(self, claim: CandidateClaim, *, event_id: str) -> StoryEvent:
        """Create a canonical event candidate after explicit human approval.

        This method does not write files and does not trust claim.status. The caller must
        explicitly invoke it as the approval action.
        """
        if not validate_id(event_id, "event"):
            raise ValueError(f"invalid event id: {event_id}")
        return StoryEvent(
            id=event_id,
            subject=claim.subject,
            type=f"{claim.predicate}.set",
            at=claim.at,
            payload={"value": claim.value, "approved_claim": claim.id},
            source={"kind": "claim_approval", **claim.source},
        )
