"""Central configuration constants for SkillRunes.

Provider names, model names, paths, and tuneable thresholds live here so
nothing is hardcoded elsewhere in the codebase.
"""

# Transcript provider selection
PROVIDER_ENV_VAR = "SKILLRUNES_PROVIDER"
DEFAULT_PROVIDER = "claude_code"
SUPPORTED_PROVIDERS = ("claude_code", "codex")

# Billing for model calls.
#   auto          API key if ANTHROPIC_API_KEY is set, else the Claude CLI
#   subscription  always use the logged-in Claude CLI; API keys are stripped
#   api           always use ANTHROPIC_API_KEY via the SDK
BILLING_ENV_VAR = "SKILLRUNES_BILLING"
BILLING_MODES = ("auto", "subscription", "api")
DEFAULT_BILLING = "auto"

# Claude model used for all API calls.  Update here to change globally.
MODEL = "claude-sonnet-4-20250514"

# Local dashboard server settings
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 4823

# Hidden state directory written inside the current project
STORE_DIR_NAME = ".skillrunes"

# Sessions with more messages than this are split into chunks before analysis
MAX_MESSAGES_PER_CHUNK = 200

# Root folder where Claude Code writes session transcripts
CLAUDE_PROJECTS_ROOT = "~/.claude/projects"

# Known local Codex transcript roots, searched recursively for rollout-*.jsonl.
CODEX_SESSION_ROOTS = ("~/.codex/sessions", "~/.Codex/sessions", "~/.Codex/projects")
