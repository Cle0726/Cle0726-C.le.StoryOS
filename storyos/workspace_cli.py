from __future__ import annotations

import argparse
import json
import sys

from storyos.canon_commit import CanonCommitError, CanonCommitWorkbench
from storyos.claim_review import ClaimReviewError, ClaimReviewWorkbench, ReviewDecision
from storyos.manuscript_history import ManuscriptHistory, ManuscriptHistoryError
from storyos.manuscript_recovery import ManuscriptRecovery, ManuscriptRecoveryError
from storyos.manuscript_writer import (
    MAX_MANUSCRIPT_BYTES,
    ManuscriptConflictError,
    ManuscriptWriteError,
    ManuscriptWriter,
)
from storyos.materialization import MaterializationError, MaterializationWorkbench
from storyos.project import StoryProject
from storyos.project_authoring import (
    ProjectAuthoringError,
    create_manuscript,
    create_project,
)
from storyos.project_session import ProjectSession
from storyos.scene_workspace import SceneWorkspace, SceneWorkspaceError
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError


def _read_manuscript_stdin() -> str:
    raw = sys.stdin.buffer.read(MAX_MANUSCRIPT_BYTES + 1)
    if len(raw) > MAX_MANUSCRIPT_BYTES:
        raise ManuscriptWriteError(
            f"manuscript exceeds the {MAX_MANUSCRIPT_BYTES}-byte safety limit"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManuscriptWriteError("manuscript save input must be UTF-8 text") from exc


def _conflict_payload(
    project: StoryProject,
    workspace: AuthoringWorkspace,
    path: str,
    expected_sha256: str,
) -> dict:
    current = workspace.load_manuscript(project, path)
    return {
        "schema": "story.authoring-manuscript-conflict.v1",
        "project_id": str(project.manifest.get("id") or ""),
        "path": str(current["path"]),
        "reason": "stale_working_copy",
        "expected_sha256": expected_sha256,
        "current_sha256": str(current["sha256"]),
        "current": {
            "title": current["title"],
            "season": current["season"],
            "episode": current["episode"],
            "bytes": current["bytes"],
            "characters": current["characters"],
            "lines": current["lines"],
            "sha256": current["sha256"],
            "content": current["content"],
        },
        "policy": {
            "read_only": True,
            "manuscript_mutation": False,
            "history_mutation": False,
            "recovery_mutation": False,
            "canonical_mutation": False,
            "staging_mutation": False,
        },
    }


def _review_result(review, result: str) -> dict:
    return {
        "schema": "story.claim-review-result.v1",
        "result": result,
        "review": review.as_mapping(),
        "policy": {
            "read_only": False,
            "review_mutation": True,
            "materialization_mutation": False,
            "canonical_mutation": False,
            "staging_mutation": True,
        },
    }


def _materialization_result(candidate: dict, result: str) -> dict:
    return {
        "schema": "story.materialization-result.v1",
        "result": result,
        "candidate": candidate,
        "policy": {
            "read_only": False,
            "review_mutation": False,
            "materialization_mutation": True,
            "canonical_mutation": False,
            "staging_mutation": True,
            "quarantine_only": True,
            "commit_required": True,
        },
    }


def _canon_commit_result(commit_result: dict, result: str) -> dict:
    return {
        "schema": "story.canon-commit-command-result.v1",
        "result": result,
        "commit": commit_result,
        "policy": {
            "read_only": False,
            "review_mutation": False,
            "materialization_mutation": False,
            "canonical_mutation": True,
            "staging_mutation": False,
            "explicit_candidate_sha256_confirmation": True,
            "canonical_create_only": True,
            "canonical_overwrite": False,
            "audit_precedes_canonical_mutation": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="storyos-workspace")
    sub = parser.add_subparsers(dest="command", required=True)

    p_project_create = sub.add_parser(
        "project-create",
        help="Initialize StoryOS inside an existing directory without overwriting user files",
    )
    p_project_create.add_argument("project")
    p_project_create.add_argument("--name", required=True)
    p_project_create.add_argument("--language", default="zh-CN")

    p_session = sub.add_parser(
        "session",
        help="Build a compact read-only project session view for launcher/recovery entry",
    )
    p_session.add_argument("project")

    p_snapshot = sub.add_parser("snapshot", help="Build a read-only authoring workspace snapshot")
    p_snapshot.add_argument("project")
    p_snapshot.add_argument("--through", type=int, default=None)

    p_scene = sub.add_parser(
        "scene",
        help="Build a read-only scene/episode workspace anchored to one manuscript",
    )
    p_scene.add_argument("project")
    p_scene.add_argument("path", help="Project-relative manuscript path returned by snapshot")
    p_scene.add_argument("--through", type=int, default=None)
    p_scene.add_argument(
        "--pov",
        default=None,
        help="Optional character entity ID; enables conservative POV-safe context",
    )

    p_entity = sub.add_parser("entity", help="Build a read-only focused view for one story entity")
    p_entity.add_argument("project")
    p_entity.add_argument("entity_id")
    p_entity.add_argument("--through", type=int, default=None)

    p_manuscript = sub.add_parser("manuscript", help="Read one manuscript working copy")
    p_manuscript.add_argument("project")
    p_manuscript.add_argument("path", help="Project-relative manuscript path returned by snapshot")

    p_manuscript_create = sub.add_parser(
        "manuscript-create",
        help="Create one new manuscript working copy without overwriting an episode",
    )
    p_manuscript_create.add_argument("project")
    p_manuscript_create.add_argument("--title", required=True)
    p_manuscript_create.add_argument("--season", required=True, type=int)
    p_manuscript_create.add_argument("--episode", required=True, type=int)

    p_history = sub.add_parser(
        "manuscript-history",
        help="List immutable archived revisions for one manuscript working copy",
    )
    p_history.add_argument("project")
    p_history.add_argument("path", help="Project-relative manuscript path returned by snapshot")

    p_revision = sub.add_parser(
        "manuscript-revision",
        help="Read one immutable archived manuscript revision by SHA-256",
    )
    p_revision.add_argument("project")
    p_revision.add_argument("path", help="Project-relative manuscript path returned by snapshot")
    p_revision.add_argument("sha256", help="Revision SHA-256 returned by manuscript-history")

    p_recovery = sub.add_parser(
        "manuscript-recovery",
        help="Read the isolated crash-recovery draft for one manuscript",
    )
    p_recovery.add_argument("project")
    p_recovery.add_argument("path", help="Project-relative manuscript path returned by snapshot")

    p_recovery_save = sub.add_parser(
        "manuscript-recovery-save",
        help="Autosave a draft into the isolated recovery area without touching the manuscript",
    )
    p_recovery_save.add_argument("project")
    p_recovery_save.add_argument("path", help="Project-relative manuscript path returned by snapshot")
    p_recovery_save.add_argument("base_sha256", help="Manuscript SHA-256 used as this draft's editing base")

    p_recovery_clear = sub.add_parser(
        "manuscript-recovery-clear",
        help="Clear an inspected recovery draft using its exact draft SHA-256",
    )
    p_recovery_clear.add_argument("project")
    p_recovery_clear.add_argument("path", help="Project-relative manuscript path returned by snapshot")
    p_recovery_clear.add_argument("expected_draft_sha256", help="Recovery draft SHA-256 observed by the caller")

    p_save = sub.add_parser(
        "manuscript-save",
        help="Save one existing manuscript working copy with an exact SHA-256 compare-and-swap guard",
    )
    p_save.add_argument("project")
    p_save.add_argument("path", help="Project-relative manuscript path returned by snapshot")
    p_save.add_argument("expected_sha256", help="SHA-256 observed when the manuscript was loaded")

    p_claim_review = sub.add_parser(
        "claim-review",
        help="Build the non-canonical claim review queue for the desktop governance view",
    )
    p_claim_review.add_argument("project")
    p_claim_review.add_argument("--claim", dest="claim_id", default=None)

    p_claim_decide = sub.add_parser(
        "claim-decide",
        help="Persist one explicit non-canonical claim review decision",
    )
    p_claim_decide.add_argument("project")
    p_claim_decide.add_argument("claim_id")
    p_claim_decide.add_argument(
        "--decision",
        required=True,
        choices=[item.value for item in ReviewDecision],
    )
    p_claim_decide.add_argument("--predicate", dest="normalized_predicate", default=None)
    p_claim_decide.add_argument("--value-json", default=None)
    p_claim_decide.add_argument("--note", default="")
    p_claim_decide.add_argument("--replace", action="store_true")

    p_materialization_plan = sub.add_parser(
        "materialization-plan",
        help="Recheck reviewed claims for quarantine materialization",
    )
    p_materialization_plan.add_argument("project")
    p_materialization_plan.add_argument("--claim", dest="claim_id", default=None)

    p_materialization_stage = sub.add_parser(
        "materialization-stage",
        help="Publish one ready reviewed claim into create-only quarantine staging",
    )
    p_materialization_stage.add_argument("project")
    p_materialization_stage.add_argument("claim_id")

    p_commit_plan = sub.add_parser(
        "canon-commit-plan",
        help="Inspect explicit Canon commit readiness for quarantined candidates",
    )
    p_commit_plan.add_argument("project")
    p_commit_plan.add_argument("--claim", dest="claim_id", default=None)

    p_commit = sub.add_parser(
        "canon-commit",
        help="Commit one exact quarantine candidate into create-only Canon",
    )
    p_commit.add_argument("project")
    p_commit.add_argument("claim_id")
    p_commit.add_argument("--confirm-sha256", required=True)
    p_commit.add_argument("--actor", required=True)
    p_commit.add_argument("--note", default="")

    args = parser.parse_args()
    try:
        if args.command == "project-create":
            payload = create_project(
                args.project,
                name=args.name,
                language=args.language,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return

        project = StoryProject.open(args.project)
        workspace = AuthoringWorkspace()
        history = ManuscriptHistory()
        recovery = ManuscriptRecovery()

        if args.command == "session":
            payload = ProjectSession().build(project)
        elif args.command == "snapshot":
            payload = workspace.build_snapshot(project, through_sequence=args.through)
        elif args.command == "scene":
            payload = SceneWorkspace().build(
                project,
                args.path,
                through_sequence=args.through,
                pov_entity_id=args.pov,
            )
        elif args.command == "entity":
            payload = workspace.build_entity_view(
                project, args.entity_id, through_sequence=args.through
            )
        elif args.command == "manuscript":
            payload = workspace.load_manuscript(project, args.path)
        elif args.command == "manuscript-create":
            payload = create_manuscript(
                project,
                title=args.title,
                season=args.season,
                episode=args.episode,
            )
        elif args.command == "manuscript-history":
            payload = history.list_revisions(project, args.path)
        elif args.command == "manuscript-revision":
            payload = history.load_revision(project, args.path, args.sha256)
        elif args.command == "manuscript-recovery":
            payload = recovery.load(project, args.path)
        elif args.command == "manuscript-recovery-save":
            payload = recovery.save(
                project,
                args.path,
                base_sha256=args.base_sha256,
                content=_read_manuscript_stdin(),
            )
        elif args.command == "manuscript-recovery-clear":
            payload = recovery.clear(
                project,
                args.path,
                expected_draft_sha256=args.expected_draft_sha256,
            )
        elif args.command == "manuscript-save":
            try:
                payload = ManuscriptWriter().save(
                    project,
                    args.path,
                    expected_sha256=args.expected_sha256,
                    content=_read_manuscript_stdin(),
                )
            except ManuscriptConflictError:
                payload = _conflict_payload(
                    project,
                    workspace,
                    args.path,
                    args.expected_sha256,
                )
        elif args.command == "claim-review":
            payload = ClaimReviewWorkbench().build_queue(project, claim_id=args.claim_id)
        elif args.command == "claim-decide":
            normalized_value = None
            if args.value_json is not None:
                try:
                    normalized_value = json.loads(args.value_json)
                except json.JSONDecodeError as exc:
                    raise ClaimReviewError(f"invalid normalized value JSON: {exc}") from exc
            kwargs = {}
            if args.value_json is not None:
                kwargs["normalized_value"] = normalized_value
            review, result = ClaimReviewWorkbench().decide(
                project,
                claim_id=args.claim_id,
                decision=args.decision,
                normalized_predicate=args.normalized_predicate,
                note=args.note,
                replace=args.replace,
                **kwargs,
            )
            payload = _review_result(review, result)
        elif args.command == "materialization-plan":
            payload = MaterializationWorkbench().build_plan(
                project,
                claim_id=args.claim_id,
            )
        elif args.command == "materialization-stage":
            candidate, result = MaterializationWorkbench().stage(
                project,
                claim_id=args.claim_id,
            )
            payload = _materialization_result(candidate, result)
        elif args.command == "canon-commit-plan":
            payload = CanonCommitWorkbench().build_plan(
                project,
                claim_id=args.claim_id,
            )
        elif args.command == "canon-commit":
            commit_result, result = CanonCommitWorkbench().commit(
                project,
                claim_id=args.claim_id,
                confirm_sha256=args.confirm_sha256,
                actor=args.actor,
                note=args.note,
            )
            payload = _canon_commit_result(commit_result, result)
        else:  # pragma: no cover - argparse requires one of the registered commands
            raise ValueError(f"unsupported workspace command: {args.command}")
    except (
        AuthoringWorkspaceError,
        SceneWorkspaceError,
        ManuscriptHistoryError,
        ManuscriptRecoveryError,
        ManuscriptWriteError,
        ProjectAuthoringError,
        ClaimReviewError,
        MaterializationError,
        CanonCommitError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        parser.exit(2, f"storyos-workspace: {exc}\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
