from __future__ import annotations

from typing import Any

from storyos.manuscript_recovery import ManuscriptRecovery
from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace


class ProjectSession:
    """Build a compact read-only project/session view for desktop launchers.

    The session view intentionally omits manuscript and recovery draft content. It only
    exposes enough validated metadata to identify a StoryOS project and surface recovery
    work that needs the author's attention before entering the full workspace.
    """

    def __init__(self) -> None:
        self._workspace = AuthoringWorkspace()
        self._recovery = ManuscriptRecovery()

    def build(self, project: StoryProject) -> dict[str, Any]:
        manuscripts = self._workspace.list_manuscripts(project)
        recoveries: list[dict[str, Any]] = []
        recovery_slots = 0
        stale_base_drafts = 0

        for manuscript in manuscripts:
            view = self._recovery.load(project, str(manuscript["path"]))
            recovery = view.get("recovery")
            if not isinstance(recovery, dict):
                continue
            recovery_slots += 1
            if not bool(recovery.get("recoverable")):
                continue
            base_matches_current = bool(recovery.get("base_matches_current"))
            if not base_matches_current:
                stale_base_drafts += 1
            recoveries.append(
                {
                    "path": str(manuscript["path"]),
                    "title": str(manuscript["title"]),
                    "season": manuscript["season"],
                    "episode": manuscript["episode"],
                    "current_sha256": str(view["current_sha256"]),
                    "base_sha256": str(recovery["base_sha256"]),
                    "draft_sha256": str(recovery["draft_sha256"]),
                    "bytes": int(recovery["bytes"]),
                    "characters": int(recovery["characters"]),
                    "lines": int(recovery["lines"]),
                    "captured_mtime_ns": int(recovery["captured_mtime_ns"]),
                    "base_matches_current": base_matches_current,
                    "draft_matches_current": bool(recovery["draft_matches_current"]),
                    "recoverable": True,
                }
            )

        recoveries.sort(
            key=lambda item: (
                10**9 if item["season"] is None else int(item["season"]),
                10**9 if item["episode"] is None else int(item["episode"]),
                str(item["path"]),
            )
        )

        return {
            "schema": "story.authoring-project-session.v1",
            "project": {
                "id": str(project.manifest.get("id") or ""),
                "name": str(project.manifest.get("name") or ""),
                "language": str(project.manifest.get("language") or ""),
            },
            "summary": {
                "manuscripts": len(manuscripts),
                "recovery_slots": recovery_slots,
                "recoverable_drafts": len(recoveries),
                "stale_base_drafts": stale_base_drafts,
            },
            "recoveries": recoveries,
            "policy": {
                "read_only": True,
                "manuscript_mutation": False,
                "history_mutation": False,
                "recovery_mutation": False,
                "canonical_mutation": False,
                "staging_mutation": False,
            },
        }
