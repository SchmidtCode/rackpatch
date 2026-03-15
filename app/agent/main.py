from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from common import config


SERVER_URL = config.env("RACKPATCH_SERVER_URL", "http://localhost:9080", "OPS_SERVER_URL").rstrip("/")
STATE_DIR = Path(config.env("RACKPATCH_AGENT_STATE_DIR", "/var/lib/rackpatch-agent", "OPS_AGENT_STATE_DIR"))
STATE_FILE = STATE_DIR / "agent.json"
BOOTSTRAP_TOKEN = config.env("RACKPATCH_AGENT_BOOTSTRAP_TOKEN", "", "OPS_AGENT_BOOTSTRAP_TOKEN")
AGENT_NAME = config.env("RACKPATCH_AGENT_NAME", socket.gethostname(), "OPS_AGENT_NAME")
DISPLAY_NAME = config.env("RACKPATCH_AGENT_DISPLAY_NAME", AGENT_NAME, "OPS_AGENT_DISPLAY_NAME")
AGENT_MODE = config.env("RACKPATCH_AGENT_MODE", "systemd", "OPS_AGENT_MODE")
AGENT_LABELS = [
    item.strip()
    for item in config.env("RACKPATCH_AGENT_LABELS", "", "OPS_AGENT_LABELS").split(",")
    if item.strip()
]
AGENT_VERSION = config.env("RACKPATCH_AGENT_VERSION", config.APP_VERSION, "OPS_AGENT_VERSION")
AGENT_INSTALL_DIR = config.env("RACKPATCH_AGENT_INSTALL_DIR", "", "OPS_AGENT_INSTALL_DIR")
AGENT_COMPOSE_DIR = config.env("RACKPATCH_AGENT_COMPOSE_DIR", "", "OPS_AGENT_COMPOSE_DIR")
COMPOSE_DISCOVERY_TTL_SECONDS = int(
    config.env("RACKPATCH_AGENT_COMPOSE_DISCOVERY_TTL", "300", "OPS_AGENT_COMPOSE_DISCOVERY_TTL")
)

_compose_discovery_cache: dict[str, Any] = {
    "captured_at": 0.0,
    "projects": [],
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"rackpatch-agent/{AGENT_VERSION}"})


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capabilities() -> list[str]:
    caps = {"package_check"}
    if shutil.which("docker"):
        caps.add("docker")
        caps.add("docker-exec")
        caps.add("docker-compose-discovery")
    if shutil.which("sudo"):
        caps.add("sudo-packages")
    if Path("/etc/pve").exists():
        caps.add("proxmox")
    return sorted(caps)


def register() -> dict[str, Any]:
    response = SESSION.post(
        f"{SERVER_URL}/api/v1/agents/register",
        headers={"X-Ops-Agent-Token": BOOTSTRAP_TOKEN},
        json={
            "name": AGENT_NAME,
            "display_name": DISPLAY_NAME,
            "transport": "poll",
            "platform": platform.platform(),
            "version": AGENT_VERSION,
            "capabilities": capabilities(),
            "labels": AGENT_LABELS,
            "metadata": {
                "python": sys.version.split()[0],
                "mode": AGENT_MODE,
                "hostname": socket.gethostname(),
                "install_dir": AGENT_INSTALL_DIR,
                "compose_dir": AGENT_COMPOSE_DIR,
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    save_state(
        {
            "agent_id": payload["id"],
            "agent_secret": payload["agent_secret"],
            "poll_seconds": payload.get("poll_seconds", config.AGENT_POLL_SECONDS),
        }
    )
    return load_state()


def ensure_registered() -> dict[str, Any]:
    state = load_state()
    if not state.get("agent_id") or not state.get("agent_secret"):
        return register()
    return state


def agent_headers(state: dict[str, Any]) -> dict[str, str]:
    return {"X-Ops-Agent-Secret": state["agent_secret"]}


def _run_json_command(command: list[str]) -> Any:
    rc, stdout = run_command(command)
    if rc != 0:
        raise RuntimeError(stdout or f"command failed: {' '.join(command)}")
    return json.loads(stdout)


def discover_compose_projects() -> list[dict[str, Any]]:
    if not shutil.which("docker"):
        return []

    rc, container_ids_raw = run_command(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"]
    )
    if rc != 0:
        return []

    container_ids = [item.strip() for item in container_ids_raw.splitlines() if item.strip()]
    if not container_ids:
        return []

    try:
        payload = _run_json_command(["docker", "inspect", *container_ids])
    except Exception:  # noqa: BLE001
        return []

    projects: dict[tuple[str, str, str], dict[str, Any]] = {}
    for container in payload:
        labels = ((container.get("Config") or {}).get("Labels") or {})
        project_name = str(labels.get("com.docker.compose.project") or "").strip()
        project_dir = str(labels.get("com.docker.compose.project.working_dir") or "").strip()
        config_files_raw = str(labels.get("com.docker.compose.project.config_files") or "").strip()
        if not project_name or not project_dir:
            continue

        config_files = [item.strip() for item in config_files_raw.split(",") if item.strip()]
        key = (project_name, project_dir, ",".join(config_files))
        project = projects.setdefault(
            key,
            {
                "project_name": project_name,
                "project_dir": project_dir,
                "config_files": config_files,
                "compose_env_files": [".env"] if Path(project_dir, ".env").exists() else [],
                "services": [],
            },
        )
        project["services"].append(
            {
                "service": str(labels.get("com.docker.compose.service") or "").strip(),
                "container_name": str(container.get("Name") or "").lstrip("/"),
                "image": str((container.get("Config") or {}).get("Image") or "").strip(),
                "state": str((container.get("State") or {}).get("Status") or "").strip(),
            }
        )

    return sorted(
        (
            {
                **project,
                "services": sorted(
                    project["services"],
                    key=lambda service: (service.get("service") or service.get("container_name") or ""),
                ),
            }
            for project in projects.values()
        ),
        key=lambda project: (project["project_name"], project["project_dir"]),
    )


def compose_projects_metadata() -> list[dict[str, Any]]:
    now = time.time()
    cached_at = float(_compose_discovery_cache.get("captured_at") or 0.0)
    if now - cached_at < COMPOSE_DISCOVERY_TTL_SECONDS:
        return list(_compose_discovery_cache.get("projects") or [])

    projects = discover_compose_projects()
    _compose_discovery_cache["captured_at"] = now
    _compose_discovery_cache["projects"] = projects
    return list(projects)


def heartbeat_metadata() -> dict[str, Any]:
    metadata = {
        "capabilities": capabilities(),
        "mode": AGENT_MODE,
    }
    if AGENT_INSTALL_DIR:
        metadata["install_dir"] = AGENT_INSTALL_DIR
    if AGENT_COMPOSE_DIR:
        metadata["compose_dir"] = AGENT_COMPOSE_DIR
    if shutil.which("docker"):
        metadata["docker"] = {
            "compose_projects": compose_projects_metadata(),
        }
    return metadata


def heartbeat(state: dict[str, Any]) -> None:
    SESSION.post(
        f"{SERVER_URL}/api/v1/agents/heartbeat",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "metadata": heartbeat_metadata(),
        },
        timeout=30,
    ).raise_for_status()


def post_event(state: dict[str, Any], job_id: str, message: str, stream: str = "stdout") -> None:
    SESSION.post(
        f"{SERVER_URL}/api/v1/jobs/{job_id}/events",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "stream": stream,
            "message": message,
        },
        timeout=30,
    ).raise_for_status()


def complete(state: dict[str, Any], job_id: str, status: str, result: dict[str, Any]) -> None:
    SESSION.post(
        f"{SERVER_URL}/api/v1/jobs/{job_id}/complete",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "status": status,
            "result": result,
        },
        timeout=60,
    ).raise_for_status()


