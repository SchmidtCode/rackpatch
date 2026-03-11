#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

dry_run="${OPS_DRY_RUN:-true}"
selected_stacks="${OPS_SELECTED_STACKS:-[]}"

log_section "Approved Docker Window"
printf '[%s] dry_run=%s\n' "$(timestamp)" "${dry_run}"
printf '[%s] selected_stacks=%s\n' "$(timestamp)" "${selected_stacks}"

log_section "Policy Validation"
run_cmd /workspace/scripts/validate-policy.py

log_section "Compose Validation"
run_cmd /workspace/scripts/validate-compose.sh

log_section "Apply Approved Stack Updates"
run_locked_cmd ansible-playbook \
  /workspace/playbooks/apply_docker_updates.yml \
  -e "selected_stacks=${selected_stacks}" \
  -e "dry_run=${dry_run}"

normalized_stacks="$(python3 - <<'PY' "${selected_stacks}"
import json
import sys

value = sys.argv[1].strip()
if not value or value == "[]":
    print("")
    raise SystemExit(0)
if value.startswith("["):
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        print(value)
    else:
        print(",".join(item for item in parsed if item))
else:
    print(value)
PY
)"

if [[ -n "${normalized_stacks}" ]]; then
  OPS_SELECTED_STACKS="${normalized_stacks}" OPS_CHECK_WINDOW=approve /workspace/scripts/semaphore/run-check-updates.sh
else
  OPS_CHECK_WINDOW=approve /workspace/scripts/semaphore/run-check-updates.sh
fi
