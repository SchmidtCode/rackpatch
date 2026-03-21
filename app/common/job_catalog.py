from __future__ import annotations

from copy import deepcopy
from typing import Any


JOB_KIND_DEFINITIONS: list[dict[str, Any]] = [
    {
        "kind": "docker_update",
        "label": "Docker update",
        "mode": "stack_multi",
        "target_type": "stack",
        "summary": "Select one or more stacks to update through enrolled Docker-capable agents.",
        "defaults": {
            "executor": "agent",
            "window": "all",
            "dry_run": True,
            "requires_approval": False,
        },
        "default_select_all": True,
        "fields": [
            {
                "name": "dry_run",
                "type": "toggle",
                "label": "Dry run",
                "hint": "Validate compose configuration on the target agent without pulling images or restarting services.",
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Hold the job for approval before it starts running.",
            },
        ],
    },
    {
        "kind": "package_check",
        "label": "Package check",
        "mode": "host_multi",
        "target_type": "host",
        "summary": "Choose one or more guest or Docker hosts to inspect for package updates. Each selected host queues its own helper-backed agent job.",
        "special_access": {
            "required_capability": "host-package-check",
            "label": "limited host-maintenance helper",
            "short_label": "Requires limited host-maintenance helper access.",
            "summary": "Requires the limited host-maintenance helper on each selected host.",
            "missing_detail": "Enable the limited host-maintenance helper on the host agent to use package checks from the UI.",
        },
        "defaults": {
            "executor": "agent",
            "requires_approval": False,
        },
        "host_groups_exclude": ["proxmox_nodes"],
        "fields": [],
    },
    {
        "kind": "package_patch",
        "label": "Package patch",
        "mode": "host_multi",
        "target_type": "host",
        "summary": "Choose one or more guest or Docker hosts to patch. Each selected host queues its own helper-backed agent job.",
        "special_access": {
            "required_capability": "host-package-patch",
            "label": "limited host-maintenance helper",
            "short_label": "Requires limited host-maintenance patch access.",
            "summary": "Requires the limited host-maintenance helper with package patch access on each selected host.",
            "missing_detail": "Enable the limited host-maintenance helper with package patch access on the host agent to use package patching from the UI.",
        },
        "defaults": {
            "executor": "agent",
            "dry_run": True,
            "requires_approval": False,
        },
        "host_groups_exclude": ["proxmox_nodes"],
        "fields": [
            {
                "name": "dry_run",
                "type": "toggle",
                "label": "Dry run",
                "hint": "Preview package patching and prechecks without making changes.",
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Hold the patch job until someone approves it.",
            },
        ],
    },
    {
        "kind": "backup",
        "label": "Backup",
        "mode": "manual",
        "target_type": "volume",
        "manual_label": "Volume",
        "manual_placeholder": "volume name",
        "summary": "Enter the Docker volume name to back up.",
        "defaults": {
            "executor": "worker",
        },
        "fields": [
            {
                "name": "output_name",
                "type": "text",
                "label": "Archive name",
                "hint": "Optional. Leave blank to use the volume name with a .tgz suffix.",
                "placeholder": "volume-backup.tgz",
                "optional": True,
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Pause the backup job for approval before it runs.",
            },
        ],
    },
    {
        "kind": "rollback",
        "label": "Rollback stack",
        "mode": "stack_single",
        "target_type": "stack",
        "summary": "Select exactly one stack to roll back from the control-plane host.",
        "defaults": {
            "executor": "worker",
            "requires_approval": True,
        },
        "fields": [
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Keep rollback behind approval before it is released to the worker.",
            },
        ],
    },
]

JOB_KIND_INDEX = {item["kind"]: item for item in JOB_KIND_DEFINITIONS}


def list_job_kinds() -> list[dict[str, Any]]:
    return deepcopy(JOB_KIND_DEFINITIONS)


def get_job_kind(kind: str) -> dict[str, Any] | None:
    item = JOB_KIND_INDEX.get(kind)
    return deepcopy(item) if item else None


def known_job_kinds() -> set[str]:
    return set(JOB_KIND_INDEX)
