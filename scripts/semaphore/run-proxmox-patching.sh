#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

dry_run="${OPS_DRY_RUN:-true}"
target_limit="${OPS_PROXMOX_LIMIT:-proxmox_nodes}"

log_section "Proxmox Node Patching"
printf '[%s] dry_run=%s\n' "$(timestamp)" "${dry_run}"
printf '[%s] limit=%s\n' "$(timestamp)" "${target_limit}"

run_locked_cmd ansible-playbook \
  /workspace/playbooks/patch_proxmox_nodes.yml \
  --limit "${target_limit}" \
  -e "dry_run=${dry_run}"

if [[ "${target_limit}" == "proxmox_nodes" || "${target_limit}" == "proxmox" ]]; then
  OPS_PACKAGE_SCOPE=proxmox /workspace/scripts/semaphore/run-check-packages.sh
else
  OPS_PACKAGE_HOSTS="${target_limit}" /workspace/scripts/semaphore/run-check-packages.sh
fi
