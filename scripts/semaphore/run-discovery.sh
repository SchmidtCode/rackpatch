#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

log_section "Policy Validation"
run_cmd /workspace/scripts/validate-policy.py

log_section "Compose Validation"
run_cmd /workspace/scripts/validate-compose.sh

log_section "Discovery Payload"
run_cmd python3 /workspace/scripts/render_approval_payload.py --window discovery

RACKPATCH_CHECK_WINDOW=all /workspace/scripts/semaphore/run-check-updates.sh
RACKPATCH_PACKAGE_SCOPE=all /workspace/scripts/semaphore/run-check-packages.sh
