#!/usr/bin/env python3
import os
import sys
from pathlib import Path

import yaml

STACKS_PATH = Path(os.environ.get('OPS_STACKS_FILE', Path(__file__).resolve().parent.parent / 'config' / 'stacks.yml'))

with STACKS_PATH.open('r', encoding='utf-8') as handle:
    data = yaml.safe_load(handle)

required = {'name', 'host', 'risk', 'update_mode', 'image_strategy', 'healthcheck', 'backup_before', 'snapshot_before', 'stop_order', 'start_order'}
allowed_modes = {'auto-windowed', 'approve'}

errors = []
for stack in data.get('stacks', []):
    missing = required.difference(stack)
    if missing:
        errors.append(f"{stack.get('name', '<unknown>')}: missing keys {sorted(missing)}")
    if not stack.get('path') and not stack.get('project_dir'):
        errors.append(f"{stack.get('name', '<unknown>')}: either path or project_dir is required")
    if stack.get('update_mode') not in allowed_modes:
        errors.append(f"{stack.get('name', '<unknown>')}: invalid update_mode {stack.get('update_mode')}")
    if stack.get('risk') == 'high' and stack.get('update_mode') != 'approve':
        errors.append(f"{stack.get('name', '<unknown>')}: high-risk stacks must stay approve-gated")
    if stack.get('snapshot_before') and not stack.get('backup_before') and stack.get('risk') == 'high':
        errors.append(f"{stack.get('name', '<unknown>')}: high-risk snapshot stacks should also request backups")
    if 'compose_env_files' not in stack or not stack.get('compose_env_files'):
        errors.append(f"{stack.get('name', '<unknown>')}: compose_env_files is required so image refs stay tracked")

if errors:
    print('\n'.join(errors), file=sys.stderr)
    raise SystemExit(1)

print(f"validated {len(data.get('stacks', []))} stack policies")
