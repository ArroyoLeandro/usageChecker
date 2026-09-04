"""Structural gate: dependency edges only ever point to a strictly lower
layer.

Per design.md's layering decision: L0 `platform/` ... L5 `app.py`, a total
order on layers with downward-only edges -- which by construction admits no
cycle. This test parses every module with `ast` (no import execution, no
subprocess) and resolves each *relative* import to the absolute dotted
module it names, then checks the resolved target's layer number is strictly
lower than the importing module's layer number.

Imports guarded by `if TYPE_CHECKING:` are deliberately excluded from the
scan: `formatting.py`'s `from .config import ClaudeProfile` under such a
guard exists purely for a type hint and creates no runtime edge -- see
`formatting.py`'s own docstring, which explicitly anticipates this test.
Intra-package sibling edges (e.g. `platform/_windows.py` importing
`platform/_types.py`, or `ui/popup.py` importing `ui/dismiss.py`) are exempt
from the layer-number check entirely: design.md's allow-map governs edges
*between* the named packages/modules in its table, not sub-structure within
one of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "claude_usage_tray"

# design.md's layering table, keyed by the first path component under
# claude_usage_tray/ (a "family": a single module like `config.py`, or an
# entire package like `platform/`/`ui/` treated as one row, matching how the
# table itself is written). `settings`/`alerts` are listed now even though
# their files don't exist yet (slices c/d) -- harmless today, and this test
# will start covering them the moment they're created.
LAYER: dict[str, int] = {
    "platform": 0,
    "paths": 1,
    "jsonstore": 1,
    "formatting": 1,
    "theme": 1,
    "quotas": 1,
    # The provider adapters. L1 because `config` (L2) must ask an adapter
    # where a profile's credentials live in order to build the profile at
    # all -- which is also why this package may not import `requests`, and
    # why `usage_request` returns a description of a call instead of making
    # one. It may not import `quotas` either: both are L1, and same-layer
    # edges are forbidden, so quota windows are plain `str` here.
    "providers": 1,
    "config": 2,
    "icons": 2,
    "settings": 2,
    "alerts": 2,
    # Imports `paths` (L1) and nothing else in-package. Only `app.py` imports
    # it: configuring logging is the entry point's job, and every other module
    # just calls `logging.getLogger(__name__)` with no intra-package edge.
    "logging_setup": 2,
    # Pure ceiling policy plus its store. L2 because three callers need the
    # same answer to "which percentage binds" and two of them (`mcp_server`,
    # `hooks`) are L5 peers forbidden from importing each other -- the same
    # bind that put `quotas.py` at L1.
    "budget": 2,
    "api": 3,
    # Which Claude installation a headless consumer reports on. L3 rather
    # than inside `mcp_server/` for the reason above; it cannot go lower
    # because it depends on `config` (L2).
    "accounts": 3,
    "ui": 4,
    "app": 5,
    # Second entry point, peer to `app.py`: it composes `config` (L2) and
    # `api` (L3) into an MCP server. Listed at 5 rather than 4 so this gate
    # forbids an edge to `app` in *either* direction -- the tray must not
    # grow a dependency on a background server, and the server must not need
    # the tray's runtime to answer a question.
    "mcp_server": 5,
    # The enforcing half, peer to both. It may not import `mcp_server`, which
    # is what forced `accounts` and `budget` down to shared layers.
    "hooks": 5,
}


def _module_dotted_name(path: Path) -> tuple[str, bool]:
    """Return (dotted module name, is_package_init).

    A package's `__init__.py` gets the package's own dotted name (Python
    special-cases this: for a package, `__name__ == __package__`), unlike a
    plain module, whose `__package__` is its *parent*.
    """
    relative = path.relative_to(SOURCE_ROOT.parent).with_suffix("")
    parts = list(relative.parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    return ".".join(parts), is_init


def _family(dotted: str) -> str | None:
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != "claude_usage_tray":
        return None
    return parts[1]


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _base_package(package: str, level: int) -> str:
    """Resolve the package a relative import is anchored to, per `level`
    dots (`from . import x` is level 1 == the module's own `__package__`;
    each extra dot strips one more trailing component)."""
    if level <= 1:
        return package
    parts = package.split(".")
    parts = parts[: len(parts) - (level - 1)]
    return ".".join(parts)


def _collect_runtime_relative_imports(tree: ast.Module, module_dotted: str, is_init: bool) -> set[str]:
    package = module_dotted if is_init else module_dotted.rsplit(".", 1)[0]
    targets: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            return  # prune: type-only imports create no runtime edge
        if isinstance(node, ast.ImportFrom) and node.level >= 1:
            base = _base_package(package, node.level)
            if node.module:
                targets.add(f"{base}.{node.module}" if base else node.module)
            else:
                for alias in node.names:
                    targets.add(f"{base}.{alias.name}" if base else alias.name)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return targets


def test_dependency_edges_point_only_to_a_strictly_lower_layer():
    violations: list[str] = []

    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module_dotted, is_init = _module_dotted_name(path)
        source_family = _family(module_dotted)
        if source_family is None or source_family not in LAYER:
            continue  # entry-point glue (e.g. __main__), not layer-tracked

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target in _collect_runtime_relative_imports(tree, module_dotted, is_init):
            target_family = _family(target)
            if target_family is None or target_family not in LAYER:
                continue
            if target_family == source_family:
                continue  # intra-package sibling edge, not a cross-layer edge
            if LAYER[target_family] >= LAYER[source_family]:
                violations.append(
                    f"{module_dotted} (layer {LAYER[source_family]}, {source_family!r}) imports "
                    f"{target} (layer {LAYER[target_family]}, {target_family!r}) -- not strictly lower"
                )

    assert violations == [], "Upward or same-or-higher-layer dependency edge(s) found:\n" + "\n".join(violations)
