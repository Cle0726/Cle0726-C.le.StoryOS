from __future__ import annotations

import argparse
import json
import sys

from storyos.manuscript_history import ManuscriptHistory, ManuscriptHistoryError
from storyos.manuscript_writer import (
    MAX_MANUSCRIPT_BYTES,
    ManuscriptConflictError,
    ManuscriptWriteError,
    ManuscriptWriter,
)
from storyos.project import StoryProject
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

    p_snapshot = sub.add_parser("snapshot", help="Build a read-only authoring workspace snapshot")
    p_snapshot.add_argument("project")
    p_snapshot.add_argument("--through", type=int, default=None)

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
        if args.command == "snapshot":
            payload = workspace.build_snapshot(project, through_sequence=args.through)
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
        ManuscriptHistoryError,
        ManuscriptWriteError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        parser.exit(2, f"storyos-workspace: {exc}\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
