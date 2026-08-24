from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ProjectFileLockError(RuntimeError):
    """Raised when a StoryOS cross-process file lock cannot be acquired safely."""


def _ensure_safe_directory(project_root: Path, path: Path) -> None:
    if path.exists():
        if path.is_symlink():
            raise ProjectFileLockError(f"lock path cannot be a symlink: {path}")
        if not path.is_dir():
            raise ProjectFileLockError(f"lock path is not a directory: {path}")
    else:
        try:
            path.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise ProjectFileLockError(f"failed to create StoryOS lock directory: {exc}") from exc
        if path.is_symlink() or not path.is_dir():
            raise ProjectFileLockError(f"lock path is not a safe directory: {path}")

    try:
        path.resolve().relative_to(project_root)
    except ValueError as exc:
        raise ProjectFileLockError(f"lock path escapes project root: {path}") from exc


def _lock_path(project_root: Path, namespace: str, resource: str | None) -> Path:
    if not namespace or any(ch in namespace for ch in "/\\\0\r\n"):
        raise ProjectFileLockError("invalid lock namespace")

    project_root = project_root.resolve()
    key = "project" if resource is None else hashlib.sha256(resource.encode("utf-8")).hexdigest()
    storyos_dir = project_root / ".storyos"
    locks_dir = storyos_dir / "locks"
    namespace_dir = locks_dir / namespace

    # Create/check each component individually so a pre-existing .storyos or locks
    # directory symlink cannot redirect StoryOS lock ownership outside the project.
    for directory in (storyos_dir, locks_dir, namespace_dir):
        _ensure_safe_directory(project_root, directory)

    return namespace_dir / f"{key}.lock"


def _acquire(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        # LK_LOCK blocks until the single lock byte becomes available. The OS releases
        # the lock automatically if the owning process exits or crashes.
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def project_file_lock(
    project_root: Path,
    namespace: str,
    *,
    resource: str | None = None,
) -> Iterator[None]:
    """Serialize StoryOS mutations across processes using an OS-managed file lock.

    The lock file is intentionally persistent; ownership is held by the operating system,
    so a crashed process cannot leave a permanently-owned stale lock behind. Only failures
    caused by the lock primitive itself are translated to ``ProjectFileLockError``;
    exceptions raised by code inside the protected section are never rewritten.
    """

    path = _lock_path(project_root, namespace, resource)
    if path.exists() and path.is_symlink():
        raise ProjectFileLockError("lock file cannot be a symlink")

    try:
        handle = path.open("a+b")
    except OSError as exc:
        raise ProjectFileLockError(f"failed to open StoryOS project lock: {exc}") from exc

    with handle:
        try:
            _acquire(handle)
        except OSError as exc:
            raise ProjectFileLockError(f"failed to acquire StoryOS project lock: {exc}") from exc

        try:
            yield
        finally:
            try:
                _release(handle)
            except OSError as exc:
                raise ProjectFileLockError(f"failed to release StoryOS project lock: {exc}") from exc
