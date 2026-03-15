from __future__ import annotations

from copy import deepcopy
from typing import Any


JOB_KIND_DEFINITIONS: list[dict[str, Any]] = [
    {
        "kind": "docker_discover",
        "label": "Docker discover",
        "mode": "stack_multi",
        "target_type": "stack",
        "summary": "Select one or more stacks to inspect. Leave all selected to discover everything.",
        "defaults": {
            "executor": "worker",
            "window": "all",
            "requires_approval": False,
        },
        "default_select_all": True,
        "fields": [
            {
                "name": "window",
                "type": "select",
                "label": "Discovery Window",
                "hint": "Choose whether to inspect every stack or only the configured maintenance windows.",
                "options": [
                    {"value": "all", "label": "All stacks"},
                    {"value": "approve", "label": "Approved window"},
                    {"value": "auto-windowed", "label": "Low-risk window"},
                ],
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Pause this discovery job for approval before it is queued.",
            },
        ],
    },
    {
        "kind": "docker_update",
        "label": "Docker update",
        "mode": "stack_multi",
        "target_type": "stack",
        "summary": "Select one or more stacks to update. Leave all selected to run across everything.",
        "defaults": {
            "executor": "auto",
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
                "hint": "Preview the Docker update without pulling images or restarting services.",
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
        "summary": "Choose one or more guest or Docker hosts to inspect for package updates.",
        "special_access": {
            "required_capability": "host-package-check",
            "label": "limited host-maintenance helper",
            "short_label": "Requires limited host-maintenance helper access.",
            "summary": "Requires the limited host-maintenance helper on each selected host.",
            "missing_detail": "Enable the limited host-maintenance helper on the host agent to use package checks from the UI.",
        },
        "defaults": {
            "executor": "auto",
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
        "summary": "Choose one or more guest or Docker hosts to patch.",
        "special_access": {
            "required_capability": "host-package-patch",
            "label": "limited host-maintenance helper",
            "short_label": "Requires limited host-maintenance patch access.",
            "summary": "Requires the limited host-maintenance helper with package patch access on each selected host.",
            "missing_detail": "Enable the limited host-maintenance helper with package patch access on the host agent to use package patching from the UI.",
        },
        "defaults": {
            "executor": "auto",
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
        "kind": "snapshot",
        "label": "Snapshot guest",
        "mode": "host_multi",
        "target_type": "host",
        "summary": "Choose one or more guest hosts to snapshot.",
        "defaults": {
            "executor": "worker",
            "requires_approval": False,
        },
        "host_groups_exclude": ["proxmox_nodes"],
        "fields": [
            {
                "name": "dry_run",
                "type": "toggle",
                "label": "Dry run",
                "hint": "Validate snapshot targets without creating a snapshot.",
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Pause the snapshot request for approval first.",
            },
        ],
    },
    {
        "kind": "proxmox_patch",
        "label": "Proxmox patch",
        "mode": "host_multi",
        "target_type": "host",
        "summary": "Choose one or more Proxmox nodes to patch.",
        "defaults": {
            "executor": "worker",
            "dry_run": True,
            "requires_approval": False,
        },
        "host_groups_include": ["proxmox_nodes"],
        "fields": [
            {
                "name": "dry_run",
                "type": "toggle",
                "label": "Dry run",
                "hint": "Preview package changes on the selected Proxmox nodes.",
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Queue the node patching job behind an approval step.",
            },
        ],
    },
    {
        "kind": "proxmox_reboot",
        "label": "Proxmox reboot",
        "mode": "host_multi",
        "target_type": "host",
        "summary": "Choose one or more Proxmox nodes to reboot.",
        "defaults": {
            "executor": "worker",
            "dry_run": True,
            "requires_approval": False,
        },
        "host_groups_include": ["proxmox_nodes"],
        "fields": [
            {
                "name": "dry_run",
                "type": "toggle",
                "label": "Dry run",
                "hint": "Show the reboot plan without scheduling the reboot.",
            },
            {
                "name": "requires_approval",
                "type": "toggle",
                "label": "Require approval",
                "hint": "Pause this reboot job until it is approved.",
            },
            {
                "name": "reboot_mode",
                "type": "select",
                "label": "Reboot mode",
                "hint": "Soft reboot tries the guest shutdown order first. Hard reboot restarts the node directly.",
                "options": [
                    {"value": "soft", "label": "Soft reboot"},
                    {"value": "hard", "label": "Hard reboot"},
                ],
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
        "summary": "Select exactly one stack to roll back.",
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
