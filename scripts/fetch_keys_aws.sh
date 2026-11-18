#!/bin/bash
# On the EC2 runner: materialize keys from Secrets Manager into the layout the runner reads
# (~/.config/sidechannel/keys/node{1,2}.env, judge.env). Uses the instance role (host only).
#   secret sidechannel/agent-keys = {"node1": {"ANTHROPIC_API_KEY": "...", ...}, "node2": {...}}
#   secret sidechannel/judge      = {"OPENAI_API_KEY": "..."}
set -euo pipefail
D="$HOME/.config/sidechannel"; mkdir -p "$D/keys"; chmod 700 "$D" "$D/keys"; umask 077
aws secretsmanager get-secret-value --region us-east-1 --secret-id sidechannel/agent-keys --query SecretString --output text \
  | python3 -c "
import json, sys, os
d = json.load(sys.stdin)
for role in ('node1', 'node2'):
    with open(os.path.expanduser(f'~/.config/sidechannel/keys/{role}.env'), 'w') as f:
        f.writelines(f'{k}={v}\n' for k, v in d[role].items())
    print(role, sorted(d[role]))"
# The instance role may only GetSecretValue (no DescribeSecret), so just try to read it.
if J=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id sidechannel/judge --query SecretString --output text 2>/dev/null); then
  printf '%s' "$J" | python3 -c "import json,sys,os; d=json.load(sys.stdin); open(os.path.expanduser('~/.config/sidechannel/judge.env'),'w').writelines(f'{k}={v}\n' for k,v in d.items()); print('judge', sorted(d))"
  unset J
fi
