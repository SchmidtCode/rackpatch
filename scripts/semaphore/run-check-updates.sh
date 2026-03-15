#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

window="${RACKPATCH_CHECK_WINDOW:-all}"
selected_stacks="${RACKPATCH_SELECTED_STACKS:-}"
report_file="$(mktemp)"
trap 'rm -f "${report_file}"' EXIT

log_section "Docker Update Check"
printf '[%s] window=%s\n' "$(timestamp)" "${window}"
printf '[%s] selected_stacks=%s\n' "$(timestamp)" "${selected_stacks:-<none>}"

cmd=(python3 /workspace/scripts/check_stack_updates.py --window "${window}")
if [[ -n "${selected_stacks}" ]]; then
  cmd+=(--stack "${selected_stacks}")
fi

printf '[%s] RUN %s > %s\n' "$(timestamp)" "${cmd[*]}" "${report_file}"
"${cmd[@]}" >"${report_file}"

log_section "Summary"
run_cmd python3 /workspace/scripts/print_report_summary.py --kind docker --input "${report_file}"

log_section "Raw JSON"
cat "${report_file}"
