from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


class AtomicWriteError(RuntimeError):
    """Raised when StoryOS cannot safely publish one metadata file."""


def _prepare_parent(project_root: Path, destination: Path) -> Path:
    root = project_root.resolve()
    destination = destination.absolute()
    try:
        relative_parent = destination.parent.relative_to(root)
    except ValueError as exc:
        raise AtomicWriteError(f"metadata destination escapes project root: {destination}") from exc

    cursor = root
    for part in relative_parent.parts:
        cursor = cursor / part
        if cursor.exists():
            if cursor.is_symlink():
                raise AtomicWriteError(f"metadata path cannot contain symlinks: {cursor}")
            if not cursor.is_dir():
                raise AtomicWriteError(f"metadata parent is not a directory: {cursor}")
            continue
        try:
            cursor.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise AtomicWriteError(f"failed to create metadata directory: {exc}") from exc
        if cursor.is_symlink() or not cursor.is_dir():
            raise AtomicWriteError(f"metadata path is not a safe directory: {cursor}")

    try:
        destination.parent.resolve().relative_to(root)
    except ValueError as exc:
        raise AtomicWriteError(f"metadata destination escapes project root: {destination}") from exc
    if destination.exists() and destination.is_symlink():
        raise AtomicWriteError(f"metadata destination cannot be a symlink: {destination}")
    return destination


def _write_temp(destination: Path, text: str) -> Path:
    fd, raw_temp = tempfile.mkstemp(
        prefix=f".{destination.name}.storyos-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        mode = 0o644
        if destination.exists() and not destination.is_symlink():
            try:
                mode = stat.S_IMODE(destination.stat().st_mode)
            except OSError:
                pass
        try:
            os.chmod(temp, mode)
        except OSError:
            pass
        return temp
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_replace_text(project_root: Path, destination: Path, text: str) -> None:
    """Durably replace a project metadata file through a same-directory temp file."""

    destination = _prepare_parent(project_root, destination)
    temp = _write_temp(destination, text)
    try:
        if destination.exists() and destination.is_symlink():
            raise AtomicWriteError(f"metadata destination became a symlink: {destination}")
        os.replace(temp, destination)
        temp = None
        _fsync_directory(destination.parent)
    except OSError as exc:
        raise AtomicWriteError(f"failed to atomically replace metadata: {exc}") from exc
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_create_text(project_root: Path, destination: Path, text: str) -> None:
    """Atomically publish a new metadata file without overwriting an existing target."""

    destination = _prepare_parent(project_root, destination)
    temp = _write_temp(destination, text)
    published = False
    try:
        if destination.exists():
            raise FileExistsError(destination)
        if os.name == "nt":
            # On Windows os.rename is atomic and refuses to overwrite an existing target.
            os.rename(temp, destination)
            published = True
            temp = None
        else:
            # A hard-link publish is atomic and fails if destination already exists.
            os.link(temp, destination)
            published = True
            temp.unlink()
            temp = None
        _fsync_directory(destination.parent)
    except FileExistsError:
        raise
    except OSError as exc:
        raise AtomicWriteError(f"failed to atomically create metadata: {exc}") from exc
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        if not published and destination.exists() and destination.is_symlink():
            raise AtomicWriteError(f"metadata destination became a symlink: {destination}")
