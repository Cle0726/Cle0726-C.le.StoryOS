from __future__ import annotations

from typing import Any

from storyos.canon_commit import CanonCommitError, CanonCommitWorkbench as _CanonCommitWorkbench
from storyos.file_lock import ProjectFileLockError, project_file_lock
from storyos.project import StoryProject


class CanonCommitWorkbench(_CanonCommitWorkbench):
    """Canon commit service with a project-wide mutation lock.

    The underlying workbench already performs candidate SHA checks, durable audit-before-
    Canon authorization, revalidation, create-only writes, and post-write verification.
    This service adds the missing transaction boundary between the final revalidation and
    canonical create for StoryOS processes by holding one project-wide OS lock for the
    entire commit operation.
    """

    def commit(
        self,
        project: StoryProject,
        *,
        claim_id: str,
        confirm_sha256: str,
        actor: str,
        note: str = "",
    ) -> tuple[dict[str, Any], str]:
        try:
            with project_file_lock(project.root, "canon-commit"):
                return super().commit(
                    project,
                    claim_id=claim_id,
                    confirm_sha256=confirm_sha256,
                    actor=actor,
                    note=note,
                )
        except ProjectFileLockError as exc:
            raise CanonCommitError(str(exc)) from exc
