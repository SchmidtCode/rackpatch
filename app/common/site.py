from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from common import config


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _resolve_workspace_path(path: Path) -> Path:
    if path.exists():
        return path
    try:
        relative = path.relative_to("/workspace")
    except ValueError:
        return path
    candidate = REPO_ROOT / relative
    return candidate if candidate.exists() else path


def site_root() -> Path:
    return _resolve_workspace_path(config.SITE_ROOT)


def site_name() -> str:
    return config.SITE_NAME


def inventory_path() -> Path:
    return site_root() / "inventory" / "hosts.yml"


def stacks_path() -> Path:
    return site_root() / "stacks.yml"


def maintenance_path() -> Path:
    return site_root() / "maintenance.yml"


def group_vars_path() -> Path:
    return site_root() / "inventory" / "group_vars" / "all.yml"


def load_defined_stacks() -> list[dict[str, Any]]:
    return _load_yaml(stacks_path()).get("stacks", [])


def load_stacks() -> list[dict[str, Any]]:
    from common import stack_catalog

    return stack_catalog.load_stack_catalog()


def find_stack(name: str) -> dict[str, Any] | None:
    from common import stack_catalog

    return stack_catalog.find_stack(name)


def load_group_vars() -> dict[str, Any]:
    return _load_yaml(group_vars_path())


def load_maintenance() -> dict[str, Any]:
    return _load_yaml(maintenance_path())


def load_hosts() -> list[dict[str, Any]]:
    inventory = _load_yaml(inventory_path())
    results: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], group_name: str) -> None:
        hosts = node.get("hosts") or {}
        for name, data in hosts.items():
            host_data = dict(data or {})
            host_data["name"] = name
            host_data["group"] = group_name
            results.append(host_data)
        for child_name, child_data in (node.get("children") or {}).items():
            if isinstance(child_data, dict):
                walk(child_data, child_name)

    walk(inventory.get("all") or {}, "all")
    deduped: dict[str, dict[str, Any]] = {}
    for item in results:
        deduped[item["name"]] = item
    return list(deduped.values())


def default_schedules() -> list[dict[str, Any]]:
    windows = load_group_vars().get("default_windows", {})
    stack_names = [stack["name"] for stack in load_defined_stacks() if stack.get("name")]
    return [
        {
            "name": "Docker Image Discovery",
            "kind": "docker_discover",
            "cron_expr": windows.get("docker_discovery", windows.get("discovery", "0 5 * * *")),
            "payload": {
                "executor": "worker",
                "window": "all",
                "requires_approval": False,
                "notify": True,
                "notify_on": ["completed", "failed"],
            },
        },
        {
            "name": "Host Package Check",
            "kind": "package_check",
            "cron_expr": windows.get("host_package_check", "15 5 * * *"),
            "payload": {
                "executor": "worker",
                "scope": "all",
                "requires_approval": False,
                "notify": True,
                "notify_on": ["completed", "failed"],
            },
        },
        {
            "name": "Guest OS Patch Approval",
            "kind": "package_patch",
            "cron_expr": windows.get("guest_patch_approval", windows.get("approved_guest_container", "0 4 * * 6")),
            "payload": {
                "executor": "worker",
                "target_ref": "guests",
                "limit": "guests",
                "dry_run": False,
                "requires_approval": True,
                "notify": True,
            },
        },
        {
            "name": "Docker Stack Update Approval",
            "kind": "docker_update",
            "cron_expr": windows.get("docker_update_approval", windows.get("docker_auto", "30 5 * * 6")),
            "payload": {
                "executor": "worker",
                "target_ref": "full-stack-catalog",
                "selected_stacks": stack_names,
                "dry_run": False,
                "requires_approval": True,
                "notify": True,
            },
        },
        {
            "name": "Proxmox Node Patch Approval",
            "kind": "proxmox_patch",
            "cron_expr": windows.get("proxmox_patch_approval", windows.get("proxmox_nodes", "30 4 * * 0")),
            "payload": {
                "executor": "worker",
                "target_ref": "proxmox_nodes",
                "limit": "proxmox_nodes",
                "dry_run": False,
                "requires_approval": True,
                "notify": True,
            },
        },
    ]
