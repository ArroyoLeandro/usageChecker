"""Structural gate: exactly one module may reference `sys.platform`.

Per design.md's layering decision, `claude_usage_tray/platform/__init__.py`
is the single seam for OS-specific behavior. Every other module must obtain
platform-dependent behavior by calling into that seam, never by branching on
`sys.platform` itself. This test scans the whole source tree and fails on
the first offending reference outside the seam.
"""

from __future__ import annotations

from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "claude_usage_tray"
ALLOWED_FILE = SOURCE_ROOT / "platform" / "__init__.py"


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_sys_platform_referenced_only_in_seam():
    offenders = {}
    for path in _python_files():
        if path == ALLOWED_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        if "sys.platform" in text:
            offenders[str(path.relative_to(SOURCE_ROOT))] = text.count("sys.platform")

    assert offenders == {}, (
        "sys.platform referenced outside the platform seam "
        f"(claude_usage_tray/platform/__init__.py): {offenders}"
    )


def test_seam_module_itself_references_sys_platform():
    # Sanity check for the scan itself: the allowed file must actually use
    # sys.platform, otherwise the "exactly one" claim above is vacuous.
    text = ALLOWED_FILE.read_text(encoding="utf-8")
    assert "sys.platform" in text
