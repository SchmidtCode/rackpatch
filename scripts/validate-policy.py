#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

import yaml


OPS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS_ROOT / "app"))

from common import site  # noqa: E402


with site.stacks_path().open("r", encoding="utf-8") as handle:
    data = yaml.safe_load(handle) or {}

required = {
    "name",
    "host",
    "risk",
    "update_mode",
    "image_strategy",
    "healthcheck",
    "backup_before",
    "snapshot_before",
    "stop_order",
    "start_order",
}
allowed_modes = {"auto-windowed", "approve"}

errors = []
for stack in data.get("stacks", []):
    missing = required.difference(stack)
    if missing:
        errors.append(f"{stack.get('name', '<unknown>')}: missing keys {sorted(missing)}")
    if not stack.get("path") and not stack.get("project_dir"):
        errors.append(f"{stack.get('name', '<unknown>')}: either path or project_dir is required")
    if stack.get("update_mode") not in allowed_modes:
        errors.append(f"{stack.get('name', '<unknown>')}: invalid update_mode {stack.get('update_mode')}")
    if stack.get("risk") == "high" and stack.get("update_mode") != "approve":
        errors.append(f"{stack.get('name', '<unknown>')}: high-risk stacks must stay approve-gated")
    if stack.get("snapshot_before") and not stack.get("backup_before") and stack.get("risk") == "high":
        errors.append(f"{stack.get('name', '<unknown>')}: high-risk snapshot stacks should also request backups")
    if "compose_env_files" not in stack:
        errors.append(f"{stack.get('name', '<unknown>')}: compose_env_files is required")
    elif not isinstance(stack.get("compose_env_files"), list):
        errors.append(f"{stack.get('name', '<unknown>')}: compose_env_files must be a list")

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)

print(f"validated {len(data.get('stacks', []))} stack policies from {site.stacks_path()}")
