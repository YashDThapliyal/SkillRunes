<img width="1024" height="572" alt="image" src="https://github.com/user-attachments/assets/840c760a-7e42-4cf6-b4df-9a37061ab031" />

# SkillRunes

Analyse coding-agent session transcripts and recursively improve a `SKILL.md` file.

SkillRunes turns past coding-agent sessions into reusable project memory. It
reads local transcript files, extracts what the assistant tried, what worked,
what failed, and what patterns are worth remembering, then uses those learnings
to generate or update a project `SKILL.md`.

The goal is simple: each time you run SkillRunes, your project-level assistant
instructions get a little sharper.

SkillRunes is provider-based and reads both **Claude Code** and **OpenAI Codex
CLI** transcripts.

Unlike tools that only generate instruction files, SkillRunes **measures whether
they help**: deterministic behaviour signals (tool error rate, retry loops,
user corrections) are computed from every transcript, compared across skill
versions with bootstrap confidence intervals, and a replay benchmark runs the
agent on held-out tasks to test a new skill before it is promoted.

## What's New in 0.3.0

- **Behaviour signals** from transcripts, no LLM needed: tool error rate, retry
  loops (identical call re-issued after failing), user corrections, interruptions
- **Codex provider**: `~/.codex/sessions` rollout transcripts
- **`skillrunes impact`**: did behaviour change after each `SKILL.md` version?
  (95% bootstrap CIs; dashboard chart)
- **`skillrunes bench`**: replay benchmark: headless agent runs on held-out
  tasks under different instruction files, paired comparison, promotion gate
- **`--synthesis delta`**: ACE-style rule-level memory with stable ids,
  provenance, helpful/harmful votes and anti-collapse guards
- **OTLP export** with OpenTelemetry GenAI semantic conventions
- **Billing guard**: `--billing subscription` never lets an API key reach a
  `claude` subprocess

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

# Export OTLP traces (GenAI semantic conventions), optionally to a collector
skillrunes export-traces --format otlp-json --endpoint http://localhost:4318

# Measure behaviour change across skill versions
skillrunes impact

# Curate memory as rule-level edits instead of full rewrites
skillrunes run --synthesis delta

# Use the logged-in Claude subscription even if ANTHROPIC_API_KEY is set
skillrunes run --billing subscription

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

- `claude_code` — default; reads `~/.claude/projects/**.jsonl`
- `codex` — reads `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`

Both map onto the same message model (text, tool calls, tool results with error
flags, token usage), so signals, impact, and trace export work identically.
Sessions are matched to the current working directory.

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

## Measuring Impact

Every analysed session gets deterministic signals computed straight from the
transcript:

| Signal | Definition |
|---|---|
| tool error rate | tool results flagged as errors / tool calls |
| retry loops | an identical tool call re-issued after that exact call failed |
| user corrections | a later user turn that pushes back ("no, …", "revert that", "why did you…") |
| interruptions | the user stopped the agent mid-turn |

`skillrunes impact` buckets sessions by which `SKILL.md` version was active
when they started and compares each version with the previous one using a
seeded 95% bootstrap CI on the difference of means (verdict: improved,
regressed, inconclusive, or too few sessions). Older summaries are backfilled
from raw archives with no model calls. This is observational; use
`skillrunes bench` for a controlled comparison.

## Replay Benchmark

`skillrunes bench` runs the agent headlessly (`claude -p`) on a task suite in
throwaway copies of a project, once per **arm** (instruction file) and seed,
then scores each run with the suite's checker.

```bash
SUITE=bench/invoicing/suite.toml
# 1. Train: run train tasks with no skill file; reviewer feedback is replayed
#    once on failure, producing realistic correction transcripts
skillrunes bench run --suite $SUITE --split train --seeds 2
# 2. Learn instruction files from those transcripts
skillrunes bench learn                        # SkillRunes (rewrite)
skillrunes bench learn --synthesis delta      # SkillRunes (ACE-style delta)
skillrunes bench oneshot --suite $SUITE       # /init-style one-shot baseline
# 3. Test on held-out tasks, paired across arms
D=.skillrunes/bench/invoicing
skillrunes bench run --suite $SUITE --split test --seeds 5 \
  --arm none --arm oneshot=$D/oneshot-claude.md \
  --arm skillrunes=$D/skillrunes-skill.md --arm delta=$D/skillrunes-delta-skill.md
# 4. Compare and gate: exit code 2 if the candidate is not non-inferior
skillrunes bench report --gate skillrunes
```

Isolation and safety:

- `--setting-sources project` and `--strict-mcp-config`: your global
  `~/.claude` rules, hooks, plugins and MCP servers cannot leak into runs
- the arm's instruction file is the only `CLAUDE.md` the agent sees
- every agent run bills your **Claude subscription**: API-key variables are
  stripped and `claude auth status` must report a `claude.ai` login
- runs are parallel (`--workers`) and resumable; results append to `results.jsonl`

Arms are compared on identical (task, seed) pairs with a paired bootstrap, so
task difficulty cancels out. `--gate` promotes a candidate only if its
pass-rate gain is non-negative and the CI lower bound is above `-margin`.

The bundled **invoicing** suite is a small Python library whose house
conventions (Decimal money, a domain error hierarchy, logging instead of
print, changelog entries, a frozen legacy test folder, documented public API)
are visible in the code but never written down. A hidden reviewer enforces
them along with a hidden functional test per task: 5 train tasks and 8
held-out test tasks.

## Delta Mode (rule-level memory)

Rewriting an instruction file in full on every update can silently drop most
of it ("context collapse", *Agentic Context Engineering*, ICLR 2026). With
`--synthesis delta` the model proposes small operations (`add`, `update`,
`remove`, `helpful`, `harmful`) against a rulebook, and deterministic code
applies them:

- every rule has a stable id and the sessions it came from
- near-duplicate additions become helpful votes instead of new rules
- removals per update are capped at 25% of the rulebook
- rules with two more harmful than helpful votes are pruned

The rulebook lives in `.skillrunes/rules.json`; `SKILL.md` is rendered from it.

## Billing

`--billing` (or `SKILLRUNES_BILLING`) selects how model calls are paid for:
`auto` (API key if set, else Claude CLI), `subscription` (Claude CLI only; API
keys are stripped from the subprocess), or `api`. `skillrunes bench` always
uses the subscription.

## Local Files

Running SkillRunes creates:

- `.skillrunes/sessions/` — raw transcript archives and extracted summaries
- `.skillrunes/skills/` — versioned `SKILL.md` history
- `.skillrunes/exports/` — export metadata
- `.skillrunes/evals/` — deterministic eval artifacts
- `.skillrunes/traces/` — redacted trace exports (SkillRunes JSON and OTLP)
- `.skillrunes/impact.json` — latest behaviour impact report
- `.skillrunes/rules.json` — rule-level memory (delta mode)
- `.skillrunes/bench/<suite>/` — benchmark results, transcripts, reports
- `.skillrunes/metrics.json` — dashboard metrics
- `SKILL.md` — the current generated skill file

## Roadmap

- ~~Claude Code provider~~
- ~~Codex provider~~
- Cursor provider
- Bench suites built from a project's own history
- Dashboard view for benchmark results
