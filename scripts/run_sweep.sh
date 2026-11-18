#!/bin/bash
# Run a sweep end-to-end on the Docker host, fully detached (survives SSH disconnects):
#   sweep (resumable) → score all trials → judge un-judged trials.
# Re-running the same command resumes; completed trials (DONE marker) and judged trials are skipped.
#   scripts/run_sweep.sh sweeps/ladder_v1.json [parallel]       # start (returns immediately)
#   tail -f artifacts/sweeps/<name>.log                          # follow progress
#   kill $(cat artifacts/sweeps/<name>.pid)                       # stop (resume later by re-running)
set -euo pipefail
cd "$(dirname "$0")/.."
SPEC="$1"; PAR="${2:-6}"
NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" "$SPEC")
mkdir -p artifacts/sweeps
LOG="artifacts/sweeps/$NAME.log"; PID="artifacts/sweeps/$NAME.pid"
if [[ -f "$PID" ]] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "already running (pid $(cat "$PID")); log: $LOG"; exit 0
fi
setsid nohup bash -c "
  echo \"== \$(date -u +%FT%TZ) sweep start: $SPEC parallel=$PAR\"
  python3 -m harness.runner.trial sweep '$SPEC' --parallel $PAR
  echo \"== \$(date -u +%FT%TZ) scoring\"
  python3 analysis/score.py artifacts/trials/$NAME-* --csv artifacts/sweeps/$NAME-scores.csv | tail -3
  echo \"== \$(date -u +%FT%TZ) judging\"
  scripts/judge.sh artifacts/trials/$NAME-* --parallel $PAR
  if command -v aws >/dev/null && curl -s -m 2 -o /dev/null http://169.254.169.254/ 2>/dev/null; then
    echo \"== \$(date -u +%FT%TZ) pushing scrubbed artifacts to S3\"; scripts/push_artifacts_s3.sh || echo \"S3 push FAILED\"
  fi
  echo \"== \$(date -u +%FT%TZ) PIPELINE COMPLETE\"
" >> "$LOG" 2>&1 < /dev/null &
echo $! > "$PID"
echo "started pid $(cat "$PID"); log: $LOG"
