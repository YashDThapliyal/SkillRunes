"""Central configuration constants for SkillRunes.

Provider names, model names, paths, and tuneable thresholds live here so
nothing is hardcoded elsewhere in the codebase.
"""

# Transcript provider selection
PROVIDER_ENV_VAR = "SKILLRUNES_PROVIDER"
LEGACY_PROVIDER_ENV_VAR = "AGENTLENS_PROVIDER"
DEFAULT_PROVIDER = "claude_code"
SUPPORTED_PROVIDERS = ("claude_code", "codex")

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

# Known local Codex transcript roots.  The directory may exist before a stable
# JSONL transcript format is available, so CodexProvider still raises a clear
# not-implemented error until parsing support is added.
CODEX_SESSION_ROOTS = ("~/.codex/sessions", "~/.Codex/sessions", "~/.Codex/projects")
