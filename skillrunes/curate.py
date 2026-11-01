"""Deterministic curation of rule-level memory (ACE-style).

Full-document rewrites of an instruction file can silently drop most of it
("context collapse"; Zhang et al., Agentic Context Engineering, ICLR 2026).
SkillRunes' delta mode instead asks the model for small edit operations and
applies them here, in plain code, with guards:

  * removals per round (explicit removes AND vote-based pruning combined) are
    capped at max_removal_fraction of current rules (at least one)
  * an "add" that near-duplicates an existing rule becomes a helpful vote
  * rules whose harmful votes exceed helpful votes by prune_margin are pruned
  * malformed or unknown-id operations are rejected and reported, never guessed

Everything here is pure: inputs are never mutated and no I/O happens.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from skillrunes.models import CurationReport, RuleBook, RuleDelta, SkillRule

DEFAULT_MAX_REMOVAL_FRACTION = 0.25
DEFAULT_PRUNE_MARGIN = 2
DUPLICATE_THRESHOLD = 0.8
VALID_OPS = ("add", "update", "remove", "helpful", "harmful")
DEFAULT_SECTION = "Guidance"
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "with", "be", "is", "it"})


def apply_deltas(
    book: RuleBook,
    deltas: list[RuleDelta],
    *,
    max_removal_fraction: float = DEFAULT_MAX_REMOVAL_FRACTION,
    prune_margin: int = DEFAULT_PRUNE_MARGIN,
) -> tuple[RuleBook, CurationReport]:
    """Apply model-proposed deltas to a rulebook; return the new book + report."""
    version = book.version + 1
    rules: OrderedDict[str, SkillRule] = OrderedDict((r.id, r) for r in book.rules)
    next_id = book.next_id
    report = CurationReport()
    removal_budget = max(1, int(len(book.rules) * max_removal_fraction)) if book.rules else 0

    for delta in deltas:
        if delta.op not in VALID_OPS:
            report.rejected.append(f"unknown op {delta.op!r}")
            continue
        if delta.op == "add":
            next_id = _add(rules, delta, version, next_id, report)
            continue
        rule = rules.get(delta.rule_id)
        if rule is None:
            report.rejected.append(f"{delta.op} {delta.rule_id or '<no id>'}: unknown rule")
            continue
        if delta.op == "update":
            _update(rules, rule, delta, version, report)
        elif delta.op == "remove":
            if len(report.removed) >= removal_budget:
                report.rejected.append(f"remove {rule.id}: removal cap reached")
                continue
            del rules[rule.id]
            report.removed.append(rule.id)
        else:
            rules[rule.id] = _vote(rule, delta.op, delta.session_ids)

    for rule in list(rules.values()):
        if rule.harmful - rule.helpful < prune_margin:
            continue
        if len(report.removed) + len(report.pruned) >= removal_budget:
            report.rejected.append(f"prune {rule.id}: removal cap reached")
            continue
        del rules[rule.id]
        report.pruned.append(rule.id)

    return RuleBook(version=version, next_id=next_id, rules=tuple(rules.values())), report


def render_rulebook(book: RuleBook, title: str = "Project Skill") -> str:
    """Render rules as SKILL.md Markdown grouped by section.

    Rule ids are kept in trailing HTML comments so agents see clean bullets
    while SkillRunes can map feedback back to individual rules.
    """
    sections: OrderedDict[str, list[SkillRule]] = OrderedDict()
    for rule in book.rules:
        sections.setdefault(rule.section or DEFAULT_SECTION, []).append(rule)
    lines = [f"# {title}", ""]
    for section, rules in sections.items():
        lines += [f"## {section}", ""]
        lines += [f"- {rule.text} <!-- {rule.id} -->" for rule in rules]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def rulebook_for_prompt(book: RuleBook) -> str:
    """Compact listing of current rules (with ids and scores) for the model."""
    if not book.rules:
        return "(no rules yet)"
    return "\n".join(
        f"[{r.id}] ({r.section}; +{r.helpful}/-{r.harmful}) {r.text}" for r in book.rules
    )


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of content words; 1.0 for identical wording."""
    words_a, words_b = _words(a), _words(b)
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _add(
    rules: OrderedDict[str, SkillRule],
    delta: RuleDelta,
    version: int,
    next_id: int,
    report: CurationReport,
) -> int:
    text = delta.text.strip()
    if not text:
        report.rejected.append("add: empty text")
        return next_id
    duplicate = next((r for r in rules.values() if similarity(r.text, text) >= DUPLICATE_THRESHOLD), None)
    if duplicate is not None:
        rules[duplicate.id] = _vote(duplicate, "helpful", delta.session_ids)
        report.merged_duplicates.append(duplicate.id)
        return next_id
    rule_id = f"R{next_id}"
    rules[rule_id] = SkillRule(
        id=rule_id,
        section=delta.section.strip() or DEFAULT_SECTION,
        text=text,
        source_sessions=tuple(dict.fromkeys(delta.session_ids)),
        created_version=version,
        updated_version=version,
    )
    report.added.append(rule_id)
    return next_id + 1


def _update(
    rules: OrderedDict[str, SkillRule],
    rule: SkillRule,
    delta: RuleDelta,
    version: int,
    report: CurationReport,
) -> None:
    text = delta.text.strip()
    if not text:
        report.rejected.append(f"update {rule.id}: empty text")
        return
    rules[rule.id] = rule.model_copy(
        update={
            "text": text,
            "section": delta.section.strip() or rule.section,
            "source_sessions": tuple(dict.fromkeys((*rule.source_sessions, *delta.session_ids))),
            "updated_version": version,
        }
    )
    report.updated.append(rule.id)


def _vote(rule: SkillRule, op: str, session_ids: tuple[str, ...]) -> SkillRule:
    field = "helpful" if op == "helpful" else "harmful"
    return rule.model_copy(
        update={
            field: getattr(rule, field) + 1,
            "source_sessions": tuple(dict.fromkeys((*rule.source_sessions, *session_ids))),
        }
    )


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}
