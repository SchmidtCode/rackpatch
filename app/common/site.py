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


def load_inventory() -> dict[str, Any]:
    return _load_yaml(inventory_path())


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
    inventory = load_inventory()
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


def group_hosts(group_name: str) -> list[str]:
    inventory = load_inventory()
    root = inventory.get("all") or {}
    if group_name == "all":
        target_group = root
    else:
        target_group = None

        def find_group(node: dict[str, Any], desired: str) -> dict[str, Any] | None:
            for child_name, child_data in (node.get("children") or {}).items():
                if child_name == desired and isinstance(child_data, dict):
                    return child_data
                if isinstance(child_data, dict):
                    match = find_group(child_data, desired)
                    if match is not None:
                        return match
            return None

        target_group = find_group(root, group_name)

    if not isinstance(target_group, dict):
        return []

    def collect(node: dict[str, Any], selected: list[str]) -> None:
        for host_name in (node.get("hosts") or {}):
            if host_name not in selected:
                selected.append(host_name)
        for child_data in (node.get("children") or {}).values():
            if isinstance(child_data, dict):
                collect(child_data, selected)

    hosts: list[str] = []
    collect(target_group, hosts)
    return hosts


def default_schedules() -> list[dict[str, Any]]:
    windows = load_group_vars().get("default_windows", {})
    stack_names = [stack["name"] for stack in load_defined_stacks() if stack.get("name")]
    return [
        {
            "name": "Host Package Check",
            "kind": "package_check",
            "cron_expr": windows.get("host_package_check", "15 5 * * *"),
            "payload": {
                "executor": "agent",
                "scope": "guests",
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
                "executor": "agent",
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
                "executor": "agent",
                "target_ref": "full-stack-catalog",
                "selected_stacks": stack_names,
                "dry_run": False,
                "requires_approval": True,
                "notify": True,
            },
        },
    ]
