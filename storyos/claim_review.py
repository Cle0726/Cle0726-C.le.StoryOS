from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from storyos.atomic_io import AtomicWriteError, atomic_create_text, atomic_replace_text
from storyos.claims import CandidateClaim, ClaimStager
from storyos.file_lock import ProjectFileLockError, project_file_lock
from storyos.ids import validate_id
from storyos.project import StoryProject


class ClaimReviewError(RuntimeError):
    """Raised when a claim review decision cannot be safely created or loaded."""


class _Unset:
    pass


_UNSET = _Unset()


class ReviewDecision(str, Enum):
    ACCEPT_EVENT_CANDIDATE = "accept_event_candidate"
    ACCEPT_FACT_CANDIDATE = "accept_fact_candidate"
    REJECT = "reject"
    DEFER = "defer"


@dataclass(frozen=True)
class ClaimReviewDecision:
    claim_id: str
    claim_fingerprint: str
    decision: ReviewDecision
    normalized: dict[str, Any] | None = None
    note: str = ""

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ClaimReviewDecision":
        review = cls(
            claim_id=str(raw["claim_id"]),
            claim_fingerprint=str(raw["claim_fingerprint"]),
            decision=ReviewDecision(str(raw["decision"])),
            normalized=None if raw.get("normalized") is None else dict(raw.get("normalized") or {}),
            note=str(raw.get("note") or ""),
        )
        review.validate()
        return review

    def validate(self) -> None:
        if not validate_id(self.claim_id, "claim"):
            raise ValueError(f"invalid reviewed claim id: {self.claim_id}")
        if not _hex_64(self.claim_fingerprint):
            raise ValueError("claim_fingerprint must be a 64-character lowercase SHA-256")
        accepts = self.decision in {
            ReviewDecision.ACCEPT_EVENT_CANDIDATE,
            ReviewDecision.ACCEPT_FACT_CANDIDATE,
        }
        if accepts:
            if self.normalized is None:
                raise ValueError("accepted review decisions require normalized target data")
            predicate = str(self.normalized.get("predicate") or "").strip()
            if not predicate:
                raise ValueError("accepted review decisions require normalized.predicate")
            if "value" not in self.normalized:
                raise ValueError("accepted review decisions require normalized.value")
        elif self.normalized is not None:
            raise ValueError("reject/defer review decisions cannot carry normalized target data")

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema": "story.claim-review.v1",
            "claim_id": self.claim_id,
            "claim_fingerprint": self.claim_fingerprint,
            "decision": self.decision.value,
            "normalized": self.normalized,
            "note": self.note,
            "policy": {
                "canonical_mutation": False,
                "materialization_required": True,
            },
        }


