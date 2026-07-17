"""Structural gate: pure modules stay UI/network-free, transitively.

Spawns a fresh interpreter per module (not a mock, not a mere direct import
in-process — a real subprocess), imports the module, and inspects
`sys.modules` afterward for `tkinter`, `pystray`, `PIL`, or `requests`. A
direct import check would miss a module that imports something which
imports tkinter three levels down; a fresh subprocess catches that.

This is v5 (Phase 9): the platform seam, paths/jsonstore, formatting/theme,
icons (PIL-allowlisted -- it is the one module besides `ui/` permitted to
pull in a normally forbidden package, since PIL is genuinely
headless-testable), `config` (stdlib + paths + jsonstore only), and now
`ui/*`. `ui/dismiss.py` and the `ui` package's own `__init__.py` are fully
clean (tkinter is only imported under `TYPE_CHECKING` in `dismiss.py`, and
`ui/__init__.py` defers `popup`/`profiles_window` via `__getattr__` so
merely importing the package never touches tkinter). `ui/popup.py`,
`ui/profiles_window.py`, and `ui/widgets.py` import real tkinter at module
scope and are gated the same way `icons.py` is for PIL: exempt for exactly
one forbidden package (`tkinter`), never `pystray`/`PIL`/`requests`.
"""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest

FORBIDDEN_PACKAGES = {"tkinter", "pystray", "PIL", "requests"}

# Modules verified importable, on every platform, without pulling in any
# forbidden package -- even transitively.
CLEAN_MODULES = [
    "claude_usage_tray.platform",
    "claude_usage_tray.platform._darwin",
    "claude_usage_tray.platform._posix",
    "claude_usage_tray.paths",
    "claude_usage_tray.jsonstore",
    "claude_usage_tray.formatting",
    "claude_usage_tray.theme",
    # The three quota windows and their Spanish names. `settings` and
    # `alerts` are both L2 and may not import each other, so the windows
    # they both name had to fall to a layer they can both reach.
    "claude_usage_tray.quotas",
    "claude_usage_tray.config",
    "claude_usage_tray.settings",
    "claude_usage_tray.alerts",
    "claude_usage_tray.ui",
    "claude_usage_tray.ui.dismiss",
    # Slice (e). The hover tracker takes its OS access as injected callables
    # and its `Rect` only under TYPE_CHECKING, so the module that decides when
    # a tooltip appears is testable with no tray, no cursor and no display.
    "claude_usage_tray.ui.hover",
    # The viewport arithmetic behind `widgets.ScrollableView`. It takes its
    # `WorkArea` as an argument and only under TYPE_CHECKING, so the module
    # that decides how tall a window may be -- and therefore whether the
    # settings window's "Cerrar" is on the desktop at all -- is decidable with
    # no display to measure.
    "claude_usage_tray.ui.scroll",
    # The colour rules behind `settings_window`'s colour section. It reasons
    # about a `Palette` and a sparse override map and touches no widget, so
    # the rule that decides whether showing a colour counts as choosing it --
    # and therefore whether switching preset still works -- is testable with
    # no colour picker to open.
    "claude_usage_tray.ui.colors",
]

# Modules allowed exactly one forbidden import: PIL. `icons.py` renders
# with Pillow, which -- unlike tkinter/pystray/requests -- is actually
# installed in this headless dev/CI environment, so its import hygiene is
# still worth gating: tkinter/pystray/requests must never sneak in here,
# even transitively through PIL.
PIL_ALLOWLISTED_MODULES = [
    "claude_usage_tray.icons",
]

# Modules allowed exactly one forbidden import: tkinter. These build real
# widget trees and import tkinter at module scope -- unlike PIL/requests,
# tkinter is genuinely not installed in this dev/CI environment, so on this
# box the import itself fails (see the test below); on a box that does have
# tkinter (e.g. Windows), the same test asserts the forbidden set is
# exactly {"tkinter"}, never pystray/PIL/requests.
TKINTER_ALLOWLISTED_MODULES = [
    "claude_usage_tray.ui.popup",
    "claude_usage_tray.ui.main_window",
    "claude_usage_tray.ui.profiles_window",
    "claude_usage_tray.ui.settings_window",
    "claude_usage_tray.ui.hover_popup",
    "claude_usage_tray.ui.widgets",
]

# Backends gated behind a Windows-only stdlib module (winreg). Off-Windows,
# a direct import of these must fail *only* because that stdlib module is
# missing -- never because a forbidden GUI/network package was imported
# first. (In production this module is never imported directly off-Windows;
# `platform/__init__.py` only selects it when `IS_WINDOWS` is true.)
WINDOWS_ONLY_MODULES = [
    "claude_usage_tray.platform._windows",
]


def _probe(module_name: str) -> subprocess.CompletedProcess:
    probe_source = (
        "import sys, importlib\n"
        f"importlib.import_module({module_name!r})\n"
        "forbidden = {'tkinter', 'pystray', 'PIL', 'requests'} & "
        "{m.split('.')[0] for m in sys.modules}\n"
        "print(sorted(forbidden))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("module_name", CLEAN_MODULES)
def test_clean_module_imports_without_forbidden_packages(module_name):
    result = _probe(module_name)
    assert result.returncode == 0, f"{module_name} failed to import cleanly:\n{result.stderr}"
    assert result.stdout.strip() == "[]", f"{module_name} pulled in forbidden packages: {result.stdout.strip()}"


@pytest.mark.parametrize("module_name", PIL_ALLOWLISTED_MODULES)
def test_pil_allowlisted_module_imports_without_other_forbidden_packages(module_name):
    result = _probe(module_name)
    assert result.returncode == 0, f"{module_name} failed to import cleanly:\n{result.stderr}"
    forbidden = set(ast.literal_eval(result.stdout.strip()))
    assert forbidden == {"PIL"}, (
        f"{module_name} must import exactly PIL among forbidden packages, got: {sorted(forbidden)}"
    )


def _tkinter_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


@pytest.mark.parametrize("module_name", TKINTER_ALLOWLISTED_MODULES)
def test_tkinter_allowlisted_module_imports_or_fails_only_on_missing_tkinter(module_name):
    result = _probe(module_name)
    if _tkinter_available():
        assert result.returncode == 0, f"{module_name} failed to import cleanly:\n{result.stderr}"
        forbidden = set(ast.literal_eval(result.stdout.strip()))
        assert forbidden == {"tkinter"}, (
            f"{module_name} must import exactly tkinter among forbidden packages, got: {sorted(forbidden)}"
        )
    else:
        assert result.returncode != 0, f"{module_name} was expected to fail without tkinter installed"
        assert "tkinter" in result.stderr, f"{module_name} failed for an unexpected reason:\n{result.stderr}"
        for forbidden in FORBIDDEN_PACKAGES - {"tkinter"}:
            assert forbidden not in result.stderr, (
                f"{module_name} touched forbidden package {forbidden!r} before failing:\n{result.stderr}"
            )


@pytest.mark.parametrize("module_name", WINDOWS_ONLY_MODULES)
def test_windows_only_module_fails_only_on_missing_stdlib_off_windows(module_name):
    if sys.platform == "win32":
        pytest.skip("winreg is available on Windows; nothing to characterize here")

    result = _probe(module_name)
    assert result.returncode != 0, f"{module_name} was expected to fail off-Windows (needs winreg)"
    assert "winreg" in result.stderr, f"{module_name} failed for an unexpected reason:\n{result.stderr}"
    for forbidden in FORBIDDEN_PACKAGES:
        assert forbidden not in result.stderr, (
            f"{module_name} touched forbidden package {forbidden!r} before failing:\n{result.stderr}"
        )
