from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/isekai"
STABLE_FACADES = {
    "cli/__init__.py",
    "distribution/__init__.py",
    "foundation/__init__.py",
    "runtime_contract.py",
    "workflow/__init__.py",
}


def _sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["isekai", *parts])


def _relative_import_target(path: Path, node: ast.ImportFrom) -> str:
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    parts = package.split(".")
    if node.level > 1:
        parts = parts[: -(node.level - 1)]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def test_domain_modules_stay_below_the_reviewability_budget() -> None:
    oversized = {
        str(path.relative_to(PACKAGE)): len(path.read_text(encoding="utf-8").splitlines())
        for path in _sources()
        if len(path.read_text(encoding="utf-8").splitlines()) > 750
    }
    assert oversized == {}


def test_public_compatibility_facades_stay_thin() -> None:
    oversized = {
        name: len((PACKAGE / name).read_text(encoding="utf-8").splitlines())
        for name in STABLE_FACADES
        if len((PACKAGE / name).read_text(encoding="utf-8").splitlines()) > 250
    }
    assert oversized == {}


def test_no_new_module_level_import_cycles_are_introduced() -> None:
    sources = _sources()
    modules = {_module_name(path) for path in sources}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for path in sources:
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            dependencies: set[str] = set()
            if isinstance(node, ast.ImportFrom):
                target = (
                    _relative_import_target(path, node)
                    if node.level
                    else (node.module or "")
                )
                dependencies.add(target)
                dependencies.update(f"{target}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            graph[module].update(dependency for dependency in dependencies if dependency in modules)

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            cycle = visiting[visiting.index(module) :] + [module]
            raise AssertionError("module-level import cycle: " + " -> ".join(cycle))
        if module in visited:
            return
        visiting.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        visiting.pop()
        visited.add(module)

    for module in sorted(modules):
        visit(module)


def test_every_source_module_imports_in_a_fresh_interpreter() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    failures = []
    for path in _sources():
        module = _module_name(path)
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            failures.append(f"{module}: {completed.stderr.strip()}")
    assert failures == []


def test_ci_uses_immutable_actions_and_explicit_runner_families() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    uses = re.findall(r"^\s*uses:\s+[^@\s]+@([^\s#]+)", workflow, re.MULTILINE)

    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in uses)
    assert "-latest" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert workflow.count("persist-credentials: false") == workflow.count(
        "actions/checkout@"
    )
    assert "pip install --upgrade" not in workflow
    assert "python -m pip install" not in workflow
    assert "uv sync --frozen --extra test" in workflow
    assert "bubblewrap=0.9.0-1ubuntu0.1" in workflow
    assert "@anthropic-ai/claude-code-linux-x64@2.1.224" in workflow
    assert "npm install --global" not in workflow
