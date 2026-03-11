#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

dry_run="${OPS_DRY_RUN:-false}"

log_section "Low-Risk Docker Window"
printf '[%s] dry_run=%s\n' "$(timestamp)" "${dry_run}"

log_section "Policy Validation"
run_cmd /workspace/scripts/validate-policy.py

log_section "Compose Validation"
run_cmd /workspace/scripts/validate-compose.sh

log_section "Apply Auto-Windowed Stack Updates"
run_locked_cmd ansible-playbook \
  /workspace/playbooks/apply_docker_updates.yml \
  -e "target_window=auto-windowed" \
  -e "dry_run=${dry_run}"

OPS_CHECK_WINDOW=auto-windowed /workspace/scripts/semaphore/run-check-updates.sh
