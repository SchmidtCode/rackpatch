from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
import yaml

from common import config


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def site_root() -> Path:
    return config.resolve_runtime_path(config.SITE_ROOT)


def site_name() -> str:
    return config.SITE_NAME


def inventory_path() -> Path:
    return site_root() / "inventory" / "hosts.yml"


def load_inventory() -> dict[str, Any]:
    return _load_yaml(inventory_path())


def save_inventory(inventory: dict[str, Any]) -> None:
    path = inventory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(inventory, handle, sort_keys=False)


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


def normalize_timezone_name(value: Any, *, fallback: Any = "UTC") -> str:
    candidate = str(value or "").strip()
    fallback_name = str(fallback or "").strip()
    if not candidate:
        candidate = fallback_name or "UTC"
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        if fallback_name and fallback_name != candidate:
            return normalize_timezone_name(fallback_name, fallback="UTC")
        return "UTC"
    return candidate


def maintenance_timezone_name() -> str:
    return normalize_timezone_name(load_group_vars().get("maintenance_timezone"), fallback="UTC")


def schedule_timezone_name(value: Any = None) -> str:
    return normalize_timezone_name(value, fallback=maintenance_timezone_name())


def schedule_next_run(cron_expr: str, *, timezone_name: Any = None, base: datetime | None = None) -> datetime:
    current = base or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(schedule_timezone_name(timezone_name))
    next_run = croniter(cron_expr, current.astimezone(zone)).get_next(datetime)
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=zone)
    return next_run.astimezone(timezone.utc)


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


def find_host(name: str) -> dict[str, Any] | None:
    desired = str(name or "").strip()
    if not desired:
        return None
    for item in load_hosts():
        if str(item.get("name") or "").strip() == desired:
            return item
    return None


def load_groups() -> list[str]:
    inventory = load_inventory()
    groups: list[str] = []

    def walk(node: dict[str, Any], group_name: str) -> None:
        if group_name not in groups:
            groups.append(group_name)
        for child_name, child_data in (node.get("children") or {}).items():
            if isinstance(child_data, dict):
                walk(child_data, child_name)

    walk(inventory.get("all") or {}, "all")
    return groups


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ensure_inventory_root(inventory: dict[str, Any]) -> dict[str, Any]:
    all_node = _as_mapping(inventory.get("all"))
    inventory["all"] = all_node
    return all_node


def _find_group_node(node: dict[str, Any], group_name: str) -> dict[str, Any] | None:
    for child_name, child_data in (_as_mapping(node.get("children"))).items():
        if child_name == group_name and isinstance(child_data, dict):
            return child_data
        if isinstance(child_data, dict):
            match = _find_group_node(child_data, group_name)
            if match is not None:
                return match
    return None


def _ensure_group_node(inventory: dict[str, Any], group_name: str) -> dict[str, Any]:
    all_node = _ensure_inventory_root(inventory)
    if group_name == "all":
        return all_node
    existing = _find_group_node(all_node, group_name)
    if existing is not None:
        return existing
    children = _as_mapping(all_node.get("children"))
    all_node["children"] = children
    children[group_name] = {}
    return children[group_name]


def _remove_host_from_node(node: dict[str, Any], host_name: str) -> bool:
    removed = False
    hosts = _as_mapping(node.get("hosts"))
    if host_name in hosts:
        del hosts[host_name]
        removed = True
    if hosts:
        node["hosts"] = hosts
    elif "hosts" in node:
        node.pop("hosts", None)
    for child_data in _as_mapping(node.get("children")).values():
        if isinstance(child_data, dict) and _remove_host_from_node(child_data, host_name):
            removed = True
    return removed


def upsert_host(original_name: str, host_name: str, group_name: str, data: dict[str, Any]) -> dict[str, Any]:
    inventory = load_inventory()
    all_node = _ensure_inventory_root(inventory)
    desired_name = str(host_name or "").strip()
    desired_group = str(group_name or "").strip() or "all"
    if not desired_name:
        raise ValueError("host name is required")
    if original_name and desired_name != original_name and find_host(desired_name) is not None:
        raise ValueError(f"host {desired_name} already exists")
    _remove_host_from_node(all_node, str(original_name or "").strip() or desired_name)
    group_node = _ensure_group_node(inventory, desired_group)
    hosts = _as_mapping(group_node.get("hosts"))
    group_node["hosts"] = hosts
    hosts[desired_name] = dict(data or {})
    save_inventory(inventory)
    updated = find_host(desired_name)
    if updated is None:
        raise ValueError(f"failed to write host {desired_name}")
    return updated


def delete_host(host_name: str) -> bool:
    inventory = load_inventory()
    all_node = _ensure_inventory_root(inventory)
    removed = _remove_host_from_node(all_node, str(host_name or "").strip())
    if removed:
        save_inventory(inventory)
    return removed


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
    timezone_name = maintenance_timezone_name()
    stack_names = [stack["name"] for stack in load_defined_stacks() if stack.get("name")]
    return [
        {
            "name": "Daily Docker Stack Check",
            "kind": "docker_check",
            "cron_expr": windows.get("docker_check_daily", "45 5 * * *"),
            "timezone": timezone_name,
            "payload": {
                "executor": "agent",
                "target_ref": "full-stack-catalog",
                "selected_stacks": stack_names,
                "requires_approval": False,
                "notify": False,
            },
        },
        {
            "name": "Host Package Check",
            "kind": "package_check",
            "cron_expr": windows.get("host_package_check", "15 5 * * *"),
            "timezone": timezone_name,
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
            "timezone": timezone_name,
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
            "timezone": timezone_name,
            "payload": {
                "executor": "agent",
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
            "timezone": timezone_name,
            "payload": {
                "executor": "agent",
                "target_ref": "proxmox_nodes",
                "limit": "proxmox_nodes",
                "dry_run": False,
                "requires_approval": True,
                "notify": True,
            },
        },
    ]
