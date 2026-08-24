from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from storyos.authority import CanonFact
from storyos.claim_review import ClaimReviewWorkbench, ReviewDecision, claim_fingerprint
from storyos.claims import CandidateClaim, ClaimStager, ClaimStatus
from storyos.events import StoryEvent
from storyos.ids import stable_id
from storyos.project import StoryProject


class MaterializationError(RuntimeError):
    """Raised when a reviewed Claim cannot be safely staged for materialization."""


@dataclass(frozen=True)
class MaterializationItem:
    claim_id: str
    ready: bool
    reasons: tuple[str, ...]
    kind: str | None
    target_id: str | None
    candidate: dict[str, Any] | None
    check: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "ready": self.ready,
            "reasons": list(self.reasons),
            "kind": self.kind,
            "target_id": self.target_id,
            "candidate": self.candidate,
            "check": self.check,
        }


class MaterializationWorkbench:
    """Build and persist quarantine candidates without mutating canonical files."""

    namespace = "review-materialization"

    def __init__(self) -> None:
        self._checker = ClaimStager()
        self._reviews = ClaimReviewWorkbench()

    def build_plan(self, project: StoryProject, *, claim_id: str | None = None) -> dict[str, Any]:
        facts = project.load_canon_facts()
        events = project.load_events()
        reviews = self._reviews.load_reviews(project)
        claims = sorted(project.load_claims(), key=lambda item: (item.at.sequence, item.id))

        items: list[MaterializationItem] = []
        for claim in claims:
            if claim_id is not None and claim.id != claim_id:
                continue
            items.append(
                self._build_item(
                    claim,
                    review=reviews.get(claim.id),
                    canon_facts=facts,
                    events=events,
                )
            )

        ready = sum(1 for item in items if item.ready)
        reasons: dict[str, int] = {}
        for item in items:
            for reason in item.reasons:
                reasons[reason] = reasons.get(reason, 0) + 1

        return {
            "schema": "story.materialization-plan.v1",
            "filters": {"claim_id": claim_id},
            "summary": {
                "claims": len(items),
                "ready": ready,
                "blocked": len(items) - ready,
                "reasons": dict(sorted(reasons.items())),
            },
            "items": [item.as_dict() for item in items],
            "policy": {
                "quarantine_only": True,
                "canonical_mutation": False,
                "commit_required": True,
                "stale_reviews_rejected": True,
                "conflicts_rechecked_at_plan_time": True,
                "fact_validity_intervals_checked": True,
            },
        }

    def stage(self, project: StoryProject, *, claim_id: str) -> tuple[dict[str, Any], str]:
        plan = self.build_plan(project, claim_id=claim_id)
        if not plan["items"]:
            raise MaterializationError(f"unknown staged claim: {claim_id}")
        item = plan["items"][0]
        if not item["ready"]:
            reasons = ", ".join(item["reasons"]) or "not_ready"
            raise MaterializationError(f"claim is not ready for materialization staging: {reasons}")

        mapping = quarantine_mapping_from_plan_item(item)
        kind = str(item["kind"])
        target_id = str(item["target_id"])
        directory = "events" if kind == "event" else "facts"
        destination = project.root / "staging" / "materialization" / directory / f"{target_id}.yaml"

        if destination.exists():
            existing = _load_data(destination)
            if existing == mapping:
                return mapping, "unchanged"
            raise MaterializationError(
                f"materialization candidate already exists with different content: {target_id}"
            )

        _write_yaml(destination, mapping)
        return mapping, "created"

    def _build_item(self, claim: CandidateClaim, *, review, canon_facts, events) -> MaterializationItem:
        empty_check = {"can_approve": False, "duplicate_of": None, "issues": []}
        if review is None:
            return MaterializationItem(claim.id, False, ("unreviewed",), None, None, None, empty_check)

        if review.claim_fingerprint != claim_fingerprint(claim):
            return MaterializationItem(claim.id, False, ("stale_review",), None, None, None, empty_check)

        if review.decision not in {
            ReviewDecision.ACCEPT_EVENT_CANDIDATE,
            ReviewDecision.ACCEPT_FACT_CANDIDATE,
        }:
            return MaterializationItem(
                claim.id,
                False,
                (f"decision_{review.decision.value}",),
                None,
                None,
                None,
                empty_check,
            )

        if review.normalized is None:
            return MaterializationItem(
                claim.id, False, ("missing_normalized_target",), None, None, None, empty_check
            )

        kind = "event" if review.decision is ReviewDecision.ACCEPT_EVENT_CANDIDATE else "fact"
        normalized_claim = CandidateClaim(
            id=claim.id,
            subject=claim.subject,
            predicate=str(review.normalized["predicate"]),
            value=review.normalized["value"],
            at=claim.at,
            confidence=claim.confidence,
            source={
                **claim.source,
                "review_decision": review.decision.value,
                "review_claim_fingerprint": review.claim_fingerprint,
            },
            proposed_authority=claim.proposed_authority,
            status=ClaimStatus.PENDING,
        )
        normalized_claim.validate()
        result = self._checker.check(normalized_claim, canon_facts=canon_facts, events=events)
        check_issues = [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "existing_ref": issue.existing_ref,
            }
            for issue in result.issues
        ]

        interval_conflicts: list[CanonFact] = []
        if kind == "fact":
            interval_conflicts = _fact_interval_conflicts(
                normalized_claim,
                canon_facts,
            )
            if interval_conflicts:
                check_issues.append(
                    {
                        "code": "canon_interval_conflict",
                        "severity": "error",
                        "message": (
                            "fact validity interval overlaps equal-authority Canon with a different value"
                        ),
                        "existing_ref": ",".join(fact.id for fact in interval_conflicts),
                    }
                )

        check = {
            "can_approve": result.can_approve and not interval_conflicts,
            "duplicate_of": result.duplicate_of,
            "issues": check_issues,
        }

        reasons: list[str] = []
        if not result.can_approve:
            reasons.append("current_conflict")
        if interval_conflicts:
            reasons.append("future_interval_conflict")
        if result.duplicate_of is not None:
            reasons.append("already_canonical_duplicate")

        identity = json.dumps(
            {
                "claim_id": claim.id,
                "claim_fingerprint": review.claim_fingerprint,
                "decision": review.decision.value,
                "subject": claim.subject,
                "predicate": normalized_claim.predicate,
                "value": normalized_claim.value,
                "sequence": claim.at.sequence,
                "authority": claim.proposed_authority.name.lower(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        if kind == "event":
            target_id = stable_id("event", self.namespace, identity)
            payload = {
                "schema": "story.event.v1",
                "id": target_id,
                "subject": claim.subject,
                "type": f"{normalized_claim.predicate}.set",
                "at": {
                    "sequence": claim.at.sequence,
                    "season": claim.at.season,
                    "episode": claim.at.episode,
                    "scene": claim.at.scene,
                },
                "payload": {"value": normalized_claim.value, "approved_claim": claim.id},
                "source": {
                    "kind": "review_materialization_candidate",
                    "claim_id": claim.id,
                    "claim_fingerprint": review.claim_fingerprint,
                    "review_decision": review.decision.value,
                    "claim_source": claim.source,
                },
            }
            StoryEvent.from_mapping({key: value for key, value in payload.items() if key != "schema"})
            assumptions = [
                "normalized predicate is materialized as a state-setting '<predicate>.set' event",
                "the original claim story position is reused for the event candidate",
            ]
        else:
            target_id = stable_id("canon", self.namespace, identity)
            payload = {
                "schema": "story.canon.v1",
                "id": target_id,
                "subject": claim.subject,
                "predicate": normalized_claim.predicate,
                "value": normalized_claim.value,
                "authority": claim.proposed_authority.name.lower(),
                "valid_from": claim.at.sequence,
                "source": {
                    "kind": "review_materialization_candidate",
                    "claim_id": claim.id,
                    "claim_fingerprint": review.claim_fingerprint,
                    "review_decision": review.decision.value,
                    "claim_source": claim.source,
                },
                "tags": ["reviewed_candidate"],
            }
            CanonFact.from_mapping({key: value for key, value in payload.items() if key != "schema"})
            assumptions = [
                "fact authority is inherited from the Candidate Claim proposed_authority",
                "valid_from defaults to the original claim sequence and must be reviewed before canonical commit",
                "equal-authority validity intervals are checked for future overlap before staging",
            ]

        candidate = {
            "claim_fingerprint": review.claim_fingerprint,
            "review": review.as_mapping(),
            "check": check,
            "canonical_payload": payload,
            "assumptions": assumptions,
        }
        return MaterializationItem(
            claim_id=claim.id,
            ready=not reasons,
            reasons=tuple(reasons),
            kind=kind,
            target_id=target_id,
            candidate=candidate,
            check=check,
        )


def _fact_interval_conflicts(
    claim: CandidateClaim,
    canon_facts,
) -> list[CanonFact]:
    """Find different equal-authority facts whose validity overlaps the new open interval."""

    conflicts: list[CanonFact] = []
    new_start = claim.at.sequence
    for fact in canon_facts:
        if (
            fact.subject != claim.subject
            or fact.predicate != claim.predicate
            or fact.authority != claim.proposed_authority
            or not fact.authority.is_mainline
            or fact.value == claim.value
        ):
            continue
        if _intervals_overlap(new_start, None, fact.valid_from, fact.valid_to):
            conflicts.append(fact)
    return sorted(conflicts, key=lambda fact: fact.id)


def _intervals_overlap(
    left_start: int | None,
    left_end: int | None,
    right_start: int | None,
    right_end: int | None,
) -> bool:
    left_min = -1 if left_start is None else left_start
    right_min = -1 if right_start is None else right_start
    left_max = float("inf") if left_end is None else left_end
    right_max = float("inf") if right_end is None else right_end
    return left_min <= right_max and right_min <= left_max


def quarantine_mapping_from_plan_item(item: dict[str, Any]) -> dict[str, Any]:
    candidate_raw = item.get("candidate")
    if not isinstance(candidate_raw, dict):
        raise MaterializationError("materialization item is missing candidate data")
    candidate = dict(candidate_raw)
    kind = str(item.get("kind") or "")
    target_id = str(item.get("target_id") or "")
    claim_id = str(item.get("claim_id") or "")
    if kind not in {"event", "fact"}:
        raise MaterializationError(f"unsupported materialization kind: {kind}")
    if not target_id or not claim_id:
        raise MaterializationError("materialization item is missing target/claim identity")

    return {
        "schema": "story.materialization-candidate.v1",
        "kind": kind,
        "target_id": target_id,
        "claim_id": claim_id,
        "claim_fingerprint": candidate["claim_fingerprint"],
        "review": candidate["review"],
        "check": candidate["check"],
        "canonical_payload": candidate["canonical_payload"],
        "assumptions": candidate["assumptions"],
        "policy": {
            "quarantine_only": True,
            "canonical_mutation": False,
            "commit_required": True,
        },
    }


def _load_data(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() == ".json":
            return json.load(fh)
        return yaml.safe_load(fh)


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8", newline="\n")
