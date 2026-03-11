#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

dry_run="${OPS_DRY_RUN:-true}"
target_limit="${OPS_GUEST_LIMIT:-docker_hosts}"

log_section "Guest Patching"
printf '[%s] dry_run=%s\n' "$(timestamp)" "${dry_run}"
printf '[%s] limit=%s\n' "$(timestamp)" "${target_limit}"

run_locked_cmd ansible-playbook \
  /workspace/playbooks/patch_guests.yml \
  --limit "${target_limit}" \
  -e "dry_run=${dry_run}"

if [[ "${target_limit}" == "guests" || "${target_limit}" == "docker_hosts" ]]; then
  OPS_PACKAGE_SCOPE="${target_limit}" /workspace/scripts/semaphore/run-check-packages.sh
else
  OPS_PACKAGE_HOSTS="${target_limit}" /workspace/scripts/semaphore/run-check-packages.sh
fi
