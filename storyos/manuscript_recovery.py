from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from storyos.project import StoryProject
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError

MAX_RECOVERY_DRAFT_BYTES = 16 * 1024 * 1024
MAX_RECOVERY_RECORD_BYTES = 40 * 1024 * 1024


class ManuscriptRecoveryError(RuntimeError):
    """Raised when a recovery draft cannot be read or mutated safely."""


class ManuscriptRecovery:
    """Single-slot crash recovery for one workspace-validated manuscript path.

    Recovery drafts live under ``.storyos/manuscript-recovery`` and never mutate the
    manuscript working copy, immutable manuscript history, Canon, or staging. The slot
    records the manuscript SHA observed by the editor plus a SHA of the logical UTF-8
    draft content. Loading a recovery draft never applies it automatically.
    """

    def __init__(self) -> None:
        self._workspace = AuthoringWorkspace()

    def load(self, project: StoryProject, relative_path: str) -> dict[str, Any]:
        loaded = self._load_manuscript(project, relative_path)
        normalized_path = str(loaded["path"])
        record_path = self._record_path(project, normalized_path, create=False)
        record = self._read_record(record_path, normalized_path)
        return self._read_payload(project, loaded, record, record_path)

    def save(
        self,
        project: StoryProject,
        relative_path: str,
        *,
        base_sha256: str,
        content: str,
    ) -> dict[str, Any]:
        base = _validate_sha256(base_sha256, "base_sha256")
        if not isinstance(content, str):
            raise ManuscriptRecoveryError("recovery draft content must be text")
        if "\0" in content:
            raise ManuscriptRecoveryError("recovery draft content cannot contain NUL characters")
        raw_content = content.encode("utf-8")
        if len(raw_content) > MAX_RECOVERY_DRAFT_BYTES:
            raise ManuscriptRecoveryError(
                f"recovery draft exceeds the {MAX_RECOVERY_DRAFT_BYTES}-byte safety limit"
            )

        loaded = self._load_manuscript(project, relative_path)
        normalized_path = str(loaded["path"])
        record_path = self._record_path(project, normalized_path, create=True)
        draft_sha = hashlib.sha256(raw_content).hexdigest()
        record = {
            "schema": "story.manuscript-recovery-record.v1",
            "path": normalized_path,
            "base_sha256": base,
            "draft_sha256": draft_sha,
            "content": content,
        }
        encoded = (
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_RECOVERY_RECORD_BYTES:
            raise ManuscriptRecoveryError("recovery record exceeded the storage safety limit")

        if record_path.exists() and record_path.is_symlink():
            raise ManuscriptRecoveryError("recovery draft record cannot be a symlink")

        temp_path: Path | None = None
        try:
            fd, raw_temp = tempfile.mkstemp(
                prefix=".draft.",
                suffix=".tmp",
                dir=record_path.parent,
            )
            temp_path = Path(raw_temp)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if record_path.exists() and record_path.is_symlink():
                raise ManuscriptRecoveryError("recovery draft record became a symlink")
            os.replace(temp_path, record_path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        stored = self._read_record(record_path, normalized_path)
        if stored is None or stored["draft_sha256"] != draft_sha or stored["content"] != content:
            raise ManuscriptRecoveryError("recovery draft failed post-write integrity validation")
        current = self._load_manuscript(project, normalized_path)
        return {
            "schema": "story.authoring-manuscript-recovery-save.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": normalized_path,
            "recovery": self._summary(current, stored, record_path),
            "policy": _write_policy(),
        }

    def clear(
        self,
        project: StoryProject,
        relative_path: str,
        *,
        expected_draft_sha256: str,
    ) -> dict[str, Any]:
        expected = _validate_sha256(expected_draft_sha256, "expected_draft_sha256")
        loaded = self._load_manuscript(project, relative_path)
        normalized_path = str(loaded["path"])
        record_path = self._record_path(project, normalized_path, create=False)
        record = self._read_record(record_path, normalized_path)

        if record is None:
            return {
                "schema": "story.authoring-manuscript-recovery-clear.v1",
                "project_id": str(project.manifest.get("id") or ""),
                "path": normalized_path,
                "expected_draft_sha256": expected,
                "cleared": False,
                "reason": "absent",
                "policy": _write_policy(),
            }
        if record["draft_sha256"] != expected:
            raise ManuscriptRecoveryError(
                "recovery draft changed since it was inspected; refusing to clear a newer draft"
            )
        if record_path.is_symlink() or not record_path.is_file():
            raise ManuscriptRecoveryError("recovery draft record is not a regular file")

        # Re-read immediately before unlink so a changed slot is not knowingly removed.
        latest = self._read_record(record_path, normalized_path)
        if latest is None or latest["draft_sha256"] != expected:
            raise ManuscriptRecoveryError(
                "recovery draft changed during clear; refusing to remove it"
            )
        record_path.unlink()
        return {
            "schema": "story.authoring-manuscript-recovery-clear.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": normalized_path,
            "expected_draft_sha256": expected,
            "cleared": True,
            "reason": "cleared",
            "policy": _write_policy(),
        }

    def _read_payload(
        self,
        project: StoryProject,
        loaded: dict[str, Any],
        record: dict[str, Any] | None,
        record_path: Path,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "story.authoring-manuscript-recovery.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": str(loaded["path"]),
            "current_sha256": str(loaded["sha256"]),
            "present": record is not None,
            "recovery": None,
            "policy": _read_policy(),
        }
        if record is not None:
            payload["recovery"] = self._summary(loaded, record, record_path)
        return payload

    @staticmethod
    def _summary(
        loaded: dict[str, Any],
        record: dict[str, Any],
        record_path: Path,
    ) -> dict[str, Any]:
        content = str(record["content"])
        raw = content.encode("utf-8")
        matches_current = content == str(loaded["content"])
        return {
            "base_sha256": str(record["base_sha256"]),
            "draft_sha256": str(record["draft_sha256"]),
            "bytes": len(raw),
            "characters": len(content),
            "lines": 0 if not content else content.count("\n") + 1,
            "captured_mtime_ns": record_path.stat().st_mtime_ns,
            "base_matches_current": str(record["base_sha256"]) == str(loaded["sha256"]),
            "draft_matches_current": matches_current,
            "recoverable": not matches_current,
            "content": content,
        }

    def _load_manuscript(self, project: StoryProject, relative_path: str) -> dict[str, Any]:
        try:
            return self._workspace.load_manuscript(project, relative_path)
        except AuthoringWorkspaceError as exc:
            raise ManuscriptRecoveryError(str(exc)) from exc

    def _record_path(
        self,
        project: StoryProject,
        normalized_path: str,
        *,
        create: bool,
    ) -> Path:
        project_root = project.root.resolve()
        storyos_dir = project_root / ".storyos"
        recovery_root = storyos_dir / "manuscript-recovery"
        relative = Path(normalized_path)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ManuscriptRecoveryError("manuscript recovery path escapes the project root")

        for path in (storyos_dir, recovery_root):
            if path.exists() and path.is_symlink():
                raise ManuscriptRecoveryError("manuscript recovery root cannot be a symlink")

        cursor = recovery_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise ManuscriptRecoveryError("manuscript recovery path cannot contain symlinks")

        directory = recovery_root / relative
        if create:
            directory.mkdir(parents=True, exist_ok=True)

        if recovery_root.exists():
            resolved_root = recovery_root.resolve()
            try:
                resolved_root.relative_to(project_root)
            except ValueError as exc:
                raise ManuscriptRecoveryError("manuscript recovery root escapes the project") from exc
            if directory.exists():
                try:
                    directory.resolve().relative_to(resolved_root)
                except ValueError as exc:
                    raise ManuscriptRecoveryError("manuscript recovery path escapes its root") from exc
        return directory / "draft.json"

    @staticmethod
    def _read_record(record_path: Path, normalized_path: str) -> dict[str, Any] | None:
        if not record_path.exists():
            return None
        if record_path.is_symlink() or not record_path.is_file():
            raise ManuscriptRecoveryError("recovery draft record is not a regular file")
        raw = record_path.read_bytes()
        if len(raw) > MAX_RECOVERY_RECORD_BYTES:
            raise ManuscriptRecoveryError("recovery draft record exceeded the read safety limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManuscriptRecoveryError("recovery draft record is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ManuscriptRecoveryError("recovery draft record must be an object")
        if value.get("schema") != "story.manuscript-recovery-record.v1":
            raise ManuscriptRecoveryError("recovery draft record has an unsupported schema")
        if value.get("path") != normalized_path:
            raise ManuscriptRecoveryError("recovery draft record path does not match its manuscript")

        base = _validate_sha256(str(value.get("base_sha256") or ""), "base_sha256")
        draft_sha = _validate_sha256(str(value.get("draft_sha256") or ""), "draft_sha256")
        content = value.get("content")
        if not isinstance(content, str):
            raise ManuscriptRecoveryError("recovery draft content must be text")
        if "\0" in content:
            raise ManuscriptRecoveryError("recovery draft content cannot contain NUL characters")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_RECOVERY_DRAFT_BYTES:
            raise ManuscriptRecoveryError("recovery draft exceeded the content safety limit")
        if hashlib.sha256(encoded).hexdigest() != draft_sha:
            raise ManuscriptRecoveryError("recovery draft failed SHA-256 integrity validation")
        return {
            "schema": "story.manuscript-recovery-record.v1",
            "path": normalized_path,
            "base_sha256": base,
            "draft_sha256": draft_sha,
            "content": content,
        }


def _validate_sha256(value: str, label: str) -> str:
    trimmed = value.strip()
    if len(trimmed) != 64 or any(ch not in "0123456789abcdef" for ch in trimmed):
        raise ManuscriptRecoveryError(f"{label} must be 64 lowercase hexadecimal characters")
    return trimmed


def _read_policy() -> dict[str, bool]:
    return {
        "read_only": True,
        "manuscript_mutation": False,
        "history_mutation": False,
        "recovery_mutation": False,
        "canonical_mutation": False,
        "staging_mutation": False,
    }


def _write_policy() -> dict[str, bool]:
    return {
        "read_only": False,
        "manuscript_mutation": False,
        "history_mutation": False,
        "recovery_mutation": True,
        "canonical_mutation": False,
        "staging_mutation": False,
    }
