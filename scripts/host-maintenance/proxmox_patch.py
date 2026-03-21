#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
from pathlib import Path


BACKUP_ROOT = Path(
    os.environ.get("RACKPATCH_HOST_PROXMOX_BACKUP_DIR", "/var/lib/rackpatch-host-helper/artifacts")
)
CLUSTER_CONFIG = Path("/etc/pve")


def _run(command: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    stdout = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
    return result.returncode, stdout


def _pending_packages() -> tuple[int, str, list[str]]:
    rc, stdout = _run(["sh", "-lc", "apt list --upgradable 2>/dev/null | tail -n +2"])
    packages = [line.strip() for line in stdout.splitlines() if line.strip()]
    return rc, stdout, packages


def _backup_cluster_config(node_name: str) -> tuple[int, str, dict[str, object] | None]:
    if not CLUSTER_CONFIG.exists():
        return 1, "This host does not look like a Proxmox node because /etc/pve is missing.", None

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    archive_path = BACKUP_ROOT / f"{node_name}-etc-pve-{stamp}.tgz"
    rc, stdout = _run(["tar", "-C", "/etc", "-czf", str(archive_path), "pve"])
    artifact = {
        "kind": "backup",
        "target_ref": node_name,
        "path": str(archive_path),
        "source": "proxmox_patch",
    }
    return rc, stdout, artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch a Proxmox node through the rackpatch host helper.")
    parser.add_argument("--dry-run", action="store_true", help="Preview package state without applying changes.")
    args = parser.parse_args()

    node_name = socket.gethostname()
    if not CLUSTER_CONFIG.exists():
        payload = {
            "exit_code": 1,
            "dry_run": args.dry_run,
            "node": node_name,
            "stdout": "This host does not look like a Proxmox node because /etc/pve is missing.",
            "artifacts": [],
        }
        print(json.dumps(payload))
        return 1
    check_rc, check_stdout, packages = _pending_packages()

    if args.dry_run:
        payload = {
            "exit_code": check_rc,
            "dry_run": True,
            "node": node_name,
            "packages": packages,
            "package_count": len(packages),
            "reboot_required": os.path.exists("/var/run/reboot-required"),
            "stdout": "\n".join(
                line
                for line in [
                    "Dry run requested. Reporting pending Proxmox package state without applying changes.",
                    check_stdout,
                    "No /etc/pve backup was created in dry-run mode.",
                ]
                if line
            ).strip(),
        }
        print(json.dumps(payload))
        return check_rc

    backup_rc, backup_stdout, artifact = _backup_cluster_config(node_name)
    output_lines = []
    if backup_stdout:
        output_lines.append(backup_stdout)
    if backup_rc != 0:
        payload = {
            "exit_code": backup_rc,
            "dry_run": False,
            "node": node_name,
            "reboot_required": os.path.exists("/var/run/reboot-required"),
            "stdout": "\n".join(
                line
                for line in [
                    "Failed to archive /etc/pve before patching.",
                    *output_lines,
                ]
                if line
            ).strip(),
            "artifacts": [artifact] if artifact else [],
        }
        print(json.dumps(payload))
        return backup_rc

    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    exit_code = 0
    for command in (
        ["apt-get", "update"],
        ["apt-get", "dist-upgrade", "-y"],
        ["apt-get", "autoremove", "-y"],
    ):
        rc, stdout = _run(command, env=env)
        if stdout:
            output_lines.append(stdout)
        if rc != 0:
            exit_code = rc
            break

    payload = {
        "exit_code": exit_code,
        "dry_run": False,
        "node": node_name,
        "reboot_required": os.path.exists("/var/run/reboot-required"),
        "stdout": "\n".join(part for part in output_lines if part).strip(),
        "artifacts": [artifact] if artifact else [],
    }
    print(json.dumps(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
