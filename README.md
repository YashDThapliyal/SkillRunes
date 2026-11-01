<img width="1024" height="572" alt="image" src="https://github.com/user-attachments/assets/840c760a-7e42-4cf6-b4df-9a37061ab031" />

# SkillRunes

Analyse coding-agent session transcripts and recursively improve a `SKILL.md` file.

SkillRunes turns past coding-agent sessions into reusable project memory. It
reads local transcript files, extracts what the assistant tried, what worked,
what failed, and what patterns are worth remembering, then uses those learnings
to generate or update a project `SKILL.md`.

The goal is simple: each time you run SkillRunes, your project-level assistant
instructions get a little sharper.

SkillRunes is provider-based. The first working provider is **Claude Code**.
Codex support is planned and exposed as an experimental provider path, but
transcript ingestion is not implemented yet.

## What's New in 0.2.0

SkillRunes 0.2.0 focuses on safer, more portable agent memory:

- Privacy/redaction modes: `basic`, `strict`, and explicit `off`
- `skillrunes doctor privacy` for local secret scanning
- Token-budgeted skill compression and `skillrunes prune`
- Claude Skills export to `.claude/skills/skillrunes-project-memory/`
- `AGENTS.md` export for agent-compatible project instructions
- Deterministic local evals for skill-version quality checks
- Redacted OTel-style trace export for analysed sessions

## How It Works

1. SkillRunes scans transcript files for the current project.
2. New sessions are parsed and archived locally.
3. A model extracts structured session summaries.
4. SkillRunes synthesizes those summaries into a versioned `SKILL.md`.
5. The dashboard shows session metrics, failure patterns, and skill history.

All SkillRunes state is written inside the current project under `.skillrunes/`.

## Install

```bash
pip install skillrunes
```

## Usage

```bash
# Initialise for the current project
skillrunes init

# Analyse new sessions and update SKILL.md
skillrunes run

# Redact transcript secrets before archive/model analysis (default: basic)
skillrunes run --redact-level basic

# Choose a transcript provider explicitly
skillrunes run --provider claude_code

# Generate token-budgeted project memory
skillrunes run --budget-tokens 1200 --compression-mode balanced

# Preview changes without writing files
skillrunes run --dry-run

# Recompress the current skill memory without ingesting new sessions
skillrunes prune --budget-tokens 1200 --dry-run

# Export memory to agent-native instruction formats
skillrunes export --target claude
skillrunes export --target agents

# Scan local SkillRunes artifacts for likely secrets
skillrunes doctor privacy

# Run deterministic before/after memory-quality checks
skillrunes eval --latest

# Export analysed sessions as redacted OTel-style JSON
skillrunes export-traces --format otel-json

# Launch the local observability dashboard
skillrunes visualize
```

## Setup

SkillRunes needs model access when running transcript analysis.

You can use an Anthropic API key:

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

Or, if no `ANTHROPIC_API_KEY` is set, SkillRunes falls back to the local Claude
Code CLI:

```bash
claude --print
```

That means SkillRunes can run without an Anthropic API key as long as Claude
Code is installed and authenticated. Without either an API key or a working
Claude Code login, `skillrunes run` cannot extract summaries.

Provider selection can also be configured with:

```bash
export SKILLRUNES_PROVIDER=claude_code
```

## Providers

Supported provider names today:

- `claude_code` — supported, default
- `codex` — planned / experimental; currently raises a clear not-implemented error

The Claude Code provider reads JSONL transcripts from `~/.claude/projects/` and
matches sessions to the current working directory.

## Privacy

SkillRunes defaults to `--redact-level basic`, which redacts common secrets
before raw transcript archiving and model extraction. Basic redaction covers API
keys, bearer tokens, GitHub tokens, `.env`-style secret assignments, private key
blocks, authorization headers, and URLs with embedded credentials.

Use `--redact-level strict` to additionally redact emails, home-directory
paths, external absolute paths, IP addresses, and URL query strings. Use
`--redact-level off` only when you explicitly want unredacted local archives and
model input; the CLI prints a warning before continuing.

Run `skillrunes doctor privacy --level basic` to scan local SkillRunes artifacts
for likely secrets without modifying files. Use `--level strict` to also flag
emails, paths, IP addresses, and URL query strings.

## Token Budgets and Exports

SkillRunes can compress generated memory to an approximate token budget:

```bash
skillrunes run --budget-tokens 1200 --compression-mode balanced
skillrunes prune --budget-tokens 1200 --dry-run
```

Supported compression modes are `concise`, `balanced`, and `detailed`.

Exports keep root `SKILL.md` intact while also writing agent-native instruction
formats:

- `skillrunes export --target claude` writes
  `.claude/skills/skillrunes-project-memory/SKILL.md`
- `skillrunes export --target agents` updates a managed block in `AGENTS.md`
- `skillrunes export --target all` does both

Codex transcript ingestion is still not implemented. For Codex today, use the
`AGENTS.md` export path as instruction memory.

## Evals and Trace Export

`skillrunes eval --latest` compares the latest two skill versions using local,
deterministic checks for token deltas, duplicate-rule growth, repeated failure
mentions, redaction cleanliness, and reusable pattern coverage.

`skillrunes export-traces --format otel-json` writes redacted OTel-style JSON
for analysed sessions under `.skillrunes/traces/`. Trace export re-applies
redaction at export time and includes a minimal `skillrunes.skill_update` event
for sessions that contributed extracted patterns to generated memory.

Compression is deterministic and line-based: it prioritizes high-signal lines
such as headings, failures, tests, and standing instructions, but very tight
budgets may simplify Markdown structure.

## Local Files

Running SkillRunes creates:

- `.skillrunes/sessions/` — raw transcript archives and extracted summaries
- `.skillrunes/skills/` — versioned `SKILL.md` history
- `.skillrunes/exports/` — export metadata
- `.skillrunes/evals/` — deterministic eval artifacts
- `.skillrunes/traces/` — redacted trace exports
- `.skillrunes/metrics.json` — dashboard metrics
- `SKILL.md` — the current generated skill file

## Roadmap

- Claude Code provider
- Codex provider
- Cursor provider
- Provider-agnostic dashboard
