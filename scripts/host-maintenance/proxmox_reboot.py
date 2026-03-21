#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path


SCRIPT_DIR = Path(os.environ.get("RACKPATCH_HOST_PROXMOX_REBOOT_SCRIPT_DIR", "/root"))
LOG_DIR = Path(os.environ.get("RACKPATCH_HOST_PROXMOX_REBOOT_LOG_DIR", "/var/log"))
CLUSTER_CONFIG = Path("/etc/pve")


def _shell_log_function() -> list[str]:
    return [
        'log() {',
        '  echo "[$(date --iso-8601=seconds)] $*";',
        '}',
    ]


def _wait_helpers() -> list[str]:
    return [
        'wait_for_qemu_stop() {',
        '  local vmid="$1"',
        '  local deadline=$((SECONDS + 600))',
        '  while (( SECONDS < deadline )); do',
        '    if qm status "$vmid" | grep -q "status: stopped"; then',
        '      return 0',
        '    fi',
        '    sleep 5',
        '  done',
        '  return 1',
        '}',
        '',
        'wait_for_lxc_stop() {',
        '  local vmid="$1"',
        '  local deadline=$((SECONDS + 600))',
        '  while (( SECONDS < deadline )); do',
        '    if pct status "$vmid" | grep -q "status: stopped"; then',
        '      return 0',
        '    fi',
        '    sleep 5',
        '  done',
        '  return 1',
        '}',
    ]


def _soft_script(log_path: Path, guest_order: list[str]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"exec >>{shlex.quote(str(log_path))} 2>&1",
        "",
        *_shell_log_function(),
        "",
        *_wait_helpers(),
        "",
        'log "soft reboot scheduled by rackpatch host helper"',
        f'log "guest shutdown order: {" ".join(guest_order) if guest_order else "(none configured)"}"',
    ]
    if guest_order:
        for guest_id in guest_order:
            quoted_guest_id = shlex.quote(guest_id)
            qemu_stop_guard = (
                f'  wait_for_qemu_stop {quoted_guest_id} || '
                f'{{ log "guest {guest_id} did not stop cleanly; aborting host reboot"; exit 1; }}'
            )
            lxc_stop_guard = (
                f'  wait_for_lxc_stop {quoted_guest_id} || '
                f'{{ log "guest {guest_id} did not stop cleanly; aborting host reboot"; exit 1; }}'
            )
            lines.extend(
                [
                    f'if qm config {quoted_guest_id} >/dev/null 2>&1; then',
                    f'  log "shutting down qemu guest {guest_id}"',
                    f"  qm shutdown {quoted_guest_id} --timeout 600 || true",
                    qemu_stop_guard,
                    f'elif pct config {quoted_guest_id} >/dev/null 2>&1; then',
                    f'  log "shutting down lxc guest {guest_id}"',
                    f"  pct shutdown {quoted_guest_id} --timeout 600 || true",
                    lxc_stop_guard,
                    "else",
                    f'  log "guest {guest_id} is not present on this node; skipping"',
                    "fi",
                ]
            )
    else:
        lines.append('log "no guest shutdown order configured; rebooting node directly after grace period"')
    lines.extend(
        [
            'log "all configured guests stopped; rebooting node"',
            "sleep 5",
            "systemctl reboot",
            "",
        ]
    )
    return "\n".join(lines)


def _hard_script(log_path: Path) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"exec >>{shlex.quote(str(log_path))} 2>&1",
            "",
            *_shell_log_function(),
            "",
            'log "hard reboot scheduled by rackpatch host helper"',
            'log "warning: guests on this node will be hard-interrupted"',
            "sleep 10",
            "systemctl reboot",
            "",
        ]
    )


def _write_script(script_path: Path, content: str) -> None:
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script_path.write_text(content, encoding="utf-8")
    os.chmod(script_path, 0o700)


def _schedule(script_path: Path) -> None:
    with Path(os.devnull).open("r", encoding="utf-8") as stdin_handle:
        with Path(os.devnull).open("a", encoding="utf-8") as stdout_handle:
            subprocess.Popen(
                ["bash", str(script_path)],
                stdin=stdin_handle,
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )


def _normalize_guest_order(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    guest_order: list[str] = []
    for value in values:
        if not value.isdigit():
            raise ValueError("guest_order entries must be numeric VMIDs")
        guest_order.append(value)
    return guest_order


def main() -> int:
    parser = argparse.ArgumentParser(description="Schedule a Proxmox node reboot through the rackpatch host helper.")
    parser.add_argument("--dry-run", action="store_true", help="Preview the reboot plan without scheduling it.")
    parser.add_argument("--reboot-mode", choices=["soft", "hard"], default="soft")
    parser.add_argument("--guest-order", default="", help="Comma-separated VMID shutdown order for soft reboots.")
    args = parser.parse_args()

    node_name = socket.gethostname()
    if not CLUSTER_CONFIG.exists():
        payload = {
            "exit_code": 1,
            "dry_run": args.dry_run,
            "node": node_name,
            "reboot_mode": args.reboot_mode,
            "guest_order": [],
            "stdout": "This host does not look like a Proxmox node because /etc/pve is missing.",
            "artifacts": [],
        }
        print(json.dumps(payload))
        return 1
    guest_order = _normalize_guest_order(args.guest_order)
    if args.dry_run:
        payload = {
            "exit_code": 0,
            "dry_run": True,
            "node": node_name,
            "reboot_mode": args.reboot_mode,
            "guest_order": guest_order,
            "stdout": "\n".join(
                [
                    "Dry run requested. No Proxmox reboot was scheduled.",
                    f"reboot_mode={args.reboot_mode}",
                    f"guest_order={' '.join(guest_order) if guest_order else '(none configured)'}",
                ]
            ),
        }
        print(json.dumps(payload))
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    script_path = SCRIPT_DIR / f"rackpatch-proxmox-reboot-{args.reboot_mode}-{stamp}.sh"
    log_path = LOG_DIR / f"rackpatch-proxmox-reboot-{args.reboot_mode}-{stamp}.log"
    content = _soft_script(log_path, guest_order) if args.reboot_mode == "soft" else _hard_script(log_path)

    _write_script(script_path, content)
    _schedule(script_path)

    payload = {
        "exit_code": 0,
        "dry_run": False,
        "node": node_name,
        "reboot_mode": args.reboot_mode,
        "guest_order": guest_order,
        "stdout": "\n".join(
            [
                f"Scheduled {args.reboot_mode} Proxmox reboot for {node_name}.",
                f"script={script_path}",
                f"log={log_path}",
            ]
        ),
        "artifacts": [
            {
                "kind": "reboot-plan",
                "target_ref": node_name,
                "path": str(script_path),
                "source": "proxmox_reboot",
            },
            {
                "kind": "reboot-log",
                "target_ref": node_name,
                "path": str(log_path),
                "source": "proxmox_reboot",
            },
        ],
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
