"""Package the tray app: a `.exe` on Windows, a `.app` bundle on macOS.

One script for both targets because almost everything is shared -- the entry
point, the name, the bundled assets -- and the parts that are not are small
and worth stating side by side rather than hiding in a second file that
drifts. `sys.platform` is read here rather than through
`claude_usage_tray.platform`: this is a build tool that runs *before* the app
exists, and it decides how to package, not how the app behaves at runtime.
The seam's one-place rule is about the shipped package, and
`tests/test_platform_seam.py` scans exactly that.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from claude_usage_tray import paths

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
NAME = "ClaudeUsage"
ICON_ICO = ROOT / "assets" / paths.ICON_FILENAME
ICON_PNG = ROOT / "assets" / "claude_usage_icon.png"
VERSION_INFO = ROOT / "version_info.txt"

IS_WINDOWS = sys.platform == "win32"
IS_DARWIN = sys.platform == "darwin"

#: Reverse-DNS bundle id. macOS keys per-app state off this -- notification
#: permissions, the Login Items entry, window restoration -- so it must stay
#: stable across releases or the user silently loses those grants.
BUNDLE_ID = "com.anthropic.claudeusage"

#: The `.iconset` members we can produce from a 256x256 source *without*
#: upscaling: (filename stem, pixel size). macOS picks the nearest available
#: size, so omitting 512/1024 costs a little sharpness in a large Finder icon
#: and nothing at all in the places this app is actually seen. Inventing those
#: sizes by enlarging a 256 would just ship a blurrier icon.
ICONSET_MEMBERS = (
    ("icon_16x16", 16),
    ("icon_16x16@2x", 32),
    ("icon_32x32", 32),
    ("icon_32x32@2x", 64),
    ("icon_128x128", 128),
    ("icon_128x128@2x", 256),
    ("icon_256x256", 256),
)


def build_icns(destination: Path) -> Path:
    """Render `assets/claude_usage_icon.png` into a macOS `.icns`.

    PyInstaller's `--icon` will not take a `.png` or a `.ico` for a `.app`
    bundle, and the repo ships neither an `.icns` nor the 1024px master an
    ideal one would come from -- so it is generated at build time from the
    256px PNG that is versioned, instead of committing a binary blob that
    could drift from it.

    Uses `iconutil`, which is part of the base macOS install (no Xcode, no
    Homebrew): the toolchain assumption stays "a Mac", same as the rest of
    this branch.
    """
    iconset = destination / f"{NAME}.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)

    with Image.open(ICON_PNG) as source:
        master = source.convert("RGBA")
        for stem, size in ICONSET_MEMBERS:
            master.resize((size, size), Image.LANCZOS).save(iconset / f"{stem}.png")

    icns = destination / f"{NAME}.icns"
    subprocess.run(
        ["iconutil", "--convert", "icns", "--output", str(icns), str(iconset)],
        check=True,
    )
    shutil.rmtree(iconset, ignore_errors=True)
    return icns


def mark_as_menu_bar_app(app_bundle: Path) -> None:
    """Set `LSUIElement` so the app runs with no Dock tile and no menu bar of
    its own.

    Without it a menu-bar utility still claims a Dock icon and an app menu,
    which for something whose whole UI is a status item reads as a bug. It is
    an `Info.plist` key with no PyInstaller command-line flag, and reaching it
    otherwise would mean maintaining a `.spec` file for this one entry -- so
    the plist is edited in place, after the build, with the stdlib.
    """
    plist_path = app_bundle / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    plist["LSUIElement"] = True
    # Named for the user, not for the filesystem: this is what the Keychain
    # permission prompt and the notification banner call the app. Assigned,
    # not `setdefault` -- PyInstaller has already written the bundle name
    # here, so a default would never win.
    plist["CFBundleDisplayName"] = "Claude Usage"
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)

    # Re-sign: PyInstaller signs the bundle as its last step, so the edit
    # above breaks the seal ("invalid Info.plist (plist or signature have
    # been modified)"). That is not cosmetic on Apple Silicon, where every
    # executable must carry a valid signature or the kernel refuses to run
    # it -- the app would die on launch with nothing but a crash report.
    # Ad-hoc (`-`) matches what PyInstaller itself applied; a real Developer
    # ID would go here instead if this were ever distributed.
    subprocess.run(
        ["codesign", "--force", "--sign", "-", "--deep", str(app_bundle)],
        check=True,
        capture_output=True,
    )
    verify = subprocess.run(
        ["codesign", "--verify", "--strict", str(app_bundle)],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        raise SystemExit(f"The rebuilt bundle failed signature verification:\n{verify.stderr}")


def main() -> None:
    if not (IS_WINDOWS or IS_DARWIN):
        raise SystemExit(f"No packaging target defined for {sys.platform!r}.")

    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        NAME,
    ]

    if IS_WINDOWS:
        # One self-contained file to hand someone: the Windows story has
        # always been "download the exe".
        command += ["--onefile", "--icon", str(ICON_ICO)]
        # Embed a Windows version resource so the app shows as "ClaudeUsage"
        # (its FileDescription) rather than the raw "claudeusage.exe" filename
        # -- in Task Manager, the file's Properties, and the notification
        # toast's app name. Windows-only: PyInstaller rejects the flag on
        # other targets. See version_info.txt.
        command += ["--version-file", str(VERSION_INFO)]
    else:
        # `--onedir`, PyInstaller's default, rather than the Windows
        # `--onefile`: a onefile bundle unpacks its whole payload to a temp
        # directory on *every* launch, which for a login-time menu-bar app is
        # a delay paid daily for a convenience (a single file) that a `.app`
        # -- already a directory the user sees as one icon -- does not need.
        command += ["--icon", str(build_icns(BUILD))]
        command += ["--osx-bundle-identifier", BUNDLE_ID]

    command += [
        # PyInstaller's --add-data separator is platform-dependent: ";" on
        # Windows, ":" on POSIX (os.pathsep, never a hardcoded ":"). Without
        # this flag the icon is never unpacked into the bundle, so
        # paths.app_icon() (which resolves via sys._MEIPASS at runtime)
        # finds nothing and the window/tray icon silently falls back to
        # none. Only the .ico is bundled -- the rest of assets/ (README
        # images) is not needed at runtime.
        "--add-data",
        f"{ICON_ICO}{os.pathsep}assets",
        str(ROOT / "launcher.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)

    if IS_DARWIN:
        app_bundle = DIST / f"{NAME}.app"
        mark_as_menu_bar_app(app_bundle)
        print(f"\nListo: {app_bundle}")
        print("Arrastralo a /Applications y abrilo desde ahi.")


if __name__ == "__main__":
    main()
