#!/bin/bash
# Run on your own machine (AWS admin profile). Prompts for API keys without echoing them and
# merges them into Secrets Manager: sidechannel/agent-keys (per role) and sidechannel/judge.
# Press Enter at a prompt to keep the current value.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-shmatikov}" AWS_REGION=us-east-1
read -rsp "Anthropic API key (Claude Code agents): " ANT; echo
read -rsp "OpenAI API key (Codex agents): " OAI; echo
read -rsp "OpenAI API key for the JUDGE (Enter = same as Codex key): " JUD; echo
ANT="$ANT" OAI="$OAI" JUD="${JUD:-$OAI}" python3 - <<'PY'
import json, os, subprocess
def aws(*a, check=True): return subprocess.run(["aws", *a], capture_output=True, text=True, check=check)
r = aws("secretsmanager", "get-secret-value", "--secret-id", "sidechannel/agent-keys", "--query", "SecretString", "--output", "text")
doc = json.loads(r.stdout)
for role in ("node1", "node2"):
    if os.environ["ANT"]: doc[role]["ANTHROPIC_API_KEY"] = os.environ["ANT"]
    if os.environ["OAI"]: doc[role]["CODEX_API_KEY"] = os.environ["OAI"]
aws("secretsmanager", "put-secret-value", "--secret-id", "sidechannel/agent-keys", "--secret-string", json.dumps(doc))
print("agent-keys now has:", {k: sorted(v) for k, v in doc.items()})
if os.environ["JUD"]:
    j = json.dumps({"OPENAI_API_KEY": os.environ["JUD"]})
    if aws("secretsmanager", "describe-secret", "--secret-id", "sidechannel/judge", check=False).returncode == 0:
        aws("secretsmanager", "put-secret-value", "--secret-id", "sidechannel/judge", "--secret-string", j)
    else:
        aws("secretsmanager", "create-secret", "--name", "sidechannel/judge", "--tags", "Key=project,Value=sidechannel", "--secret-string", j)
    print("judge secret set")
PY
