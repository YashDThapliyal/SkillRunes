"""FastAPI dashboard server for SkillRunes.

Routes:
  GET /              — main dashboard page (Jinja2 HTML template)
  GET /api/metrics   — ProjectMetrics as JSON
  GET /api/skills    — skill version history as JSON

Data is read exclusively through store.py.  No model calls are made here.
"""

import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import skillrunes.store as store
from skillrunes.models import SessionSummary

app = FastAPI(title="SkillRunes Dashboard")

_HERE = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_HERE / "templates"))


def _tojson(value: Any) -> str:
    def _default(obj: Any) -> str:
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M")
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    return json.dumps(value, default=_default)


_templates.env.filters["escape_html"] = html.escape
_templates.env.filters["tojson"] = _tojson


_static_dir = _HERE / "static"
if _static_dir.exists() and any(_static_dir.iterdir()):
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# Analytics pre-computation
# ---------------------------------------------------------------------------

def _status(s: SessionSummary) -> str:
    if s.what_worked and not s.what_failed:
        return "clean"
    if s.what_worked and s.what_failed:
        return "partial"
    return "failed"


_STATUS_COLOR = {
    "clean":   "#3fb950",
    "partial": "#d29922",
    "failed":  "#f85149",
}


def _build_analytics(sessions: list[SessionSummary]) -> dict[str, Any]:
    """Compute all chart data server-side so the template stays logic-free."""
    if not sessions:
        return {}

    ordered = sorted(sessions, key=lambda s: s.analyzed_at)

    labels        = [s.session_id[:8] for s in ordered]
    statuses      = [_status(s) for s in ordered]
    colors        = [_STATUS_COLOR[st] for st in statuses]
    tokens        = [s.token_count for s in ordered]
    durations     = [round(s.duration_seconds) for s in ordered]
    messages      = [s.message_count for s in ordered]
    patterns_cnt  = [len(s.patterns) for s in ordered]
    worked_cnt    = [len(s.what_worked) for s in ordered]
    failed_cnt    = [len(s.what_failed) for s in ordered]
    compacted     = [s.compaction_occurred for s in ordered]

    # tokens-per-message efficiency (0 where message_count == 0)
    efficiency = [
        round(t / m, 1) if m > 0 else 0
        for t, m in zip(tokens, messages)
    ]

    # log-scale tokens (for chart readability with large outliers)
    log_tokens = [round(math.log10(t + 1), 3) for t in tokens]

    # distribution counts
    n_clean   = statuses.count("clean")
    n_partial = statuses.count("partial")
    n_failed  = statuses.count("failed")

    # cumulative pattern growth (how knowledge accumulates over sessions)
    cumulative_patterns: list[int] = []
    running = 0
    for p in patterns_cnt:
        running += p
        cumulative_patterns.append(running)

    return {
        "labels":               labels,
        "colors":               colors,
        "statuses":             statuses,
        "tokens":               tokens,
        "log_tokens":           log_tokens,
        "durations":            durations,
        "messages":             messages,
        "patterns_cnt":         patterns_cnt,
        "worked_cnt":           worked_cnt,
        "failed_cnt":           failed_cnt,
        "efficiency":           efficiency,
        "compacted":            compacted,
        "cumulative_patterns":  cumulative_patterns,
        "n_clean":              n_clean,
        "n_partial":            n_partial,
        "n_failed":             n_failed,
        # summary stats
        "total":                len(ordered),
        "avg_tokens":           round(sum(tokens) / len(tokens)) if tokens else 0,
        "max_tokens":           max(tokens) if tokens else 0,
        "avg_messages":         round(sum(messages) / len(messages)) if messages else 0,
        "avg_efficiency":       round(sum(efficiency) / len(efficiency), 1) if efficiency else 0,
        "compaction_count":     sum(compacted),
        "total_patterns":       sum(patterns_cnt),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    store.init_store()
    metrics  = store.load_metrics()
    skills   = store.load_skill_history()
    sessions = store.load_all_summaries()
    sessions_desc = sorted(sessions, key=lambda s: s.analyzed_at, reverse=True)
    analytics = _build_analytics(sessions)

    return _templates.TemplateResponse("index.html", {
        "request":    request,
        "metrics":    metrics,
        "skills":     skills,
        "sessions":   sessions_desc,
        "a":          analytics,   # short alias — used heavily in template
    })


@app.get("/api/metrics")
async def api_metrics() -> JSONResponse:
    store.init_store()
    metrics = store.load_metrics()
    if metrics is None:
        return JSONResponse(content={}, status_code=404)
    return JSONResponse(content=metrics.model_dump(mode="json"))


@app.get("/api/skills")
async def api_skills() -> JSONResponse:
    store.init_store()
    skills = store.load_skill_history()
    return JSONResponse(content=[sv.model_dump(mode="json") for sv in skills])
