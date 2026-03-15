#!/usr/bin/env bash
set -euo pipefail

rackpatch_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_site_root="${RACKPATCH_SITE_ROOT:-${rackpatch_root}/sites/example}"
stacks_file="${RACKPATCH_STACKS_FILE:-${default_site_root%/}/stacks.yml}"

python3 - <<'PY' "${stacks_file}" | while IFS= read -r root; do
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
for stack in payload.get("stacks", []):
    print(stack.get("path") or stack.get("project_dir") or "")
PY
  [[ -z "${root}" ]] && continue
  echo "[validate] ${root}"
  compose_cmd=("${rackpatch_root}/scripts/compose-wrapper.sh")
  [[ -f "${root}/.env" ]] && compose_cmd+=(--env-file .env)
  [[ -f "${root}/glance.env" ]] && compose_cmd+=(--env-file glance.env)
  [[ -f "${root}/pihole.env" ]] && compose_cmd+=(--env-file pihole.env)
  [[ -f "${root}/compose-images.envvars" ]] && compose_cmd+=(--env-file compose-images.envvars)
  compose_cmd+=(config)
  (cd "${root}" && "${compose_cmd[@]}" >/dev/null)
done
