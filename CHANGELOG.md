# Changelog

## 0.3.0

### Added

- Deterministic behaviour signals per session (tool calls/errors, retry loops,
  user corrections, interruptions, errors by tool); tool results are now parsed.
- Codex CLI provider for `~/.codex/sessions` rollout transcripts.
- `skillrunes impact`: per-skill-version cohorts with 95% bootstrap CIs, signal
  backfill from raw archives, dashboard chart and `/api/impact`.
- `skillrunes bench run|learn|oneshot|report`: isolated, parallel, resumable
  replay benchmark with paired comparisons and a non-inferiority gate.
- Bundled `invoicing` benchmark suite (5 train / 8 test tasks, hidden reviewer).
- `run --synthesis delta`: ACE-style rule-level memory (`.skillrunes/rules.json`).
- `export-traces --format otlp-json [--endpoint URL]` with OpenTelemetry GenAI
  semantic conventions.
- `--billing auto|subscription|api` and `SKILLRUNES_BILLING`.

### Fixed

- Dashboard index crashed on Starlette >= 1.0 (`TemplateResponse` signature).
- Redaction now covers tool result content.
- Skill history loading no longer crashes on non-numeric `v*.md` files.

### Security/Privacy

- `claude` subprocesses never inherit `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`.
- OTLP export includes tool names and timing only, never tool inputs/outputs.

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
