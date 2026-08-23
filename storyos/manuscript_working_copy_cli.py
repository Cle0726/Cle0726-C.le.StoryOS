from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from storyos.manuscript_working_copy import (
    ManuscriptConflictError,
    ManuscriptWorkingCopy,
    ManuscriptWorkingCopyError,
)
from storyos.project import StoryProject


_PAYLOAD_NAME_RE = re.compile(r"^cle-storyos-manuscript-[0-9a-f]{32}\.json$")
_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
_PAYLOAD_SCHEMA = "story.manuscript-write-payload.v1"


def main() -> None:
    parser = argparse.ArgumentParser(prog="storyos-manuscript")
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save", help="Atomically replace one manuscript working copy")
    p_save.add_argument("project")
    p_save.add_argument("payload_name", help="Validated StoryOS payload basename in the OS temp directory")

    args = parser.parse_args()

    if args.command != "save":
        parser.exit(2, "storyos-manuscript: unsupported command\n")

    try:
        payload_path = _payload_path(args.payload_name)
        payload = _consume_payload(payload_path)
        project = StoryProject.open(args.project)
        result = ManuscriptWorkingCopy().save(
            project,
            str(payload.get("path") or ""),
            expected_sha256=str(payload.get("expected_sha256") or ""),
            content=_require_content(payload),
        )
    except ManuscriptConflictError as exc:
        result = {
            "schema": "story.manuscript-save.v1",
            "status": "conflict",
            "expected_sha256": exc.expected_sha256,
            "current_sha256": exc.current_sha256,
            "policy": {
                "manuscript_mutation": False,
                "canonical_mutation": False,
                "staging_mutation": False,
            },
        }
    except (ManuscriptWorkingCopyError, FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"storyos-manuscript: {exc}\n")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def _payload_path(payload_name: str) -> Path:
    name = str(payload_name).strip()
    if not _PAYLOAD_NAME_RE.fullmatch(name):
        raise ManuscriptWorkingCopyError("invalid manuscript payload name")
    return Path(tempfile.gettempdir()) / name


def _consume_payload(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise ManuscriptWorkingCopyError("manuscript payload symlinks are not supported")
    if not path.is_file():
        raise ManuscriptWorkingCopyError("manuscript payload does not exist")
    size = path.stat().st_size
    if size > _MAX_PAYLOAD_BYTES:
        raise ManuscriptWorkingCopyError("manuscript payload exceeds 16 MiB limit")
    try:
        raw = path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema") != _PAYLOAD_SCHEMA:
        raise ManuscriptWorkingCopyError("unsupported manuscript write payload schema")
    return payload


def _require_content(payload: dict[str, object]) -> str:
    content = payload.get("content")
    if not isinstance(content, str):
        raise ManuscriptWorkingCopyError("manuscript payload content must be text")
    return content


if __name__ == "__main__":
    main()
