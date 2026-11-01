# Changelog

## 0.2.0

### Added

- Privacy/redaction modes for `skillrunes run`: `basic`, `strict`, and explicit `off`.
- `skillrunes doctor privacy` for local scanning of SkillRunes artifacts and generated instruction files.
- Token-budgeted skill generation via `--budget-tokens` and `--compression-mode`.
- `skillrunes prune` for recompressing current skill memory without ingesting new sessions.
- Claude Skills export to `.claude/skills/skillrunes-project-memory/SKILL.md`.
- `AGENTS.md` export with SkillRunes-managed markers that preserve user-authored content.
- Deterministic local evals with `skillrunes eval --latest` and `--before/--after`.
- Redacted OTel-style trace export with `skillrunes export-traces --format otel-json`.

### Fixed

- Restored `load_metrics()` so saved dashboard metrics load correctly.
- Made strict URL redaction order-safe and idempotent for credential-bearing URLs.
- Re-applied redaction during trace export so stored summary text is not trusted blindly.

### Security/Privacy

- Redacts common secrets before raw transcript archive and model extraction by default.
- Supports strict scanning/redaction for emails, paths, IP addresses, and URL query strings.
- Blocks trace export with `--redact-level off`; exports require `basic` or `strict`.

### Known Limitations

- Codex transcript ingestion is not implemented yet; use `AGENTS.md` export for Codex-compatible instructions.
- Compression is deterministic and line-based, so tight budgets may simplify Markdown structure.
- Dashboard trace UI is minimal; trace data is currently exported as JSON artifacts.
