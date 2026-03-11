#!/usr/bin/env bash
set -euo pipefail

stacks_file="${OPS_STACKS_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/stacks.yml}"

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
  compose_cmd=("$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/compose-wrapper.sh")
  [[ -f "${root}/.env" ]] && compose_cmd+=(--env-file .env)
  [[ -f "${root}/glance.env" ]] && compose_cmd+=(--env-file glance.env)
  [[ -f "${root}/pihole.env" ]] && compose_cmd+=(--env-file pihole.env)
  [[ -f "${root}/compose-images.envvars" ]] && compose_cmd+=(--env-file compose-images.envvars)
  compose_cmd+=(config)
  (cd "${root}" && "${compose_cmd[@]}" >/dev/null)
done
