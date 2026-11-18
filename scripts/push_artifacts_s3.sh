#!/bin/bash
# On the EC2 runner: scrub credentials from artifacts, then sync them to the private S3 bucket.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/scrub_artifacts.py artifacts/trials
aws s3 sync artifacts/ "s3://sidechannel-exp-artifacts-122997907744/artifacts/" --only-show-errors
echo "synced artifacts to S3"
