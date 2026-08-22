"""Enforces intra-package dependencies.

Adding a package to `src/` fails this test until it is given a rank. Choosing where a
new package sits is a design decision, and it should be made on purpose with planning.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from functools import cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

# Bottom to top. A package may import any package ranked strictly below it, and
# never one at its own rank or above.
LAYERS: dict[str, int] = {
    "domain": 0,  # the shared vocabulary: imports nothing of ours
    "term": 1,  # talking to the terminal: printing, prompts, progress
    "config": 2,  # the user's settings
    "models": 3,  # broker payload shapes
    "app": 4,  # process-wide context and logging
    "db": 5,  # persistence
    "services": 6,  # outside world: brokers, quotes, FX
    "engine": 7,  # the cost-base replay and everything computed from it
    "ingest": 8,  # source rows to transactions
    "importers": 9,  # reading workbooks
    "exporters": 10,  # writing workbooks
    "datagen": 11,  # demo and mock folios
    "ui": 12,  # rendering reports
    "cli": 13,  # argument parsing and wiring
}


@cache
def _packages() -> frozenset[str]:
    return frozenset(
        p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    )


@cache
def _tree(path: Path) -> ast.Module:
    """Parse `path` once, however many tests walk it."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> set[str]:
    """Every first-party top-level package `path` imports."""
    packages = _packages()
    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
    return found & packages


def _module_graph() -> dict[str, set[str]]:
    """Import edges between individual first-party modules."""
    packages = _packages()
    graph: dict[str, set[str]] = defaultdict(set)
    for path in SRC.rglob("*.py"):
        name = ".".join(path.relative_to(SRC).with_suffix("").parts)
        name = name.removesuffix(".__init__")
        for node in ast.walk(_tree(path)):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            for target in targets:
                if target.split(".")[0] in packages and target != name:
                    graph[name].add(target)
    return graph


def test_every_package_has_a_layer() -> None:
    """A new package under `src/` must be placed in the order deliberately."""
    assert _packages() == set(LAYERS), (
        "Package added or removed without updating LAYERS. Decide where it sits: "
        "it may only import packages ranked below it."
    )


@pytest.mark.parametrize("package", sorted(LAYERS))
def test_a_package_only_imports_layers_below_it(package: str) -> None:
    """No module may import a package at or above its own layer."""
    rank = LAYERS[package]
    violations = [
        f"{path.relative_to(SRC)} imports {imported} "
        f"({package}={rank} may not import {imported}={LAYERS[imported]})"
        for path in (SRC / package).rglob("*.py")
        for imported in sorted(_imports(path))
        if imported != package and LAYERS[imported] >= rank
    ]
    assert not violations, "Upward import:\n  " + "\n  ".join(violations)


def test_no_module_imports_form_a_cycle() -> None:
    """Catch cycles between modules, including inside a single package."""
    graph = _module_graph()
    cycles: list[str] = []

    def walk(node: str, stack: list[str], seen: set[str]) -> None:
        for nxt in sorted(graph.get(node, ())):
            if nxt in stack:  # pragma: no cover
                cycles.append(" -> ".join([*stack[stack.index(nxt) :], nxt]))
            elif nxt not in seen:
                seen.add(nxt)
                walk(nxt, [*stack, nxt], seen)

    for node in sorted(graph):
        walk(node, [node], {node})

    assert not cycles, "Import cycle:\n  " + "\n  ".join(sorted(set(cycles)))
