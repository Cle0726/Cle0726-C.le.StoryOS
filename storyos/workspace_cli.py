from __future__ import annotations

import argparse
import json
import sys

from storyos.manuscript_history import ManuscriptHistory, ManuscriptHistoryError
from storyos.manuscript_recovery import ManuscriptRecovery, ManuscriptRecoveryError
from storyos.manuscript_writer import (
    MAX_MANUSCRIPT_BYTES,
    ManuscriptConflictError,
    ManuscriptWriteError,
    ManuscriptWriter,
)
from storyos.project import StoryProject
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
            "canonical_mutation": False,
            "staging_mutation": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="storyos-workspace")
    sub = parser.add_subparsers(dest="command", required=True)

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

    args = parser.parse_args()
    try:
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
        else:
            try:
                payload = ManuscriptWriter().save(
                    project,
                    args.path,
                    expected_sha256=args.expected_sha256,
                    content=_read_manuscript_stdin(),
                )
            except ManuscriptConflictError:
                # A stale save is expected workflow, not a transport failure. Return the
                # current disk version as a strictly read-only conflict envelope so desktop
                # clients can compare explicitly without parsing stderr strings.
                payload = _conflict_payload(
                    project,
                    workspace,
                    args.path,
                    args.expected_sha256,
                )
    except (
        AuthoringWorkspaceError,
        SceneWorkspaceError,
        ManuscriptHistoryError,
        ManuscriptRecoveryError,
        ManuscriptWriteError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        parser.exit(2, f"storyos-workspace: {exc}\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
