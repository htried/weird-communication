#!/bin/bash
# One-time setup for a dedicated experiment laptop (macOS). Run as an ADMIN user:
#   sudo ./setup/setup_machine.sh
# Creates the standard (non-admin) user the agents run as, installs the kit to a
# root-owned location, creates the write-only results drop, and stores API keys.
set -euo pipefail

AGENT_USER="${AGENT_USER:-lab}"
KIT_DIR=/Users/Shared/runner          # neutral name: agents may stumble on it
RESULTS_DIR=/Users/Shared/.r          # mode 1733: agent can write, cannot list

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"

# 1. Agent user (standard account, no sudo, no Apple ID).
if ! id "$AGENT_USER" &>/dev/null; then
  echo "Creating standard user '$AGENT_USER' (you'll be asked for its password)..."
  sysadminctl -addUser "$AGENT_USER" -fullName "Lab" -password -
fi
if dseditgroup -o checkmember -m "$AGENT_USER" admin &>/dev/null; then
  echo "ERROR: $AGENT_USER is an admin. Make it a standard user."; exit 1
fi

# 2. Kit: root-owned, world-readable, not writable by the agent.
mkdir -p "$KIT_DIR"
cp "$SRC/run.sh" "$SRC/setup/preflight.sh" "$KIT_DIR/"
cp -R "$SRC/prompts" "$KIT_DIR/"
chown -R root:wheel "$KIT_DIR"; chmod -R a+rX,go-w "$KIT_DIR"; chmod 755 "$KIT_DIR"/*.sh

# 3. Results drop: sticky, write+exec only, so the agent can't enumerate past runs.
mkdir -p "$RESULTS_DIR"
chown root:wheel "$RESULTS_DIR"; chmod 1733 "$RESULTS_DIR"

# 4. API keys (separate keys per laptop, so there's no shared server-side state).
KEYS_DIR="/Users/$AGENT_USER/.config/runner"
mkdir -p "$KEYS_DIR"
if [[ ! -f "$KEYS_DIR/keys.env" ]]; then
  read -rsp "Anthropic API key for THIS laptop: " AK; echo
  read -rsp "OpenAI API key for THIS laptop:    " OK; echo
  umask 077
  printf 'ANTHROPIC_API_KEY=%q\nCODEX_API_KEY=%q\n' "$AK" "$OK" > "$KEYS_DIR/keys.env"
fi
chown -R "$AGENT_USER":staff "/Users/$AGENT_USER/.config"; chmod 600 "$KEYS_DIR/keys.env"

cat <<EOF

Done. Next (see README):
  - Log in to the GUI as '$AGENT_USER' and install Claude Code there (native installer).
  - Make sure codex, ffmpeg and imagesnap are on PATH (brew, as admin).
  - Turn Bluetooth OFF, and turn AirDrop/Handoff off for '$AGENT_USER'.
  - As '$AGENT_USER' in Terminal: $KIT_DIR/preflight.sh   (approve mic + camera)
  - Then, as admin: sudo ./setup/lockdown.sh
EOF
