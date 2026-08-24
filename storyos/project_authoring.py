from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from storyos.atomic_io import AtomicWriteError, atomic_create_text
from storyos.file_lock import ProjectFileLockError, project_file_lock
from storyos.project import StoryProject


class ProjectAuthoringError(RuntimeError):
    """Raised when product-level project authoring cannot be completed safely."""


_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _plain_text(value: str, label: str, *, max_length: int) -> str:
    text = str(value).strip()
    if not text:
        raise ProjectAuthoringError(f"{label}不能为空")
    if len(text) > max_length:
        raise ProjectAuthoringError(f"{label}过长")
    if any(char in text for char in ("\0", "\r", "\n")):
        raise ProjectAuthoringError(f"{label}包含非法控制字符")
    return text


def _safe_filename_title(value: str) -> str:
    title = _plain_text(value, "章节标题", max_length=120)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    safe = re.sub(r"\s+", "_", safe).strip(" ._")
    if not safe:
        safe = "Untitled"
    if safe.upper() in _RESERVED_WINDOWS_NAMES:
        safe = f"_{safe}"
    return safe[:80].rstrip(" ._") or "Untitled"


def _ensure_existing_safe_root(root: str | Path) -> Path:
    raw = Path(root).expanduser()
    if raw.is_symlink():
        raise ProjectAuthoringError("项目目录不能是符号链接")
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ProjectAuthoringError("请选择一个已经存在的文件夹") from exc
    if not resolved.is_dir():
        raise ProjectAuthoringError("项目路径必须是文件夹")
    return resolved


def _ensure_directory(root: Path, relative: str) -> None:
    path = root / relative
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ProjectAuthoringError(f"StoryOS 目录位置被其他文件占用：{relative}")
        return
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ProjectAuthoringError(f"无法创建 StoryOS 目录 {relative}：{exc}") from exc
    if path.is_symlink() or not path.is_dir():
        raise ProjectAuthoringError(f"StoryOS 目录创建结果不安全：{relative}")


def create_project(
    root: str | Path,
    *,
    name: str,
    language: str = "zh-CN",
) -> dict[str, Any]:
    """Initialize StoryOS metadata inside an existing directory without overwriting files."""

    project_root = _ensure_existing_safe_root(root)
    project_name = _plain_text(name, "项目名称", max_length=120)
    project_language = _plain_text(language or "zh-CN", "项目语言", max_length=32)
    manifest_path = project_root / "storyos.yaml"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ProjectAuthoringError("这个文件夹已经包含 storyos.yaml；请直接打开现有项目")

    manifest = {
        "schema": "story.project.v1",
        "id": f"storyos_{uuid.uuid4().hex}",
        "name": project_name,
        "language": project_language,
        "paths": {"manuscript": "manuscript"},
    }
    text = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120)

    try:
        with project_file_lock(project_root, "project-create"):
            if manifest_path.exists() or manifest_path.is_symlink():
                raise ProjectAuthoringError(
                    "项目初始化期间检测到 storyos.yaml 已出现；没有覆盖它"
                )
            atomic_create_text(project_root, manifest_path, text)
            for relative in (
                "manuscript",
                "entities",
                "events",
                "canon",
                "staging/claims",
                "staging/reviews",
                "staging/materialization/events",
                "staging/materialization/facts",
            ):
                _ensure_directory(project_root, relative)
    except (AtomicWriteError, ProjectFileLockError) as exc:
        raise ProjectAuthoringError(str(exc)) from exc

    project = StoryProject.open(project_root)
    return {
        "schema": "story.project-create.v1",
        "project": {
            "id": str(project.manifest.get("id") or ""),
            "name": str(project.manifest.get("name") or ""),
            "language": str(project.manifest.get("language") or ""),
            "path": str(project.root),
        },
        "policy": {
            "project_mutation": True,
            "manuscript_mutation": False,
            "canonical_mutation": False,
            "staging_mutation": False,
            "create_only": True,
        },
    }


def create_manuscript(
    project: StoryProject,
    *,
    title: str,
    season: int,
    episode: int,
) -> dict[str, Any]:
    """Create one empty manuscript working copy without overwriting an existing episode."""

    if isinstance(season, bool) or not isinstance(season, int) or season < 1 or season > 9999:
        raise ProjectAuthoringError("季号必须是 1 到 9999 的整数")
    if isinstance(episode, bool) or not isinstance(episode, int) or episode < 1 or episode > 9999:
        raise ProjectAuthoringError("集号必须是 1 到 9999 的整数")

    title_text = _plain_text(title, "章节标题", max_length=120)
    filename_title = _safe_filename_title(title_text)
    paths = dict(project.manifest.get("paths") or {})
    configured = Path(str(paths.get("manuscript") or "manuscript"))
    if configured.is_absolute():
        raise ProjectAuthoringError("项目正文目录必须是项目内相对路径")
    manuscript_root = (project.root / configured).resolve()
    if not manuscript_root.is_relative_to(project.root):
        raise ProjectAuthoringError("项目正文目录越出了项目根目录")

    relative = configured / f"S{season:02d}" / f"EP{episode:02d}_{filename_title}.txt"
    destination = project.root / relative

    try:
        with project_file_lock(
            project.root,
            "manuscript-create",
            resource=f"S{season:04d}-EP{episode:04d}",
        ):
            season_dir = manuscript_root / f"S{season:02d}"
            if season_dir.exists():
                if season_dir.is_symlink() or not season_dir.is_dir():
                    raise ProjectAuthoringError("季目录不是安全文件夹")
                episode_pattern = re.compile(
                    rf"^EP{episode:02d}(?:[_\-\s]|$)",
                    flags=re.IGNORECASE,
                )
                for current in season_dir.iterdir():
                    if current.is_symlink():
                        raise ProjectAuthoringError("正文目录不能包含符号链接")
                    if current.is_file() and episode_pattern.match(current.stem):
                        raise ProjectAuthoringError(
                            f"S{season:02d} · EP{episode:02d} 已经存在；请打开现有章节"
                        )
            try:
                atomic_create_text(project.root, destination, "")
            except FileExistsError as exc:
                raise ProjectAuthoringError("目标章节文件已经存在；没有覆盖它") from exc
    except (AtomicWriteError, ProjectFileLockError) as exc:
        raise ProjectAuthoringError(str(exc)) from exc

    return {
        "schema": "story.manuscript-create.v1",
        "path": relative.as_posix(),
        "title": title_text,
        "season": season,
        "episode": episode,
        "policy": {
            "project_mutation": False,
            "manuscript_mutation": True,
            "history_mutation": False,
            "recovery_mutation": False,
            "canonical_mutation": False,
            "staging_mutation": False,
            "create_only": True,
        },
    }
