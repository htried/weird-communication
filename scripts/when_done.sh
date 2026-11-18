#!/bin/bash
# Run a command (detached, survives SSH disconnects) once a log contains a marker (default: PIPELINE COMPLETE).
#   scripts/when_done.sh artifacts/sweeps/lg1.log artifacts/sweeps/after-lg1.log 'cmd ...'
set -euo pipefail
cd "$(dirname "$0")/.."
WATCH="$1"; LOG="$2"; CMD="$3"; MARK="${4:-PIPELINE COMPLETE}"
setsid nohup bash -c "
  while ! grep -q '$MARK' '$WATCH' 2>/dev/null; do sleep 60; done
  echo \"== \$(date -u +%FT%TZ) $WATCH complete; running queued command\"
  $CMD
  echo \"== \$(date -u +%FT%TZ) QUEUED COMMAND DONE\"
" >> "$LOG" 2>&1 < /dev/null &
echo "queued (pid $!); log: $LOG"
