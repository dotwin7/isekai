from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/isekai"
STABLE_FACADES = {
    "cli/__init__.py",
    "distribution/__init__.py",
    "foundation/__init__.py",
    "intake.py",
    "jsonio.py",
    "locking.py",
    "runtime_contract.py",
    "project.py",
    "routing.py",
    "session.py",
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
