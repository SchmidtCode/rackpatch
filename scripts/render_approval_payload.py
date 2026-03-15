#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

OPS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OPS_ROOT / "app"))

from common import site, stack_catalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Render approval payload from the stack catalog.')
    parser.add_argument('--stacks', default=str(site.stacks_path()))
    parser.add_argument('--window', default='approved_guest_container')
    parser.add_argument('--event-file')
    args = parser.parse_args()

    del args.stacks
    stack_catalog_payload = {"stacks": stack_catalog.load_stack_catalog()}
    event_payload = None
    if args.event_file:
        with Path(args.event_file).open('r', encoding='utf-8') as handle:
            event_payload = json.load(handle)

    selected = []
    for stack in stack_catalog_payload['stacks']:
        if args.window == 'discovery' or stack['update_mode'] == args.window or stack['name'] in (event_payload or {}).get('approved_services', []):
            selected.append(
                {
                    'name': stack['name'],
                    'host': stack['host'],
                    'risk': stack['risk'],
                    'update_mode': stack['update_mode'],
                    'backup_before': stack['backup_before'],
                    'snapshot_before': stack['snapshot_before'],
                    'healthcheck': stack['healthcheck'],
                }
            )

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'window': args.window,
        'approved_services': [stack['name'] for stack in selected],
        'rollback_required': any(stack['snapshot_before'] for stack in selected),
        'stacks': selected,
    }
    if event_payload:
        payload['source_event'] = event_payload

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
