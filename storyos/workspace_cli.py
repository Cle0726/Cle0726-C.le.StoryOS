from __future__ import annotations

import argparse
import json
import sys

from storyos.manuscript_writer import (
    MAX_MANUSCRIPT_BYTES,
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
        if args.command == "snapshot":
            payload = workspace.build_snapshot(project, through_sequence=args.through)
        elif args.command == "entity":
            payload = workspace.build_entity_view(
                project, args.entity_id, through_sequence=args.through
            )
        elif args.command == "manuscript":
            payload = workspace.load_manuscript(project, args.path)
        else:
            payload = ManuscriptWriter().save(
                project,
                args.path,
                expected_sha256=args.expected_sha256,
                content=_read_manuscript_stdin(),
            )
    except (
        AuthoringWorkspaceError,
        ManuscriptWriteError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        parser.exit(2, f"storyos-workspace: {exc}\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
