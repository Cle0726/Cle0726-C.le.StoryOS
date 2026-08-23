from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from storyos.manuscript_history import ManuscriptHistory, ManuscriptHistoryError
from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError

MAX_MANUSCRIPT_BYTES = 16 * 1024 * 1024


class ManuscriptWriteError(RuntimeError):
    """Raised when a manuscript working-copy write cannot be completed safely."""


class ManuscriptConflictError(ManuscriptWriteError):
    """Raised when the manuscript changed after the editor loaded it."""


class ManuscriptWriter:
    """Write only existing manuscript working copies behind an exact SHA-256 CAS guard.

    This class has no Canon, staging, review, materialization, or claim mutation methods.
    It deliberately reuses the read-only workspace path validator before every write.
    Immediately before replacing a manuscript it archives the version being replaced
    into the content-addressed manuscript history store.
    """

    def __init__(self) -> None:
        self._workspace = AuthoringWorkspace()
        self._history = ManuscriptHistory()

    def save(
        self,
        project: StoryProject,
        relative_path: str,
        *,
        expected_sha256: str,
        content: str,
    ) -> dict[str, Any]:
        expected = _validate_sha256(expected_sha256)
        if not isinstance(content, str):
            raise ManuscriptWriteError("manuscript content must be text")
        if "\0" in content:
            raise ManuscriptWriteError("manuscript content cannot contain NUL characters")

        try:
            loaded = self._workspace.load_manuscript(project, relative_path)
        except AuthoringWorkspaceError as exc:
            raise ManuscriptWriteError(str(exc)) from exc

        normalized_path = str(loaded["path"])
        candidate = project.root / Path(normalized_path)
        if candidate.is_symlink():
            raise ManuscriptWriteError("manuscript symlinks are not supported")
        path = candidate.resolve()
        if not path.is_file():
            raise ManuscriptWriteError(f"unknown manuscript file: {relative_path}")

        current_raw = path.read_bytes()
        current_sha = hashlib.sha256(current_raw).hexdigest()
        if current_sha != expected or str(loaded["sha256"]) != expected:
            raise ManuscriptConflictError(
                "manuscript changed since it was loaded; reload before saving "
                f"(expected {expected}, current {current_sha})"
            )

        had_utf8_bom = current_raw.startswith(b"\xef\xbb\xbf")
        encoded = content.encode("utf-8")
        next_raw = (b"\xef\xbb\xbf" + encoded) if had_utf8_bom else encoded
        if len(next_raw) > MAX_MANUSCRIPT_BYTES:
            raise ManuscriptWriteError(
                f"manuscript exceeds the {MAX_MANUSCRIPT_BYTES}-byte safety limit"
            )

        previous_mode = stat.S_IMODE(path.stat().st_mode)
        temp_path: Path | None = None
        archived: dict[str, Any] | None = None
        try:
            fd, raw_temp = tempfile.mkstemp(
                prefix=f".{path.name}.storyos-",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(raw_temp)
            with os.fdopen(fd, "wb") as handle:
                handle.write(next_raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_path, previous_mode)
            except OSError:
                # Permission-mode preservation is best effort on platforms that do not expose it.
                pass

            # Recheck immediately before history/archive + replace so another editor cannot
            # be silently overwritten. No history mutation happens on a stale save.
            if candidate.is_symlink():
                raise ManuscriptWriteError("manuscript became a symlink before save")
            latest_raw = path.read_bytes()
            latest_sha = hashlib.sha256(latest_raw).hexdigest()
            if latest_sha != expected:
                raise ManuscriptConflictError(
                    "manuscript changed during save; reload before retrying "
                    f"(expected {expected}, current {latest_sha})"
                )

            try:
                archived = self._history.archive_bytes(project, normalized_path, latest_raw)
            except ManuscriptHistoryError as exc:
                raise ManuscriptWriteError(f"failed to archive previous manuscript revision: {exc}") from exc

            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        written = path.read_bytes()
        written_sha = hashlib.sha256(written).hexdigest()
        decoded = written.decode("utf-8-sig")
        return {
            "schema": "story.authoring-manuscript-save.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": normalized_path,
            "previous_sha256": expected,
            "sha256": written_sha,
            "bytes": len(written),
            "characters": len(decoded),
            "lines": 0 if not decoded else decoded.count("\n") + 1,
            "written": True,
            "history": {
                "archived_previous_sha256": str((archived or {}).get("sha256") or expected),
                "created": bool((archived or {}).get("created", False)),
            },
            "policy": {
                "read_only": False,
                "manuscript_mutation": True,
                "history_mutation": True,
                "canonical_mutation": False,
                "staging_mutation": False,
            },
        }


def _validate_sha256(value: str) -> str:
    expected = value.strip()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ManuscriptWriteError("expected_sha256 must be 64 lowercase hexadecimal characters")
    return expected
