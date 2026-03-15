#!/usr/bin/env bash

set -euo pipefail

export ANSIBLE_CONFIG="${ANSIBLE_CONFIG:-/workspace/ansible.cfg}"
export PYTHONUNBUFFERED=1

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %Z'
}

log_section() {
  printf '\n[%s] === %s ===\n' "$(timestamp)" "$*"
}

run_cmd() {
  printf '[%s] RUN %s\n' "$(timestamp)" "$*"
  "$@"
}

run_locked_cmd() {
  local lock_file="${RACKPATCH_LOCK_FILE:-/workspace/state/rackpatch-execution.lock}"
  local rc=0
  printf '[%s] RUN (locked) %s\n' "$(timestamp)" "$*"
  if (
    flock -n 9 || exit 200
    "$@"
  ) 9>"${lock_file}"; then
    rc=0
  else
    rc=$?
  fi
  if [[ ${rc} -eq 200 ]]; then
    printf '[%s] lock busy: %s\n' "$(timestamp)" "${lock_file}" >&2
    return 1
  fi
  return "${rc}"
}
