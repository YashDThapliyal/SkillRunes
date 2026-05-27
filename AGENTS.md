# SkillRunes — AGENTS.md

## What this project is
A Python CLI tool that reads coding-agent session transcripts through provider modules,
analyzes them using a model client, recursively improves a SKILL.md file, and optionally
launches a local observability dashboard. Distributed as a PyPI package.

Claude Code is the first working provider and reads JSONL files from ~/.claude/projects/.
Codex is a planned / experimental provider path and should fail with a clear
not-implemented error until transcript parsing is implemented.

## Stack
- Python 3.11+
- Typer (CLI framework)
- Anthropic Python SDK (claude-sonnet-4-20250514)
- FastAPI + uvicorn (dashboard server)
- Jinja2 (dashboard templating)
- Chart.js (frontend charts, loaded via CDN)
- Pydantic (data models)
- Rich (terminal output formatting)

## Project structure
skillrunes/
├── __main__.py        # Entry point
├── cli.py             # Typer CLI commands (run, visualize, init)
├── ingest/            # Transcript providers (Claude Code, Codex, future providers)
├── extract.py         # Model extraction calls — analyzes sessions
├── synthesize.py      # Skill file generation + versioning
├── store.py           # .skillrunes/ folder read/write
├── models.py          # Pydantic models for all data structures
├── dashboard/
│   ├── server.py      # FastAPI app
│   ├── templates/     # Jinja2 HTML templates
│   └── static/        # CSS, any local JS
└── utils.py           # Shared helpers

.skillrunes/            # Hidden state folder (gitignored)
├── sessions/          # Per-session extracted JSON summaries
├── skills/            # Versioned SKILL.md history (v1.md, v2.md...)
└── metrics.json       # Aggregated metrics for dashboard

## Coding conventions
- All data structures defined as Pydantic models in models.py before being used elsewhere
- All file I/O goes through store.py — never read/write .skillrunes/ directly from other modules
- All model calls go through extract.py and synthesize.py only
- Use Rich for all terminal output — no raw print() statements
- Async where it makes the code cleaner, sync is fine for file operations
- Type hints on every function signature

## Things to never do
- Never read transcript files outside provider-owned transcript roots and the current project directory
- Never make model calls from cli.py directly
- Never hardcode model names — use a MODEL constant in a config.py file
- Never write to SKILL.md without first saving the current version to .skillrunes/skills/

## Provider roadmap
- Claude Code provider
- Codex provider
- Cursor provider
- Provider-agnostic dashboard
