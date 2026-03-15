#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

INVENTORY_FILE = Path(os.environ.get("RACKPATCH_INVENTORY_FILE", "/workspace/inventory/hosts.yml"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check managed guests and Proxmox nodes for package updates.")
    parser.add_argument("--scope", default="all", help="Scope to inspect: all, guests, docker_hosts, or proxmox.")
    parser.add_argument("--host", action="append", dest="hosts", default=[], help="Specific host name to inspect.")
    return parser.parse_args()


def run_json_command(command: list[str]) -> dict:
    result = subprocess.run(command, capture_output=True, text=True, check=False, env=os.environ.copy())
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(command)}")
    return json.loads(result.stdout)


def load_inventory() -> dict:
    return run_json_command(["ansible-inventory", "-i", str(INVENTORY_FILE), "--list"])


def resolve_hosts(inventory: dict, scope: str, requested_hosts: list[str]) -> list[str]:
    if requested_hosts:
        return requested_hosts

    def group_hosts(name: str, seen: set[str] | None = None) -> list[str]:
        seen = seen or set()
        if name in seen:
            return []
        seen.add(name)
        group = inventory.get(name) or {}
        selected: list[str] = list(group.get("hosts") or [])
        for child in group.get("children") or []:
            for host in group_hosts(child, seen):
                if host not in selected:
                    selected.append(host)
        return selected

    if scope == "all":
        selected: list[str] = []
        for host in group_hosts("proxmox_nodes") + group_hosts("guests"):
            if host not in selected:
                selected.append(host)
        return selected
    if scope == "guests":
        return group_hosts("guests")
    if scope == "docker_hosts":
        return group_hosts("docker_hosts")
    if scope in {"proxmox", "proxmox_nodes"}:
        return group_hosts("proxmox_nodes")
    return [scope]


def extract_module_stdout(stdout: str) -> str:
    if ">>" not in stdout:
        return stdout.strip()
    return stdout.split(">>", 1)[1].strip()


def build_group_map(inventory: dict) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name, value in inventory.items():
        if name.startswith("_") or not isinstance(value, dict):
            continue
        groups[name] = list(value.get("hosts") or [])
    return groups


def check_host(host: str, hostvars: dict, group_map: dict[str, list[str]]) -> dict:
    command = (
        "sh -lc 'apt list --upgradable 2>/dev/null | tail -n +2; "
        "printf \"__RACKPATCH_REBOOT__=%s\\n\" \"$(test -f /var/run/reboot-required && echo yes || echo no)\"'"
    )
    result = subprocess.run(
        ["ansible", host, "-i", str(INVENTORY_FILE), "-m", "shell", "-a", command],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )

    payload = {
        "name": host,
        "ansible_host": hostvars.get("ansible_host", host),
        "maintenance_tier": hostvars.get("maintenance_tier", "unknown"),
        "guest_type": hostvars.get(
            "guest_type",
            "node" if host in group_map.get("proxmox_nodes", []) else "guest",
        ),
        "status": "unknown",
        "package_count": 0,
        "reboot_required": False,
        "packages": [],
    }

    if result.returncode != 0:
        payload["status"] = "error"
        payload["error"] = result.stderr.strip() or result.stdout.strip() or "ansible package check failed"
        return payload

    body = extract_module_stdout(result.stdout)
    package_lines: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("__RACKPATCH_REBOOT__="):
            payload["reboot_required"] = line.split("=", 1)[1].strip().lower() == "yes"
            continue
        package_lines.append(line)

    payload["packages"] = package_lines
    payload["package_count"] = len(package_lines)
    if package_lines:
        payload["status"] = "outdated"
    elif payload["reboot_required"]:
        payload["status"] = "reboot-required"
    else:
        payload["status"] = "up-to-date"
    return payload


def main() -> int:
    args = parse_args()
    requested_hosts: list[str] = []
    for item in args.hosts:
        requested_hosts.extend(part.strip() for part in item.split(",") if part.strip())

    inventory = load_inventory()
    group_map = build_group_map(inventory)
    hostvars = inventory.get("_meta", {}).get("hostvars", {})
    selected_hosts = resolve_hosts(inventory, args.scope, requested_hosts)

    reports = [check_host(host, hostvars.get(host, {}), group_map) for host in selected_hosts]
    payload = {
        "scope": args.scope,
        "requested_hosts": requested_hosts,
        "host_count": len(reports),
        "hosts_outdated": sum(1 for item in reports if item["status"] == "outdated"),
        "reboot_hosts": sum(1 for item in reports if item["reboot_required"]),
        "total_packages": sum(item["package_count"] for item in reports),
        "hosts": reports,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
