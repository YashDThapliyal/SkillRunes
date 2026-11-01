"""Agent-native export targets for SkillRunes memory."""

from __future__ import annotations

from pathlib import Path

from skillrunes.compress import estimate_tokens
from skillrunes.models import SkillExport, SkillVersion
from skillrunes.utils import now_utc, render_skill_diff

EXPORT_TARGETS = ("claude", "agents", "all")

AGENTS_BEGIN = "<!-- BEGIN SKILLRUNES MANAGED MEMORY -->"
AGENTS_END = "<!-- END SKILLRUNES MANAGED MEMORY -->"


def validate_export_target(target: str) -> str:
    if target not in EXPORT_TARGETS:
        raise ValueError(f"Unknown export target {target!r}. Expected: {', '.join(EXPORT_TARGETS)}.")
    return target


def build_claude_skill(skill: SkillVersion, reference: str = "") -> tuple[dict[Path, str], list[SkillExport]]:
    """Build files for .claude/skills/skillrunes-project-memory."""
    root = Path(".claude") / "skills" / "skillrunes-project-memory"
    skill_md = (
        "---\n"
        "name: skillrunes-project-memory\n"
        "description: Project-specific memory distilled from prior coding-agent sessions. "
        "Use when working in this repository, debugging repeated failures, or following learned project conventions.\n"
        "---\n\n"
        "# SkillRunes Project Memory\n\n"
        f"{skill.content.strip()}\n"
    )
    if reference.strip():
        skill_md += "\n## Supporting Reference\n\nFor detailed lower-priority history, see [reference.md](reference.md).\n"

    files = {root / "SKILL.md": skill_md}
    if reference.strip():
        files[root / "reference.md"] = reference

    exports = [
        SkillExport(
            target="claude",
            path=str(path),
            version=skill.version,
            token_estimate=estimate_tokens(content),
            generated_at=now_utc(),
        )
        for path, content in files.items()
    ]
    return files, exports


def build_agents_update(existing: str, skill: SkillVersion) -> str:
    """Replace or append the SkillRunes-managed AGENTS.md section."""
    section = (
        f"{AGENTS_BEGIN}\n"
        "## SkillRunes Project Memory\n\n"
        "The following guidance is generated from prior coding-agent sessions. "
        "Preserve user-authored instructions outside this managed block.\n\n"
        f"{skill.content.strip()}\n"
        f"{AGENTS_END}\n"
    )

    if AGENTS_BEGIN in existing and AGENTS_END in existing:
        before, rest = existing.split(AGENTS_BEGIN, 1)
        _, after = rest.split(AGENTS_END, 1)
        return before.rstrip() + "\n\n" + section + after.lstrip()
    if existing.strip():
        return existing.rstrip() + "\n\n" + section
    return section


def diff_file(path: Path, old: str, new: str) -> str:
    """Return a unified diff for one planned export write."""
    return render_skill_diff(old, new, 1).replace("SKILL.md (v0)", str(path)).replace(
        "SKILL.md (v1)", str(path)
    )
