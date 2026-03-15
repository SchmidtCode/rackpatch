from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from common import config, jobs, site


def runtime_env() -> dict[str, str]:
    group_vars = site.load_group_vars()
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "ANSIBLE_CONFIG": "/workspace/ansible.cfg",
            "OPS_SITE_ROOT": str(site.site_root()),
            "OPS_STACKS_FILE": str(site.stacks_path()),
            "OPS_INVENTORY_FILE": str(site.inventory_path()),
            "OPS_ROLLBACK_ROOT": str(group_vars.get("rollback_root", "/data/rollbacks")),
        }
    )
    return env


def run_logged(job_id: str, command: list[str], cwd: str = "/workspace") -> dict[str, Any]:
    jobs.append_event(job_id, f"RUN {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=runtime_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output_lines: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        output_lines.append(line)
        jobs.append_event(job_id, line)
    rc = process.wait()
    return {"exit_code": rc, "stdout": "\n".join(output_lines)}


def artifacts_from_output(output: str) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        if "OPS_ARTIFACT " not in raw_line:
            continue
        line = raw_line.split("OPS_ARTIFACT ", 1)[1].strip().strip('"').strip(",")
        parts: dict[str, str] = {}
        for chunk in line.split():
            if "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            parts[key] = value
        if parts.get("kind") and parts.get("value"):
            artifacts.append(parts)
    return artifacts


def summarize_docker_update(payload: dict[str, Any], target_ref: str) -> dict[str, Any]:
    command = ["python3", "scripts/summarize_docker_update.py"]
    selected_stacks = list(payload.get("selected_stacks") or [])
    if selected_stacks:
        for stack_name in selected_stacks:
            command.extend(["--stack", stack_name])
    else:
        command.extend(["--window", payload.get("window", "approve")])

    result = subprocess.run(
        command,
        cwd="/workspace",
        env=runtime_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "docker update summary failed"
        raise RuntimeError(message)
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise RuntimeError("docker update summary returned a non-object payload")
    return parsed


def worker_command(kind: str, payload: dict[str, Any], target_ref: str) -> list[str]:
    inventory = str(site.inventory_path())
    stacks_file = str(site.stacks_path())
    dry_run = "true" if payload.get("dry_run", False) else "false"

    if kind == "docker_discover":
        command = ["python3", "scripts/check_stack_updates.py", "--window", payload.get("window", "all")]
        for name in payload.get("stacks", []):
            command.extend(["--stack", name])
        return command
    if kind == "docker_update":
        selected = json.dumps(
            payload.get("selected_stacks", [target_ref] if target_ref != "all" else []),
            separators=(",", ":"),
        )
        command = [
            "ansible-playbook",
            "playbooks/apply_docker_updates.yml",
            "-i",
            inventory,
            "-e",
            f"ops_stacks_file={stacks_file}",
            "-e",
            f"dry_run={dry_run}",
        ]
        if payload.get("selected_stacks"):
            command.extend(["-e", f"selected_stacks={selected}"])
        elif target_ref == "all":
            command.extend(["-e", f"target_window={payload.get('window', 'approve')}"])
        else:
            command.extend(["-e", f"selected_stacks={selected}"])
        return command
    if kind == "package_check":
        command = ["python3", "scripts/check_package_updates.py", "--scope", payload.get("scope", "all")]
        for host in payload.get("hosts", []):
            command.extend(["--host", host])
        return command
    if kind == "package_patch":
        limit = payload.get("limit", target_ref)
        return [
            "ansible-playbook",
            "playbooks/patch_guests.yml",
            "-i",
            inventory,
            "--limit",
            limit,
            "-e",
            f"dry_run={dry_run}",
        ]
    if kind == "snapshot":
        limit = payload.get("limit", target_ref)
        return [
            "ansible-playbook",
            "playbooks/snapshot_guest.yml",
            "-i",
            inventory,
            "--limit",
            limit,
            "-e",
            f"dry_run={dry_run}",
        ]
    if kind == "proxmox_patch":
        limit = payload.get("limit", target_ref)
        return [
            "ansible-playbook",
            "playbooks/patch_proxmox_nodes.yml",
            "-i",
            inventory,
            "--limit",
            limit,
            "-e",
            f"dry_run={dry_run}",
        ]
    if kind == "proxmox_reboot":
        limit = payload.get("limit", target_ref)
        reboot_mode = payload.get("reboot_mode", "soft")
        return [
            "ansible-playbook",
            "playbooks/reboot_proxmox_nodes.yml",
            "-i",
            inventory,
            "--limit",
            limit,
            "-e",
            f"dry_run={dry_run}",
            "-e",
            f"reboot_mode={reboot_mode}",
        ]
    if kind == "backup":
        backup_root = str(config.BACKUPS_ROOT)
        volume = payload.get("volume", target_ref)
        output_name = payload.get("output_name", f"{volume}.tgz")
        return [
            "python3",
            "scripts/backup_named_volume.py",
            "--volume",
            volume,
            "--backup-root",
            backup_root,
            "--output-name",
            output_name,
        ]
    if kind == "rollback":
        return [
            "python3",
            "scripts/rollback_stack.py",
            "--stack",
            target_ref,
        ]
    raise ValueError(f"unsupported job kind: {kind}")
