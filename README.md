# SkillRunes

Analyse coding-agent session transcripts and recursively improve a `SKILL.md` file.

SkillRunes is provider-based. The first working provider is **Claude Code**,
which reads JSONL transcripts from `~/.claude/projects/`. Codex support is
planned and exposed as an experimental provider path, but transcript ingestion
is not implemented yet.

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

# Choose a transcript provider explicitly
skillrunes run --provider claude_code

# Preview changes without writing files
skillrunes run --dry-run

# Launch the local observability dashboard
skillrunes visualize
```

## Setup

Copy `.env.example` to `.env` and add your Anthropic API key:

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

Provider selection can also be configured with:

```bash
export SKILLRUNES_PROVIDER=claude_code
```

`AGENTLENS_PROVIDER` is still read as a backward-compatible fallback when
`SKILLRUNES_PROVIDER` is unset.

Supported provider names today:

- `claude_code` — supported, default
- `codex` — planned / experimental; currently raises a clear not-implemented error

## Roadmap

- Claude Code provider
- Codex provider
- Cursor provider
- Provider-agnostic dashboard
