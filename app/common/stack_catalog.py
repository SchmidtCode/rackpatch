from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import re

import yaml

from common import agents as agent_records, config


DEFAULT_DISCOVERED_RISK = "medium"
DEFAULT_DISCOVERED_UPDATE_MODE = "approve"
DISCOVERED_ORDER_BASE = 1000
DISCOVERED_ORDER_STEP = 10
LOCAL_HOST_KEYS = {"localhost", "127.0.0.1", "::1"}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def defined_stacks_path() -> Path:
    return config.resolve_runtime_path(config.SITE_ROOT) / "stacks.yml"


def load_defined_stacks(path: Path | None = None) -> list[dict[str, Any]]:
    return _load_yaml(path or defined_stacks_path()).get("stacks", [])


def _sanitize_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "discovered-stack"


def _host_key(value: str) -> str:
    return str(value or "localhost").strip() or "localhost"


def _path_key(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def stack_project_dir(stack: dict[str, Any] | None) -> str:
    if not stack:
        return ""
    return _path_key(stack.get("project_dir") or stack.get("path"))


def stack_runtime_host(stack: dict[str, Any] | None) -> str:
    if not stack:
        return "localhost"
    host = _host_key(stack.get("host"))
    guest_host = _host_key(stack.get("guest_host"))
    if host in LOCAL_HOST_KEYS and guest_host not in LOCAL_HOST_KEYS:
        return guest_host
    return host


def _defined_stack_keys(stacks: list[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for stack in stacks:
        keys.add((stack_runtime_host(stack), stack_project_dir(stack)))
    return keys


def _healthcheck_target(project: dict[str, Any]) -> str:
    services = project.get("services") or []
    if services:
        primary = services[0]
        return str(primary.get("container_name") or primary.get("service") or project.get("project_name") or "unknown")
    return str(project.get("project_name") or "unknown")


def _discovered_image_strategy(project: dict[str, Any]) -> str:
    compose_env_files = [str(item).strip() for item in (project.get("compose_env_files") or []) if str(item).strip()]
    return "env-ref" if compose_env_files else "compose-default"


def _iter_agent_projects() -> list[dict[str, Any]]:
    try:
        from common import db

        rows = db.fetch_all("SELECT name, status, last_seen_at, metadata FROM agents ORDER BY name")
    except Exception:  # noqa: BLE001
        return []

    discovered: list[dict[str, Any]] = []
    for row in rows:
        row = agent_records.with_effective_status(row)
        metadata = row.get("metadata") or {}
        docker_meta = metadata.get("docker") or {}
        compose_projects = docker_meta.get("compose_projects") or []
        for project in compose_projects:
            project_dir = _path_key(project.get("project_dir"))
            project_name = str(project.get("project_name") or "").strip()
            if not project_dir or not project_name:
                continue
            discovered.append(
                {
                    "host": _host_key(row.get("name")),
                    "agent_status": str(row.get("status") or "unknown"),
                    "project_name": project_name,
                    "project_dir": project_dir,
                    "config_files": list(project.get("config_files") or []),
                    "compose_env_files": list(project.get("compose_env_files") or []),
                    "services": list(project.get("services") or []),
                }
            )
    return discovered


def _assign_discovered_names(projects: list[dict[str, Any]], defined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used_names = {str(stack.get("name") or "").strip() for stack in defined if stack.get("name")}
    name_counts = Counter(_sanitize_name(project.get("project_name") or "") for project in projects)
    named_projects: list[dict[str, Any]] = []

    for project in projects:
        base_name = _sanitize_name(project.get("project_name") or "")
        if name_counts[base_name] > 1 or base_name in used_names:
            base_name = _sanitize_name(f"{base_name}-{project['host']}")
        candidate = base_name
        suffix = 2
        while candidate in used_names:
            candidate = f"{base_name}-{suffix}"
            suffix += 1
        used_names.add(candidate)
        named_projects.append({**project, "stack_name": candidate})
    return named_projects


def load_discovered_stacks(defined: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    defined = defined or load_defined_stacks()
    defined_keys = _defined_stack_keys(defined)
    raw_projects = sorted(
        _iter_agent_projects(),
        key=lambda project: (project["host"], project["project_name"], project["project_dir"]),
    )
    filtered_projects = [
        project
        for project in raw_projects
        if (_host_key(project["host"]), _path_key(project["project_dir"])) not in defined_keys
    ]
    named_projects = _assign_discovered_names(filtered_projects, defined)

    discovered_stacks: list[dict[str, Any]] = []
    for index, project in enumerate(named_projects):
        order = DISCOVERED_ORDER_BASE + (index * DISCOVERED_ORDER_STEP)
        discovered_stacks.append(
            {
                "name": project["stack_name"],
                "host": project["host"],
                "guest_host": project["host"],
                "path": project["project_dir"],
                "project_dir": project["project_dir"],
                "compose_env_files": project.get("compose_env_files", []),
                "risk": DEFAULT_DISCOVERED_RISK,
                "update_mode": DEFAULT_DISCOVERED_UPDATE_MODE,
                "image_strategy": _discovered_image_strategy(project),
                "healthcheck": {
                    "type": "container",
                    "target": _healthcheck_target(project),
                },
                "backup_before": False,
                "snapshot_before": False,
                "stop_order": order,
                "start_order": order,
                "catalog_source": "discovered",
                "project_name": project["project_name"],
                "config_files": project.get("config_files", []),
                "discovered_services": project.get("services", []),
                "agent_status": project.get("agent_status", "unknown"),
            }
        )
    return discovered_stacks


def load_stack_catalog(include_discovered: bool = True) -> list[dict[str, Any]]:
    defined = load_defined_stacks()
    if not include_discovered:
        return defined
    return defined + load_discovered_stacks(defined)


def find_stack(name: str, include_discovered: bool = True) -> dict[str, Any] | None:
    for stack in load_stack_catalog(include_discovered=include_discovered):
        if stack.get("name") == name:
            return stack
    return None
