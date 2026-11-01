"""Load and validate benchmark suites (TOML) and arm specifications."""

from __future__ import annotations

import tomllib
from pathlib import Path

from skillrunes.models import BenchArm, BenchSuite, BenchTask

VALID_SPLITS = ("train", "test")
NO_SKILL_ARM = "none"


def load_suite(path: Path) -> BenchSuite:
    """Parse a suite.toml file into a validated BenchSuite."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read suite {path}: {exc}") from exc

    meta = data.get("suite") or {}
    suite_dir = path.resolve().parent
    project_dir = suite_dir / str(meta.get("project", "project"))
    if not project_dir.is_dir():
        raise ValueError(f"Suite project directory not found: {project_dir}")
    if not meta.get("check"):
        raise ValueError("Suite is missing [suite].check")

    tasks = tuple(BenchTask(**task) for task in data.get("tasks", []))
    _validate_tasks(tasks)
    return BenchSuite(
        name=str(meta.get("name", suite_dir.name)),
        suite_dir=str(suite_dir),
        project_dir=str(project_dir),
        check=str(meta["check"]),
        timeout_seconds=int(meta.get("timeout_seconds", 600)),
        tasks=tasks,
    )


def select_tasks(suite: BenchSuite, split: str, task_ids: list[str] | None = None) -> list[BenchTask]:
    """Return suite tasks in the given split ("all" for both), optionally filtered."""
    if split not in (*VALID_SPLITS, "all"):
        raise ValueError(f"split must be one of train, test, all (got {split!r})")
    tasks = [t for t in suite.tasks if split == "all" or t.split == split]
    if task_ids:
        unknown = set(task_ids) - {t.id for t in tasks}
        if unknown:
            raise ValueError(f"Unknown task id(s) for split {split}: {', '.join(sorted(unknown))}")
        tasks = [t for t in tasks if t.id in task_ids]
    return tasks


def parse_arm(spec: str) -> BenchArm:
    """Parse 'none' or 'name=path/to/instructions.md' into a BenchArm."""
    name, sep, raw_path = spec.partition("=")
    name = name.strip()
    if not name or "|" in name:
        raise ValueError(f"Invalid arm name in {spec!r}")
    if not sep:
        if name != NO_SKILL_ARM:
            raise ValueError(f"Arm {name!r} needs a file: use {name}=path/to/SKILL.md")
        return BenchArm(name=name)
    path = Path(raw_path.strip()).expanduser()
    if not path.is_file():
        raise ValueError(f"Instruction file for arm {name!r} not found: {path}")
    return BenchArm(name=name, skill_path=str(path.resolve()), skill_text=path.read_text())


def _validate_tasks(tasks: tuple[BenchTask, ...]) -> None:
    if not tasks:
        raise ValueError("Suite defines no [[tasks]].")
    ids = [t.id for t in tasks]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate task ids: {', '.join(sorted(duplicates))}")
    bad = [t.id for t in tasks if t.split not in VALID_SPLITS or "|" in t.id]
    if bad:
        raise ValueError(f"Tasks with invalid split or id: {', '.join(bad)}")
