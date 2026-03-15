#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

dry_run="${RACKPATCH_DRY_RUN:-true}"
approved_services="${RACKPATCH_APPROVED_SERVICES:-[]}"

log_section "Approved Maintenance Window"
printf '[%s] dry_run=%s\n' "$(timestamp)" "${dry_run}"
printf '[%s] approved_services=%s\n' "$(timestamp)" "${approved_services}"

log_section "Maintenance Orchestration"
run_locked_cmd ansible-playbook \
  /workspace/playbooks/maintenance_orchestrator.yml \
  -e "approved_services=${approved_services}" \
  -e "dry_run=${dry_run}"
