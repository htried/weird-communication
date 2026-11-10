#!/bin/bash
# Run as the agent user, in Terminal.app, logged into the GUI.
# The first run triggers the macOS mic and camera permission dialogs. Click Allow.
# If you skip this, every agent attempt at the mic or camera fails silently, and the
# data would wrongly say those channels went unused.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
tmp=$(mktemp -d)
ok(){ printf '  [ok]   %s\n' "$1"; }; bad(){ printf '  [FAIL] %s\n' "$1"; }

echo "Tools:"
for t in claude codex ffmpeg imagesnap say afplay python3 swift osascript; do
  command -v "$t" >/dev/null && ok "$t" || bad "$t missing"
done

echo "Speaker (you should hear this):"
osascript -e 'set volume output volume 60 without output muted'
say "preflight check" && ok "say"

echo "Microphone (2s):"
if ffmpeg -loglevel error -f avfoundation -i ":0" -t 2 -y "$tmp/m.wav" 2>/dev/null && [[ -s "$tmp/m.wav" ]]; then
  ok "mic recorded $(stat -f%z "$tmp/m.wav") bytes"; else bad "mic (grant Terminal mic access: System Settings > Privacy)"; fi

echo "Camera:"
if imagesnap -w 1 "$tmp/c.jpg" >/dev/null 2>&1 && [[ -s "$tmp/c.jpg" ]]; then
  ok "camera snapshot"; else bad "camera (grant Terminal camera access)"; fi

echo "Bluetooth:"
if system_profiler SPBluetoothDataType 2>/dev/null | grep -qi "State: On"; then
  bad "Bluetooth is ON (turn it off for round 1)"; else ok "off"; fi

echo "Network (meaningful only after lockdown.sh):"
curl -s -m 5 -o /dev/null https://example.com && bad "general internet reachable" || ok "general internet blocked"
curl -s -m 5 -o /dev/null https://api.anthropic.com && ok "api.anthropic.com reachable" || bad "api.anthropic.com unreachable"
curl -s -m 5 -o /dev/null https://api.openai.com && ok "api.openai.com reachable" || bad "api.openai.com unreachable"
rm -rf "$tmp"
