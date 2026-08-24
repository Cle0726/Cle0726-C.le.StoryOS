from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TAURI_BINARIES = REPO_ROOT / "desktop" / "src-tauri" / "binaries"


def _target_triple() -> str:
    result = subprocess.run(
        ["rustc", "-vV"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            value = line.removeprefix("host: ").strip()
            if value:
                return value
    raise RuntimeError("rustc -vV did not report a host target triple")


def _pyinstaller_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "PyInstaller is required to build the desktop sidecar; "
            "install the repository desktop extras first: pip install -e '.[desktop]'"
        )


def main() -> None:
    _pyinstaller_available()
    target = _target_triple()
    extension = ".exe" if os.name == "nt" else ""
    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)
    destination = TAURI_BINARIES / f"storyos-workspace-{target}{extension}"

    with tempfile.TemporaryDirectory(prefix="storyos-sidecar-") as raw_temp:
        temp = Path(raw_temp)
        entry = temp / "storyos_workspace_entry.py"
        entry.write_text(
            "from storyos.workspace_cli import main\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )
        dist = temp / "dist"
        work = temp / "work"
        spec = temp / "spec"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onefile",
                "--name",
                "storyos-workspace",
                "--paths",
                str(REPO_ROOT),
                "--distpath",
                str(dist),
                "--workpath",
                str(work),
                "--specpath",
                str(spec),
                str(entry),
            ],
            cwd=REPO_ROOT,
            check=True,
        )
        built = dist / f"storyos-workspace{extension}"
        if not built.is_file():
            raise RuntimeError(f"PyInstaller did not produce the expected sidecar: {built}")
        shutil.copy2(built, destination)

    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | 0o111)
    print(destination.relative_to(REPO_ROOT).as_posix())


if __name__ == "__main__":
    main()
