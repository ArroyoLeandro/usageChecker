from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from claude_usage_tray import paths

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
NAME = "ClaudeUsage"
ICON = ROOT / "assets" / paths.ICON_FILENAME
VERSION_INFO = ROOT / "version_info.txt"


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        NAME,
        "--icon",
        str(ICON),
        # Embed a Windows version resource so the app shows as "ClaudeUsage"
        # (its FileDescription) rather than the raw "claudeusage.exe" filename
        # -- in Task Manager, the file's Properties, and the notification
        # toast's app name. Ignored on non-Windows builds. See version_info.txt.
        "--version-file",
        str(VERSION_INFO),
        # PyInstaller's --add-data separator is platform-dependent: ";" on
        # Windows, ":" on POSIX (os.pathsep, never a hardcoded ":"). Without
        # this flag the .ico is never unpacked into the onefile bundle, so
        # paths.app_icon() (which resolves via sys._MEIPASS at runtime)
        # finds nothing and the window/tray icon silently falls back to
        # none. Only the .ico is bundled -- the rest of assets/ (README
        # images) is not needed at runtime.
        "--add-data",
        f"{ICON}{os.pathsep}assets",
        str(ROOT / "launcher.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
