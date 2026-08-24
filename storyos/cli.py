from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from storyos.authority import CanonResolver
from storyos.canon_commit import CanonCommitError
from storyos.canon_commit_service import CanonCommitWorkbench
from storyos.claim_review import ClaimReviewError, ClaimReviewWorkbench, ReviewDecision
from storyos.claims import ClaimStager
from storyos.knowledge import KnowledgeTimeline
from storyos.materialization import MaterializationError, MaterializationWorkbench
from storyos.project import StoryProject
from storyos.state import StoryStateProjector


def _fact_payload(fact):
    return {
        "id": fact.id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "value": fact.value,
        "authority": fact.authority.name.lower(),
        "valid_from": fact.valid_from,
        "valid_to": fact.valid_to,
        "reveal_at": fact.reveal_at,
        "source": fact.source,
        "tags": list(fact.tags),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="storyos")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate canonical and staging project files")
    p_validate.add_argument("project")

    p_state = sub.add_parser("state", help="Project story state at a sequence boundary")
    p_state.add_argument("project")
    p_state.add_argument("--through", type=int, default=None)

    p_canon = sub.add_parser("canon", help="Resolve one canon predicate by authority and timeline")
    p_canon.add_argument("project")
    p_canon.add_argument("subject")
    p_canon.add_argument("predicate")
    p_canon.add_argument("--through", type=int, default=None)

    p_knowledge = sub.add_parser("knowledge", help="Show what one entity knows at a sequence boundary")
    p_knowledge.add_argument("project")
    p_knowledge.add_argument("entity")
    p_knowledge.add_argument("--through", type=int, default=None)

    p_claims = sub.add_parser("claims", help="Check staged claims without modifying Canon")
    p_claims.add_argument("project")
    p_claims.add_argument("--id", dest="claim_id", default=None)

    p_review = sub.add_parser("claim-review", help="Build the non-canonical claim review queue")
    p_review.add_argument("project")
    p_review.add_argument("--subject", default=None)
    p_review.add_argument("--predicate", default=None)
    p_review.add_argument("--claim", dest="claim_id", default=None)

    p_decide = sub.add_parser("claim-decide", help="Persist one non-canonical review sidecar")
    p_decide.add_argument("project")
    p_decide.add_argument("claim_id")
    p_decide.add_argument("--decision", required=True, choices=[item.value for item in ReviewDecision])
    p_decide.add_argument("--predicate", dest="normalized_predicate", default=None)
    p_decide.add_argument("--value-json", default=None)
    p_decide.add_argument("--note", default="")
    p_decide.add_argument("--replace", action="store_true")

    p_mat_plan = sub.add_parser("materialization-plan", help="Recheck reviewed claims for quarantine materialization")
    p_mat_plan.add_argument("project")
    p_mat_plan.add_argument("--claim", dest="claim_id", default=None)

    p_mat_stage = sub.add_parser("materialization-stage", help="Write one ready claim to quarantine staging")
    p_mat_stage.add_argument("project")
    p_mat_stage.add_argument("claim_id")

    p_commit_plan = sub.add_parser(
        "canon-commit-plan",
        help="Inspect whether staged quarantine candidates are safe for explicit Canon commit",
    )
    p_commit_plan.add_argument("project")
    p_commit_plan.add_argument("--claim", dest="claim_id", default=None)

    p_commit = sub.add_parser(
        "canon-commit",
        help="Create one canonical Event/Fact from an exact reviewed quarantine candidate",
    )
    p_commit.add_argument("project")
    p_commit.add_argument("claim_id")
    p_commit.add_argument("--confirm-sha256", required=True)
    p_commit.add_argument("--actor", required=True)
    p_commit.add_argument("--note", default="")

    args = parser.parse_args()
    project = StoryProject.open(args.project)

    if args.command == "validate":
        entities = project.load_entities()
        events = project.load_events()
        facts = project.load_canon_facts()
        claims = project.load_claims()
        errors = project.validate_references()
        payload = {
            "ok": not errors,
            "entities": len(entities),
            "events": len(events),
            "canon_facts": len(facts),
            "staged_claims": len(claims),
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0 if not errors else 2)

    if args.command == "state":
        states = StoryStateProjector().project(project.load_events(), through_sequence=args.through)
        payload = {
            entity_id: {
                "values": state.values,
                "knowledge": sorted(state.knowledge),
                "resolved_plots": sorted(state.resolved_plots),
            }
            for entity_id, state in states.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "canon":
        resolution = CanonResolver().resolve(
            project.load_canon_facts(),
            subject=args.subject,
            predicate=args.predicate,
            through_sequence=args.through,
        )
        payload = {
            "ok": not resolution.ambiguous,
            "ambiguous": resolution.ambiguous,
            "fact": None if resolution.fact is None else _fact_payload(resolution.fact),
            "conflicts": [_fact_payload(fact) for fact in resolution.conflicts],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "knowledge":
        facts = KnowledgeTimeline(project.load_events()).known_facts(args.entity, through_sequence=args.through)
        print(json.dumps({"entity": args.entity, "through": args.through, "facts": sorted(facts)}, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "claim-review":
        try:
            payload = ClaimReviewWorkbench().build_queue(
                project,
                subject=args.subject,
                predicate=args.predicate,
                claim_id=args.claim_id,
            )
        except ClaimReviewError as exc:
            parser.exit(2, f"storyos: claim review failed: {exc}\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "claim-decide":
        kwargs = {}
        if args.value_json is not None:
            try:
                kwargs["normalized_value"] = json.loads(args.value_json)
            except json.JSONDecodeError as exc:
                parser.exit(2, f"storyos: invalid --value-json: {exc}\n")
        try:
            review, result = ClaimReviewWorkbench().decide(
                project,
                claim_id=args.claim_id,
                decision=args.decision,
                normalized_predicate=args.normalized_predicate,
                note=args.note,
                replace=args.replace,
                **kwargs,
            )
        except (ClaimReviewError, ValueError) as exc:
            parser.exit(2, f"storyos: claim decision failed: {exc}\n")
        print(
            json.dumps(
                {
                    "schema": "story.claim-review-result.v1",
                    "result": result,
                    "review": review.as_mapping(),
                    "policy": {"canonical_mutation": False, "materialization_required": True},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.command == "materialization-plan":
        try:
            payload = MaterializationWorkbench().build_plan(project, claim_id=args.claim_id)
        except (MaterializationError, ValueError) as exc:
            parser.exit(2, f"storyos: materialization plan failed: {exc}\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "materialization-stage":
        try:
            candidate, result = MaterializationWorkbench().stage(project, claim_id=args.claim_id)
        except (MaterializationError, ValueError) as exc:
            parser.exit(2, f"storyos: materialization staging failed: {exc}\n")
        print(
            json.dumps(
                {
                    "schema": "story.materialization-result.v1",
                    "result": result,
                    "candidate": candidate,
                    "policy": {
                        "quarantine_only": True,
                        "canonical_mutation": False,
                        "commit_required": True,
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.command == "canon-commit-plan":
        try:
            payload = CanonCommitWorkbench().build_plan(project, claim_id=args.claim_id)
        except (CanonCommitError, MaterializationError, ValueError) as exc:
            parser.exit(2, f"storyos: Canon commit plan failed: {exc}\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "canon-commit":
        try:
            commit_result, result = CanonCommitWorkbench().commit(
                project,
                claim_id=args.claim_id,
                confirm_sha256=args.confirm_sha256,
                actor=args.actor,
                note=args.note,
            )
        except (CanonCommitError, MaterializationError, ValueError) as exc:
            parser.exit(2, f"storyos: Canon commit failed: {exc}\n")
        print(
            json.dumps(
                {
                    "schema": "story.canon-commit-command-result.v1",
                    "result": result,
                    "commit": commit_result,
                    "policy": {
                        "explicit_candidate_sha256_confirmation": True,
                        "canonical_create_only": True,
                        "canonical_overwrite": False,
                        "audit_precedes_canonical_mutation": True,
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    checker = ClaimStager()
    canon = project.load_canon_facts()
    events = project.load_events()
    claims = project.load_claims()
    if args.claim_id is not None:
        claims = [claim for claim in claims if claim.id == args.claim_id]
        if not claims:
            raise SystemExit(f"unknown claim id: {args.claim_id}")
    rows = []
    for claim in claims:
        result = checker.check(claim, canon_facts=canon, events=events)
        rows.append(
            {
                "claim_id": result.claim_id,
                "can_approve": result.can_approve,
                "duplicate_of": result.duplicate_of,
                "issues": [asdict(issue) for issue in result.issues],
            }
        )
    print(json.dumps({"claims": rows}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