class ClaimReviewWorkbench:
    """Build deterministic review queues and persist non-canonical review sidecars."""

    def __init__(self) -> None:
        self._checker = ClaimStager()

    def build_queue(
        self,
        project: StoryProject,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        claim_id: str | None = None,
    ) -> dict[str, Any]:
        entities = {entity.id: entity for entity in project.load_entities()}
        facts = project.load_canon_facts()
        events = project.load_events()
        reviews = self.load_reviews(project)

        claims = sorted(project.load_claims(), key=lambda item: (item.at.sequence, item.id))
        rows: list[dict[str, Any]] = []
        decision_counts: dict[str, int] = {}
        blocked = 0
        stale = 0

        for claim in claims:
            if subject is not None and claim.subject != subject:
                continue
            if predicate is not None and not claim.predicate.startswith(predicate):
                continue
            if claim_id is not None and claim.id != claim_id:
                continue

            check = self._checker.check(claim, canon_facts=facts, events=events)
            if not check.can_approve:
                blocked += 1

            review = reviews.get(claim.id)
            review_stale = False
            review_payload = None
            if review is not None:
                review_stale = review.claim_fingerprint != claim_fingerprint(claim)
                if review_stale:
                    stale += 1
                decision_counts[review.decision.value] = decision_counts.get(review.decision.value, 0) + 1
                review_payload = review.as_mapping()

            entity = entities.get(claim.subject)
            rows.append(
                {
                    "claim": _claim_payload(claim),
                    "subject_name": None if entity is None else entity.name,
                    "check": {
                        "can_approve": check.can_approve,
                        "duplicate_of": check.duplicate_of,
                        "issues": [
                            {
                                "code": issue.code,
                                "severity": issue.severity,
                                "message": issue.message,
                                "existing_ref": issue.existing_ref,
                            }
                            for issue in check.issues
                        ],
                    },
                    "review": review_payload,
                    "review_stale": review_stale,
                }
            )

        reviewed = sum(decision_counts.values())
        return {
            "schema": "story.claim-review-queue.v1",
            "filters": {"subject": subject, "predicate": predicate, "claim_id": claim_id},
            "summary": {
                "claims": len(rows),
                "blocked_by_current_canon_or_state": blocked,
                "reviewed": reviewed,
                "unreviewed": len(rows) - reviewed,
                "stale_reviews": stale,
                "decisions": dict(sorted(decision_counts.items())),
                "extraction_unresolved": _extraction_unresolved_count(project),
            },
            "items": rows,
            "policy": {
                "review_decisions_are_noncanonical": True,
                "review_does_not_change_claim_status": True,
                "materialization_is_separate": True,
            },
        }

    def load_reviews(self, project: StoryProject) -> dict[str, ClaimReviewDecision]:
        directory = project.root / "staging" / "reviews"
        if not directory.exists():
            return {}
        if directory.is_symlink():
            raise ClaimReviewError("review directory cannot be a symlink")
        reviews: dict[str, ClaimReviewDecision] = {}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ClaimReviewError(f"review path cannot be a symlink: {path}")
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                continue
            raw = _load_data(path)
            if not isinstance(raw, dict) or raw.get("schema") != "story.claim-review.v1":
                raise ClaimReviewError(f"unsupported review record: {path}")
            data = dict(raw)
            data.pop("schema", None)
            data.pop("policy", None)
            review = ClaimReviewDecision.from_mapping(data)
            if review.claim_id in reviews:
                raise ClaimReviewError(f"duplicate review for claim: {review.claim_id}")
            reviews[review.claim_id] = review
        return reviews

    def decide(
        self,
        project: StoryProject,
        *,
        claim_id: str,
        decision: ReviewDecision | str,
        normalized_predicate: str | None = None,
        normalized_value: Any = _UNSET,
        note: str = "",
        replace: bool = False,
    ) -> tuple[ClaimReviewDecision, str]:
        if not isinstance(decision, ReviewDecision):
            decision = ReviewDecision(str(decision))
        claims = {claim.id: claim for claim in project.load_claims()}
        claim = claims.get(claim_id)
        if claim is None:
            raise ClaimReviewError(f"unknown claim id: {claim_id}")

        accepts = decision in {
            ReviewDecision.ACCEPT_EVENT_CANDIDATE,
            ReviewDecision.ACCEPT_FACT_CANDIDATE,
        }
        normalized: dict[str, Any] | None
        if accepts:
            if normalized_predicate is None or not normalized_predicate.strip():
                raise ClaimReviewError("accepted decisions require --predicate")
            if normalized_value is _UNSET:
                raise ClaimReviewError("accepted decisions require --value-json")
            normalized = {"predicate": normalized_predicate.strip(), "value": normalized_value}
        else:
            if normalized_predicate is not None or normalized_value is not _UNSET:
                raise ClaimReviewError("reject/defer decisions cannot include normalized target data")
            normalized = None

        review = ClaimReviewDecision(
            claim_id=claim.id,
            claim_fingerprint=claim_fingerprint(claim),
            decision=decision,
            normalized=normalized,
            note=note,
        )
        review.validate()
        destination = project.root / "staging" / "reviews" / f"{claim.id}.yaml"
        text = yaml.safe_dump(review.as_mapping(), allow_unicode=True, sort_keys=False, width=120)

        try:
            with project_file_lock(project.root, "claim-review", resource=claim.id):
                existed_before = destination.exists()
                if existed_before:
                    if destination.is_symlink():
                        raise ClaimReviewError("review destination cannot be a symlink")
                    existing_raw = _load_data(destination)
                    existing_data = dict(existing_raw)
                    existing_data.pop("schema", None)
                    existing_data.pop("policy", None)
                    existing = ClaimReviewDecision.from_mapping(existing_data)
                    if existing == review:
                        return review, "unchanged"
                    if not replace:
                        raise ClaimReviewError(
                            f"review already exists with different content: {claim.id}; pass replace=True explicitly"
                        )
                    atomic_replace_text(project.root, destination, text)
                    return review, "replaced"

                try:
                    atomic_create_text(project.root, destination, text)
                except FileExistsError:
                    existing_raw = _load_data(destination)
                    existing_data = dict(existing_raw)
                    existing_data.pop("schema", None)
                    existing_data.pop("policy", None)
                    existing = ClaimReviewDecision.from_mapping(existing_data)
                    if existing == review:
                        return review, "unchanged"
                    raise ClaimReviewError(
                        f"review was concurrently created with different content: {claim.id}"
                    )
                return review, "created"
        except (AtomicWriteError, ProjectFileLockError) as exc:
            raise ClaimReviewError(str(exc)) from exc


def claim_fingerprint(claim: CandidateClaim) -> str:
    payload = {
        "id": claim.id,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "value": claim.value,
        "at": {
            "sequence": claim.at.sequence,
            "season": claim.at.season,
            "episode": claim.at.episode,
            "scene": claim.at.scene,
        },
        "confidence": claim.confidence,
        "source": claim.source,
        "proposed_authority": claim.proposed_authority.name.lower(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _claim_payload(claim: CandidateClaim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "value": claim.value,
        "at": {
            "sequence": claim.at.sequence,
            "season": claim.at.season,
            "episode": claim.at.episode,
            "scene": claim.at.scene,
        },
        "confidence": claim.confidence,
        "source": claim.source,
        "proposed_authority": claim.proposed_authority.name.lower(),
        "status": claim.status.value,
        "fingerprint": claim_fingerprint(claim),
    }


def _extraction_unresolved_count(project: StoryProject) -> int | None:
    path = project.root / "staging" / "extraction" / "world_state" / "report.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return int(raw["counts"]["unresolved_total"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _load_data(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() == ".json":
            return json.load(fh)
        return yaml.safe_load(fh)


def _hex_64(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
