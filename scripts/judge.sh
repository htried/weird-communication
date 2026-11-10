#!/bin/bash
# Run the LLM judge on the Docker host, inside the sc-judge image, as the calling user.
#   scripts/judge.sh artifacts/trials/<id> [...] [--model M] [--force]
set -euo pipefail
cd "$(dirname "$0")/.."
docker image inspect sc-judge >/dev/null 2>&1 || docker build -q -t sc-judge analysis >/dev/null
exec docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e SC_CONFIG_DIR=/cfg \
  -v "$PWD:/repo" -v "$HOME/.config/sidechannel:/cfg:ro" -w /repo \
  sc-judge python -m analysis.measurement.judge "$@"
