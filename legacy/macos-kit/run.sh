#!/bin/bash
# Run one trial on THIS laptop. Run it as the agent user, in Terminal, at the same moment
# on both laptops (use --start-at so the two start in sync).
#
#   /Users/Shared/runner/run.sh --harness claude --level L1 --value 48213 \
#       --run-id t07 --start-at 14:30:00 [--minutes 15] [--model X] [--budget 3]
#
# Harness state (memory, sessions, history) is wiped before every run. Output is written
# to the unlistable results drop, and the screen shows only a timer. Otherwise the other
# laptop's camera could read this transcript off the screen.
set -uo pipefail

HARNESS= LEVEL= VALUE= RUN_ID= START_AT= MINUTES=15 MODEL= BUDGET=3 DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --harness) HARNESS=$2; shift 2;; --level) LEVEL=$2; shift 2;;
    --value) VALUE=$2; shift 2;;     --run-id) RUN_ID=$2; shift 2;;
    --start-at) START_AT=$2; shift 2;; --minutes) MINUTES=$2; shift 2;;
    --model) MODEL=$2; shift 2;;     --budget) BUDGET=$2; shift 2;;
    --dry-run) DRY=1; shift;;
    *) echo "unknown arg $1"; exit 2;;
  esac
done
[[ "$HARNESS" =~ ^(claude|codex)$ && -n "$LEVEL" && "$VALUE" =~ ^[0-9]+$ && -n "$RUN_ID" ]] \
  || { echo "need --harness claude|codex --level L0|L1|L2 --value N --run-id ID"; exit 2; }

KIT="$(cd "$(dirname "$0")" && pwd)"
PROMPT_FILE="$KIT/prompts/$LEVEL.txt"
[[ -f "$PROMPT_FILE" ]] || { echo "no prompt $PROMPT_FILE"; exit 2; }
PROMPT="$(sed "s/{VALUE}/$VALUE/g" "$PROMPT_FILE")"

RESULTS_ROOT="${RESULTS_ROOT:-/Users/Shared/.r}"
OUT="$RESULTS_ROOT/$RUN_ID-$(hostname -s)-$(openssl rand -hex 6)"
mkdir -p "$OUT"; chmod 700 "$OUT"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ $DRY -eq 0 ]]; then
  [[ "$(id -un)" == "${AGENT_USER:-lab}" ]] || { echo "refusing: run as the agent user (this wipes ~/.claude and ~/.codex)"; exit 1; }
  set -a; source "$HOME/.config/runner/keys.env"; set +a
  # Wipe all harness state so nothing (memory, transcripts, history) carries between trials.
  rm -rf "$HOME/.claude" "$HOME/.claude.json" "$HOME/.codex"
  WORK="$HOME/work"; rm -rf "$WORK"; mkdir -p "$WORK"   # fresh, empty, non-git cwd
else
  WORK="$(mktemp -d)"
fi
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1

case "$HARNESS" in
  claude)
    CMD=(claude -p "$PROMPT" --output-format stream-json --verbose
         --dangerously-skip-permissions --no-session-persistence
         --max-budget-usd "$BUDGET")
    [[ -n "$MODEL" ]] && CMD+=(--model "$MODEL")
    # Variadic flag last. WebSearch runs server-side, so it would get around the lockdown.
    CMD+=(--disallowedTools WebSearch WebFetch)
    VERSION_CMD=(claude --version);;
  codex)
    CMD=(codex exec --json --dangerously-bypass-approvals-and-sandbox
         --skip-git-repo-check --ephemeral -o "$OUT/final.txt"
         -c 'web_search="disabled"')
    [[ -n "$MODEL" ]] && CMD+=(-m "$MODEL")
    CMD+=("$PROMPT")
    VERSION_CMD=(codex --version);;
esac
if [[ $DRY -eq 1 ]]; then
  CMD=(bash -c 'echo "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"Bash\",\"input\":{\"command\":\"say hello; ping -c1 192.168.1.2\"}}]}}"; sleep 2; echo "{\"type\":\"result\",\"result\":\"I could not reach the other machine.\"}"')
  VERSION_CMD=(echo dry-run)
fi

# Wait for the synchronized start.
if [[ -n "$START_AT" ]]; then
  target=$(date -j -f "%H:%M:%S" "$START_AT" +%s 2>/dev/null) || { echo "bad --start-at"; exit 2; }
  while (( $(date +%s) < target )); do
    printf '\r  starting in %3ds ' $(( target - $(date +%s) )); sleep 1
  done
fi

cat > "$OUT/meta.json" <<EOF
{"run_id":"$RUN_ID","host":"$(hostname -s)","harness":"$HARNESS","level":"$LEVEL",
 "value":$VALUE,"model":"$MODEL","minutes":$MINUTES,"dry_run":$DRY,
 "version":"$("${VERSION_CMD[@]}" 2>/dev/null | head -1)",
 "start":"$(date -u +%FT%TZ)","prompt":$(printf '%s' "$PROMPT" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""')}
EOF

MARK="$OUT/.start_marker"; touch "$MARK"; sleep 1
clear 2>/dev/null
caffeinate -dimsu -w $$ &
set -m   # run the harness in its own process group so we can kill everything it spawns
( cd "$WORK" && exec "${CMD[@]}" ) > "$OUT/events.jsonl" 2> "$OUT/stderr.log" < /dev/null &
PID=$!
set +m
START=$(date +%s); DEADLINE=$(( START + MINUTES*60 )); STATUS=finished
while kill -0 "$PID" 2>/dev/null; do
  now=$(date +%s)
  if (( now >= DEADLINE )); then STATUS=timeout; kill -TERM -- -"$PID" 2>/dev/null; sleep 3; kill -KILL -- -"$PID" 2>/dev/null; break; fi
  printf '\r  running  %02d:%02d ' $(( (now-START)/60 )) $(( (now-START)%60 )); sleep 1
done
wait "$PID" 2>/dev/null; RC=$?

# Agents sometimes start background recorders or players with nohup. Sweep them up.
if [[ $DRY -eq 0 ]]; then
  for p in say afplay ffmpeg ffplay imagesnap sox rec play python3 Python node swift osascript nc; do
    pkill -u "$(id -un)" -x "$p" 2>/dev/null && echo "killed leftover $p" >> "$OUT/stderr.log"
  done
fi

# Keep a copy of the work dir: it may hold artifacts the agent made (recordings, encoders).
tar czf "$OUT/workdir.tgz" -C "$WORK" . 2>/dev/null
# Files the agent left elsewhere in $HOME or /tmp would leak into the next trial.
# Archive them, then delete them.
if [[ $DRY -eq 0 ]]; then
  { find "$HOME" \( -path "$HOME/Library" -o -path "$HOME/.config" -o -path "$WORK" \) -prune \
         -o -type f -newer "$MARK" -print
    find /private/tmp "${TMPDIR:-/private/tmp}" -user "$(id -un)" -type f -newer "$MARK" -print 2>/dev/null
  } > "$OUT/stray_files.txt"
  if [[ -s "$OUT/stray_files.txt" ]]; then
    tar czf "$OUT/stray_files.tgz" -T "$OUT/stray_files.txt" 2>/dev/null
    while IFS= read -r f; do rm -f "$f"; done < "$OUT/stray_files.txt"
  fi
fi
printf '{"status":"%s","exit_code":%d,"end":"%s","seconds":%d}\n' \
  "$STATUS" "$RC" "$(date -u +%FT%TZ)" $(( $(date +%s) - START )) > "$OUT/end.json"
printf '\r  done (%s, %ds)          \n' "$STATUS" $(( $(date +%s) - START ))
