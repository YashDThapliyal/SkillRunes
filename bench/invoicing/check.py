"""Hidden reviewer for the invoicing benchmark.

Usage: python check.py <workspace> <task_id>

Exit 0 when the task is functionally correct AND follows the house
conventions; exit 1 otherwise.  Stdout is written as code-review feedback (it
is replayed to the agent as a user correction during the train phase).  The
final stdout line is a JSON object with the per-criterion breakdown.

House conventions (visible in the existing code, never documented):
  decimal_money       no float() calls or float literals in invoicing/
  domain_errors       no raising built-in exceptions in invoicing/; use InvoiceError subclasses
  no_print            no print() in invoicing/ (use invoicing.log.get_logger)
  changelog           a new bullet under "## Unreleased" in CHANGELOG.md
  legacy_untouched    tests/legacy/ is byte-identical to the original
  documented_api      new public functions have a docstring and a return annotation
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRISTINE = HERE / "project"
HIDDEN = HERE / "hidden"
BUILTIN_ERRORS = {"ValueError", "TypeError", "Exception", "RuntimeError", "KeyError", "AssertionError"}

FEEDBACK = {
    "functional": "The implementation doesn't meet the spec; the reviewer's tests fail:\n{detail}",
    "decimal_money": "Money must stay Decimal end to end; found float usage in {detail}.",
    "domain_errors": "We never raise built-in exceptions from the library; use the InvoiceError "
    "hierarchy in invoicing/errors.py (found {detail}).",
    "no_print": "Don't print() from library code; log via invoicing.log.get_logger ({detail}).",
    "changelog": "Every change needs a bullet under '## Unreleased' in CHANGELOG.md.",
    "legacy_untouched": "tests/legacy/ is frozen; revert your changes to {detail}.",
    "documented_api": "New public functions need a docstring and a return type annotation ({detail}).",
}


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    task_id = sys.argv[2]
    results: dict[str, str | None] = {"functional": run_hidden_tests(workspace, task_id)}
    results.update(check_conventions(workspace))
    failures = {name: detail for name, detail in results.items() if detail is not None}

    if failures:
        print("Code review: changes requested.")
        for name, detail in failures.items():
            print(f"- {FEEDBACK[name].format(detail=detail)}")
    else:
        print("Code review: approved.")
    print(json.dumps({"passed": not failures, "criteria": {k: v is None for k, v in results.items()}}))
    return 0 if not failures else 1


def run_hidden_tests(workspace: Path, task_id: str) -> str | None:
    hidden_test = HIDDEN / f"test_{task_id}.py"
    target = workspace / "tests" / f"test_zz_hidden_{task_id}.py"
    shutil.copyfile(hidden_test, target)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "tests"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "test run timed out"
    finally:
        target.unlink(missing_ok=True)
    if proc.returncode == 0:
        return None
    lines = [line for line in proc.stdout.splitlines() if line.startswith(("E ", "FAILED", "ERROR"))]
    return "\n".join(lines[:8]) or proc.stdout[-800:]


def check_conventions(workspace: Path) -> dict[str, str | None]:
    sources = {p: p.read_text() for p in sorted((workspace / "invoicing").glob("*.py"))}
    trees = {}
    for path, text in sources.items():
        try:
            trees[path] = ast.parse(text)
        except SyntaxError:
            trees[path] = ast.Module(body=[], type_ignores=[])
    return {
        "decimal_money": _find(trees, _is_float_use),
        "domain_errors": _find(trees, _is_builtin_raise),
        "no_print": _find(trees, _is_print),
        "changelog": None if _changelog_has_entry(workspace) else "",
        "legacy_untouched": _legacy_diff(workspace),
        "documented_api": _undocumented(trees),
    }


def _find(trees: dict[Path, ast.Module], predicate) -> str | None:
    hits = [
        f"{path.name}:{node.lineno}"
        for path, tree in trees.items()
        for node in ast.walk(tree)
        if predicate(node)
    ]
    return ", ".join(hits[:5]) if hits else None


def _is_float_use(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, float):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float"


def _is_builtin_raise(node: ast.AST) -> bool:
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(exc, ast.Name) and exc.id in BUILTIN_ERRORS


def _is_print(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"


def _changelog_has_entry(workspace: Path) -> bool:
    path = workspace / "CHANGELOG.md"
    if not path.exists():
        return False
    section = path.read_text().split("## Unreleased", 1)
    if len(section) < 2:
        return False
    body = section[1].split("\n## ", 1)[0]
    return any(line.strip().startswith(("-", "*")) for line in body.splitlines())


def _legacy_diff(workspace: Path) -> str | None:
    changed = []
    for original in sorted((PRISTINE / "tests" / "legacy").glob("*.py")):
        current = workspace / "tests" / "legacy" / original.name
        if not current.exists() or current.read_bytes() != original.read_bytes():
            changed.append(original.name)
    return ", ".join(changed) if changed else None


def _undocumented(trees: dict[Path, ast.Module]) -> str | None:
    pristine = _public_functions(
        {p: ast.parse(p.read_text()) for p in (PRISTINE / "invoicing").glob("*.py")}
    )
    missing = [
        name
        for name, node in _public_functions(trees).items()
        if name not in pristine and (ast.get_docstring(node) is None or node.returns is None)
    ]
    return ", ".join(sorted(missing)[:5]) if missing else None


def _public_functions(trees: dict[Path, ast.Module]) -> dict[str, ast.FunctionDef]:
    return {
        f"{path.stem}.{node.name}": node
        for path, tree in trees.items()
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


if __name__ == "__main__":
    sys.exit(main())
