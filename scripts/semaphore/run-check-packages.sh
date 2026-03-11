#!/usr/bin/env bash

set -euo pipefail

source /workspace/scripts/semaphore/common.sh

scope="${OPS_PACKAGE_SCOPE:-all}"
selected_hosts="${OPS_PACKAGE_HOSTS:-}"
report_file="$(mktemp)"
trap 'rm -f "${report_file}"' EXIT

log_section "Package Update Check"
printf '[%s] scope=%s\n' "$(timestamp)" "${scope}"
printf '[%s] selected_hosts=%s\n' "$(timestamp)" "${selected_hosts:-<none>}"

cmd=(python3 /workspace/scripts/check_package_updates.py --scope "${scope}")
if [[ -n "${selected_hosts}" ]]; then
  cmd+=(--host "${selected_hosts}")
fi

printf '[%s] RUN %s > %s\n' "$(timestamp)" "${cmd[*]}" "${report_file}"
"${cmd[@]}" >"${report_file}"

log_section "Summary"
run_cmd python3 /workspace/scripts/print_report_summary.py --kind package --input "${report_file}"

log_section "Raw JSON"
cat "${report_file}"
