#!/bin/bash
# Push the repo to the Docker host (default: boomhauer). Artifacts live on the host; pull them with --pull.
set -euo pipefail
HOST="${SC_HOST:-boomhauer}"
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "--pull" ]]; then
  rsync -az "$HOST:sidechannel-exp/artifacts/" artifacts/
else
  rsync -az --delete --exclude artifacts/ --exclude .git/ --exclude __pycache__/ --exclude legacy/ ./ "$HOST:sidechannel-exp/"
fi
