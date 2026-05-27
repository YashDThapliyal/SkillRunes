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

# Choose a transcript provider explicitly
skillrunes run --provider claude_code

# Preview changes without writing files
skillrunes run --dry-run

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

## Local Files

Running SkillRunes creates:

- `.skillrunes/sessions/` — raw transcript archives and extracted summaries
- `.skillrunes/skills/` — versioned `SKILL.md` history
- `.skillrunes/metrics.json` — dashboard metrics
- `SKILL.md` — the current generated skill file

## Roadmap

- Claude Code provider
- Codex provider
- Cursor provider
- Provider-agnostic dashboard
