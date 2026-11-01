#!/usr/bin/env bash
# Full SkillRunes held-out experiment on one bench suite.
#
#   bench/run_experiment.sh [suite]     suite = invoicing (default) | invoicing_tacit
#
#   1. train:  5 train tasks x 3 seeds, no instruction file, 1 reviewer-feedback round
#   2. learn:  SkillRunes (rewrite + delta) from train transcripts; one-shot /init baseline
#   3. test:   8 held-out tasks x 4 arms x 5 seeds = 160 runs
#   4. report: paired comparison vs no-instruction baseline + promotion gate
#
# Every agent run bills the logged-in Claude subscription (API keys are
# stripped; the harness aborts unless `claude auth status` says claude.ai).
# Resumable: re-running skips runs already in results.jsonl.
set -euo pipefail

cd "$(dirname "$0")/.."
SUITE_NAME=${1:-invoicing}
SUITE=bench/$SUITE_NAME/suite.toml
OUT=bench/$SUITE_NAME/runs/${RUN_NAME:-main}
[ -f "$SUITE" ] || { echo "No suite at $SUITE" >&2; exit 1; }
WORKERS=${WORKERS:-6}
SR=${SKILLRUNES:-.venv/bin/skillrunes}

echo "== 1/4 train"
$SR bench run --suite $SUITE --split train --seeds 3 --workers "$WORKERS" --out "$OUT"

echo "== 2/4 learn instruction files"
[ -f "$OUT/skillrunes-skill.md" ] || $SR bench learn --out "$OUT"
[ -f "$OUT/skillrunes-delta-skill.md" ] || $SR bench learn --out "$OUT" --synthesis delta
[ -f "$OUT/oneshot-claude.md" ] || $SR bench oneshot --suite $SUITE --out "$OUT"

echo "== 3/4 held-out test"
$SR bench run --suite $SUITE --split test --seeds 5 --workers "$WORKERS" --out "$OUT" \
  --arm none \
  --arm oneshot="$OUT/oneshot-claude.md" \
  --arm skillrunes="$OUT/skillrunes-skill.md" \
  --arm delta="$OUT/skillrunes-delta-skill.md"

echo "== 4/4 report"
$SR bench report --out "$OUT" --suite-name "$SUITE_NAME" --split train --baseline none || true
$SR bench report --out "$OUT" --suite-name "$SUITE_NAME" --split test --baseline none --gate skillrunes || true
$SR bench report --out "$OUT" --suite-name "$SUITE_NAME" --split test --baseline oneshot --gate skillrunes || true
