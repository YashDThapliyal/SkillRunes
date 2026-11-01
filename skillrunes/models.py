"""Pydantic data models for SkillRunes.

All data structures used across the application are defined here before being
referenced elsewhere.  Every model uses ConfigDict(extra="ignore") so that
schema drift in transcript files (new fields added by future provider versions)
does not crash the parser.
"""

from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# JSONL parsing models
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    """A single tool invocation extracted from an assistant message."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    input: dict[str, Any] = {}


class ToolResult(BaseModel):
    """Outcome of one tool invocation, reported back to the agent."""

    model_config = ConfigDict(extra="ignore")

    tool_use_id: str = ""
    is_error: bool = False
    content: str = ""


class Usage(BaseModel):
    """Token usage reported on an assistant message."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class RawMessage(BaseModel):
    """One parsed line from a coding-agent JSONL session transcript.

    The classmethod `from_jsonl_line` normalises the polymorphic JSONL format
    into this unified representation.  Lines that carry no semantic content
    (hook attachments, file-history snapshots, permission-mode records, etc.)
    return None so callers can skip them without special-casing each type.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str = ""
    uuid: str = ""
    parent_uuid: str | None = ""
    type: str
    role: str | None = None
    timestamp: datetime | None = None
    content: str = ""
    tool_calls: list[ToolCall] = []
    tool_results: list[ToolResult] = []
    cwd: str = ""
    usage: Usage | None = None

    # Line types that carry semantic conversation content worth analysing.
    # ClassVar keeps Pydantic from treating this as a model field or private attr.
    MEANINGFUL_TYPES: ClassVar[frozenset[str]] = frozenset({"user", "assistant"})

    @classmethod
    def from_jsonl_line(cls, data: dict[str, Any]) -> "RawMessage | None":
        """Parse a raw dict from one JSONL line into a RawMessage.

        Returns None for line types that contain no conversation content
        (noise lines such as attachments, snapshots, permission records).
        """
        line_type = data.get("type", "")
        if line_type not in cls.MEANINGFUL_TYPES:
            return None

        message = data.get("message", {})
        role = message.get("role", line_type)
        raw_content = message.get("content", "")

        # Normalise content: strings stay as-is; block lists are flattened to
        # plain text with tool_use blocks extracted separately.
        content_text = ""
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []

        if isinstance(raw_content, str):
            content_text = raw_content
        elif isinstance(raw_content, list):
            text_parts: list[str] = []
            for block in raw_content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        )
                    )
                elif block_type == "tool_result":
                    tool_results.append(
                        ToolResult(
                            tool_use_id=block.get("tool_use_id", ""),
                            is_error=bool(block.get("is_error", False)),
                            content=_flatten_tool_content(block.get("content", ""))[
                                :TOOL_RESULT_SNIPPET_CHARS
                            ],
                        )
                    )
                # thinking blocks are skipped intentionally
            content_text = "\n".join(text_parts)

        usage_data = message.get("usage")
        usage = Usage(**usage_data) if usage_data else None

        return cls(
            session_id=data.get("sessionId", ""),
            uuid=data.get("uuid", ""),
            parent_uuid=data.get("parentUuid", ""),
            type=line_type,
            role=role,
            timestamp=data.get("timestamp"),
            content=content_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            cwd=data.get("cwd", ""),
            usage=usage,
        )


# Tool output can be huge; only the head is kept for error classification.
TOOL_RESULT_SNIPPET_CHARS = 500


def _flatten_tool_content(content: Any) -> str:
    """Normalise tool_result content (string or list of text blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


# ---------------------------------------------------------------------------
# Deterministic behaviour signals
# ---------------------------------------------------------------------------

class SessionSignals(BaseModel):
    """Behaviour signals computed from a transcript without any model call.

    These are the ground-truth measurements SkillRunes uses to judge whether a
    skill version actually changed agent behaviour.
    """

    model_config = ConfigDict(extra="ignore")

    tool_calls: int = 0
    tool_errors: int = 0
    retry_loops: int = 0
    user_corrections: int = 0
    interruptions: int = 0
    user_turns: int = 0
    errors_by_tool: dict[str, int] = {}

    @property
    def tool_error_rate(self) -> float:
        return self.tool_errors / self.tool_calls if self.tool_calls else 0.0

    @property
    def correction_rate(self) -> float:
        return self.user_corrections / self.user_turns if self.user_turns else 0.0


# ---------------------------------------------------------------------------
# Extraction output models
# ---------------------------------------------------------------------------

class SessionSummary(BaseModel):
    """Extracted analysis of one coding-agent session.

    Produced by extract.py via the model client and persisted to
    .skillrunes/sessions/<session_id>.json.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    goal: str
    approaches_tried: list[str] = []
    what_worked: list[str] = []
    what_failed: list[str] = []
    patterns: list[str] = []
    token_count: int = 0
    duration_seconds: float = 0.0
    compaction_occurred: bool = False
    analyzed_at: datetime
    message_count: int = 0
    redaction_report: "RedactionReport | None" = None
    signals: SessionSignals | None = None
    started_at: datetime | None = None
    provider: str = ""


# ---------------------------------------------------------------------------
# Skill versioning models
# ---------------------------------------------------------------------------

class SkillVersion(BaseModel):
    """One archived version of the SKILL.md file.

    Stored in .skillrunes/skills/v{version}.md; version numbers are
    monotonically increasing integers starting at 1.
    """

    model_config = ConfigDict(extra="ignore")

    version: int
    timestamp: datetime
    content: str
    change_summary: str = ""
    token_estimate: int = 0
    compression_report: "CompressionReport | None" = None
    reference_content: str = ""


# ---------------------------------------------------------------------------
# Privacy, compression, export, eval, and trace models
# ---------------------------------------------------------------------------

class RedactionReport(BaseModel):
    """Summary of redactions applied to a transcript or file scan."""

    model_config = ConfigDict(extra="ignore")

    level: str
    detector_counts: dict[str, int] = {}
    redacted_fields: list[str] = []
    timestamp: datetime


class CompressionReport(BaseModel):
    """Token-budget report for generated project memory."""

    model_config = ConfigDict(extra="ignore")

    mode: str
    budget_tokens: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    kept_count: int = 0
    moved_count: int = 0
    dropped_count: int = 0


class SkillExport(BaseModel):
    """One generated export to an agent-native instruction target."""

    model_config = ConfigDict(extra="ignore")

    target: str
    path: str
    version: int
    token_estimate: int
    generated_at: datetime


class EvalResult(BaseModel):
    """Deterministic comparison of two skill versions."""

    model_config = ConfigDict(extra="ignore")

    before_version: int
    after_version: int
    metrics: dict[str, float | int | str | bool] = {}
    checks: dict[str, bool] = {}
    generated_at: datetime


class TraceExport(BaseModel):
    """OTel-style trace export for analysed sessions."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    provider: str
    spans: list[dict[str, Any]] = []
    redaction_report: RedactionReport | None = None
    generated_at: datetime


# ---------------------------------------------------------------------------
# Aggregated metrics models
# ---------------------------------------------------------------------------

class TimePoint(BaseModel):
    """A single (date, numeric value) data point for time-series charts."""

    model_config = ConfigDict(extra="ignore")

    date: datetime
    value: float


class PatternCount(BaseModel):
    """A recurring pattern paired with how many sessions it appeared in."""

    model_config = ConfigDict(extra="ignore")

    pattern: str
    count: int


class ProjectMetrics(BaseModel):
    """Aggregated metrics across all analysed sessions for this project.

    Computed by utils.compute_metrics() and persisted to
    .skillrunes/metrics.json for the dashboard to read.
    """

    model_config = ConfigDict(extra="ignore")

    sessions_analyzed: int = 0
    success_rate_series: list[TimePoint] = []
    token_series: list[TimePoint] = []
    top_failure_patterns: list[PatternCount] = []


# ---------------------------------------------------------------------------
# Skill impact (before/after behaviour measurement)
# ---------------------------------------------------------------------------

class VersionCohort(BaseModel):
    """Sessions that ran while one skill version was active.

    version 0 is the baseline period before the first SKILL.md existed.
    metrics maps metric name → mean per session (lower is better for all).
    """

    model_config = ConfigDict(extra="ignore")

    version: int
    active_from: datetime | None = None
    active_until: datetime | None = None
    session_ids: list[str] = []
    metrics: dict[str, float] = {}


class MetricDelta(BaseModel):
    """Change in one metric between two consecutive version cohorts.

    ci_low/ci_high bound the after-minus-before difference of means (95%
    bootstrap).  verdict is improved / regressed / inconclusive /
    insufficient_data.
    """

    model_config = ConfigDict(extra="ignore")

    metric: str
    before_version: int
    after_version: int
    before_mean: float
    after_mean: float
    delta: float
    ci_low: float | None = None
    ci_high: float | None = None
    verdict: str = "insufficient_data"


class ImpactReport(BaseModel):
    """Behavioural impact of each skill version on later sessions."""

    model_config = ConfigDict(extra="ignore")

    cohorts: list[VersionCohort] = []
    deltas: list[MetricDelta] = []
    unmeasured_sessions: int = 0
    min_sessions: int = 0
    generated_at: datetime


# ---------------------------------------------------------------------------
# Replay benchmark (skillrunes bench)
# ---------------------------------------------------------------------------

class BenchTask(BaseModel):
    """One task in a benchmark suite."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    prompt: str
    split: str = "test"


class BenchSuite(BaseModel):
    """A benchmark suite: a template project, a checker, and tasks.

    check is a command template; {python}, {suite_dir}, {workspace} and
    {task_id} are substituted.  Exit code 0 means the task passed.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    suite_dir: str
    project_dir: str
    check: str
    timeout_seconds: int = 600
    tasks: tuple[BenchTask, ...] = ()


class BenchArm(BaseModel):
    """An experimental condition: which instruction file the agent gets."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    skill_path: str = ""
    skill_text: str = ""


class AgentRunStats(BaseModel):
    """What one headless agent invocation did, parsed from stream-json."""

    model_config = ConfigDict(extra="ignore")

    session_id: str = ""
    num_turns: int = 0
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd_estimate: float = 0.0
    is_error: bool = False
    error: str = ""
    signals: SessionSignals = SessionSignals()

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


class BenchRunResult(BaseModel):
    """Outcome of one (task, arm, seed) run, after any feedback rounds."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    split: str
    arm: str
    seed: int
    passed: bool
    first_attempt_passed: bool
    criteria: dict[str, bool] = {}
    check_output: str = ""
    attempts: list[AgentRunStats] = []
    transcript_path: str = ""
    model: str = ""
    started_at: datetime
    finished_at: datetime

    @property
    def key(self) -> str:
        return f"{self.task_id}|{self.arm}|{self.seed}"


class ArmSummary(BaseModel):
    """Aggregate metrics for one arm over all its runs."""

    model_config = ConfigDict(extra="ignore")

    arm: str
    runs: int
    pass_rate: float
    pass_rate_ci: tuple[float, float]
    criteria_rates: dict[str, float] = {}
    mean_turns: float = 0.0
    mean_tokens: float = 0.0
    mean_tool_errors: float = 0.0
    mean_retries: float = 0.0
    mean_duration_seconds: float = 0.0
    agent_errors: int = 0


class ArmComparison(BaseModel):
    """Paired comparison of a candidate arm against a baseline arm.

    Each delta is mean(candidate - baseline) over (task, seed) pairs with a
    95% paired-bootstrap CI.
    """

    model_config = ConfigDict(extra="ignore")

    candidate: str
    baseline: str
    pairs: int
    pass_rate_delta: float
    pass_rate_ci: tuple[float, float]
    tokens_delta: float
    tokens_ci: tuple[float, float]
    turns_delta: float
    turns_ci: tuple[float, float]


class BenchReport(BaseModel):
    """Aggregated benchmark results."""

    model_config = ConfigDict(extra="ignore")

    suite: str
    split: str
    arms: list[ArmSummary] = []
    comparisons: list[ArmComparison] = []
    generated_at: datetime


# ---------------------------------------------------------------------------
# Rule-level memory (ACE-style incremental curation)
# ---------------------------------------------------------------------------

class SkillRule(BaseModel):
    """One curated rule in the project memory, with provenance and feedback."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    section: str
    text: str
    source_sessions: tuple[str, ...] = ()
    helpful: int = 0
    harmful: int = 0
    created_version: int = 0
    updated_version: int = 0

    @property
    def net_score(self) -> int:
        return self.helpful - self.harmful


class RuleDelta(BaseModel):
    """One edit proposed by the model against the current rulebook.

    op is add | update | remove | helpful | harmful.  add needs section+text;
    update needs rule_id+text; the others need rule_id.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    op: str
    rule_id: str = ""
    section: str = ""
    text: str = ""
    session_ids: tuple[str, ...] = ()
    reason: str = ""


class CurationReport(BaseModel):
    """What happened when deltas were applied to a rulebook."""

    model_config = ConfigDict(extra="ignore")

    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    merged_duplicates: list[str] = []
    pruned: list[str] = []
    rejected: list[str] = []


class RuleBook(BaseModel):
    """The full rule-level memory; rendered to SKILL.md for agents."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: int = 0
    next_id: int = 1
    rules: tuple[SkillRule, ...] = ()
