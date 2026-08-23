from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from storyos.authority import CanonResolver
from storyos.claims import ClaimStager
from storyos.duanxian_import import DuanxianImportError, DuanxianV39Importer
from storyos.knowledge import KnowledgeTimeline
from storyos.project import StoryProject
from storyos.state import StoryStateProjector
from storyos.worldstate_claims import (
    DuanxianWorldStateClaimExtractor,
    WorldStateClaimExtractionError,
)


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

    p_import = sub.add_parser(
        "import-duanxian-v39",
        help="Import the Duanxian Season 1 v3.9 mother package into a new StoryOS project",
    )
    p_import.add_argument("source")
    p_import.add_argument("target")
    p_import.add_argument("--allow-dirty-source", action="store_true")

    p_worldstate = sub.add_parser(
        "worldstate-claims",
        help="Analyze imported World State records into non-canonical Candidate Claims",
    )
    p_worldstate.add_argument("project")
    p_worldstate.add_argument(
        "--write",
        action="store_true",
        help="Persist deterministic Claims under staging/claims/world_state; default is dry-run",
    )

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

    args = parser.parse_args()

    if args.command == "import-duanxian-v39":
        try:
            report = DuanxianV39Importer(args.source).apply(
                args.target,
                allow_dirty_source=args.allow_dirty_source,
            )
        except DuanxianImportError as exc:
            parser.exit(2, f"storyos: import failed: {exc}\n")
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return

    project = StoryProject.open(args.project)

    if args.command == "worldstate-claims":
        extractor = DuanxianWorldStateClaimExtractor()
        try:
            extraction = extractor.analyze(project)
            persistence = (
                extractor.persist(project, extraction)
                if args.write
                else {"write": False, "claims_total": len(extraction.claims)}
            )
        except WorldStateClaimExtractionError as exc:
            parser.exit(2, f"storyos: World State extraction failed: {exc}\n")
        payload = extraction.report()
        payload["persistence"] = persistence
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

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
        facts = KnowledgeTimeline(project.load_events()).known_facts(
            args.entity,
            through_sequence=args.through,
        )
        print(
            json.dumps(
                {"entity": args.entity, "through": args.through, "facts": sorted(facts)},
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
