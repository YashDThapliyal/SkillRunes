"""Hidden reviewer for the invoicing_tacit benchmark.

Usage: python check.py <workspace> <task_id>

Same functional tests and visible conventions as the invoicing suite (Decimal
money, domain errors, no print, frozen legacy tests, documented API), minus the
CHANGELOG rule, plus four TACIT conventions that nothing in the repository
reveals — they exist only in this reviewer's feedback:

  changeset        every change adds a non-empty .changeset/*.md file
  exported_api     new public functions are listed in invoicing.__all__
  error_prefix     errors raised by new functions start with "<function name>: "
  tests_added      every new public function is referenced by a test in tests/

Exit 0 only if everything passes.  Stdout is review feedback (replayed to the
agent during the train phase); the last line is a JSON criteria breakdown.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_BASE_PATH = HERE.parent / "invoicing" / "check.py"
_spec = importlib.util.spec_from_file_location("invoicing_base_check", _BASE_PATH)
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
base.PRISTINE = HERE / "project"  # compare against this suite's pristine copy

HIDDEN_TEST_PREFIX = "test_zz_hidden_"
VISIBLE = ("decimal_money", "domain_errors", "no_print", "legacy_untouched", "documented_api")

FEEDBACK = {
    **base.FEEDBACK,
    "changeset": "We track release notes with changesets: add a short markdown file under "
    ".changeset/ describing the change (e.g. .changeset/add-refund.md).",
    "exported_api": "New public functions must be exported from the package: import them in "
    "invoicing/__init__.py and add them to __all__ (missing: {detail}).",
    "error_prefix": "Error messages must start with the function name and a colon, e.g. "
    "raise ValidationError(\"refund: amount must be positive\") (found {detail}).",
    "tests_added": "Every new public function needs at least one test in tests/ that "
    "exercises it (missing: {detail}).",
}


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    task_id = sys.argv[2]
    results: dict[str, str | None] = {"functional": base.run_hidden_tests(workspace, task_id)}
    visible = base.check_conventions(workspace)
    results.update({name: visible[name] for name in VISIBLE})
    new_functions = _new_public_functions(workspace)
    results.update(
        {
            "changeset": _changeset(workspace),
            "exported_api": _not_exported(workspace, new_functions),
            "error_prefix": _bad_prefixes(new_functions),
            "tests_added": _untested(workspace, new_functions),
        }
    )
    failures = {name: detail for name, detail in results.items() if detail is not None}
    if failures:
        print("Code review: changes requested.")
        for name, detail in failures.items():
            print(f"- {FEEDBACK[name].format(detail=detail)}")
    else:
        print("Code review: approved.")
    print(json.dumps({"passed": not failures, "criteria": {k: v is None for k, v in results.items()}}))
    return 0 if not failures else 1


def _new_public_functions(workspace: Path) -> dict[str, ast.FunctionDef]:
    """Public module-level functions in invoicing/ that the pristine repo lacks."""
    pristine = {
        node.name
        for path in (base.PRISTINE / "invoicing").glob("*.py")
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef)
    }
    found: dict[str, ast.FunctionDef] = {}
    for path in sorted((workspace / "invoicing").glob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_") and node.name not in pristine:
                found[node.name] = node
    return found


def _changeset(workspace: Path) -> str | None:
    folder = workspace / ".changeset"
    files = [p for p in folder.glob("*.md") if p.read_text().strip()] if folder.is_dir() else []
    return None if files else ""


def _not_exported(workspace: Path, functions: dict[str, ast.FunctionDef]) -> str | None:
    init = workspace / "invoicing" / "__init__.py"
    exported: set[str] = set()
    try:
        for node in ast.parse(init.read_text()).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                exported = {
                    elt.value for elt in getattr(node.value, "elts", []) if isinstance(elt, ast.Constant)
                }
    except (OSError, SyntaxError):
        pass
    missing = sorted(set(functions) - exported)
    return ", ".join(missing[:5]) if missing else None


def _bad_prefixes(functions: dict[str, ast.FunctionDef]) -> str | None:
    bad: list[str] = []
    for name, node in functions.items():
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Raise) or not isinstance(sub.exc, ast.Call) or not sub.exc.args:
                continue
            message = _leading_text(sub.exc.args[0])
            if message is None or not message.startswith(f"{name}:"):
                bad.append(f"{name}:{sub.lineno}")
    return ", ".join(bad[:5]) if bad else None


def _leading_text(arg: ast.expr) -> str | None:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr) and arg.values and isinstance(arg.values[0], ast.Constant):
        return str(arg.values[0].value)
    return None


def _untested(workspace: Path, functions: dict[str, ast.FunctionDef]) -> str | None:
    test_text = "\n".join(
        p.read_text()
        for p in (workspace / "tests").rglob("test_*.py")
        if not p.name.startswith(HIDDEN_TEST_PREFIX)
    )
    missing = sorted(name for name in functions if name not in test_text)
    return ", ".join(missing[:5]) if missing else None


if __name__ == "__main__":
    sys.exit(main())
