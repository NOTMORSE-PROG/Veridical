"""BUG-552: nothing outside F6 imports a statcheck-backed module.

statcheck pulls in pandas and scipy.stats (~80 MiB, ~0.7 CPU-s to import).
The pipeline imports F6's service once, in a worker thread, when a check
reaches its integrity stage (BUG-532). Any other import of those modules,
at module level or inside a function running on the event loop, would bring
the memory back into an idle server and stall the API while it loads.
"""

import ast
from pathlib import Path

import app

ROOT = Path(app.__file__).parent
FORENSICS = ROOT / "checks" / "forensics"
HEAVY_LIBRARIES = {"statcheck", "pandas", "scipy"}
# The one sanctioned way in: imported by name in a thread (pipeline/machine.py).
LAZY_PATH = ("pipeline/machine.py", "app.checks.forensics.service")


def _module_name(path: Path) -> str:
    return ".".join(("app", *path.relative_to(ROOT).with_suffix("").parts))


def _imports(tree: ast.AST, module: str) -> set[str]:
    """Every module a file imports, at any depth (functions included)."""
    found = set()
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _top(name: str) -> str:
    return name.split(".")[0]


def _statcheck_backed() -> set[str]:
    """F6 modules that reach statcheck, pandas or scipy through their imports."""
    graph = {}
    for path in FORENSICS.glob("*.py"):
        if path.name == "sprite_worker.py":
            continue  # runs only as a separate process, with stand-in libraries
        module = _module_name(path)
        graph[module] = _imports(ast.parse(path.read_text(encoding="utf-8")), module)
    backed = {m for m, deps in graph.items() if {_top(d) for d in deps} & HEAVY_LIBRARIES}
    changed = True
    while changed:
        changed = False
        for module, deps in graph.items():
            if module not in backed and deps & backed:
                backed.add(module)
                changed = True
    return backed


def _string_imports(tree: ast.AST) -> set[str]:
    """Module names passed as string literals to import_module(...)."""
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", None)) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def test_the_statcheck_backed_modules_are_found():
    backed = _statcheck_backed()
    # The scan must see the modules it protects, or it would pass vacuously.
    assert {
        "app.checks.forensics.service",
        "app.checks.forensics.extract",
        "app.checks.forensics.pcheck",
        "app.checks.forensics.notation",
    } <= backed


def test_no_module_outside_f6_imports_a_statcheck_backed_module():
    backed = _statcheck_backed()
    offenders = []
    for path in ROOT.rglob("*.py"):
        if FORENSICS in path.parents:
            continue
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _imports(tree, _module_name(path))
        bad = sorted(n for n in names if n in backed or _top(n) in HEAVY_LIBRARIES)
        for name in sorted(_string_imports(tree)):
            if (name in backed or _top(name) in HEAVY_LIBRARIES) and (relative, name) != LAZY_PATH:
                bad.append(f"import_module({name!r})")
        offenders.extend(f"{relative}: {name}" for name in bad)
    assert offenders == []


def test_the_pipeline_loads_f6_in_a_worker_thread():
    source = (ROOT / LAZY_PATH[0]).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "to_thread"
        and len(node.args) >= 2
        and getattr(node.args[0], "attr", None) == "import_module"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == LAZY_PATH[1]
    ]
    assert len(calls) == 1
