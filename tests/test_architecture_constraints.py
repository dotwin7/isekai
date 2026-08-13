from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src/isekai"


def _source_modules() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_production_modules_stay_below_700_lines() -> None:
    oversized = {
        path.relative_to(SOURCE_ROOT).as_posix(): len(path.read_text().splitlines())
        for path in _source_modules()
        if len(path.read_text().splitlines()) >= 700
    }
    assert oversized == {}


def test_production_functions_stay_below_180_lines() -> None:
    oversized: list[str] = []
    for path in _source_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                size = node.end_lineno - node.lineno + 1
                if size >= 180:
                    relative = path.relative_to(SOURCE_ROOT).as_posix()
                    oversized.append(f"{relative}:{node.lineno} {node.name} ({size})")
    assert oversized == []


def test_production_has_no_private_cross_module_imports() -> None:
    private_imports: list[str] = []
    for path in _source_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            for imported in node.names:
                if imported.name.startswith("_"):
                    relative = path.relative_to(SOURCE_ROOT).as_posix()
                    private_imports.append(
                        f"{relative}:{node.lineno} {node.module}.{imported.name}"
                    )
    assert private_imports == []
