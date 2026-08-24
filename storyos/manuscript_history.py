from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError


class ManuscriptHistoryError(RuntimeError):
    """Raised when manuscript history cannot be read or archived safely."""


class ManuscriptHistory:
    """Content-addressed history for manuscript working copies.

    Historical snapshots live under ``.storyos/manuscript-history`` and mirror only
    workspace-validated manuscript paths. The current working copy is treated as the
    latest revision; snapshots are created only for versions that are about to be
    replaced by a successful CAS save.
    """

    def __init__(self) -> None:
        self._workspace = AuthoringWorkspace()

    def archive_bytes(
        self,
        project: StoryProject,
        normalized_path: str,
        raw: bytes,
    ) -> dict[str, Any]:
        directory = self._history_directory(project, normalized_path, create=True)
        sha256 = hashlib.sha256(raw).hexdigest()
        snapshot = directory / f"{sha256}.snapshot"

        if snapshot.exists():
            self._verify_snapshot(snapshot, sha256, raw)
            return {"sha256": sha256, "created": False}
        if snapshot.is_symlink():
            raise ManuscriptHistoryError("manuscript history snapshot cannot be a symlink")

        temp_path: Path | None = None
        try:
            fd, raw_temp = tempfile.mkstemp(
                prefix=f".{sha256}.",
                suffix=".tmp",
                dir=directory,
            )
            temp_path = Path(raw_temp)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())

            # A concurrent identical archive is harmless. Never adopt differing bytes.
            if snapshot.exists():
                self._verify_snapshot(snapshot, sha256, raw)
            else:
                os.replace(temp_path, snapshot)
                temp_path = None
                self._verify_snapshot(snapshot, sha256, raw)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return {"sha256": sha256, "created": True}

    def list_revisions(self, project: StoryProject, relative_path: str) -> dict[str, Any]:
        loaded = self._load_manuscript(project, relative_path)
        normalized_path = str(loaded["path"])
        directory = self._history_directory(project, normalized_path, create=False)
        current_sha = str(loaded["sha256"])

        archived: list[dict[str, Any]] = []
        if directory.exists():
            for path in directory.glob("*.snapshot"):
                if path.is_symlink() or not path.is_file():
                    raise ManuscriptHistoryError("manuscript history contains an unsafe snapshot")
                sha256 = path.stem
                if not _is_sha256(sha256):
                    raise ManuscriptHistoryError("manuscript history contains an invalid snapshot name")
                raw = path.read_bytes()
                self._verify_snapshot(path, sha256, raw)
                if sha256 == current_sha:
                    continue
                archived.append(
                    self._summary_from_raw(
                        sha256,
                        raw,
                        current=False,
                        captured_mtime_ns=path.stat().st_mtime_ns,
                    )
                )

        archived.sort(
            key=lambda row: (int(row["captured_mtime_ns"] or 0), str(row["sha256"])),
            reverse=True,
        )
        current = {
            "sha256": current_sha,
            "bytes": int(loaded["bytes"]),
            "characters": int(loaded["characters"]),
            "lines": int(loaded["lines"]),
            "current": True,
            "captured_mtime_ns": None,
        }
        return {
            "schema": "story.authoring-manuscript-history.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": normalized_path,
            "current_sha256": current_sha,
            "revisions": [current, *archived],
            "policy": _read_policy(),
        }

    def load_revision(
        self,
        project: StoryProject,
        relative_path: str,
        sha256: str,
    ) -> dict[str, Any]:
        revision_sha = _validate_sha256(sha256)
        loaded = self._load_manuscript(project, relative_path)
        normalized_path = str(loaded["path"])
        current_sha = str(loaded["sha256"])

        if revision_sha == current_sha:
            content = str(loaded["content"])
            return {
                "schema": "story.authoring-manuscript-revision.v1",
                "project_id": str(project.manifest.get("id") or ""),
                "path": normalized_path,
                "sha256": revision_sha,
                "bytes": int(loaded["bytes"]),
                "characters": int(loaded["characters"]),
                "lines": int(loaded["lines"]),
                "current": True,
                "content": content,
                "policy": _read_policy(),
            }

        directory = self._history_directory(project, normalized_path, create=False)
        snapshot = directory / f"{revision_sha}.snapshot"
        if snapshot.is_symlink() or not snapshot.is_file():
            raise ManuscriptHistoryError(f"unknown manuscript revision: {revision_sha}")
        raw = snapshot.read_bytes()
        self._verify_snapshot(snapshot, revision_sha, raw)
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ManuscriptHistoryError("manuscript history snapshot is not UTF-8 text") from exc
        summary = self._summary_from_raw(
            revision_sha,
            raw,
            current=False,
            captured_mtime_ns=snapshot.stat().st_mtime_ns,
        )
        return {
            "schema": "story.authoring-manuscript-revision.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": normalized_path,
            **summary,
            "content": content,
            "policy": _read_policy(),
        }

    def _load_manuscript(self, project: StoryProject, relative_path: str) -> dict[str, Any]:
        try:
            return self._workspace.load_manuscript(project, relative_path)
        except AuthoringWorkspaceError as exc:
            raise ManuscriptHistoryError(str(exc)) from exc

    def _history_directory(
        self,
        project: StoryProject,
        normalized_path: str,
        *,
        create: bool,
    ) -> Path:
        project_root = project.root.resolve()
        storyos_dir = project_root / ".storyos"
        history_root = storyos_dir / "manuscript-history"
        relative = Path(normalized_path)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ManuscriptHistoryError("manuscript history path escapes the project root")

        for path in (storyos_dir, history_root):
            if path.exists() and path.is_symlink():
                raise ManuscriptHistoryError("manuscript history root cannot be a symlink")

        cursor = history_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise ManuscriptHistoryError("manuscript history path cannot contain symlinks")

        directory = history_root / relative
        if create:
            directory.mkdir(parents=True, exist_ok=True)

        if history_root.exists():
            resolved_root = history_root.resolve()
            try:
                resolved_root.relative_to(project_root)
            except ValueError as exc:
                raise ManuscriptHistoryError("manuscript history root escapes the project") from exc
            if directory.exists():
                try:
                    directory.resolve().relative_to(resolved_root)
                except ValueError as exc:
                    raise ManuscriptHistoryError("manuscript history path escapes its root") from exc
        return directory

    @staticmethod
    def _verify_snapshot(path: Path, sha256: str, expected_raw: bytes) -> None:
        if path.is_symlink() or not path.is_file():
            raise ManuscriptHistoryError("manuscript history snapshot is not a regular file")
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != sha256 or raw != expected_raw:
            raise ManuscriptHistoryError("manuscript history snapshot failed integrity validation")

    @staticmethod
    def _summary_from_raw(
        sha256: str,
        raw: bytes,
        *,
        current: bool,
        captured_mtime_ns: int | None,
    ) -> dict[str, Any]:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ManuscriptHistoryError("manuscript history snapshot is not UTF-8 text") from exc
        return {
            "sha256": sha256,
            "bytes": len(raw),
            "characters": len(content),
            "lines": 0 if not content else content.count("\n") + 1,
            "current": current,
            "captured_mtime_ns": captured_mtime_ns,
        }


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _validate_sha256(value: str) -> str:
    trimmed = value.strip()
    if not _is_sha256(trimmed):
        raise ManuscriptHistoryError("revision sha256 must be 64 lowercase hexadecimal characters")
    return trimmed


def _read_policy() -> dict[str, bool]:
    return {
        "read_only": True,
        "manuscript_mutation": False,
        "history_mutation": False,
        "canonical_mutation": False,
        "staging_mutation": False,
    }