def claim(state: dict[str, Any]) -> dict[str, Any] | None:
    response = SESSION.post(
        f"{SERVER_URL}/api/v1/agents/claim",
        headers=agent_headers(state),
        json={"agent_id": state["agent_id"]},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("job") is None:
        return None
    return payload["job"]


def run_command(command: list[str], cwd: str | None = None) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        output.append(raw.rstrip("\n"))
    return process.wait(), "\n".join(output)


def check_packages() -> dict[str, Any]:
    rc, stdout = run_command(
        [
            "sh",
            "-lc",
            "apt list --upgradable 2>/dev/null | tail -n +2; "
            "printf '__OPS_REBOOT__=%s\\n' \"$(test -f /var/run/reboot-required && echo yes || echo no)\"",
        ]
    )
    packages = []
    reboot_required = False
    for line in stdout.splitlines():
        if line.startswith("__OPS_REBOOT__="):
            reboot_required = line.split("=", 1)[1] == "yes"
        elif line.strip():
            packages.append(line.strip())
    return {"exit_code": rc, "packages": packages, "reboot_required": reboot_required, "stdout": stdout}


def patch_packages() -> dict[str, Any]:
    rc, stdout = run_command(
        [
            "sh",
            "-lc",
            "sudo apt-get update && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y && "
            "sudo apt-get autoremove -y",
        ]
    )
    return {"exit_code": rc, "stdout": stdout}


def docker_update(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = payload["project_dir"]
    compose_env_files = payload.get("compose_env_files", [])
    command = ["docker", "compose"]
    for env_file in compose_env_files:
        command.extend(["--env-file", env_file])
    rc_config, out_config = run_command(command + ["config"], cwd=project_dir)
    if rc_config != 0:
        return {"exit_code": rc_config, "stdout": out_config}
    rc_pull, out_pull = run_command(command + ["pull"], cwd=project_dir)
    if rc_pull != 0:
        return {"exit_code": rc_pull, "stdout": out_pull}
    rc_up, out_up = run_command(command + ["up", "-d"], cwd=project_dir)
    return {"exit_code": rc_up, "stdout": "\n".join([out_config, out_pull, out_up]).strip()}


def execute_job(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = dict(job.get("payload") or {})
    kind = job["kind"]
    if kind == "package_check":
        result = check_packages()
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "package_patch":
        result = patch_packages()
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "docker_update":
        result = docker_update(payload)
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    return "failed", {"error": f"unsupported agent job kind: {kind}"}


def main() -> int:
    state = ensure_registered()
    print(f"rackpatch-agent registered name={AGENT_NAME} id={state['agent_id']}", flush=True)
    while True:
        try:
            heartbeat(state)
            job = claim(state)
            if job:
                job_id = str(job["id"])
                post_event(state, job_id, f"agent {AGENT_NAME} executing {job['kind']}")
                status, result = execute_job(job)
                for line in str(result.get("stdout", "")).splitlines():
                    if line.strip():
                        post_event(state, job_id, line)
                complete(state, job_id, status, result)
            time.sleep(float(state.get("poll_seconds", config.AGENT_POLL_SECONDS)))
        except Exception as exc:  # noqa: BLE001
            print(f"rackpatch-agent loop error: {exc}", flush=True)
            time.sleep(config.AGENT_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
