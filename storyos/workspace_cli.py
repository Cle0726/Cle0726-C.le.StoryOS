from __future__ import annotations

import argparse
import json

from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError


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
        else:
            payload = workspace.load_manuscript(project, args.path)
    except (AuthoringWorkspaceError, FileNotFoundError, OSError, ValueError) as exc:
        parser.exit(2, f"storyos-workspace: {exc}\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
