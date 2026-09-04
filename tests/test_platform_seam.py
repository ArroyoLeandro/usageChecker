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


# --- backend parity ---------------------------------------------------------
#
# The seam's whole value is that `platform.foo()` answers on every OS. A
# function added to `__init__.py` and to only one backend does not fail on
# the developer's machine -- it fails on somebody else's, at runtime, with an
# AttributeError. This gate catches that at test time on any machine.
#
# Parsed with `ast` rather than imported, for the same reason the scan above
# is: `_windows.py` pulls in `ctypes.wintypes`, which does not exist off
# Windows, so importing all three backends is impossible from any one host.

BACKENDS = ("_windows.py", "_darwin.py", "_posix.py")


def _public_functions(path):
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }


def _facade_delegated_names():
    """Every `_backend.<name>` the facade actually calls."""
    import ast

    # Parsed, not grepped: `_backend.foo` also appears inside the facade's
    # docstrings, and a text scan would count those as delegations.
    tree = ast.parse(ALLOWED_FILE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "_backend"
        ):
            names.add(node.attr)
    assert names, "the facade delegates to nothing; this gate would be vacuous"
    return names


def test_every_backend_implements_the_whole_seam():
    required = _facade_delegated_names()
    missing = {}
    for name in BACKENDS:
        implemented = _public_functions(SOURCE_ROOT / "platform" / name)
        gap = sorted(required - implemented)
        if gap:
            missing[name] = gap

    assert missing == {}, (
        "these platform backends do not implement every function the seam "
        f"delegates to, so the call would AttributeError there: {missing}"
    )


def test_backends_expose_no_extra_public_functions():
    # The other direction: a public function on one backend that the facade
    # never calls is either dead or a caller reaching past the seam.
    allowed = _facade_delegated_names()
    extras = {}
    for name in BACKENDS:
        gap = sorted(_public_functions(SOURCE_ROOT / "platform" / name) - allowed)
        if gap:
            extras[name] = gap

    assert extras == {}, (
        "public functions on a platform backend that the seam does not "
        f"delegate to (dead code, or a caller bypassing the facade): {extras}"
    )
