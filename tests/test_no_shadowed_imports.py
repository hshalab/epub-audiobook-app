"""A function-level import makes that name local for the *whole* function body.

Any earlier read of the same name then raises UnboundLocalError instead of
resolving to the module-level import, and only on the code path that reaches the
earlier read. `_run_single_video` shipped exactly this: it read `settings.data_root`
near the top and re-imported `settings` ~45 lines further down, so every batch
render died with

    cannot access local variable 'settings' where it is not associated with a value

after the full render and validation had already succeeded -- 40 minutes of work
thrown away at the last step. It stayed hidden while an earlier validation failure
was short-circuiting the function before the read.

This is a whole-codebase check rather than a per-function test because the trigger
is a code *shape*, not a behaviour: the shadowed read is unreachable until some
unrelated failure stops happening, so no realistic unit test would have covered it.
Benign deferred imports (the name is imported before any use, as in
Settings.get_ffmpeg_path) are deliberately not flagged.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def _module_level_imports(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _own_scope(node: ast.AST):
    """Yield nodes in this scope, not descending into nested scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _own_scope(child)


def _shadowed_reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = _module_level_imports(tree)
    problems: list[str] = []

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(_own_scope(func))

        first_import: dict[str, int] = {}
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    if name not in first_import:
                        first_import[name] = node.lineno

        first_read: dict[str, int] = {}
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.lineno < first_read.get(node.id, 1 << 30):
                    first_read[node.id] = node.lineno

        for name, import_line in first_import.items():
            if name not in module_names:
                continue  # no module-level name to shadow
            read_line = first_read.get(name)
            if read_line is not None and read_line < import_line:
                problems.append(
                    f"{path.relative_to(APP.parent)}:{import_line}: '{name}' is "
                    f"imported inside {func.name}() but already read at line "
                    f"{read_line}, so that read raises UnboundLocalError instead "
                    f"of using the module-level import -- drop the local import"
                )
    return problems


def test_no_function_import_shadows_an_earlier_read():
    problems: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        problems.extend(_shadowed_reads(path))

    assert not problems, "\n".join(["shadowed module-level imports:", *problems])
