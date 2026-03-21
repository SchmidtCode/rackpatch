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
            "ANSIBLE_CONFIG": str(config.resolve_runtime_path(config.ANSIBLE_CONFIG_PATH)),
            "RACKPATCH_SITE_ROOT": str(site.site_root()),
            "RACKPATCH_STACKS_FILE": str(site.stacks_path()),
            "RACKPATCH_INVENTORY_FILE": str(site.inventory_path()),
            "RACKPATCH_ROLLBACK_ROOT": str(group_vars.get("rollback_root", "/data/rollbacks")),
            "RACKPATCH_RUNTIME_ROOT": str(config.RUNTIME_ROOT),
        }
    )
    return env


def run_logged(job_id: str, command: list[str], cwd: str | None = None) -> dict[str, Any]:
    jobs.append_event(job_id, f"RUN {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=cwd or str(config.RUNTIME_ROOT),
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
        if "RACKPATCH_ARTIFACT " not in raw_line:
            continue
        line = raw_line.split("RACKPATCH_ARTIFACT ", 1)[1].strip().strip('"').strip(",")
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
    command = ["python3", str(config.SCRIPTS_ROOT / "summarize_docker_update.py")]
    selected_stacks = list(payload.get("selected_stacks") or [])
    if selected_stacks:
        for stack_name in selected_stacks:
            command.extend(["--stack", stack_name])
    else:
        command.extend(["--window", payload.get("window", "approve")])

    result = subprocess.run(
        command,
        cwd=str(config.RUNTIME_ROOT),
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
    if kind == "backup":
        backup_root = str(config.BACKUPS_ROOT)
        volume = payload.get("volume", target_ref)
        output_name = payload.get("output_name", f"{volume}.tgz")
        return [
            "python3",
            str(config.SCRIPTS_ROOT / "backup_named_volume.py"),
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
            str(config.SCRIPTS_ROOT / "rollback_stack.py"),
            "--stack",
            target_ref,
        ]
    raise ValueError(f"unsupported job kind: {kind}")
