from __future__ import annotations

import codecs
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from storyos.project import StoryProject


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown"}


class ManuscriptWorkingCopyError(RuntimeError):
    """Raised when a manuscript working-copy write is unsafe or invalid."""


class ManuscriptConflictError(ManuscriptWorkingCopyError):
    """Raised when the on-disk manuscript changed after the editor loaded it."""

    def __init__(self, expected_sha256: str, current_sha256: str) -> None:
        super().__init__("manuscript changed on disk; reload before saving")
        self.expected_sha256 = expected_sha256
        self.current_sha256 = current_sha256


class ManuscriptWorkingCopy:
    """Narrow author-text mutation surface.

    This class can replace an existing manuscript working-copy file only. It does
    not touch Story State, staging claims, Canon facts, review state or commits.
    """

    def save(
        self,
        project: StoryProject,
        relative_path: str,
        *,
        expected_sha256: str,
        content: str,
    ) -> dict[str, Any]:
        expected = str(expected_sha256).strip().lower()
        if not _SHA256_RE.fullmatch(expected):
            raise ManuscriptWorkingCopyError("expected_sha256 must be a lowercase SHA-256 hex digest")
        if not isinstance(content, str):
            raise ManuscriptWorkingCopyError("manuscript content must be text")

        path = _resolve_manuscript_path(project, relative_path)
        raw = path.read_bytes()
        current_sha256 = hashlib.sha256(raw).hexdigest()
        if current_sha256 != expected:
            raise ManuscriptConflictError(expected, current_sha256)

        has_bom = raw.startswith(codecs.BOM_UTF8)
        encoded = content.encode("utf-8")
        next_raw = codecs.BOM_UTF8 + encoded if has_bom else encoded
        next_sha256 = hashlib.sha256(next_raw).hexdigest()
        if next_raw == raw:
            return _result_payload(
                project,
                path,
                previous_sha256=current_sha256,
                sha256=next_sha256,
                raw=next_raw,
                content=content,
                status="unchanged",
            )

        mode = stat.S_IMODE(path.stat().st_mode)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.storyos-",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(next_raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, path)
            _best_effort_fsync_directory(path.parent)
        finally:
            if temp_path.exists():
                temp_path.unlink()

        return _result_payload(
            project,
            path,
            previous_sha256=current_sha256,
            sha256=next_sha256,
            raw=next_raw,
            content=content,
            status="saved",
        )


def _resolve_manuscript_path(project: StoryProject, relative_path: str) -> Path:
    root = _manuscript_root(project)
    requested = Path(str(relative_path))
    if requested.is_absolute():
        raise ManuscriptWorkingCopyError("manuscript path must be project-relative")
    if not requested.parts or any(part in {"", ".", ".."} for part in requested.parts):
        raise ManuscriptWorkingCopyError("manuscript path contains an invalid segment")

    candidate = project.root / requested
    if candidate.is_symlink():
        raise ManuscriptWorkingCopyError("manuscript symlinks are not supported")
    path = candidate.resolve()
    if not path.is_relative_to(root):
        raise ManuscriptWorkingCopyError("manuscript path escapes the configured manuscript root")
    if not path.is_file():
        raise ManuscriptWorkingCopyError(f"unknown manuscript file: {relative_path}")
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ManuscriptWorkingCopyError("unsupported manuscript file type")
    return path


def _manuscript_root(project: StoryProject) -> Path:
    paths = dict(project.manifest.get("paths") or {})
    configured = Path(str(paths.get("manuscript") or "manuscript"))
    if configured.is_absolute():
        raise ManuscriptWorkingCopyError("project manuscript path must be relative")
    root = (project.root / configured).resolve()
    if not root.is_relative_to(project.root):
        raise ManuscriptWorkingCopyError("project manuscript path escapes the project root")
    if not root.is_dir():
        raise ManuscriptWorkingCopyError("configured manuscript root does not exist")
    return root


def _result_payload(
    project: StoryProject,
    path: Path,
    *,
    previous_sha256: str,
    sha256: str,
    raw: bytes,
    content: str,
    status: str,
) -> dict[str, Any]:
    return {
        "schema": "story.manuscript-save.v1",
        "status": status,
        "project_id": str(project.manifest.get("id") or ""),
        "path": path.relative_to(project.root).as_posix(),
        "previous_sha256": previous_sha256,
        "sha256": sha256,
        "bytes": len(raw),
        "characters": len(content),
        "lines": 0 if not content else content.count("\n") + 1,
        "policy": {
            "manuscript_mutation": True,
            "canonical_mutation": False,
            "staging_mutation": False,
        },
    }


def _best_effort_fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
