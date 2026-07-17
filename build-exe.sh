#!/usr/bin/env bash
#
# Build the Windows executable from WSL and copy it back into ./dist/.
#
# PyInstaller does not cross-compile, so the exe must be produced by a Windows
# Python interpreter; this script drives one over WSL interop. Sources are
# staged on a local Windows disk first because PyInstaller writes thousands of
# temporary files, and doing that across the \\wsl.localhost\ bridge is slow
# and unreliable.
#
# The staging directory is reused between runs: rsync's excludes also protect
# the staged .venv from --delete, so dependencies install once.
#
# Usage:  ./build-exe.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE_NAME="ClaudeUsageBuild"
EXE_NAME="ClaudeUsage.exe"

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
step() { printf '\n==> %s\n' "$1"; }

command -v py.exe >/dev/null 2>&1 ||
	die "py.exe is not reachable. This script needs WSL interop enabled and Python installed on Windows."
command -v wslpath >/dev/null 2>&1 ||
	die "wslpath not found. This script only runs under WSL."

# Read %LOCALAPPDATA% from Windows rather than hardcoding a user name, which is
# not knowable from the WSL side.
localappdata_win="$(cmd.exe /c 'echo %LOCALAPPDATA%' 2>/dev/null | tr -d '\r\n')"
[ -n "$localappdata_win" ] || die "could not resolve %LOCALAPPDATA% from Windows."

stage_win="${localappdata_win}\\${STAGE_NAME}"
stage="$(wslpath -u "$stage_win")"

step "Staging sources in ${stage_win}"
mkdir -p "$stage"
rsync -a --delete \
	--exclude '.git/' \
	--exclude '.venv/' \
	--exclude 'build/' \
	--exclude 'dist/' \
	--exclude '__pycache__/' \
	--exclude '.pytest_cache/' \
	--exclude '.engram/' \
	--exclude '.atl/' \
	--exclude 'openspec/' \
	--exclude 'tests/' \
	"$REPO/" "$stage/"

venv_py="${stage}/.venv/Scripts/python.exe"
if [ ! -f "$venv_py" ]; then
	step "Creating the Windows venv"
	(cd "$stage" && py.exe -3 -m venv .venv)
fi

step "Installing dependencies"
(cd "$stage" && "$venv_py" -m pip install --disable-pip-version-check --quiet -r requirements.txt)

step "Running PyInstaller"
(cd "$stage" && "$venv_py" build.py)

built="${stage}/dist/${EXE_NAME}"
[ -f "$built" ] || die "PyInstaller finished but ${EXE_NAME} is not in ${stage_win}\\dist."

step "Copying the executable back"
mkdir -p "$REPO/dist"
cp -f "$built" "$REPO/dist/${EXE_NAME}"

printf '\ndist/%s  (%s)\n' "$EXE_NAME" "$(du -h "$REPO/dist/${EXE_NAME}" | cut -f1)"
printf 'Run it from Windows:  %s\n' "$(wslpath -w "$REPO/dist/${EXE_NAME}")"
